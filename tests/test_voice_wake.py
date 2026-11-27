"""Hands-free, the node's side (docs/notes/hands-free-voice.md step 1;
operator, 2026-11-27: 1A, 2A, 3): the name on the text, the commands,
continuation, the echo guard, and the privacy and activity rules of
POST /ask/voice/heard."""
from __future__ import annotations

import io
import json
import wave

import pytest
from fastapi.testclient import TestClient

from app import transcribe, voice_wake
from app.voice_wake import heard


@pytest.mark.parametrize("text", ["Philippe, chi era Garibaldi?", "philip chi era Garibaldi?",
                                  "Filippo: chi era Garibaldi?", "Felipe chi era Garibaldi",
                                  "Hey Philippe, chi era Garibaldi?", "PHILIPPÉ chi era Garibaldi?"])
def test_the_name_in_its_written_forms_addresses_the_question(text):
    h = heard(text)
    assert h.addressed and h.command is None and h.text.startswith("chi era Garibaldi")


@pytest.mark.parametrize("text,cmd", [
    ("Philippe, wait", "wait"), ("Philippe hold on", "wait"), ("Philippe aspetta!", "wait"),
    ("Philippe, attends.", "wait"), ("Philippe un momento", "wait"),
    ("Philippe, go on", "resume"), ("Philippe continua", "resume"), ("Philippe vas-y", "resume"),
    ("Philippe, stop!", "stop"), ("Philippe basta", "stop"), ("Philippe arrête", "stop"),
    ("Philippe", "listen"), ("Philippe...", "listen"),
])
def test_the_commands_after_the_name(text, cmd):
    h = heard(text)
    assert h.addressed and h.command == cmd and h.text is None


def test_what_is_not_addressed_is_dropped_with_no_text():
    for t in ("chi era Garibaldi?", "the radio says the traffic is heavy", "Garibaldi Philippe"):
        h = heard(t)
        assert not h.addressed and h.text is None and h.dropped == "not addressed"
    assert heard("").dropped == "nothing said"


def test_continuation_takes_an_utterance_without_the_name():
    assert heard("e suo fratello?", follow=True).text == "e suo fratello?"
    assert heard("stop", follow=True).command == "stop"


def test_the_node_s_own_voice_is_dropped_whatever_it_starts_with():
    said = "Philippe Garibaldi was born in Nice in 1807. He led the expedition of the Thousand."
    assert heard("He led the expedition of the Thousand", said=said).dropped == "echo"
    assert heard("Philippe Garibaldi was born in Nice", said=said).dropped == "echo"
    assert heard("Philippe, stop", said=said).command == "stop", "a short command is never an echo"
    assert heard("Philippe, what about his brother?", said=said).text == "what about his brother?"


def test_another_name_set_by_the_node():
    assert heard("Louise, chi era Garibaldi?", name="Louise").text == "chi era Garibaldi?"
    assert not heard("Philippe, chi era Garibaldi?", name="Louise").addressed


# ---- the route --------------------------------------------------------------------------

def _wav(seconds=1.0) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x01\x00" * int(16000 * seconds))
    return b.getvalue()


@pytest.fixture
def route(isolated_config, monkeypatch):
    from app.api import app
    from app.rage import dispatch
    said = {"text": "", "status": 200, "claims": 0}

    def fake(audio, filename, ctype, fields, url=None):
        return transcribe.Answer(said["status"], json.dumps({"text": said["text"]}).encode(), "application/json",
                                 "trimmed: 0.9 s of speech kept of 1.0 s", (("vad", 30.0), ("stt", 410.0)))
    monkeypatch.setattr(transcribe, "transcribe", fake)
    real = dispatch.question

    def counted(*a, **kw):
        said["claims"] += 1
        return real(*a, **kw)
    monkeypatch.setattr(dispatch, "question", counted)
    from tests.test_web_pages import _patch_hash
    _patch_hash(monkeypatch)
    return TestClient(app), said


def _post(c, **fields):
    return c.post("/ask/voice/heard", files={"file": ("u.wav", _wav(), "audio/wav")}, data=fields)


def test_the_route_answers_addressed_and_keeps_the_question_open(route, capsys):
    c, said = route
    said["text"] = "Philippe, chi era Garibaldi?"
    got = _post(c, language="it").json()
    assert got == {"addressed": True, "command": None, "text": "chi era Garibaldi?"}
    assert said["claims"] == 1
    line = [ln for ln in capsys.readouterr().out.splitlines() if "[voice] heard" in ln][-1]
    assert "1.0 s, vad trimmed, stt 0.41 s: addressed (question)" in line


def test_an_unaddressed_utterance_is_never_returned_logged_or_activity(route, capsys):
    c, said = route
    said["text"] = "the secret code is 4471 and my bank is Fineco"
    got = _post(c).json()
    assert got == {"addressed": False, "command": None, "text": None, "dropped": "not addressed"}
    assert said["claims"] == 0, "an open microphone is not activity"
    out = capsys.readouterr()
    assert "4471" not in out.out + out.err and "Fineco" not in out.out + out.err
    assert "dropped (not addressed)" in out.out


def test_a_failed_transcription_is_logged_by_its_length_and_outcome_only(route, capsys):
    c, said = route
    said["status"], said["text"] = 502, "Fineco"
    r = _post(c)
    assert r.status_code == 502
    out = capsys.readouterr()
    assert "[voice] heard 1.0 s" in out.out and "failed (HTTP 502)" in out.out and "Fineco" not in out.out + out.err


def test_the_echo_and_the_continuation_reach_the_route(route):
    c, said = route
    said["text"] = "He led the expedition of the Thousand"
    assert _post(c, said="He led the expedition of the Thousand.").json()["dropped"] == "echo"
    said["text"] = "e suo fratello?"
    assert _post(c, follow="1").json()["text"] == "e suo fratello?"


def test_the_name_is_the_node_s_and_the_command_line_sets_it(isolated_config):
    from app import louise, platform_config
    from app.platform_config import SPEECH_KEYS
    assert "wake_name" in SPEECH_KEYS and platform_config.SpeechConfig().wake_name is None
    a = louise.build_parser().parse_args(["node", "speech", "set", "--name", "Louise"])
    assert a.wake_name == "Louise"
    assert voice_wake.forms_of(None) == voice_wake.FORMS["philippe"]
