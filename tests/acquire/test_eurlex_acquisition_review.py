"""The EUR-Lex acquisition defects of the review of 2026-11-21 (Q1-Q8,
docs/notes/eurlex-review-2026-11-21.md), each reproduced by the reviewer
before it was fixed. The engine's own (Q3) is in test_fetch_backoff.py and
test_common_engine.py, on a generic spec."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.acquire import cellar, eurlex_dump, work_trees
from app.acquire.spec import Filter, RemoteFile
from app.plugins.eurlex import from_tree, rdf
from tests.acquire.test_cellar import _spec, _years, listing_cache, mock, no_wait  # noqa: F401
from tests.acquire.test_eurlex_dump import FORMEX, MTD, _items, serial  # noqa: F401


# ---- Q1: a kept tree older than the listing's date is never landed as current ----------------

_MANIF = b'rdf:about="http://publications.europa.eu/resource/cellar/81716b12-9c7b-4b5f-ba41-ddd13747a029.0005.04">'
_WAS = b"2016-10-26T10:18:11.971+02:00"


def _trees() -> tuple[bytes, bytes]:
    """The work's tree as Cellar served it at 13:00, and as it serves it
    after its Italian Formex manifestation was replaced at 15:38."""
    old = (FORMEX[2] / MTD).read_bytes()
    j = old.index(_WAS, old.index(_MANIF))
    return old, old[:j] + b"2026-11-19T15:38:00.000+01:00" + old[j + len(_WAS):]


def _served(monkeypatch, new: bytes) -> list[str]:
    old = _trees()[0]
    items = _items(FORMEX[2])
    names = {it.uri: it.name for f in from_tree.notice(rdf.scope(rdf.read(old), "ITA"), "ITA", FORMEX[0]).items.values()
             for it in f}
    asked: list[str] = []

    def get(client, url, headers, what, *, charge, ok=(200,), follow=True):
        asked.append(what)
        body = new if headers.get("Accept") == cellar.RDF_ACCEPT else items[names[url]]
        return httpx.Response(200, content=body, request=httpx.Request("GET", url))
    monkeypatch.setattr(cellar, "_get", get)
    return asked


def test_a_kept_tree_older_than_the_listing_is_asked_of_cellar_again(tmp_path, monkeypatch):
    """13:00: a unit of the work had Cellar's tree, kept 6 h. 16:00: the
    listing dates the Italian unit 15:38. The kept tree dated it 2016: it
    landed, and the engine recorded `remote_mtime=15:38, fetch_done=1` —
    no later pass would fetch it again."""
    old, new = _trees()
    celex = FORMEX[0]
    assert from_tree.notice(rdf.scope(rdf.read(new), "ITA"), "ITA", celex).modified.startswith("2026-11-19")
    asked = _served(monkeypatch, new)
    corpus = tmp_path / "corpus"
    recent = work_trees.RecentTrees(tmp_path / "recent")
    monkeypatch.setattr(work_trees, "recent_for", lambda root: recent)
    recent.get(celex, lambda: old)
    rel = cellar.rel_path("it", celex)
    remote = RemoteFile(rel_path=rel, mtime=cellar._epoch("2026-11-19T15:38:00+01:00"),
                        remote=cellar.encode(celex, "it"))
    cellar.TREE_UNITS.fetch(SimpleNamespace(url="https://cellar.test"), remote, corpus / rel)
    held = from_tree.notice(rdf.loads((corpus / rel).read_bytes()), "ITA", celex)
    assert cellar._epoch(held.modified) >= remote.mtime, "the unit held is as recent as the listing says"
    assert sum("non-inferred tree" in a for a in asked) == 1, "the stale kept tree asked of Cellar again"
    assert recent.get(celex, lambda: pytest.fail("kept anew"))[0] == new, "Cellar's answer kept"


def test_a_kept_tree_as_recent_as_the_listing_still_serves_without_a_request(tmp_path, monkeypatch):
    old, new = _trees()
    celex = FORMEX[0]
    asked = _served(monkeypatch, new)
    recent = work_trees.RecentTrees(tmp_path / "recent")
    recent.get(celex, lambda: new)
    when = cellar._epoch("2026-11-19T15:38:00+01:00")
    got = cellar.fetch_unit_from_tree("https://cellar.test", celex, "it", tmp_path / "u", client=object(),
                                      trees=recent, listed_mtime=when)
    assert got.tree_from == "kept" and not any("non-inferred tree" in a for a in asked)
    # the dump's incomplete list states no date: the window alone, as before
    recent2 = work_trees.RecentTrees(tmp_path / "recent2")
    recent2.get(celex, lambda: old)
    got = cellar.fetch_unit_from_tree("https://cellar.test", celex, "it", tmp_path / "v", client=object(),
                                      trees=recent2, listed_mtime=None)
    assert got.tree_from == "kept"


# ---- Q4: the incomplete record is locked across processes -------------------------------------

def _marker(corpus: str, n: int, start) -> None:
    start.wait()
    for i in range(n):
        eurlex_dump.mark_fetched(Path(corpus), "it", f"32000R{i:05d}")


def _builder(corpus: str, n: int, start) -> None:
    import time
    start.wait()
    for k in range(n):
        eurlex_dump._record_incomplete(Path(corpus), {"fr": {f"32001R{j:05d}": "true" for j in range(k + 1)}})
        time.sleep(0.01)


def test_the_cli_build_and_the_worker_lose_none_of_each_other_s_marks(tmp_path):
    """The CLI build (`louise dev eurlex-dump`) records its languages while
    the worker marks the units Cellar gave: a lock per PROCESS lost marks
    (reproduced: 298 of 300 fetched marks kept, the build's 33 of 40)."""
    import json
    import multiprocessing
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    n = 300
    base = {"it": {f"32000R{i:05d}": {"in_force": "true", "reform": True, "tried": None} for i in range(n)}}
    for j in range(2000):
        base["it"][f"39999R{j:05d}"] = {"in_force": "true", "tried": None}
    (corpus / eurlex_dump.INCOMPLETE).write_text(json.dumps(base))
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    a = ctx.Process(target=_marker, args=(str(corpus), n, start))
    b = ctx.Process(target=_builder, args=(str(corpus), 40, start))
    a.start()
    b.start()
    start.set()
    a.join(120)
    b.join(120)
    assert (a.exitcode, b.exitcode) == (0, 0)
    got = eurlex_dump.incomplete(corpus)
    fetched = sum(1 for i in range(n) if (got["it"].get(f"32000R{i:05d}") or {}).get("fetched"))
    assert (fetched, len(got.get("fr") or {})) == (n, 40)


# ---- Q5: a mark and a listing cost per unit of work, not per record size ---------------------

def _record(corpus: Path, n_per_lang: int = 10_000) -> dict:
    import json
    corpus.mkdir(parents=True, exist_ok=True)
    rec = {lang: {f"3{2000 + i % 25}R{i:05d}": {"in_force": "true", "reform": True, "tried": None}
                  for i in range(n_per_lang)} for lang in cellar.held_languages(SimpleNamespace(slices=None))}
    (corpus / eurlex_dump.INCOMPLETE).write_text(json.dumps(rec, sort_keys=True))
    return rec


def test_a_mark_appends_a_line_and_never_rewrites_the_record(tmp_path):
    """Reproduced: 15.6 MiB at 240k entries rewritten per unit, 379 ms under
    the lock (measured here 426 ms; after, 0.10 ms)."""
    import time
    corpus = tmp_path / "corpus"
    rec = _record(corpus)
    path = corpus / eurlex_dump.INCOMPLETE
    before = eurlex_dump._sig(path)
    assert before[2] > 15 << 20
    keys = [(l, c) for l in sorted(rec) for c in list(rec[l])[:20]]
    t0 = time.monotonic()
    for l, c in keys:
        eurlex_dump.mark_fetched(corpus, l, c)
    eurlex_dump.mark_tried(corpus, *keys[-1], "cellar: 404 to the tree")
    per = (time.monotonic() - t0) / len(keys)
    assert eurlex_dump._sig(path) == before, "the record is not rewritten by a mark"
    assert (corpus / eurlex_dump.MARKS).stat().st_size < 120 * (len(keys) + 2), "a line a mark"
    assert per < 0.02, f"{per * 1000:.1f} ms a mark"
    got = eurlex_dump.incomplete(corpus)
    assert all(got[l][c]["fetched"] for l, c in keys) and got[keys[-1][0]][keys[-1][1]]["tried"]
    assert sum(1 for u in got.values() for e in u.values() if e.get("fetched")) == len(keys)


def test_the_marks_are_folded_once_they_outgrow_the_record_and_a_stale_file_is_void(tmp_path, monkeypatch):
    import json
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / eurlex_dump.INCOMPLETE).write_text(json.dumps(
        {"it": {f"32000R{i:05d}": {"in_force": "true", "tried": None} for i in range(50)}}))
    monkeypatch.setattr(eurlex_dump, "FOLD_MIN", 0)
    size = (corpus / eurlex_dump.INCOMPLETE).stat().st_size
    for i in range(50):
        eurlex_dump.mark_fetched(corpus, "it", f"32000R{i:05d}")
        if not (corpus / eurlex_dump.MARKS).exists():
            break
    assert i < 49 and not (corpus / eurlex_dump.MARKS).exists(), "folded once larger than the record"
    folded = json.loads((corpus / eurlex_dump.INCOMPLETE).read_text())
    assert all(folded["it"][f"32000R{k:05d}"]["fetched"] for k in range(i + 1)) and size
    # marks written against a record a build has since replaced (a crash between
    # the record's write and the marks' removal) are never read again
    eurlex_dump.mark_tried(corpus, "it", "32000R00049", "a verdict")
    stale = (corpus / eurlex_dump.MARKS).read_bytes()
    eurlex_dump._record_incomplete(corpus, {"it": {"32000R00049": "true"}})
    (corpus / eurlex_dump.MARKS).write_bytes(stale)
    assert eurlex_dump.incomplete(corpus)["it"]["32000R00049"]["tried"] == "a verdict", "kept by the build"
    eurlex_dump._record_incomplete(corpus, {"it": {"32000R00049": "true", "32000R00001": "true"}})
    (corpus / eurlex_dump.MARKS).write_bytes(stale.replace(b"00049", b"00001"))
    assert not eurlex_dump.incomplete(corpus)["it"]["32000R00001"].get("tried"), "a stale marks file is void"


