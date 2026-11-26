"""THE TREE FROM THE BULK (2026-11-26, EUR-Lex5.next step 8): parser 4 asked
Cellar for the tree of every one of the 124,511 units the Data Dump lacks
whole — ~1,180 s of every 300 s of the pass, items ~20 s — while the dump's
MTD holds each of those works' trees, byte for byte the one Cellar served
(München's proof on 20 units). Bulk first: the tree is taken from the MTD of
the dump stamped into the corpus, by the unit's CELEX, checked as the work's
own; only the items are asked. Architecture's review of 1be767f6: never over
a held unit, never older than the date Cellar listed, back to Cellar's own
tree where the items do not answer, the index built before the pass."""
from __future__ import annotations

import json
import os
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from app.acquire import cellar, cellar_listing, eurlex_dump, work_trees
from app.acquire.spec import RemoteFile
from app.plugins.eurlex import from_tree, rdf
from tests.acquire.test_eurlex_acquisition_review import _served, _trees
from tests.acquire.test_eurlex_dump import FORMEX, MTD

CELEX, UUID, FX = FORMEX[0], FORMEX[1], FORMEX[2]
MEMBER = f"{UUID}/tree_non_inferred.rdf"
CREATED = "2026-11-15T01:00:00+01:00"


def _zip(p, members: dict[str, bytes]):
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w") as z:
        for n, b in members.items():
            z.writestr(n, b)
    return p


def _bulk(tmp_path, members, **kw):
    p = _zip(tmp_path / "mtd" / "LEG_MTD.zip", members)
    index = work_trees.mtd_index(p, tmp_path / "index", threads=2, log=lambda m: None, **kw)
    return work_trees.BulkTrees(p, index, log=lambda m: None), index


def test_the_index_names_each_celex_s_work_is_kept_and_an_old_one_removed(tmp_path):
    bulk, index = _bulk(tmp_path, {MEMBER: (FX / MTD).read_bytes()})
    assert index[CELEX] == UUID
    (tmp_path / "index" / "stale.json.gz").write_bytes(b"x")
    os.utime(tmp_path / "mtd" / "LEG_MTD.zip", ns=(1, 1))          # a new identity: built again
    work_trees.mtd_index(tmp_path / "mtd" / "LEG_MTD.zip", tmp_path / "index", threads=1, log=lambda m: None)
    assert [p.name for p in (tmp_path / "index").glob("*.json.gz")] != ["stale.json.gz"]
    assert len(list((tmp_path / "index").glob("*.json.gz"))) == 1
    again = work_trees.mtd_index(tmp_path / "mtd" / "LEG_MTD.zip", tmp_path / "index", threads=1,
                                 log=lambda m: (_ for _ in ()).throw(AssertionError("built again")))
    assert again[CELEX] == UUID


def test_a_celex_two_works_state_is_left_to_cellar_and_a_bad_member_is_counted(tmp_path, monkeypatch):
    other = "00000000-0000-0000-0000-000000000000"
    tree = (FX / MTD).read_bytes()
    real = zipfile.ZipFile.read
    monkeypatch.setattr(zipfile.ZipFile, "read", lambda self, n, *a: (_ for _ in ()).throw(zipfile.BadZipFile("x"))
                        if n.startswith("11111111") else real(self, n, *a))
    said = []
    p = _zip(tmp_path / "LEG_MTD.zip", {MEMBER: tree, f"{other}/tree_non_inferred.rdf": tree,
                                        "11111111-1111-1111-1111-111111111111/tree_non_inferred.rdf": tree})
    index = work_trees.mtd_index(p, tmp_path / "index", threads=2, log=said.append)
    assert CELEX not in index
    assert "1 member(s) that do not read" in said[-1]


def test_the_build_stops_between_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(work_trees, "INDEX_CHUNK", 1)
    p = _zip(tmp_path / "LEG_MTD.zip", {MEMBER: (FX / MTD).read_bytes()})
    with pytest.raises(work_trees.Stopped):
        work_trees.mtd_index(p, tmp_path / "index", threads=1, stop=lambda: True, log=lambda m: None)
    assert not list((tmp_path / "index").glob("*.json.gz")), "a stopped build keeps nothing"


