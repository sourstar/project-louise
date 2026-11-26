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
            "subjects": "Napoleon I, Emperor of the French, 1769-1821 -- Poetry"}
# "by": where the year is not the record's own plain statement — today only
# PG's automatic summary (app/ask/works.py attach_first_published); never an
# annotation's claim, which no code sets as first_published
HISTORY = {"chunk_id": "gutenberg:7:0:1", "book_id": 7, "title": "History of Napoleon", "authors": "Abbott",
           "lang": "en", "text": "He died in 1821.", "first_published": 1855,
           "first_published_by": "PG's automatic summary",
           "subjects": "Napoleon I, Emperor of the French, 1769-1821"}
BARE = {"chunk_id": "gutenberg:9:0:1", "book_id": 9, "title": "Untitled", "authors": "?", "lang": "en",
        "text": "Some text."}


def _sources(*hits) -> str:
    m = retrieval.build_answer_messages("Quando morì Napoleone?", {"hits": list(hits), "catalog": [], "facts": []})
    return m[0]["content"], m[1]["content"]


def test_each_source_says_its_first_publication_and_whether_it_is_a_literary_work():
    system, user = _sources(NAPOLEON, HISTORY, BARE)
    assert "[1] gutenberg:42 — Il cinque maggio — Manzoni (it) — first published 1821 — a literary work " \
           "(by its subject headings) — " \
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
          "Fiction -- Technique", "Romanticism", "Universe"]
    assert [s for s in yes if not retrieval.literary(s)] == []
    assert [s for s in no if retrieval.literary(s)] == []
    _, user = _sources({**BARE, "subjects": "France -- History -- 1789-1815 | Historical fiction"})
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
    assert novel.startswith(" — a literary work (by its Gutenberg category) — Gutenberg category: Novels; "
                            "Historical Fiction — LCC PQ")
    assert "Best Books" not in novel, "a shelf that is not a category is not said"
    essay = retrieval._work_said({"subjects": "Liberty | JC",
                                  "bookshelves": "Category: Essays, Letters & Speeches | Category: Politics"})
    assert essay.startswith(" — a work of opinion or belief (by its Gutenberg category) — ")
    history = retrieval._work_said({"subjects": "France -- History -- 1789-1815 | DC",
                                    "bookshelves": "Category: History - European"})
    assert "a literary work" not in history and "opinion" not in history
    assert "Gutenberg category: History - European — LCC DC" in history
    assert "opinion" not in retrieval._work_said({"subjects": "Etiquette | BJ"}), "a class is a topic, not a stance"
    assert retrieval._work_said({"subjects": "Animals -- Juvenile fiction | PZ"}).startswith(" — a literary work")
    assert "a literary work" not in retrieval._work_said({"subjects": "Shakespeare -- Criticism | PR"}), \
        "PA-PT hold criticism too: named, never read as fiction"
    assert "LCC E300" in retrieval._work_said({"subjects": "United States -- History | E300"}), "an LCC with digits"


#: THE REAL RECORDS (tests/fixtures/gutenberg-real, copied byte for byte from
#: the node's mirror, 2026-11-26): each one's verdict, as its own RDF gives
#: its subjects and shelves to the parser (app/plugins/gutenberg/rdf.py).
#: None: no verdict — a fact, or scripture.
REAL = {
    5: None, 7: None, 14: None, 89: None, 566: None, 686: None, 732: None,          # law, treaties, history, reference
    674: None, 1170: None, 656: None, 3176: None, 4763: None,                     # lives, history, travel, logic
    10: None, 30: None, 17: None, 216: None, 2017: None,                           # scripture: never framed
    151: "a literary work", 201: "a literary work", 2600: "a literary work",       # poems, Flatland, a novel
    1874: "a literary work", 119: "a literary work", 1194: "a literary work",      # its own headings say fiction
    1962: "a work of opinion or belief", 5428: "a work of opinion or belief",      # the Defences of poetry
    1974: "a work of opinion or belief", 7148: "a work of opinion or belief",      # Poetics, Schlegel's Lectures
    3623: None,                                     # The Golden Bough: anthropology; Religion is a topic shelf
    44027: None,      # The Expositor's Bible: The Psalms — "Bible. Psalms -- Commentaries", a Poetry shelf
    30252: None,      # Ezekiel's visitors — "-- Criticism, interpretation, etc." outranks Science-Fiction & Fantasy
    # Cheyne's Job and Solomon: a study (its criticism headings), on a Philosophy & Ethics shelf — opinion by
    # that shelf, exactly as the probe's Bible criticism on a Philosophy shelf (#45475; architecture, 2026-11-26)
    65866: "a work of opinion or belief",
}


