"""tests/hold/test_write_lock.py — the one writer per collection (07 §7.7):
`state/lock`, taken by the node for every collection it runs and by a
`hold` command that writes, or exit 3 naming the holder — 2 for the four
whose later retry is not the answer; `node pause <collection>` makes the
worker release it, and waits for that, bounded; a crash leaves no stale lock."""
import contextlib
import inspect
import io
import json
import os
import threading
import time

import pytest

from app import collections as collections_mod, louise, node_state, worker
from app.hold import deposit, lock as lock_mod
from tests.deposit.test_deposit_identity import ENCODER_URL, make, node  # noqa: F401


# ---- the lock itself --------------------------------------------------------

def test_a_second_taker_is_refused_and_told_who_holds_it(tmp_path):
    state = tmp_path / "c" / "state"
    held = lock_mod.take(state, "c", "hold records build")
    assert held.held and lock_mod.lock_path(state).exists()
    with pytest.raises(lock_mod.Held) as e:
        lock_mod.take(state, "c", "hold vectors build")
    assert e.value.exit_code == 3 and e.value.holder["what"] == "hold records build"
    assert e.value.holder["pid"] == os.getpid() and "retry when it is done" in str(e.value)
    assert lock_mod.holder(state)["what"] == "hold records build"
    held.release()
    assert not held.held and lock_mod.holder(state) is None
    with lock_mod.take(state, "c", "hold vectors build") as again:
        assert again.held
    assert lock_mod.holder(state) is None


def test_the_four_exit_2_and_say_what_releases_the_node(tmp_path):
    state = tmp_path / "c" / "state"
    with lock_mod.take(state, "c", "the node (worker)"):
        with pytest.raises(lock_mod.Held) as e:
            lock_mod.take(state, "c", "hold collection forget", exit_code=2)
    assert e.value.exit_code == 2 and "node pause <collection>" in str(e.value)


def test_a_holder_that_dies_leaves_no_stale_lock(tmp_path):
    """The kernel drops a flock with its holder's descriptor: a crashed
    command cannot leave a collection locked, only its last words."""
    state = tmp_path / "c" / "state"
    crashed = lock_mod.take(state, "c", "hold records build")
    crashed._f.close()                      # the process died mid-write
    with lock_mod.take(state, "c", "hold vectors build") as taken:
        assert taken.held


def test_a_wait_for_the_worker_ends_with_its_pause_and_never_waits_for_another_writer(tmp_path):
    state = tmp_path / "c" / "state"
    with lock_mod.take(state, "c", lock_mod.WORKER):
        t0 = time.monotonic()
        who = lock_mod.wait_while_held(state, 30, still=lambda: False)
        assert who["what"] == lock_mod.WORKER and time.monotonic() - t0 < 2, "withdrawn: at once"
        with pytest.raises(lock_mod.Held) as e:
            lock_mod.take_waiting(state, "c", "hold collection forget", wait_s=30,
                                  still=lambda: False, exit_code=2)
        assert e.value.exit_code == 2 and e.value.waited is None, "not paused: refused as before"
    with lock_mod.take(state, "c", "hold records build"):
        t0 = time.monotonic()
        assert lock_mod.wait_while_held(state, 30) is None and time.monotonic() - t0 < 2
        with pytest.raises(lock_mod.Held) as e:
            lock_mod.take_waiting(state, "c", "hold vectors build", wait_s=30, still=lambda: True)
        assert e.value.holder["what"] == "hold records build" and time.monotonic() - t0 < 2


# ---- the node holds the lock of every collection it runs ----------------------

def test_the_worker_takes_the_lock_of_a_collection_it_runs_and_lets_go_when_paused(node, monkeypatch):  # noqa: F811
    make(node, "alice")
    c = collections_mod.by_name("alice")
    monkeypatch.setattr(worker, "_locks", {})
    monkeypatch.setattr(worker, "_lock_nagged", {})
    state = lock_mod.state_dir(node, "alice")
    active = worker._hold_locks([c], set())
    assert [x.name for x in active] == ["alice"]
    assert lock_mod.holder(state)["what"] == "the node (worker)"
    with pytest.raises(lock_mod.Held):
        lock_mod.take(state, "alice", "hold records build")
    # paused by the operator: released after the unit, not run this round
    retired: list[str] = []
    monkeypatch.setattr(worker, "retire_collection",
                        lambda name, timeout=60.0: retired.append(name))
    assert worker._hold_locks([c], {"alice"}) == [] and retired == ["alice"]
    assert lock_mod.holder(state) is None
    # resumed: taken again
    assert [x.name for x in worker._hold_locks([c], set())] == ["alice"]
    worker._release_locks()
    assert lock_mod.holder(state) is None


def test_a_collection_another_writer_holds_is_not_run_this_round(node, monkeypatch):  # noqa: F811
    make(node, "alice")
    c = collections_mod.by_name("alice")
    monkeypatch.setattr(worker, "_locks", {})
    monkeypatch.setattr(worker, "_lock_nagged", {})
    with lock_mod.take(lock_mod.state_dir(node, "alice"), "alice", "hold records build"):
        assert worker._hold_locks([c], set()) == []
    assert [x.name for x in worker._hold_locks([c], set())] == ["alice"]
    worker._release_locks()


