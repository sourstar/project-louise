"""A collection's text index is opened BEFORE the first question needs it,
and the open of one index holds up no other (app/fts.py `get_index`,
`open_all`; app/warmup.py `open_text_indexes`).

The node, 2026-11-25: the operator's first question after a deploy, "fts
12,600" over gutenberg — a 54 GB text index in 21 language groups on the SATA
drive, opened by that question, one group after the other under the module's
one lock; later searches of the same process had measured 0.4 s at the median
(storage-tiering.md §12)."""
from __future__ import annotations

import threading
import time

import pytest

from app import collections as collections_mod, fts, warmup


def _index(collection, lang: str, book_id: int, text: str) -> None:
    fts.index_book(lang, book_id, [{"id": f"fake:{book_id}:0:1", "chapter": "", "text": text}], collection)


@pytest.fixture
def held(isolated_config):
    c = collections_mod.default_collection()
    _index(c, "en", 1, "the white whale and the captain")
    _index(c, "it", 2, "la balena bianca e il capitano")
    _index(c, "de", 3, "der weisse wal und der kapitaen")
    fts.close_all()
    fts.forget(fts.base_of(c))                   # as a process that has just started: nothing open
    yield c
    fts.close_all()


def test_every_language_group_is_opened_and_then_searches_as_before(held):
    groups = fts.existing_groups(held)
    assert len(groups) >= 3
    opened, failed, seconds = fts.open_all(held)
    assert sorted(opened) == groups and failed == {} and seconds >= 0
    with fts._lock:
        assert {k[1] for k in fts._indexes if k[0] == str(fts.base_of(held))} == set(groups)
    hits = fts.search_records(fts.group_for("it"), "balena", 5, held)
    assert [h["book_id"] for h in hits] == [2], "opened ahead, the index answers what it always did"


def test_the_open_of_one_index_does_not_wait_for_the_open_of_another(held, monkeypatch):
    """The module's one lock was held across `tantivy.Index(...)`: two legs
    of a first search opened their indexes one after the other. Each index
    has its own lock now; the same index is still opened once."""
    real = fts.tantivy.Index
    live, peak, opens, lock = [0], [0], [], threading.Lock()

    def slow(schema, path=None, **kw):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            opens.append(path)
        time.sleep(0.2)
        try:
            return real(schema, path=path, **kw)
        finally:
            with lock:
                live[0] -= 1
    monkeypatch.setattr(fts.tantivy, "Index", slow)
    groups = fts.existing_groups(held)
    t0 = time.monotonic()
    threads = [threading.Thread(target=fts.get_index, args=(g, held)) for g in groups for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    took = time.monotonic() - t0
    assert peak[0] >= 2, "different indexes open side by side"
    assert sorted(opens) == sorted(str(fts.base_of(held) / g) for g in groups), "each index opened ONCE"
    assert took < 0.2 * len(groups), f"not one after the other ({took:.2f} s for {len(groups)} opens of 0.2 s)"


def test_a_forget_waits_for_an_open_in_flight_and_leaves_no_handle_behind(held, monkeypatch):
    """A collection that left the node: its handles go (normattiva,
    2026-10-13, written through an Index whose files were gone). An open that
    began before the forget must not put the handle back after it."""
    real = fts.tantivy.Index
    inside, go = threading.Event(), threading.Event()

    def slow(schema, path=None, **kw):
        inside.set()
        go.wait(5)
        return real(schema, path=path, **kw)
    monkeypatch.setattr(fts.tantivy, "Index", slow)
    group = fts.existing_groups(held)[0]
    opener = threading.Thread(target=fts.get_index, args=(group, held))
    opener.start()
    assert inside.wait(5)
    forgot = []
    forgetter = threading.Thread(target=lambda: forgot.append(fts.forget(fts.base_of(held))))
    forgetter.start()
    time.sleep(0.1)
    assert forgetter.is_alive(), "it waits for the open in flight"
    go.set()
    opener.join(5)
    forgetter.join(5)
    assert forgot == [1]
    with fts._lock:
        assert not [k for k in fts._indexes if k[0] == str(fts.base_of(held))], "no handle left behind"


def test_a_group_that_cannot_be_opened_is_said_and_the_others_still_open(held, monkeypatch):
    real = fts.get_index

    def one_fails(group, collection=None):
        if group == fts.group_for("de"):
            raise RuntimeError("the index at … is held by another process")
        return real(group, collection)
    monkeypatch.setattr(fts, "get_index", one_fails)
    opened, failed, _ = fts.open_all(held)
    assert fts.group_for("de") not in opened and len(opened) == len(fts.existing_groups(held)) - 1
    assert list(failed) == [fts.group_for("de")] and "held by another process" in failed[fts.group_for("de")]


def test_the_web_opens_every_searched_collections_text_index_at_its_start_and_says_the_seconds(
        held, monkeypatch, capsys):
    annotations = type("C", (), {"name": "claims"})()
    broken = type("C", (), {"name": "broken"})()
    monkeypatch.setattr(collections_mod, "load_all", lambda *a, **k: [held, annotations, broken])
    monkeypatch.setattr(collections_mod, "is_annotations", lambda c: c is annotations)
    real = fts.open_all
    monkeypatch.setattr(fts, "open_all", lambda c, **k: (_ for _ in ()).throw(OSError("no such drive"))
                        if c is broken else real(c, **k))
    out = warmup.open_text_indexes("test")
    assert [(name, n) for name, n, _ in out] == [(held.name, len(fts.existing_groups(held)))]
    said = capsys.readouterr().out
    assert f"{held.name}: text index opened, {len(fts.existing_groups(held))} language group(s) in " in said
    assert "broken: text index not opened (test): OSError: no such drive" in said, "one collection never stops the rest"
    assert "claims" not in said, "an annotations collection has no text leg"
    assert "a first question no longer opens them" in said


def test_the_start_opens_them_on_a_thread_of_its_own_and_never_twice(monkeypatch):
    monkeypatch.setenv("LOUISE_WARMUP", "1")
    monkeypatch.setattr(warmup, "_started", False)
    started = []
    monkeypatch.setattr(warmup.threading, "Thread",
                        lambda target=None, name=None, daemon=None: type("T", (), {
                            "start": lambda self: started.append((name, daemon))})())
    assert warmup.start() is True and warmup.start() is False
    assert started == [("warmup", True), ("warmup-text", True)], "beside the models' warm, neither waiting for the other"
    monkeypatch.setenv("LOUISE_WARMUP", "0")
    monkeypatch.setattr(warmup, "_started", False)
    assert warmup.start() is False and len(started) == 2
