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


def test_two_threads_printing_at_once_never_share_a_line():
    """The interleaving itself, made to happen: each thread writes its text,
    then the other writes its text, then each its newline — what
    `print(text, flush=True)` does from two threads in the same instant."""
    import threading
    from app import log_lines
    raw = _Writes()
    out = log_lines.WholeLines(raw)
    a_text, b_text, a_done = threading.Event(), threading.Event(), threading.Event()

    def a():
        out.write("embedding goes on: NeuRIS")
        a_text.set()
        b_text.wait(5)
        out.write("\n")
        a_done.set()

    def b():
        a_text.wait(5)
        out.write("embedding goes on: wikidata")
        b_text.set()
        a_done.wait(5)
        out.write("\n")
    threads = [threading.Thread(target=a), threading.Thread(target=b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert raw.calls == ["embedding goes on: NeuRIS\n", "embedding goes on: wikidata\n"], raw.calls


def test_whole_lines_passes_several_lines_and_keeps_an_unfinished_one_for_its_thread():
    from app import log_lines
    raw = _Writes()
    out = log_lines.WholeLines(raw)
    assert out.write("one\ntwo\nthr") == 11
    assert raw.calls == ["one\ntwo\n"]
    out.write("ee\n")
    assert raw.calls == ["one\ntwo\n", "three\n"]


def test_the_worker_installs_it_once(monkeypatch):
    from app import log_lines
    real = _Writes()
    monkeypatch.setattr(sys, "stdout", real)
    assert log_lines.install() is True and isinstance(sys.stdout, log_lines.WholeLines)
    assert log_lines.install() is False, "a second call wraps nothing twice"
    print("a line", flush=True)
    assert real.calls == ["a line\n"]
    import inspect
    assert "log_lines.install()" in inspect.getsource(worker.main)
