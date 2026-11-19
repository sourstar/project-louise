"""The Ask page's voice in a real browser (app/static/voice.js; operator,
2026-11-07): Chromium's fake microphone, fed a WAV — a tone, then quiet —
records; the page stops by itself once the speech is over, posts a WAV of
16-bit samples at 16 kHz, mono, to /v1/audio/transcriptions and fills the
question box without asking; the speaker requests /v1/audio/speech per
sentence of the answer, in order, and stops when tapped; a voice the node
does not offer has no button; a phone's width holds it all. While the answer
streams (operator, 2026-11-08), each sentence is asked for once the stream
completes it — the first before the stream ends — none twice, none half,
played in order; a tap or a new question cancels what is in flight; a typed
question waits for the speaker, a spoken one is read by itself; a refused
playback is said; a translated answer is read from its first sentence.

tests/browser/ask_voice.js drives the page the app renders against a
scripted node."""
from __future__ import annotations

import io
import math
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import web
from app.api import app
from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "ask_voice.js"
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)


def _wav(samples: list[float], rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *(round(s * 32767) for s in samples)))
    return buf.getvalue()


def test_the_ask_page_speaks_and_listens(isolated_config, config_dir, tmp_path):
    (config_dir / "platform.toml").write_text(
        '[speech]\nstt_model = "whisper-large-v3"\nvad_model = "silero-vad"\ntts_model = "omnivoice-cpp"\n',
        encoding="utf-8")
    (tmp_path / "ask.html").write_text(TestClient(app).get("/ask").text, encoding="utf-8")
    (config_dir / "platform.toml").write_text("", encoding="utf-8")
    (tmp_path / "ask_off.html").write_text(TestClient(app).get("/ask").text, encoding="utf-8")
    hits = [{"chunk_id": f"c:{k}:0:1", "book_id": k, "source_collection": "c", "lang": "en",
             "title": f"Title {k}", "authors": "", "chapter": f"Chapter {k}",
             "text": f"Dante {k} wrote it.", "origin_url": None} for k in (1, 2)]
    (tmp_path / "results.html").write_text(web.TEMPLATES.env.get_template("_ask_results.html").render(
        result={"hits": hits, "timings": {"fts": 1}}), encoding="utf-8")
    # what the fake microphone hears, looped: quiet, a second of "speech", quiet
    rate = 48000
    tone = [0.5 * math.sin(2 * math.pi * 440 * i / rate) for i in range(int(1.2 * rate))]
    (tmp_path / "voice.wav").write_bytes(_wav([0.0] * int(0.3 * rate) + tone + [0.0] * int(2.5 * rate), rate))
    # what the fake node says for each sentence: a quarter of a second
    (tmp_path / "reply.wav").write_bytes(_wav([0.0] * 4000, 16000))
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(tmp_path), str(STATIC)],
                         capture_output=True, text=True, timeout=300, env=browser_support.env())
    report = run.stdout + run.stderr
    assert run.returncode == 0 and "ALL PASSED" in run.stdout, report
    assert "FAIL" not in run.stdout, report
