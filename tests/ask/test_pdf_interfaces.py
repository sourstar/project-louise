"""The saved PDF through the other interfaces (operator, 2026-11-11: "Since we
produce it internally, make it available through the other interfaces
too"): `ask pdf` on the command line, `POST /api/ask/pdf`, and the MCP tool
`louise_pdf` — the same document the Ask page saves (app/ask_pdf.py), from
Louise's own answer or from a client's own answer and the hits it used.
`louise_pdf` is for a person's file: offered to MCP clients, never to
Louise's chat model."""
from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app import api, ask_pdf, config, louise
from app.ask import chat, mcp
from tests.ask.test_chat import SEARCH_RESULT, scripted

CHAT_OUT = {
    "answer": "Ahab hunts it [1]; see [2].", "answer_lang": "en",
    "sources": [{"n": 1, "uid": "gutenberg:2701", "title": "Moby Dick", "authors": "Melville, Herman",
                 "lang": "en", "collection_id": "sha256:" + "2" * 64, "chunk_id": "gutenberg:2701:3:1",
                 "origin_url": "https://www.gutenberg.org/ebooks/2701"},
                {"n": 2, "uid": "pubmed:1", "title": "Whales", "lang": "en", "collection_id": None,
                 "chunk_id": None, "origin_url": "javascript:x"}],
    "passages": {"1": {"text": "Call me Ishmael.", "chapter": "Loomings"}, "2": {"text": "Whales.", "chapter": None}},
    "cited": [{"n": 1, "uid": "gutenberg:2701", "title": "Moby Dick"}]}


@pytest.fixture
def names(monkeypatch):
    monkeypatch.setattr(ask_pdf, "_names", lambda: {"sha256:" + "2" * 64: "gutenberg-books"})


@pytest.fixture
def rendered(monkeypatch):
    """`render` replaced: the HTML each call laid out, a stub PDF back."""
    seen: list[str] = []
    monkeypatch.setattr(ask_pdf, "render", lambda html: seen.append(html) or b"%PDF-1.7 stub")
    return seen


def test_chat_keeps_the_passages_it_showed_only_when_asked(monkeypatch):
    monkeypatch.setattr(config, "CHAT_URL", "http://chat")
    out = chat.ask("q", call=lambda n, a: SEARCH_RESULT, model=scripted(("A [1].", [])))
    assert "passages" not in out, "`ask chat`'s output as it was"
    out = chat.ask("q", call=lambda n, a: SEARCH_RESULT, model=scripted(("A [1].", [])), passages=True)
    assert out["passages"]["1"]["text"].startswith("xxx") and set(out["passages"]) == {"1", "2"}


def test_louises_answer_becomes_the_pdfs_document(names):
    doc = ask_pdf.from_chat("who hunts the whale?", CHAT_OUT, paper="letter", tz="Europe/Rome",
                            collections=["gutenberg-books"])
    one, two = doc.references
    assert (one.n, one.collection, one.book, one.chunk) == (1, "gutenberg-books", "2701", "gutenberg:2701:3:1")
    assert one.passage == "Call me Ishmael." and one.chapter == "Loomings" and one.authors == "Melville, Herman"
    assert two.collection is None and two.book is None, "no chunk id: no reader link"
    html = ask_pdf.build_html(doc, base_url="https://n.example", node="n.example",
                              when=ask_pdf.reader_time(doc.tz))
    assert "size: letter portrait" in html and "javascript" not in html
    assert "https://n.example/read/gutenberg-books/2701?hl=gutenberg%3A2701%3A3%3A1" in html
    assert "References · 2, 2 cited in the answer" in html


def test_a_clients_own_answer_and_hits_become_the_document(names):
    hits = [{"uid": "gutenberg:2701", "title": "Moby Dick", "lang": "en", "collection_id": "sha256:" + "2" * 64,
             "chunk_id": "gutenberg:2701:3:1", "text": "Call me Ishmael.", "origin_url": "https://x.org/1"},
            {"n": 7, "title": "Seven", "passage": "kept", "collection": "c"}, "not a hit"]
    doc = ask_pdf.from_given("q", "A [1] [7].", hits)
    assert [r.n for r in doc.references] == [1, 7]
    assert doc.references[0].passage == "Call me Ishmael." and doc.references[1].passage == "kept"