def _real(n: int) -> dict:
    from pathlib import Path
    from app.plugins.gutenberg import rdf
    m = rdf.parse(Path(__file__).parent / "fixtures" / "gutenberg-real" / str(n), n)
    return {"subjects": m["subjects"], "bookshelves": m["bookshelves"]}


import pytest


@pytest.mark.parametrize("n", sorted(REAL))
def test_a_real_records_verdict(n):
    """Architecture's read, 2026-11-26: on these, topic shelves (Adventure,
    Humour, Mythology, History - Religious, Religion, Politics) had called
    history, biography, travel, law and scripture literature or opinion, and
    scripture written as a subdivision ("Taoism -- Sacred books") was framed."""
    said = retrieval._work_said(_real(n))
    got = ("a literary work" if " — a literary work (" in said else
           "a work of opinion or belief" if " — a work of opinion or belief (" in said else None)
    assert got == REAL[n], said[:240]


def test_all_31_real_records_are_judged():
    assert len(REAL) == 31


def test_a_verdict_says_its_basis():
    assert "a literary work (by its subject headings)" in retrieval._work_said(_real(2600))
    assert "a literary work (by its Gutenberg category)" in retrieval._work_said(_real(201)), \
        "Flatland: its shelves are all that says so"
    assert "a work of opinion or belief (by its Gutenberg category)" in retrieval._work_said(_real(1974))
    assert "LCC F001" in retrieval._work_said(_real(7)), "an LCC with digits is read as one"


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


def test_through_the_real_stages_and_search_each_record_says_what_it_is(isolated_config, monkeypatch, tmp_path):
    """Architecture, 2026-11-26: the classification must reach the answer on
    the live path. Real records (War and Peace, the US Constitution) through
    the real discovery and RDF stage into the manifest, a real
    `retrieval.search` over them, and the answer's prompt: no subject is
    handed in."""
    import shutil
    from pathlib import Path
    from app import collections as collections_mod, db, discovery, worker
    corpus = tmp_path / "corpus_real"
    for n in (2600, 5):
        d = corpus / str(n)
        shutil.copytree(Path(__file__).parent / "fixtures" / "gutenberg-real" / str(n), d)
        (d / f"pg{n}.txt.utf8").write_text("A text of the book.\n", encoding="utf-8")
    c = collections_mod.Collection(name="gutenberg", plugin="gutenberg", profile=None, embed_mode_default=None,
                                   corpus_dir=corpus, manifest_path=tmp_path / "m" / "manifest.db",
                                   fts_dir=tmp_path / "m" / "fts", lance_dir=tmp_path / "m" / "lancedb")
    con = db.connect(c)
    discovery.sync_collection(con, c)
    for row in con.execute("SELECT * FROM documents ORDER BY uid").fetchall():
        worker.stage_rdf(con, row, c)
    monkeypatch.setattr(retrieval.refs, "get_store", lambda: None)
    monkeypatch.setattr(retrieval.refs.RESOLVER, "resolve", lambda q, s: [])
    hits = [{"book_id": n, "chunk_id": f"gutenberg:{n}:0:1", "uid": f"gutenberg:{n}", "text": "…",
             "score": 1.0, "rrf": 0.03, "source_collection": "gutenberg"} for n in (2600, 5)]
    monkeypatch.setattr(retrieval, "_search_one_collection",
                        lambda con, c, q, k, mode, tr, ids, plan=None, depth=0: {
                            "catalog": [], "merged": [dict(h) for h in hits], "translations": {},
                            "timings": {"catalog": 0.0}, "scope": {}})
    monkeypatch.setattr(retrieval, "_rerank_list", lambda q, hs, recipe, budget_s, depth: (hs, False))
    out = retrieval.search(con, "Napoleon", k=5, collection=c, translate=False)
    _, user = retrieval.build_answer_messages("Napoleon", out)[0:2]
    heads = {h.split(" — ")[0].split(":")[1]: h for h in
             (line for line in user["content"].splitlines() if line.startswith("["))}
    novel, law = heads.get("2600", ""), heads.get("5", "")
    assert "a literary work (by its subject headings)" in novel and "Gutenberg category: Historical Novels" in novel
    assert "a literary work" not in law and "opinion" not in law and "LCC JK, KF" in law


