"""The answer fits the chat model's context (München, 2026-11-16: "Scandali nei
diari di Sanuto" asked 17,455 tokens of a 16,384 context, and the reader saw a
bare 400). Two defects: the prompt was not sized before the call, and the
error carried the status without the server's reason. Now the prompt is sized
by its estimate (`api._fit_answer`), asked again smaller by the server's own
count when it is still over (`api._refit`), and every chat error says why
(`llm.refuse_on_error`). The sources the reader is shown stay all of them;
each says whether the answer read it, and so does the PDF."""
from __future__ import annotations

import inspect

import httpx
import pytest

from app import api, ask_pdf, llm, retrieval

URL = "http://chat.invalid/v1/chat/completions"
SANUTO = {"error": {"code": 400, "type": "exceed_context_size_error",
                    "message": "request (17455 tokens) exceeds the available context size "
                               "(16384 tokens)"}}


def _hit(n: int, chars: int = 1800) -> dict:
    return {"chunk_id": f"gutenberg:{n}:0:1", "book_id": n, "uid": f"gutenberg:{n}",
            "text": (f"passage {n} " * 400)[:chars], "title": f"T{n}", "authors": "A",
            "lang": "it"}


def _result(n: int = 10, chars: int = 1800) -> dict:
    return {"hits": [_hit(i, chars) for i in range(1, n + 1)], "facts": [], "catalog": [],
            "timings": {"total": 1.0}}


def _exceeded(requested: int = 17455, available: int = 16384) -> llm.ContextExceeded:
    body = {"error": {"code": 400, "message": f"request ({requested} tokens) exceeds the "
                                              f"available context size ({available} tokens)"}}
    r = httpx.Response(400, request=httpx.Request("POST", URL), json=body)
    with pytest.raises(llm.ContextExceeded) as got:
        llm.refuse_on_error(r)
    return got.value


def _body(**kw) -> api.ChatBody:
    kw.setdefault("messages", [{"role": "user", "content": "Scandali nei diari di Sanuto"}])
    return api.ChatBody(speed=True, **kw)


