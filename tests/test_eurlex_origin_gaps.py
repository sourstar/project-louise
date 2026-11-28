"""PARSER 6: 09 §9.5's `origin_gaps` (the operator's rule B, 2026-11-28: "keep
the record, mark it as degraded, mark the parts as missing"), read from the
unit's OWN files so a verifier reproduces it from the corpus."""
from __future__ import annotations

from types import SimpleNamespace

from app.pack import records as pack_records
from app.plugins.eurlex import SOURCE, formex
from app.plugins.eurlex import source as src
from tests.test_eurlex_parser4 import _docs, _lay


def test_a_whole_unit_states_no_gap(tmp_path):
    _lay(tmp_path, "31972R1473")
    (_ref, doc), = _docs(SOURCE, tmp_path).values()
    assert doc.origin_gaps == []


def test_a_file_the_wrapper_names_and_the_unit_lacks_is_absent(monkeypatch):
    act = SimpleNamespace(rendered_sections=None, missing=["L_1.000102.xml"], wrapper_file="w.doc.xml")
    monkeypatch.setattr(formex, "_parse", lambda b, n: (_ for _ in ()).throw(formex.FormexError("x")))
    assert src._origin_gaps({"w.doc.xml": b"<DOC/>"}, act, "3") == [{"part": "L_1.000102.xml", "why": "absent"}]


def test_files_in_each_other_s_place_are_each_another_s(monkeypatch):
    """bg 32006R1828: `…01009703.xml` and `…01011401.xml` hold each other."""
    a, b = "L_2006371BG.01009703.xml", "L_2006371BG.01011401.xml"
    act = SimpleNamespace(rendered_sections=None, missing=[], wrapper_file="w.doc.xml")
    monkeypatch.setattr(formex, "_parse", lambda data, n: object())
    monkeypatch.setattr(formex, "main_names", lambda w: [a])
    monkeypatch.setattr(formex, "annex_names", lambda w: [b])
    monkeypatch.setattr(formex, "misnamed", lambda n, h: None)
    places = {a: ("L.371", 97, 3, 114, 1), b: ("L.371", 114, 1, 97, 3)}       # each states the other's page
    monkeypatch.setattr(formex, "stated_place", lambda n, h: places[n])
    got = src._origin_gaps({"w.doc.xml": b"", a: b"x", b: b"y"}, act, "32006R1828")
    assert got == [{"part": a, "why": f"another's: {b}"}, {"part": b, "why": f"another's: {a}"}]
    assert pack_records.origin_gaps(got) == got, "the format's own shape"


def test_a_rendition_states_none_and_parser_5_states_none(tmp_path):
    act = SimpleNamespace(rendered_sections=[("x", "y")])
    assert src._origin_gaps({}, act, "3") == []
    _lay(tmp_path, "31972R1473")
    (_ref, doc), = _docs(SOURCE.at_parser_version(5), tmp_path).values()
    assert doc.origin_gaps == []
