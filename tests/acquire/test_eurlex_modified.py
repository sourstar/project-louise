"""A UNIT'S LAST MODIFICATION IS ITS WORK'S, ITS EXPRESSION'S AND ITS
MANIFESTATIONS' LATEST (2026-11-19). Cellar replaced 32015D1735's English
Formex (manifestation .0006.02 at 2026-11-19 15:38 CET, its expression at
16:08) and left the work's cmr:lastModificationDate at 2026-11-02: the
listing (`cellar.page_query`) and the held tree both read the work's alone,
so the corrected act was never fetched again. Both sides now compare
`Notice.modified`; a record's `last_modified` stays the work's."""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from app.acquire import cellar
from app.acquire.spec import RemoteFile
from app.plugins.eurlex import from_tree, rdf
from app.plugins.eurlex import source as src
from tests.acquire.test_eurlex_dump import FORMEX

MOD = "http://publications.europa.eu/ontology/cdm/cmr#lastModificationDate"
CELEX_KEY = "http://publications.europa.eu/ontology/cdm#resource_legal_id_celex"
WORK, LATER = "2026-11-02T20:51:18.849+01:00", "2026-11-19T16:08:31.302+01:00"


def _tree(work: str, others: str) -> dict:
    """The fixture's held (scoped) tree: its work dated `work`, every other
    dated node — the Italian expression and its manifestations — `others`."""
    tree = copy.deepcopy(rdf.loads((FORMEX[2] / rdf.TREE_FILE).read_bytes()))
    find, gp = rdf.groups(tree)
    wg = next(g for g, p in gp.items() if FORMEX[0] in str(p.get(CELEX_KEY)))
    for uri, props in tree["nodes"].items():
        if MOD in props:
            when = work if find(uri) == wg else others
            props[MOD] = [{"@value": when, "@type": "http://www.w3.org/2001/XMLSchema#dateTime"}]
    return tree


def test_the_unit_s_modification_is_the_latest_of_work_expression_and_manifestations():
    note = from_tree.notice(_tree(WORK, LATER), "ITA", FORMEX[0])
    assert note.last_modified == WORK, "the record's field: the work's, as before"
    assert note.modified == LATER
    same = from_tree.notice(_tree(LATER, WORK), "ITA", FORMEX[0])
    assert same.modified == same.last_modified == LATER


def test_instants_compare_across_offsets():
    from app.plugins.eurlex.notice import latest
    assert latest(["2026-11-19T15:38:51.103+01:00", "2026-11-19T15:00:00+00:00"]) == "2026-11-19T15:00:00+00:00"
    assert latest([]) is None and latest(["not a date"]) is None


def test_the_listing_reads_the_three_levels():
    for q in (cellar.page_query(2015, ("it",), 0), cellar.fingerprint_query(2015, ("it",))):
        for var in ("?work", "?expr", "?manif"):
            assert f"{{ {var} cmr:lastModificationDate ?modified }}" in q, (var, q)
        assert "UNION" in q and "MAX(?modified)" in q


def _held(corpus: Path, tree: dict) -> Path:
    unit = corpus / src.unit_rel("it", FORMEX[0])
    unit.mkdir(parents=True)
    (unit / rdf.TREE_FILE).write_bytes(rdf.dumps(tree))
    return unit


def test_a_manifestation_replaced_after_the_held_tree_is_fetched_and_a_current_unit_is_not(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(cellar, "fetch_unit_from_tree",
                        lambda base, celex, lang, dest, trees=None, **kw: called.append(celex)
                        or cellar.Fetched(files={}, moved=1))
    ep = SimpleNamespace(url="https://cellar.test")
    rel = cellar.rel_path("it", FORMEX[0])

    def remote(when: str) -> RemoteFile:
        return RemoteFile(rel_path=rel, remote=cellar.encode(FORMEX[0], "it"), mtime=cellar._epoch(when))
    # held before the replacement: every level at the work's date
    old = _held(tmp_path / "old", _tree(WORK, WORK))
    assert cellar.TREE_UNITS.fetch(ep, remote(LATER), old / rdf.TREE_FILE) == 1 and called, \
        "the work's date unchanged, the manifestation's later: fetched"
    called.clear()
    # held after it: the listing's latest is the held tree's own
    now = _held(tmp_path / "now", _tree(WORK, LATER))
    assert cellar.TREE_UNITS.fetch(ep, remote(LATER), now / rdf.TREE_FILE) == 0 and not called, \
        "current: no request, never fetched pass after pass"


def test_the_branch_notice_states_the_same_latest():
    """Parser 3's notice: its WORK, EXPRESSION and MANIFESTATION dates."""
    from app.plugins.eurlex import notice as notice_mod
    note = notice_mod.parse((FORMEX[2] / src.NOTICE).read_bytes())
    stamps = list(note.dated.values()) + [note.last_modified]
    assert note.modified == notice_mod.latest(stamps) and note.modified is not None


def test_a_window_kept_in_the_work_date_shape_is_listed_again(tmp_path, monkeypatch):
    from app.acquire import cellar_listing
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path)
    cellar_listing.keep("https://cellar.test", 2015, ("it",), 1, WORK, [["32015D1735", "it", 1]])
    assert cellar_listing.held("https://cellar.test", 2015, ("it",), 1, WORK) == [["32015D1735", "it", 1]]
    monkeypatch.setattr(cellar_listing, "SHAPE", "work-date")
    assert cellar_listing.held("https://cellar.test", 2015, ("it",), 1, WORK) is None
