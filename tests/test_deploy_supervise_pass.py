"""deploy/supervise-pass.sh and deploy/docker-memory-ceiling.sh — a pass that
crashes comes back by itself (operator, 2026-10-30), one the kernel kills
for memory is held until its cause is known (operator, 2026-11-19), and the
caps of what runs at once never make a GLOBAL OOM. Run against a fake
`docker`."""
import os
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def _fake_docker(tmp_path: Path, codes: list[int]) -> dict:
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (tmp_path / "codes").write_text("\n".join(map(str, codes)) + "\n")
    (bin_ / "docker").write_text(f"""#!/bin/bash
echo "$*" >> {tmp_path}/calls
code=$(head -1 {tmp_path}/codes); sed -i 1d {tmp_path}/codes
exit $code
""")
    (bin_ / "docker").chmod(0o755)
    return dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", PAUSE="0", DEFER_WAIT="0",
                HOLD=str(tmp_path / "hold"), CEILING=str(tmp_path / "no-ceiling"))


def _run(tmp_path, env, *args):
    p = subprocess.run(["bash", str(DEPLOY / "supervise-pass.sh"), "build", *args],
                       env=env, capture_output=True, text=True, timeout=60)
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    return p, calls


def test_a_memory_kill_holds_the_pass_until_it_is_released(tmp_path):
    """Operator, 2026-11-19 ("the very first time a worker is killed because
    of OOM, it is not relauched until the cause has been determined or
    diagnostics has been added"): exit 137 is held, never run again with
    the pool halved; a later supervisor does not start it either; the
    release, naming the cause or the diagnostic, lifts it and is kept."""
    env = _talking_docker(tmp_path, [(137, "Killed")])
    env["PARSE_PROCESSES"] = "12"
    p, calls = _run(tmp_path, env, "--memory", "12g", "img", "louise", "hold", "records", "build")
    assert p.returncode == 137 and len(calls) == 1 and "HELD, not restarted" in p.stdout, p.stdout
    held = tmp_path / "build.oom-hold"
    assert "exit 137" in held.read_text() and "Killed" in held.read_text(), "when, and its last lines"
    p, calls = _run(tmp_path, env, "--memory", "12g", "img")
    assert p.returncode == 137 and len(calls) == 1 and "not started until released" in p.stdout
    rel = subprocess.run(["bash", str(DEPLOY / "release-oom.sh"), "build", "--diagnostic"], env=env,
                         capture_output=True, text=True, timeout=30, check=False)
    assert rel.returncode == 2 and held.exists(), "a release says it in words"
    rel = subprocess.run(["bash", str(DEPLOY / "release-oom.sh"), "build", "--cause",
                          "32021R2017 el: 5.25 GB to chunk"], env=env, capture_output=True, text=True, timeout=30,
                         check=False)
    assert rel.returncode == 0 and not held.exists(), rel.stdout + rel.stderr
    assert "cause: 32021R2017 el" in (tmp_path / "oom-holds.log").read_text()
    (tmp_path / "codes").write_text("0\n")
    (tmp_path / "lines").write_text("done\n")
    p, calls = _run(tmp_path, env, "--memory", "12g", "img")
    assert p.returncode == 0 and len(calls) == 2, "released, it runs again"
    assert "PARSE_PROCESSES=12" in calls[-1], "never with the pool halved"


def test_a_sigpipe_is_a_crash_and_comes_back_with_its_pool_whole(tmp_path):
    """141 (SIGPIPE) is no memory kill: again in PAUSE, as any crash, and the
    pool is no longer halved."""
    env = _fake_docker(tmp_path, [141, 0])
    env["PARSE_PROCESSES"] = "12"
    p, calls = _run(tmp_path, env, "--memory", "12g", "img")
    assert p.returncode == 0 and len(calls) == 2
    assert all("PARSE_PROCESSES=12" in c for c in calls)


def test_a_pass_is_first_in_line_for_the_oom_killer_and_carries_its_cap(tmp_path):
    env = _fake_docker(tmp_path, [0])
    p, calls = _run(tmp_path, env, "-m", "4g", "img")
    assert p.returncode == 0
    assert "--oom-score-adj 500" in calls[0] and "-m 4g" in calls[0]
    # 2026-11-16: Docker's default soft nofile (1024) killed a pilot's tantivy writers three times
    assert "--ulimit nofile=65536:524288" in calls[0]