def test_a_unit_landed_from_the_bulk_is_the_unit_landed_from_cellar(tmp_path, monkeypatch):
    """Cellar serving the same tree: the two units, byte for byte, file for file."""
    old, _ = _trees()
    asked = _served(monkeypatch, old)
    bulk, _ = _bulk(tmp_path, {MEMBER: old})
    a = cellar.fetch_unit_from_tree("https://cellar.test", CELEX, "it", tmp_path / "a", client=object(), bulk=bulk)
    n = len(asked)
    b = cellar.fetch_unit_from_tree("https://cellar.test", CELEX, "it", tmp_path / "b", client=object())
    assert (a.tree_from, b.tree_from) == ("dump", "cellar")
    assert not any("non-inferred tree" in w for w in asked[:n]), "from the bulk: the items only"
    files = sorted(os.listdir(tmp_path / "a"))
    assert files == sorted(os.listdir(tmp_path / "b"))
    assert all((tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes() for f in files)


def test_a_bulk_tree_older_than_the_listed_date_is_not_taken(tmp_path):
    old, _ = _trees()
    bulk, _ = _bulk(tmp_path, {MEMBER: old})
    assert cellar._bulk_tree(bulk, CELEX, "ITA", None) is not None
    assert cellar._bulk_tree(bulk, CELEX, "ITA", cellar._epoch("2026-11-19T15:38:00+01:00")) is None
    assert cellar._bulk_tree(bulk, "31999R9999", "ITA", None) is None, "the MTD lacks the work"


def test_items_the_bulk_tree_names_that_cellar_does_not_serve_send_the_unit_to_cellar_s_tree(tmp_path, monkeypatch):
    old, new = _trees()
    asked = _served(monkeypatch, new)
    real = cellar._get
    gone = {"first": True}

    def get(client, url, headers, what, **kw):
        if headers.get("Accept") != cellar.RDF_ACCEPT and gone.pop("first", False):
            raise cellar.TransportError("cellar: 404")
        return real(client, url, headers, what, **kw)
    monkeypatch.setattr(cellar, "_get", get)
    bulk, _ = _bulk(tmp_path, {MEMBER: old})
    got = cellar.fetch_unit_from_tree("https://cellar.test", CELEX, "it", tmp_path / "u", client=object(), bulk=bulk)
    assert got.tree_from == "cellar" and sum("non-inferred tree" in w for w in asked) == 1
    held = from_tree.notice(rdf.loads((tmp_path / "u" / rdf.TREE_FILE).read_bytes()), "ITA", CELEX)
    assert held.modified.startswith("2026-11-19"), "Cellar's own tree landed, not the bulk's"


def test_a_held_unit_on_the_due_path_never_takes_the_bulk_tree(tmp_path, monkeypatch):
    """Architecture's repro: held 2026-11-24, the MTD's 2022: the dump build
    left the held newer unit to the API, and the bulk's tree landed over it."""
    old, new = _trees()
    asked = _served(monkeypatch, new)
    corpus = tmp_path / "corpus"
    rel = cellar.rel_path("it", CELEX)
    (corpus / rel).parent.mkdir(parents=True)
    (corpus / rel).write_bytes(rdf.dumps(rdf.scope(rdf.read(new), "ITA")))
    bulk, _ = _bulk(tmp_path, {MEMBER: old})
    units = cellar.TreeUnits()
    units._bulk[str(corpus.resolve())] = {"it": bulk}
    monkeypatch.setattr(work_trees, "recent_for", lambda root: work_trees.RecentTrees(tmp_path / "recent"))
    remote = RemoteFile(rel_path=rel, mtime=None, remote=cellar.encode(CELEX, "it"))
    assert units._bulk_of(corpus, {"celex": CELEX, "lang": "it"}, remote, corpus / rel) == (None, None)
    (corpus / rel).unlink()
    assert units._bulk_of(corpus, {"celex": CELEX, "lang": "it"}, remote, corpus / rel)[0] is None, \
        "the unit's folder is there: held, whatever it holds"
    os.rmdir((corpus / rel).parent)
    key = str(corpus.resolve())
    units._after[key] = {"it": 1000.0}
    units._cover[key] = {(1972, "it")}
    units._dates[key] = {(CELEX, "it"): 900}
    assert units._bulk_of(corpus, {"celex": CELEX, "lang": "it"}, remote, corpus / rel) == (bulk, 900), \
        "not held, listed unchanged since the dump: the bulk's tree"


def _due_units(tmp_path, *, dates, cover, after=1000.0):
    units = cellar.TreeUnits()
    corpus = tmp_path / "corpus"
    key = str(corpus.resolve())
    units._bulk[key] = {"it": object()}
    units._after[key], units._cover[key], units._dates[key] = {"it": after}, cover, dates
    rel = cellar.rel_path("it", CELEX)
    remote = RemoteFile(rel_path=rel, mtime=None, remote=cellar.encode(CELEX, "it"))
    return units, corpus, remote, corpus / rel


def test_a_unit_changed_since_the_dump_or_absent_from_its_window_takes_cellar_s_tree(tmp_path):
    units, corpus, remote, dest = _due_units(tmp_path, dates={(CELEX, "it"): 2000}, cover={(1972, "it")})
    assert units._bulk_of(corpus, {"celex": CELEX, "lang": "it"}, remote, dest) == (None, None)
    units, corpus, remote, dest = _due_units(tmp_path, dates={}, cover={(1972, "it")})
    assert units._bulk_of(corpus, {"celex": CELEX, "lang": "it"}, remote, dest) == (None, None)


def test_a_unit_whose_window_is_not_known_whole_since_the_dump_waits(tmp_path):
    units, corpus, remote, dest = _due_units(tmp_path, dates={(CELEX, "it"): 900}, cover=set())
    with pytest.raises(cellar.WindowNotListed) as e:
        units._bulk_of(corpus, {"celex": CELEX, "lang": "it"}, remote, dest)
    assert cellar._episode(e.value), "deferred, never marked tried"


def _statement(folder, lang="it", created=CREATED):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "datadump.json").write_text(json.dumps({"language": lang, "created": created}))


