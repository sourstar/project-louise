"""O1 of the EUR-Lex review (2026-11-21): the worker kept no log file and
Docker's log died with each container — every deploy and the 01:56Z panic
erased the evidence of 2026-11-19/20. Now the worker's lines (written by
its guard, its children's included) and the web's go to rotated files under
the node's data as well (app/log_file.py, the standard library's
RotatingFileHandler), bounded in size."""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app import config, log_file

ROOT = Path(__file__).resolve().parent.parent
STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ")


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    return tmp_path / "logs"


def test_the_file_is_under_the_nodes_data_unless_a_dir_is_named(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_DIR", raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    assert log_file.path("worker") == tmp_path / "data" / "logs" / "worker.log"
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "elsewhere"))
    assert log_file.path("web") == tmp_path / "elsewhere" / "web.log"


def test_the_file_rotates_within_its_bound(logs, monkeypatch):
    monkeypatch.setattr(config, "LOG_FILE_BYTES", 2000)
    monkeypatch.setattr(config, "LOG_FILE_BACKUPS", 2)
    line = log_file.open_lines("worker")
    for i in range(500):
        line(f"10:38:{i % 60:02d} [eurlex5 info:fts] indexed {i} documents in one commit")
    line.handler.close()
    files = sorted(p.name for p in logs.iterdir())
    assert files == ["worker.log", "worker.log.1", "worker.log.2"], files
    assert all(p.stat().st_size <= 2000 for p in logs.iterdir()), "each file within its bound"
    last = (logs / "worker.log").read_text().splitlines()[-1]
    assert STAMP.match(last) and last.endswith("indexed 499 documents in one commit"), last


def test_the_tee_passes_every_byte_on_and_files_whole_lines_from_every_thread(logs):
    original = io.StringIO()
    line = log_file.open_lines("web")
    tee = log_file.Tee(original, line)
    tee.write("a half ")
    tee.write("line\nand ")
    tee.write("another\n")

    def say(n):
        for i in range(50):
            print(f"thread {n} line {i}", file=tee)
    threads = [threading.Thread(target=say, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    line.handler.close()
    assert original.getvalue().startswith("a half line\nand another\n"), "the stream as before"
    filed = [STAMP.sub("", ln) for ln in (logs / "web.log").read_text().splitlines()]
    assert filed[:2] == ["a half line", "and another"]
    assert sorted(filed[2:]) == sorted(f"thread {n} line {i}" for n in range(8) for i in range(50)), \
        "every line whole, none interleaved"


def test_a_log_that_cannot_be_written_never_stops_the_process(tmp_path, monkeypatch):
    said: list[str] = []
    (tmp_path / "a-file").write_text("x")
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "a-file" / "logs"))
    assert log_file.open_lines("worker", say=said.append) is None
    assert len(said) == 1 and "cannot be opened" in said[0]
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    line = log_file.open_lines("worker", say=said.append)

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(line.handler, "emit", lambda record: line.handler.handleError(record))
    try:
        raise OSError(28, "No space left on device")
    except OSError:
        line("one")
        line("two")
    assert len(said) == 2 and "cannot be written" in said[1], said
    monkeypatch.setattr(config, "LOG_FILE_BYTES", 0)
    assert log_file.open_lines("worker", say=said.append) is None, "LOG_FILE_BYTES=0: off"
    assert log_file.tee_std("worker") is None


def test_the_guard_files_the_workers_lines_and_its_own(tmp_path):
    """The worker's guard (its container command) reads every line of the
    worker's merged stdout and stderr: the file holds them, its children's
    included, and the guard's own; the worker is told not to write it again."""
    child = ("import os, subprocess, sys\n"
             "print('the worker: parsing eurlex5 32021R2017 el', flush=True)\n"
             "print('the worker, to stderr', file=sys.stderr, flush=True)\n"
             "subprocess.run([sys.executable, '-c', \"print('a parse child')\"])\n"
             f"print('written by', os.environ.get({log_file.WRITTEN_BY!r}), flush=True)\n")
    code = ("import sys\n"
            "from app import log_file, worker_guard\n"
            "log_file.tee_std('worker')\n"
            f"sys.exit(worker_guard.run(command=[sys.executable, '-c', {child!r}]))\n")
    env = {**os.environ, "LOG_DIR": str(tmp_path / "logs"), "DATA_DIR": str(tmp_path / "data"),
           "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=120, check=False)
    assert r.returncode == 0, r.stderr
    assert "the worker: parsing eurlex5" in r.stdout, "the container's log as before"
    filed = [STAMP.sub("", ln) for ln in (tmp_path / "logs" / "worker.log").read_text().splitlines()]
    for want in ("the worker: parsing eurlex5 32021R2017 el", "the worker, to stderr", "a parse child",
                 "written by worker-guard"):
        assert want in filed, (want, filed)


def test_the_web_entry_opens_its_file_before_uvicorn_configures_its_logging(monkeypatch):
    import uvicorn

    from app import web_main
    order: list = []
    monkeypatch.setattr(log_file, "tee_std", lambda name: order.append(("tee", name)))
    monkeypatch.setattr(uvicorn, "main", lambda: order.append(("uvicorn", list(sys.argv))) or 0)
    monkeypatch.setattr(sys, "argv", ["x"])
    assert web_main.main(["app.api:app", "--port", "8742"]) == 0
    assert order == [("tee", "web"), ("uvicorn", ["uvicorn", "app.api:app", "--port", "8742"])]
    import signal
    assert signal.getsignal(signal.SIGTERM) is web_main._stopped_while_loading
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    from app import louise
    assert louise.WEB_COMMAND[:2] == ("-m", "app.web_main"), "node start runs the image's own command"
