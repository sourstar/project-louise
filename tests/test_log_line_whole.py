"""One event, one line of the log (app/db.py `say_line`).

München, the worker's start of 2026-11-25: the "embedding goes on: …" lines
of NeuRIS and wikidata came out on ONE line, no newline between them —
`print(text, flush=True)` writes the text and its newline as two writes, and
two threads logging in the same instant interleave them."""
from __future__ import annotations

import sys

from app import db, worker


class _Writes:
    def __init__(self):
        self.calls: list[str] = []

    def write(self, s: str) -> int:
        self.calls.append(s)
        return len(s)

    def flush(self) -> None:
        pass


def test_a_log_line_and_its_newline_are_one_write(isolated_config, monkeypatch):
    con = db.connect()
    out = _Writes()
    monkeypatch.setattr(sys, "stdout", out)
    db.log_event(con, "info", "embed", "embedding goes on: the keeper's preload after the start has ended")
    worker._node_line("pool")("EUR-Lex5.next fts: 1 part in the parse pool")
    monkeypatch.undo()
    assert len(out.calls) == 2, out.calls
    assert all(c.endswith("\n") and c.count("\n") == 1 for c in out.calls), \
        "the text and its newline in two writes are what two threads interleave"
    assert "embedding goes on" in out.calls[0] and "[node info:pool] EUR-Lex5.next fts" in out.calls[1]
