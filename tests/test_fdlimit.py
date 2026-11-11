"""Open files (München, 2026-11-11): the worker met its soft limit of 1024
(hard 524288) twice in six minutes and one normattiva document was written
an error for it. The soft limit is raised to the hard one at every start
(app/fdlimit.py; systemd's guidance, Go's runtime since 1.19); running out of
files or space is a deferral, never a document's error (CLAUDE.md), and is
said with a census of what holds the files."""
from __future__ import annotations

import errno
import os

import pytest

from app import fdlimit, worker


class FakeResource:
    RLIMIT_NOFILE = 7
    RLIM_INFINITY = -1

    def __init__(self, soft, hard):
        self.limits = (soft, hard)

    def getrlimit(self, which):
        return self.limits

    def setrlimit(self, which, value):
        self.limits = value


def test_the_soft_limit_is_raised_to_the_hard_one(monkeypatch):
    r = FakeResource(1024, 524288)
    monkeypatch.setattr(fdlimit, "resource", r)
    assert fdlimit.raise_soft() == (1024, 524288) and r.limits == (524288, 524288)
    assert fdlimit.raise_soft() == (524288, 524288), "already there: nothing to do"
    monkeypatch.setattr(fdlimit, "resource", FakeResource(1024, -1))
    assert fdlimit.raise_soft() == (1024, 1024), "an unlimited hard limit is not asked for"

    def refused(which, value):
        raise ValueError("not permitted")
    r = FakeResource(1024, 4096)
    r.setrlimit = refused
    monkeypatch.setattr(fdlimit, "resource", r)
    assert fdlimit.raise_soft() is None, "a process that cannot raise it runs with what it has"


def test_the_census_names_what_holds_the_files(tmp_path):
    fds = tmp_path / "fd"
    fds.mkdir()
    targets = ["/data/c/vectors/chunks.lance/data/a.lance", "/data/c/vectors/chunks.lance/_versions/3.manifest",
               "/data/c/state/manifest.sqlite", "/data/c/state/manifest.sqlite-wal",
               "/data/c/state/manifest.sqlite-shm", "/data/c/index/abc.idx", "socket:[123]", "pipe:[9]",
               "anon_inode:[eventpoll]", "/usr/lib/libz.so"]
    for i, t in enumerate(targets):
        os.symlink(t, fds / str(i))
    got = fdlimit.census(str(fds))
    assert got == {"lance": 2, "sqlite": 1, "sqlite-wal": 1, "sqlite-shm": 1, "text-index": 1,
                   "socket": 1, "pipe": 1, "anon": 1, "other": 1, "total": 10}
    assert fdlimit.census(str(tmp_path / "none")) == {}
    line = fdlimit.said(got)
    assert line.startswith("10 open file(s)") and "lance 2" in line


@pytest.mark.parametrize("exc", [
    OSError(errno.EMFILE, "Too many open files"),
    OSError(errno.ENFILE, "Too many open files in system"),
    OSError(errno.ENOSPC, "No space left on device"),
    RuntimeError("lance error: LanceError(IO): Unable to walk dir: Too many open files (os error 24)"),
    RuntimeError("lance error: No space left on device (os error 28)"),
])
def test_out_of_files_or_space_is_the_node_s_never_the_document_s(exc):
    assert worker.resources_exhausted(exc) and worker.transient_failure(exc)


def test_it_is_found_down_the_chain_and_nothing_else_is_taken_for_it():
    try:
        try:
            raise OSError(errno.EMFILE, "Too many open files")
        except OSError as inner:
            raise RuntimeError("embed failed") from inner
    except RuntimeError as outer:
        assert worker.resources_exhausted(outer)
    for exc in (ValueError("a malformed document"), MemoryError(), OSError(errno.ENOENT, "gone"),
                RuntimeError("os error 2")):
        assert not worker.resources_exhausted(exc), exc


def test_it_is_said_at_most_once_a_minute(monkeypatch):
    said = []
    monkeypatch.setattr(worker.db, "log_event", lambda con, level, stage, msg, *a: said.append(msg))
    monkeypatch.setattr(worker, "_exhausted_said", [0.0])
    clock = [1000.0]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    e = OSError(errno.EMFILE, "Too many open files")
    worker.say_exhausted(None, "embed", e)
    worker.say_exhausted(None, "embed", e)
    clock[0] += 61
    worker.say_exhausted(None, "fts", e)
    assert len(said) == 2 and "open file(s)" in said[0] and said[1].startswith("fts deferred")
