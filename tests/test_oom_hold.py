"""tests/test_oom_hold.py — AN OOM KILL IS NOT RELAUNCHED UNTIL ITS CAUSE IS
KNOWN (operator, 2026-11-19, "A"; app/oom_hold.py, app/worker_guard.py): the
first memory kill in the worker's cgroup writes a hold naming what it was
doing; while it is in force no worker starts; a release names the cause or
the diagnostic and is kept. Every other end of the worker is as before."""
import json
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from app import oom_hold, worker_guard


def _cgroup(tmp_path, kills: int = 0) -> Path:
    cg = tmp_path / "cgroup"
    cg.mkdir(exist_ok=True)
    (cg / "memory.events").write_text(f"low 0\nhigh 0\nmax 12\noom 3\noom_kill {kills}\n")
    (cg / "memory.peak").write_text(str(23 * 2 ** 30))
    return cg


def _log() -> list[dict]:
    p = oom_hold.path().parent / oom_hold.LOG_FILE
    return [json.loads(line) for line in p.read_text().splitlines()]


def test_the_first_hold_is_kept_and_a_release_names_the_cause_or_the_diagnostic():
    assert oom_hold.held() is None
    first = oom_hold.hold("worker", {"last_lines": ["eurlex5: 32021R2017 el"]}, now=0)
    again = oom_hold.hold("cgroup", {"in_flight": []}, now=60)
    assert again == first and oom_hold.held()["kind"] == "worker", "the first kill's cause is the one owed"
    for bad in ({}, {"cause": "x", "diagnostic": "y"}, {"cause": ""}):
        with pytest.raises(ValueError, match="in words"):
            oom_hold.release(**bad)
    assert oom_hold.held() is not None, "a refused release lifts nothing"
    out = oom_hold.release(cause="32021R2017 el: 5.25 GB to chunk", now=120)
    assert oom_hold.held() is None and out["released"] == first
    held, released = _log()
    assert held == {"held": first} and released["cause"].startswith("32021R2017") and "diagnostic" not in released
    with pytest.raises(ValueError, match="no OOM hold"):
        oom_hold.release(diagnostic="the peak per byte is logged")


def test_the_hold_is_said_in_plain_words_with_its_release():
    says = oom_hold.describe(oom_hold.hold("cgroup", {"memory_peak": 23 * 2 ** 30}, now=0))
    assert says.startswith("HELD since 1970-01-01T00:00:00Z") and "parse-pool child" in says
    assert "23.0 GB" in says and "louise node release-oom --cause" in says and str(oom_hold.path()) in says


def test_a_kill_is_one_past_the_process_baseline_and_its_handler_runs_once(tmp_path, monkeypatch):
    cg = _cgroup(tmp_path, kills=2)
    monkeypatch.setattr(oom_hold, "CGROUP_DIR", str(cg))
    seen: list[tuple[int, int]] = []
    oom_hold.on_kill(lambda b, a: seen.append((b, a)))
    assert oom_hold.baseline() == 2 and oom_hold.killed() is None, "kills before this process are not its own"
    _cgroup(tmp_path, kills=3)
    assert oom_hold.killed() == (2, 3) and oom_hold.killed() == (2, 3) and seen == [(2, 3)]


def test_where_the_counter_does_not_read_nothing_is_a_kill(tmp_path):
    assert oom_hold.oom_kills() is None and oom_hold.killed() is None
    stop = threading.Event()
    oom_hold.watch(stop, lambda b, a: pytest.fail("no counter, no kill"), every_s=0.01)


def test_the_watcher_calls_the_handler_on_the_first_kill_and_ends(tmp_path, monkeypatch):
    cg = _cgroup(tmp_path)
    monkeypatch.setattr(oom_hold, "CGROUP_DIR", str(cg))
    seen: list[tuple[int, int]] = []
    stop = threading.Event()
    t = threading.Thread(target=oom_hold.watch, args=(stop, lambda b, a: seen.append((b, a))),
                         kwargs={"every_s": 0.01})
    t.start()
    time.sleep(0.05)
    assert seen == [] and t.is_alive()
    _cgroup(tmp_path, kills=1)
    t.join(timeout=5)
    assert not t.is_alive() and seen == [(0, 1)]


