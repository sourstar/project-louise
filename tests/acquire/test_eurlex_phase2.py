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
                        lambda base, celex, lang, dest: called.append((celex, lang, dest))
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