@pytest.mark.parametrize("layout", ["dated", "flat"])
def test_the_mtd_read_is_the_one_of_the_dump_stamped_into_the_corpus(tmp_path, layout):
    base, corpus = tmp_path / "datadump", tmp_path / "corpus"
    if layout == "dated":
        mtd = _zip(base / "dump-2026-11-15" / "datadump-mtd" / "LEG_MTD.zip", {MEMBER: b"x"})
        for lang in ("it", "fr"):
            _statement(base / "dump-2026-11-15" / f"datadump-{lang}", lang)
            (base / "dump-2026-11-15" / f"datadump-{lang}" / "LEG_MTD.zip").symlink_to("../datadump-mtd/LEG_MTD.zip")
        _statement(base / "datadump-it", "it", "2026-11-08T01:00:00+01:00")     # the older cut, flat
        _zip(base / "datadump-it" / "LEG_MTD.zip", {MEMBER: b"older"})
    else:
        _statement(base / "datadump-it", "it")
        mtd = _zip(base / "datadump-it" / "LEG_MTD.zip", {MEMBER: b"x"})
    eurlex_dump._stamp(corpus, {"it": CREATED})
    assert work_trees.stamped_mtd(base, corpus, "it", log=lambda m: None) == mtd.resolve()
    said = []
    assert work_trees.stamped_mtd(base, corpus, "fr", log=said.append) is None, "fr has no dump stamped"


def test_the_kept_listing_vouches_only_for_windows_known_whole_after_the_dump(tmp_path, monkeypatch):
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    cellar_listing.keep("https://cellar.test", 1972, ("it",), 2, None, [[CELEX, "it", 100], ["31972R0001", "it", 7]])
    cellar_listing.keep("https://cellar.test", 1973, ("it", "fr"), 0, None, [])
    old = cellar_listing._path("https://cellar.test", 1972, ("it",))
    os.utime(old, (10, 10))                                       # known whole before the dump
    dates, cover = cellar_listing.coverage({(CELEX, "it")}, {"it": 50.0, "fr": 50.0})
    assert dates == {} and cover == {(1973, "it"), (1973, "fr")}, "an empty window still covers its languages"
    cellar_listing.vouched("https://cellar.test", 1972, ("it",))  # its fingerprint asked now, and holding
    dates, cover = cellar_listing.coverage({(CELEX, "it")}, {"it": 50.0, "fr": 50.0})
    assert dates == {(CELEX, "it"): 100} and (1972, "it") in cover