# --- the guard: the worker's container command --------------------------------

FAKE_WORKER = r'''
import os, signal, sys
from pathlib import Path
runs, events, plan = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3].split(",")
n = int(runs.read_text()) if runs.exists() else 0
runs.write_text(str(n + 1))
what = plan[min(n, len(plan) - 1)]
print(f"run {n}: parsing eurlex5 32021R2017 el", flush=True)
if what == "oom":           # the kernel's kill: the counter rises, then SIGKILL
    t = events.read_text()
    k = int(t.rsplit("oom_kill ", 1)[1])
    events.write_text(t.replace(f"oom_kill {k}", f"oom_kill {k + 1}"))
    os.kill(os.getpid(), signal.SIGKILL)
if what == "kill":          # a SIGKILL that was not the memory killer's
    os.kill(os.getpid(), signal.SIGKILL)
sys.exit(int(what))
'''


def _guard(tmp_path, monkeypatch, plan: str, *, then=None, stopping=None):
    cg = _cgroup(tmp_path)
    monkeypatch.setattr(oom_hold, "CGROUP_DIR", str(cg))
    runs = tmp_path / "runs"
    cmd = [sys.executable, "-c", FAKE_WORKER, str(runs), str(cg / "memory.events"), plan]
    t = threading.Thread(target=then, daemon=True) if then else None
    if t:
        t.start()
    rc = worker_guard.run(command=cmd, poll_s=0.02, say_s=0.05, stopping=stopping)
    if t:
        t.join(timeout=10)
    return rc, int(runs.read_text()) if runs.exists() else 0


def _when_held(fn, timeout: float = 20.0):
    def go():
        end = time.monotonic() + timeout
        while oom_hold.held() is None and time.monotonic() < end:
            time.sleep(0.01)
        fn()
    return go


def test_a_worker_the_kernel_killed_for_memory_stays_down_until_released(tmp_path, monkeypatch, capfd):
    rel: list[dict] = []
    rc, runs = _guard(tmp_path, monkeypatch, "oom,0",
                      then=_when_held(lambda: (time.sleep(0.2), rel.append(dict(oom_hold.held())),
                                               oom_hold.release(cause="32021R2017 el"))))
    assert rc == 0 and runs == 2, "held, then started again once released"
    (rec,) = rel
    assert rec["kind"] == "worker" and rec["exit"] == "SIGKILL" and rec["oom_kill"] == [0, 1]
    assert rec["memory_peak"] == 23 * 2 ** 30 and rec["last_lines"] == ["run 0: parsing eurlex5 32021R2017 el"]
    err = capfd.readouterr().err
    assert "killed the worker for memory: not relaunched" in err and "HELD since" in err


def test_a_stop_while_held_ends_the_guard_and_starts_nothing(tmp_path, monkeypatch):
    stop = threading.Event()
    rc, runs = _guard(tmp_path, monkeypatch, "oom,0", then=_when_held(stop.set), stopping=stop)
    assert rc == 0 and runs == 1 and oom_hold.held() is not None, "the hold outlives the container"
    rc, runs = _guard(tmp_path, monkeypatch, "0", stopping=stop)
    assert rc == 0 and runs == 1, "a hold in force at start: no worker is started"


def test_a_worker_that_stopped_itself_on_a_kill_in_its_cgroup_is_held(tmp_path, monkeypatch):
    stop = threading.Event()
    rc, runs = _guard(tmp_path, monkeypatch, str(oom_hold.EXIT_HELD), then=_when_held(stop.set),
                      stopping=stop)
    assert rc == 0 and runs == 1
    rec = oom_hold.held()
    assert rec["kind"] == "cgroup" and rec["exit"] == oom_hold.EXIT_HELD, "written where the worker could not"


@pytest.mark.parametrize("plan,code", [("3", 3), ("130", 130), ("1", 1), ("kill", 128 + signal.SIGKILL)])
def test_every_other_end_is_the_worker_own_and_its_restart_policy_acts(tmp_path, monkeypatch, plan, code):
    rc, runs = _guard(tmp_path, monkeypatch, plan)
    assert (rc, runs) == (code, 1) and oom_hold.held() is None