def test_retire_collection_releases_the_lock_too(node, monkeypatch):  # noqa: F811
    make(node, "alice")
    c = collections_mod.by_name("alice")
    monkeypatch.setattr(worker, "_locks", {})
    worker._hold_locks([c], set())
    worker.retire_collection("alice", timeout=1)
    assert lock_mod.holder(lock_mod.state_dir(node, "alice")) is None


# ---- node pause <collection> ----------------------------------------------------

def test_node_pause_and_resume_one_collection_persist_in_the_node_state(node):  # noqa: F811
    assert node_state.paused_collections() == set()
    assert louise.main(["node", "pause", "alice"]) == 0
    assert node_state.paused_collections() == {"alice"}
    assert louise.main(["node", "pause", "bob"]) == 0
    assert node_state.paused_collections() == {"alice", "bob"}
    assert louise.main(["node", "resume", "alice"]) == 0
    assert node_state.paused_collections() == {"bob"}


def test_node_pause_without_a_collection_is_the_mode(node):  # noqa: F811
    assert louise.main(["node", "pause"]) == 0
    assert node_state.worker_state() == "paused"
    assert louise.main(["node", "pause", "--seed-only"]) == 0
    assert node_state.worker_state() == "seed-only"
    assert louise.main(["node", "resume"]) == 0
    assert node_state.worker_state() == "running"


# ---- the pause waits for what it asks (docs/notes/manual-steps.md, 5) -------------
#
# München, 2026-11-28: `node pause gutenberg` returned 0, the jobs POSTed 1-5 s
# later were refused at the carry, and the worker let go 80 s after the pause.

@contextlib.contextmanager
def _worker_running(node, name, unit_s, monkeypatch):  # noqa: F811
    """The worker's own `_hold_locks`, a round every 50 ms on a thread, its
    retirement of a paused collection one unit of `unit_s`: the lock taken
    before this returns and let go only once a round has seen the pause."""
    c = collections_mod.by_name(name)
    monkeypatch.setattr(worker, "_locks", {})
    monkeypatch.setattr(worker, "_lock_nagged", {})
    monkeypatch.setattr(worker, "retire_collection", lambda n, timeout=60.0: time.sleep(unit_s))
    worker._hold_locks([c], node_state.paused_collections())
    stop = threading.Event()

    def rounds():
        while not stop.is_set():
            worker._hold_locks([c], node_state.paused_collections())
            stop.wait(0.05)

    t = threading.Thread(target=rounds, daemon=True)
    t.start()
    try:
        yield c
    finally:
        stop.set()
        t.join(timeout=10)
        worker._release_locks()


WAITING = "waiting for the worker to finish alice's current unit"


def test_the_pause_bound_covers_the_workers_retirement_and_a_round():
    retire = inspect.signature(worker.retire_collection).parameters["timeout"].default
    assert louise.PAUSE_WAIT_S - retire >= 60, "the retirement's deadline, and a round to reach it"


def test_node_pause_waits_until_the_worker_has_let_go(node, monkeypatch, capsys):  # noqa: F811
    make(node, "alice")
    state = lock_mod.state_dir(node, "alice")
    with _worker_running(node, "alice", 0.6, monkeypatch):
        assert lock_mod.holder(state)["what"] == lock_mod.WORKER
        t0 = time.monotonic()
        assert louise.main(["node", "pause", "alice", "--wait", "30"]) == 0
        took = time.monotonic() - t0
        assert lock_mod.holder(state) is None, "it returned once the lock was free"
    assert 0.5 <= took < 10
    cap = capsys.readouterr()
    assert cap.err.count(WAITING) == 1, "said once"
    out = json.loads(cap.out)
    assert out["paused"] == ["alice"] and out["applied"] is True and out["waited_s"] >= 0.5


def test_node_pause_past_its_bound_exits_3_and_the_pause_stays_recorded(node, monkeypatch, capsys):  # noqa: F811
    make(node, "alice")
    state = lock_mod.state_dir(node, "alice")
    with lock_mod.take(state, "alice", lock_mod.WORKER):          # a unit longer than the bound
        t0 = time.monotonic()
        assert louise.main(["node", "pause", "alice", "--wait", "0.5"]) == 3
        assert 0.5 <= time.monotonic() - t0 < 5
        cap = capsys.readouterr()
        assert "recorded and still being applied" in cap.err and cap.err.count(WAITING) == 1
        assert json.loads(cap.out)["applied"] is False
        monkeypatch.setattr(louise, "PAUSE_WAIT_S", 0.3)              # the default bound is the same
        assert louise.main(["node", "pause", "alice"]) == 3
    assert node_state.paused_collections() == {"alice"}, "recorded either way"
    assert "after 0.3 s" in capsys.readouterr().err


