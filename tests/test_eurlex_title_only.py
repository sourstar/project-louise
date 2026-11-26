"""TITLE-ONLY (lesson E1, 2026-11-25: 41 Formex acts of EUR-Lex5 indexed with
no chunk, their source holding only a title — an empty PREAMBLE and
ENACTING.TERMS). A chunkless document states why (app/build_check.py refuses
one that states none): the issue `title-only`, decided from the SOURCE, never
from the parse's output (architecture's read of b4f6b52a). The 41 acts of
EUR-Lex5 carry it once re-parsed (`hold records build`)."""
from __future__ import annotations

import re

from app.plugins.eurlex import SOURCE, formex
from app.plugins.eurlex import source as src
from tests.test_eurlex_parser4 import _docs, _lay

BODY = "L_1972160IT.01000101.xml"


def test_a_formex_act_whose_source_holds_only_its_title_says_so(tmp_path):
    unit = _lay(tmp_path, "31972R1473")
    b = unit / BODY
    data = b.read_bytes()
    data = re.sub(rb"<PREAMBLE>.*?</PREAMBLE>", b"<PREAMBLE/>", data, flags=re.S)
    data = re.sub(rb"<ENACTING\.TERMS>.*?</ENACTING\.TERMS>", b"<ENACTING.TERMS/>", data, flags=re.S)
    b.write_bytes(data)
    (_ref, doc), = _docs(SOURCE, tmp_path).values()
    assert "title-only" in [i.code for i in doc.issues]
    assert doc.title


def test_a_parse_that_loses_an_act_s_text_is_never_called_title_only(tmp_path, monkeypatch):
    """The source holds text; the parse loses it: no `title-only`, so the
    chunkless check catches the loss (invariant 11)."""
    _lay(tmp_path, "31972R1473")
    monkeypatch.setattr(formex, "sections", lambda act: [("Article 1", "  ")])
    (_ref, doc), = _docs(SOURCE, tmp_path).values()
    assert "title-only" not in [i.code for i in doc.issues]


def test_an_act_with_text_states_no_title_only(tmp_path):
    _lay(tmp_path, "31972R1473")
    (_ref, doc), = _docs(SOURCE, tmp_path).values()
    assert "title-only" not in [i.code for i in doc.issues]


def test_the_issue_is_declared():
    assert "title-only" in src.ISSUES


def test_the_walk_says_where_it_is(tmp_path):
    """Lesson C3: the startup scan was silent 28-55 minutes."""
    from app.plugins.base import DiscoverState
    _lay(tmp_path, "31972R1473")
    said = []
    list(SOURCE.discover(DiscoverState(corpus_root=tmp_path, say=said.append)))
    assert said and said[0].startswith("walking lang=it/1972")
