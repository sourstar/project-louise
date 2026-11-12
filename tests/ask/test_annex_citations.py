"""An article cited IN AN ANNEX, or named by a word (2026-11-12, after
München's acceptance of the amendments by annex): "art. XI dell'allegato B
del r.d. 9 novembre 1861, n. 327" found the act and dropped the article and
the annex — the scan's `_ART` takes only "art. N" right before the act —
and "articolo unico", "art. XI" were read as no article at all."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from app import db, validity
from app.ask import articles
from app.plugins.normattiva import source as nsrc
from app.refs import normattiva as nref
from app.refs.normattiva import label_token

RD = "urn:nir:stato:regio.decreto:1861-11-09;327"


def _one(query: str):
    got = nref.article_citations(query)
    assert len(got) == 1, got
    return got[0]


@pytest.mark.parametrize("query, article, annex, label", [
    ("art. XI dell'allegato B del regio decreto 9 novembre 1861, n. 327", "xi", "allegato B",
     "art. XI dell'allegato B"),
    ("art. 3 dell'allegato B del regio decreto 9 novembre 1861, n. 327", "3", "allegato B",
     "art. 3 dell'allegato B"),
    ("art. 3 dell’allegato B al regio decreto 9 novembre 1861, n. 327", "3", "allegato B",
     "art. 3 dell'allegato B"),
    ("art. 3, comma 2, allegato 1, al regio decreto 9 novembre 1861, n. 327", "3", "allegato 1",
     "art. 3 dell'allegato 1"),
    ("articolo unico del regio decreto 9 novembre 1861, n. 327", "unico", "", "art. unico"),
    ("articolo transitorio del regio decreto 9 novembre 1861, n. 327", "transitorio", "", "art. transitorio"),
    # unchanged: the article right before the act
    ("art. 5 del regio decreto 9 novembre 1861, n. 327", "5", "", "art. 5"),
])
def test_the_article_and_the_annex_before_an_act_are_read(query, article, annex, label):
    c = _one(query)
    assert (c.article, c.annex, c.label, c.keys[0]) == (article, annex, label, RD)


@pytest.mark.parametrize("query", [
    "mestieri e arti del regio decreto 9 novembre 1861, n. 327",     # "arti" is a word, not "art. I"
    "il regio decreto 9 novembre 1861, n. 327",
])
def test_no_article_is_invented(query):
    assert nref.article_citations(query) == []


def test_the_scan_itself_is_unchanged():
    """The text index's citations keep `art` None where `_ART` did not take
    it: the look-back is the article pin's alone."""
    got = nref.citations("art. XI dell'allegato B del regio decreto 9 novembre 1861, n. 327")
    assert [c["art"] for c in got] == [None]


def test_a_short_citation_takes_the_resolved_act_by_year_and_number():
    fact = {"kind": "atto", "entity_id": "urn:nir:stato:decreto.legislativo:2010-07-02;104",
            "label": "Codice del processo amministrativo"}
    other = {"kind": "atto", "entity_id": "urn:nir:stato:legge:2010-07-02;105", "label": "x"}
    got = nref.article_citations("art. 3 dell'allegato 1 al d.lgs. 104/2010", facts=[fact, other])
    assert [(c.article, c.annex, c.also) for c in got] == [("3", "allegato 1", (fact["entity_id"],))]


@pytest.mark.parametrize("label, token", [
    ("Allegato B-art. XI", "xi"), ("Articolo unico.", "unico"),
    ("Regolamento-articolo transitorio", "transitorio"), ("Art. 5. — modifica all'art. 7", "5"),
    ("Art. 01.", "01"), ("Mestieri e arti", None), ("Capo I", None),
])
def test_a_label_reads_a_word_article_too(label, token):
    assert label_token(label) == token


# ---- the pin: the cited annex resolved against the version's own labels ------------------

CHUNKS = [{"chapter": "Art. 3.", "text": "decreto 3"},
          {"chapter": "ALLEGATO BB-art. 3", "text": "bb 3"},
          {"chapter": "Allegato B - Regolamento-art. 3", "text": "allegato b 3"},
          {"chapter": "Allegato B - Regolamento-art. XI", "text": "allegato b xi"}]


def _want(article: str, annex: str):
    return SimpleNamespace(read=label_token, article=article,
                           citation=SimpleNamespace(annexes=(), annex=annex))


@pytest.fixture
def doc(monkeypatch):
    from app import reader
    monkeypatch.setattr(reader, "document", lambda c, n: {"chunks": CHUNKS})


@pytest.mark.parametrize("article, annex, text, annexes", [
    ("3", "allegato B", "allegato b 3", ("Allegato B - Regolamento",)),
    ("xi", "allegato b", "allegato b xi", ("Allegato B - Regolamento",)),
    ("3", "allegato BB", "bb 3", ("ALLEGATO BB",)),
    ("3", "", "decreto 3", ()),
])
def test_the_cited_annex_is_the_held_one_its_words_begin(doc, article, annex, text, annexes):
    ch, _held, got = articles._read_article(None, 1, _want(article, annex))
    assert ch["text"] == text and got == annexes


def test_an_annex_the_version_does_not_hold_pins_nothing_never_the_decrees_article(doc):
    ch, _held, got = articles._read_article(None, 1, _want("3", "allegato C"))
    assert ch is None and got == ("allegato C",)


def test_names_annex():
    assert articles.names_annex("ALLEGATO B", "allegato B")
    assert articles.names_annex("Allegato B) Regolamento", "allegato b")
    assert not articles.names_annex("Allegato BB", "allegato B")
    assert not articles.names_annex(None, "allegato B") and not articles.names_annex("Allegato B", "")


def test_the_annex_articles_changes_are_its_own():
    """The amendments index's `<annex>|xi` (AMENDMENTS_VERSION 3) meets the
    citation once its annex is resolved to the held name."""
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    doc = SimpleNamespace(extra={"vigenza_from": "2020-01-01", "active_modifications": [
        {"destination": f"{RD}#Allegato B - Regolamento-art. XI", "type": "substitution"},
        {"destination": f"{RD}#Allegato B - Regolamento-art. 3", "type": "repeal"},
        {"destination": f"{RD}#3", "type": "insertion"}]}, keys=[])
    validity._record_amendments(con, "normattiva:1", validity.amendments_of_doc(doc, nsrc.NormattivaSource()))
    con.execute("INSERT INTO validity_works(key, work) VALUES(?,?)", (RD, "rd-work"))
    got = articles._changes(con, "rd-work", "xi", ("Allegato B - Regolamento",))
    assert [(r["su"], r["tipo"]) for r in got] == [("art. xi", "substitution")]
    held = {None, "Allegato B - Regolamento"}
    three = articles._changes(con, "rd-work", "3", ("Allegato B - Regolamento",), held)
    assert sorted(r["tipo"] for r in three) == ["insertion", "repeal"], \
        "its own repeal, and the bare #3 the source does not place (marked)"
