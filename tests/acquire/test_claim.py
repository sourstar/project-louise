"""Advisory acquisition claim — prevents the CLI and the worker from fetching
the same collection concurrently, without ever being able to wedge acquisition
permanently.
"""
import json
import os
import time

import pytest

from app import collections as collections_mod, db
from app.acquire import claim as claim_mod


def _collection(tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    return collections_mod.Collection(
        name="test", plugin="gutenberg", profile=None, embed_mode_default=None,
        corpus_dir=d, manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb")


@pytest.fixture
def con(tmp_path):
    return db.connect(_collection(tmp_path))


def test_claim_is_taken_and_released(con):
    assert claim_mod.read_claim(con) is None
    with claim_mod.acquisition_claim(con, "test"):
        held = claim_mod.read_claim(con)
        assert held["pid"] == os.getpid()
        assert held["source_id"] == "test"
    assert not claim_mod.read_claim(con)


def test_claim_is_released_even_when_the_body_raises(con):
    with pytest.raises(RuntimeError, match="boom"):
        with claim_mod.acquisition_claim(con, "test"):
            raise RuntimeError("boom")
    assert not claim_mod.read_claim(con), "a crash must not leave the claim held"


def test_a_live_claim_from_another_process_is_refused(con):
    db.set_control(con, claim_mod.CLAIM_KEY,
                    json.dumps({"pid": os.getpid() + 99_999, "ts": time.time(),
                                 "source_id": "test"}))
    with pytest.raises(claim_mod.AcquisitionBusy, match="already running"):
        with claim_mod.acquisition_claim(con, "test"):
            pass


def test_a_stale_claim_is_taken_over(con):
    """A hard-killed holder must never wedge acquisition forever."""
    db.set_control(con, claim_mod.CLAIM_KEY,
                    json.dumps({"pid": os.getpid() + 99_999,
                                 "ts": time.time() - claim_mod.STALE_AFTER_SECONDS - 1,
                                 "source_id": "test"}))
    with claim_mod.acquisition_claim(con, "test"):
        assert claim_mod.read_claim(con)["pid"] == os.getpid()


def test_reentrant_claim_by_the_same_pid_is_allowed(con):
    """The same process re-entering (e.g. worker retrying) is not a conflict."""
    db.set_control(con, claim_mod.CLAIM_KEY,
                    json.dumps({"pid": os.getpid(), "holder": claim_mod._me(),
                                "ts": time.time(), "source_id": "test"}))
    with claim_mod.acquisition_claim(con, "test"):
        assert claim_mod.read_claim(con)["pid"] == os.getpid()


def test_force_overrides_a_live_claim(con):
    db.set_control(con, claim_mod.CLAIM_KEY,
                    json.dumps({"pid": os.getpid() + 99_999, "ts": time.time(),
                                 "source_id": "test"}))
    with claim_mod.acquisition_claim(con, "test", force=True):
        assert claim_mod.read_claim(con)["pid"] == os.getpid()


def test_corrupt_claim_value_is_treated_as_absent(con):
    db.set_control(con, claim_mod.CLAIM_KEY, "not json{{{")
    with claim_mod.acquisition_claim(con, "test"):
        assert claim_mod.read_claim(con)["pid"] == os.getpid()


def test_refresh_keeps_a_long_run_from_going_stale(con, monkeypatch):
    with claim_mod.acquisition_claim(con, "test"):
        original = claim_mod.read_claim(con)["ts"]
        # too soon: no write
        assert claim_mod.refresh_claim(con, "test", time.time()) is not None
        assert claim_mod.read_claim(con)["ts"] == original
        # long enough ago: refreshed
        claim_mod.refresh_claim(con, "test", time.time() - 3600)
        assert claim_mod.read_claim(con)["ts"] >= original


def test_refresh_does_not_steal_another_processes_claim(con):
    other = {"pid": os.getpid() + 99_999, "ts": 12345.0, "source_id": "test"}
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps(other))
    claim_mod.refresh_claim(con, "test", time.time() - 3600)
    assert claim_mod.read_claim(con) == other


def test_the_same_pid_in_another_container_is_another_process(con, monkeypatch):
    """The worker and a supervised `hold records fetch` pass can each be pid 1
    in their own container: the claim names the PID namespace too
    (`model_pins.process_tag`), so the second refuses instead of reading the
    first's claim as its own and fetching the same files beside it."""
    from app import model_pins
    db.set_control(con, claim_mod.CLAIM_KEY,
                   json.dumps({"pid": os.getpid(), "holder": f"pid {os.getpid()}@4026530001",
                               "ts": time.time(), "source_id": "test"}))
    monkeypatch.setattr(model_pins, "process_tag", lambda: f"pid {os.getpid()}@4026539999")
    with pytest.raises(claim_mod.AcquisitionBusy, match="4026530001"):
        with claim_mod.acquisition_claim(con, "test"):
            pass
    monkeypatch.setattr(model_pins, "process_tag", lambda: f"pid {os.getpid()}@4026530001")
    with claim_mod.acquisition_claim(con, "test"):        # its own: re-entrant, as before
        assert claim_mod.read_claim(con)["holder"] == f"pid {os.getpid()}@4026530001"
    assert claim_mod.read_claim(con) is None


def test_a_claim_from_before_the_tag_is_read_by_its_pid(con, monkeypatch):
    """A claim written before the holder field (pid only) still counts as
    this process's where the tag is the pid alone (no namespace readable)."""
    from app import model_pins
    monkeypatch.setattr(model_pins, "process_tag", lambda: f"pid {os.getpid()}")
    db.set_control(con, claim_mod.CLAIM_KEY,
                   json.dumps({"pid": os.getpid(), "ts": time.time(), "source_id": "test"}))
    with claim_mod.acquisition_claim(con, "test"):
        pass


# ---- a dead predecessor's claim (München, 2026-11-26, D6) ----

def test_a_claim_made_in_another_boot_is_dead_whatever_its_age(con, monkeypatch):
    """The worker a hardware reset killed at 09:00:20Z held the claim, 179 s
    old; its successor's startup fetch was refused for the 300 s staleness."""
    now = time.time()
    dead = {"pid": 8, "holder": "8@4026533985", "ts": now - 179, "source_id": "eurlex"}
    monkeypatch.setattr(claim_mod, "boot_id", lambda: "boot-after-the-reset")
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps({**dead, "boot": "boot-before-the-reset"}))
    assert claim_mod.busy_for(con) is None
    with claim_mod.acquisition_claim(con, "eurlex"):
        mine = claim_mod.read_claim(con)
        assert mine["pid"] == os.getpid() and mine["boot"] == "boot-after-the-reset", "names its boot"
        assert claim_mod.held(mine), "this process's own claim is held (the Library card reads it so)"
    # the same boot: live, and asked again only when it goes stale
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps({**dead, "boot": "boot-after-the-reset"}))
    assert 125 <= claim_mod.busy_for(con) <= 127
    with pytest.raises(claim_mod.AcquisitionBusy) as e:
        with claim_mod.acquisition_claim(con, "eurlex"):
            pass
    assert 125 <= e.value.retry_in <= 127
    # a claim that names no boot (made before this), or a boot unread: its age alone decides
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps(dead))
    assert claim_mod.busy_for(con) is not None
    monkeypatch.setattr(claim_mod, "boot_id", lambda: None)
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps({**dead, "boot": "boot-before-the-reset"}))
    assert claim_mod.busy_for(con) is not None


