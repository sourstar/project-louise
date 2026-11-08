"""LEGI on the ELI layer, one record per origin file (app/plugins/legi;
docs/notes/legal-fr-de-eu.md §2; legal-collections-nemotron.md): the four
kinds parsed from real files with every field kept, the validity axis on
articles only, the quirks, the id and its fallback day, the tarballs'
packs and the order discovery applies them in, the deletion lists, the
withdrawals, and the end to end through the rdf and fts stages."""
from __future__ import annotations

import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app import chunking, collections as collections_mod, db, discovery, fts, plugin_io, retrieval, validity, worker
from app.pack import slicing
from app.plugins import eli
from app.plugins.base import DiscoverState, DocRef, Mutability
from app.plugins.legi import SOURCE, pack
from app.plugins.legi import source as lsrc
from app.plugins.legi import xml as lx
from tests.legi_fixtures import (ART_ABROGE, ART_MODIFIE, ART_MORT_NE, ART_PLACEHOLDER, CASF, CMMI, FIX,
                                 REAL, SECTION, STRUCT, VERSION, article, member, tarball, texte_version)

BASE = "Freemium_legi_global_20250713-140000.tar.gz"


@pytest.fixture(autouse=True)
def _fresh_caches():
    pack.reset_caches()
    yield
    pack.reset_caches()


def _refs(root: Path, state_dir: Path | None = None) -> list[DocRef]:
    return list(SOURCE.discover(DiscoverState(corpus_root=root, state_dir=state_dir)))


def _docs(root: Path) -> dict[str, object]:
    """The record of each file, by its member's file name."""
    out = {}
    for r in _refs(root):
        if r.error is None and r.scope == "document":
            out[r.locator.rsplit("/", 1)[-1]] = SOURCE.parse(SOURCE.fetch(r))
    return out


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "legi"
    tarball(root / BASE, REAL)
    return root


# ---- the four kinds, every field kept -------------------------------------------------------

def _origin_values(data: bytes) -> set[str]:
    """Every attribute value and leaf text the file states, outside the
    CONTENU blocks (whose text the record holds rendered)."""
    root = ET.fromstring(data)
    out: set[str] = set()

    def walk(e):
        if e.tag == "CONTENU":
            return
        out.update(v.strip() for v in e.attrib.values() if v.strip())
        if len(e) == 0 and (e.text or "").strip():
            out.add(" ".join(e.text.split()))
        for c in e:
            walk(c)
    walk(root)
    return out


@pytest.mark.parametrize("name,data", [
    ("LEGIARTI000006398122.xml", ART_ABROGE), ("LEGIARTI000019325262.xml", ART_MODIFIE),
    ("LEGISCTA000006135575.xml", SECTION), ("LEGITEXT000006070666.xml", STRUCT),
])
def test_every_value_the_origin_states_is_in_the_record(corpus, name, data):
    """Invariant 11: nothing the file asserts is dropped — every attribute and
    every leaf element's text is in the record (its extra, title or sections)."""
    docs = {k: v for k, v in _docs(corpus).items()}
    if name == "LEGITEXT000006070666.xml":
        doc = next(d for d in _all(corpus) if "meta_texte_chronicle" in d.extra and "struct" in d.extra)
    else:
        doc = docs[name]
    held = json.dumps(doc.extra, ensure_ascii=False) + doc.title + json.dumps(doc.sections, ensure_ascii=False)
    missing = sorted(v for v in _origin_values(data) if v not in held)
    assert missing == []


def _all(root):
    return [SOURCE.parse(SOURCE.fetch(r)) for r in _refs(root) if r.error is None and r.scope == "document"]


def test_the_text_version_keeps_its_parts_as_sections_and_its_metadata(corpus):
    doc = next(d for d in _all(corpus) if "meta_texte_version" in d.extra)
    assert doc.title == "Code des instruments monétaires et des médailles"
    held = json.dumps(doc.extra, ensure_ascii=False) + doc.title
    assert [v for v in _origin_values(VERSION) if v not in held] == []
    assert doc.extra["meta_texte_version"]["ETAT"] == "VIGUEUR" and doc.extra["in_force"] is True
    assert doc.sections == [], "the real file's VISAS … RECT are empty"
    tv = texte_version("LEGITEXT000000000001", "Décret de test", visas="Le Premier ministre, Vu …")
    got = SOURCE.parse(_raw(tv, member("texte_version", "LEGITEXT000000000001")))
    assert got.sections == [("VISAS", "Le Premier ministre, Vu …")]