def _reform_corpus(tmp_path) -> tuple[Path, Path]:
    """A unit held as XHTML whose tree lists a Formex package the dump lacks:
    recorded by the build to re-form (test_eurlex_package.py)."""
    from tests.test_eurlex_package import _held_as_xhtml, _packaged_dump
    corpus = tmp_path / "corpus"
    unit = _held_as_xhtml(corpus)
    got = eurlex_dump.build(_packaged_dump(tmp_path / "dump", with_package=False), corpus, ["it"],
                            log=lambda m: None)["it"]
    assert got["package-not-in-dump"] == 1
    return corpus, unit


def _no_reads(monkeypatch) -> list[str]:
    """Every read of a held unit's tree or wrapper, counted."""
    from app.plugins.eurlex import from_tree as ft
    read: list[str] = []
    notice, named = ft.notice, eurlex_dump.held_not_its_name
    monkeypatch.setattr(ft, "notice", lambda *a, **k: read.append("tree") or notice(*a, **k))
    monkeypatch.setattr(eurlex_dump, "held_not_its_name", lambda *a: read.append("wrapper") or named(*a))
    return read


def test_a_held_unit_to_re_form_is_judged_by_a_stat_not_read_every_listing(tmp_path, serial, monkeypatch):  # noqa: F811
    """Reproduced: every listing read and parsed every held re-form unit's
    tree and wrapper (0.9 ms an entry, ~3.5 min at 232k; measured here
    1.2-1.4 ms, after 7-8 us)."""
    from tests.test_eurlex_package import PKG, _package
    corpus, unit = _reform_corpus(tmp_path)
    entry = eurlex_dump.incomplete(corpus)["it"][FORMEX[0]]
    assert entry["lacks"] == [PKG] and entry["held"] == eurlex_dump._sig(unit / rdf.TREE_FILE)
    read = _no_reads(monkeypatch)
    for _ in range(3):
        assert [c for _l, c, _f in eurlex_dump.incomplete_due(corpus, {"it"})] == [FORMEX[0]]
    assert read == [], "as the build found it: nothing read"
    (unit / PKG).write_bytes(_package())
    assert eurlex_dump.incomplete_due(corpus, {"it"}) == [], "held whole now: judged from its files"
    assert read and set(read) <= {"tree", "wrapper"}
    read.clear()
    assert eurlex_dump.incomplete_due(corpus, {"it"}) == [] and read == [], "judged once: a stat again"


