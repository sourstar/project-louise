"""deploy/watchdog/louise-watchdog.sh — the development watchdog's host half
(operator, 2026-11-07): no agent beat for 2 h, the dev units stopped — only
`louise-…` names, never the watchdog's own — then the node's working state
restored by `checkpoint watch`. Run against a fake `docker` and `systemctl`."""
import json
import os
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "watchdog"

FAKE_DOCKER = """#!/bin/bash
echo "docker $*" >> {d}/calls
if [ "$1" = inspect ]; then cat {d}/running; exit 0; fi
case " $* " in
  *" --dry-run "*) cat {d}/dry.json; exit $(cat {d}/dry.code) ;;
  *) cat {d}/real.json; exit $(cat {d}/real.code) ;;
esac
"""
FAKE_SYSTEMCTL = """#!/bin/bash
echo "systemctl $*" >> {d}/calls
case "$3" in louise-gone) exit 5 ;; louise-stuck) exit 1 ;; esac
exit 0
"""


def _node(tmp_path: Path, *, running="true", dry=(0, {}), real=(10, {})) -> dict:
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("systemctl", FAKE_SYSTEMCTL)):
        (bin_ / name).write_text(body.format(d=tmp_path))
        (bin_ / name).chmod(0o755)
    (tmp_path / "running").write_text(running + "\n")
    for stem, (code, out) in (("dry", dry), ("real", real)):
        (tmp_path / f"{stem}.code").write_text(f"{code}\n")
        (tmp_path / f"{stem}.json").write_text(json.dumps(out))
    return dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", LOG=str(tmp_path / "log" / "watchdog.log"))


def _run(tmp_path, env):
    p = subprocess.run(["bash", str(DEPLOY / "louise-watchdog.sh")], env=env, capture_output=True,
                       text=True, timeout=60)
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    log = Path(env["LOG"]).read_text() if Path(env["LOG"]).exists() else ""
    return p, calls, log


def _expired(units):
    return (11, {"expired": True, "units": units, "says": "no agent beat since …", "saved_at": "t",
                 "note": "engine A/B"})


def test_nothing_to_do_is_silent_and_stops_nothing(tmp_path):
    p, calls, log = _run(tmp_path, _node(tmp_path, dry=(0, {"expired": False, "units": []})))
    assert p.returncode == 0 and log == ""
    assert calls == ["docker inspect -f {{.State.Running}} ix-louise-rag-worker-1",
                     "docker exec ix-louise-rag-worker-1 python -m app.louise node checkpoint watch "
                     "--after 2h --dry-run --json"]


def test_expired_stops_the_units_first_then_restores_by_watch(tmp_path):
    env = _node(tmp_path, dry=_expired(["louise-engine-ab5", "louise-gone"]),
                real=(10, {"restored": {"changed": [{"what": "mode", "to": "running"}]}}))
    p, calls, log = _run(tmp_path, env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert calls[2:] == ["systemctl stop -- louise-engine-ab5", "systemctl stop -- louise-gone",
                         "docker exec ix-louise-rag-worker-1 python -m app.louise node checkpoint watch "
                         "--after 2h --json"], "units first, then watch (not restore) restores"
    assert "EXPIRED" in log and "stopped louise-engine-ab5" in log
    assert "louise-gone: not loaded, nothing to stop" in log and "restored: mode -> running" in log


def test_only_louise_units_are_stopped_never_the_watchdog_and_a_refusal_is_said(tmp_path):
    units = ["sshd", "louise-watchdog", "louise-watchdog.timer", "louise-x; reboot", "-louise-x",
             "docker.service", "louise-engine-ab5", "louise-stuck"]
    p, calls, log = _run(tmp_path, _node(tmp_path, dry=_expired(units)))
    stops = [c for c in calls if c.startswith("systemctl")]
    assert stops == ["systemctl stop -- louise-engine-ab5", "systemctl stop -- louise-stuck"]
    for u in ("sshd", "louise-watchdog", "louise-watchdog.timer", "louise-x; reboot", "-louise-x",
              "docker.service"):
        assert f"REFUSED unit '{u}'" in log, u
    assert "louise-stuck: systemctl stop exit 1" in log
    assert p.returncode == 1, "a refusal or a failed stop marks the run failed"
    assert calls[-1].endswith("watch --after 2h --json"), "the state is restored all the same"


def test_a_worker_that_is_not_running_is_said_and_nothing_is_started(tmp_path):
    p, calls, log = _run(tmp_path, _node(tmp_path, running="false"))
    assert p.returncode == 1 and len(calls) == 1 and calls[0].startswith("docker inspect")
    assert "is not running (false)" in log and "apps are not started from here" in log


def test_a_failed_restore_or_watch_is_exit_one(tmp_path):
    p, _, log = _run(tmp_path, _node(tmp_path, dry=_expired([]), real=(1, {"says": "FAILED in part"})))
    assert p.returncode == 1 and "restore exit 1" in log
    other = tmp_path / "unreadable"
    other.mkdir()
    p, calls, log = _run(other, _node(other, dry=(2, {})))
    assert p.returncode == 1 and "watch --dry-run exit 2" in log and len(calls) == 2


def test_the_after_and_container_are_the_environments(tmp_path):
    env = _node(tmp_path, dry=(0, {}))
    env.update(AFTER="90m", CONTAINER="w2")
    _, calls, _ = _run(tmp_path, env)
    assert calls[1] == "docker exec w2 python -m app.louise node checkpoint watch --after 90m --dry-run --json"


def test_the_units_run_the_installed_copy_every_ten_minutes_persistently():
    service = (DEPLOY / "louise-watchdog.service").read_text()
    timer = (DEPLOY / "louise-watchdog.timer").read_text()
    readme = (DEPLOY / "README.md").read_text()
    assert "Type=oneshot" in service and "ExecStart=/root/louise-watchdog/louise-watchdog.sh" in service
    assert "OnCalendar=*:0/10" in timer and "Persistent=true" in timer and "WantedBy=timers.target" in timer
    assert "/root/louise-watchdog/" in readme and "systemctl enable --now louise-watchdog.timer" in readme
    assert os.access(DEPLOY / "louise-watchdog.sh", os.X_OK)
