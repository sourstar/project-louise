"""Readers for older stages of a language (app/historical.py; operator,
2026-11-16): A names the stage, B modernises the spelling of the copy the
model reads, C points at a translation among the sources — under Quality
only, and only where the passage, the record's language or the question
calls for it."""
from app import historical, retrieval

DANTE = ("Nel mezzo del cammin di nostra vita mi ritrovai per una selva oscura, ché la diritta "
         "via era smarrita. Ma per trattar del ben ch'i' vi trovai, dirò de l'altre cose ch'i' "
         "v'ho scorte. Io non so ben ridir com' i' v'intrai, che m'avea di paura il cor compunto")
SEICENTO = "Hauendo l'huomo grande ambitione, la natione & la perfettione della questione ſi vede"
MODERN = ("La questione della lingua italiana fu dibattuta a lungo nel Novecento, e gli studiosi "
          "hanno discusso la gestione delle edizioni critiche e la loro diffusione nelle scuole.")


def _hit(n, text, lang="it", **kw):
    return {"chunk_id": f"gutenberg:{n}:0:1", "book_id": n, "uid": f"gutenberg:{n}", "text": text,
            "title": f"T{n}", "authors": "A", "lang": lang, **kw}


def test_spelling_of_the_period_is_modernised_for_reading():
    text, applied = historical.modernise(historical.ITALIAN, SEICENTO)
    assert text == "Avendo l'uomo grande ambizione, la nazione e la perfezione della questione si vede"
    assert set(applied) == {"ſ → s", "&/et → e", "-tione → -zione", "h- (huomo → uomo)"}


def test_modern_words_are_left_alone():
    # -stione is modern (questione, gestione); ha, ho, hanno are modern
    text, applied = historical.modernise(historical.ITALIAN, MODERN + " Lui ha e io ho, loro hanno.")
    assert applied == [] and "questione" in text and "gestione" in text


def test_older_text_is_found_and_modern_is_not():
    assert historical.markers(historical.ITALIAN, DANTE) >= historical.ITALIAN.threshold
    assert historical.markers(historical.ITALIAN, SEICENTO) >= historical.ITALIAN.threshold
    assert historical.markers(historical.ITALIAN, MODERN) == 0


def test_a_modern_book_quoting_dante_is_read_so_only_where_it_quotes():
    shown, reads = historical.read_sources(
        [_hit(1, MODERN), _hit(2, MODERN + " Come scrive Dante: " + DANTE)], "chi era Dante?")
    assert list(shown) == [2] and reads[0].why == "text"


def test_the_question_is_a_trigger():
    one = "Si legge: tant' era pien di sonno, e de la selva, ch'i' non so dire."   # two markers
    weak = "Il libro, de la sua epoca, racconta la storia."                   # one marker
    assert not historical.read_sources([_hit(1, weak)], "di cosa parla il libro?")[0]
    shown, reads = historical.read_sources([_hit(1, weak)], "un romanzo del Trecento")
    assert 1 in shown and reads[0].why == "question"
    assert historical.read_sources([_hit(1, one)], "x")[0]
    assert historical.asks_about_period("poesia del XIV secolo")
    assert historical.asks_about_period("17th-century letters")
    assert not historical.asks_about_period("romanzi del 1950")


def test_a_stage_code_names_its_stage():
    shown, reads = historical.read_sources([_hit(1, "Whan that Aprille", lang="enm")], "x")
    assert "«Middle English»" in shown[1][0] and reads[0].why == "language code"


def test_a_translation_among_the_sources_is_pointed_at():
    hits = [_hit(1, DANTE, editions=[{"uid": "gutenberg:2", "lang": "en", "held": True}]),
            _hit(2, "Midway upon the journey of our life", lang="en")]
    shown, reads = historical.read_sources(hits, "x", answer_lang="it")
    assert "a translation of it is source [2]" in shown[1][0] and reads[0].translation_in == 2
    assert "with its translation [2]" in historical.said(reads)


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
    # Speed: as before
    system, user = (m["content"] for m in retrieval.build_answer_messages("q", result))
    assert historical.SYSTEM_RULE not in system and "Hauendo" in user and "older" not in user


def test_speed_turns_it_off_in_the_chat_body():
    from app import api
    body = api.ChatBody(messages=[{"role": "user", "content": "q"}])
    assert body.speed is False                   # Quality, the default
    result = {"hits": [_hit(1, SEICENTO)], "facts": [], "catalog": []}
    reads: list = []
    api._answer_messages(body, "q", result, reads)
    assert reads
    reads = []
    api._answer_messages(api.ChatBody(messages=body.messages, speed=True), "q", result, reads)
    assert reads == []
