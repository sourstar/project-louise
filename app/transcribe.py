"""Speech to text for the Ask page's microphone, and for any client of
OpenAI's `POST /v1/audio/transcriptions` (operator, 2026-11-07: "voice
support in Louise's web GUI").

The upload is relayed to the node's model server (app/voice.py `server`,
CHAT_URL's — LocalAI) with the node's speech-to-text model where the
request names none (`[speech] stt_model`), after what only Louise adds:

- SILENCE IS NOT SENT. Where the node sets a voice-activity model
  (`[speech] vad_model`), a clip in the page's own form — a WAV of 16-bit
  samples at 16 kHz, mono — is asked of LocalAI's `POST /v1/vad` first, and
  only its speech goes on: the segments in order, GAP_S of silence between
  two. A clip with no speech in it is answered with an empty text and the
  speech-to-text model is not called: Whisper writes words into silence
  ("Thank you for watching" is the known one), and a breath or a room's
  hum is not a question. Any other audio goes on untouched. The answer's
  `Louise-VAD` header says what was done.
- THE MODELS STAY LOADED WHILE A READER SPEAKS: both pinned
  (app/model_pins.py) from the first clip and released speech.IDLE_S after
  the end of the last (`speech.pinned`, HOLDER).
- A VAD THAT FAILS COSTS THE TRIM, NOT THE QUESTION: an error status (400
  for a model or audio it refuses, 500 for its backend), no answer or one
  that cannot be read sends the clip on whole — never an empty transcript —
  says why in the header and writes a warn line in the node's log
  (`models`, db.NODE_STAGES). The page's own energy check only ends a
  recording; what is speech is the node's VAD's to say.

LocalAI's VAD (localai.io/features/voice-activity-detection; its schema
core/schema/localai.go `VADRequest`/`VADResponse`, the silero-vad backend):
`POST /v1/vad` (also `/vad`) `{"model", "audio": [float32 PCM, 16 kHz,
mono]}` → `{"segments": [{"start", "end"}]}` in SECONDS. A segment still open
when the clip ends has `end` 0 — silero-vad-go leaves it unset — read here
as "to the end of the clip". `segments` is the one function that speaks it.
"""
from __future__ import annotations

import io
import json
import struct
import sys
import wave
from array import array
from dataclasses import dataclass
from typing import Any

from app import httpc, model_pins, speech, voice

#: what the VAD takes, and the page sends
RATE = 16000
#: the silence put between two kept segments: their words stay apart
GAP_S = 0.2
#: OpenAI's own bound on an upload
MAX_BYTES = 25 * 1024 * 1024
#: one transcription, on a model that may have to load first
CALL_TIMEOUT_S = 180.0
#: the VAD of one clip (silero: milliseconds a second of audio, plus a load)
VAD_TIMEOUT_S = 60.0
HOLDER = "speech-input"
JSON = "application/json"
TEXT = "text/plain; charset=utf-8"


class VadFailed(Exception):
    """The VAD gave no answer to read: the clip goes on whole."""


@dataclass(frozen=True)
class Answer:
    status: int
    body: bytes
    content_type: str
    #: the `Louise-VAD` header: off, skipped, failed, silence or trimmed
    vad: str


def pcm16_mono_16k(data: bytes) -> bytes | None:
    """The sample bytes of a WAV in the page's form — 16-bit PCM at 16 kHz,
    mono — little-endian as the file holds them; None for any other audio."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) \
                    != (1, 2, RATE, "NONE"):
                return None
            return w.readframes(w.getnframes())
    except (wave.Error, EOFError, struct.error, ValueError):
        return None


def wav(frames: bytes) -> bytes:
    """`frames` (16-bit little-endian samples) as a 16 kHz mono WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    return buf.getvalue()