def test_a_pass_without_a_memory_cap_is_refused(tmp_path):
    env = _fake_docker(tmp_path, [0])
    p, calls = _run(tmp_path, env, "img", "louise", "hold", "check")
    assert p.returncode == 2 and not calls and "no --memory cap" in p.stdout


def test_a_held_pass_is_not_restarted(tmp_path):
    env = _fake_docker(tmp_path, [1, 0])
    (tmp_path / "bin" / "docker").write_text(f"""#!/bin/bash
echo "$*" >> {tmp_path}/calls; touch {tmp_path}/hold; exit 1
""")
    p, calls = _run(tmp_path, env, "--memory=8g", "img")
    assert p.returncode == 1 and len(calls) == 1 and "held" in p.stdout


@pytest.mark.parametrize("code", [130, 143])
def test_a_pass_stopped_on_purpose_is_not_restarted(tmp_path, code):
    """Operator, 2026-10-31: "if I stop it from freenas, likely because I want
    to fix something that might actually lead to the crash, it shouldn't
    restart by itself". A `docker stop` is SIGTERM: the pass exits 130 (07
    §7.7), or 143 if it did not catch it. The OOM killer's SIGKILL (137) is
    held too, since 2026-11-19 (operator: not relaunched until its cause is
    known)."""
    env = _fake_docker(tmp_path, [code, 0])
    p, calls = _run(tmp_path, env, "--memory=8g", "img")
    assert p.returncode == code and len(calls) == 1 and "stopped on purpose" in p.stdout
    oom = tmp_path / "oom"
    oom.mkdir()
    p, calls = _run(oom, _fake_docker(oom, [137, 0]), "--memory=8g", "img")
    assert p.returncode == 137 and len(calls) == 1, "a memory kill is held, not run again"


def test_a_refused_pass_is_not_restarted_and_a_deferred_one_is(tmp_path):
    """07 §7.7: exit 2 is a refusal — a rule stood in the way, and nothing
    changes by itself — exit 3 a deferral that may clear. München,
    2026-11-02: pubmed's CPU reference was refused every minute (VeGa states
    no run for an unmeasured tree) and held the night's chain."""
    env = _fake_docker(tmp_path, [2, 0])
    p, calls = _run(tmp_path, env, "--memory=8g", "img")
    assert p.returncode == 2 and len(calls) == 1 and "refused" in p.stdout
    later = tmp_path / "deferred"
    later.mkdir()
    p, calls = _run(later, _fake_docker(later, [3, 0]), "--memory=8g", "img")
    assert p.returncode == 0 and len(calls) == 2, "a deferral is asked again"


def _cgroup(tmp_path, *, max_="max", current=20 << 30, total_kb=61_900_000):
    cg = tmp_path / "docker"
    cg.mkdir()
    (cg / "memory.max").write_text(max_ + "\n")
    (cg / "memory.current").write_text(f"{current}\n")
    (tmp_path / "meminfo").write_text(f"MemTotal:       {total_kb} kB\nMemFree: 1 kB\n")
    return dict(os.environ, DOCKER_CGROUP=str(cg), MEMINFO=str(tmp_path / "meminfo")), cg


def _ceiling(env):
    return subprocess.run(["bash", str(DEPLOY / "docker-memory-ceiling.sh")], env=env,
                          capture_output=True, text=True, timeout=30)


def test_the_ceiling_is_ninety_percent_of_the_host_in_whole_pages_and_idempotent(tmp_path):
    env, cg = _cgroup(tmp_path)
    p = _ceiling(env)
    want = 61_900_000 * 1024 * 9 // 10 // 4096 * 4096
    assert p.returncode == 0 and (cg / "memory.max").read_text().strip() == str(want), p.stdout
    assert "max -> " in p.stdout
    again = _ceiling(env)
    assert again.returncode == 0 and again.stdout == "", "set already: silent"


