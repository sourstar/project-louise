"""A source is a work of its own time (operator, 2026-11-26): the answer's
model is told each source's first publication and its subjects, as the
record states them, and how to read them — what a novel narrates is not
historical fact, and a source's "now" is its first publication's — and the
answer's sources and its PDF show the first publication."""
from __future__ import annotations

from datetime import datetime, timezone

from app import api, ask_pdf, retrieval

NAPOLEON = {"chunk_id": "gutenberg:42:0:1", "book_id": 42, "title": "Il cinque maggio", "authors": "Manzoni",
            "lang": "it", "text": "Ei fu. Siccome immobile…", "first_published": 1821,
            "work_subjects": ("Napoleon I, Emperor of the French, 1769-1821 -- Poetry",)}
HISTORY = {"chunk_id": "gutenberg:7:0:1", "book_id": 7, "title": "History of Napoleon", "authors": "Abbott",
           "lang": "en", "text": "He died in 1821.", "first_published": 1855, "first_published_by": "Wikidata",
           "work_subjects": ("Napoleon I, Emperor of the French, 1769-1821",)}
BARE = {"chunk_id": "gutenberg:9:0:1", "book_id": 9, "title": "Untitled", "authors": "?", "lang": "en",
        "text": "Some text."}


def _sources(*hits) -> str:
    m = retrieval.build_answer_messages("Quando morì Napoleone?", {"hits": list(hits), "catalog": [], "facts": []})
    return m[0]["content"], m[1]["content"]


def test_each_source_says_its_first_publication_and_whether_it_is_a_literary_work():
    system, user = _sources(NAPOLEON, HISTORY, BARE)
    assert "[1] gutenberg:42 — Il cinque maggio — Manzoni (it) — first published 1821 — a literary work — " \
           "subjects: Napoleon I, Emperor of the French, 1769-1821 -- Poetry" in user
    assert "[2] gutenberg:7 — History of Napoleon — Abbott (en) — first published 1855 (by Wikidata) — " \
           "subjects: Napoleon I, Emperor of the French, 1769-1821" in user
    assert "a literary work" not in user.split("[2]")[1].split("\n")[0], "history is not called literature"
    assert "[3] gutenberg:9 — Untitled — ? (en)\n" in user, "a record that says nothing: the header says nothing"


def test_the_model_is_told_how_to_read_a_work_and_its_dates():
    system, _ = _sources(BARE)
    assert "never present its events or its characters' deeds as historical fact" in system
    assert "counts from that work's first publication" in system and "never from today" in system


def test_subjects_in_other_vocabularies_and_as_a_string():
    for subjects in ("Romanzi storici | Napoleone", ("Drama",), ("Short stories",), ("Poésie",)):
        _, user = _sources({**BARE, "work_subjects": subjects})
        assert "a literary work" in user, subjects
    _, user = _sources({**BARE, "work_subjects": ("France -- History -- 1789-1815",)})
    assert "a literary work" not in user


def test_the_answers_sources_and_its_pdf_show_the_first_publication():
    got = api.source_entries([HISTORY, BARE])
    assert got[0]["first_published"] == 1855 and got[0]["first_published_by"] == "Wikidata"
    assert got[1]["first_published"] is None
    doc = ask_pdf.AskPdf(question="q", answer="Morì nel 1821 [1].",
                         references=[ask_pdf.PdfReference(n=1, title="History of Napoleon",
                                                          first_published="1855 · by Wikidata")])
    html = ask_pdf.build_html(doc, base_url="https://ask.example.org", node="n",
                              when=datetime(2026, 11, 26, tzinfo=timezone.utc))
    assert "first published 1855 · by Wikidata" in html
    assert ask_pdf._year_said(HISTORY) == "1855 · by Wikidata" and ask_pdf._year_said(BARE) is None