def _raw(data: bytes, name: str, tar: str = "/x/" + BASE):
    from app.plugins.base import RawDoc
    return RawDoc(ref=DocRef(source_id="legi", native_id="1", locator=f"{tar}::{name}"),
                  raw_hash="h", data=data)


def test_each_kind_its_title_keys_and_sections(corpus):
    docs = _docs(corpus)
    a = docs["LEGIARTI000006398122.xml"]
    assert a.title == "Code des instruments monétaires et des médailles — Article 1"
    assert a.keys == ["legi:LEGIARTI000006398122", f"legi:{CMMI}"]
    assert a.sections == [("Article 1", "La fabrication des monnaies est exécutée par voie de régie "
                                        "administrative, sous l'autorité du ministre de l'économie et des finances.")]
    assert a.language == "fr" and a.extra["meta_article"]["ETAT"] == "ABROGE"
    s = docs["LEGISCTA000006135575.xml"]
    assert s.sections == [("TITRE_TA", "Paragraphe II : Frappe des médailles.")]
    assert [x["id"] for x in s.extra["structure_ta"]][:2] == ["LEGIARTI000006398192", "LEGIARTI000006398191"]
    struct = next(d for d in _all(corpus) if "struct" in d.extra)
    # parser 2: the structure as bodiless sections — no article chunk, the outline's labels
    assert struct.sections == [("Chapitre I : Monnaies et médailles", ""), ("Chapitre II : Billets de banque (abrogé)", ""),
                               ("Chapitre III : Imitation de billets de banque et de monnaies. (abrogé)", ""),
                               ("Chapitre IV : Dispositions communes.", "")]
    assert struct.keys == [f"legi:{CMMI}"]
    from app import chunking_legal
    [outline] = chunking_legal.chunk_legal_article(struct.uid, 1, struct.sections, params=chunking_legal.SHARED_LAYER[
        "chunker"]["params"], title=struct.title)
    assert outline["chapter"] == chunking_legal.OUTLINE and "Chapitre IV : Dispositions communes." in outline["text"]
    assert chunking.chunker_name(SOURCE.chunking_profile, SOURCE.mutability) == "legal_article"
    assert SOURCE.chunking_profile == "legal_article" and SOURCE.mutability is Mutability.MUTABLE


def test_an_element_the_dtd_does_not_name_is_kept_as_other():
    data = ART_ABROGE.replace(b"</ARTICLE>", b'<NOUVEAU attr="v">texte neuf</NOUVEAU></ARTICLE>')
    doc = SOURCE.parse(_raw(data, member("article", "LEGIARTI000006398122")))
    assert doc.extra["other"] == [{"tag": "NOUVEAU", "attrs": {"attr": "v"}, "text": "texte neuf"}]


def test_every_root_child_the_dtds_declare_is_read():
    """The DTDs (Legilibre/dtd-legi @ 2c64cbf): each root's children are
    parts the parser names — none falls to `other` on a conforming file."""
    dtd = FIX / "dtd"
    declared = {}
    for f, root in (("legi_article.dtd", "ARTICLE"), ("legi_section_ta.dtd", "SECTION_TA"),
                    ("legi_texte_struct.dtd", "TEXTELR"), ("legi_texte_version.dtd", "TEXTE_VERSION")):
        m = re.search(rf"<!ELEMENT {root} \(([^)]*)\)", (dtd / f).read_text(encoding="utf-8"))
        declared[root] = {p.strip(" ?*+") for p in m.group(1).split(",")}
    for root, parts in declared.items():
        body = "".join(f"<{p}/>" for p in parts)
        doc = SOURCE.parse(_raw(f"<{root}>{body}</{root}>".encode(), "a/b/LEGIARTI000000000001.xml"))
        assert "other" not in doc.extra, (root, doc.extra.get("other"))


# ---- validity: articles only ----------------------------------------------------------------

def test_only_an_article_is_a_version_of_a_work(corpus):
    v = validity.from_plugin(SOURCE)
    assert v == validity.Validity(from_key="extra.version_from", work_key="extra.work_key")
    for d in _all(corpus):
        got = validity.of_doc(d, v, SOURCE)
        if "meta_article" in d.extra:
            assert got is not None and got["work"].startswith("LEGIARTI")
        else:
            assert got is None, f"{d.title}: a non-article record is never a version"
            assert "version_from" not in d.extra and "work_key" not in d.extra


