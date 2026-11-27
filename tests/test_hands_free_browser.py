"""Hands-free in a real browser (docs/notes/hands-free-voice.md step 1;
operator, 2026-11-27: 1A, 2A, 3), Chromium's fake microphone playing tone
bursts as utterances: each goes to the node as a WAV, one in flight, the
question asked, the continuation window, the tap that turns it off."""
from __future__ import annotations

import math
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "hands_free.js"
VOICE = Path(__file__).resolve().parent.parent / "app" / "static" / "voice.js"

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)


def _utterances(path: Path) -> None:
    """1 s of a loud tone, then 2.5 s of a faint hiss: an utterance every 3.5 s."""
    rate, frames = 48000, bytearray()
    for i in range(int(rate * 3.5)):
        t = i / rate
        v = 0.5 * math.sin(2 * math.pi * 300 * t) if t < 1.0 else 0.002 * math.sin(2 * math.pi * 1000 * t)
        frames += struct.pack("<h", int(v * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(bytes(frames))


def test_hands_free_sends_each_utterance_one_at_a_time_and_asks_the_addressed(tmp_path):
    wav = tmp_path / "utterances.wav"
    _utterances(wav)
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(VOICE), str(wav)], capture_output=True, text=True,
                         timeout=300, env=browser_support.env())
    report = run.stdout + run.stderr
    assert run.returncode == 0 and "ALL PASSED" in run.stdout, report
