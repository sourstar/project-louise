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
