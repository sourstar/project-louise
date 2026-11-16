"""The EUR-Lex units built from the Data Dump alone (app/acquire/eurlex_dump.py;
operator, 2026-11-15, B). The dump is laid out as München measured the real
one — `datadump-<l>/` with its `datadump.json`, LEG_<L>_FMX.zip and
LEG_<L>_HTML.zip of `<work uuid>/<form>/<name>`, and LEG_MTD.zip of
`<work uuid>/tree_non_inferred.rdf` — from München's three Italian units."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import plugin_io
from app.acquire import cellar, eurlex_dump
from app.plugins.base import DiscoverState
from app.plugins.eurlex import SOURCE, rdf
from app.plugins.eurlex import source as src

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "eurlex" / "eurlex-dumpfirst"
MTD = "LEG_MTD.tree_non_inferred.rdf"
FORMEX = ("31972R1473", "81716b12-9c7b-4b5f-ba41-ddd13747a029", FIX / "formex" / "31972R1473", "fmx4")
HTML = ("31978A0060", "9f8d2f10-1d07-40df-b89c-de49ee84556c", FIX / "html" / "31978A0060", "html")
CONS = ("01979D0491-20061025", "1e02d77b-08fb-43c7-9022-32c41a30dc34",
        FIX / "consolidation" / "01979D0491-20061025", None)


def _items(fx: Path) -> dict[str, bytes]:
    return {f.name: f.read_bytes() for f in fx.iterdir() if f.name not in (MTD, src.NOTICE, rdf.TREE_FILE)}


def _dump(base: Path, *, drop: str | None = None) -> Path:
    """The dump: Italian items of the Formex and HTML acts, and the MTD of
    all three works (the third, 32006D0717's, with no Italian item)."""
    folder = base / "datadump-it"
    folder.mkdir(parents=True)
    (folder / "datadump.json").write_text(json.dumps({"language": "it",
                                                       "created": "2026-11-15T01:00:00+01:00"}))
    with zipfile.ZipFile(folder / "LEG_IT_FMX.zip", "w") as z:
        for n, b in _items(FORMEX[2]).items():
            if n != drop:
                z.writestr(f"{FORMEX[1]}/fmx4/{n}", b)
    with zipfile.ZipFile(folder / "LEG_IT_HTML.zip", "w") as z:
        for n, b in _items(HTML[2]).items():
            z.writestr(f"{HTML[1]}/html/{n}", b)
    with zipfile.ZipFile(folder / "LEG_MTD.zip", "w") as z:
        for _c, uuid, fx, _f in (FORMEX, HTML, CONS):
            z.writestr(f"{uuid}/tree_non_inferred.rdf", (fx / MTD).read_bytes())
    return base


@pytest.fixture
def serial(monkeypatch):
    """One process: the children's caches are this process's."""
    from app import parallel
    monkeypatch.setattr(parallel, "processes", lambda: 1)
    eurlex_dump._LANGS.clear()
    eurlex_dump._MTDS.clear()


def test_every_unit_the_dump_holds_is_built_from_it_alone(tmp_path, serial):
    corpus = tmp_path / "corpus"
    got = eurlex_dump.build(_dump(tmp_path / "dump"), corpus, None, log=lambda m: None)
    c = got["it"]
    assert (c["built"], c["not-in-language"]) == (2, 1), c
    for celex, _uuid, fx, _form in (FORMEX, HTML):
        unit = corpus / src.unit_rel("it", celex)
        held = {f.name: f.read_bytes() for f in unit.iterdir()}
        assert src.NOTICE not in held
        assert held.pop(rdf.TREE_FILE) == (fx / rdf.TREE_FILE).read_bytes(), "the tree Cellar serves"
        assert held == _items(fx), "the items Cellar serves, every one of the form"
    again = eurlex_dump.build(_dump(tmp_path / "dump2"), corpus, ["it"], log=lambda m: None)
    assert again["it"]["held"] == 2 and not again["it"]["built"], "a landed unit is never written again"


def test_a_form_the_dump_does_not_hold_whole_is_left_to_the_api(tmp_path, serial):
    """The Formex body missing: the act is not built from its HTML — the
    unit would not be the one Cellar gives (cellar.form_of)."""
    corpus = tmp_path / "corpus"
    got = eurlex_dump.build(_dump(tmp_path / "dump", drop="L_1972160IT.01000101.xml"), corpus, ["it"],
                            log=lambda m: None)
    assert got["it"]["incomplete"] == 1 and got["it"]["built"] == 1
    assert not (corpus / src.unit_rel("it", FORMEX[0])).exists()


def test_a_language_the_dump_lacks_is_said(tmp_path, serial):
    said = []
    got = eurlex_dump.build(_dump(tmp_path / "dump"), tmp_path / "corpus", ["it", "fr"], log=said.append)
    assert list(got) == ["it"] and any("no dump of fr" in m for m in said)


def test_records_vs_origin_refetches_the_same_unit_from_cellar(tmp_path, serial, monkeypatch):
    """`fetch_unit_from_tree` — Cellar's tree, then the items it names — lands
    the unit the dump built, byte for byte: the same origin_hash."""
    corpus = tmp_path / "corpus"
    eurlex_dump.build(_dump(tmp_path / "dump"), corpus, ["it"], log=lambda m: None)
    served: dict[str, bytes] = {}
    asked: list[str] = []
    for celex, _uuid, fx, form in (FORMEX, HTML):
        served[cellar.resource_url("https://cellar.test", celex)] = (fx / MTD).read_bytes()
        scoped = rdf.loads((fx / rdf.TREE_FILE).read_bytes())
        from app.plugins.eurlex import from_tree
        for it in from_tree.notice(scoped, "ITA", celex).items[form]:
            served[it.uri] = (fx / it.name).read_bytes()

    def get(client, url, headers, what, *, charge, ok=(200,)):
        asked.append(headers.get("Accept", ""))
        charge(len(served[url]))
        return SimpleNamespace(content=served[url], headers={})
    monkeypatch.setattr(cellar, "_get", get)
    spec = SimpleNamespace(endpoints=[SimpleNamespace(url="https://cellar.test")])
    for celex, *_ in (FORMEX, HTML):
        rel = src.unit_rel("it", celex)
        out, moved = cellar.UNITS.refetch(spec, rel, tmp_path / "again", parser=4)
        assert moved > 0 and sorted(p.name for p in out.iterdir()) == \
            sorted(p.name for p in (corpus / rel).iterdir())
        a = SOURCE.fetch(next(r for r in SOURCE.discover(DiscoverState(corpus_root=corpus))
                              if r.locator.endswith(celex)))
        b = SOURCE.fetch(next(r for r in SOURCE.discover(DiscoverState(corpus_root=tmp_path / "again"))
                              if r.locator.endswith(celex)))
        assert a.raw_hash == b.raw_hash
    assert not [h for h in asked if "notice=branch" in h], "no notice asked"


def test_the_units_parse_as_parser_4_records(tmp_path, serial):
    corpus = tmp_path / "corpus"
    eurlex_dump.build(_dump(tmp_path / "dump"), corpus, ["it"], log=lambda m: None)
    refs = list(SOURCE.discover(DiscoverState(corpus_root=corpus)))
    assert len(refs) == 2 and all(r.error is None for r in refs)
    for r in refs:
        doc = plugin_io.canonical_doc(SOURCE, {"native_id": r.native_id, "path": r.locator})
        assert doc.sections and doc.extra["in_force"] is True and "history" not in doc.extra


def test_an_earlier_corpus_s_units_the_dump_did_not_give_are_carried_without_their_notice(tmp_path, serial):
    """RULE 1: the consolidation an earlier per-unit fetch landed (notice,
    tree, items) — never in the dump — linked into the history slice with
    its tree and items, the same bytes; the Formex act the dump built is
    left as the dump built it."""
    old = tmp_path / "old"
    for celex, _uuid, fx, _f in (FORMEX, CONS):
        unit = old / src.unit_rel("it", celex)
        unit.mkdir(parents=True)
        for f in fx.iterdir():
            if f.name != MTD:
                shutil.copy(f, unit / f.name)
    corpus = tmp_path / "corpus"
    eurlex_dump.build(_dump(tmp_path / "dump"), corpus, ["it"], log=lambda m: None)
    got = eurlex_dump.carry(old, corpus, log=lambda m: None)
    assert (got["held"], got["history"]) == (1, 1), got
    unit = corpus / src.HISTORY / src.unit_rel("it", CONS[0])
    names = sorted(p.name for p in unit.iterdir())
    assert names == sorted(["CL1979D0491IT0040010.0001.doc.xml", "CL1979D0491IT0040010.0001.xml",
                            rdf.TREE_FILE])
    assert (unit / rdf.TREE_FILE).stat().st_ino == \
        (old / src.unit_rel("it", CONS[0]) / rdf.TREE_FILE).stat().st_ino, "linked, not copied"
    refs = {Path(r.locator).name: r for r in SOURCE.discover(DiscoverState(corpus_root=corpus))}
    assert set(refs) == {FORMEX[0], HTML[0], CONS[0]}
    doc = plugin_io.canonical_doc(SOURCE, {"native_id": refs[CONS[0]].native_id,
                                           "path": refs[CONS[0]].locator})
    assert doc.extra["history"] is True and doc.extra["work_celex"] == "31979D0491"


def test_a_parser_4_collection_s_per_unit_pass_is_held_back(monkeypatch, tmp_path):
    """Bulk first: before the dump is built into it, nothing is listed or
    asked of Cellar for it (tests/acquire/test_eurlex_phase2.py: after);
    parser 3's pass runs as before."""
    import sqlite3
    from app import db
    from app.acquire import engine, specs
    spec = specs.ACQUIRE_REGISTRY["eurlex"]
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)

    def coll(v):
        return SimpleNamespace(name="eurlex4", corpus_dir=tmp_path, slices=None,
                               codex=SimpleNamespace(parser=SimpleNamespace(version=v)))
    assert spec.inventory.held_back(coll(3)) is None
    why = spec.inventory.held_back(coll(4))
    assert "Data Dump" in why and "not asked" in why
    called = []
    monkeypatch.setattr(engine, "_run_acquisition", lambda *a, **k: called.append(1))
    monkeypatch.setattr(engine, "_require_license_ack", lambda *a, **k: None)
    got = engine.run_acquisition(con, coll(4), spec, "default")
    assert got["stopped"] == why and got["fetched"] == 0 and not called


def test_the_unit_origin_carries_the_codex_s_parser():
    from app.acquire import unit_origin
    got = unit_origin.for_codex(SimpleNamespace(plugin="eurlex", parser=SimpleNamespace(version=4)))
    try:
        assert got.parser == 4
    finally:
        got.close()


def test_the_command_resolves_its_collection(monkeypatch, tmp_path):
    """`louise dev eurlex-dump` reaches its own checks (2026-11-16 on the node:
    a NameError on `collections_mod` before the first one)."""
    from app import collections as collections_mod, louise
    monkeypatch.setattr(collections_mod, "by_name", lambda name: None)
    a = SimpleNamespace(collection="nope", dump=str(tmp_path), languages=None, carry_from=None)
    with pytest.raises(SystemExit, match="not a held eurlex collection"):
        louise.cmd_dev_eurlex_dump(a)
