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


def test_no_year_decides_it_at_answer_time():
    """b2af94f's year trigger is gone (architecture, 2026-11-16): a record
    with no claim is read by its passage alone."""
    assert not historical.read_sources(_numbered(_hit(1, MODERN, first_published=1353)), "x")[0]
    assert historical.first_published({"first_published": "1590"}) == 1590
    assert historical.first_published({"year": 1353}) is None


def _fact(stage, method):
    return {"stage": stage, "method": method, "collection_id": "sha256:" + "0" * 64,
            "snapshot_id": "2026-11", "claim": "annotations:1", "confidence": "0.8"}


def test_a_record_with_a_claim_is_read_by_its_claim():
    # the claim reads a modern-looking passage as Old Italian, and names its method
    shown, reads = historical.read_sources(_numbered(
        _hit(1, MODERN, language_stage=_fact("wikidata:Q135913388", "human")),
        _hit(2, MODERN, language_stage=_fact("wikidata:Q135913388", "matcher")),
        _hit(3, DANTE, language_stage=_fact(None, "human"))), "x")
    assert sorted(shown) == [1, 2]       # a person's "not an older stage" skips the passage check
    assert "«Old Italian»" in shown[1][0]
    assert reads[0].badge() == "Old Italian, by hand"
    assert reads[1].badge() == "Old Italian, by its text"
    shown, reads = historical.read_sources(
        _numbered(_hit(1, SEICENTO, language_stage=_fact("wikidata:Q135913388", "human"))), "x")
    assert reads[0].badge() == "Old Italian, by hand, spelling modernised for the model"


def test_a_modern_version_in_the_same_language_is_pointed_at():
    hits = _numbered(
        _hit(1, DANTE, language_stage=_fact("wikidata:Q135913388", "matcher"),
             editions=[{"uid": "gutenberg:2", "lang": "it", "held": True},
                       {"uid": "gutenberg:3", "lang": "en", "held": True}]),
        _hit(2, MODERN), _hit(3, "Midway upon the journey of our life", lang="en"))
    shown, reads = historical.read_sources(hits, "x")
    assert "a modern version of it is source [2]" in shown[1][0]
    assert reads[0].badge().endswith("read with its modern version [2]")


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
    assert ("reading", [{"n": 1, "reading": "older Italian, by this passage, spelling modernised for the model"}]) in parts
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


# --------------------------------------------------------------------------
# the claim (09 §9.5, amended 2026-11-16) and its matcher

def _claim(obj="wikidata:Q135913388", kind="matcher", subject="gutenberg:1"):
    method = dict(historical.MATCHER) if kind == "matcher" else {"kind": kind}
    return {"type": "language-stage", "subject": subject, "object": obj, "method": method,
            "evidence": {"why": "t"}, "confidence": "0.8", "contributor": "c"}


def test_a_claim_from_each_method_is_valid_and_a_label_is_not():
    from app.hold import annotations as ann
    for c in (_claim(), _claim("iso639-3:enm", "human"), _claim(None, "human")):
        assert ann.check_claim(c, target_plugin="gutenberg")["type"] == "language-stage"
    for bad, why in ((_claim("older Italian"), "canonical id"), (_claim("lang:it"), "never by a label"),
                     (_claim(None), "only in a human claim"), (_claim("iso639-3:ENM"), "ISO 639-3"),
                     ({**_claim(), "subject_lang": "it", "object_lang": "it"}, "unknown")):
        with pytest.raises(ann.AnnotationsError, match=why):
            ann.check_claim(bad, target_plugin="gutenberg")
    model = {**_claim(), "method": {"kind": "model", "id": "m", "version": 1, "model": "sha256:" + "a" * 64,
                                    "prompt": "sha256:" + "b" * 64, "temperature": "0"}}
    for other in (model, {**_claim(), "method": {"kind": "wikidata"}}):
        with pytest.raises(ann.AnnotationsError, match="matcher, human"):    # the corpus alone
            ann.check_claim(other, target_plugin="gutenberg")


def test_a_person_and_the_matcher_coexist_and_the_person_wins():
    from app.hold import annotations as ann
    from app.ask import annotations as ask_ann
    m, h = _claim(), _claim(None, "human")
    assert ann.identity_key(m) != ann.identity_key(h)
    assert ann.identity_key(m) == ann.identity_key({**m, "object": "iso639-3:enm"})
    idx = ann.ClaimIndex("2026-11", [ann.Claim("annotations:1", m), ann.Claim("annotations:2", h)])
    assert [c.uid for c in idx.stages["gutenberg:1"]] == ["annotations:1", "annotations:2"]
    assert not idx.graph and not idx.by_target

    class A:
        class codex:
            collection_id = "sha256:" + "1" * 64
    hit: dict = {"uid": "gutenberg:1"}
    ask_ann._attach_stage(hit, A, "2026-11", idx.stages["gutenberg:1"])
    assert hit["language_stage"]["stage"] is None and hit["language_stage"]["method"] == "human"
    hit = {"uid": "gutenberg:1"}
    ask_ann._attach_stage(hit, A, "2026-11", idx.stages["gutenberg:1"][:1])
    assert hit["language_stage"]["method"] == "matcher"
    ask_ann._attach_stage(hit, A, "2026-11", [ann.Claim("annotations:3", _claim(None, "human"))])
    assert hit["language_stage"]["method"] == "human"           # a person over the matcher


