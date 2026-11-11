"""Open files: the process's limit raised to what it may hold, and a census
of what it holds when it runs out (München, 2026-11-11: the worker hit its
soft limit of 1024 — hard 524288 — twice in six minutes, and one normattiva
document was written an error for it).

THE STANDARD: the soft limit exists for `select()`, whose fd_set stops at
1024. A program that never uses `select()` raises its soft limit to the hard
one at start — systemd's guidance (Lennart Poettering, "File Descriptor
Limits", 2021: services keep 1024 soft, programs that can take more raise it
themselves) and what the Go runtime has done for every program since Go 1.19.
Louise's I/O is asyncio (epoll), httpx, Lance and SQLite; `subprocess` uses
poll. So the raise is done here, in-process, at the start of the worker, the
web process and every command — every way the app is deployed gets it, the
TrueNAS app and a supervised pass included, without a compose `ulimits` line
to keep in step (that line is the alternative, for a deployment that wants a
lower cap than the hard limit).

THE CENSUS says what holds them, by kind, read from /proc/self/fd: the
Lance files, the SQLite databases and their WAL and SHM, the text indexes,
sockets, pipes, the rest. Logged when a stage meets exhaustion
(app/worker.py `resources_exhausted`), so the next time names its holder.
"""
from __future__ import annotations

import os
from collections import Counter

try:
    import resource
except ImportError:                      # not POSIX: nothing to raise
    resource = None  # type: ignore[assignment]


def raise_soft() -> tuple[int, int] | None:
    """The soft RLIMIT_NOFILE raised to the hard one; (before, after), or
    None where the platform has no such limit. Never raises: a process that
    cannot raise it runs with what it has."""
    if resource is None:
        return None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if hard == resource.RLIM_INFINITY or soft == resource.RLIM_INFINITY or soft >= hard:
            return soft, soft
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
        return soft, resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    except (OSError, ValueError):
        return None


def _kind(target: str) -> str:
    if target.startswith("socket:"):
        return "socket"
    if target.startswith("pipe:"):
        return "pipe"
    if target.startswith("anon_inode:"):
        return "anon"
    name = target.rsplit("/", 1)[-1]
    if ".lance" in target:               # a table's directory is <name>.lance
        return "lance"
    if name.endswith("-wal"):
        return "sqlite-wal"
    if name.endswith("-shm"):
        return "sqlite-shm"
    if name.endswith((".sqlite", ".db", ".sqlite3")):
        return "sqlite"
    if "/index/" in target or "/fts/" in target:   # a collection's text index (tantivy)
        return "text-index"
    return "other"


def census(fd_dir: str = "/proc/self/fd") -> dict[str, int]:
    """How many files this process holds, by kind (`total` among them); an
    empty dict where /proc cannot be read."""
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return {}
    kinds: Counter[str] = Counter()
    for n in names:
        try:
            kinds[_kind(os.readlink(os.path.join(fd_dir, n)))] += 1
        except OSError:
            continue                     # closed between the listing and the read
    out = dict(kinds.most_common())
    out["total"] = sum(kinds.values())
    return out


def said(c: dict[str, int]) -> str:
    """The census as one log line's words."""
    if not c:
        return "the open files could not be counted"
    parts = [f"{k} {v}" for k, v in c.items() if k != "total"]
    limit = None
    if resource is not None:
        try:
            limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        except (OSError, ValueError):
            pass
    return (f"{c.get('total', 0)} open file(s)" + (f" of {limit}" if limit else "")
            + (": " + ", ".join(parts) if parts else ""))