def test_node_pause_wait_0_and_a_collection_no_worker_runs_return_at_once(node, capsys):  # noqa: F811
    make(node, "alice")
    state = lock_mod.state_dir(node, "alice")
    with lock_mod.take(state, "alice", lock_mod.WORKER):
        t0 = time.monotonic()
        assert louise.main(["node", "pause", "alice", "--wait", "0"]) == 0
        assert time.monotonic() - t0 < 2
    cap = capsys.readouterr()
    assert json.loads(cap.out) == {"paused": ["alice"]} and WAITING not in cap.err, "today's pause"
    # held by another writer, or by nobody: no worker runs it, nothing to wait for
    with lock_mod.take(state, "alice", "hold records build"):
        t0 = time.monotonic()
        assert louise.main(["node", "pause", "alice", "--wait", "30"]) == 0
        assert time.monotonic() - t0 < 2
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert louise.main(["node", "pause", "bob", "--wait", "30"]) == 0


def test_a_writer_after_the_pause_waits_for_the_workers_release_and_runs(node, monkeypatch, capsys):  # noqa: F811
    """forget, one of 07 §7.7's four: the pause recorded, the worker still in
    its unit, the command waits for the release instead of refusing."""
    make(node, "alice")
    state = lock_mod.state_dir(node, "alice")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with _worker_running(node, "alice", 1.0, monkeypatch):
        assert louise.main(["node", "pause", "alice", "--wait", "0"]) == 0
        assert lock_mod.holder(state)["what"] == lock_mod.WORKER, "the worker still finishes its unit"
        t0 = time.monotonic()
        assert louise.main(["hold", "collection", "forget", "alice", "--root", str(node),
                            "--here", "--yes"]) == 0
        assert time.monotonic() - t0 < 10
    assert not (node / "alice").exists()
    assert capsys.readouterr().err.count(WAITING) == 1


def test_a_writer_waits_only_for_the_worker_of_a_paused_collection(node, monkeypatch, capsys):  # noqa: F811
    make(node, "alice")
    state = lock_mod.state_dir(node, "alice")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    forget = ["hold", "collection", "forget", "alice", "--root", str(node), "--here", "--yes"]
    monkeypatch.setattr(louise, "PAUSE_WAIT_S", 30.0)
    with lock_mod.take(state, "alice", lock_mod.WORKER):             # not paused: refused at once
        t0 = time.monotonic()
        assert louise.main(forget) == 2 and time.monotonic() - t0 < 5
    assert WAITING not in capsys.readouterr().err
    node_state.set_paused("alice", True, by="test")
    with lock_mod.take(state, "alice", "hold records build"):       # another writer: at once
        t0 = time.monotonic()
        assert louise.main(forget) == 2 and time.monotonic() - t0 < 5
    assert WAITING not in capsys.readouterr().err
    monkeypatch.setattr(louise, "PAUSE_WAIT_S", 0.4)
    with lock_mod.take(state, "alice", lock_mod.WORKER):             # the worker past the bound
        t0 = time.monotonic()
        assert louise.main(forget) == 3, "the pause still being applied: a retry is the answer"
        assert 0.4 <= time.monotonic() - t0 < 5
    err = capsys.readouterr().err
    assert err.count(WAITING) == 1 and "still being applied" in err
    assert deposit.held_file(node, "alice").exists(), "nothing was removed"


# ---- the commands -------------------------------------------------------------------

def test_a_writing_command_exits_3_naming_the_holder_and_forget_exits_2(node, monkeypatch, capsys):  # noqa: F811
    make(node, "alice")
    state = lock_mod.state_dir(node, "alice")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with lock_mod.take(state, "alice", "the node (worker)"):
        assert louise.main(["hold", "records", "build", "alice", "--root", str(node)]) == 3
        assert "held by the node (worker)" in capsys.readouterr().err
        assert louise.main(["hold", "collection", "forget", "alice", "--root", str(node),
                            "--here", "--yes"]) == 2
        assert "node pause <collection>" in capsys.readouterr().err
        assert deposit.held_file(node, "alice").exists(), "nothing was removed"
    # the node released it: forget runs, and releases its own lock at the end
    assert louise.main(["hold", "collection", "forget", "alice", "--root", str(node),
                        "--here", "--yes"]) == 0
    assert not (node / "alice").exists()


def test_a_command_releases_its_lock_when_it_ends(node, monkeypatch):  # noqa: F811
    make(node, "alice")
    state = lock_mod.state_dir(node, "alice")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    # dry run takes nothing; a real --here forget takes it and `main` releases
    louise.main(["hold", "collection", "forget", "alice", "--root", str(node), "--dry-run"])
    assert lock_mod.holder(state) is None and louise._cli_locks == []
    assert louise._cli_locks == []


def test_show_takes_no_lock(node, capsys):  # noqa: F811
    make(node, "alice")
    with lock_mod.take(lock_mod.state_dir(node, "alice"), "alice", "the node (worker)"):
        assert louise.main(["hold", "collection", "show", "alice", "--root", str(node)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["collection_id"].startswith("sha256:")
