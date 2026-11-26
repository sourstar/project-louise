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
# "by": where the year is not the record's own plain statement — today only
# PG's automatic summary (app/ask/works.py attach_first_published); never an
# annotation's claim, which no code sets as first_published
HISTORY = {"chunk_id": "gutenberg:7:0:1", "book_id": 7, "title": "History of Napoleon", "authors": "Abbott",
           "lang": "en", "text": "He died in 1821.", "first_published": 1855,
           "first_published_by": "PG's automatic summary",
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
    assert "[2] gutenberg:7 — History of Napoleon — Abbott (en) — first published 1855 (by PG\'s automatic summary) — " \
           "subjects: Napoleon I, Emperor of the French, 1769-1821" in user
    assert "a literary work" not in user.split("[2]")[1].split("\n")[0], "history is not called literature"
    assert "[3] gutenberg:9 — Untitled — ? (en)\n" in user, "a record that says nothing: the header says nothing"


def test_the_model_is_told_how_to_read_a_work_and_its_dates():
    system, _ = _sources(BARE)
    assert "never present its events or its characters' deeds as historical fact" in system
    assert "counts from that work's first publication" in system and "never from today" in system


def test_a_literary_work_is_told_by_its_subject_headings_form_never_by_a_word_in_them():
    """Architecture, 2026-11-26: a wrong match tells the model a history book
    is fiction. Project Gutenberg's LCSH headings, as its records carry them."""
    yes = ["Napoleon I, Emperor of the French, 1769-1821 -- Fiction", "Historical fiction", "Short stories",
           "Love stories", "English poetry", "France -- History -- Revolution, 1789-1799 -- Drama",
           "Fairy tales", "Adventure stories", "Napoleon I, Emperor of the French, 1769-1821 -- Juvenile fiction",
           "Detective and mystery stories", "Science fiction",
           # their qualifiers stripped, whatever follows (architecture, 2026-11-26)
           "Fairy tales -- Germany", "Short stories, American", "Greek drama (Tragedy)",
           "English poetry -- 19th century", "Epic poetry, Greek", "Detective and mystery stories, English",
           "Science fiction, American", "Gothic fiction (Literary genre)", "Sonnets, English",
           "Russian fiction -- Translations into English", "Poetry -- Translations into English"]
    no = ["Romance languages -- Grammar", "Rome -- History -- Empire, 30 B.C.-476 A.D.",
          "Drama -- History and criticism", "English poetry -- History and criticism",
          "Napoleon I, Emperor of the French, 1769-1821", "France -- History -- 1789-1815",
          "Fiction -- Technique", "Romanticism", "Universe", "Bible stories", "Bible stories, English"]
    assert [s for s in yes if not retrieval.literary(s)] == []
    assert [s for s in no if retrieval.literary(s)] == []
    _, user = _sources({**BARE, "work_subjects": "France -- History -- 1789-1815 | Historical fiction"})
    assert "a literary work" in user, "subjects as PG's flat string too"


def test_the_answers_sources_and_its_pdf_show_the_first_publication():
    got = api.source_entries([HISTORY, BARE])
    assert got[0]["first_published"] == 1855 and got[0]["first_published_by"] == "PG's automatic summary"
    assert got[1]["first_published"] is None
    doc = ask_pdf.AskPdf(question="q", answer="Morì nel 1821 [1].",
                         references=[ask_pdf.PdfReference(n=1, title="History of Napoleon",
                                                          first_published="1855 · by PG's automatic summary")])
    html = ask_pdf.build_html(doc, base_url="https://ask.example.org", node="n",
                              when=datetime(2026, 11, 26, tzinfo=timezone.utc))
    assert "first published 1855 · by PG&#39;s automatic summary" in html or "first published 1855 · by PG's automatic summary" in html
    assert ask_pdf._year_said(HISTORY) == "1855 · by PG's automatic summary" and ask_pdf._year_said(BARE) is None


def test_gutenbergs_category_shelves_and_class_say_fiction_fact_or_opinion():
    """Operator, 2026-11-26: "Collections like gutenberg carry the category
    tag to determine what's fiction, what's a fact and what's an opinion"."""
    novel = retrieval._work_said({"subjects": "Napoleon I, Emperor of the French, 1769-1821 | PQ",
                                  "bookshelves": "Category: Novels | Category: Historical Fiction | Best Books Ever"})
    assert novel.startswith(" — a literary work — Gutenberg category: Novels; Historical Fiction — LCC PQ")
    assert "Best Books" not in novel, "a shelf that is not a category is not said"
    essay = retrieval._work_said({"subjects": "Liberty | JC",
                                  "bookshelves": "Category: Essays, Letters & Speeches | Category: Politics"})
    assert essay.startswith(" — a work of opinion or belief — ")
    history = retrieval._work_said({"subjects": "France -- History -- 1789-1815 | DC",
                                    "bookshelves": "Category: History - European"})
    assert "a literary work" not in history and "opinion" not in history
    assert "Gutenberg category: History - European — LCC DC" in history
    assert retrieval._work_said({"subjects": "Ethics | BJ"}).startswith(" — a work of opinion or belief")
    assert "opinion" not in retrieval._work_said({"subjects": "Psychology | BF"}), "BF is psychology"
    assert retrieval._work_said({"subjects": "Animals -- Juvenile fiction | PZ"}).startswith(" — a literary work")
    assert "a literary work" not in retrieval._work_said({"subjects": "Shakespeare -- Criticism | PR"}), \
        "PA-PT hold criticism too: named, never read as fiction"
    system, _ = _sources(BARE)
    assert "what it states is its author's view" in system


def test_the_search_brings_each_hits_subjects_and_shelves_from_its_record(tmp_path):
    """The hit had only title, authors and language: the record's own
    classification reached nothing (2026-11-26)."""
    import sqlite3
    from types import SimpleNamespace
    con = sqlite3.connect(tmp_path / "m.db")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE documents (source TEXT, native_id TEXT, title TEXT, authors TEXT, lang TEXT, "
                "subjects TEXT, bookshelves TEXT, state TEXT)")
    con.execute("INSERT INTO documents VALUES ('gutenberg', '42', 'T', 'A', 'it', 'Poetry | PQ', "
                "'Category: Poetry', 'embedded')")
    hits = [{"book_id": 42, "chunk_id": "gutenberg:42:0:1"}]
    try:
        retrieval._attach_metadata_and_enqueue(con, SimpleNamespace(plugin="gutenberg", codex=None,
                                                                    embed_mode="off", name="gutenberg"), hits)
    except Exception:   # noqa: BLE001 — what follows the attach needs a whole collection; the attach is done first
        pass
    assert hits[0]["subjects"] == "Poetry | PQ" and hits[0]["bookshelves"] == "Category: Poetry"


def test_a_collection_whose_subjects_are_not_a_librarys_headings_gets_none(tmp_path):
    """A Wikipedia article's categories say what it is about ("Italian
    poetry", "1890s novels"): read as a genre they would call it literature."""
    import sqlite3
    from types import SimpleNamespace
    con = sqlite3.connect(tmp_path / "w.db")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE documents (source TEXT, native_id TEXT, title TEXT, authors TEXT, lang TEXT, "
                "subjects TEXT, bookshelves TEXT)")
    con.execute("INSERT INTO documents VALUES ('wikipedia', '7', 'Giacomo Leopardi', '', 'it', "
                "'Italian poetry | 1830s novels', '')")
    hits = [{"book_id": 7, "chunk_id": "wikipedia:7:0:1"}]
    try:
        retrieval._attach_metadata_and_enqueue(con, SimpleNamespace(plugin="wikipedia", codex=None,
                                                                    embed_mode="off", name="wikipedia"), hits)
    except Exception:   # noqa: BLE001 — the attach is done first
        pass
    assert "subjects" not in hits[0] and retrieval._work_said(hits[0]) == ""


def test_through_the_search_a_novels_record_says_so_in_the_answers_header(isolated_config, monkeypatch, tmp_path):
    """Architecture, 2026-11-26: the classification must reach the answer on
    the live path, not only when a test hands it in."""
    import shutil
    from pathlib import Path
    from app import collections as collections_mod, db, discovery
    corpus = tmp_path / "corpus_w"
    shutil.copytree(Path(__file__).parent / "fixtures" / "gutenberg-mini" / "corpus", corpus)
    c = collections_mod.Collection(name="gutenberg", plugin="gutenberg", profile=None, embed_mode_default=None,
                                   corpus_dir=corpus, manifest_path=tmp_path / "m" / "manifest.db",
                                   fts_dir=tmp_path / "m" / "fts", lance_dir=tmp_path / "m" / "lancedb")
    con = db.connect(c)
    discovery.sync_collection(con, c)
    con.execute("UPDATE documents SET title='Le Rouge', authors='Stendhal', subjects=?, bookshelves=? "
                "WHERE native_id='10003'",
                ("France -- History -- 1815-1830 -- Fiction | PQ", "Category: Novels"))
    con.commit()
    monkeypatch.setattr(retrieval.refs, "get_store", lambda: None)
    monkeypatch.setattr(retrieval.refs.RESOLVER, "resolve", lambda q, s: [])
    hit = {"book_id": 10003, "chunk_id": "gutenberg:10003:0:1", "uid": "gutenberg:10003", "text": "Julien…",
           "score": 1.0, "rrf": 0.03, "source_collection": "gutenberg"}
    monkeypatch.setattr(retrieval, "_search_one_collection",
                        lambda con, c, q, k, mode, tr, ids, plan=None, depth=0: {
                            "catalog": [], "merged": [dict(hit)], "translations": {},
                            "timings": {"catalog": 0.0}, "scope": {}})
    monkeypatch.setattr(retrieval, "_rerank_list", lambda q, hits, recipe, budget_s, depth: (hits, False))
    out = retrieval.search(con, "Julien Sorel", k=5, collection=c, translate=False)
    assert out["hits"], out
    _, user = retrieval.build_answer_messages("Julien Sorel", out)[0:2]
    head = user["content"].split("[1]")[1].split("\n")[0]
    assert "a literary work — Gutenberg category: Novels — LCC PQ — subjects: France -- History -- 1815-1830 -- Fiction" \
        in head, head
