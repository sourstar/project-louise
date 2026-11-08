"""One computation per key at a time, its result handed to every caller who
asked meanwhile: the standard single-flight pattern (Go's
`golang.org/x/sync/singleflight`, `Group.Do`). `app/share/seeder.py` keeps
its own lock-per-file for the snapshot (2026-11-08, part A); this is the same
pattern as a reusable group, for the per-part reads of `app/share/possession.py`.

Why it exists (2026-11-08, #416 and #419 on the node): the embed pool's 65
threads all missed the same cache entry at once (a part's record hashes, a
part's possession) and each one built it, so the worker went to 23 GB in
45 s and the cgroup killed it, twice a minute, with pubmed paused.

A waiter's wait is BOUNDED (CLAUDE.md: a pipeline thread never waits
without a bound): past `timeout` it does the work itself, as it would have
without the group. A leader's exception is raised to the callers waiting
on it, as in Go.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Hashable

#: how long a caller waits for the one computing its key before computing it
#: itself; seconds, against a part's decode of seconds
WAIT_S = 300.0


class _Call:
    __slots__ = ("done", "value", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None


class Group:
    """`do(key, fn)`: fn() run by one caller per key at a time; the others
    get its result. Nothing is kept once the call ends; a cache, if any, is
    the caller's (fill it inside fn, read it before `do`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[Hashable, _Call] = {}

    def do(self, key: Hashable, fn: Callable[[], Any], *, timeout: float | None = None) -> Any:
        with self._lock:
            call = self._calls.get(key)
            leader = call is None
            if leader:
                call = self._calls[key] = _Call()
        if not leader:
            if call.done.wait(WAIT_S if timeout is None else timeout):
                if call.error is not None:
                    raise call.error
                return call.value
            return fn()             # the leader is stuck: bounded, then our own
        try:
            call.value = fn()
            return call.value
        except BaseException as e:
            call.error = e
            raise
        finally:
            with self._lock:
                if self._calls.get(key) is call:
                    del self._calls[key]
            call.done.set()

    def __len__(self) -> int:
        """Calls in flight now."""
        with self._lock:
            return len(self._calls)
