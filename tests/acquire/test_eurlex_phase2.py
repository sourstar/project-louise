"""EUR-Lex on parser 4, after the Data Dump (operator, 2026-11-15: B and
"Add history as a download and embedding option"; CLAUDE.md, bulk first):
the per-unit pass asks Cellar only what the dump lacks — a unit held and
current costs no request — places each unit not held yet in the base or the
history slice, never moves a held one, and builds a newer dump first."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.acquire import cellar, engine, eurlex_dump
from app.acquire.spec import Filter, RemoteFile
from app.plugins.eurlex import source as src
from tests.acquire.test_cellar import ACT, CONS, _spec, listing_cache, mock, no_wait  # noqa: F401
from tests.acquire.test_eurlex_dump import FORMEX, HTML, _dump, serial  # noqa: F401

REPEALED = "32016R1037"


def coll(corpus: Path, version: int = 4, slices=None):
    return SimpleNamespace(name="eurlex4", corpus_dir=corpus, slices=slices,
                           codex=SimpleNamespace(parser=SimpleNamespace(version=version)))


def _rels(inv, spec, langs=("fr",)) -> dict[str, str]:
    flt = Filter(include=tuple(f"{p}lang={l}/*" for l in langs for p in ("", "history/")))
    return {cellar.unit_of(f)["celex"]: f.rel_path
            for f in cellar.listing(spec.endpoints[0], spec, flt, years=range(2016, 2017),
                                    place=inv._place)}


def test_a_unit_not_held_is_placed_by_its_in_force_and_a_held_one_where_it_is(mock, tmp_path):
    mock.add(REPEALED, "fr", ACT, in_force="false")
    spec = _spec(mock)
    corpus = tmp_path / "corpus"
    inv = cellar.TreeInventory(corpus)
    got = _rels(inv, spec)
    assert got == {ACT: f"lang=fr/2016/{ACT}/tree.json",
                   CONS: f"history/lang=fr/2016/{CONS}/tree.json",
                   REPEALED: f"history/lang=fr/2016/{REPEALED}/tree.json"}
    assert any("resource_legal_in-force" in q for q in mock.queries()), "the in-force asked with the page"
    # held: never moved, whatever Cellar says now
    for rel in (f"lang=fr/2016/{REPEALED}", f"{src.HISTORY}/lang=fr/2016/{ACT}"):
        (corpus / rel).mkdir(parents=True)
        (corpus / rel / "tree.json").write_text("{}")
    asked = len(mock.queries())
    got = _rels(inv, spec)
    assert got[REPEALED] == f"lang=fr/2016/{REPEALED}/tree.json"
    assert got[ACT] == f"history/lang=fr/2016/{ACT}/tree.json"
    assert not [q for q in mock.queries()[asked:] if "OFFSET" in q], "the window kept, with its in-force"


def test_parser_3_s_listing_is_as_it_was(mock):
    spec = _spec(mock)
    got = list(cellar.listing(spec.endpoints[0], spec, Filter(include=("lang=fr/*",)), years=range(2016, 2017)))
    assert sorted(f.rel_path for f in got) == [f"lang=fr/2016/{CONS}/tree.json", f"lang=fr/2016/{ACT}/tree.json"]
    assert not any("in-force" in q for q in mock.queries())


def test_a_history_profile_alone_lists_its_language():
    assert cellar.languages_of(Filter(include=("history/lang=it/*",))) == ("it",)


def _held_unit(corpus: Path, celex: str, modified: str) -> Path:
    """A held parser-4 unit whose work states `modified`."""
    from tests.acquire.test_eurlex_dump import FIX, MTD
    from app.plugins.eurlex import rdf
    fx = {FORMEX[0]: FORMEX[2], HTML[0]: HTML[2]}[celex]
    tree = rdf.loads((fx / rdf.TREE_FILE).read_bytes())
    for props in tree["nodes"].values():
        if cellar_mod_key in props:
            props[cellar_mod_key] = [{"@value": modified,
                                      "@type": "http://www.w3.org/2001/XMLSchema#dateTime"}]
    unit = corpus / src.unit_rel("it", celex)
    unit.mkdir(parents=True)
    (unit / rdf.TREE_FILE).write_bytes(rdf.dumps(tree))
    return unit


cellar_mod_key = "http://publications.europa.eu/ontology/cdm/cmr#lastModificationDate"


def test_a_unit_held_and_current_costs_no_request_and_a_stale_one_is_fetched_tree_first(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    unit = _held_unit(corpus, FORMEX[0], "2022-07-22T18:52:20.917+02:00")
    called = []
    monkeypatch.setattr(cellar, "fetch_unit_from_tree",
                        lambda base, celex, lang, dest, trees=None: called.append((celex, lang, dest))
                        or cellar.Fetched(files={}, moved=7))
    remote = RemoteFile(rel_path=cellar.rel_path("it", FORMEX[0]), remote=cellar.encode(FORMEX[0], "it"),
                        mtime=cellar._epoch("2022-07-22T16:52:20Z"))
    ep = SimpleNamespace(url="https://cellar.test")
    assert cellar.TREE_UNITS.fetch(ep, remote, unit / "tree.json") == 0 and not called
    later = RemoteFile(rel_path=remote.rel_path, remote=remote.remote, mtime=cellar._epoch("2025-01-01"))
    assert cellar.TREE_UNITS.fetch(ep, later, unit / "tree.json") == 7
    assert called == [(FORMEX[0], "it", unit)], "into the place it is held"
    new = corpus / "lang=it/2016/32016R0001"
    cellar.TREE_UNITS.fetch(ep, RemoteFile(rel_path="x", remote=cellar.encode("32016R0001", "it"), mtime=1),
                            new / "tree.json")
    assert called[-1] == ("32016R0001", "it", new), "a unit not held: fetched, never from a notice"


def test_cellar_is_asked_nothing_before_the_dump_of_every_held_language_is_built(tmp_path, serial):
    corpus = tmp_path / "corpus"
    c = coll(corpus, slices=("lang=it", "history/lang=fr"))
    inv = cellar.INVENTORY
    why = inv.held_back(c)
    assert "of fr, it is not built" in why and "bulk first" in why
    eurlex_dump.build(_dump(tmp_path / "dump"), corpus, ["it"], log=lambda m: None)
    assert eurlex_dump.stamp(corpus) == {"it": "2026-11-15T01:00:00+01:00"}
    assert "of fr is not built" in inv.held_back(c)
    assert len(cellar.held_languages(coll(corpus))) == 24, "a file naming no slices holds them all"
    eurlex_dump._stamp(corpus, {"fr": "2026-11-15T01:00:00+01:00"})
    assert inv.held_back(c) is None
    assert inv.held_back(coll(corpus, version=3)) is None, "parser 3 as before"


def test_the_collection_s_spec_is_the_tree_one_on_parser_4(tmp_path):
    spec = cellar_spec()
    four = engine.effective_spec(coll(tmp_path), spec)
    assert isinstance(four.inventory, cellar.TreeInventory) and four.unit_fetcher is cellar.TREE_UNITS
    assert four.inventory.corpus == tmp_path
    three = engine.effective_spec(coll(tmp_path, version=3), spec)
    assert three.inventory is cellar.INVENTORY and three.unit_fetcher is cellar.UNITS


def cellar_spec():
    from app.acquire import specs
    return specs.ACQUIRE_REGISTRY["eurlex"]


def test_a_newer_dump_is_built_first_and_refreshes_what_it_states_modified_later(tmp_path, serial):
    """Bulk first whenever a newer bulk is there: a held unit the newer dump
    states modified later is rebuilt from it in place; one as recent is kept."""
    corpus = tmp_path / "corpus"
    dump = _dump(tmp_path / "dump")
    # the corpus built from an older dump, its Formex act's tree older than the dump's
    old = _held_unit(corpus, FORMEX[0], "2020-01-01T00:00:00Z")
    eurlex_dump._stamp(corpus, {"it": "2026-11-08T01:00:00+01:00"})
    got = eurlex_dump.build_newer(dump, corpus, ["it"], log=lambda m: None)
    assert got["it"]["refreshed"] == 1 and got["it"]["built"] == 1, got
    assert sorted(p.name for p in old.iterdir()) == sorted([*(p.name for p in FORMEX[2].iterdir()
                                                             if p.suffix == ".xml" and p.name != "notice.xml"),
                                                           "tree.json"])
    assert eurlex_dump.stamp(corpus)["it"] == "2026-11-15T01:00:00+01:00"
    assert eurlex_dump.build_newer(dump, corpus, ["it"], log=lambda m: None) == {}, "built in already"


def test_prepare_builds_a_newer_dump_before_the_pass(tmp_path, serial, monkeypatch):
    from app.acquire import datadump
    corpus = tmp_path / "corpus"
    monkeypatch.setattr(datadump, "_DUMPS", datadump.Dumps(_dump(tmp_path / "dump")))
    pending = [RemoteFile(rel_path=cellar.rel_path("it", FORMEX[0]), remote=cellar.encode(FORMEX[0], "it"))]
    assert cellar.TREE_UNITS.prepare(pending, pending, coll(corpus), None) == []
    assert (corpus / src.unit_rel("it", FORMEX[0]) / "tree.json").is_file()
    assert eurlex_dump.stamp(corpus) == {"it": "2026-11-15T01:00:00+01:00"}


# ---- what the bulk lacks, as the bulk states it, first (München, 2026-11-16) --------------

def _incomplete_corpus(tmp_path):
    """A dump build that left 31972R1473 (it) incomplete: its Formex body missing."""
    corpus = tmp_path / "corpus"
    eurlex_dump.build(_dump(tmp_path / "dump", drop="L_1972160IT.01000101.xml"), corpus, ["it"],
                      log=lambda m: None)
    return corpus


def test_the_build_records_what_it_lacks(tmp_path, serial):  # noqa: F811
    corpus = _incomplete_corpus(tmp_path)
    assert eurlex_dump.incomplete(corpus) == {"it": {FORMEX[0]: {"in_force": "true", "tried": None}}}
    assert eurlex_dump.incomplete_due(corpus, ("it",)) == [("it", FORMEX[0], "true")]
    assert eurlex_dump.incomplete_due(corpus, ("fr",)) == []


def test_the_pass_asks_first_for_what_the_dump_lacks_without_a_listing(tmp_path, serial, mock):  # noqa: F811
    corpus = _incomplete_corpus(tmp_path)
    spec = _spec(mock)
    inv = cellar.TreeInventory(corpus, "eurlex4")
    got = inv.list(spec.endpoints[0], spec, Filter(include=("lang=it/*",)))
    assert [f.rel_path for f in got] == [f"lang=it/1972/{FORMEX[0]}/tree.json"]
    assert cellar.unit_of(got[0]) == {"celex": FORMEX[0], "lang": "it"} and got[0].mtime is None
    assert not mock.queries(), "no SPARQL: the dump said what it lacks"
    # a profile without that language lists as before
    assert all(f.rel_path.startswith(("lang=fr/", "history/lang=fr/"))
               for f in inv.list(spec.endpoints[0], spec, Filter(include=("lang=fr/*",))))


def test_a_unit_cellar_does_not_give_either_is_tried_once_and_the_listing_follows(tmp_path, serial, mock,  # noqa: F811
                                                                                  monkeypatch):
    corpus = _incomplete_corpus(tmp_path)
    remote = RemoteFile(rel_path=f"lang=it/1972/{FORMEX[0]}/tree.json", mtime=None,
                        remote=cellar.encode(FORMEX[0], "it"))

    def refuse(*a, **kw):
        raise cellar.TransportError("cellar: the tree of 31972R1473 (ITA) names no item of any form")
    monkeypatch.setattr(cellar, "fetch_unit_from_tree", refuse)
    with pytest.raises(cellar.TransportError):
        cellar.TREE_UNITS.fetch(SimpleNamespace(url="https://cellar.test"), remote, corpus / remote.rel_path)
    entry = eurlex_dump.incomplete(corpus)["it"][FORMEX[0]]
    assert "names no item" in entry["tried"] and eurlex_dump.incomplete_due(corpus, ("it",)) == []
    said = []
    spec = _spec(mock)
    inv = cellar.TreeInventory(corpus, "eurlex4")
    monkeypatch.setattr(inv, "_say", said.append)
    list(inv.list(spec.endpoints[0], spec, Filter(include=("lang=fr/*",))))
    assert mock.queries(), "none left to ask first: the complete listing"
    # a rebuild of the same dump keeps the verdict: not asked first again
    eurlex_dump.build(_dump(tmp_path / "dump2", drop="L_1972160IT.01000101.xml"), corpus, ["it"],
                      log=lambda m: None)
    assert eurlex_dump.incomplete(corpus)["it"][FORMEX[0]]["tried"] == entry["tried"]


def test_the_endpoint_going_away_is_no_verdict(tmp_path, serial, monkeypatch):  # noqa: F811
    corpus = _incomplete_corpus(tmp_path)
    remote = RemoteFile(rel_path=f"lang=it/1972/{FORMEX[0]}/tree.json", mtime=None,
                        remote=cellar.encode(FORMEX[0], "it"))

    def down(*a, **kw):
        raise cellar.TransportError("cellar: ConnectError: timed out")
    monkeypatch.setattr(cellar, "fetch_unit_from_tree", down)
    with pytest.raises(cellar.TransportError):
        cellar.TREE_UNITS.fetch(SimpleNamespace(url="https://cellar.test"), remote, corpus / remote.rel_path)
    assert eurlex_dump.incomplete_due(corpus, ("it",)) == [("it", FORMEX[0], "true")]


def test_the_listing_says_each_window(mock):
    said = []
    spec = _spec(mock)
    list(cellar.listing(spec.endpoints[0], spec, Filter(include=("lang=fr/*",)), years=range(2016, 2018),
                        say=said.append))
    assert [m.split(":")[1].split("(")[0].strip() for m in said] == ["listed 2016", "listed 2017"]
    assert all(" units in " in m for m in said)


@pytest.mark.parametrize("text, episode", [
    ("cellar: 503 to the tree of X (u)", True), ("cellar: 429 to the tree of X (u)", True),
    ("cellar: ReadTimeout: timed out", True), ("cellar: ConnectError: [Errno 111]", True),
    ("cellar: 404 to the tree of X (u)", False), ("cellar: the tree of X names no item of any form", False),
])
def test_an_episode_is_told_from_a_verdict(text, episode):
    assert cellar._episode(cellar.TransportError(text)) is episode


def test_cellar_is_asked_on_four_connections_and_the_report_splits_trees_from_items():
    """2026-11-16: two connections gave 582 units an hour at 0.1 MB/s
    (latency-bound); Cellar publishes no limit, fewer than five in flight is
    the practice reported. The report says where a unit's time goes."""
    from app.acquire import specs
    from app.transfer.manager import get_manager
    spec = specs.EURLEX_SPEC
    assert get_manager().endpoint_slots(spec.endpoints[0], spec) == 4
    units = cellar.TreeUnits()
    units._count(cellar.Fetched(files={}, moved=1 << 20, tree_s=2.5, items_s=4.0))
    (line,) = units.reports()
    assert "asking: trees 2 s, items 4 s" in line or "asking: trees 3 s, items 4 s" in line


