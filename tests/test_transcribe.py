"""Speech to text for the Ask page's microphone (app/transcribe.py; operator,
2026-11-07: voice support in the web GUI): OpenAI's transcription request
relayed with the node's model, a clip in the page's form trimmed to the
speech LocalAI's VAD finds, silence answered empty without the model, any
other audio untouched, the models pinned while a reader speaks."""
from __future__ import annotations

import io
import json
import struct
import time
import wave

import pytest
from fastapi.testclient import TestClient

from app import api, speech, transcribe
from tests.deposit.test_deposit_identity import node  # noqa: F401

RATE = transcribe.RATE
GAP = round(transcribe.GAP_S * RATE)


def pcm(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def clip(samples: list[int], rate: int = RATE, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm(samples))
    return buf.getvalue()


def frames_of(data: bytes) -> tuple[tuple[int, int, int], bytes]:
    with wave.open(io.BytesIO(data), "rb") as w:
        return (w.getnchannels(), w.getsampwidth(), w.getframerate()), w.readframes(w.getnframes())


#: 3 s, every sample telling where it came from
SAMPLES = [(i % 30000) - 15000 for i in range(3 * RATE)]


class R:
    def __init__(self, status=200, content=b"", ctype="application/json", data=None):
        self.status_code, self.content, self.headers, self._data = status, content, {"content-type": ctype}, data

    def json(self):
        if self._data is None:
            raise ValueError("not JSON")
        return self._data


@pytest.fixture
def platform(tmp_path, monkeypatch):
    """`platform.toml` with a `[speech]` table of the given keys."""
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))

    def write(**keys):
        (tmp_path / "platform.toml").write_text(
            "[speech]\n" + "".join(f'{k} = "{v}"\n' for k, v in keys.items()), encoding="utf-8")
    return write


@pytest.fixture
def server(monkeypatch):
    """The model server: `/vad` answers `state["vad"]` (an R, or an exception
    to raise), the transcription `state["stt"]`; every call recorded."""
    calls: list[tuple[str, dict]] = []
    pins: list[tuple] = []
    state = {"vad": R(data={"segments": []}),
             "stt": R(content=b'{"text":" Who wrote the Divine Comedy?"}')}

    def post(url, **kw):
        calls.append((url, kw))
        got = state["vad"] if url.endswith("/vad") else state["stt"]
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(transcribe.httpc, "post", post)
    monkeypatch.setattr(speech.model_pins, "hold",
                        lambda url, model, holder: pins.append(("hold", model, holder)) or True)
    monkeypatch.setattr(speech.model_pins, "release", lambda holder: pins.append(("release", holder)))
    monkeypatch.setattr(speech, "IDLE_S", 0.2)
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(transcribe.model_pins, "node_event", lambda level, text: logged.append((level, text)))
    state["logged"] = logged
    return state, calls, pins


def stt_calls(calls):
    return [kw for url, kw in calls if url.endswith("/v1/audio/transcriptions")]


def vad_calls(calls):
    return [json.loads(kw["content"]) for url, kw in calls if url.endswith("/vad")]


# ---- the sample math -------------------------------------------------------

def test_the_vad_takes_each_sample_over_32768_exactly():
    assert transcribe.floats(pcm([-32768, -1, 0, 1, 16384, 32767])) == \
        [-1.0, -1 / 32768, 0.0, 1 / 32768, 0.5, 32767 / 32768]


def test_segments_in_seconds_become_sample_ranges_sorted_and_merged():
    n = 3 * RATE
    assert transcribe.ranges([{"start": 0.5, "end": 1.0}], n) == [(8000, 16000)]
    # LocalAI's seconds are float32: 0.53 arrives as 0.5299999713897705 and is sample 8480
    assert transcribe.ranges([{"start": 0.5299999713897705, "end": 0.7300000190734863}], n) == [(8480, 11680)]
    assert transcribe.ranges([{"start": 1.5, "end": 2.0}, {"start": 0.25, "end": 0.75},
                              {"start": 0.5, "end": 1.0}], n) == [(4000, 16000), (24000, 32000)]
    # a segment still open when the clip ended: silero-vad-go leaves its end 0
    assert transcribe.ranges([{"start": 2.5, "end": 0}], n) == [(40000, n)]
    assert transcribe.ranges([{"start": 0, "end": 0}], n) == [(0, n)], "speech from first to last sample"
    assert transcribe.ranges([{"start": -1, "end": 9.0}], n) == [(0, n)], "clamped to the clip"
    assert transcribe.ranges([{"start": 4.0, "end": 5.0}, "x", {"start": "?"}], n) == [], \
        "outside the clip, or not a segment: nothing"