def test_an_entry_recorded_before_its_state_was_is_judged_once(tmp_path, monkeypatch):
    import json
    from tests.test_eurlex_package import _held_as_xhtml
    corpus = tmp_path / "corpus"
    _held_as_xhtml(corpus)
    (corpus / eurlex_dump.INCOMPLETE).write_text(json.dumps(
        {"it": {FORMEX[0]: {"in_force": "true", "reform": True, "tried": None}}}))
    read = _no_reads(monkeypatch)
    assert len(eurlex_dump.incomplete_due(corpus, {"it"})) == 1 and read
    read.clear()
    assert len(eurlex_dump.incomplete_due(corpus, {"it"})) == 1 and read == []


# ---- Q2: a due unit failing as an episode never starves the complete listing ------------------

def _incomplete_corpus(tmp_path) -> Path:
    """A dump build that left 31972R1473 (it) incomplete: its Formex body missing."""
    from tests.acquire.test_eurlex_dump import _dump
    corpus = tmp_path / "corpus"
    eurlex_dump.build(_dump(tmp_path / "dump", drop="L_1972160IT.01000101.xml"), corpus, ["it"],
                      log=lambda m: None)
    return corpus


def test_a_due_unit_failing_as_an_episode_never_starves_the_complete_listing(tmp_path, serial, mock,  # noqa: F811
                                                                             monkeypatch):
    """Reproduced: Cellar answering 500 on the one unit the dump lacked
    (`_get`'s attempts spent: an episode, no verdict) — 50 passes, the due
    list every time, the complete listing (every new act) never once."""
    _years(monkeypatch)
    corpus = _incomplete_corpus(tmp_path)

    def five_hundred(*a, **kw):
        raise cellar.TransportError("cellar: 500 to L_1972160IT.01000101.xml of 31972R1473 (ITA) after 5 "
                                    "attempts (connection timed out: deferred to the next round)")
    monkeypatch.setattr(cellar, "fetch_unit_from_tree", five_hundred)
    spec = _spec(mock)
    inv = cellar.TreeInventory(corpus, "eurlex5")
    monkeypatch.setattr(inv, "_say", lambda m: None)
    ep = SimpleNamespace(url="https://cellar.test")
    listed_at = None
    for k in range(50):
        for f in inv.list(spec.endpoints[0], spec, Filter(include=("lang=it/*", "lang=fr/*"))):
            with pytest.raises(cellar.TransportError):
                cellar.TREE_UNITS.fetch(ep, f, corpus / f.rel_path)
        if mock.queries():
            listed_at = k
            break
    assert listed_at == 1, "the pass after the episode lists"
    entry = eurlex_dump.incomplete(corpus)["it"][FORMEX[0]]
    assert entry["deferred"]["n"] == 1 and not entry.get("tried"), "an episode is no verdict"
    at = entry["deferred"]["at"]
    # due again once the engine's wait for a first failure (60 s) is out, and longer after the next
    assert eurlex_dump.incomplete_due(corpus, ("it",), now=at + 59) == []
    assert eurlex_dump.incomplete_due(corpus, ("it",), now=at + 60) == [("it", FORMEX[0], "true")]
    due = RemoteFile(rel_path=cellar.rel_path("it", FORMEX[0]), mtime=None, remote=cellar.encode(FORMEX[0], "it"))
    with pytest.raises(cellar.TransportError):
        cellar.TREE_UNITS.fetch(ep, due, corpus / due.rel_path)
    entry = eurlex_dump.incomplete(corpus)["it"][FORMEX[0]]
    assert entry["deferred"]["n"] == 2
    assert eurlex_dump.incomplete_due(corpus, ("it",), now=entry["deferred"]["at"] + 299) == []
    assert eurlex_dump.incomplete_due(corpus, ("it",), now=entry["deferred"]["at"] + 300)