def floats(frames: bytes) -> list[float]:
    """float32 PCM as LocalAI's VAD takes it: each sample over 32768, exact."""
    a = array("h")
    a.frombytes(frames[: len(frames) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    return [s / 32768.0 for s in a]


def ranges(segments: list[Any], n: int, rate: int = RATE) -> list[tuple[int, int]]:
    """LocalAI's segments (seconds) as sample ranges [lo, hi) inside [0, n),
    sorted, overlaps merged. An `end` of 0, or one not after its `start`, is
    a segment still open when the clip ended: it runs to the end."""
    out: list[tuple[int, int]] = []
    for s in segments:
        try:
            a = float(s.get("start") or 0.0)
            b = float(s.get("end") or 0.0)
        except (AttributeError, TypeError, ValueError):
            continue
        lo = min(max(round(a * rate), 0), n)
        hi = n if b <= a else min(max(round(b * rate), 0), n)
        if hi > lo:
            out.append((lo, hi))
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(out):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def trimmed(frames: bytes, spans: list[tuple[int, int]], gap: int | None = None) -> bytes:
    """The speech of `frames`, span after span, `gap` samples of silence
    (GAP_S) between two and none at either end."""
    pause = b"\x00\x00" * (round(GAP_S * RATE) if gap is None else gap)
    return pause.join(frames[2 * lo: 2 * hi] for lo, hi in spans)


def segments(url: str, model: str, frames: bytes) -> list[tuple[int, int]]:
    """THE ONE CALL OF LocalAI's `POST /v1/vad`: the clip's speech as sample
    ranges (`ranges`), [] for none. Raises VadFailed when there is no
    answer to read."""
    body = json.dumps({"model": model, "audio": floats(frames)}, separators=(",", ":"))
    try:
        r = httpc.post(f"{url}/v1/vad", content=body.encode(), headers={"Content-Type": JSON},
                       timeout=VAD_TIMEOUT_S)
    except Exception as e:   # noqa: BLE001 — the clip goes on whole, and the header says why
        raise VadFailed(f"{type(e).__name__}: {e}"[:200]) from None
    if r.status_code != 200:
        raise VadFailed(f"HTTP {r.status_code}")
    try:
        got = r.json()
    except ValueError:
        raise VadFailed("the answer is not JSON") from None
    segs = got.get("segments") if isinstance(got, dict) else None
    if not isinstance(segs, list) and segs is not None:
        raise VadFailed("the answer has no segments list")
    return ranges(segs or [], len(frames) // 2)


def silence(fmt: str, language: str | None, seconds: float) -> tuple[bytes, str]:
    """The answer to a clip with no speech, in the response format asked."""
    if fmt in ("text", "srt"):
        return b"", TEXT
    if fmt == "vtt":
        return b"WEBVTT\n\n", TEXT
    if fmt == "verbose_json":
        return json.dumps({"task": "transcribe", "language": language or "",
                           "duration": round(seconds, 3), "text": "", "segments": []}).encode(), JSON
    return b'{"text":""}', JSON


def _error(status: int, message: str, vad: str) -> Answer:
    return Answer(status, json.dumps({"error": {"message": message, "type": "invalid_request_error"}},
                                     ensure_ascii=False).encode(), JSON, vad)


def transcribe(audio: bytes, filename: str, content_type: str, fields: list[tuple[str, str]],
               url: str | None = None) -> Answer:
    """An OpenAI transcription request — the file and the form's other
    fields — relayed to the model server after the VAD's trim."""
    url = (url or voice.server()).rstrip("/")
    try:
        node = voice.settings()
    except voice.LOAD_ERRORS as e:
        return _error(500, f"this node's platform file does not load ({e})", "off")
    form: dict[str, Any] = {}           # a field given twice (timestamp_granularities[]) as a list
    for k, v in fields:
        if k == "file":
            continue
        if k in form:
            form[k] = (form[k] if isinstance(form[k], list) else [form[k]]) + [v]
        else:
            form[k] = v
    model = str(form.get("model") or "").strip() or node.stt_model
    if not model:
        return _error(400, "no speech-to-text model is set on this node: "
                           "`louise node speech set --stt <model>`", "off")
    form["model"] = model
    if not audio:
        return _error(400, "the upload holds no audio", "off")
    fmt = str(form.get("response_format") or "json")
    vad = "off"
    send = audio
    with speech.pinned(url, [node.vad_model, model], HOLDER):
        if node.vad_model:
            frames = pcm16_mono_16k(audio)
            if frames is None:
                vad = "skipped: not a 16-bit 16 kHz mono WAV"
            else:
                n = len(frames) // 2
                try:
                    spans = segments(url, node.vad_model, frames) if n else []
                except VadFailed as e:
                    vad = f"failed: {e}"
                    model_pins.node_event("warn", f"voice activity: {node.vad_model} at {url} failed ({e}); "
                                                  f"a {n / RATE:.2f} s clip sent to {model} whole, untrimmed")
                else:
                    if not spans:
                        body, ctype = silence(fmt, form.get("language"), n / RATE)
                        return Answer(200, body, ctype, f"silence: no speech in {n / RATE:.2f} s")
                    kept = sum(hi - lo for lo, hi in spans)
                    send = wav(trimmed(frames, spans))
                    filename, content_type = "speech.wav", "audio/wav"
                    vad = f"trimmed: {kept / RATE:.2f} s of speech kept of {n / RATE:.2f} s"
        try:
            r = httpc.post(f"{url}/v1/audio/transcriptions",
                           files={"file": (filename or "audio", send, content_type or "application/octet-stream")},
                           data=form, timeout=CALL_TIMEOUT_S)
        except Exception as e:   # noqa: BLE001 — said to the reader, in plain words
            return _error(502, f"the speech-to-text model at {url} did not answer "
                               f"({type(e).__name__})", vad)
    return Answer(r.status_code, r.content, r.headers.get("content-type", JSON), vad)
