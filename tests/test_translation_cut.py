"""A translation the model server CUT is a miss, never the passage's
translation (2026-11-25).

`llm.translate_stream` dropped how its stream ended, and the Ask page's
route (`POST /ask/translate/stream`) joined whatever had arrived — after an
error in mid-stream, a cap, a closed stream, or an exception under way — and
both SENT it as the item's finished text and KEPT it in the translation
cache, where every later reader of that passage got the cut version with no
request made. "The entries are translated IN FULL" (operator, 2026-09-18):
a passage is whole or shown in its original."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import llm, web
from app.api import app

LONG = "The whale rose beside the ship, and the sea closed over it again. " * 10   # over _STREAM_OVER


@pytest.fixture
def page(isolated_config, monkeypatch):
    monkeypatch.setattr(web, "_translation_cache", type(web._translation_cache)())
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(web.db, "log_event", lambda con, level, source, msg, *a, **k: logged.append((level, msg)))
    return TestClient(app), logged


def _stream(client, **more) -> list[dict]:
    r = client.post("/ask/translate/stream", json={
        "lang": "it", "speed": True, "items": [{"id": "e1", "text": LONG, "src": "en"}], **more})
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def _model(monkeypatch, pieces: list[str], end: BaseException | None) -> list[int]:
    asked: list[int] = []

    def fake(text, lang):
        asked.append(1)
        yield from pieces
        if end is not None:
            raise end
    monkeypatch.setattr(llm, "translate_stream", fake)
    return asked


@pytest.mark.parametrize("end", [
    llm.StreamCut("the endpoint reported an error: no_room_on_device: m: the card has no room"),
    llm.StreamCut("the token limit was reached"),
    llm.StreamCut("the stream closed before its end marker"),
    ConnectionError("the model server went away"),
])
def test_a_cut_translation_is_a_miss_and_is_not_kept(page, monkeypatch, end):
    client, logged = page
    asked = _model(monkeypatch, ["La balena emerse ", "accanto alla nave"], end)
    lines = _stream(client)
    assert [l["delta"] for l in lines if "delta" in l] == ["La balena emerse ", "accanto alla nave"], \
        "the words are relayed as they are written"
    assert {"id": "e1", "missed": True} in lines, lines
    assert not any("text" in l for l in lines), "a cut passage is never sent as the item's finished text"
    assert lines[-1] == {"done": True, "failed": True, "missed": ["e1"]}
    # ...and NOT KEPT: the next reader's request asks the model again
    _stream(client)
    assert len(asked) == 2, "the cut translation was remembered as the passage's"
    level, said = logged[0]
    assert level == "warn" and "1 cut short and shown in the original, not kept" in said and str(end)[:40] in said, said


def test_a_whole_translation_is_sent_and_kept(page, monkeypatch):
    client, logged = page
    asked = _model(monkeypatch, ["La balena emerse ", "accanto alla nave."], None)
    lines = _stream(client)
    assert {"id": "e1", "text": "La balena emerse accanto alla nave."} in lines
    assert lines[-1] == {"done": True, "failed": False, "missed": []}
    again = _stream(client)
    assert len(asked) == 1 and {"id": "e1", "text": "La balena emerse accanto alla nave."} in again, \
        "a whole one is kept: no second request"
    assert logged[0][0] == "info" and "cut short" not in logged[0][1]


def test_a_model_that_fails_before_a_word_is_asked_once(page, monkeypatch):
    """Unchanged: a call that raised before writing a word marks the rest
    missed instead of queueing each behind the same failure."""
    client, _ = page
    asked = _model(monkeypatch, [], llm.StreamCut("the endpoint reported an error: slot unavailable"))
    r = client.post("/ask/translate/stream", json={"lang": "it", "speed": True, "items": [
        {"id": "e1", "text": LONG, "src": "en"}, {"id": "e2", "text": LONG + " Again.", "src": "en"}]})
    lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert len(asked) == 1 and lines[-1]["missed"] == ["e1", "e2"]