def test_prepare_lists_the_missing_windows_first_and_an_unlisted_one_leaves_its_units_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    monkeypatch.setattr(work_trees, "_BULK", {})
    base, corpus = tmp_path / "datadump", tmp_path / "corpus"
    _statement(base / "datadump-it", "it")
    _zip(base / "datadump-it" / "LEG_MTD.zip", {MEMBER: (FX / MTD).read_bytes()})
    eurlex_dump._stamp(corpus, {"it": CREATED})
    listed = []

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        listed.append((year, langs))
        if year == 1973:
            raise cellar.TransportError("cellar: 503 to the count of 1973")
        cellar_listing.keep(url, year, langs, 1, None, [[CELEX, "it", 100, None]], force=True)
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)
    units = cellar.TreeUnits()
    due = [RemoteFile(rel_path=cellar.rel_path("it", c), mtime=None, remote=cellar.encode(c, "it"))
           for c in (CELEX, "31973R0002")]
    units._prepare_bulk(base, corpus, ["it"], due, "eu", "https://cellar.test")
    assert listed == [(1972, ("it",)), (1973, ("it",))]
    unit = {"celex": CELEX, "lang": "it"}
    assert units._bulk_of(corpus, unit, due[0], corpus / due[0].rel_path)[1] == 100
    with pytest.raises(cellar.WindowNotListed):
        units._bulk_of(corpus, {"celex": "31973R0002", "lang": "it"}, due[1], corpus / due[1].rel_path)


def test_a_tree_cellar_served_this_pass_newer_than_the_bulk_s_wins_without_a_request(tmp_path, monkeypatch):
    old, new = _trees()
    asked = _served(monkeypatch, new)
    bulk, _ = _bulk(tmp_path, {MEMBER: old})
    recent = work_trees.RecentTrees(tmp_path / "recent")
    recent.get(CELEX, lambda: new)
    got = cellar.fetch_unit_from_tree("https://cellar.test", CELEX, "it", tmp_path / "u", client=object(),
                                      trees=recent, bulk=bulk)
    assert got.tree_from == "kept" and not any("non-inferred tree" in w for w in asked)
    held = from_tree.notice(rdf.loads((tmp_path / "u" / rdf.TREE_FILE).read_bytes()), "ITA", CELEX)
    assert held.modified.startswith("2026-11-19")


def test_where_each_tree_came_from_is_written_beside_the_corpus(tmp_path):
    work_trees.record_source(tmp_path, "it", CELEX, "dump", CREATED)
    work_trees.record_source(tmp_path, "it", "31972R0001", "cellar", CREATED)
    assert work_trees.source_of(tmp_path, "it", CELEX)["dump"] == CREATED
    assert "dump" not in work_trees.source_of(tmp_path, "it", "31972R0001")


def test_prepare_readies_the_bulk_before_the_pass_and_an_mtd_that_does_not_open_holds_it_back(tmp_path, monkeypatch):
    from app.acquire.spec import HeldBack
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    monkeypatch.setattr(work_trees, "_BULK", {})
    base, corpus = tmp_path / "datadump", tmp_path / "corpus"
    _statement(base / "datadump-it", "it")
    _zip(base / "datadump-it" / "LEG_MTD.zip", {MEMBER: (FX / MTD).read_bytes()})
    eurlex_dump._stamp(corpus, {"it": CREATED})
    units = cellar.TreeUnits()
    due = [RemoteFile(rel_path=cellar.rel_path("it", CELEX), mtime=None, remote=cellar.encode(CELEX, "it"))]
    monkeypatch.setattr(cellar, "_window_files", lambda *a, **k: iter(()))
    units._prepare_bulk(base, corpus, ["it"], due, "eu", "https://cellar.test")
    assert units._bulk[str(corpus.resolve())]["it"](CELEX) is not None
    (base / "datadump-it" / "LEG_MTD.zip").write_bytes(b"not a zip")
    monkeypatch.setattr(work_trees, "_BULK", {})
    with pytest.raises(HeldBack):
        units._prepare_bulk(base, corpus, ["it"], due, "eu", "https://cellar.test")
