"""A unit's day as the PARSE decides its wrapper (2026-11-25, EUR-Lex5.next):
the night's scans inserted 7,871 rows under new ids. Discovery asked
`formex.own_wrapper` without the tree's listing and the act's date, so since
the lone-wrapper rules of 2026-11-21/23 it refused wrappers the parse reads as
the act's own and dated those units by their tree's work day: a new id for an
unchanged unit (31958D1006(02) en: its wrapper's NO.CELEX says (01), its tree
lists it as (02)'s; same mtime, same size, 1958-10-06 became 1958-09-15), and
the old row never retired, because `superseded_ids` could not name it.

Now discovery asks again with the listing where the lone wrapper is refused,
and `superseded_ids` names every day the unit has had a rule for — as the
parse decides it, the lone wrapper's whatever it states (before 2026-11-21),
the wrapper found by its content, the tree's — so a scan retires the other
rows at that locator."""
from __future__ import annotations

import os
import shutil

from app import db
from app.plugins import eli
from app.plugins.eurlex import SOURCE, rdf
from app.plugins.eurlex import source as src
from tests.acquire.test_eurlex_dump import FORMEX
from tests.test_eurlex_identity import CELEX, _coll, _scan

WRAPPER = "L_1972160IT.01000101.doc.xml"
OJ_DAY = "1972-07-16"          # the wrapper's PUBLICATION.REF
WORK_DAY = "1972-06-30"        # the tree's work day (its date_document)


def _unit(coll, *, label: str | None = None):
    unit = coll.corpus_dir / src.unit_rel("it", CELEX)
    shutil.copytree(FORMEX[2], unit, ignore=shutil.ignore_patterns("notice.xml", "LEG_MTD*"))
    if label:
        w = unit / WRAPPER
        w.write_bytes(w.read_bytes().replace(b"<BIB.DOC>", f"<BIB.DOC><NO.CELEX>{label}</NO.CELEX>".encode(), 1))
    return unit


def _day(unit) -> str | None:
    entries = sorted(os.scandir(unit), key=lambda e: e.name)
    return src._unit_day(unit, CELEX, entries, SOURCE.TREE_FACTS)


def _nid(day: str) -> str:
    return str(eli.expression_native_id(day, (CELEX, "it"), src.version_key(CELEX)))


def test_a_listed_wrapper_labelled_another_act_dates_the_unit_as_the_parse_reads_it(tmp_path):
    coll = _coll(tmp_path)
    plain = _day(_unit(coll))
    assert plain == OJ_DAY
    shutil.rmtree(coll.corpus_dir)
    unit = _unit(coll, label="31972R9999")
    entries = sorted(os.scandir(unit), key=lambda e: e.name)
    try:
        src.wrapper_head(CELEX, entries)
    except src.formex.FormexError:
        pass
    else:
        raise AssertionError("without the listing the lone wrapper naming another act is refused")
    assert _day(unit) == OJ_DAY, "the tree lists the wrapper as this act's: the parse reads it, the day is its OJ's"


def test_the_id_a_refused_wrapper_gave_is_retired_by_the_next_scan(tmp_path, isolated_config):
    """Tonight's rows: an unchanged unit held under its OJ day's id, and a new
    row under its tree day's id. The scan keeps the first and retires the second."""
    coll = _coll(tmp_path)
    _unit(coll, label="31972R9999")
    con = db.connect(coll)
    try:
        (ref,), _ = _scan(con, coll)
        assert ref.native_id == _nid(OJ_DAY)
        assert _nid(WORK_DAY) in SOURCE.superseded_ids(ref)
        stray = _nid(WORK_DAY)
        con.execute("INSERT INTO documents(uid, source, native_id, path) VALUES(?,?,?,?)",
                    (f"eurlex:{stray}", "eurlex", stray, ref.locator))
        con.commit()
        from app.hold import identity
        counts, retire = identity.plan(con, SOURCE, log=lambda m: None)
        assert retire == [(f"eurlex:{stray}", f"eurlex:{ref.native_id}")], retire
    finally:
        con.close()


def test_a_wrapper_the_rules_now_refuse_names_the_id_the_old_rule_gave(tmp_path, monkeypatch):
    """hr 32009D0480 holds a 1998 act's package: dated by it before
    2026-11-21, by its tree since. `superseded_ids` names the old id."""
    coll = _coll(tmp_path)
    unit = _unit(coll, label="31972R9999")
    monkeypatch.setattr(src, "_tree_listing", lambda *a: (set(), None))     # the tree lists none of it
    assert _day(unit) == WORK_DAY
    ref = SOURCE.ref_of_unit(unit)
    assert ref is not None and ref.native_id == _nid(WORK_DAY)
    assert _nid(OJ_DAY) in SOURCE.superseded_ids(ref), "the lone wrapper's day, the rule before 2026-11-21"


def test_an_unrefused_unit_is_dated_without_reading_its_tree(tmp_path, monkeypatch):
    coll = _coll(tmp_path)
    unit = _unit(coll)
    monkeypatch.setattr(src, "_tree_listing", lambda *a: (_ for _ in ()).throw(AssertionError("tree read")))
    assert _day(unit) == OJ_DAY
    assert rdf.TREE_FILE in os.listdir(unit)