# ---- one tree per work within a pass (München, 2026-11-18: trees 1,144 s, items 33 s) ------

def test_a_tree_cellar_served_serves_the_work_s_other_languages_within_the_window(tmp_path):
    import threading
    from app.acquire import work_trees
    now = [1_000_000.0]
    store = work_trees.RecentTrees(tmp_path / "trees", window_s=3600, clock=lambda: now[0])
    asked = []

    def ask():
        asked.append(1)
        return b"<rdf:RDF/>"
    import os
    assert store.get("32016R0679", ask) == (b"<rdf:RDF/>", "cellar")
    os.utime(store._path("32016R0679"), (now[0], now[0]))
    assert store.get("32016R0679", ask) == (b"<rdf:RDF/>", "kept") and len(asked) == 1
    now[0] += 3601
    assert store.get("32016R0679", ask)[1] == "cellar" and len(asked) == 2, "past the window: Cellar again"
    store._path("32016R0679").write_bytes(b"not gzip")
    os.utime(store._path("32016R0679"), (now[0], now[0]))
    assert store.get("32016R0679", ask)[1] == "cellar", "a file not whole is asked again"
    # two connections on one work at once: one request
    asked.clear()
    slow = threading.Event()

    def ask_slow():
        asked.append(1)
        slow.wait(0.3)
        return b"<rdf:RDF/>"
    got = []
    ts = [threading.Thread(target=lambda: got.append(store.get("32019L0790", ask_slow)[1])) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    os_kept = sorted(got)
    assert len(asked) == 1 and os_kept == ["cellar", "kept", "kept"]


def test_the_pass_asks_a_work_s_tree_once_for_its_units(tmp_path, monkeypatch):
    """fetch_unit_from_tree with the pass's store: the second unit of the
    work takes the kept tree, and the report says so."""
    from app.acquire import work_trees
    from app.plugins.eurlex import rdf
    from tests.acquire.test_eurlex_dump import CONS
    served = {cellar.resource_url("https://cellar.test", CONS[0]): (CONS[2] / rdf.TREE_FILE).read_bytes()}
    from app.plugins.eurlex import from_tree
    for it in from_tree.notice(rdf.loads(served[next(iter(served))]), "ITA", CONS[0]).items["fmx4"]:
        served[it.uri] = (CONS[2] / it.name).read_bytes()
    asked = []

    def get(client, url, headers, what, *, charge, ok=(200,)):
        asked.append(url)
        return SimpleNamespace(content=served[url], headers={})
    monkeypatch.setattr(cellar, "_get", get)
    monkeypatch.setattr(cellar, "_read_tree", lambda data, celex: rdf.loads(data))
    store = work_trees.RecentTrees(tmp_path / "trees")
    a = cellar.fetch_unit_from_tree("https://cellar.test", CONS[0], "it", tmp_path / "a", client=object(),
                                    trees=store)
    b = cellar.fetch_unit_from_tree("https://cellar.test", CONS[0], "it", tmp_path / "b", client=object(),
                                    trees=store)
    tree_url = cellar.resource_url("https://cellar.test", CONS[0])
    assert asked.count(tree_url) == 1 and (a.tree_from, b.tree_from) == ("cellar", "kept")
    assert b.tree_s == 0.0
    assert (tmp_path / "a" / rdf.TREE_FILE).read_bytes() == (tmp_path / "b" / rdf.TREE_FILE).read_bytes()
