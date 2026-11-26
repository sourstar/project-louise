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


def _collection_for(con, tmp_path):
    return collections_mod.Collection(
        name="test", plugin="gutenberg", profile=None, embed_mode_default=None,
        corpus_dir=tmp_path / "corpus", manifest_path=tmp_path / "manifest.db",
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


def test_a_boot_clock_that_reads_a_claim_in_the_future_gives_way_to_the_wall_clock(con, monkeypatch):
    """THE FOURTH READ: a claim's "up" ahead of the reader's CLOCK_BOOTTIME (a
    time namespace, a CRIU restore) read a day-old claim as held."""
    monkeypatch.setattr(claim_mod, "boot_id", lambda: "this-boot")
    monkeypatch.setattr(claim_mod, "uptime", lambda: 100.0)
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps(
        {"pid": 8, "holder": "8@1", "ts": time.time() - 86_400, "source_id": "eurlex", "boot": "this-boot",
         "up": 50_000.0}))
    assert claim_mod.busy_for(con) is None, "a day old by the wall clock"


def test_the_claims_keepalive_outlives_a_connection_that_does_not_open(tmp_path, monkeypatch):
    """THE FIFTH READ: the keepalive's connect sat outside its try — one that
    raised ended the thread, and the claim went stale under a live pass."""
    import sqlite3
    from app.acquire import engine
    opens, calls = [], []

    from app import db as app_db
    real_connect = app_db.connect

    def connect(collection, **k):
        opens.append(1)
        if len(opens) == 1:
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(collection)
    monkeypatch.setattr(engine.claim_mod, "refresh_claim", lambda con, sid, last: calls.append(1) or last)
    monkeypatch.setattr(app_db, "connect", connect)
    waits = iter([False, False, True])
    keep = engine._ClaimKeepalive.__new__(engine._ClaimKeepalive)
    keep._source_id = "eurlex"
    keep._collection = collections_mod.Collection(
        name="eurlex", plugin="eurlex", profile=None, embed_mode_default=None, corpus_dir=tmp_path / "c",
        manifest_path=tmp_path / "m.db", fts_dir=tmp_path / "f", lance_dir=tmp_path / "l")
    keep._done = type("Done", (), {"wait": lambda self, s: next(waits)})()
    keep._run()
    assert opens == [1, 1] and calls == [1], "opened again at the next tick, and renewed"


