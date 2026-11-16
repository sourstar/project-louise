"""Readers for older stages of a language (app/historical.py; operator,
2026-11-16; reviewed by the architecture session the same day): A names
the stage, B modernises the spelling of the copy the model reads, C points
at a translation among the sources — under Quality only, on both answer
paths, and only where the passage, the record's language, the work's first
publication or the question calls for it."""
import pytest

from app import historical, retrieval

DANTE = ("Nel mezzo del cammin di nostra vita mi ritrovai per una selva oscura, ché la diritta "
         "via era smarrita. Ma per trattar del ben ch'i' vi trovai, dirò de l'altre cose ch'i' "
         "v'ho scorte. Io non so ben ridir com' i' v'intrai, che m'avea di paura il cor compunto")
SEICENTO = "Hauendo l'huomo grande ambitione, la natione et la perfettione della questione ſi vede"
MODERN = ("La questione della lingua italiana fu dibattuta a lungo nel Novecento, e gli studiosi "
          "hanno discusso la gestione delle edizioni critiche e la loro diffusione nelle scuole.")

#: modern sentences the review flagged (architecture, 2026-11-16)
MODERN_FLAGGED = [
    "Le dee dell'Olimpo sono descritte all'uopo il volume, anco se fora di luogo.",
    "Pedro de la Cruz e Juan de la Vega firmarono il contratto con Procter & Gamble.",
    "Lo studio di Procter & Gamble et al. conferma i dati, et cetera, et seq.",
]


def _hit(n, text, lang="it", **kw):
    return {"chunk_id": f"gutenberg:{n}:0:1", "book_id": n, "uid": f"gutenberg:{n}", "text": text,
            "title": f"T{n}", "authors": "A", "lang": lang, **kw}


def _numbered(*hits):
    return list(enumerate(hits, 1))


def test_spelling_of_the_period_is_modernised_for_reading():
    text, applied = historical.modernise(historical.ITALIAN, SEICENTO)
    assert text == "Avendo l'uomo grande ambizione, la nazione e la perfezione della questione si vede"
    assert set(applied) == {"ſ → s", "et → e", "-tione → -zione", "h- (huomo → uomo)"}
    assert historical.modernise(historical.ITALIAN, "che hauea detto")[0] == "che avea detto"


def test_modern_words_are_left_alone():
    # -stione is modern (questione, gestione); ha, ho, hanno are modern
    text, applied = historical.modernise(historical.ITALIAN, MODERN + " Lui ha e io ho, loro hanno.")
    assert applied == [] and "questione" in text and "gestione" in text


@pytest.mark.parametrize("sentence", MODERN_FLAGGED)
def test_the_reviews_modern_sentences_are_not_flagged_nor_rewritten(sentence):
    assert historical.markers(historical.ITALIAN, sentence) < historical.ITALIAN.threshold
    assert not historical.read_sources(_numbered(_hit(1, sentence)), "di cosa parla?")[0]
    assert historical.modernise(historical.ITALIAN, sentence)[0] == sentence


def test_older_text_is_found_and_modern_is_not():
    assert historical.markers(historical.ITALIAN, DANTE) >= historical.ITALIAN.threshold
    assert historical.markers(historical.ITALIAN, SEICENTO) >= historical.ITALIAN.threshold
    assert historical.markers(historical.ITALIAN, MODERN) == 0


def test_a_modern_book_quoting_dante_is_read_so_only_where_it_quotes():
    shown, reads = historical.read_sources(
        _numbered(_hit(1, MODERN), _hit(2, MODERN + " Come scrive Dante: " + DANTE)), "chi era Dante?")
    assert list(shown) == [2] and reads[0].why == "text"


def test_the_question_is_a_trigger():
    weak = "Il libro racconta, ch'i' credo, la storia."                    # one marker
    assert not historical.read_sources(_numbered(_hit(1, weak)), "di cosa parla il libro?")[0]
    shown, reads = historical.read_sources(_numbered(_hit(1, weak)), "un romanzo del Trecento")
    assert 1 in shown and reads[0].why == "question"
    assert historical.asks_about_period("poesia del XIV secolo")
    assert historical.asks_about_period("17th-century letters")
    assert not historical.asks_about_period("romanzi del 1950")


def test_a_stage_code_names_its_stage():
    shown, reads = historical.read_sources(_numbered(_hit(1, "Whan that Aprille", lang="enm")), "x")
    assert "«Middle English»" in shown[1][0] and reads[0].why == "language code"


def test_the_first_publication_is_a_trigger_for_the_work_in_its_own_language():
    shown, reads = historical.read_sources(_numbered(
        _hit(1, MODERN, first_published=1353, work_lang="it"),
        _hit(2, MODERN, first_published=1950, work_lang="it"),
        _hit(3, MODERN)), "x")
    assert list(shown) == [1] and reads[0].why == "year"
    assert "first published 1353" in shown[1][0]
    assert historical.first_published({"first_published": "1590"}) == 1590
    assert historical.first_published({"first_published": "c1590"}) is None
    # one field: `year` is not read
    assert historical.first_published({"year": 1353}) is None


