"""The Ask page's "save pdf" in a real browser (operator, 2026-11-11): the
button shows once the answer is complete, and what it posts to /ask/pdf is
what the reader sees — tests/browser/ask_save_pdf.js."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import web
from app.api import app
from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "ask_save_pdf.js"
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)

HITS = [
    {"source_collection": "wikipedia", "book_id": 2471318, "chunk_id": "wikipedia:2471318:3",
     "title": "Lighthouse of Alexandria", "authors": "Doe J", "lang": "en", "sources": ["fts"],
     "chapter": "History", "origin_url": "https://en.wikipedia.org/wiki/Lighthouse_of_Alexandria",
     "text": "The tower was built in the third century BC.", "editions": [], "annotations": [],
     "moved_by": [{"weight": "catalog_first", "from": 3, "to": 1}]},
    {"source_collection": "wikipedia", "book_id": 8830114, "chunk_id": "wikipedia:8830114:1",
     "title": "Faro di Alessandria", "lang": "it", "sources": ["dense"], "chapter": "",
     "origin_url": None, "text": "Commissionato da Tolomeo I.", "editions": [], "annotations": []},
]


def test_the_page_posts_what_the_reader_sees(tmp_path, isolated_config):
    (tmp_path / "ask.html").write_text(TestClient(app).get("/ask").text, encoding="utf-8")
    (tmp_path / "results.html").write_text(
        web.TEMPLATES.env.get_template("_ask_results.html").render(result={"hits": HITS}), encoding="utf-8")
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(tmp_path), str(STATIC)],
                         capture_output=True, text=True, timeout=120, env=browser_support.env())
    assert run.returncode == 0, run.stderr[-2000:]
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert got["hiddenBefore"] and not got["hiddenAfter"], "shown once the answer is complete"
    assert got["label"] == "save pdf" and not got["disabled"] and not got["note"], got
    assert got["stored"] == "letter", "the paper is remembered in this browser"
    (body,) = got["posted"]
    assert body["paper"] == "letter"
    assert body["question"] == "Who built the lighthouse?", "the question as asked, not the box's text now"
    assert body["answer"] == "Built around 280 BC [1][2]." and body["answer_lang"] == "en"
    assert body["collections"] and body["mode"] == "hybrid" and body["tz"]
    one, two = body["references"]
    assert one["n"] == 1 and one["title"] == "Lighthouse of Alexandria" and one["title_original"] is None
    assert one["authors"] == "Doe J" and one["chapter"] == "History" and one["lang"] == "en"
    assert one["origin_url"] == "https://en.wikipedia.org/wiki/Lighthouse_of_Alexandria"
    assert one["book"] == "2471318" and one["chunk"] == "wikipedia:2471318:3"
    assert one["moved_by"] == ["catalogue match: moved up from #3"]
    assert two["title"] == "Lighthouse of Alexandria" and two["title_original"] == "Faro di Alessandria"
    assert two["passage"] == "Commissioned by Ptolemy I." and two["translated_to"] == "en"
    assert two["lang"] == "it" and two["origin_url"] is None
