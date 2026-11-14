"""One non-inferred tree per WORK (app/acquire/work_trees.py): asked of Cellar
once for every language, or taken from the Data Dump's MTD, and used for a
unit only where the unit's own notice dates it current (München's probe,
2026-11-14: a tree costs 6.7 s, 23x a notice; parser 3 asked it 24 times per
work). Against the local Cellar mock of tests/acquire/test_cellar.py."""
from __future__ import annotations

import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.acquire import cellar, datadump, work_trees
from app.plugins.eurlex import rdf
from app.plugins.eurlex import source as src
from tests.acquire.test_cellar import ACT, Cellar, _cellar, _dated_tree, _spec

UUID = "ae929f3c-4a24-11e6-9c64-01aa75ed71a1"
D1, D2 = "2016-07-01T10:00:00.000+02:00", "2026-11-14T09:00:00.000+01:00"


@pytest.fixture
def mock():
    m = Cellar()
    for lang in ("en", "fr"):
        m.add(ACT, lang, ACT, uuid=UUID, manif_modified=D1)
    yield m
    m.server.shutdown()


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(cellar.time, "sleep", lambda s: None)


def _fetch(mock, root: Path, lang: str, trees=None, dump=None):
    return cellar.fetch_unit(mock.base, ACT, lang, root / src.unit_rel(lang, ACT), trees=trees, dump=dump)


def _tree_of(root: Path, lang: str) -> bytes:
    return (root / src.unit_rel(lang, ACT) / rdf.TREE_FILE).read_bytes()


def test_the_work_s_tree_is_asked_once_for_every_language_and_each_slice_is_cellar_s(mock, tmp_path):
    trees = work_trees.WorkTrees(tmp_path / "trees")
    a, b = _fetch(mock, tmp_path / "c", "en", trees), _fetch(mock, tmp_path / "c", "fr", trees)
    assert mock.rdf_asked == [ACT], "one tree for the two languages"
    assert (a.tree_from, b.tree_from) == ("cellar", "kept")
    # each language's slice is the one a fetch without the store lands
    for lang in ("en", "fr"):
        _fetch(mock, tmp_path / "plain", lang)
        assert _tree_of(tmp_path / "c", lang) == _tree_of(tmp_path / "plain", lang)
    assert _tree_of(tmp_path / "c", "en") != _tree_of(tmp_path / "c", "fr"), "scoped to each language"


def test_a_tree_held_from_before_a_change_cellar_dated_is_asked_again(mock, tmp_path):
    trees = work_trees.WorkTrees(tmp_path / "trees")
    _fetch(mock, tmp_path / "c", "en", trees)
    mock.units[(ACT, "fr")]["manif_modified"] = D2          # Cellar changed the French manifestation
    got = _fetch(mock, tmp_path / "c", "fr", trees)
    assert got.tree_from == "cellar" and mock.rdf_asked == [ACT, ACT]
    assert _fetch(mock, tmp_path / "c", "en", trees).tree_from == "kept", "the new tree is the one kept"


def test_a_notice_that_dates_nothing_never_takes_a_held_tree(tmp_path):
    m = Cellar()
    try:
        m.add(ACT, "en", ACT)                               # no uuid: an undated notice
        m.add(ACT, "fr", ACT)
        trees = work_trees.WorkTrees(tmp_path / "trees")
        assert _fetch(m, tmp_path, "en", trees).tree_from == "cellar"
        assert _fetch(m, tmp_path, "fr", trees).tree_from == "cellar"
        assert m.rdf_asked == [ACT, ACT]
    finally:
        m.server.shutdown()


def _mtd(root: Path, uuid: str, body: bytes) -> datadump.Dumps:
    folder = root / "datadump-it"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "LEG_MTD.zip", "w") as z:
        z.writestr(f"{uuid}/{datadump.MTD_MEMBER}", body)
    (folder / datadump.STATEMENT).write_text(json.dumps({"language": "it", "created": "2026-11-08T00:00:00+14:00"}))
    return datadump.Dumps(root, log=lambda *_: None)


def test_the_dump_s_mtd_gives_the_tree_where_the_notice_dates_it_current(mock, tmp_path):
    dumps = _mtd(tmp_path / "dump", UUID, _dated_tree(mock.base, UUID, {"en": D1, "fr": D1}))
    trees = work_trees.WorkTrees(tmp_path / "trees", dumps)
    got = _fetch(mock, tmp_path / "c", "en", trees)
    assert got.tree_from == "dump" and mock.rdf_asked == []
    _fetch(mock, tmp_path / "plain", "en")
    assert _tree_of(tmp_path / "c", "en") == _tree_of(tmp_path / "plain", "en")
    assert _fetch(mock, tmp_path / "c", "fr", trees).tree_from == "kept"


def test_an_mtd_tree_older_than_cellar_s_is_not_taken(mock, tmp_path):
    dumps = _mtd(tmp_path / "dump", UUID, _dated_tree(mock.base, UUID, {"en": D1, "fr": D1}))
    mock.units[(ACT, "en")]["manif_modified"] = D2
    got = _fetch(mock, tmp_path / "c", "en", work_trees.WorkTrees(tmp_path / "trees", dumps))
    assert got.tree_from == "cellar" and mock.rdf_asked == [ACT]


def test_a_manifestation_the_notice_does_not_list_makes_the_tree_another_one(mock):
    tree = rdf.read(_dated_tree(mock.base, UUID, {"en": D1}))
    dated = {_cellar(mock.base, UUID): D1, _cellar(mock.base, UUID, "en"): D1}
    assert not work_trees.matches(tree, dated), "the tree holds .0006.02, the notice lists none"
    dated[_cellar(mock.base, UUID, "en", True)] = D1
    assert work_trees.matches(tree, dated)
    assert not work_trees.matches(tree, {k: v for k, v in dated.items() if k != _cellar(mock.base, UUID)}), \
        "no work date, nothing to hold the tree to"
    assert not work_trees.matches(tree, {**dated, _cellar(mock.base, UUID): D2})


def test_two_languages_fetched_at_once_ask_one_tree(mock, tmp_path):
    trees = work_trees.WorkTrees(tmp_path / "trees")
    with ThreadPoolExecutor(2) as pool:
        got = list(pool.map(lambda lang: _fetch(mock, tmp_path / "c", lang, trees), ["en", "fr"]))
    assert sorted(g.tree_from for g in got) == ["cellar", "kept"] and mock.rdf_asked == [ACT]


def test_the_unit_fetcher_keeps_trees_beside_the_corpus_and_records_vs_origin_asks_cellar(mock, tmp_path,
                                                                                        monkeypatch):
    from app.acquire.spec import RemoteFile
    monkeypatch.setattr(work_trees, "_STORES", {})
    monkeypatch.setattr(datadump, "_DUMPS", datadump.Dumps(None))
    corpus = tmp_path / "corpus"
    for lang in ("en", "fr"):
        remote = RemoteFile(rel_path=cellar.rel_path(lang, ACT), remote=cellar.encode(ACT, lang))
        cellar.UNITS.fetch(_spec(mock).endpoints[0], remote, corpus / remote.rel_path)
    assert mock.rdf_asked == [ACT]
    assert list((corpus / ".pack" / work_trees.KIND).rglob("*.json.gz")), "derived, beside the corpus"
    cellar.UNITS.refetch(_spec(mock), src.unit_rel("fr", ACT), tmp_path / "again")
    assert mock.rdf_asked == [ACT, ACT], "the origin is asked, never the store"