def test_the_trimmed_clip_is_the_spans_in_order_a_gap_of_silence_between_two():
    f = pcm(SAMPLES)
    spans = [(8000, 16000), (24000, 32000)]
    got = transcribe.trimmed(f, spans)
    assert got == f[16000:32000] + b"\x00\x00" * GAP + f[48000:64000]
    assert len(got) == 2 * (8000 + GAP + 8000)
    assert transcribe.trimmed(f, [(0, 10)]) == f[:20], "one span: no gap at either end"


def test_only_the_pages_form_is_read_as_samples():
    assert transcribe.pcm16_mono_16k(clip(SAMPLES[:100])) == pcm(SAMPLES[:100])
    assert transcribe.pcm16_mono_16k(clip(SAMPLES[:100], rate=48000)) is None
    assert transcribe.pcm16_mono_16k(clip(SAMPLES[:100], channels=2)) is None
    assert transcribe.pcm16_mono_16k(b"\x1aE\xdf\xa3webm") is None
    assert transcribe.pcm16_mono_16k(b"") is None


# ---- the relay -------------------------------------------------------------

def test_a_clip_is_trimmed_to_its_speech_and_relayed_with_the_nodes_model(platform, server):
    platform(stt_model="whisper-large-v3", vad_model="silero-vad")
    state, calls, _ = server
    state["vad"] = R(data={"segments": [{"start": 0.5, "end": 1.0}, {"start": 1.5, "end": 2.0}]})
    got = transcribe.transcribe(clip(SAMPLES), "question.wav", "audio/wav",
                                [("file", "x"), ("language", "it"), ("response_format", "json")],
                                url="http://ai:8080")
    assert (got.status, got.body) == (200, b'{"text":" Who wrote the Divine Comedy?"}')
    assert got.vad == "trimmed: 1.00 s of speech kept of 3.00 s"
    asked = vad_calls(calls)
    assert len(asked) == 1 and asked[0]["model"] == "silero-vad"
    assert asked[0]["audio"] == [s / 32768 for s in SAMPLES], "every sample, exactly"
    (stt,) = stt_calls(calls)
    assert stt["data"] == {"language": "it", "response_format": "json", "model": "whisper-large-v3"}
    name, data, ctype = stt["files"]["file"]
    assert (name, ctype) == ("speech.wav", "audio/wav")
    fmt, frames = frames_of(data)
    assert fmt == (1, 2, RATE)
    f = pcm(SAMPLES)
    assert frames == f[16000:32000] + b"\x00\x00" * GAP + f[48000:64000]
    assert [u for u, _ in calls] == ["http://ai:8080/v1/vad", "http://ai:8080/v1/audio/transcriptions"]


@pytest.mark.parametrize("fmt, body, ctype", [
    ("json", b'{"text":""}', "application/json"),
    (None, b'{"text":""}', "application/json"),
    ("text", b"", "text/plain; charset=utf-8"),
    ("srt", b"", "text/plain; charset=utf-8"),
    ("vtt", b"WEBVTT\n\n", "text/plain; charset=utf-8"),
])
def test_a_silent_clip_is_answered_empty_and_the_model_never_called(platform, server, fmt, body, ctype):
    platform(stt_model="whisper-large-v3", vad_model="silero-vad")
    state, calls, _ = server
    fields = [("response_format", fmt)] if fmt else []
    got = transcribe.transcribe(clip([0] * RATE), "q.wav", "audio/wav", fields, url="http://ai")
    assert (got.status, got.body, got.content_type) == (200, body, ctype)
    assert got.vad == "silence: no speech in 1.00 s"
    assert stt_calls(calls) == [], "Whisper writes words into silence: it is not asked"


