"""The search's kind on the Ask page, in a real browser
(tests/browser/ask_search_kind.js; docs/notes/fact-fiction-search.md): the
page asks auto, the chip's tap runs the question again with that kind for
the search and the answer alike, and a new question is auto again."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import web
from app.api import app
from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "ask_search_kind.js"

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)


def test_the_chip_changes_the_kind_of_this_question_only(isolated_config, tmp_path):
    (tmp_path / "ask.html").write_text(TestClient(app).get("/ask").text, encoding="utf-8")
    hits = [{"chunk_id": "c:1:0:1", "book_id": 1, "source_collection": "c", "lang": "en", "title": "Title 1",
             "authors": "", "chapter": "Chapter 1", "text": "Napoleon crossed the Niemen.", "origin_url": None}]
    (tmp_path / "results.html").write_text(web.TEMPLATES.env.get_template("_ask_results.html").render(
        result={"hits": hits, "timings": {"fts": 1}, "kind_applied": "facts", "kind_by": "the question",
                "left_out": {"count": 2, "by_basis": {"by its subject headings": 2}}}), encoding="utf-8")
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(tmp_path)], capture_output=True,
                         text=True, timeout=300, env=browser_support.env())
    report = run.stdout + run.stderr
    assert run.returncode == 0 and "ALL PASSED" in run.stdout, report
