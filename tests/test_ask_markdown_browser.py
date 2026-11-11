"""The answer's Markdown, formatted on the Ask page (operator, 2026-11-12:
"the text box seems to receive a md formatting that is printed as it is;
show the formatted text instead") — in a real browser, since what it makes
is DOM. tests/browser/ask_markdown.js checks the formats, that nothing the
model writes becomes HTML of its own, and the citations."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app
from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "ask_markdown.js"
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)


def test_the_answer_is_shown_formatted_and_safe(tmp_path, isolated_config):
    (tmp_path / "ask.html").write_text(TestClient(app).get("/ask").text, encoding="utf-8")
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(tmp_path), str(STATIC)],
                         capture_output=True, text=True, timeout=120, env=browser_support.env())
    report = run.stdout + run.stderr
    assert run.returncode == 0 and "ALL PASSED" in run.stdout, report
