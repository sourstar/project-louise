"""The wait after a failed fetch attempt, for every collection (operator, 2026-11-07: "retry every 1, 5, 10, 30,
60, 120 minutes, then every 120 minutes only. This for all the collections, since fetching must be a common
engine"). Before it, `_is_pending` never read `retries`: each pass retried a failing file at once."""
import sqlite3

import pytest

from app.acquire import engine


class Remote:
    def __init__(self, rel_path="updatefiles/pubmed26n1550.xml.gz", size=152968889, mtime=1785082407):
        self.rel_path = rel_path
        self.size = size
        self.mtime = mtime
        self.extra = False


class Spec:
    source_id = "pubmed"


MD5 = ("TransportError: /data/staging/pubmed/updatefiles/pubmed26n1550.xml.gz: md5 sidecar mismatch "
       "(expected 8d60a22b9fa25c7d37b01d13edfdfa15, got 0e6d2…)")


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE fetch_files (source_id TEXT, rel_path TEXT, "
              "endpoint_url TEXT, remote_size INTEGER, remote_mtime INTEGER, "
              "local_path TEXT, bytes_fetched INTEGER, checksum_declared TEXT, "
              "checksum_algo TEXT, blake2 TEXT, fetch_done INTEGER DEFAULT 0, "
              "fetch_version INTEGER DEFAULT 0, profile TEXT, error TEXT, "
              "retries INTEGER DEFAULT 0, extra INTEGER DEFAULT 0, "
              "updated_at INTEGER, PRIMARY KEY (source_id, rel_path))")
    return c


def _fail(con, f, at, times=1):
    for _ in range(times):
        engine._upsert_failed(con, "pubmed", f, "ftp.ncbi.nlm.nih.gov/pubmed/updatefiles", 0, "full", MD5)
    con.execute("UPDATE fetch_files SET updated_at=? WHERE rel_path=?", (int(at), f.rel_path))


def test_the_schedule_is_the_operators():
    waits = [engine.retry_wait_s(n) for n in range(0, 10)]
    assert waits == [0, 60, 300, 600, 1800, 3600, 7200, 7200, 7200, 7200]


@pytest.mark.parametrize("failures,wait", [(1, 60), (2, 300), (3, 600), (4, 1800), (5, 3600), (6, 7200),
                                           (11, 7200)])
def test_a_failed_file_waits_its_backoff_then_is_tried(con, failures, wait):
    f = Remote()
    _fail(con, f, at=1_000_000, times=failures)
    assert engine._backing_off(con, Spec, f, now=1_000_000 + wait - 1)
    assert not engine._backing_off(con, Spec, f, now=1_000_000 + wait)


def test_a_file_never_failed_and_one_done_never_wait(con):
    f = Remote()
    assert not engine._backing_off(con, Spec, f, now=0)       # no row
    engine._upsert_aborted(con, "pubmed", f, "ftp", 1234, "full")
    assert not engine._backing_off(con, Spec, f, now=0)       # a clean stop adds no attempt
    _fail(con, f, at=1_000_000)
    con.execute("UPDATE fetch_files SET fetch_done=1")
    assert not engine._backing_off(con, Spec, f, now=1_000_001)


def test_another_file_from_the_origin_is_tried_at_once(con):
    f = Remote()
    _fail(con, f, at=1_000_000, times=6)
    republished = Remote(size=f.size + 1)
    assert not engine._backing_off(con, Spec, republished, now=1_000_001)


def test_the_selection_counts_a_waiting_file_and_does_not_try_it():
    src = open(engine.__file__, encoding="utf-8").read()
    assert "waiting = {f.rel_path for f in files if f.rel_path in pending and _backing_off(con, spec, f)}" in src
    assert "pending -= waiting" in src
    assert '"waiting": len(waiting)' in src and '"waiting": 0' in src
