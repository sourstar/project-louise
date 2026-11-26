"""TITLE-ONLY (lesson E1, 2026-11-25: 41 Formex acts of EUR-Lex5 indexed with
no chunk, their source holding only a title — an empty PREAMBLE and
ENACTING.TERMS). A chunkless document states why (app/build_check.py refuses
one that states none): the issue `title-only`, said, the record held."""
from __future__ import annotations

from app.plugins.eurlex import SOURCE, formex
from app.plugins.eurlex import source as src
from tests.test_eurlex_parser4 import _docs, _lay


def test_an_act_whose_source_holds_only_its_title_says_so(tmp_path, monkeypatch):
    _lay(tmp_path, "31972R1473")
    monkeypatch.setattr(formex, "sections", lambda act: [("Article 1", "  "), ("Annex", "")])
    (_ref, doc), = _docs(SOURCE, tmp_path).values()
    assert [i.code for i in doc.issues if i.code == "title-only"] == ["title-only"]
    assert doc.title


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