def test_the_boot_is_read_from_the_kernel():
    got = claim_mod.boot_id()
    assert got is None or (len(got) >= 32 and got == claim_mod.boot_id())


def test_within_one_boot_a_claims_age_is_the_boots_own_clock(con, monkeypatch):
    """THE THIRD READ, run: after the wall clock was stepped back more than two
    refreshes, a `hold records fetch` beside the worker took its LIVE claim,
    and both fetched the same pending list. Within one boot the age is
    CLOCK_BOOTTIME's, which no step moves."""
    up = [10_000.0]
    monkeypatch.setattr(claim_mod, "uptime", lambda: up[0])
    monkeypatch.setattr(claim_mod, "boot_id", lambda: "this-boot")
    now = time.time()
    live = {"pid": 8, "holder": "8@4026533985", "source_id": "eurlex", "boot": "this-boot", "up": up[0] - 30}
    for ts in (now + 3600, now - 3600):                  # the wall clock stepped back, then forward, an hour
        db.set_control(con, claim_mod.CLAIM_KEY, json.dumps({**live, "ts": ts}))
        assert 270 <= claim_mod.busy_for(con) <= 276, "30 s old on the boot's clock, whatever the wall says"
    up[0] += 300                                         # its holder died: 330 s on the boot's clock
    assert claim_mod.busy_for(con) is None
    # a claim that names no boot is dated by the wall clock, as before
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps({"pid": 8, "holder": "8@4026533985", "ts": now - 30,
                                                          "source_id": "eurlex"}))
    assert claim_mod.busy_for(con) is not None
    with claim_mod.acquisition_claim(con, "eurlex", force=True):
        mine = claim_mod.read_claim(con)
        assert mine["up"] == up[0] and mine["boot"] == "this-boot"


def test_a_claim_is_renewed_and_given_back_only_while_it_is_its_holders(con):
    """THE THIRD READ: read, then written apart, a holder could overwrite the
    claim another process had just taken."""
    with claim_mod.acquisition_claim(con, "eurlex"):
        theirs = json.dumps({"pid": 9, "holder": "9@1", "ts": time.time(), "source_id": "eurlex"})
        db.set_control(con, claim_mod.CLAIM_KEY, theirs)              # taken meanwhile
        claim_mod.refresh_claim(con, "eurlex", last_refresh=0.0)
        assert db.get_control(con, claim_mod.CLAIM_KEY) == theirs, "not renewed over another's"
    assert db.get_control(con, claim_mod.CLAIM_KEY) == theirs, "not given back over another's"
    db.set_control(con, claim_mod.CLAIM_KEY, "")
    claim_mod.refresh_claim(con, "eurlex", last_refresh=0.0)          # nothing to renew: nothing written
    assert db.get_control(con, claim_mod.CLAIM_KEY) == ""


