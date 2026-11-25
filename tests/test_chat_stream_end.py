"""The streamed answer's END is said (2026-10-13: "the answer was cut", and
nothing had recorded how it ended). `llm.chat_stream` returns a StreamEnd —
the endpoint's finish_reason, whether its end marker came, an error it
carried — and the answer prompt carries Qwen3's no-think switch, measured on
the node that day (app/retrieval.py THINK_OFF)."""
import json

import httpx

from app import config, llm, retrieval


def _stream_client(monkeypatch, body: bytes, status: int = 200) -> None:
    def handler(request):
        return httpx.Response(status, content=body,
                              headers={"content-type": "text/event-stream"})
    real = httpx.Client
    monkeypatch.setattr(llm.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(llm, "warm", lambda level=None: None)


def _openai(monkeypatch) -> None:
    monkeypatch.setattr(config, "CHAT_API", "openai")
    monkeypatch.setattr(config, "CHAT_URL", "http://chat.test")
    monkeypatch.setattr(config, "CHAT_MODEL", "m")


def _sse(*objs: dict, done: bool = True) -> bytes:
    out = b"".join(f"data: {json.dumps(o)}\n\n".encode() for o in objs)
    return out + (b"data: [DONE]\n\n" if done else b"")


def _run() -> tuple[list[str], llm.StreamEnd]:
    ends: list[llm.StreamEnd] = []

    def gen():
        ends.append((yield from llm.chat_stream([{"role": "user", "content": "q"}])))
    pieces = list(gen())
    return pieces, ends[0]


def test_a_whole_openai_stream_ends_with_stop(monkeypatch):
    _openai(monkeypatch)
    _stream_client(monkeypatch, _sse(
        {"choices": [{"delta": {"content": "Il "}, "finish_reason": None}]},
        {"choices": [{"delta": {"reasoning_content": "hidden"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "diritto"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]}))
    pieces, end = _run()
    assert pieces == ["Il ", "diritto"], "reasoning is never shown"
    assert end == llm.StreamEnd(finish_reason="stop", done=True, error=None)
    assert end.whole and end.why() == "whole"


def test_a_capped_stream_says_so(monkeypatch):
    _openai(monkeypatch)
    _stream_client(monkeypatch, _sse(
        {"choices": [{"delta": {"content": "Il diritto"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]}))
    _, end = _run()
    assert end.finish_reason == "length" and end.done and not end.whole
    assert "token limit" in end.why()


def test_a_stream_closed_without_its_marker_is_not_whole(monkeypatch):
    _openai(monkeypatch)
    _stream_client(monkeypatch, _sse(
        {"choices": [{"delta": {"content": "Il diritto all'"}, "finish_reason": None}]},
        done=False))
    pieces, end = _run()
    assert pieces == ["Il diritto all'"]
    assert end == llm.StreamEnd(finish_reason=None, done=False, error=None)
    assert not end.whole and "closed before" in end.why()


def test_an_error_payload_in_the_stream_is_carried(monkeypatch):
    _openai(monkeypatch)
    _stream_client(monkeypatch, _sse(
        {"choices": [{"delta": {"content": "Il "}, "finish_reason": None}]},
        {"error": {"message": "slot unavailable", "code": 503}}, done=False))
    pieces, end = _run()
    assert pieces == ["Il "]
    assert end.error == "slot unavailable" and not end.whole
    assert "slot unavailable" in end.why()


def test_the_ollama_dialect_reads_done_reason(monkeypatch):
    monkeypatch.setattr(config, "CHAT_API", "ollama")
    monkeypatch.setattr(config, "CHAT_URL", "http://chat.test")
    body = (json.dumps({"message": {"content": "Il "}, "done": False}) + "\n"
            + json.dumps({"message": {"content": "diritto"}, "done": True,
                          "done_reason": "length"}) + "\n")
    _stream_client(monkeypatch, body.encode())
    pieces, end = _run()
    assert pieces == ["Il ", "diritto"]
    assert end == llm.StreamEnd(finish_reason="length", done=True, error=None)
    assert not end.whole


def test_delta_still_answers_the_old_pair(monkeypatch):
    _openai(monkeypatch)
    assert llm._delta('data: {"choices": [{"delta": {"content": "x"}}]}') == ("x", False)
    assert llm._delta("data: [DONE]") == ("", True)
    assert llm._delta(": keepalive") == ("", False)


def test_the_answer_prompt_carries_the_no_think_switch():
    result = {"hits": [], "facts": [], "catalog": []}
    for kw in ({}, {"answer_lang": "it"}):
        system = retrieval.build_answer_messages("q", result, **kw)[0]["content"]
        assert system.endswith(retrieval.THINK_OFF), system[-60:]
    assert "ISO code 'it'" in retrieval.build_answer_messages("q", result, answer_lang="it")[0]["content"]


def test_the_citations_are_asked_last_and_by_example():
    """With the switch on the model stopped citing (0 markers in Italian,
    2026-10-13) and the page's [n] links went with it; a reminder written as
    "[n]" was copied literally ("[n4]"). The question is followed by the
    reminder with a concrete example of the shape the page links."""
    result = {"hits": [{"chunk_id": "normattiva:1:0:1", "book_id": 1, "text": "t", "title": "T",
                        "authors": "", "lang": "it"}], "facts": [], "catalog": []}
    user = retrieval.build_answer_messages("Diritto all'oblio", result, answer_lang="it")[1]["content"]
    assert "Question: Diritto all'oblio\n\n" in user
    assert user.endswith(retrieval.CITE_REMINDER)
    assert "like [2] or [2][5]" in retrieval.CITE_REMINDER and "[n]" not in retrieval.CITE_REMINDER
    system = retrieval.build_answer_messages("q", result)[0]["content"]
    assert system.endswith(retrieval.THINK_OFF)


# ---- a failure the server reports in mid-stream, then its ordinary end ------
#
# LocalAI (core/http/endpoints/openai/chat.go, read by the engine session,
# 2026-11-25): once chunks have gone out, a failure is `data: {"error":
# {"message", "type": "server_error", "code": "server_error"}}` and THEN
# `data: [DONE]`. A client that waits only for [DONE] sees a finished answer.

LOCALAI_ERROR = {"error": {"message": "no_room_on_device: m: the card has no room",
                           "type": "server_error", "code": "server_error"}}


def test_an_error_followed_by_the_end_marker_is_not_a_whole_answer(monkeypatch):
    _openai(monkeypatch)
    _stream_client(monkeypatch, _sse(
        {"choices": [{"delta": {"content": "Il diritto "}, "finish_reason": None}]},
        LOCALAI_ERROR, done=True))
    pieces, end = _run()
    assert pieces == ["Il diritto "]
    assert end.error and "no_room_on_device" in end.error and not end.whole
    assert "the endpoint reported an error" in end.why()


def _translated(text: str = "x" * 500) -> tuple[list[str], BaseException | None]:
    pieces: list[str] = []
    try:
        for piece in llm.translate_stream(text, "it"):
            pieces.append(piece)
    except llm.StreamCut as e:
        return pieces, e
    return pieces, None


def test_a_translation_the_server_cut_raises_after_its_words(monkeypatch):
    """`translate_stream` read the stream's end and dropped it: a cut passage
    came back as a finished translation."""
    _openai(monkeypatch)
    _stream_client(monkeypatch, _sse(
        {"choices": [{"delta": {"content": "La balena "}, "finish_reason": None}]},
        LOCALAI_ERROR, done=True))
    pieces, cut = _translated()
    assert pieces == ["La balena "], "the words written are still relayed as they come"
    assert isinstance(cut, llm.StreamCut) and "no_room_on_device" in str(cut)


def test_a_capped_translation_raises(monkeypatch):
    _openai(monkeypatch)
    _stream_client(monkeypatch, _sse(
        {"choices": [{"delta": {"content": "La "}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]}))
    assert "token limit" in str(_translated()[1])


def test_a_translation_whose_stream_closed_early_raises(monkeypatch):
    _openai(monkeypatch)
    _stream_client(monkeypatch, _sse(
        {"choices": [{"delta": {"content": "La "}, "finish_reason": None}]}, done=False))
    assert "closed before" in str(_translated()[1])


def test_a_whole_translation_does_not_raise(monkeypatch):
    _openai(monkeypatch)
    _stream_client(monkeypatch, _sse(
        {"choices": [{"delta": {"content": "La balena."}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]}))
    assert _translated() == (["La balena."], None)


# ---- the answer's language (operator, 2026-11-25) ---------------------------
#
# "I made a search with a query in italian and the answer was in french":
# "Parlami della vita di Dante Alighieri" over gutenberg, the first source a
# French history of Italian literature, the small chat model, no answer
# language chosen on the page.

DANTE = {"hits": [{"chunk_id": "gutenberg:31432:0:1", "book_id": 31432, "lang": "fr", "authors": "Ginguené",
                   "title": "Histoire littéraire d'Italie (1/9)", "text": "Dante naquit à Florence en 1265."},
                  {"chunk_id": "gutenberg:71218:0:1", "book_id": 71218, "lang": "it", "authors": "Dante",
                   "title": "La vita nuova", "text": "In quella parte del libro de la mia memoria."}],
         "facts": [], "catalog": []}
Q = "Parlami della vita di Dante Alighieri"


def test_a_question_with_no_language_chosen_is_answered_in_its_own_language_named(monkeypatch):
    system, user = (m["content"] for m in retrieval.build_answer_messages(Q, {**DANTE, "query_lang": "it"}))
    assert "The question is written in Italian (ISO code 'it'): write your whole answer in that language, " \
           "whatever language the sources are in." in system
    # ...and again AFTER the sources and the question, where a small model still reads it
    tail = user[user.index("Question: " + Q):]
    assert "Write the answer in Italian (ISO code 'it'), not in the language of the sources." in tail
    assert user.index("Histoire littéraire") < user.index("Write the answer in Italian")
    assert user.endswith(retrieval.CITE_REMINDER), "the citation reminder stays last (2026-10-13)"
    assert system.endswith(retrieval.THINK_OFF)


def test_a_language_chosen_on_the_page_wins_over_the_questions_own():
    system, user = (m["content"] for m in retrieval.build_answer_messages(
        Q, {**DANTE, "query_lang": "it"}, answer_lang="en"))
    assert "Write your whole answer in English (ISO code 'en'), whatever language the question or the " \
           "sources are in." in system
    assert "Write the answer in English (ISO code 'en'), not in the language of the sources." in user
    assert "Italian" not in system and "Italian" not in user[user.index("Question: "):]


def test_where_nobody_knows_the_questions_language_the_prompt_still_says_whose_it_is():
    system, user = (m["content"] for m in retrieval.build_answer_messages(Q, {**DANTE, "query_lang": None}))
    assert "answer in the user's language" in system and "ISO code" not in system
    tail = user[user.index("Question: " + Q):]
    assert "Write the answer in the language of the question above, not in the language of the sources." in tail
    assert user.endswith(retrieval.CITE_REMINDER)