def test_a_ceiling_under_what_the_containers_use_is_not_set(tmp_path):
    env, cg = _cgroup(tmp_path, current=58 << 30)
    p = _ceiling(env)
    assert p.returncode == 1 and (cg / "memory.max").read_text().strip() == "max" and "NOT set" in p.stdout


def test_no_docker_cgroup_is_said_and_is_not_an_error(tmp_path):
    env = dict(os.environ, DOCKER_CGROUP=str(tmp_path / "absent"), PATH="/usr/bin:/bin")
    p = _ceiling(env)
    assert p.returncode == 0 and "not set" in p.stdout


def test_the_supervisor_applies_the_ceiling_before_every_run(tmp_path):
    env = _fake_docker(tmp_path, [1, 0])
    ceiling = tmp_path / "ceiling.sh"
    ceiling.write_text(f"#!/bin/bash\necho x >> {tmp_path}/ceilings\n")
    ceiling.chmod(0o755)
    env["CEILING"] = str(ceiling)
    p, calls = _run(tmp_path, env, "--memory", "12g", "img")
    assert p.returncode == 0 and len(calls) == 2
    assert (tmp_path / "ceilings").read_text().count("x") == 2


@pytest.mark.parametrize("gate", [["hold", "collection", "new", "eurlex"], ["hold", "embedder", "check", "pubmed"]])
def test_a_gates_verdict_is_not_asked_again_until_it_says_yes(tmp_path, gate):
    """2026-11-08 (München's legal chain, `new` to run supervised): a gate's
    exit 1 is "this run does not reproduce itself" (invariant 12); rerun every
    minute, a flaky engine would pass once and be admitted. Any other pass's
    exit 1 is still asked again, and VERDICTS="" says a gate's is too."""
    env = _fake_docker(tmp_path, [1, 0])
    p, calls = _run(tmp_path, env, "--memory=8g", "img", "python", "-m", "app.louise", *gate)
    assert p.returncode == 1 and len(calls) == 1 and "verdict" in p.stdout, p.stdout
    other = tmp_path / "other"
    other.mkdir()
    p, calls = _run(other, _fake_docker(other, [1, 0]), "--memory=8g", "img", "louise", "hold", "records", "build")
    assert p.returncode == 0 and len(calls) == 2, "a records build's exit 1 is a failure, asked again"
    opt = tmp_path / "opt"
    opt.mkdir()
    env = dict(_fake_docker(opt, [1, 0]), VERDICTS="")
    p, calls = _run(opt, env, "--memory=8g", "img", *gate)
    assert p.returncode == 0 and len(calls) == 2


def test_a_re_creation_waiting_for_its_forget_is_not_asked_again(tmp_path):
    """2026-11-13 (München, legi F): `collection new --re-create` exits 4 when
    its swap waits for the operator's `forget` (EXIT_SWAP_WAITS), and says the
    same however often it is asked; the supervisor ran it three times and
    called it a defect. 4 is `new`'s verdict, and no other pass's."""
    from app.louise import EXIT_SWAP_WAITS
    assert EXIT_SWAP_WAITS == 4
    env = _fake_docker(tmp_path, [4, 0])
    p, calls = _run(tmp_path, env, "--memory=8g", "img", "louise", "hold", "collection", "new", "legi",
                    "--re-create", "legi")
    assert p.returncode == 4 and len(calls) == 1 and "verdict" in p.stdout, p.stdout
    for kind in (["hold", "embedder", "check", "pubmed"], ["hold", "records", "build"]):
        other = tmp_path / kind[1]
        other.mkdir()
        p, calls = _run(other, _fake_docker(other, [4, 0]), "--memory=8g", "img", "louise", *kind)
        assert p.returncode == 0 and len(calls) == 2, f"{kind}: exit 4 is a failure there, asked again"