def test_a_translation_is_not_read_as_its_works_period():
    """A modern Italian Shakespeare is not 1600s Italian (the review's 3)."""
    for hit in (_hit(1, MODERN, first_published=1600,
                     authors="Shakespeare, William; Rusconi, Carlo (translator)"),
                _hit(1, MODERN, first_published=1600, work_lang="en"),
                _hit(1, MODERN, first_published=1600)):     # its language unknown, no marker
        assert not historical.read_sources(_numbered(hit), "x")[0], hit
    # unknown language: the year lowers the bar to one marker
    shown, reads = historical.read_sources(
        _numbered(_hit(1, MODERN + " ch'i' dico", first_published=1600)), "x")
    assert reads[0].why == "year"


def test_a_translation_among_the_sources_is_pointed_at():
    hits = _numbered(_hit(1, DANTE, editions=[{"uid": "gutenberg:2", "lang": "en", "held": True}]),
                     _hit(2, "Midway upon the journey of our life", lang="en"))
    shown, reads = historical.read_sources(hits, "x", answer_lang="it")
    assert "a translation of it is source [2]" in shown[1][0] and reads[0].translation_in == 2
    assert "read with its translation [2]" in reads[0].badge()


def test_the_system_rule_says_a_quotation_is_modernised():
    assert "its spelling modernised, and a quotation from it says so" in historical.SYSTEM_RULE


def test_the_answer_prompt_reads_older_sources_under_quality_only():
    result = {"hits": [_hit(1, SEICENTO), _hit(2, MODERN)], "facts": [], "catalog": []}
    read_result, reads = historical.prepare(result, "q")
    system, user = (m["content"] for m in retrieval.build_answer_messages("q", read_result))
    assert historical.SYSTEM_RULE in system and system.endswith(retrieval.THINK_OFF)
    assert "«older Italian» · spelling modernised for reading" in user
    assert "Avendo l'uomo" in user and "Hauendo" not in user
    assert [r.n for r in reads] == [1]
    # the hit itself, which the page shows, is untouched
    assert result["hits"][0]["text"] == SEICENTO
    system, user = (m["content"] for m in retrieval.build_answer_messages("q", result))
    assert historical.SYSTEM_RULE not in system and "Hauendo" in user and "older" not in user


def test_speed_turns_it_off_and_the_badge_is_on_the_source():
    from app import api
    body = api.ChatBody(messages=[{"role": "user", "content": "q"}])
    assert body.speed is False                   # Quality, the default
    result = {"hits": [_hit(1, SEICENTO), _hit(2, MODERN)], "facts": [], "catalog": []}
    reads: list = []
    api._answer_messages(body, "q", result, reads)
    entries = api.source_entries(result["hits"], reads)
    assert entries[0]["reading"].startswith("older Italian") and "reading" not in entries[1]
    assert entries[0]["text"] == SEICENTO          # the source shown is the source's own
    reads = []
    api._answer_messages(api.ChatBody(messages=body.messages, speed=True), "q", result, reads)
    assert reads == []


def test_the_answer_carries_no_line_the_model_did_not_write(monkeypatch):
    from app import api, llm

    def stream(messages, temperature=0.3):
        yield "Risposta [1]."
        return llm.StreamEnd(finish_reason="stop", done=True, error=None)
    monkeypatch.setattr(llm, "chat_stream", stream)
    logged = []
    monkeypatch.setattr(api.db, "log_event", lambda con, lvl, stage, msg, *a, **k: logged.append(msg))
    monkeypatch.setattr(api.db, "log_query", lambda *a, **k: None)
    body = api.ChatBody(messages=[{"role": "user", "content": "q"}], stream=True)
    result = {"hits": [_hit(1, SEICENTO)], "facts": [], "catalog": [], "timings": {"total": 1.0}}
    parts = list(api.answer_parts(body, "q", result, 0.0, con=object()))
    assert "".join(v for k, v in parts if k == "delta") == "Risposta [1]."
    assert ("reading", [{"n": 1, "reading": "older Italian, spelling modernised for the model"}]) in parts
    assert any(m.startswith("read as older language: [1]") for m in logged)


def test_the_mcp_loop_reads_older_sources_with_the_same_function():
    from app.ask import chat
    result = {"lists": [{"hits": [_hit(1, SEICENTO), _hit(2, MODERN)]}]}
    sources, reads = chat.Sources(), []
    shown = chat.shown_search("q", result, sources, question="q", reads=reads)
    assert "«older Italian»" in shown and "Avendo l'uomo" in shown and "Hauendo" not in shown
    assert sources.entries[0]["reading"].startswith("older Italian") and "reading" not in sources.entries[1]
    assert sources.passages[1]["text"] == SEICENTO
    # Speed: no reader
    shown = chat.shown_search("q", result, chat.Sources())
    assert "older" not in shown and "Hauendo" in shown


def test_the_older_rule_goes_before_the_language_rule():
    from app.ask import chat
    last = chat.language_rule("it")
    out = chat.with_older_rule("S." + last, last)
    assert out.endswith(last) and historical.SYSTEM_RULE in out
    assert chat.with_older_rule(out, last) == out