def test_the_work_key_is_the_chain_s_smallest_id_and_the_start_is_date_debut(corpus):
    a = _docs(corpus)["LEGIARTI000019325262.xml"]
    assert a.extra["version_from"] == "2004-10-26"
    assert a.extra["work_key"] == "LEGIARTI000019325262", "smaller than LEGIARTI000027037614"
    assert lsrc.next_start(a.extra) == "2007-11-09"
    assert "in_force" not in a.extra, "MODIFIE: the article is in force by another version; absent, never guessed"
    assert [i.code for i in a.issues] == [], "DATE_FIN 2007-11-09 is the next version's start"


def test_a_repeal_closes_its_article_at_its_date_fin(corpus):
    a = _docs(corpus)["LEGIARTI000006398122.xml"]
    assert a.extra["in_force"] is False
    assert list(SOURCE.closures(a)) == [("legi:LEGIARTI000006398122", "2001-01-01")]
    m = _docs(corpus)["LEGIARTI000019325262.xml"]
    assert list(SOURCE.closures(m)) == [], "a MODIFIE version is ended by the next one"


def test_modifie_mort_ne_is_held_and_never_a_version_nor_searchable():
    doc = SOURCE.parse(_raw(ART_MORT_NE, member("article", "LEGIARTI000019325262", CASF)))
    assert doc.sections == [] and "version_from" not in doc.extra and "work_key" not in doc.extra
    assert doc.extra["mort_ne_text"].startswith("GUIDE-BARÈME")
    assert doc.extra["in_force"] is False
    assert "modifie-mort-ne" in {i.code for i in doc.issues}
    assert validity.of_doc(doc, validity.from_plugin(SOURCE), SOURCE) is None
    from app import chunking_legal
    assert list(chunking_legal.chunk_legal_article(doc.uid, 1, doc.sections, params=chunking_legal.SHARED_LAYER[
        "chunker"]["params"], title=doc.title)) == [], "never searchable"
    assert list(SOURCE.closures(doc)) == []


def test_a_sibling_mort_ne_never_ends_a_version():
    """Its siblings' derived end skips it: it never entered into force."""
    a = article("LEGIARTI000000000010", debut="2000-01-01", fin="2010-01-01", etat="MODIFIE",
                versions=[("LEGIARTI000000000010", "MODIFIE", "2000-01-01", "2010-01-01"),
                          ("LEGIARTI000000000011", "MODIFIE_MORT_NE", "2005-01-01", "2004-01-01"),
                          ("LEGIARTI000000000012", "VIGUEUR", "2010-01-01", "2999-01-01")])
    doc = SOURCE.parse(_raw(a, member("article", "LEGIARTI000000000010")))
    assert lsrc.next_start(doc.extra) == "2010-01-01"
    assert "date-fin-disagrees" not in {i.code for i in doc.issues}


def test_the_origin_s_end_is_compared_with_the_derived_one():
    a = article("LEGIARTI000000000020", etat="MODIFIE", debut="2000-01-01", fin="2005-01-01")
    doc = SOURCE.parse(_raw(a, member("article", "LEGIARTI000000000020")))
    issue = next(i for i in doc.issues if i.code == "date-fin-disagrees")
    assert "DATE_FIN 2005-01-01, derived 2999-01-01" in issue.detail, "no later version listed"
    ok = article("LEGIARTI000000000021", etat="VIGUEUR", debut="2000-01-01", fin="2999-01-01")
    assert "date-fin-disagrees" not in {i.code for i in SOURCE.parse(_raw(ok, member("article", "x"))).issues}
    empty = article("LEGIARTI000000000022", etat="", debut="2000-01-01")
    assert "etat-empty" in {i.code for i in SOURCE.parse(_raw(empty, member("article", "x"))).issues}


def test_a_placeholder_start_is_kept_and_named():
    doc = SOURCE.parse(_raw(ART_PLACEHOLDER, member("article", "LEGIARTI000006398122")))
    assert doc.extra["version_from"] == "2222-02-22"
    assert "placeholder-start" in {i.code for i in doc.issues}
    day, src = lsrc.sort_day(lx.index_fields(ART_PLACEHOLDER))
    assert (day, src) == ("1500-01-01", "fallback"), "the code's text dates are 2999-01-01 too"


# ---- the id ---------------------------------------------------------------------------------

