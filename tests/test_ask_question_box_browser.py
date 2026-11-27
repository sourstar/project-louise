"""The question box fits its text (operator, 2026-11-27: "Fix the query text
box to a minimum size, and expand it automatically to fit the text; the
current manual resize is not handy"), measured in a real browser
(tests/browser/ask_question_box.js): two lines at least, growing to 40% of
the screen, no handle — by CSS `field-sizing: content` and by the page's
fallback alike."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app
from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "ask_question_box.js"
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)


def test_the_question_box_fits_its_text(isolated_config, tmp_path):
    (tmp_path / "ask.html").write_text(TestClient(app).get("/ask").text, encoding="utf-8")
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(tmp_path), str(STATIC)], capture_output=True,
                         text=True, timeout=300, env=browser_support.env())
    report = run.stdout + run.stderr
    assert run.returncode == 0 and "ALL PASSED" in run.stdout, report
