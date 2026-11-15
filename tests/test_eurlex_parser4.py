"""EUR-Lex parser 4 (2026-11-15): the record's facts from the unit's TREE,
never from a notice — so a unit built from the Data Dump alone (its MTD tree
scoped to the language, and the items) is a record (operator, B: "Build every
in-force act, in all 24 languages, from the Data Dump alone"). The units are
München's, byte-exact from the node (tests/fixtures/eurlex/eurlex-dumpfirst):
a Formex act (31972R1473), an act in HTML alone (31978A0060) and a
consolidation (01979D0491-20061025), each with the notice Cellar served and
its work's tree from the dump's LEG_MTD.zip of 2026-11-08."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app import plugin_io
from app.pack import slicing
from app.plugins.base import DiscoverState, UnindexableDocument
from app.plugins.eurlex import SOURCE, from_tree, notice, rdf
from app.plugins.eurlex import source as src

FIX = Path(__file__).parent / "fixtures" / "eurlex" / "eurlex-dumpfirst"
MTD = "LEG_MTD.tree_non_inferred.rdf"
V3 = SOURCE.at_parser_version(3)
UNITS = {"31972R1473": FIX / "formex" / "31972R1473", "31978A0060": FIX / "html" / "31978A0060",
         "01979D0491-20061025": FIX / "consolidation" / "01979D0491-20061025"}


def _lay(root: Path, celex: str, *, notice_too: bool = False, history: bool = False,
         tree_from_mtd: bool = False) -> Path:
    """The unit as the dump builder writes it: the items and `tree.json`;
    `tree_from_mtd`: the tree the dump's MTD gives, scoped (what the builder
    writes); `notice_too`: a unit Cellar's per-unit fetch landed (parser 3)."""
    fx = UNITS[celex]
    dest = root / (src.HISTORY if history else "") / src.unit_rel("it", celex)
    dest.mkdir(parents=True)
    for f in fx.iterdir():
        if f.name in (MTD, src.NOTICE, rdf.TREE_FILE):
            continue
        shutil.copy(f, dest / f.name)
    if tree_from_mtd:
        (dest / rdf.TREE_FILE).write_bytes(rdf.dumps(rdf.scope(rdf.read((fx / MTD).read_bytes()), "ITA")))
    else:
        shutil.copy(fx / rdf.TREE_FILE, dest / rdf.TREE_FILE)
    if notice_too:
        shutil.copy(fx / src.NOTICE, dest / src.NOTICE)
    return dest


def _docs(plugin, root: Path) -> dict:
    out = {}
    for ref in plugin.discover(DiscoverState(corpus_root=root)):
        assert ref.error is None, ref.error
        out[Path(ref.locator).name] = (ref, plugin_io.canonical_doc(
            plugin, {"native_id": ref.native_id, "path": ref.locator}))
    return out


def test_the_registered_parser_is_4_and_carries_3():
    assert SOURCE.PARSER_VERSION == 4 and SOURCE.at_parser_version(4) is SOURCE
    assert isinstance(V3, src.EurlexSourceV3) and V3.PARSER_VERSION == 3
    assert SOURCE.at_parser_version(2) is None


@pytest.mark.parametrize("celex", ["31972R1473", "31978A0060"])
def test_the_dumps_tree_is_the_tree_cellar_serves(celex):
    """The held tree (Cellar, `notice=non-inferred-tree`, scoped) and the
    dump's MTD tree, scoped, are one canonical JSON."""
    fx = UNITS[celex]
    assert rdf.dumps(rdf.scope(rdf.read((fx / MTD).read_bytes()), "ITA")) == (fx / rdf.TREE_FILE).read_bytes()


@pytest.mark.parametrize("celex", list(UNITS))
def test_every_fact_the_notice_gave_comes_from_the_tree(celex):
    fx = UNITS[celex]
    held = notice.parse((fx / src.NOTICE).read_bytes())
    got = from_tree.notice(rdf.loads((fx / rdf.TREE_FILE).read_bytes()), "ITA", celex)
    for f in ("celex", "eli", "sector", "resource_type", "year", "number", "date_document",
              "entry_into_force", "end_of_validity", "in_force", "base_celex", "display_language",
              "expression_title", "last_modified", "date_publication"):
        assert getattr(got, f) == getattr(held, f), f
    assert {f: sorted(i.name for i in v) for f, v in got.items.items()} == \
        {f: sorted(i.name for i in v) for f, v in held.items.items() if v or f in got.items}
    # the relations the notice adds are other works' (inverse links) and its
    # `BASED_ON` alias of the based-on links the tree states
    extra = set(held.relations) - set(got.relations)
    assert all(t.endswith("_BY_ACT_CONSOLIDATED") or t.endswith("_BY_RESOURCE_LEGAL") or t == "BASED_ON"
               for t, _c in extra), extra
    assert set(got.relations) <= set(held.relations)


