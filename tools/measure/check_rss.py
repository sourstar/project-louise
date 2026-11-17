"""The peak resident memory of a pass and of each of its processes —
`hold check` first (München, 2026-11-17: step 6's check of wikidata.next ran
inside the worker's 24 GiB cgroup while wikipedia embedded, and the memcg
OOM killed a 2.4 GB python process).

    python -m tools.measure.check_rss [--every 0.1] [--out FILE] -- <command…>   (from /srv, the image's tree)

    e.g. python -m tools.measure.check_rss -- python -m app.louise hold check wikidata \\
             --checks vectors-sample --seed 00…

THE HARNESS THAT EXISTS: the scheduled checks' own sampler,
`app/scheduled_checks.session_rss` (the command in its own session, its
pool's children inheriting it; each process's VmRSS summed), read every
`--every` seconds — so the TREE's peak is what a memcg sees, to within a
sample. Beside it, each process's own peak: its VmHWM, read while it lives
(a pool child's last reading before it exits). A page shared copy-on-write
is counted once per process, as `session_rss` counts it: the sum errs high.

Prints one JSON object: the command, its exit, its seconds, the tree's peak
and when, and each process's peak with its command line, largest first.
Nothing is written but `--out`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time


def _status(pid: str, proc: str = "/proc") -> dict[str, int]:
    out = {}
    try:
        with open(f"{proc}/{pid}/status", "rb") as f:
            for line in f:
                if line.startswith((b"VmRSS:", b"VmHWM:")):
                    out[line.split(b":")[0].decode()] = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def _session_of(pid: str, proc: str = "/proc") -> int | None:
    try:
        with open(f"{proc}/{pid}/stat", "rb") as f:
            stat = f.read()
        return int(stat[stat.rindex(b")") + 2:].split()[3])
    except (OSError, ValueError, IndexError):
        return None


def _cmdline(pid: str, proc: str = "/proc") -> str:
    try:
        with open(f"{proc}/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()[:200]
    except OSError:
        return ""


def measure(argv: list[str], *, every: float = 0.1, env: dict | None = None) -> dict:
    from app.scheduled_checks import session_rss
    t0 = time.monotonic()
    child = subprocess.Popen(argv, start_new_session=True, env=env)
    sid = child.pid
    peak, peak_at = 0, 0.0
    procs: dict[str, dict] = {}
    while True:
        tree = session_rss(sid)
        if tree is not None and tree > peak:
            peak, peak_at = tree, time.monotonic() - t0
        for pid in (e for e in os.listdir("/proc") if e.isdigit()):
            if _session_of(pid) != sid:
                continue
            st = _status(pid)
            if not st:
                continue
            rec = procs.setdefault(pid, {"pid": int(pid), "cmd": _cmdline(pid), "hwm": 0})
            rec["hwm"] = max(rec["hwm"], st.get("VmHWM", 0), st.get("VmRSS", 0))
        if child.poll() is not None:
            break
        time.sleep(every)
    return {"argv": argv, "exit": child.returncode, "seconds": round(time.monotonic() - t0, 2),
            "tree_peak_bytes": peak, "tree_peak_at_s": round(peak_at, 2),
            "processes": sorted(procs.values(), key=lambda r: -r["hwm"])}


def main(args: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--every", type=float, default=0.1, help="seconds between two samples")
    ap.add_argument("--out", default=None, help="also write the JSON here")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command")
    a = ap.parse_args(args)
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        ap.error("no command: `-- <command…>`")
    got = measure(cmd, every=a.every)
    text = json.dumps(got, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    print(f"tree peak {got['tree_peak_bytes'] / 2**20:,.0f} MiB at {got['tree_peak_at_s']} s; largest process "
          f"{(got['processes'][0]['hwm'] if got['processes'] else 0) / 2**20:,.0f} MiB; "
          f"{len(got['processes'])} process(es); exit {got['exit']} in {got['seconds']} s", file=sys.stderr)
    return int(got["exit"] or 0)


if __name__ == "__main__":
    sys.exit(main())
