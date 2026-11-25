"""The Ask button is Abort while a question is being answered, in a real
browser (operator, 2026-11-25: "while a query is in processing I want the Ask
button to switch to Abort to be able to abort the query in case I change my
mind or I want to change the query").

tests/browser/ask_abort.js drives the real page against a scripted node: a
search and an answer aborted in flight (the node told to cancel each, DELETE
/ask/relay/<id>), a question aborted before the node named its job, a
question that ends by itself, one replaced by a newer one, and one picked up
after a reload."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import web
from app.api import app
from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "ask_abort.js"
N = 3

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)


def test_the_ask_button_aborts_a_question_in_flight(isolated_config, tmp_path):
    (tmp_path / "ask.html").write_text(TestClient(app).get("/ask").text, encoding="utf-8")
    hits = [{"chunk_id": f"c:{k}:0:1", "book_id": k, "source_collection": "c", "lang": "en",
             "title": f"Title {k}", "authors": "", "chapter": f"Chapter {k}",
             "text": f"The whale {k} rose beside the ship.", "origin_url": None} for k in range(1, N + 1)]
    (tmp_path / "results.html").write_text(web.TEMPLATES.env.get_template("_ask_results.html").render(
        result={"hits": hits, "timings": {"fts": 1}}), encoding="utf-8")
    (tmp_path / "n.txt").write_text(str(N))
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(tmp_path)], capture_output=True,
                         text=True, timeout=300, env=browser_support.env())
    report = run.stdout + run.stderr
    assert run.returncode == 0 and "ALL PASSED" in run.stdout, report
    assert "FAIL" not in run.stdout, report
