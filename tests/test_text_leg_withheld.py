"""A text index that cannot be searched is SAID (the operator, 2026-11-23:
"A"): named in the search's `legs_withheld` (leg "text", 05 §5.3 amended),
logged once with its collection, shown on the Ask page and in the answer's
sources. Until then it answered an empty list: a copy mounted read-only,
whose lock tantivy takes to read, gave no hits to every question, and a
measurement read it as a fast search (München)."""
from __future__ import annotations

import pytest

from app import fts as fts_mod
from app import language_plan, retrieval
from tests.test_catalog_index import col  # noqa: F401 — the fixture

LOCKED = ('Failed to acquire Lockfile: IoError(Os { code: 30, kind: ReadOnlyFilesystem, '
          'message: "Read-only file system" })')


def test_the_text_search_raises_when_asked_and_says_it_once(col, monkeypatch, capsys):  # noqa: F811
    c, _con = col
    fts_mod._logged_failures.clear()

    def locked(group, collection=None):
        raise ValueError(LOCKED)
    monkeypatch.setattr(fts_mod, "get_index", locked)
    capsys.readouterr()
    for _ in range(3):
        assert fts_mod.search("en", "moby", collection=c) == [], "a caller that looks a record up: [] as before"
    with pytest.raises(fts_mod.TextLegError, match="the text index en could not be searched.*Read-only"):
        fts_mod.search("en", "moby", collection=c, raise_errors=True)
    said = [line for line in capsys.readouterr().err.splitlines() if "warn:text" in line]
    assert len(said) == 1 and f"[{c.name} warn:text]" in said[0], "logged once, naming the collection"


def test_a_text_index_that_fails_is_named_withheld_and_the_others_answer(col, monkeypatch):  # noqa: F811
    c, con = col
    fts_mod._logged_failures.clear()
    hit = {"score": 1.0, "book_id": 1, "chunk_id": "g:1:0", "chapter": "", "lang": "en",
           "text": "Moby", "source": "fts:en"}

    def records(group, q, limit, collection=None, langs=None, raise_errors=False, **restrict):
        if group == "de":
            raise fts_mod.TextLegError(f"the text index de could not be searched: ValueError: {LOCKED}")
        return [dict(hit)]
    monkeypatch.setattr(fts_mod, "search_records", records)
    monkeypatch.setattr(fts_mod, "existing_groups", lambda c: ["en", "de"])
    plan = language_plan.plan("Moby", [c], False)
    out = retrieval._search_one_collection(con, c, "Moby", 5, "hybrid", False, set(), plan)
    legs = [leg for leg in retrieval._legs_withheld(c, out) if leg["leg"] == "text"]
    assert len(legs) == 1
    why = legs[0]["reason"]
    assert why.startswith("1 of its 2 text index(es) could not be searched; the others answered")
    assert "the text index de" in why and "Read-only" in why


def test_every_text_index_failing_is_said_as_the_whole_leg(col, monkeypatch):  # noqa: F811
    c, con = col

    def records(group, q, limit, **kw):
        raise fts_mod.TextLegError(f"the text index {group} could not be searched: OSError: gone")
    monkeypatch.setattr(fts_mod, "search_records", records)
    monkeypatch.setattr(fts_mod, "existing_groups", lambda c: ["en", "de"])
    plan = language_plan.plan("Moby", [c], False)
    out = retrieval._search_one_collection(con, c, "Moby", 5, "hybrid", False, set(), plan)
    (leg,) = [leg for leg in retrieval._legs_withheld(c, out) if leg["leg"] == "text"]
    assert leg["reason"].startswith("2 of its 2 text index(es) could not be searched: ")
    assert "the others answered" not in leg["reason"]


def test_a_healthy_text_leg_says_nothing(col, monkeypatch):  # noqa: F811
    c, con = col
    monkeypatch.setattr(fts_mod, "search_records", lambda *a, **k: [])
    monkeypatch.setattr(fts_mod, "existing_groups", lambda c: ["en"])
    plan = language_plan.plan("Moby", [c], False)
    out = retrieval._search_one_collection(con, c, "Moby", 5, "hybrid", False, set(), plan)
    assert out.get("text_leg") is None
    assert not [leg for leg in retrieval._legs_withheld(c, out) if leg["leg"] == "text"]


@pytest.mark.parametrize("leg,says", [("vector", "Searched on its text only"),
                                      ("text", "Searched without its full-text index"),
                                      ("catalog", "Searched without its catalogue")])
def test_the_answers_sources_say_which_leg_was_missing(leg, says):
    from app.ask import chat
    assert chat.LEG_WITHHELD_SAYS[leg] == says


def test_the_ask_page_shows_every_withheld_leg():
    from app import web
    t = web.TEMPLATES.env.get_template("_ask_results.html")
    html = t.render(result={"hits": [], "withheld": [], "timings": {}, "legs_withheld": [
        {"collection": "wikipedia", "leg": "text", "reason": "1 of its 10 text index(es) could not be searched"},
        {"collection": "pubmed", "leg": "vector", "reason": "the run failed the gate"}]},
        label=lambda n: n)
    text = " ".join(html.split())
    assert "Some collections were searched with a part missing." in text
    assert "wikipedia</span> no full-text search: 1 of its 10 text index(es) could not be searched" in text
    assert "pubmed</span> no meaning search (vectors): the run failed the gate" in text
