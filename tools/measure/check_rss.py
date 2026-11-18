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

WHAT THE OOM KILLER SEES (2026-11-18: wikidata.next's check read 11.3 GiB
of summed VmRSS against a 12g cap, over 50 processes, and was not killed).
The sum counts a shared page once per process, so beside it:
  - `tree_peak_pss_bytes`: the peak of the processes' Pss
    (/proc/<pid>/smaps_rollup, a shared page split among its sharers — the
    standard proportional measure, as smem reports it), read at most every
    `--pss-every` seconds: it walks each process's page tables;
  - `cgroup`: the memory cgroup the pass runs in (cgroup v2, the
    container's under `docker run --memory`): its own `memory.peak`, and
    the peak of `anon` from memory.stat with the `file` (page cache,
    reclaimable) beside it — the charge the memcg OOM acts on;
  - `at_peak`: the processes alive at the summed peak, each with its RSS;
    and for every process when it was first and last seen (`first_s`,
    `last_s`), so which pools overlapped is read, not guessed.
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


def _pss(pid: str, proc: str = "/proc") -> int | None:
    """The process's Pss in bytes (smaps_rollup), or None where unreadable."""
    try:
        with open(f"{proc}/{pid}/smaps_rollup", "rb") as f:
            for line in f:
                if line.startswith(b"Pss:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def cgroup_dir(proc: str = "/proc", root: str = "/sys/fs/cgroup") -> str | None:
    """This process's cgroup v2 directory ("0::<path>" in /proc/self/cgroup),
    where it holds a memory controller's files; None otherwise."""
    try:
        with open(f"{proc}/self/cgroup", encoding="utf-8") as f:
            for line in f:
                if line.startswith("0::"):
                    d = root + line[3:].strip().rstrip("/")
                    return d if os.path.isfile(f"{d}/memory.current") else None
    except OSError:
        pass
    return None


def _cg_int(d: str, name: str) -> int | None:
    try:
        with open(f"{d}/{name}", encoding="utf-8") as f:
            v = f.read().strip()
        return None if v == "max" else int(v)
    except (OSError, ValueError):
        return None


def _cg_stat(d: str) -> dict[str, int]:
    out = {}
    try:
        with open(f"{d}/memory.stat", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(" ")
                if k in ("anon", "file"):
                    out[k] = int(v)
    except (OSError, ValueError):
        pass
    return out


def measure(argv: list[str], *, every: float = 0.1, pss_every: float = 1.0, env: dict | None = None,
            cgroup: str | None = "auto") -> dict:
    from app.scheduled_checks import session_rss
    cg = cgroup_dir() if cgroup == "auto" else cgroup
    t0 = time.monotonic()
    child = subprocess.Popen(argv, start_new_session=True, env=env)
    sid = child.pid
    peak, peak_at, at_peak = 0, 0.0, []
    pss_peak, pss_at, pss_last = 0, 0.0, -1e9
    anon_peak, file_at_anon, anon_at = 0, 0, 0.0
    procs: dict[str, dict] = {}
    while True:
        now = time.monotonic() - t0
        tree = session_rss(sid)
        live = []
        for pid in (e for e in os.listdir("/proc") if e.isdigit()):
            if _session_of(pid) != sid:
                continue
            st = _status(pid)
            if not st:
                continue
            rec = procs.setdefault(pid, {"pid": int(pid), "cmd": _cmdline(pid), "hwm": 0,
                                         "first_s": round(now, 2)})
            rec["hwm"] = max(rec["hwm"], st.get("VmHWM", 0), st.get("VmRSS", 0))
            rec["last_s"] = round(now, 2)
            live.append((pid, st.get("VmRSS", 0)))
        if tree is not None and tree > peak:
            peak, peak_at = tree, now
            at_peak = sorted(({"pid": int(p), "cmd": procs[p]["cmd"][:120], "rss": r} for p, r in live),
                             key=lambda r: -r["rss"])
        if now - pss_last >= pss_every and live:
            pss_last = now
            got = [_pss(p) for p, _ in live]
            total = sum(v for v in got if v is not None)
            if total > pss_peak:
                pss_peak, pss_at = total, now
        if cg:
            st = _cg_stat(cg)
            if st.get("anon", 0) > anon_peak:
                anon_peak, file_at_anon, anon_at = st["anon"], st.get("file", 0), now
        if child.poll() is not None:
            break
        time.sleep(every)
    out = {"argv": argv, "exit": child.returncode, "seconds": round(time.monotonic() - t0, 2),
           "tree_peak_bytes": peak, "tree_peak_at_s": round(peak_at, 2),
           "tree_peak_pss_bytes": pss_peak, "tree_peak_pss_at_s": round(pss_at, 2),
           "at_peak": at_peak,
           "processes": sorted(procs.values(), key=lambda r: -r["hwm"])}
    if cg:
        out["cgroup"] = {"dir": cg, "memory_max": _cg_int(cg, "memory.max"),
                         "memory_peak": _cg_int(cg, "memory.peak"),
                         "anon_peak": anon_peak, "anon_peak_at_s": round(anon_at, 2),
                         "file_at_anon_peak": file_at_anon}
    return out


def main(args: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--every", type=float, default=0.1, help="seconds between two samples")
    ap.add_argument("--pss-every", type=float, default=1.0,
                    help="seconds between two readings of the processes' Pss (it walks their page tables)")
    ap.add_argument("--out", default=None, help="also write the JSON here")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command")
    a = ap.parse_args(args)
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        ap.error("no command: `-- <command…>`")
    got = measure(cmd, every=a.every, pss_every=a.pss_every)
    text = json.dumps(got, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    cg = got.get("cgroup") or {}
    print(f"tree peak {got['tree_peak_bytes'] / 2**20:,.0f} MiB summed RSS at {got['tree_peak_at_s']} s, "
          f"{got['tree_peak_pss_bytes'] / 2**20:,.0f} MiB Pss; "
          + (f"cgroup anon peak {cg['anon_peak'] / 2**20:,.0f} MiB (page cache {cg['file_at_anon_peak'] / 2**20:,.0f} "
             f"MiB beside it), memory.peak {(cg.get('memory_peak') or 0) / 2**20:,.0f} MiB; " if cg else "")
          + f"{len(got['at_peak'])} process(es) alive at the peak", file=sys.stderr)
    print(f"largest process "
          f"{(got['processes'][0]['hwm'] if got['processes'] else 0) / 2**20:,.0f} MiB; "
          f"{len(got['processes'])} process(es); exit {got['exit']} in {got['seconds']} s", file=sys.stderr)
    # the command's own exit, and a signal as a shell says it (128 + n): an OOM
    # kill reads 137, which deploy/supervise-pass.sh answers by halving the pool
    code = int(got["exit"] or 0)
    return 128 - code if code < 0 else code


if __name__ == "__main__":
    sys.exit(main())