def test_the_watcher_samples_which_process_holds_what(tmp_path, monkeypatch):
    cg = _cgroup(tmp_path)
    proc = tmp_path / "proc"
    for pid, anon, adj, cmd in ((7, 6_000_000, 0, b"python\0-m\0app.worker\0"),
                                (9, 4_700_000, 500, b"python\0-c\0from multiprocessing.spawn\0")):
        d = proc / str(pid)
        d.mkdir(parents=True)
        (d / "status").write_text(f"Name:\tpython\nRssAnon:\t{anon} kB\nRssFile:\t10 kB\n")
        (d / "oom_score_adj").write_text(f"{adj}\n")
        (d / "cmdline").write_bytes(cmd)
    (cg / "cgroup.procs").write_text("9\n7\n12\n")       # 12: gone meanwhile
    got = oom_hold.processes(str(cg), proc=str(proc))
    assert [(p["pid"], p["rss_anon"], p["oom_score_adj"]) for p in got] == [
        (7, 6_000_000 * 1024, 0), (9, 4_700_000 * 1024, 500)], "largest first"
    assert got[0]["cmd"] == "python -m app.worker"
    assert oom_hold.processes(str(tmp_path / "none")) == []
    monkeypatch.setattr(oom_hold, "CGROUP_DIR", str(cg))
    monkeypatch.setattr(oom_hold, "processes", lambda cgroup=None: [{"pid": 7}])
    stop = threading.Event()
    t = threading.Thread(target=oom_hold.watch, args=(stop, lambda b, a: None), kwargs={"every_s": 0.01})
    t.start()
    end = time.monotonic() + 5
    while not oom_hold.last_sample() and time.monotonic() < end:
        time.sleep(0.01)
    stop.set()
    t.join(5)
    assert oom_hold.last_sample()["processes"] == [{"pid": 7}]


def test_the_worker_watcher_writes_the_hold_with_what_was_in_flight(monkeypatch):
    from app import parallel, worker
    monkeypatch.setattr(worker, "_oom_held", threading.Event())
    monkeypatch.setattr(worker, "_stop", threading.Event())
    stopped: list[int] = []
    monkeypatch.setattr(worker, "_handle_sigterm", lambda signum, frame: stopped.append(signum))

    class C:
        name = "eurlex5"
    parallel._enter(1, "chunks", C(), [{"uid": "32021R2017-el", "size": 180_000_000}])
    try:
        worker._on_oom_kill(4, 5)
    finally:
        parallel._leave(1)
    rec = oom_hold.held()
    assert rec["kind"] == "cgroup" and rec["oom_kill"] == [4, 5]
    (part,) = rec["in_flight"]
    assert part["collection"] == "eurlex5" and part["uids"] == [["32021R2017-el", 180_000_000]] and part["bytes"] == 180_000_000
    assert rec["reserved"] == 0 and "processes_before" in rec and "processes_now" in rec, "which process held what"
    assert stopped == [signal.SIGTERM] and worker.exit_code() == oom_hold.EXIT_HELD


def test_release_oom_and_node_show(isolated_config, capsys):
    from app import louise, node_show
    assert louise.main(["node", "release-oom", "--cause", "x"]) == 2, "no hold in force"
    with pytest.raises(SystemExit):
        louise.main(["node", "release-oom", "--cause", "x", "--diagnostic", "y"])
    oom_hold.hold("worker", {"memory_peak": 2 ** 30})
    shown = node_show.show()["oom_hold"]
    assert shown["says"].startswith("HELD since") and shown["record"]["kind"] == "worker"
    capsys.readouterr()
    assert louise.main(["node", "release-oom", "--diagnostic", "the peak per byte is now logged"]) == 0
    assert oom_hold.held() is None and "oom_hold" not in node_show.show()
    assert "the peak per byte is now logged" in capsys.readouterr().out


def test_a_pass_whose_child_was_killed_for_memory_exits_137(monkeypatch, capsys):
    from app import louise, parallel
    monkeypatch.setattr(louise, "_run_releasing", lambda a: (_ for _ in ()).throw(
        parallel.MemoryKilled(0, 1, "record of eurlex5")))
    assert louise.main(["node", "show"]) == 137
    assert "killed a parse-pool child for memory" in capsys.readouterr().err
