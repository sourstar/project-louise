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
from app.acquire.spec import RemoteFile
from app.plugins.eurlex import from_tree, rdf
from tests.acquire.test_eurlex_dump import FORMEX, MTD, _items


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
