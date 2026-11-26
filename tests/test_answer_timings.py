"""WHERE AN ANSWER'S SECONDS BEFORE ITS FIRST WORD WENT (the Bug session,
2026-11-26: "answer: 1017 chars in 9.9 s, first word at 5.2 s" — and nothing
said why). `llm.chat_stream` counts the chat model's load, the gate, the
request to its first token, and reads the server's own count of the prompt
and the answer from whichever form its last line uses."""
from __future__ import annotations

import json

import httpx

from app import config, llm, model_load


def test_each_servers_own_count_is_read_in_one_vocabulary():
    # llama.cpp's server: `timings` on its last chunk
    assert llm._server_said({"timings": {"prompt_n": 3412, "prompt_ms": 4610.5, "predicted_n": 220,
                                         "predicted_ms": 4700.0}}) == \
        {"prompt_tokens": 3412, "prompt_ms": 4610.5, "out_tokens": 220, "out_ms": 4700.0}
    # LocalAI: `usage`, its times with the Extra-Usage header
    assert llm._server_said({"usage": {"prompt_tokens": 3412, "completion_tokens": 220,
                                       "timing_prompt_processing": 4610.5, "timing_token_generation": 4700.0}}) == \
        {"prompt_tokens": 3412, "prompt_ms": 4610.5, "out_tokens": 220, "out_ms": 4700.0}
    # Ollama's last line, in nanoseconds
    assert llm._server_said({"done": True, "prompt_eval_count": 10, "prompt_eval_duration": 2_000_000,
                             "eval_count": 5, "eval_duration": 3_000_000}) == \
        {"prompt_tokens": 10, "prompt_ms": 2.0, "out_tokens": 5, "out_ms": 3.0}
    assert llm._server_said({"choices": [{"delta": {"content": "x"}}]}) is None
    assert llm._server_said({"usage": {"prompt_tokens": True}}) is None, "a bool is no count"


class _Stream:
    def __init__(self, lines):
        self.lines = lines
        self.status_code = 200
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        yield from self.lines

    def read(self):
        return b""


def test_the_stream_says_where_its_time_went(monkeypatch):
    sent = {}
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": "Ciao"}}]}),
             "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 3412, "completion_tokens": 2,
                                              "timing_prompt_processing": 4610.5,
                                              "timing_token_generation": 40.0}}),
             "data: [DONE]"]

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            sent["headers"] = headers
            return _Stream(lines)
    monkeypatch.setattr(config, "CHAT_API", "openai")
    monkeypatch.setattr(config, "CHAT_URL", "http://localai:8080")
    monkeypatch.setattr(llm, "warm", lambda level=None: None)
    monkeypatch.setattr(model_load, "speaks_localai", lambda server: True)
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(llm, "refuse_on_error", lambda r: None)
    it = llm.chat_stream([{"role": "user", "content": "x"}])
    got = []
    try:
        while True:
            got.append(next(it))
    except StopIteration as s:
        end = s.value
    assert got == ["Ciao"] and end == llm.StreamEnd(finish_reason="stop", done=True, error=None)
    assert sent["headers"]["Extra-Usage"] == "true", "LocalAI gives its times only when asked"
    t = end.timings
    assert t["prompt_tokens"] == 3412 and t["prompt_ms"] == 4610.5 and t["first_s"] is not None
    said = llm.timings_said(t)
    assert said.startswith("before the request ") and "the model's load " in said and "the gate " in said
    assert "first token " in said and "prompt 3,412 tokens in 4.6 s" in said and "2 tokens written in 0.0 s" in said


def test_a_server_that_counts_nothing_is_said_so():
    said = llm.timings_said({"load_s": 30.5, "gate_s": 0.1, "before_s": 30.6, "first_s": 1.2})
    assert said == ("before the request 30.6 s (the model's load 30.5, the gate 0.1); first token 1.2 s after it; "
                    "the server counted nothing")
    assert llm.timings_said(None) == ""