def test_the_claim_is_pinned_to_its_record_alone_and_makes_its_edge():
    from app.hold import annotations as ann
    from app.hold import codex as cx
    held = {"gutenberg:1": {"content_hash": "a" * 32}}
    subs = [ann.Submitted(1, b"x", ann.check_claim(_claim(), target_plugin="gutenberg")),
            ann.Submitted(2, b"y", ann.check_claim(_claim(None, "human"), target_plugin="gutenberg"))]
    pinned = ann.pin(subs, held, "2026-11")
    assert [(c["subject_hash"], c["object_hash"], c["target_snapshot"]) for c in pinned] == \
        [("a" * 32, None, "2026-11")] * 2
    assert ann.state_of(pinned[0], {"gutenberg:1": "a" * 32}) == ann.CURRENT
    assert ann.state_of(pinned[0], {"gutenberg:1": "b" * 32}) == ann.STALE
    recs = ann.records_of(pinned, subs, version=cx.GRAPH_VERSION)
    edges = b"".join(ann.edge_lines(recs, "2026-11"))
    assert edges.count(b"annotations:language-stage") == 1      # "not an older stage" makes none
    with pytest.raises(ann.AnnotationsError, match="not\\s+hold"):
        ann.pin([ann.Submitted(1, b"x", _claim(subject="gutenberg:9"))], held, "2026-11")


def _record(uid, text, lang="it", **extra):
    return {"uid": uid, "lang": lang, "sections": [["", text]], "creators": [], "extra": extra}


def test_the_matcher_claims_an_old_italian_work_and_never_a_translation():
    from app.hold import annotations as ann
    old = _record("gutenberg:1", " ".join([DANTE] * 3))
    c = historical.stage_claim(old, "louise")
    assert c["object"] == "wikidata:Q135913388" and c["method"] == historical.MATCHER
    assert c["evidence"]["first_published"] is None and c["confidence"] == historical.CONFIDENCE
    ann.check_claim(c, target_plugin="gutenberg")
    assert historical.stage_claim(_record("gutenberg:2", MODERN * 20), "louise") is None
    assert historical.stage_claim({**old, "extra": {"creators_detail": [{"name": "X", "role": "trl"}]}},
                                  "louise") is None
    assert historical.stage_claim({**old, "lang": "en"}, "louise") is None
    # a work first published after the stage's end is not claimed; before it, the bar is lower
    later = _record("gutenberg:3", " ".join([DANTE] * 3), marc={"520": ["… first published in 1605."]})
    assert historical.stage_claim(later, "louise") is None
    sparse = _record("gutenberg:4", " ".join([MODERN] * 300) + " " + DANTE, marc={"520": ["published in 1353"]})
    got = historical.stage_claim(sparse, "louise")
    assert got is not None and got["evidence"]["first_published"] == 1353
    assert historical.stage_claim({**sparse, "extra": {}}, "louise") is None
    # the Renaissance printing alone is not Old Italian
    assert historical.stage_claim(_record("gutenberg:5", " ".join([SEICENTO] * 10)), "louise") is None


def test_the_merge_keeps_every_other_claim_and_replaces_the_matchers():
    import json
    from tools import annotations_language_stage as tool
    work = json.dumps({"type": "same-work", "subject": "gutenberg:1", "object": "gutenberg:2",
                       "subject_lang": "it", "object_lang": "en"}).encode()
    human = json.dumps(_claim(None, "human", "gutenberg:7")).encode()
    old = json.dumps(_claim(subject="gutenberg:8")).encode()
    lines, summary = tool.merge([work, human, old], [_claim(subject="gutenberg:9")])
    assert lines[:2] == [work, human] and len(lines) == 3
    assert summary == {"kept": 2, "replaced": 1, "language_stage": 1, "claims": 3}
    # a same-work of ONE language needs a stage on one end
    one = json.dumps({"type": "same-work", "subject": "gutenberg:3", "object": "gutenberg:4",
                      "subject_lang": "it", "object_lang": "it"}).encode()
    with pytest.raises(SystemExit, match="one language"):
        tool.merge([one], [])
    assert len(tool.merge([one], [_claim(subject="gutenberg:3")])[0]) == 2