def test_the_id_sorts_by_its_day_and_a_file_without_one_falls_back(corpus):
    refs = {r.locator.rsplit("/", 1)[-1]: r for r in _refs(corpus)}
    art = refs["LEGIARTI000006398122.xml"]
    assert eli.id_date(int(art.native_id)).isoformat() == "1952-06-29"
    assert int(art.native_id) == eli.expression_native_id("1952-06-29", (), "LEGIARTI000006398122")
    sec = refs["LEGISCTA000006135575.xml"]
    assert eli.id_date(int(sec.native_id)).isoformat() == lsrc.FALLBACK_DAY == "1500-01-01"
    doc = SOURCE.parse(SOURCE.fetch(sec))
    assert "sort-day-fallback" in {i.code for i in doc.issues}
    tv = [r for r in refs.values() if "texte/version" in r.locator][0]
    assert eli.id_date(int(tv.native_id)).isoformat() == "1952-06-29", "the text version's own DATE_DEBUT"


def test_the_text_s_two_files_are_two_records(corpus):
    """A LEGITEXT id names its version and its structure: two records, two ids."""
    refs = [r for r in _refs(corpus) if CMMI in r.locator.rsplit("/", 1)[-1]]
    assert len(refs) == 2 and len({r.native_id for r in refs}) == 2
    assert {lsrc.record_key(k, CMMI) for k in ("texte_version", "textelr")} == {
        f"{CMMI}/version", f"{CMMI}/struct"}


def test_a_date_out_of_the_id_s_range_and_the_text_dates_are_tried_in_order():
    assert lsrc.sort_day({"date_debut": "2999-01-01", "date_texte": "1978-01-06", "date_publi": "1978-01-07"}) \
        == ("1978-01-06", "date_texte")
    assert lsrc.sort_day({"date_debut": "2300-01-01", "date_texte": "2222-02-22", "date_publi": "1978-01-07"}) \
        == ("1978-01-07", "date_publi")
    assert lsrc.sort_day({"date_debut": None, "date_texte": "", "date_publi": "x"}) == ("1500-01-01", "fallback")


def test_a_collision_is_refused_loudly(tmp_path, monkeypatch):
    root = tmp_path / "legi"
    tarball(root / BASE, REAL)
    monkeypatch.setattr(lsrc, "native_id_of", lambda kind, ident, fields: 42)
    refs = _refs(root)
    docs = [r for r in refs if r.scope == "document"]
    errs = [r for r in refs if r.scope == "container" and "collides" in (r.error or "")]
    assert len(docs) == 1 and len(errs) == len(REAL) - 1


# ---- the tarballs: pack, order, increments, deletions ---------------------------------------

def test_one_pack_per_tarball_fresh_by_its_metadata(corpus):
    tar = corpus / BASE
    p = pack.ensure(tar)
    assert p.parent.name == ".pack" and p.name == BASE + pack.SUFFIX
    assert pack.member_bytes(tar, member("article", "LEGIARTI000006398122")) == ART_ABROGE
    with pytest.raises(pack.NotInPack):
        pack.member_bytes(tar, member("article", "LEGIARTI000099999999"))
    mtime = p.stat().st_mtime_ns
    pack.reset_caches()
    assert pack.ensure(tar) == p and p.stat().st_mtime_ns == mtime, "fresh: not rebuilt"


def test_an_id_twice_in_one_tarball_is_found_by_its_member_name(tmp_path):
    root = tmp_path / "legi"
    other = member("article", "LEGIARTI000006398122", folder="code_non_vigueur")
    changed = ART_ABROGE.replace(b"La fabrication", b"La frappe")
    tarball(root / BASE, {**REAL, other: changed})
    assert pack.member_bytes(root / BASE, other) == changed
    assert pack.member_bytes(root / BASE, member("article", "LEGIARTI000006398122")) == ART_ABROGE