def test_a_unit_of_the_dump_alone_is_the_record_parser_3_made(tmp_path):
    """The same identity, dates, validity, text and keys as parser 3 read
    from the notice; subjects and authors are concept ids, the notice is
    not held."""
    four = _docs(SOURCE, _seed(tmp_path / "four", notice_too=False, tree_from_mtd=True))
    three = _docs(V3, _seed(tmp_path / "three", notice_too=True))
    assert set(four) == set(three) == {"31972R1473", "31978A0060"}
    for celex in four:
        (r4, d4), (r3, d3) = four[celex], three[celex]
        assert r4.native_id == r3.native_id, "the same day: the id does not move"
        assert (d4.title, d4.date, d4.language, d4.keys, d4.sections) == \
            (d3.title, d3.date, d3.language, d3.keys, d3.sections)
        for k in ("celex", "valid_from", "validity_work", "in_force", "entry_into_force",
                  "end_of_validity", "eli_expression", "eli_work", "form", "items", "cdm_rdf"):
            assert d4.extra.get(k) == d3.extra.get(k), k
        assert "cdm_notice" not in d4.extra and "notice_kind" not in d4.extra
        assert all(":" in s and not s.startswith("http") for s in d4.subjects), d4.subjects
        assert d4.creators and all(c.startswith("corporate-body:") for c in d4.creators)
        assert {(e.type, e.to) for e in SOURCE.relations(d4)} == {(e.type, e.to) for e in V3.relations(d3)}
    assert "eurovoc:1073" in four["31972R1473"][1].subjects
    assert four["31978A0060"][1].extra["form"] == "html"


def _seed(root: Path, **kw) -> Path:
    for celex in ("31972R1473", "31978A0060"):
        _lay(root, celex, **kw)
    return root


def test_a_unit_that_still_holds_its_notice_keeps_it_whole_and_reads_the_tree(tmp_path):
    root = tmp_path / "c"
    _lay(root, "31972R1473", notice_too=True)
    [(_ref, doc)] = _docs(SOURCE, root).values()
    assert doc.extra["notice_kind"] == "branch" and doc.extra["cdm_notice"]
    assert "eurovoc:1073" in doc.subjects, "the facts from the tree, the notice read for nothing"


def test_the_history_slice_is_the_units_place(tmp_path):
    """A consolidation, which the dump never holds, under `history/`: its
    slice opens with `history/`, the hint from the path agrees, and `lang=it`
    alone does not hold it."""
    from app import slice_scope
    root = tmp_path / "c"
    _lay(root, "31972R1473")
    _lay(root, "01979D0491-20061025", history=True)
    docs = _docs(SOURCE, root)
    for celex, want in (("31972R1473", "lang=it/type=R/year=1972"),
                        ("01979D0491-20061025", "history/lang=it/type=D/year=2006")):
        ref, doc = docs[celex]
        got = slicing.eurlex_slice(doc, int(ref.native_id))
        assert got == want
        assert slicing._eurlex_hint({"path": ref.locator, "native_id": ref.native_id}) == got
    cons = docs["01979D0491-20061025"][1]
    assert cons.extra["history"] is True and "history" not in docs["31972R1473"][1].extra
    assert cons.extra["work_celex"] == "31979D0491", "the base act from the tree"
    assert not slice_scope.is_held("history/lang=it/type=D/year=2006", ("lang=it",))
    assert slice_scope.is_held("history/lang=it/type=D/year=2006", ("lang=it", "history/lang=it"))
    assert slice_scope.is_held("history/lang=it/type=D/year=2006", ("history",))


def test_parser_3_sees_no_history_and_no_unit_without_its_notice(tmp_path):
    root = tmp_path / "c"
    _lay(root, "31972R1473")
    _lay(root, "01979D0491-20061025", history=True, notice_too=True)
    assert list(V3.discover(DiscoverState(corpus_root=root))) == []


def test_a_tree_of_another_act_is_refused(tmp_path):
    root = tmp_path / "c"
    dest = _lay(root, "31978A0060")
    shutil.copy(UNITS["31972R1473"] / rdf.TREE_FILE, dest / rdf.TREE_FILE)
    ref = next(iter(SOURCE.discover(DiscoverState(corpus_root=root))))
    with pytest.raises(UnindexableDocument, match="no work of CELEX 31978A0060"):
        SOURCE.parse(SOURCE.fetch(ref))


def test_concept_ids():
    assert from_tree.concept_id("http://eurovoc.europa.eu/1073") == "eurovoc:1073"
    assert from_tree.concept_id(
        "http://publications.europa.eu/resource/authority/subject-matter/TRAN") == "subject-matter:TRAN"
    assert from_tree.celex_of("http://publications.europa.eu/resource/celex/31968R0259%2801%29") == "31968R0259(01)"
    assert from_tree.celex_of("http://publications.europa.eu/resource/celex/31968R0259.ITA") is None