def test_a_stop_on_a_due_unit_is_neither_a_verdict_nor_an_episode(tmp_path, serial, monkeypatch):  # noqa: F811
    from app.acquire.spec import TransferAborted
    corpus = _incomplete_corpus(tmp_path)

    def stopped(*a, **kw):
        raise TransferAborted("cellar: stopped between requests")
    monkeypatch.setattr(cellar, "fetch_unit_from_tree", stopped)
    due = RemoteFile(rel_path=cellar.rel_path("it", FORMEX[0]), mtime=None, remote=cellar.encode(FORMEX[0], "it"))
    with pytest.raises(TransferAborted):
        cellar.TREE_UNITS.fetch(SimpleNamespace(url="https://cellar.test"), due, corpus / due.rel_path)
    assert eurlex_dump.incomplete_due(corpus, ("it",)) == [("it", FORMEX[0], "true")], "due at once"


# ---- Q8: a NEWER dump records its incomplete units afresh -------------------------------------

def _dated_dump(base: Path, created: str, **kw) -> Path:
    import json
    from tests.acquire.test_eurlex_dump import _dump
    got = _dump(base, **kw)
    (got / "datadump-it" / "datadump.json").write_text(json.dumps({"language": "it", "created": created}))
    return got


def test_a_newer_dump_clears_tried_and_fetched_for_the_units_it_records_again(tmp_path, serial):  # noqa: F811
    """Reproduced: `tried` and `fetched` kept through a newer dump's build,
    so a unit Cellar failed (or gave) once was never asked first again, as
    "until a newer dump" and "records it afresh" promise."""
    corpus = tmp_path / "corpus"
    drop = "L_1972160IT.01000101.xml"
    eurlex_dump.build(_dated_dump(tmp_path / "d1", "2026-11-15T01:00:00+01:00", drop=drop), corpus, ["it"],
                      log=lambda m: None)
    eurlex_dump.mark_tried(corpus, "it", FORMEX[0], "cellar: the tree names no item")
    eurlex_dump.mark_deferred(corpus, "it", FORMEX[0], "cellar: 503 to the tree")
    assert eurlex_dump.incomplete_due(corpus, ("it",)) == []
    # the same dump again: the verdict kept (test_eurlex_phase2)
    eurlex_dump.build(_dated_dump(tmp_path / "d2", "2026-11-15T01:00:00+01:00", drop=drop), corpus, ["it"],
                      log=lambda m: None)
    assert eurlex_dump.incomplete(corpus)["it"][FORMEX[0]]["tried"]
    eurlex_dump._LANGS.clear()
    eurlex_dump._MTDS.clear()
    got = eurlex_dump.build_newer(_dated_dump(tmp_path / "d3", "2026-11-22T01:00:00+01:00", drop=drop), corpus,
                                  ["it"], log=lambda m: None)
    assert got["it"]["incomplete"] == 1
    entry = eurlex_dump.incomplete(corpus)["it"][FORMEX[0]]
    assert not entry.get("tried") and not entry.get("fetched") and not entry.get("deferred"), entry
    assert eurlex_dump.incomplete_due(corpus, ("it",)) == [("it", FORMEX[0], "true")], "asked first again"
    eurlex_dump.mark_fetched(corpus, "it", FORMEX[0])
    eurlex_dump._LANGS.clear()
    eurlex_dump._MTDS.clear()
    eurlex_dump.build_newer(_dated_dump(tmp_path / "d4", "2026-11-29T01:00:00+01:00", drop=drop), corpus,
                            ["it"], log=lambda m: None)
    assert not eurlex_dump.incomplete(corpus)["it"][FORMEX[0]].get("fetched"), "fetched cleared too"