def test_a_silent_clip_in_verbose_json_has_its_duration_and_no_segments(platform, server):
    platform(stt_model="whisper-large-v3", vad_model="silero-vad")
    got = transcribe.transcribe(clip([0] * (RATE // 2)), "q.wav", "audio/wav",
                                [("response_format", "verbose_json"), ("language", "fr")], url="http://ai")
    assert json.loads(got.body) == {"task": "transcribe", "language": "fr", "duration": 0.5,
                                    "text": "", "segments": []}


def test_without_a_vad_model_the_clip_goes_on_untouched(platform, server):
    platform(stt_model="whisper-large-v3")
    _, calls, _ = server
    audio = clip(SAMPLES[:RATE])
    got = transcribe.transcribe(audio, "q.wav", "audio/wav", [], url="http://ai")
    assert got.status == 200 and got.vad == "off" and vad_calls(calls) == []
    (stt,) = stt_calls(calls)
    assert stt["files"]["file"] == ("q.wav", audio, "audio/wav")


def test_other_audio_goes_on_untouched_and_the_vad_is_not_asked(platform, server):
    platform(stt_model="whisper-large-v3", vad_model="silero-vad")
    _, calls, _ = server
    for audio, name, ctype in ((b"\x1aE\xdf\xa3webm-opus", "q.webm", "audio/webm"),
                               (clip(SAMPLES[:4800], rate=48000), "q.wav", "audio/wav")):
        calls.clear()
        got = transcribe.transcribe(audio, name, ctype, [], url="http://ai")
        assert got.vad == "skipped: not a 16-bit 16 kHz mono WAV" and vad_calls(calls) == []
        assert stt_calls(calls)[0]["files"]["file"] == (name, audio, ctype)


@pytest.mark.parametrize("vad, why", [
    (R(status=400, content=b'{"error":"model not found"}'), "HTTP 400"),
    (R(status=500, content=b"boom"), "HTTP 500"),
    (R(status=200, content=b"<html>"), "the answer is not JSON"),
    (R(status=200, data={"segments": "none"}), "the answer has no segments list"),
    (ConnectionError("refused"), "ConnectionError: refused"),
])
def test_a_vad_that_fails_costs_the_trim_not_the_question(platform, server, vad, why):
    platform(stt_model="whisper-large-v3", vad_model="silero-vad")
    state, calls, _ = server
    state["vad"] = vad
    audio = clip(SAMPLES[:RATE])
    got = transcribe.transcribe(audio, "q.wav", "audio/wav", [], url="http://ai")
    assert got.status == 200 and got.vad == f"failed: {why}"
    assert got.body == b'{"text":" Who wrote the Divine Comedy?"}', "never an empty transcript"
    assert stt_calls(calls)[0]["files"]["file"] == ("q.wav", audio, "audio/wav"), "the clip whole"
    assert state["logged"] == [("warn", f"voice activity: silero-vad at http://ai failed ({why}); "
                                        f"a 1.00 s clip sent to whisper-large-v3 whole, untrimmed")]


def test_a_clip_of_no_samples_is_silence_and_nothing_is_asked(platform, server):
    platform(stt_model="whisper-large-v3", vad_model="silero-vad")
    _, calls, _ = server
    got = transcribe.transcribe(clip([]), "q.wav", "audio/wav", [], url="http://ai")
    assert (got.body, got.vad) == (b'{"text":""}', "silence: no speech in 0.00 s") and calls == []


def test_a_model_the_request_names_is_kept_and_none_anywhere_is_refused(platform, server):
    _, calls, _ = server
    platform(stt_model="whisper-large-v3")
    transcribe.transcribe(b"RIFF", "q.wav", "audio/wav", [("model", "whisper-1")], url="http://ai")
    assert stt_calls(calls)[-1]["data"]["model"] == "whisper-1", "a client's own choice (Open WebUI)"
    platform(tts_model="omnivoice-cpp")
    calls.clear()
    got = transcribe.transcribe(b"RIFF", "q.wav", "audio/wav", [], url="http://ai")
    assert got.status == 400 and calls == []
    assert json.loads(got.body)["error"]["message"] == \
        "no speech-to-text model is set on this node: `louise node speech set --stt <model>`"


def test_a_field_given_twice_goes_on_twice(platform, server):
    platform(stt_model="whisper-large-v3")
    _, calls, _ = server
    transcribe.transcribe(b"RIFF", "q.wav", "audio/wav",
                          [("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")],
                          url="http://ai")
    assert stt_calls(calls)[0]["data"]["timestamp_granularities[]"] == ["word", "segment"]


def test_a_model_server_that_does_not_answer_is_said_in_plain_words(platform, server):
    platform(stt_model="whisper-large-v3")
    state, _, _ = server
    state["stt"] = TimeoutError("read timed out")
    got = transcribe.transcribe(b"RIFF", "q.wav", "audio/wav", [], url="http://ai:8080")
    assert got.status == 502
    assert json.loads(got.body)["error"]["message"] == \
        "the speech-to-text model at http://ai:8080 did not answer (TimeoutError)"


def test_the_models_stay_pinned_while_a_reader_speaks_and_are_released_after(platform, server):
    platform(stt_model="whisper-large-v3", vad_model="silero-vad")
    state, _, pins = server
    state["vad"] = R(data={"segments": [{"start": 0.1, "end": 0.4}]})
    for _ in range(2):
        transcribe.transcribe(clip(SAMPLES[:RATE]), "q.wav", "audio/wav", [], url="http://ai")
    holds = [("hold", "silero-vad", "speech-input"), ("hold", "whisper-large-v3", "speech-input")]
    assert pins == holds * 2, "pinned from the first clip, not released between two"
    time.sleep(0.5)
    assert pins[-1] == ("release", "speech-input") and pins.count(("release", "speech-input")) == 1


# ---- the route -------------------------------------------------------------

def test_the_route_takes_openais_multipart_and_says_what_the_trim_did(node, platform, server):  # noqa: F811
    platform(stt_model="whisper-large-v3", vad_model="silero-vad")
    state, calls, _ = server
    state["vad"] = R(data={"segments": [{"start": 0.0, "end": 1.0}]})
    c = TestClient(api.app)
    r = c.post("/v1/audio/transcriptions", files={"file": ("question.wav", clip(SAMPLES), "audio/wav")},
               data={"language": "it"})
    assert r.status_code == 200 and r.json() == {"text": " Who wrote the Divine Comedy?"}
    assert r.headers["louise-vad"] == "trimmed: 1.00 s of speech kept of 3.00 s"
    assert stt_calls(calls)[0]["data"] == {"language": "it", "model": "whisper-large-v3"}
    state["vad"] = R(data={"segments": []})
    r = c.post("/v1/audio/transcriptions", files={"file": ("q.wav", clip([0] * 800), "audio/wav")})
    assert r.status_code == 200 and r.json() == {"text": ""} and r.headers["louise-vad"].startswith("silence")


def test_the_route_refuses_what_is_not_an_upload(node, platform, server, monkeypatch):  # noqa: F811
    platform(stt_model="whisper-large-v3")
    c = TestClient(api.app)
    r = c.post("/v1/audio/transcriptions", json={"file": "x"})
    assert r.status_code == 400 and "multipart/form-data" in r.json()["error"]["message"]
    r = c.post("/v1/audio/transcriptions", data={"model": "m"}, files={"other": ("a", b"x")})
    assert r.status_code == 400 and r.json()["error"]["message"] == "the upload has no `file`"
    monkeypatch.setattr(transcribe, "MAX_BYTES", 100)
    r = c.post("/v1/audio/transcriptions", files={"file": ("q.wav", b"\x00" * 200, "audio/wav")})
    assert r.status_code == 413
    assert server[1] == [], "nothing relayed"