def test_the_baseline_then_the_increments_after_it_then_their_deletion_lists(tmp_path):
    root = tmp_path / "legi"
    tarball(root / BASE, REAL)
    a1 = member("article", "LEGIARTI000006398122")
    # inside the baseline (dated before it): not applied, it is in the baseline
    tarball(root / "LEGI_20250701-100000.tar.gz",
            {a1: ART_ABROGE.replace(b"La fabrication", b"OLD")}, prefix="20250701-100000/")
    # after it: a new version of the article, then a later increment that deletes the section
    newer = ART_ABROGE.replace(b"La fabrication", b"La fabrication nouvelle")
    tarball(root / "LEGI_20250801-100000.tar.gz", {a1: newer}, mtime=1_800_000_000,
            prefix="20250801-100000/")
    sec = member("section_ta", "LEGISCTA000006135575")
    tarball(root / "LEGI_20250901-100000.tar.gz",
            {"liste_suppression_legi.dat": (sec[:-4] + "\n").encode()}, prefix="20250901-100000/")
    base, incs, inside = lsrc.tarballs(root)
    assert base.name == BASE and [p.name for p in incs] == ["LEGI_20250801-100000.tar.gz",
                                                            "LEGI_20250901-100000.tar.gz"]
    assert [p.name for p in inside] == ["LEGI_20250701-100000.tar.gz"]
    refs = _refs(root)
    live = {r.locator.rsplit("/", 1)[-1]: r for r in refs if r.scope == "document" and not r.deleted}
    assert "LEGISCTA000006135575.xml" not in live
    art = live["LEGIARTI000006398122.xml"]
    assert "LEGI_20250801-100000.tar.gz::20250801-100000/" in art.locator, "the newest sighting"
    assert b"nouvelle" in SOURCE.fetch(art).data
    gone = [r for r in refs if r.deleted]
    assert len(gone) == 1 and "liste_suppression_legi.dat" in gone[0].error
    assert gone[0].extra == {"reason": "withdrawn"} and gone[0].error.startswith("withdrawn:")
    assert gone[0].native_id == str(lsrc.native_id_of("section_ta", "LEGISCTA000006135575",
                                                      lx.index_fields(SECTION)))


def test_a_dossier_deletion_list_removes_a_folder(tmp_path):
    root = tmp_path / "legi"
    tarball(root / BASE, REAL)
    folder = member("article", "x").split("/article/")[0]
    tarball(root / "LEGI_20250801-100000.tar.gz",
            {"liste_suppression_legi_dossier.dat": (folder + "\n").encode()}, prefix="20250801-100000/")
    refs = _refs(root)
    live = [r for r in refs if r.scope == "document" and not r.deleted]
    assert [r.locator.rsplit("/", 1)[-1] for r in live] == ["LEGIARTI000019325262.xml"], "another text"
    assert len([r for r in refs if r.deleted]) == 4


def test_a_re_key_withdraws_the_old_id(tmp_path):
    """A placeholder the origin later fixes moves the sort day: the old id is
    withdrawn, never left answering beside the new one."""
    root = tmp_path / "legi"
    a1 = member("article", "LEGIARTI000006398122")
    tarball(root / BASE, {a1: ART_PLACEHOLDER})
    tarball(root / "LEGI_20250801-100000.tar.gz", {a1: ART_ABROGE}, prefix="20250801-100000/")
    refs = _refs(root)
    old = lsrc.native_id_of("article", "LEGIARTI000006398122", lx.index_fields(ART_PLACEHOLDER))
    new = lsrc.native_id_of("article", "LEGIARTI000006398122", lx.index_fields(ART_ABROGE))
    assert old != new
    assert [r.native_id for r in refs if not r.deleted] == [str(new)]
    assert [(r.native_id, r.error.split(":")[0]) for r in refs if r.deleted] == [(str(old), "withdrawn")]


def test_the_same_id_under_two_folders_is_one_record_and_an_issue(tmp_path):
    root = tmp_path / "legi"
    other = member("article", "LEGIARTI000006398122", folder="code_non_vigueur")
    tarball(root / BASE, REAL)
    tarball(root / "LEGI_20250801-100000.tar.gz", {other: ART_ABROGE}, prefix="20250801-100000/",
            mtime=1_800_000_000)
    refs = [r for r in _refs(root) if r.scope == "document" and "LEGIARTI000006398122" in r.locator]
    assert len(refs) == 1 and "code_non_vigueur" in refs[0].locator, "the newest sighting"
    doc = SOURCE.parse(SOURCE.fetch(refs[0]))
    issue = next(i for i in doc.issues if i.code == "id-in-two-folders")
    assert "code_en_vigueur" in issue.detail


def test_a_work_key_that_moves_between_sightings_is_an_issue(tmp_path):
    root = tmp_path / "legi"
    a = member("article", "LEGIARTI000000000050")
    v1 = article("LEGIARTI000000000050", versions=[("LEGIARTI000000000050", "VIGUEUR", "1952-06-29", "2999-01-01")])
    v2 = article("LEGIARTI000000000050", versions=[("LEGIARTI000000000049", "MODIFIE", "1900-01-01", "1952-06-29"),
                                                    ("LEGIARTI000000000050", "VIGUEUR", "1952-06-29", "2999-01-01")])
    tarball(root / BASE, {a: v1})
    tarball(root / "LEGI_20250801-100000.tar.gz", {a: v2}, prefix="20250801-100000/")
    ref = next(r for r in _refs(root) if not r.deleted)
    doc = SOURCE.parse(SOURCE.fetch(ref))
    assert doc.extra["work_key"] == "LEGIARTI000000000049"
    assert "work-key-moved" in {i.code for i in doc.issues}