# ---- Q6: an unreadable bulk holds the pass back; never the API, never a language stamped -------

def _coll(corpus: Path):
    return SimpleNamespace(name="eurlex5", corpus_dir=corpus, slices=None,
                           codex=SimpleNamespace(parser=SimpleNamespace(version=5)))


def _cut(p: Path) -> None:
    p.write_bytes(p.read_bytes()[: p.stat().st_size // 2])         # still being copied


def test_a_newer_dump_whose_mtd_does_not_open_holds_the_pass_back(tmp_path, serial, monkeypatch):  # noqa: F811
    """Reproduced: `works()` raised into prepare, the engine logged "bulk
    transport not prepared" and asked Cellar for every pending unit."""
    from app.acquire import datadump
    from app.acquire.spec import HeldBack
    from tests.acquire.test_eurlex_dump import _dump
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    eurlex_dump._stamp(corpus, {"it": "2026-10-01T01:00:00+01:00"})
    dump = _dump(tmp_path / "dump")
    for p in dump.rglob("*MTD*.zip"):
        _cut(p)
    monkeypatch.setattr(datadump, "_DUMPS", datadump.Dumps(dump))
    pending = [RemoteFile(rel_path=cellar.rel_path("it", FORMEX[0]), remote=cellar.encode(FORMEX[0], "it"), mtime=1)]
    with pytest.raises(HeldBack, match="LEG_MTD.zip does not read.*bulk first"):
        cellar.TREE_UNITS.prepare(pending, pending, _coll(corpus), None)
    assert eurlex_dump.stamp(corpus) == {"it": "2026-10-01T01:00:00+01:00"}, "not stamped"


def test_any_failure_of_a_newer_dump_s_build_holds_the_pass_back(tmp_path, serial, monkeypatch):  # noqa: F811
    from app.acquire import datadump
    from app.acquire.spec import HeldBack
    from tests.acquire.test_eurlex_dump import _dump
    monkeypatch.setattr(datadump, "_DUMPS", datadump.Dumps(_dump(tmp_path / "dump")))
    monkeypatch.setattr(eurlex_dump, "build_newer", lambda *a, **k: (_ for _ in ()).throw(KeyError("uuid")))
    pending = [RemoteFile(rel_path=cellar.rel_path("it", FORMEX[0]), remote=cellar.encode(FORMEX[0], "it"), mtime=1)]
    with pytest.raises(HeldBack, match="eurlex5: a newer Data Dump of it was not built.*KeyError"):
        cellar.TREE_UNITS.prepare(pending, pending, _coll(tmp_path / "corpus"), None)


def test_an_item_zip_that_does_not_open_stops_the_build_and_stamps_nothing(tmp_path, serial):  # noqa: F811
    """Reproduced: LangZips skipped it ("left to the API"), its units were
    counted not-in-language — never recorded for the API either — and the
    language was stamped built from that dump."""
    from tests.acquire.test_eurlex_dump import _dump
    corpus = tmp_path / "corpus"
    dump = _dump(tmp_path / "dump")
    _cut(dump / "datadump-it" / "LEG_IT_HTML.zip")
    with pytest.raises(eurlex_dump.BulkUnreadable, match="LEG_IT_HTML.zip does not read"):
        eurlex_dump.build(dump, corpus, ["it"], log=lambda m: None)
    assert eurlex_dump.stamp(corpus) == {} and eurlex_dump.incomplete(corpus) == {}
    assert eurlex_dump.packages_read(corpus) == set()


def test_a_member_that_does_not_read_stops_the_build(tmp_path, serial):  # noqa: F811
    import zipfile
    from tests.acquire.test_eurlex_dump import _dump
    corpus = tmp_path / "corpus"
    dump = _dump(tmp_path / "dump")
    path = dump / "datadump-it" / "LEG_IT_HTML.zip"
    with zipfile.ZipFile(path) as z:
        info = z.infolist()[0]
    data = bytearray(path.read_bytes())
    at = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra) + 10
    data[at] ^= 0xFF                                               # its CRC-32 no longer matches
    path.write_bytes(bytes(data))
    with pytest.raises(eurlex_dump.BulkUnreadable, match="member"):
        eurlex_dump.build(dump, corpus, ["it"], log=lambda m: None)
    assert eurlex_dump.stamp(corpus) == {}