def test_the_claim_is_a_lock_a_live_holder_keeps_and_a_dead_one_drops(con):
    """THE SIXTH READ: a live pass whose renewals failed past 300 s could have
    its claim taken, and both fetched one pending list. The claim is an flock
    beside the manifest: a live holder's is never taken — its row stale or
    gone — and the kernel drops a dead holder's at once."""
    import fcntl as _fcntl
    path = claim_mod.lock_path(con)
    assert path is not None and path.name == "acquire.lock"
    other = os.open(path, os.O_RDWR | os.O_CREAT)        # another holder's open description
    _fcntl.flock(other, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    try:
        db.set_control(con, claim_mod.CLAIM_KEY, "")      # its row given back, or never renewed
        with pytest.raises(claim_mod.AcquisitionBusy) as e:
            with claim_mod.acquisition_claim(con, "eurlex"):
                pass
        assert "its lock is held" in str(e.value)
    finally:
        os.close(other)                                   # its holder dies: the kernel drops the lock
    with claim_mod.acquisition_claim(con, "eurlex"):
        assert claim_mod.read_claim(con)["pid"] == os.getpid()
        nested = os.open(path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                _fcntl.flock(nested, _fcntl.LOCK_EX | _fcntl.LOCK_NB)   # held while the pass runs
        finally:
            os.close(nested)
    probe = os.open(path, os.O_RDWR)
    try:
        _fcntl.flock(probe, _fcntl.LOCK_EX | _fcntl.LOCK_NB)            # and let go after
    finally:
        os.close(probe)


def test_a_dead_holder_that_said_it_locked_is_taken_over_at_once(con):
    """THE SEVENTH READ, run: a holder SIGKILLed in the same boot — the kernel
    dropped its lock, its row young — refused its successor for 305 s. A row
    whose writer held the lock is dead once the lock is ours; a row that says
    nothing of a lock (a process from before it) keeps the age rule."""
    other = {"pid": 8, "holder": "8@4026533985", "source_id": "eurlex", "ts": time.time(),
             "boot": claim_mod.boot_id(), "up": claim_mod.uptime()}
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps({**other, "lock": True}))
    with claim_mod.acquisition_claim(con, "eurlex"):
        mine = claim_mod.read_claim(con)
        assert mine["pid"] == os.getpid() and mine["lock"] is True, "taken at once, and said locked"
    db.set_control(con, claim_mod.CLAIM_KEY, json.dumps(other))
    with pytest.raises(claim_mod.AcquisitionBusy):
        with claim_mod.acquisition_claim(con, "eurlex"):
            pass


def test_a_renewal_keeps_saying_the_lock_is_held(con):
    with claim_mod.acquisition_claim(con, "eurlex"):
        claim_mod.refresh_claim(con, "eurlex", 0.0)
        assert claim_mod.read_claim(con)["lock"] is True
    db.set_control(con, claim_mod.CLAIM_KEY, "")
    claim_mod.refresh_claim(con, "eurlex", 0.0)          # nothing of ours: nothing written
    assert claim_mod.read_claim(con) is None


def test_the_lock_is_let_go_whatever_the_give_back_raises(con, monkeypatch):
    """THE SEVENTH READ, run: an error but OperationalError in the give-back
    skipped the unlock, and every later fetch of the process was refused."""
    import fcntl as _fcntl
    import sqlite3

    def broken(con_, value):
        raise sqlite3.DatabaseError("database disk image is malformed")
    monkeypatch.setattr(claim_mod, "_set_if_mine", broken)
    with claim_mod.acquisition_claim(con, "eurlex"):
        pass
    probe = os.open(claim_mod.lock_path(con), os.O_RDWR)
    try:
        _fcntl.flock(probe, _fcntl.LOCK_EX | _fcntl.LOCK_NB)      # free
    finally:
        os.close(probe)
    assert not claim_mod._held_fds

    def interrupted(con_, value):
        raise KeyboardInterrupt
    monkeypatch.setattr(claim_mod, "_set_if_mine", interrupted)
    with pytest.raises(KeyboardInterrupt):
        with claim_mod.acquisition_claim(con, "eurlex"):
            pass
    probe = os.open(claim_mod.lock_path(con), os.O_RDWR)
    try:
        _fcntl.flock(probe, _fcntl.LOCK_EX | _fcntl.LOCK_NB)      # free even then
    finally:
        os.close(probe)


def test_another_process_fetching_is_a_deferral_for_the_command_line():
    """THE SEVENTH READ: `hold records fetch` refused by the worker's pass fell
    through to a traceback and exit 1, and `supervise-pass.sh` stopped it as a
    defect."""
    from app import louise
    assert louise._is_deferral(claim_mod.AcquisitionBusy("eurlex: acquisition already running in 8@1"))


def test_a_busy_write_in_a_later_torrent_tells_what_the_earlier_ones_landed(con, tmp_path, monkeypatch):
    """THE SEVENTH READ: a pass over several torrents named in `e.fetched` only
    the torrent that hit the busy write; what the earlier ones landed got no scan."""
    import sqlite3
    from app.acquire import engine
    from app.acquire.spec import AcquisitionSpec, Endpoint, Filter, Transport
    spec = AcquisitionSpec(source_id="t", transport=Transport.TORRENT,
                           endpoints=(Endpoint(transport=Transport.TORRENT, url="magnet:?a"),
                                      Endpoint(transport=Transport.TORRENT, url="magnet:?b")),
                           layout="single-file", profiles={"demo": Filter()}, estimated_bytes={"demo": 1})
    monkeypatch.setattr(engine, "effective_spec", lambda c, s, p=None, stop=None: s)
    parts = iter([{"fetched": 4}, "busy"])

    def claimed(con_, c, sp, profile, stop, priority, until):
        got = next(parts)
        if got == "busy":
            e = sqlite3.OperationalError("database is locked")
            e.fetched = 1
            raise e
        return got
    monkeypatch.setattr(engine, "_run_acquisition_claimed", claimed)
    with pytest.raises(sqlite3.OperationalError) as e:
        engine._run_acquisition(con, _collection_for(con, tmp_path), spec, "demo", None, engine.Priority.BACKFILL)
    assert e.value.fetched == 5


def test_a_writer_child_keeps_the_lock_after_its_holder_is_killed(con):
    """THE SEVENTH READ, run: a holder SIGKILLed while its rsync wrote a
    `.part` in place left the lock free, and a successor could start a second
    rsync on the same file. The writer is handed the lock (`passing_locks`):
    it lives as long as the last writer."""
    import fcntl as _fcntl
    import signal
    import subprocess
    import sys
    import textwrap
    from pathlib import Path
    manifest = con.execute("PRAGMA database_list").fetchone()[2]
    script = textwrap.dedent(f"""
        import sqlite3, subprocess, sys, time
        from app.acquire import claim
        con = sqlite3.connect({manifest!r}, isolation_level=None)
        with claim.acquisition_claim(con, "eurlex"):
            with claim.passing_locks() as fds:
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                         pass_fds=fds, start_new_session=True)
            print(child.pid, flush=True)
            time.sleep(60)
    """)
    holder = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True,
                              cwd=Path(__file__).resolve().parents[2])
    child = None
    probe = os.open(claim_mod.lock_path(con), os.O_RDWR | os.O_CREAT)
    try:
        child = int(holder.stdout.readline())
        holder.kill()                                       # the holder dies, its writer lives
        holder.wait(10)
        with pytest.raises(BlockingIOError):
            _fcntl.flock(probe, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        os.kill(child, signal.SIGKILL)                       # the last writer ends: the lock with it
        child, deadline = None, time.time() + 10
        while True:
            try:
                _fcntl.flock(probe, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                break
            except BlockingIOError:
                assert time.time() < deadline, "the lock outlived its last writer"
                time.sleep(0.05)
    finally:
        os.close(probe)
        if holder.poll() is None:
            holder.kill()
        if child is not None:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