def test_files_of_one_chain_that_disagree_on_its_key_are_an_issue(tmp_path):
    root = tmp_path / "legi"
    v1 = article("LEGIARTI000000000060", etat="MODIFIE", fin="2000-01-01",
                 versions=[("LEGIARTI000000000060", "MODIFIE", "1952-06-29", "2000-01-01"),
                           ("LEGIARTI000000000061", "VIGUEUR", "2000-01-01", "2999-01-01")])
    v2 = article("LEGIARTI000000000061", debut="2000-01-01",
                 versions=[("LEGIARTI000000000055", "MODIFIE", "1900-01-01", "1952-06-29"),
                           ("LEGIARTI000000000060", "MODIFIE", "1952-06-29", "2000-01-01"),
                           ("LEGIARTI000000000061", "VIGUEUR", "2000-01-01", "2999-01-01")])
    tarball(root / BASE, {member("article", "LEGIARTI000000000060"): v1,
                          member("article", "LEGIARTI000000000061"): v2})
    codes = {d.extra["work_key"]: {i.code for i in d.issues} for d in _all(root)}
    assert "work-key-disagrees" in codes["LEGIARTI000000000060"]
    assert "work-key-disagrees" in codes["LEGIARTI000000000055"]


def test_an_unreadable_tarball_is_a_scan_report_not_a_document(tmp_path):
    root = tmp_path / "legi"
    root.mkdir()
    (root / BASE).write_bytes(b"not a gzip")
    refs = _refs(root)
    assert len(refs) == 1 and refs[0].scope == "container" and "unreadable tarball" in refs[0].error


def test_a_scan_whose_tarballs_have_not_moved_yields_nothing_once_committed(tmp_path):
    root, state_dir = tmp_path / "legi", tmp_path / "state"
    state_dir.mkdir()
    tarball(root / BASE, REAL)
    state = DiscoverState(corpus_root=root, state_dir=state_dir)
    first = list(SOURCE.discover(state))
    assert len(first) == len(REAL)
    state.committed = state.received
    for hook in state.on_commit:
        hook()
    assert list(SOURCE.discover(DiscoverState(corpus_root=root, state_dir=state_dir))) == []
    # a new baseline no longer holding a file: the id map withdraws it
    tarball(root / "Freemium_legi_global_20260713-140000.tar.gz",
            {k: v for k, v in REAL.items() if "LEGISCTA" not in k})
    again = list(SOURCE.discover(DiscoverState(corpus_root=root, state_dir=state_dir)))
    gone = [r for r in again if r.deleted]
    assert len(gone) == 1 and "LEGISCTA000006135575" in gone[0].error and "no longer" in gone[0].error


def test_a_scan_that_could_not_read_the_whole_origin_withdraws_nothing_it_did_not_see(tmp_path):
    """An unreadable baseline, or increments alone, say nothing of the rest:
    no id the last scan held is withdrawn for their absence, and neither the
    cursor nor the id map is written."""
    root, state_dir = tmp_path / "legi", tmp_path / "state"
    state_dir.mkdir()
    tarball(root / BASE, REAL)
    state = DiscoverState(corpus_root=root, state_dir=state_dir)
    list(SOURCE.discover(state))
    state.committed = state.received
    for hook in state.on_commit:
        hook()
    pack.reset_caches()
    (root / BASE).write_bytes(b"truncated")
    state = DiscoverState(corpus_root=root, state_dir=state_dir)
    refs = list(SOURCE.discover(state))
    assert [r.scope for r in refs] == ["container"] and not any(r.deleted for r in refs)
    assert state.on_commit == []
    (root / BASE).unlink()
    tarball(root / "LEGI_20250801-100000.tar.gz", {member("article", "LEGIARTI000000000070"):
                                                   article("LEGIARTI000000000070")}, prefix="20250801-100000/")
    refs = list(SOURCE.discover(DiscoverState(corpus_root=root, state_dir=state_dir)))
    assert len(refs) == 1 and not refs[0].deleted, "increments alone: what they hold, nothing withdrawn"


