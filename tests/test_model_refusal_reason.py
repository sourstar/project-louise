"""A model server's refusal carries its reason (2026-11-21, the Bug session:
an Ask question answered "400 Bad Request" and nothing else). The chat calls
said why since 2026-11-16 (app/llm.py `refuse_on_error`); the reranker, the
translator and the encoder now do too (app/httpc.py `raise_for_status`),
as httpx errors still, so every handler of one handles them."""
from __future__ import annotations

import httpx
import pytest

from app import httpc, llm


def _answer(status: int, body: str, *, stream: bool = False) -> httpx.Response:
    req = httpx.Request("POST", "http://models:8080/v1/completions")
    if stream:
        return httpx.Response(status, request=req, stream=httpx.ByteStream(body.encode()))
    return httpx.Response(status, request=req, text=body)


def test_a_refusal_says_the_servers_reason_and_stays_an_httpx_error():
    r = _answer(400, '{"error":{"code":400,"message":"unknown field \\"keep_context_ms\\"","type":"invalid"}}')
    with pytest.raises(httpx.HTTPStatusError) as e:
        httpc.raise_for_status(r)
    assert isinstance(e.value, httpc.ModelRefused) and e.value.response.status_code == 400
    assert "unknown field" in str(e.value) and str(e.value).startswith("400 from http://models:8080/v1/completions")


def test_a_streamed_body_is_read_and_a_long_one_cut():
    r = _answer(500, "x " * 1000, stream=True)
    with pytest.raises(httpc.ModelRefused) as e:
        httpc.raise_for_status(r)
    assert len(str(e.value)) < httpc.ERROR_BODY_CHARS + 80


def test_an_empty_body_says_the_status_phrase_and_a_success_passes():
    with pytest.raises(httpc.ModelRefused, match="Bad Request"):
        httpc.raise_for_status(_answer(400, ""))
    httpc.raise_for_status(_answer(200, "{}"))


def test_the_chat_refusal_is_the_same_refusal():
    assert issubclass(llm.ChatRefused, httpc.ModelRefused)
    with pytest.raises(llm.ContextExceeded) as e:
        llm.refuse_on_error(_answer(400, "request (17013 tokens) exceeds the available context size (16384 tokens)"))
    assert (e.value.requested, e.value.available) == (17013, 16384)


def test_the_ask_paths_model_calls_say_why(monkeypatch):
    """The translator: what the reader is shown is str(e) (app/ask/api.py),
    now with the reason."""
    from app import config, translate
    monkeypatch.setattr(config, "TRANSLATE_URL", "http://models:8080")
    monkeypatch.setattr(httpx, "post", lambda url, **k: _answer(400, "model louise-translate-madlad not loaded"))
    with pytest.raises(httpx.HTTPStatusError, match="louise-translate-madlad not loaded"):
        translate._completion({"a": 1}, timeout=1)