# ---- architecture's last read, 2026-11-26: the verdict on every path ---------

WAR_AND_PEACE = {"chunk_id": "gutenberg:2600:0:1", "book_id": 2600, "uid": "gutenberg:2600", "title": "War and Peace",
                 "authors": "Tolstoy", "lang": "en", "text": "Napoleon…",
                 "subjects": "Napoleonic Wars, 1800-1815 -- Campaigns -- Russia -- Fiction | Historical fiction | PG",
                 "bookshelves": "Category: Historical Novels | Category: Novels"}


def test_the_reader_sees_the_verdict_beside_the_source_as_the_model_was_told_it():
    hit = {**WAR_AND_PEACE, "work": retrieval.work_verdict(WAR_AND_PEACE)}
    assert hit["work"] == {"kind": "a literary work", "basis": "by its subject headings"}
    assert api.source_entries([hit])[0]["work"] == hit["work"]
    from app import web
    html = web.TEMPLATES.env.get_template("_ask_results.html").render(
        result={"hits": [hit], "catalog": [], "facts": [], "timings": {}})
    assert 'data-work' in html and "a literary work · by its subject headings" in html
    assert ask_pdf._work_shown(hit) == "a literary work · by its subject headings"
    doc = ask_pdf.AskPdf(question="q", answer="[1]", references=[ask_pdf.PdfReference(
        n=1, title="War and Peace", work="a literary work · by its subject headings")])
    pdf = ask_pdf.build_html(doc, base_url="https://ask.example.org", node="n",
                             when=datetime(2026, 11, 26, tzinfo=timezone.utc))
    assert "a literary work · by its subject headings" in pdf


def test_the_other_answer_path_frames_a_source_alike(monkeypatch):
    """/ask/chat, MCP and the PDF built from a question: the same header and
    the same rule as /v1/chat/completions and the Ask tab — run, not read."""
    from types import SimpleNamespace
    from app.ask import chat, mcp, results
    hit = {**WAR_AND_PEACE, "work": retrieval.work_verdict(WAR_AND_PEACE)}
    ctx = {"collection_id": "c", "lineage_id": "l", "snapshot_id": "s", "trust_level": "local",
           "certification": None, "figures": [], "red": False}
    out = results.result_of(SimpleNamespace(plugin="gutenberg"), ctx, hit, 1, own_key=None, reranked=True)
    assert out["work"] == {"kind": "a literary work", "basis": "by its subject headings"}
    assert "a literary work (by its subject headings)" in out["work_said"]
    shown = chat.shown_search("Napoleon", {"lists": [{"hits": [out]}]}, chat.Sources(), translated=False)
    assert "a literary work (by its subject headings)" in shown
    seen = {}

    def model(messages, tools=None, **kw):
        seen["system"] = messages[0]["content"]
        return {"role": "assistant", "content": "Nel romanzo [1]."}
    from app import config
    monkeypatch.setattr(config, "CHAT_URL", "http://chat")
    try:
        chat.ask("Napoleone?", model=model, call=lambda name, args: {"lists": [{"hits": [out]}]}, speed=True)
    except Exception:   # noqa: BLE001 — the system prompt is built before any reply is read
        pass
    assert retrieval.WORK_RULE in seen.get("system", ""), "the rule goes to the model on this path too"
    assert "work_said" in mcp.TOOLS[0]["description"], "an MCP client is told how to read it"
