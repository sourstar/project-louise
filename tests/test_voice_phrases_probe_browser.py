"""Architecture's probes of the phrases that cover the wait (2026-11-27), in a
real browser against app/static/voice.js alone: a late phrase list never takes
the player from a sentence; a phrase played is reported used once."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "voice_phrases_probe.js"
VOICE = Path(__file__).resolve().parent.parent / "app" / "static" / "voice.js"

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)


def test_the_phrases_never_stop_a_reading_and_are_used_once():
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(VOICE)], capture_output=True, text=True,
                         timeout=300, env=browser_support.env())
    report = run.stdout + run.stderr
    assert run.returncode == 0 and "ALL PASSED" in run.stdout, report