def test_a_stop_of_the_supervisor_stops_the_pass_once_and_it_stays_stopped(tmp_path):
    """The watchdog (deploy/watchdog/) stops a `louise-<name>` unit; the pass
    in it must end its unit and exit 130 — told once (`docker stop`, the CLI
    forwarding nothing) — and must not come back."""
    import signal
    import time
    env = _fake_docker(tmp_path, [])
    (tmp_path / "bin" / "docker").write_text(f"""#!/bin/bash
echo "$*" >> {tmp_path}/calls
if [ "$1" = stop ]; then touch {tmp_path}/stopped; exit 0; fi
for i in $(seq 200); do [ -e {tmp_path}/stopped ] && exit 130; sleep 0.05; done
exit 0
""")
    p = subprocess.Popen(["bash", str(DEPLOY / "supervise-pass.sh"), "build", "--memory=8g", "img"],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    calls = tmp_path / "calls"
    for _ in range(100):
        if calls.exists():
            break
        time.sleep(0.05)
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=30)
    lines = calls.read_text().splitlines()
    assert p.returncode == 130, out
    assert lines[0].startswith("run --rm --sig-proxy=false --name build ")
    assert lines[1] == "stop -t 300 build" and len(lines) == 2, lines
    assert (tmp_path / "hold").exists() and "held" in out


def test_a_pass_that_ends_during_the_stop_reports_its_own_status(tmp_path):
    """München, 2026-11-12 (5c92a1d's staging, load 30 on 12 threads): the
    test above failed `143 == 130`. The trap interrupts `wait`, whose status
    is then 128 + SIGTERM; the second wait was asked only while `kill -0`
    still saw the child, so a pass that ended during the trap's `docker stop`
    was reported 143. Made certain here: `docker stop` returns only after
    the pass has ended."""
    import signal
    import time
    env = _fake_docker(tmp_path, [])
    (tmp_path / "bin" / "docker").write_text(f"""#!/bin/bash
echo "$*" >> {tmp_path}/calls
if [ "$1" = stop ]; then touch {tmp_path}/stopped; sleep 0.5; exit 0; fi
for i in $(seq 200); do [ -e {tmp_path}/stopped ] && exit 130; sleep 0.05; done
exit 0
""")
    p = subprocess.Popen(["bash", str(DEPLOY / "supervise-pass.sh"), "build", "--memory=8g", "img"],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    calls = tmp_path / "calls"
    for _ in range(100):
        if calls.exists():
            break
        time.sleep(0.05)
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=30)
    assert p.returncode == 130 and "exit 130" in out, out
    assert calls.read_text().splitlines()[1] == "stop -t 300 build"


def test_a_pass_that_fails_at_once_three_times_in_a_row_is_not_run_twenty_times(tmp_path):
    """2026-11-09: the legal pilot's `pool` died at startup (an AttributeError,
    exit 1) and the supervisor ran it 20 times over 19 minutes. Three fast
    failures in a row are a defect; a slow one between them resets the count,
    and an OOM kill never counts."""
    env = _fake_docker(tmp_path, [1, 1, 1, 0])
    p, calls = _run(tmp_path, env, "--memory=8g", "img")
    assert p.returncode == 1 and len(calls) == 3 and "a defect in the pass" in p.stdout, p.stdout
    oom = tmp_path / "oom"
    oom.mkdir()
    p, calls = _run(oom, _fake_docker(oom, [137, 137, 137, 0]), "--memory=8g", "img")
    assert p.returncode == 137 and len(calls) == 1, "a memory kill is held at once (operator, 2026-11-19)"
    slow = tmp_path / "slow"
    slow.mkdir()
    env = dict(_fake_docker(slow, [1, 1, 1, 0]), FAST_FAIL_S="0")
    p, calls = _run(slow, env, "--memory=8g", "img")
    assert p.returncode == 0 and len(calls) == 4, "a failure after FAST_FAIL_S is asked again"


def _talking_docker(tmp_path: Path, runs: list[tuple[int, str]]) -> dict:
    """A fake `docker run` that prints its line, then exits with its code."""
    env = _fake_docker(tmp_path, [code for code, _ in runs])
    (tmp_path / "lines").write_text("".join(line + "\n" for _, line in runs))
    (tmp_path / "bin" / "docker").write_text(f"""#!/bin/bash
echo "$*" >> {tmp_path}/calls
echo "working"
head -1 {tmp_path}/lines; sed -i 1d {tmp_path}/lines
code=$(head -1 {tmp_path}/codes); sed -i 1d {tmp_path}/codes
exit $code
""")
    return dict(env, FAST_FAIL_S="0")


