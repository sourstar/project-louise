"""A FORMEX MANIFESTATION SERVED AS ONE ZIP (2026-11-19). Cellar serves the
OJ's act-by-act publication — 32024R1689's `OJABA_L_202401689_ENG.fmx4.zip`,
and 32015D1735 repackaged today as `32015D1735.eng.fmx4.zip` — as one item,
a zip of the wrapper, the body and the figures. `cellar.form_of` saw no
wrapper among the item names and read those acts from XHTML. Now the
package is Formex: held as served (invariant 11), read through
(`formex.unpack`) by the fetch checks, the dump build, the unit's day and
the parser; a held unit lacking the package its tree lists is re-formed,
from the dump where it holds it, by Cellar after that where it does not."""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from app import plugin_io
from app.acquire import cellar, eurlex_dump
from app.plugins.base import DiscoverState
from app.plugins.eurlex import SOURCE, formex, from_tree, rdf
from app.plugins.eurlex import source as src
from tests.acquire.test_eurlex_dump import FORMEX, MTD, _items, serial  # noqa: F401

CELEX, UUID, FX = FORMEX[0], FORMEX[1], FORMEX[2]
PKG = "31972R1473.ita.fmx4.zip"
XHTML = "L_1972160IT.01000101.doc.html"


def _package(files: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in (files if files is not None else _items(FX)).items():
            z.writestr(n, b)
    return buf.getvalue()


def _packaged_mtd() -> bytes:
    """The fixture's tree with its Italian Formex manifestation as Cellar
    serves a packaged one: ONE item, the zip."""
    data = (FX / MTD).read_bytes().decode()
    for doc in ("DOC_2", "DOC_3"):
        data, n = re.subn(rf'  <rdf:Description rdf:about="[^"]*\.0005\.04/{doc}">.*?</rdf:Description>\n',
                          "", data, flags=re.S)
        assert n == 1
    data, n = re.subn(r"(\.0005\.04/DOC_1\">.*?)L_1972160IT\.toc\.xml", lambda m: m[1] + PKG, data, flags=re.S)
    assert n == 1
    data = re.sub(r"(ITA\.fmx4\.)L_1972160IT\.toc\.xml", lambda m: m[1] + PKG, data)
    return data.encode()


def _scoped(mtd: bytes) -> bytes:
    return rdf.dumps(rdf.scope(rdf.read(mtd), "ITA"))


def _note(mtd: bytes):
    return from_tree.notice(rdf.scope(rdf.read(mtd), "ITA"), "ITA", CELEX)


# ---- reading a package ---------------------------------------------------------------------

def test_a_package_is_read_through_and_stays_held():
    held = {PKG: _package(), "tree.json": b"{}"}
    view, came = formex.unpack(held)
    assert set(_items(FX)) <= set(view) and PKG not in view and view["tree.json"] == b"{}"
    assert came == {n: PKG for n in _items(FX)}
    assert held[PKG][:4] == b"PK\x03\x04", "the zip is not changed"
    assert formex.parse(view, CELEX).title == formex.parse(_items(FX), CELEX).title


@pytest.mark.parametrize("bad, why", [
    (lambda: _package({"sub/L.xml": b"<A/>"}), "not a plain file name"),
    (lambda: b"PK\x03\x04 broken", "not a readable zip"),
])
def test_a_package_that_does_not_read_as_one_plain_folder_is_refused(bad, why):
    with pytest.raises(formex.FormexError, match=why):
        formex.unpack({PKG: bad()})


def test_a_member_of_other_bytes_than_the_item_of_its_name_is_refused():
    with pytest.raises(formex.FormexError, match="differs from the item"):
        formex.unpack({PKG: _package({"L.xml": b"<A/>"}), "L.xml": b"<B/>"})
    same = formex.unpack({PKG: _package({"L.xml": b"<A/>"}), "L.xml": b"<A/>"})[0]
    assert same["L.xml"] == b"<A/>"


def test_a_file_named_zip_that_is_not_one_is_an_item_like_any_other():
    view, came = formex.unpack({"x.zip": b"not a zip"})
    assert view == {"x.zip": b"not a zip"} and came == {}


# ---- the form ------------------------------------------------------------------------------

def test_a_packaged_formex_is_the_form_read_not_the_xhtml():
    note = _note(_packaged_mtd())
    assert [i.name for i in note.items["fmx4"]] == [PKG] and note.items.get("xhtml")
    assert cellar.form_of(note) == "fmx4"


def test_a_held_unit_lacks_its_package_until_it_holds_it(tmp_path):
    note = _note(_packaged_mtd())
    assert cellar.lacks_package(tmp_path, note)
    (tmp_path / PKG).write_bytes(_package())
    assert not cellar.lacks_package(tmp_path, note)
    assert not cellar.lacks_package(tmp_path, _note((FX / MTD).read_bytes())), "unpacked Formex: nothing lacks"


# ---- the parse -----------------------------------------------------------------------------

def _unit(root: Path, files: dict[str, bytes]) -> Path:
    unit = root / src.unit_rel("it", CELEX)
    unit.mkdir(parents=True)
    for n, b in files.items():
        (unit / n).write_bytes(b)
    return unit


def _doc(root: Path):
    (ref,) = SOURCE.discover(DiscoverState(corpus_root=root))
    assert ref.error is None, ref.error
    return ref, plugin_io.canonical_doc(SOURCE, {"native_id": ref.native_id, "path": ref.locator})


def test_a_packaged_unit_is_the_same_act_its_id_and_day_unchanged(tmp_path):
    _unit(tmp_path / "plain", {**_items(FX), rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    _unit(tmp_path / "packed", {PKG: _package(), rdf.TREE_FILE: _scoped(_packaged_mtd())})
    pref, plain = _doc(tmp_path / "plain")
    kref, packed = _doc(tmp_path / "packed")
    assert kref.native_id == pref.native_id, "the unit's day from the wrapper inside"
    assert packed.sections == plain.sections and packed.title == plain.title
    assert packed.extra["body_root"] == "ACT" and packed.extra["eli_date_publication"] == "1972-07-16"
    assert "formex-not-this-act" not in [i.code for i in packed.issues]
    items = {i["name"]: i for i in packed.extra["items"]}
    assert items[PKG]["bytes"] == len(_package()) and "in" not in items[PKG]
    assert {n: items[n]["in"] for n in _items(FX)} == {n: PKG for n in _items(FX)}
    assert not any("in" in i for i in plain.extra["items"]), "an unpacked unit's items as before"


# ---- re-forming the held units, bulk first -------------------------------------------------

def _packaged_dump(base: Path, *, with_package: bool) -> Path:
    import json
    folder = base / "datadump-it"
    folder.mkdir(parents=True)
    (folder / "datadump.json").write_text(json.dumps({"language": "it", "created": "2026-11-15T01:00:00+01:00"}))
    with zipfile.ZipFile(folder / "LEG_IT_FMX.zip", "w") as z:
        z.writestr(f"{UUID}/fmx4/{PKG if with_package else 'other.zip'}", _package())
    with zipfile.ZipFile(folder / "LEG_IT_XHTML.zip", "w") as z:
        z.writestr(f"{UUID}/xhtml/{XHTML}", b"<html><body><p>Articolo 1</p></body></html>")
    with zipfile.ZipFile(folder / "LEG_MTD.zip", "w") as z:
        z.writestr(f"{UUID}/tree_non_inferred.rdf", _packaged_mtd())
    return base


def _held_as_xhtml(corpus: Path) -> Path:
    return _unit(corpus, {XHTML: b"<html><body><p>Articolo 1</p></body></html>",
                          rdf.TREE_FILE: _scoped(_packaged_mtd())})


def test_the_dump_build_lands_a_package_and_re_forms_a_unit_held_as_xhtml(tmp_path, serial):
    corpus = tmp_path / "corpus"
    unit = _held_as_xhtml(corpus)
    got = eurlex_dump.build(_packaged_dump(tmp_path / "dump", with_package=True), corpus, ["it"],
                            log=lambda m: None)["it"]
    assert got["reformed"] == 1 and not got["held"], got
    assert sorted(p.name for p in unit.iterdir()) == sorted([PKG, rdf.TREE_FILE]), "the XHTML left with it"
    assert (unit / PKG).read_bytes() == _package()
    assert eurlex_dump.packages_read(corpus) == {"it"}
    again = eurlex_dump.build(_packaged_dump(tmp_path / "dump2", with_package=True), corpus, ["it"],
                              log=lambda m: None)["it"]
    assert again["held"] == 1 and not again["reformed"], "re-formed once"


def test_a_package_the_dump_lacks_is_the_api_s_first_until_the_unit_holds_it(tmp_path, serial):
    corpus = tmp_path / "corpus"
    unit = _held_as_xhtml(corpus)
    got = eurlex_dump.build(_packaged_dump(tmp_path / "dump", with_package=False), corpus, ["it"],
                            log=lambda m: None)["it"]
    assert got["package-not-in-dump"] == 1 and got["incomplete"] == 1 and not got["reformed"], got
    assert (unit / XHTML).is_file(), "nothing taken away before the origin gives the package"
    assert eurlex_dump.incomplete_due(corpus, {"it"}) == [("it", CELEX, "true" if _note(_packaged_mtd()).in_force
                                                           else ("false" if _note(_packaged_mtd()).in_force is False
                                                                 else None))]
    (unit / PKG).write_bytes(_package())
    assert eurlex_dump.incomplete_due(corpus, {"it"}) == [], "held whole: no longer due"


def test_the_per_unit_pass_asks_again_for_a_lacking_package_only_after_the_bulk(tmp_path):
    from types import SimpleNamespace
    corpus = tmp_path / "corpus"
    unit = _held_as_xhtml(corpus)
    rel = Path(src.unit_rel("it", CELEX)) / rdf.TREE_FILE
    remote = SimpleNamespace(rel_path=str(rel), remote=cellar.encode(CELEX, "it"), mtime=0)
    units = cellar.TreeUnits.__new__(cellar.TreeUnits)
    note = _note(_packaged_mtd())
    assert not units._reform(unit / rdf.TREE_FILE, remote, note), "the dump build re-forms first"
    eurlex_dump._mark_packages(corpus, ["it"])
    assert units._reform(unit / rdf.TREE_FILE, remote, note)
    (unit / PKG).write_bytes(_package())
    assert not units._reform(unit / rdf.TREE_FILE, remote, note)


def test_the_units_asked_first_come_by_act_then_language(tmp_path):
    """One work's tree serves its every language (work_trees.RecentTrees):
    the incomplete units are asked act by act, not language by language."""
    import json
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    rec = {lang: {c: {"in_force": "true", "tried": None} for c in ("32001R0001", "32002R0002")}
           for lang in ("de", "fr", "it")}
    (corpus / eurlex_dump.INCOMPLETE).write_text(json.dumps(rec))
    got = eurlex_dump.incomplete_due(corpus, {"de", "fr", "it"})
    assert [(c, l) for l, c, _ in got] == [("32001R0001", "de"), ("32001R0001", "fr"), ("32001R0001", "it"),
                                           ("32002R0002", "de"), ("32002R0002", "fr"), ("32002R0002", "it")]


def test_a_dump_unit_whose_files_are_not_their_names_is_the_api_s_never_landed(tmp_path, serial):
    """The Data Dump's 9 acts of 2014–15 (32015D1735: a TIFF as the body's
    `.xml`): never landed from the dump; a held one recorded for Cellar
    first, asked once (`fetched`), then due no more."""
    import json
    folder = tmp_path / "dump" / "datadump-it"
    folder.mkdir(parents=True)
    (folder / "datadump.json").write_text(json.dumps({"language": "it", "created": "2026-11-15T01:00:00+01:00"}))
    body = "L_1972160IT.01000101.xml"
    with zipfile.ZipFile(folder / "LEG_IT_FMX.zip", "w") as z:
        for n, b in _items(FX).items():
            z.writestr(f"{UUID}/fmx4/{n}", b"II*\x00" + b"\x00" * 64 if n == body else b)
    with zipfile.ZipFile(folder / "LEG_MTD.zip", "w") as z:
        z.writestr(f"{UUID}/tree_non_inferred.rdf", (FX / MTD).read_bytes())
    corpus = tmp_path / "corpus"
    got = eurlex_dump.build(tmp_path / "dump", corpus, ["it"], log=lambda m: None)["it"]
    assert got["dump-not-its-name"] == 1 and not got["built"], got
    assert not (corpus / src.unit_rel("it", CELEX)).exists(), "never landed from the dump"
    assert [c for _l, c, _f in eurlex_dump.incomplete_due(corpus, {"it"})] == [CELEX]
    # held as the dump gave it (an earlier build): recorded for the API, due until Cellar was asked once
    unit = _unit(corpus, {**_items(FX), body: b"II*\x00" + b"\x00" * 64, rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    eurlex_dump._LANGS.clear()
    again = eurlex_dump.build(tmp_path / "dump", corpus, ["it"], log=lambda m: None)["it"]
    assert again["dump-not-its-name"] == 1 and not again["held"], again
    assert eurlex_dump.held_not_its_name(unit, CELEX)
    assert [c for _l, c, _f in eurlex_dump.incomplete_due(corpus, {"it"})] == [CELEX]
    eurlex_dump.mark_fetched(corpus, "it", CELEX)
    assert eurlex_dump.incomplete_due(corpus, {"it"}) == [], "asked once: not every pass"


def _shifted_files() -> dict[str, bytes]:
    """32013D0410's dump files (24 languages, 2026-11-21): the wrapper's name
    holds an ANNEX, the body's name holds the wrapper, the ACT sits under an
    annex's name. Cellar serves them right."""
    items = _items(FX)
    wname, body = "L_1972160IT.01000101.doc.xml", "L_1972160IT.01000101.xml"
    annex = (b'<?xml version="1.0"?><ANNEX><BIB.INSTANCE><PAGE.FIRST>54</PAGE.FIRST><PAGE.SEQ>1</PAGE.SEQ>'
             b'</BIB.INSTANCE><TITLE><TI><P>PRILOGA VII</P></TI></TITLE></ANNEX>')
    return {wname: annex, body: items[wname], "L_1972160IT.01000501.xml": items[body],
            "L_1972160IT.toc.xml": items["L_1972160IT.toc.xml"]}


def test_a_unit_whose_wrapper_is_under_another_name_is_refused_listed_and_the_api_s(tmp_path, serial):
    from app.plugins.base import UnindexableDocument
    files = _shifted_files()
    with pytest.raises(formex.FormexError, match=r"01000101.doc.xml holds <ANNEX>, not a wrapper.*"
                                                 r"01000101.xml holds a wrapper"):
        formex.parse(files, CELEX)
    corpus = tmp_path / "corpus"
    unit = _unit(corpus, {**files, rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    assert any("not a wrapper" in w for w in eurlex_dump.held_not_its_name(unit, CELEX)), "the build's API record"
    # LISTED, its day the work's own (it was "container not enumerable" on every scan)
    (ref,) = SOURCE.discover(DiscoverState(corpus_root=corpus))
    assert ref.error is None
    plain = tmp_path / "plain"
    _unit(plain, {**_items(FX), rdf.TREE_FILE: _scoped((FX / MTD).read_bytes())})
    (pref,) = SOURCE.discover(DiscoverState(corpus_root=plain))
    assert ref.native_id == pref.native_id, "the work's day is the wrapper's OJ day: the same id"
    with pytest.raises(UnindexableDocument, match="not a wrapper"):
        plugin_io.canonical_doc(SOURCE, {"native_id": ref.native_id, "path": ref.locator})