# ---- relations ------------------------------------------------------------------------------

def test_liens_are_eli_edges_in_the_direction_the_form_gives(corpus):
    a = _docs(corpus)["LEGIARTI000006398122.xml"]
    assert list(SOURCE.relations(a)) == [
        __import__("app.plugins.base", fromlist=["Edge"]).Edge(to="jorf:JORFTEXT000000574488",
                                                             type="eli:repealed_by")]
    m = _docs(corpus)["LEGIARTI000019325262.xml"]
    assert [(e.type, e.to) for e in SOURCE.relations(m)] == [("eli:amended_by", "legi:LEGIARTI000006269446")]
    # the active forms, article to article
    edge = lsrc.edge_of
    assert edge({"typelien": "MODIFIE", "sens": "source", "id": "LEGIARTI000000000001"}) == \
        ("eli:amends", "legi:LEGIARTI000000000001")
    assert edge({"typelien": "ABROGE", "sens": "source", "id": "LEGIARTI000000000001"}) == \
        ("eli:repeals", "legi:LEGIARTI000000000001")
    assert edge({"typelien": "ABROGATION", "sens": "cible", "id": "JORFARTI000000000002"}) == \
        ("eli:repeals", "jorf:JORFARTI000000000002"), "the linked element is the target: this file repeals it"
    assert edge({"typelien": "CITATION", "sens": "source", "id": "", "cidtexte": "JORFTEXT000000000003"}) == \
        ("eli:cites", "jorf:JORFTEXT000000000003"), "no article id: the text"
    assert edge({"typelien": "CITATION", "sens": "cible", "id": "LEGIARTI000000000004"}) is None, \
        "a citation OF this file is the citing file's own edge"
    assert edge({"typelien": "CODIFICATION", "sens": "source", "id": "JORFTEXT000000000005"}) is None
    assert edge({"typelien": "MODIFIE", "sens": "source", "id": "", "cidtexte": ""}) is None


# ---- locator, origin, slicing, registry -----------------------------------------------------

def test_origin_is_the_tarball_and_the_member_and_its_hash_the_member_s_bytes(corpus):
    import hashlib
    r = next(r for r in _refs(corpus) if "LEGIARTI000006398122" in r.locator)
    assert plugin_io.origin_locator(SOURCE, r.locator, corpus) == f"{BASE}#{member('article', 'LEGIARTI000006398122')}"
    raw = SOURCE.fetch(r)
    assert raw.raw_hash == hashlib.blake2b(ART_ABROGE, digest_size=16).hexdigest()


def test_origin_coverage(corpus):
    from tests.origin_coverage import assert_covered
    for r in _refs(corpus):
        assert_covered(SOURCE, r, corpus)


def test_the_slice_from_the_record_and_from_the_row_agree(corpus):
    scheme = slicing.scheme_for("legi")
    for r in _refs(corpus):
        doc = SOURCE.parse(SOURCE.fetch(r))
        got = slicing.slice_of(scheme, doc, int(r.native_id))
        hint = slicing.slice_hint("legi", {"path": r.locator, "native_id": r.native_id})
        assert got == hint and slicing.check_slice_name(got)
    assert {slicing.slice_hint("legi", {"path": r.locator, "native_id": r.native_id}).split("/")[0]
            for r in _refs(corpus)} == {"kind=article", "kind=section_ta", "kind=texte_version", "kind=textelr"}


def test_the_plugin_is_registered_with_its_declarations():
    assert collections_mod.PLUGIN_REGISTRY["legi"] is SOURCE
    assert SOURCE.license_class.value == "green" and SOURCE.native_id_order == "numeric"
    assert set(SOURCE.ISSUES) >= {"sort-day-fallback", "modifie-mort-ne", "date-fin-disagrees",
                                  "id-in-two-folders", "work-key-moved"}
    terms = (Path(lsrc.__file__).parent / "TERMS.txt").read_bytes()
    import hashlib
    assert hashlib.sha256(terms).hexdigest() == \
        "a1deb9d7554ad10e041dbd7440b3dec0ac0d3679f9e1655ee9d850908b015995", "Etalab's LO.md, byte for byte"
    assert b"\r" not in terms


# ---- end to end: the rdf and fts stages, the version table, the search ----------------------

