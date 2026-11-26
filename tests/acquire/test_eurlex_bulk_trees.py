"""THE TREE FROM THE BULK (2026-11-26, EUR-Lex5.next step 8): parser 4 asked
Cellar for the tree of every one of the 124,511 units the Data Dump lacks
whole — ~1,180 s of every 300 s of the pass, items ~20 s — while the dump's
MTD holds each of those works' trees, byte for byte the one Cellar served
(München's proof on 20 units). Bulk first: the tree is taken from the MTD by
the unit's CELEX, checked as the work's own; only the items are asked."""
from __future__ import annotations

import zipfile
from types import SimpleNamespace

from app.acquire import cellar, datadump, work_trees
from app.plugins.eurlex import from_tree, rdf
from tests.acquire.test_eurlex_dump import FORMEX, MTD

CELEX, UUID, FX = FORMEX[0], FORMEX[1], FORMEX[2]


def _mtd(tmp_path, members: dict[str, bytes]):
    p = tmp_path / "LEG_MTD.zip"
    with zipfile.ZipFile(p, "w") as z:
        for n, b in members.items():
            z.writestr(n, b)
    return p


def _bulk(tmp_path, members):
    p = _mtd(tmp_path, members)
    index = work_trees.mtd_index([p], tmp_path / "index", threads=2, log=lambda m: None)
    z = zipfile.ZipFile(p)
    dumps = SimpleNamespace(mtd_tree=lambda u: rdf.read(z.read(f"{u}/{datadump.MTD_MEMBER}")))
    return work_trees.BulkTrees(dumps, index), index


def test_the_index_names_each_celex_s_work_and_is_kept(tmp_path):
    tree = (FX / MTD).read_bytes()
    bulk, index = _bulk(tmp_path, {f"{UUID}/{datadump.MTD_MEMBER}": tree})
    assert index[CELEX] == UUID
    assert len(list((tmp_path / "index").glob("*.json.gz"))) == 1
    again = work_trees.mtd_index([tmp_path / "LEG_MTD.zip"], tmp_path / "index", threads=1,
                                 log=lambda m: (_ for _ in ()).throw(AssertionError("built again")))
    assert again == index


def test_a_celex_two_works_state_is_left_to_cellar(tmp_path):
    tree = (FX / MTD).read_bytes()
    other = "00000000-0000-0000-0000-000000000000"
    _, index = _bulk(tmp_path, {f"{UUID}/{datadump.MTD_MEMBER}": tree, f"{other}/{datadump.MTD_MEMBER}": tree})
    assert CELEX not in index


def test_a_unit_the_dump_lacks_takes_its_tree_from_the_mtd_and_asks_only_its_items(tmp_path, monkeypatch):
    tree = (FX / MTD).read_bytes()
    bulk, _ = _bulk(tmp_path, {f"{UUID}/{datadump.MTD_MEMBER}": tree})
    note = from_tree.notice(rdf.scope(rdf.read(tree), "ITA"), "ITA", CELEX)
    served = {it.uri: (FX / it.name).read_bytes() for it in note.items["fmx4"]}
    asked = []

    def get(client, url, headers, what, *, charge, ok=(200,)):
        asked.append(url)
        return SimpleNamespace(content=served[url], headers={})
    monkeypatch.setattr(cellar, "_get", get)
    got = cellar.fetch_unit_from_tree("https://cellar.test", CELEX, "it", tmp_path / "u", client=object(),
                                      bulk=bulk)
    assert got.tree_from == "dump" and got.tree_s == 0.0
    assert sorted(asked) == sorted(served), "the items only: no tree asked of Cellar"
    assert (tmp_path / "u" / rdf.TREE_FILE).read_bytes() == rdf.dumps(rdf.scope(rdf.read(tree), "ITA"))


def test_a_bulk_tree_older_than_the_listing_is_asked_of_cellar(tmp_path, monkeypatch):
    tree = (FX / MTD).read_bytes()
    bulk, _ = _bulk(tmp_path, {f"{UUID}/{datadump.MTD_MEMBER}": tree})
    assert cellar._bulk_tree(bulk, CELEX, "ITA", None) is not None
    assert cellar._bulk_tree(bulk, CELEX, "ITA", 4_000_000_000) is None, "listed after the MTD's date"
    assert cellar._bulk_tree(bulk, "31999R9999", "ITA", None) is None, "the MTD lacks the work"