def test_the_api_answers_a_question_with_a_pdf(isolated_config, names, rendered, monkeypatch):
    asked = {}

    def fake_ask(question, **kw):
        asked.update(kw, question=question)
        return CHAT_OUT
    monkeypatch.setattr(chat, "ask", fake_ask)
    r = TestClient(api.app).post("/api/ask/pdf", json={"question": "who hunts the whale?", "paper": "letter",
                                                       "collections": ["gutenberg-books"]},
                                 headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "ask.example.org"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert r.headers["content-disposition"].startswith('attachment; filename="louise-')
    assert asked["passages"] is True and asked["collections"] == ["gutenberg-books"]
    assert "size: letter portrait" in rendered[0]
    assert "https://ask.example.org/read/gutenberg-books/2701" in rendered[0]


def test_the_api_lays_out_a_clients_own_answer_without_asking_the_model(isolated_config, names, rendered,
                                                                       monkeypatch):
    monkeypatch.setattr(chat, "ask", lambda *a, **k: pytest.fail("the model was asked"))
    r = TestClient(api.app).post("/api/ask/pdf", json={
        "question": "q", "answer": "Mine [1].", "references": [{"title": "T", "text": "p", "origin_url": "https://o.org"}]})
    assert r.status_code == 200 and "Mine" in rendered[0] and "https://o.org" in rendered[0]


def test_the_api_says_why_it_cannot(isolated_config, monkeypatch):
    c = TestClient(api.app)
    assert c.post("/api/ask/pdf", json={"question": "q", "answer": "a", "paper": "a3"}).status_code == 409

    def busy(html):
        raise ask_pdf.Busy("2 PDFs are being laid out")
    monkeypatch.setattr(ask_pdf, "render", busy)
    r = c.post("/api/ask/pdf", json={"question": "q", "answer": "a"})
    assert r.status_code == 503 and r.json()["exit"] == 3 and "laid out" in r.json()["error"]


def test_mcp_offers_louise_pdf_to_clients_and_never_to_the_chat_model():
    listed = [t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
    assert listed == ["louise_search", "louise_lookup", "louise_traverse", "louise_pdf"]
    assert "louise_pdf" not in [t["function"]["name"] for t in chat.tool_specs()], \
        "the chat model answers, it does not file"


def test_mcp_returns_the_pdf_as_an_embedded_resource(names, rendered, monkeypatch):
    monkeypatch.setattr(ask_pdf, "node_base", lambda: "http://nas:8742")
    got = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "louise_pdf", "arguments": {"question": "who hunts the whale?", "answer": "Ahab [1].",
                                            "references": SEARCH_RESULT["lists"][0]["hits"], "paper": "letter"}}})
    res = got["result"]
    assert res["isError"] is False
    said, blob = res["content"]
    assert json.loads(said["text"])["references"] == 2 and json.loads(said["text"])["paper"] == "letter"
    assert blob["type"] == "resource" and blob["resource"]["mimeType"] == "application/pdf"
    assert base64.b64decode(blob["resource"]["blob"]) == b"%PDF-1.7 stub"
    assert blob["resource"]["uri"].startswith("louise://pdf/louise-") and blob["resource"]["uri"].endswith(".pdf")
    assert "http://nas:8742/read/gutenberg-books/2701" in rendered[0], "the node's own address, over stdio too"
    bad = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "louise_pdf", "arguments": {"question": "q", "answer": "a", "references": [], "paper": "a3"}}})
    assert bad["result"]["isError"] is True and "paper" in bad["result"]["content"][0]["text"]


def test_the_command_writes_the_file(tmp_path, names, rendered, monkeypatch, capsys):
    monkeypatch.setattr(chat, "ask", lambda q, **k: dict(CHAT_OUT, asked=k))
    monkeypatch.setattr(ask_pdf, "node_base", lambda: "")
    out = tmp_path / "whale.pdf"
    assert louise.main(["ask", "pdf", "who", "hunts", "the", "whale?", "--paper", "letter",
                        "--out", str(out)]) == 0
    assert out.read_bytes() == b"%PDF-1.7 stub"
    said = json.loads(capsys.readouterr().out)
    assert said["file"] == str(out) and said["references"] == 2 and said["paper"] == "letter"
    assert "/read/" not in rendered[0], "no node address set: no reader link"