@pytest.fixture
def held(tmp_path, isolated_config):
    root = tmp_path / "legi"
    v1 = article("LEGIARTI000000000101", num="9", etat="MODIFIE", debut="1952-06-29", fin="2006-12-27",
                 text="Les médailles sont frappées anciennement.",
                 versions=[("LEGIARTI000000000101", "MODIFIE", "1952-06-29", "2006-12-27"),
                           ("LEGIARTI000000000102", "VIGUEUR", "2006-12-27", "2999-01-01")])
    v2 = article("LEGIARTI000000000102", num="9", etat="VIGUEUR", debut="2006-12-27",
                 text="Les médailles sont frappées désormais.",
                 versions=[("LEGIARTI000000000101", "MODIFIE", "1952-06-29", "2006-12-27"),
                           ("LEGIARTI000000000102", "VIGUEUR", "2006-12-27", "2999-01-01")])
    members = {**REAL, member("article", "LEGIARTI000000000101"): v1,
               member("article", "LEGIARTI000000000102"): v2,
               member("article", "LEGIARTI000019325262", CASF): ART_MORT_NE}
    tarball(root / BASE, members)
    c = collections_mod.Collection(
        name="legi", plugin="legi", profile=None, embed_mode_default=None,
        corpus_dir=root, manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb")
    con = db.connect(c)
    discovery.sync_collection(con, c)
    for stage in ("rdf", "fts"):
        flag = f"{stage}_done"
        while True:
            row = con.execute(f"SELECT * FROM documents WHERE {flag}=0 AND error IS NULL "
                              f"AND in_scope=1 LIMIT 1").fetchone()
            if row is None:
                break
            getattr(worker, f"stage_{stage}")(con, row, c)
            if con.in_transaction:
                con.execute("COMMIT")
    return c, con


def _nid_of(con, ident: str) -> int:
    rows = con.execute("SELECT native_id, path FROM documents").fetchall()
    return next(int(r["native_id"]) for r in rows if r["path"].endswith(f"/{ident}.xml") and "/article/" in r["path"])


def test_the_version_table_holds_articles_only_chained_and_closed(held):
    c, con = held
    t = {int(r["native_id"]): dict(r) for r in con.execute("SELECT * FROM validity")}
    a1, a2 = _nid_of(con, "LEGIARTI000000000101"), _nid_of(con, "LEGIARTI000000000102")
    ab = _nid_of(con, "LEGIARTI000006398122")
    assert set(t) == {a1, a2, ab}, "no text, section, structure or MODIFIE_MORT_NE record is a version"
    assert (t[a1]["valid_from"], t[a1]["valid_to"], t[a1]["shown"]) == (19520629, 20061227, 0)
    assert (t[a2]["valid_from"], t[a2]["valid_to"], t[a2]["shown"]) == (20061227, None, 1)
    assert (t[ab]["valid_from"], t[ab]["valid_to"]) == (19520629, 20010101), "closed by its own DATE_FIN"
    assert t[a1]["work"] == t[a2]["work"] == "LEGIARTI000000000101"
    assert con.execute("SELECT count(*) FROM documents WHERE error IS NOT NULL").fetchone()[0] == 0


def test_the_search_admits_one_version_per_article_and_never_the_mort_ne(held):
    c, con = held
    a1, a2 = _nid_of(con, "LEGIARTI000000000101"), _nid_of(con, "LEGIARTI000000000102")

    def books(q, day):
        return {int(h["book_id"]) for h in fts.search("fr", q, limit=50, collection=c,
                                                      validity=validity.window(c, day))}
    arts = {a1, a2}
    assert books("médailles frappées", None) & arts == {a2}
    assert books("médailles frappées", 19990101) & arts == {a1}
    assert books("médailles frappées", 20070101) & arts == {a2}
    mort = next(int(r["native_id"]) for r in con.execute("SELECT native_id, path FROM documents")
                if "LEGIARTI000019325262" in r["path"])
    assert books("GUIDE BARÈME déficiences", None) == set()
    got = retrieval.search(con, "GUIDE BARÈME déficiences incapacités", k=10, mode="hybrid",
                           translate=False, collection=c)
    assert mort not in {int(h["book_id"]) for h in got["hits"]}, "held, never returned"
    # a section is not a version: it answers at every date, and hides no article
    sec = next(int(r["native_id"]) for r in con.execute("SELECT native_id, path FROM documents")
               if "LEGISCTA000006135575" in r["path"])
    assert sec in books("Frappe", None) and sec in books("Frappe", 19990101)
    assert a2 in books("médailles", None) and a1 not in books("médailles", None)
