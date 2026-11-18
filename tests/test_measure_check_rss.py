"""tools/measure/check_rss.py — a pass's peak resident memory, the tree's
and each process's, read with the scheduled checks' own sampler
(scheduled_checks.session_rss): a child holding 96 MiB under a parent
holding little reads as the tree's peak and as the largest process."""
import json
import sys

from tools.measure import check_rss

CHILD = ("import time, subprocess, sys\n"
         "p = subprocess.Popen([sys.executable, '-c', 'import time; b = bytearray(96 << 20); "
         "b[::4096] = b\"x\" * len(b[::4096]); time.sleep(1.0)'])\n"
         "p.wait()\n")


def test_the_tree_and_its_largest_process_are_read(tmp_path, capsys):
    out = tmp_path / "rss.json"
    assert check_rss.main(["--every", "0.05", "--out", str(out), "--", sys.executable, "-c", CHILD]) == 0
    got = json.loads(out.read_text())
    assert got["exit"] == 0 and got["tree_peak_bytes"] >= 96 << 20
    top = got["processes"][0]
    assert top["hwm"] >= 96 << 20 and "bytearray" in top["cmd"], top
    assert len(got["processes"]) == 2, "the parent and its child, both in the command's session"
    assert "tree peak" in capsys.readouterr().err


def test_a_failing_command_gives_its_exit(tmp_path):
    assert check_rss.main(["--", sys.executable, "-c", "raise SystemExit(3)"]) == 3


def test_a_killed_command_exits_as_a_shell_says_it():
    """SIGKILL (the memcg's OOM kill) is 137, which deploy/supervise-pass.sh
    answers by halving the pool and running the pass again."""
    assert check_rss.main(["--", sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"]) \
        == 137


def test_what_the_oom_killer_sees_is_read_beside_the_sum(tmp_path):
    """2026-11-18: the summed VmRSS counts a shared page once per process.
    Beside it: the processes' Pss, the processes alive at the summed peak,
    when each was first and last seen, and — where a cgroup v2 memory
    controller reads — the cgroup's own peak and its anon/file split."""
    out = tmp_path / "rss.json"
    assert check_rss.main(["--every", "0.05", "--pss-every", "0.05", "--out", str(out), "--",
                           sys.executable, "-c", CHILD]) == 0
    got = json.loads(out.read_text())
    assert got["tree_peak_pss_bytes"] >= 64 << 20, "the child's private 96 MiB is in its Pss"
    assert got["tree_peak_pss_bytes"] <= got["tree_peak_bytes"] * 1.05 + (8 << 20)
    alive = got["at_peak"]
    assert len(alive) == 2 and alive[0]["rss"] >= 96 << 20 and "bytearray" in alive[0]["cmd"]
    for p in got["processes"]:
        assert 0 <= p["first_s"] <= p["last_s"] <= got["seconds"]
    if check_rss.cgroup_dir() is not None:
        cg = got["cgroup"]
        assert cg["anon_peak"] >= 96 << 20 and "file_at_anon_peak" in cg


def test_the_cgroup_is_read_from_proc_self_cgroup(tmp_path):
    proc, root = tmp_path / "proc", tmp_path / "cg"
    (proc / "self").mkdir(parents=True)
    (proc / "self" / "cgroup").write_text("0::/system.slice/pass.scope\n")
    d = root / "system.slice" / "pass.scope"
    d.mkdir(parents=True)
    assert check_rss.cgroup_dir(str(proc), str(root)) is None, "no memory controller there"
    (d / "memory.current").write_text("123\n")
    (d / "memory.stat").write_text("anon 4096\nfile 8192\nkernel 1\n")
    (d / "memory.max").write_text("max\n")
    assert check_rss.cgroup_dir(str(proc), str(root)) == str(d)
    assert check_rss._cg_stat(str(d)) == {"anon": 4096, "file": 8192}
    assert check_rss._cg_int(str(d), "memory.max") is None and check_rss._cg_int(str(d), "memory.current") == 123