def test_a_claim_is_taken_in_one_transaction_and_inside_a_callers_own(con):
    with claim_mod.acquisition_claim(con, "eurlex"):
        assert not con.in_transaction, "committed: the claim is visible to others at once"
        assert claim_mod.read_claim(con)["pid"] == os.getpid()
    con.execute("BEGIN")
    try:
        with claim_mod.acquisition_claim(con, "eurlex"):
            assert con.in_transaction, "the caller's transaction is the caller's"
    finally:
        con.execute("COMMIT")


def test_the_claims_keepalive_outlives_a_busy_manifest(tmp_path, monkeypatch):
    """THE THIRD READ: one `database is locked` past the busy timeout ended the
    keepalive thread for the rest of the pass, and the claim read stale under
    a live fetch."""
    import sqlite3
    from app.acquire import engine
    calls = []

    def refresh(con, source_id, last, lost=None):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return last
    monkeypatch.setattr(engine.claim_mod, "refresh_claim", refresh)
    waits = iter([False, False, True])
    keep = engine._ClaimKeepalive.__new__(engine._ClaimKeepalive)
    keep._source_id = "eurlex"
    keep._collection = collections_mod.Collection(
        name="eurlex", plugin="eurlex", profile=None, embed_mode_default=None, corpus_dir=tmp_path / "c",
        manifest_path=tmp_path / "m.db", fts_dir=tmp_path / "f", lance_dir=tmp_path / "l")
    keep._done = type("Done", (), {"wait": lambda self, s: next(waits)})()
    import threading
    keep.lost = threading.Event()
    keep._run()
    assert calls == [1, 1], "asked again after the busy write"



def test_a_live_claim_refuses_without_waiting_for_the_write_lock(con, tmp_path):
    """THE FOURTH READ, run: under BEGIN IMMEDIATE first, a refusal on a
    manifest a scan streams into waited out the busy timeout and came back as
    OperationalError — no retry armed, the fetch lost (D6 again)."""
    import sqlite3
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps(
        {"pid": 8, "holder": "8@4026533985", "ts": time.time(), "source_id": "eurlex"}))
    path = con.execute("PRAGMA database_list").fetchone()[2]
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")                       # a scan holding the write lock
    try:
        t0 = time.monotonic()
        with pytest.raises(claim_mod.AcquisitionBusy):
            with claim_mod.acquisition_claim(con, "eurlex"):
                pass
        assert time.monotonic() - t0 < 2.0, "refused at once, the lock never asked"
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_a_busy_give_back_leaves_the_passes_own_result(con, monkeypatch):
    import sqlite3
    real = claim_mod._set_if_mine

    def busy(c, value):
        if value == "":
            raise sqlite3.OperationalError("database is locked")
        return real(c, value)
    monkeypatch.setattr(claim_mod, "_set_if_mine", busy)
    got = None
    with claim_mod.acquisition_claim(con, "eurlex"):
        got = {"fetched": 7}
    assert got == {"fetched": 7}, "the pass's result stands; the claim goes stale by itself"


def test_a_holder_whose_claim_was_taken_over_is_told_so(con):
    """THE FOURTH READ, run: renewals failed past the claim's staleness,
    another process took it, and the first fetch went on beside it."""
    import threading
    from app.acquire import engine
    lost = threading.Event()
    with claim_mod.acquisition_claim(con, "eurlex"):
        db.set_control(con, claim_mod.CLAIM_KEY, json.dumps(
            {"pid": 9, "holder": "9@1", "ts": time.time(), "source_id": "eurlex", "boot": claim_mod.boot_id(),
             "up": claim_mod.uptime()}))
        claim_mod.refresh_claim(con, "eurlex", last_refresh=0.0, lost=lost)
        assert lost.is_set()
        stop = engine._ClaimStop(None, lost)
        assert stop.is_set(), "the pass's stop: it ends at its next unit"
    lost.clear()
    db.set_control(con, claim_mod.CLAIM_KEY, "")
    claim_mod.refresh_claim(con, "eurlex", last_refresh=0.0, lost=lost)
    assert not lost.is_set(), "given back, taken by nobody: nothing lost"


def test_a_boot_clock_that_reads_a_claim_in_the_future_gives_way_to_the_wall_clock(con, monkeypatch):
    """THE FOURTH READ: a claim's "up" ahead of the reader's CLOCK_BOOTTIME (a
    time namespace, a CRIU restore) read a day-old claim as held."""
    monkeypatch.setattr(claim_mod, "boot_id", lambda: "this-boot")
    monkeypatch.setattr(claim_mod, "uptime", lambda: 100.0)
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps(
        {"pid": 8, "holder": "8@1", "ts": time.time() - 86_400, "source_id": "eurlex", "boot": "this-boot",
         "up": 50_000.0}))
    assert claim_mod.busy_for(con) is None, "a day old by the wall clock"