def test_the_same_error_twice_in_a_row_is_a_defect_however_long_each_run_took(tmp_path):
    """2026-11-14: a paragraph measurement died on one record's
    LegalChunkerError after ~27 min, and the supervisor ran it 20 times in
    9 h — too slow for the fast-failure rule. The same exit and the same last
    line twice in a row is a defect: not run a third time, its output kept."""
    err = "app.chunking_legal.LegalChunkerError: normattiva:1: the header leaves -24 tokens of 1000"
    env = _talking_docker(tmp_path, [(1, err), (1, err), (0, "done")])
    p, calls = _run(tmp_path, env, "--memory=8g", "img")
    assert p.returncode == 1 and len(calls) == 2 and "same last line twice in a row" in p.stdout, p.stdout
    assert (tmp_path / "build.last.log").read_text().splitlines()[-1] == err, "the last run's output, kept"
    # another error each time is a crash, asked again; a SIGPIPE between two equal errors resets
    other = tmp_path / "other"
    other.mkdir()
    p, calls = _run(other, _talking_docker(other, [(1, "error A"), (1, "error B"), (0, "done")]), "--memory=8g", "img")
    assert p.returncode == 0 and len(calls) == 3, p.stdout
    oom = tmp_path / "oom"
    oom.mkdir()
    p, calls = _run(oom, _talking_docker(oom, [(1, err), (141, "Broken pipe"), (1, err), (0, "done")]), "--memory=8g", "img")
    assert p.returncode == 0 and len(calls) == 4, p.stdout
    # the pass's own status comes through the pipe to tee
    ok = tmp_path / "ok"
    ok.mkdir()
    p, calls = _run(ok, _talking_docker(ok, [(3, "deferred"), (0, "done")]), "--memory=8g", "img")
    assert p.returncode == 0 and "exit 3" in p.stdout and "working" in p.stdout



def test_a_deferral_is_asked_again_even_with_the_same_last_line(tmp_path):
    """2026-11-15: Cellar's timeouts ended the EUR-Lex pass twice with the
    same line, read as "the same error twice: a defect". A deferral (exit 3)
    is asked again after its wait, outside the defect rules."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (tmp_path / "codes").write_text("3\n3\n3\n0\n")
    (bin_ / "docker").write_text(f"""#!/bin/bash
echo "$*" >> {tmp_path}/calls
echo "louise: deferred: cellar: connection timed out on the count of 1954"
code=$(head -1 {tmp_path}/codes); sed -i 1d {tmp_path}/codes
exit $code
""")
    (bin_ / "docker").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", PAUSE="0", DEFER_WAIT="0",
               HOLD=str(tmp_path / "hold"), CEILING=str(tmp_path / "no-ceiling"), FAST_FAIL_S="0")
    p, calls = _run(tmp_path, env, "--memory=8g", "img")
    assert p.returncode == 0 and len(calls) == 4, p.stdout
    assert p.stdout.count("deferred (exit 3") == 3 and "defect" not in p.stdout


def test_deferrals_have_their_own_limit(tmp_path):
    env = _fake_docker(tmp_path, [3, 3, 3, 0])
    env["MAX_DEFERS"] = "2"
    p, calls = _run(tmp_path, env, "--memory=8g", "img")
    assert p.returncode == 3 and len(calls) == 3 and "giving up" in p.stdout


def test_the_cli_exits_3_for_an_origin_away_and_1_for_a_fault():
    from app import louise
    from app.acquire import backoff
    from app.acquire.spec import TransferAborted, TransportError
    assert louise._is_deferral(TransportError("cellar: connection timed out on the count of 1954"))
    assert louise._is_deferral(backoff.Deferred("cellar: 503 to the listing"))
    import httpx
    assert louise._is_deferral(httpx.ConnectTimeout("x"))
    assert not louise._is_deferral(TransportError("cellar: L_1997209IT.toc.xml is 1024 bytes, the notice says 1023"))
    assert not louise._is_deferral(TransferAborted("stop"))
    assert not louise._is_deferral(ValueError("a defect"))