@pytest.fixture
def quiet(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(api.db, "log_event",
                        lambda con, lvl, stage, msg, *a, **k: logged.append(msg))
    monkeypatch.setattr(api.db, "log_query", lambda *a, **k: None)
    monkeypatch.setattr(ask_pdf, "keep", lambda *a, **k: "pdf1")
    return logged


# --- defect 2: the server's reason is carried ---------------------------------

def test_a_refused_request_says_the_servers_reason():
    r = httpx.Response(400, request=httpx.Request("POST", URL), json=SANUTO)
    with pytest.raises(llm.ContextExceeded) as got:
        llm.refuse_on_error(r)
    e = got.value
    assert (e.requested, e.available) == (17455, 16384)
    assert "400" in str(e) and "exceeds the available context size" in str(e)
    assert isinstance(e, httpx.HTTPStatusError)     # callers catching the old error still do


def test_a_streamed_refusal_is_read_before_it_is_raised():
    def answer(request):
        return httpx.Response(400, json=SANUTO)
    with httpx.Client(transport=httpx.MockTransport(answer)) as client, \
            client.stream("POST", URL, json={}) as r, pytest.raises(llm.ContextExceeded) as got:
        llm.refuse_on_error(r)
    assert got.value.requested == 17455 and "exceeds" in str(got.value)


def test_another_refusal_carries_at_most_its_bound_of_the_body():
    r = httpx.Response(500, request=httpx.Request("POST", URL), text="model failed: " + "x" * 5000)
    with pytest.raises(llm.ChatRefused) as got:
        llm.refuse_on_error(r)
    assert not isinstance(got.value, llm.ContextExceeded)
    said = str(got.value).split(": ", 1)[1]
    assert said.startswith("model failed:") and len(said) == llm.ERROR_BODY_CHARS
    llm.refuse_on_error(httpx.Response(200, request=httpx.Request("POST", URL), json={}))


def test_no_chat_call_raises_a_bare_status():
    for fn in (llm.chat, llm.chat_tools, llm.chat_stream):
        src = inspect.getsource(fn)
        assert "raise_for_status" not in src and "refuse_on_error(r)" in src


def test_the_mcp_loop_sizes_with_the_same_numbers():
    from app.ask import chat
    assert (chat.CONTEXT_TOKENS, chat.ANSWER_TOKENS, chat.MARGIN_TOKENS, chat.CHARS_PER_TOKEN) == \
        (llm.CONTEXT_TOKENS, llm.ANSWER_TOKENS, llm.MARGIN_TOKENS, llm.CHARS_PER_TOKEN)


# --- defect 1: the prompt is sized before the call -----------------------------

def test_a_prompt_that_fits_is_left_whole():
    p = api._fit_answer(_body(), "q", _result(10))
    assert p.used == 10 and p.history == 0 and p.source_chars == retrieval.ANSWER_SOURCE_CHARS


def test_a_long_conversation_gives_up_passages_then_turns():
    turns = []
    for i in range(6):
        turns.append({"role": "user" if i % 2 == 0 else "assistant", "content": "w " * 4000})
    body = _body(messages=turns + [{"role": "user", "content": "q"}])
    p = api._fit_answer(body, "q", _result(10))
    budget = llm.CONTEXT_TOKENS - llm.ANSWER_TOKENS - llm.MARGIN_TOKENS
    assert api._prompt_tokens(p.messages) <= budget
    assert p.used == 1 and 0 < p.history < 6
    assert p.messages[0]["role"] == "system" and "[1] gutenberg:1" in p.messages[-1]["content"]
    assert "[2] gutenberg:2" not in p.messages[-1]["content"]


def test_the_servers_count_shrinks_the_prompt_at_its_own_rate():
    result = _result(10)
    p = api._fit_answer(_body(), "q", result)
    smaller = api._refit(_body(), "q", result, p, _exceeded())
    assert smaller is not None and 1 <= smaller.used < p.used
    # chars this prompt spends per token, by the server's count: the cut, with
    # its 15 % margin, takes the request under the room left for the answer
    rate = len(api.json.dumps(p.messages, ensure_ascii=False)) / 17455
    room = 16384 - llm.ANSWER_TOKENS - llm.MARGIN_TOKENS
    assert len(api.json.dumps(smaller.messages, ensure_ascii=False)) / rate <= room
    one = api._fit_answer(_body(), "q", _result(1))
    assert api._refit(_body(), "q", _result(1), one, _exceeded()) is None


def test_a_budget_reaches_the_older_language_reader():
    from app import historical
    result = _result(10)
    _, reads = historical.prepare(result, "q", budget=1800)
    assert all(r.n == 1 for r in reads)
    assert retrieval.answer_hits_shown(result["hits"], 1800) == 1
    assert retrieval.answer_hits_shown(result["hits"], 5400) == 3
    user = retrieval.build_answer_messages("q", result, source_chars=3600)[1]["content"]
    assert "[2]" in user and "[3]" not in user


# --- the stream: asked again, before any word ---------------------------------

def test_the_stream_is_asked_again_smaller_and_says_what_was_read(monkeypatch, quiet):
    sent: list[list[dict]] = []

    def stream(messages, temperature=0.3):
        sent.append(messages)
        if len(sent) == 1:
            raise _exceeded()
        yield "Risposta [1]."
        return llm.StreamEnd(finish_reason="stop", done=True, error=None)
    monkeypatch.setattr(llm, "chat_stream", stream)
    parts = list(api.answer_parts(_body(stream=True, sources=True), "q", _result(10), 0.0,
                                  con=object()))
    reads = [v for k, v in parts if k == "read"]
    assert reads[0] == 10 and 1 <= reads[-1] < 10 and len(sent) == 2
    assert "".join(v for k, v in parts if k == "delta") == "Risposta [1]."
    last = [v for k, v in parts if k == "sources"][-1]
    assert [e["read"] for e in last] == [i <= reads[-1] for i in range(1, 11)]
    assert not any(k == "cut" for k, _ in parts)
    assert any("over the chat model's context (17,455 of 16,384 tokens)" in m for m in quiet)
    # the sources turn sent the second time holds fewer passages
    n = reads[-1] + 1
    assert f"[{n}] gutenberg:{n}" in sent[0][-1]["content"]
    assert f"[{n}] gutenberg:{n}" not in sent[1][-1]["content"]


def test_a_stream_still_over_after_its_tries_says_why(monkeypatch, quiet):
    def stream(messages, temperature=0.3):
        raise _exceeded()
        yield ""                                        # a generator, as the real one
    monkeypatch.setattr(llm, "chat_stream", stream)
    parts = list(api.answer_parts(_body(stream=True), "q", _result(10), 0.0, con=object()))
    cut = [v for k, v in parts if k == "cut"]
    assert cut and "exceeds the available context size" in cut[0]


def test_the_relay_passes_the_count_to_the_page():
    from app import web
    src = inspect.getsource(web._relay_answer)
    assert 'kind == "read"' in src and 'job.emit("read", count=value)' in src


# --- the non-stream answer ------------------------------------------------------

def test_the_whole_answer_is_asked_again_smaller(monkeypatch, quiet):
    sent: list[list[dict]] = []

    def chat(messages, temperature=0.3):
        sent.append(messages)
        if len(sent) == 1:
            raise _exceeded()
        return "Risposta [1]."
    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(api, "_con", lambda: object())
    out = api._chat_answer(_body(stream=False, sources=True), "q", _result(10), 0.0)
    assert out["choices"][0]["message"]["content"] == "Risposta [1]." and len(sent) == 2
    flags = [e["read"] for e in out["louise_sources"]]
    assert flags[0] is True and flags[-1] is False and len(flags) == 10


def test_without_a_count_the_sources_say_nothing_of_it():
    hits = _result(2)["hits"]
    assert "read" not in api.source_entries(hits)[0]
    assert [e["read"] for e in api.source_entries(hits, used=1)] == [True, False]


# --- the PDF says it too --------------------------------------------------------

def test_the_pdf_says_which_sources_the_answer_did_not_read():
    entries = api.source_entries(_result(2)["hits"], used=1)
    refs = [ask_pdf._reference(i, e, {}) for i, e in enumerate(entries, 1)]
    assert [r.read for r in refs] == [True, False]
    doc = ask_pdf.AskPdf(question="q", answer="A [1].", references=refs)
    html = ask_pdf.build_html(doc, base_url="https://ask.example.org", node="n",
                              when=ask_pdf.reader_time(None, None))
    assert html.count("not read by the answer") == 1 and "cited" in html
    unsaid = ask_pdf.AskPdf(question="q", answer="A.", references=[ask_pdf.PdfReference(n=1)])
    assert "not read by the answer" not in ask_pdf.build_html(
        unsaid, base_url="https://ask.example.org", node="n", when=ask_pdf.reader_time(None, None))


def test_the_page_marks_and_sends_what_the_answer_read():
    from pathlib import Path
    page = (Path(api.__file__).parent / "templates" / "ask.html").read_text()
    assert "es.addEventListener('read'" in page and "read:readCount" in page
    assert "read:readOf(i)" in page and "not read by the answer" in page
