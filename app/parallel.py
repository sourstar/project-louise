"""The PARSE POOL — the GIL-bound per-document work (fetch, parse, chunk,
metadata, pack records) in child processes, so a 12-core node indexes on
twelve cores instead of one (architecture amendment 47).

Measured on the reference node before this: the worker process at 232 %
of twelve cores — ONE Python thread at 87 % (whichever pipeline held the
interpreter lock), Lance's native threads for the rest — and eleven idle
cores, with the operator asking for "close to 100 % when there is not
much else to do".

What stays in the worker: every WRITER (the tantivy index writer, the
Lance table, the manifest transactions, the marks) and the selection —
the pool only computes. A batch of rows is cut into sub-batches, each a
task for one child; results come back in order, one (ok, value) per row,
so a failing document costs itself and never its neighbours, exactly as
the serial path treats a peer that cannot load.

Children are SPAWNED (never forked: the worker holds SQLite connections
and threads, and a forked copy of those is undefined), run at `nice 10`
so encoders, queries and the UI keep priority ("when there is not much
else to do"), and receive the parent's config knobs that shape chunking
so a test's or an operator's override is honoured in the child.
`PARSE_PROCESSES=1` is the in-process serial path — identical results,
no children — used by the test suite except where the pool itself is
under test.
"""
import multiprocessing
import os
import sys
import threading
import time
import weakref
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, CancelledError, Future, ProcessPoolExecutor, wait
from concurrent.futures import process as _cf_process
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager
from typing import Callable

from . import config, cpu_limit

# config knobs a child must share with the parent: everything that shapes
# a chunk, a record or a plugin's file access
KNOBS = ("CHUNK_TOKENS", "CHUNK_OVERLAP", "CHUNK_VERSION", "CLEANING_VERSION",
         "PUBMED_PACK_DIR", "CACHE_DIR", "FILES_CACHE_DIR", "CORPUS_DIR", "DATA_DIR", "REFS_DB_PATH")

_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None
_pool_size = 0
_failures = 0
_disabled = False
#: A DISABLED POOL COMES BACK (2026-11-21, P2 of the EUR-Lex review: after
#: PARSE_MAX_FAILURES the pool was off for the process's life and
#: `reset_failures` had no caller — the fts crawl, 0.7 -> 73 ms/doc on one
#: core). Off until this monotonic time, then on probation: one more failure
#: and it is off again, for twice as long (PARSE_RETRY_S doubling up to
#: PARSE_RETRY_MAX_S); a batch it answers ends the probation. The circuit
#: breaker's half-open state (Nygard, "Release It!", 2007), with the standard
#: library's executor; a library for it (pybreaker) is not worth one call site.
_disabled_until = 0.0
#: consecutive times the pool was disabled without a batch answered between
_disabled_times = 0
#: when a caller last asked for the pool or left it (monotonic): the
#: start of its idleness for `release_idle`. Under `_held_lock`.
_last_active = time.monotonic()
#: executors this process has thrown away: a failure surfacing from one of
#: them later is not a new failure of the pool (`_note_failure`)
_discarded: "weakref.WeakSet[ProcessPoolExecutor]" = weakref.WeakSet()


class _Executor(ProcessPoolExecutor):
    """ProcessPoolExecutor with CPython's own fix for gh-115634, on an
    interpreter that lacks it: the node's 3.12 (security fixes only, so it
    never will have it; 3.13.16 has it).

    A child retires after PARSE_TASKS_PER_CHILD tasks. 3.12's manager thread
    then calls `_adjust_process_count`, which spawns a replacement only if it
    cannot take a token from `_idle_worker_semaphore`. That semaphore counts
    task COMPLETIONS, not idle children, so it holds stale tokens released by
    children that are gone, and each stale token trusted leaves the pool a
    child short. It ends with no child and parts still queued. Nothing
    answers, the watchdog throws the pool away, and the interpreter's exit
    joins that pool's manager thread for ever. München, 2026-10-25:
    wikipedia-v8's build did not exit on SIGTERM, with no parse child left.
    Reproduced on 3.12.3 (8 parts, 2 children retiring after 2 tasks each):
    4 parts answered, 0 children left. 3.13 replaces a retired child
    unconditionally (`_replace_dead_worker`). This does the same when the
    manager calls, and defers to the library everywhere else."""

    def _adjust_process_count(self):
        manager = getattr(_cf_process, "_ExecutorManagerThread", None)
        if manager is None or not isinstance(threading.current_thread(), manager):
            return super()._adjust_process_count()
        # a child retired (3.12's only call from the manager), under the
        # executor's shutdown lock, which the manager holds: 3.13's rule
        if self._processes is None or (self._shutdown_thread and not self._pending_work_items):
            return
        if len(self._processes) < self._max_workers:
            self._spawn_process()


#: how long `discard` keeps terminating children before it lets go: the
#: first terminated child normally ends the manager within milliseconds
DISCARD_WAIT_S = 5.0


def _hms() -> str:
    """The time a line of the pool's own is printed, as the worker's log lines
    carry it. On 2026-11-24 the line of the timeout that froze the node had
    neither a time nor a collection: its hour had to be worked out from a
    thread's CPU time, and its stage from a stack."""
    return time.strftime("%H:%M:%S")


def discard(ex: ProcessPoolExecutor, wait_s: float = DISCARD_WAIT_S) -> None:
    """Stop an executor whose answers nobody will read: its children
    TERMINATED until its manager thread has ended, then its queue
    cancelled. Without this the manager ends when the children finish, or
    never if one is stuck or none is left, and the interpreter's exit joins
    it with no bound (`concurrent.futures.process._python_exit`).

    Python 3.14's `ProcessPoolExecutor.terminate_workers()` (gh-128041) is
    the library's call for this, but it shuts the executor down FIRST and
    terminates after. A child that retires at `max_tasks_per_child` in
    between is not replaced (the executor is shutting down), and if it was
    the last one, the parts already handed to the queue have nobody to run
    them: the manager waits for them for ever. Reproduced with children
    retiring after every part: 2 exits in 8 hung on 3.12.3. Here a
    terminated child's sentinel puts the manager on its own broken path
    (it fails every future, closes the queue's reader so its feeder cannot
    block, and joins), and the executor is shut down only once that has
    happened. A child only computes (this module's rule), so terminating one
    loses nothing anyone waits for: `compute` and `compute_pages` compute an
    unanswered part in-process."""
    _discarded.add(ex)
    manager = getattr(ex, "_executor_manager_thread", None)
    if manager is None and not hasattr(ex, "_processes"):
        # not the executor this was written against: the library's call
        terminate = getattr(ex, "terminate_workers", None)
        if terminate is not None:
            terminate()
        else:
            ex.shutdown(wait=False, cancel_futures=True)
        return
    deadline = time.monotonic() + wait_s
    while True:
        for p in list((getattr(ex, "_processes", None) or {}).values()):
            try:
                if p.is_alive():
                    p.terminate()
            except (ValueError, ProcessLookupError):   # closed, or gone already
                pass
        if manager is None or not manager.is_alive() or time.monotonic() >= deadline:
            break
        manager.join(0.02)
    if manager is not None and manager.is_alive():
        print(f"{_hms()} [parse pool] a stopped pool's manager is still waiting after {wait_s:.0f} s",
              file=sys.stderr, flush=True)
    ex.shutdown(wait=False, cancel_futures=True)


#: how often a child looks for its parent, in seconds (`watch_parent`)
ORPHAN_POLL_S = 2.0


def watch_parent() -> None:
    """A CHILD WHOSE PARENT IS GONE STOPS (2026-11-08, München: a SIGTERM to
    `hold collection new` left 18 parse children re-parented to the
    container's init, computing at 31-36 % of a core each for nobody, until
    killed by hand). A daemon thread polls the parent's pid and ends the
    child once it changes — the parent died, and the child was re-parented.
    Linux's PR_SET_PDEATHSIG would be the standard call, but it fires when
    the THREAD that started the child exits, and the worker starts its pools
    from pipeline threads; polling getppid is its usual fallback."""
    parent = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(ORPHAN_POLL_S)
            if os.getppid() != parent:
                os._exit(1)
    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def child_start(init=None, *args) -> None:
    """An initializer for any pool: the parent watched (`watch_parent`), then
    the pool's own initializer, if any."""
    watch_parent()
    if init is not None:
        init(*args)


def _child_init(knobs: dict, base: int | None = None) -> None:
    """A child sits at the WORKER's base nice + 10, whichever thread started
    the pool. A child inherits its starter's nice, and since the pipeline
    threads lower themselves (docs/notes/scheduling.md §12) a relative
    `nice(10)` landed at 15 (live, 2026-09-20) — below the index
    maintainer, whose rule is that it yields to parsing, not the reverse."""
    watch_parent()
    try:
        if base is None:
            os.nice(10)
        else:
            target = min(19, base + 10)
            if os.getpriority(os.PRIO_PROCESS, 0) < target:
                os.setpriority(os.PRIO_PROCESS, 0, target)
    except (AttributeError, OSError):
        pass
    for k, v in knobs.items():
        setattr(config, k, v)
    _first_for_the_oom_killer()


#: a parse child's `oom_score_adj` (2026-11-20): of the nine kills of that
#: morning three took the worker's main process, the one that holds the
#: rounds and writes the hold with what the pool had in flight (app/oom_hold.py).
#: A child goes first; raising one's own score needs no privilege.
CHILD_OOM_SCORE_ADJ = 500


def _first_for_the_oom_killer() -> None:
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write(str(CHILD_OOM_SCORE_ADJ))
    except OSError:
        pass


def _base_nice() -> int | None:
    """The worker's base: its main thread's nice (the main thread's tid is
    the pid)."""
    try:
        return os.getpriority(os.PRIO_PROCESS, os.getpid())
    except (AttributeError, OSError):
        return None


def _knobs() -> dict:
    return {k: getattr(config, k) for k in KNOBS if hasattr(config, k)}


def processes() -> int:
    return max(1, int(config.PARSE_PROCESSES))


def each_part(fn, tasks: list[tuple], *, limit: int | None = None):
    """`fn(*task)` for every task, in the tasks' order: in spawned processes
    when the node allows more than one (`processes()`, at most `limit` —
    the caller's bound where each task holds much memory), here otherwise.
    `tasks` is a list, or a sequence that makes task i when asked for it.

    For a pass an operator runs over a collection's parts (`hold check`, a
    snapshot's delta), not for the worker's rounds: its pool is made for
    the pass and gone after it. LAZY: the caller works on part k while the
    children compute the parts after it — vectors-sample embeds one part's
    sixteen texts while the next parts are cut (docs/notes/multicore-audit.md).
    `fn` is a module-level function, and its tasks and results pickle.
    """
    n = processes() if limit is None else max(1, min(int(limit), processes()))
    if n <= 1 or len(tasks) <= 1:
        for t in tasks:
            yield fn(*t)
        return
    from collections import deque
    # BOUNDED: at most 2n tasks in flight or done and not yet taken. Results
    # the caller has not reached do not pile up when it is the slower side —
    # the records build's plan commits every window it gets (75.9 M rows).
    # A CHILD THE KERNEL KILLED (BrokenProcessPool, a SIGKILL) does not end
    # the pass: a pool of half the size takes the parts not yet answered, in
    # order, and at one process they are computed here (operator,
    # 2026-10-30: a kill recovers by itself, "not involving this agent").
    # EXCEPT FOR MEMORY (operator, 2026-11-19): a child the memory killer
    # took (the cgroup's oom_kill rose, app/oom_hold.py) ends the pass with
    # MemoryKilled — exit 137, held by its supervisor until the cause is
    # known; the same parts met the same document 43 times in one day.
    nxt = 0                                 # the next task to submit
    taken = 0                               # the next result to yield
    from . import oom_hold
    oom_hold.baseline()                     # read BEFORE a child can be killed
    ex = ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn"),
                             initializer=watch_parent)
    window: deque = deque()
    try:
        while taken < len(tasks):
            if ex is None:                  # down to this process
                yield fn(*tasks[taken])
                taken += 1
                continue
            while nxt < len(tasks) and len(window) < 2 * n:
                window.append(ex.submit(fn, *tasks[nxt]))
                nxt += 1
            try:
                got = window[0].result()
            except BrokenProcessPool as e:
                ex.shutdown(wait=False, cancel_futures=True)
                window.clear()
                # killed for MEMORY: the pass ends here and is held (operator,
                # 2026-11-19); any other kill: the unanswered go again
                _raise_if_killed(f"{len(tasks) - taken} part(s) of {getattr(fn, '__name__', fn)} unanswered", e)
                nxt = taken                 # the unanswered go again
                n //= 2
                print(f"{_hms()} [parse pool] a child was killed: {len(tasks) - taken} part(s) go again "
                      + (f"on {n} process(es)" if n > 1 else "in this process"),
                      file=sys.stderr, flush=True)
                ex = (ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn"),
                                          initializer=watch_parent)
                      if n > 1 else None)
                continue
            window.popleft()
            taken += 1
            yield got
    finally:
        # a caller that stopped early (an error, a stop) leaves no part queued
        if ex is not None:
            ex.shutdown(wait=True, cancel_futures=True)


def each_part_unordered(fn, tasks: list[tuple], *, limit: int | None = None):
    """`(i, fn(*tasks[i]))` for every task, AS EACH ENDS — for a pass whose
    parts are independent (counters, lists), so no part waits for another.

    `each_part` yields in the tasks' order with at most 2n parts in flight or
    done and not yet taken: a slow part at the head holds the rest, and its
    children sit idle once 2n are queued (2026-11-22, München: EUR-Lex5's v4
    drift on 4 processes ran on ONE core, two children waiting on their
    pipes, for 17,425 s; CLAUDE.md "Multi-threading is a must"). Here the
    same bound, 2n in flight, is refilled as any part ends. The same
    recovery: a child the kernel killed sends the unanswered parts again on
    half the pool, the memory killer ends the pass (MemoryKilled). A part's
    own exception ends the pass, as in `each_part`."""
    n = processes() if limit is None else max(1, min(int(limit), processes()))
    if n <= 1 or len(tasks) <= 1:
        for i, t in enumerate(tasks):
            yield i, fn(*t)
        return
    from collections import deque
    from . import oom_hold
    oom_hold.baseline()                     # read BEFORE a child can be killed
    ex = ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn"),
                             initializer=watch_parent)
    todo: deque = deque(range(len(tasks)))
    inflight: dict = {}
    try:
        while todo or inflight:
            if ex is None:                  # down to this process
                while todo:
                    i = todo.popleft()
                    yield i, fn(*tasks[i])
                break
            while todo and len(inflight) < 2 * n:
                i = todo.popleft()
                inflight[ex.submit(fn, *tasks[i])] = i
            finished, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            broken = None
            for f in finished:
                i = inflight.pop(f)
                try:
                    got = f.result()
                except BrokenProcessPool as e:
                    broken = e
                    todo.appendleft(i)
                    continue
                yield i, got
            if broken is not None:
                ex.shutdown(wait=False, cancel_futures=True)
                todo = deque(sorted(set(todo) | set(inflight.values())))
                inflight.clear()
                _raise_if_killed(f"{len(todo)} part(s) of {getattr(fn, '__name__', fn)} unanswered", broken)
                n //= 2
                print(f"{_hms()} [parse pool] a child was killed: {len(todo)} part(s) go again "
                      + (f"on {n} process(es)" if n > 1 else "in this process"), file=sys.stderr, flush=True)
                ex = (ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn"),
                                          initializer=watch_parent)
                      if n > 1 else None)
    finally:
        if ex is not None:
            ex.shutdown(wait=True, cancel_futures=True)


def pool() -> ProcessPoolExecutor | None:
    """The executor, created on first use; None when serial or DISABLED."""
    global _pool, _pool_size
    n = processes()
    if n <= 1 or (_disabled and not _retry_due()):
        return None
    from . import oom_hold
    oom_hold.baseline()                     # read BEFORE a child can be killed
    old = None
    with _lock:
        if _pool is None or _pool_size != n:
            # resized: the old pool is stopped (`discard`, below, outside the
            # lock); a failure its callers see is not a new one, and they
            # compute in-process
            old = _pool
            _pool = _Executor(
                max_workers=n, mp_context=multiprocessing.get_context("spawn"),
                initializer=_child_init, initargs=(_knobs(), _base_nice()),
                # bounded memory: a child's plugin caches grow with what it
                # parsed; it is replaced after this many sub-batches
                max_tasks_per_child=max(1, int(config.PARSE_TASKS_PER_CHILD)))
            _pool_size = n
        ex = _pool
    _touch()
    if old is not None:
        discard(old)
    return ex


def _touch() -> None:
    global _last_active
    with _held_lock:
        _last_active = time.monotonic()


def release_idle(idle_s: float, *, now: float | None = None) -> int:
    """The pool stopped (`discard`) when nobody has been inside it for
    `idle_s` seconds: no batch in `compute`/`compute_pages`, no slot held,
    and no `pool()` call meanwhile. Returns how many children it had (0 when
    nothing was released). The next `pool()` starts a fresh one.

    WHY (München, 2026-10-28): the children keep the plugin caches they grew
    while parsing — 12 of them held 10.8 GiB, idle and resident for 26-42
    minutes after the node's quiet profile stopped parsing and through a
    pause, and wikipedia-v8's first snapshot could not start beside them. A
    pause stops the work, not the memory. Called by the worker's beat thread
    (telemetry-shaped, never a pipeline thread); a prefetch in flight is
    cancelled with the pool, and the stage that needed it pays later, as a
    failed prefetch always did. loky's reusable executor shuts idle workers
    down the same way (its `timeout`); this pool stays the standard
    library's, with the gh-115634 fix above."""
    global _pool, _pool_size
    if idle_s <= 0:
        return 0
    with _lock:
        with _held_lock:
            busy = bool(_wanting) or bool(_held)
            idle_for = (time.monotonic() if now is None else now) - _last_active
        if _pool is None or busy or idle_for < idle_s:
            return 0
        ex, n, _pool, _pool_size = _pool, _pool_size, None, 0
    discard(ex)
    return n


# THE LANE'S OWN PROCESS (2026-11-20, after the second OOM hold): an
# estimate is not a bound — 32018R0688 el's fts held 11.18 GB against 3.25
# estimated, and the kernel killed it inside the worker's cgroup. A large
# part (one row, `_isolate_large`) now runs in a process of its own, used
# once, whose data limit (RLIMIT_DATA, POSIX setrlimit: the heap and every
# private writable mapping) is the room its admission granted
# (`_allowance`). Past it, only that process fails — a MemoryError in
# Python, an abort in a Rust allocator (the tokenizer's) — never a child of
# the shared pool and never the main process; and the row is known
# exactly, so it is asked again with twice the room (`remembered`), or,
# past the most this worker can ever give (`never_fits`), it is the
# document's error, said with both numbers. A cgroup kill (the counter
# rose) is still the OOM hold's.
# Standardised alternative, not taken: a cgroup v2 child group per part
# (`memory.max`, the kernel's own bound, Rust allocations included without
# an abort) — it needs the container's cgroup delegated (nsdelegate, a
# writable cgroup.subtree_control), which the node's Docker does not give.
_remembered: dict[tuple[str, str], int] = {}
_remembered_lock = threading.Lock()


def _row_key(collection, row: dict) -> tuple[str, str]:
    """A row's identity for what its parse was seen to need: the manifest's
    path (physical identity) and the uid."""
    return (str(getattr(collection, "manifest_path", None) or getattr(collection, "name", "")),
            str(row.get("uid")))


def remembered(collection, row: dict) -> int:
    """The room a row's parse was seen to need past its estimate (0: none)."""
    with _remembered_lock:
        return _remembered.get(_row_key(collection, row), 0)


def _remember(collection, row: dict, need: int) -> None:
    with _remembered_lock:
        k = _row_key(collection, row)
        _remembered[k] = max(_remembered.get(k, 0), int(need))


def _allowance(need: int) -> int | None:
    """The room a large part is given, as measured once its own estimate is
    reserved: the cgroup's share less what is resident and what the OTHER
    parts in flight reserved — never less than its estimate; None where no
    limit reads (no bound is set)."""
    from .scheduled_checks import cgroup_memory
    got = cgroup_memory()
    if got is None:
        return None
    limit, used = got
    with _reserved_cv:
        others = max(0, _reserved - need)
    return max(int(need), int(limit * ADMIT_FRACTION) - used - others)


def _vm_data() -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmData:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def _compute_bounded(kind: str, collection, rows: list[dict], allowance: int | None):
    """In the lane's own process: its data limit set to what it holds now
    plus `allowance` (never above the hard limit), then the part."""
    if allowance:
        import resource
        vm = _vm_data()
        if vm is not None:
            _soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
            want = vm + int(allowance)
            if hard != resource.RLIM_INFINITY:
                want = min(want, hard)
            resource.setrlimit(resource.RLIMIT_DATA, (want, hard))
    return _compute_heavy(kind, collection, rows)


def _lane_executor() -> ProcessPoolExecutor:
    """One process, one task, gone after it."""
    return ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"),
                               initializer=_child_init, initargs=(_knobs(), _base_nice()),
                               max_tasks_per_child=1)


class _LaneTask:
    """A large part in its own process, as a future `compute` collects: its
    `result` answers per row — the end of its process past its bound
    included — and raises only a timeout, or the cgroup's own kill."""

    def __init__(self, kind: str, collection, part: list[dict], allowance: int | None) -> None:
        self.kind, self.collection, self.part, self.allowance = kind, collection, part, allowance
        self._ex = _lane_executor()
        self._f = self._ex.submit(_compute_bounded, kind, collection, part, allowance)
        self._procs = list((getattr(self._ex, "_processes", None) or {}).values())

    def add_done_callback(self, cb) -> None:
        self._f.add_done_callback(lambda _f: cb(self))

    @property
    def future(self):
        """The executor's own future, what `compute` waits on beside the
        pool's (a stand-in executor in a test may hand back something else)."""
        return self._f

    def cancel(self) -> bool:
        got = self._f.cancel()
        if isinstance(self._ex, ProcessPoolExecutor):
            discard(self._ex)               # its process terminated: nobody reads its answer
        else:
            self._ex.shutdown(wait=False)
        return got

    def _exitcode(self) -> int | None:
        for p in self._procs:
            code = getattr(p, "exitcode", None)
            if code is not None:
                return code
        return None

    def _past_bound(self, why: str) -> list[tuple[bool, object]]:
        given = self.allowance or part_peak(peak_per_byte(self.collection), self.part)
        need = 2 * int(given)
        for row in self.part:
            _remember(self.collection, row, need)
        too_big = never_fits(need, note=f"twice the {given / 2 ** 30:.1f} GB its parse passed ({why})")
        if too_big:
            return [(False, {"type": "TooLarge", "msg": too_big})] * len(self.part)
        return [(False, {"type": "MemoryWait",
                         "msg": f"its parse passed the {given / 2 ** 30:.1f} GB it was given ({why}): "
                                f"asked again with {need / 2 ** 30:.1f} GB"})] * len(self.part)

    def result(self, timeout: float | None = None) -> list[tuple[bool, object]]:
        try:
            rows = self._f.result(timeout=timeout)
        except BrokenProcessPool:
            import signal as _signal

            from . import oom_hold
            self._ex.shutdown(wait=False)
            if oom_hold.killed() is not None:
                raise                       # the cgroup's kill: the OOM hold's, in `compute`
            code = self._exitcode()
            if code == -_signal.SIGABRT:
                return self._past_bound("its process aborted: a Rust allocation refused")
            return [(False, {"type": "RuntimeError",
                             "msg": f"its parse's own process ended by signal {-code if code else '?'}"})] \
                * len(self.part)
        self._ex.shutdown(wait=False)
        if any(not ok and isinstance(v, dict) and v.get("type") == "MemoryError" for ok, v in rows):
            return self._past_bound("a MemoryError")
        return rows


def recycle_for_room(name: str | None, own_slots: int = 0) -> int:
    """The pool stopped (`discard`) because its children's KEEP holds the
    room a part needs (2026-11-20: eleven children at 0.69-1.88 GB, four of
    them idle, beside a 4.12 GB main process; their plugin caches, as
    `release_idle` found on 2026-10-28) — only where no other batch is inside
    the pool or holds a slot, another collection's or another stage's of the
    same one (the caller's own `own_slots` aside), since stopping it under
    them would send their parts in-process. The next `pool()` starts fresh
    children. Returns how many it had (0: nothing stopped)."""
    global _pool, _pool_size
    with _lock:
        with _held_lock:
            others = (any(k != name for k in _wanting) or _wanting.get(name, 0) > 1
                      or any(v for k, v in _held.items() if k != name) or _held.get(name, 0) > own_slots)
        if _pool is None or others:
            return 0
        ex, n, _pool, _pool_size = _pool, _pool_size, None, 0
    discard(ex)
    print(f"{_hms()} [parse pool] its {n} children stopped: what they kept held the room a "
          f"{name} part needed; the next batch starts fresh ones", file=sys.stderr, flush=True)
    return n


def shutdown() -> None:
    """The pool stopped (`discard`): after a failure, and when the process
    stops."""
    global _pool, _pool_size
    with _lock:
        ex, _pool, _pool_size = _pool, None, 0
    if ex is not None:
        discard(ex)


def _stop_at_exit() -> None:
    """Interpreter exit joins every pool's manager thread with no bound
    (`concurrent.futures.process._python_exit`). Registered by the library's
    own mechanism for it, `threading._register_atexit`, after the library's
    own registration, so it runs first (the hooks run last-in, first-out):
    by then nobody reads an answer (main has returned), and a stopped build
    leaves at once instead of after its pages in flight, or never."""
    shutdown()


try:
    threading._register_atexit(_stop_at_exit)
except (AttributeError, RuntimeError):     # no such hook, or the exit has begun
    pass


def _retry_due() -> bool:
    """A disabled pool whose time is up is enabled again, ON PROBATION: its
    failure count one short of PARSE_MAX_FAILURES, so the next failure
    disables it again (for twice as long) and an answered batch clears it
    (`_note_success`). False while the time is not up."""
    global _disabled, _failures
    if time.monotonic() < _disabled_until:
        return False
    with _lock:
        if not _disabled:
            return True
        _disabled = False
        _failures = max(0, int(config.PARSE_MAX_FAILURES) - 1)
    print(f"{_hms()} [parse pool] children again after {_disabled_for(_disabled_times):.0f} s off: on probation "
          f"(one more failure and they are off for {_disabled_for(_disabled_times + 1):.0f} s)",
          file=sys.stderr, flush=True)
    return True


def _disabled_for(times: int) -> float:
    """How long the pool stays off the `times`-th time in a row."""
    base = max(0.0, float(config.PARSE_RETRY_S))
    return min(max(base, float(config.PARSE_RETRY_MAX_S)), base * 2 ** max(0, times - 1))


def _note_success() -> None:
    global _failures, _disabled_times
    if _failures or _disabled_times:
        with _lock:
            _failures = 0
            _disabled_times = 0


def _note_failure(exc: BaseException, ex: ProcessPoolExecutor | None = None, *,
                  killed: bool = False, where: str = "") -> None:
    """A pool that did not answer: rebuild it, and give up on children
    entirely if it keeps happening. Printed, not log_event'd — this module
    has no manifest connection, and the worker's stdout IS the log.

    `ex`, the executor that failed: one already thrown away (by another
    caller's failure, a resize, or the process stopping) is not a new
    failure. Its answers were never coming, and counting it would throw away
    the pool that replaced it."""
    global _failures, _disabled, _disabled_until, _disabled_times
    if ex is not None and ex in _discarded:
        print(f"{_hms()} [parse pool] {type(exc).__name__}{where} from a pool already stopped: this "
              f"batch's parts in flight are parsed again in processes of their own, the rest in-process",
              file=sys.stderr, flush=True)
        return
    shutdown()
    off = 0.0
    with _lock:
        _failures += 1
        failures = _failures
        if failures >= max(1, config.PARSE_MAX_FAILURES) and not _disabled:
            _disabled = True
            _disabled_times += 1
            off = _disabled_for(_disabled_times)
            _disabled_until = time.monotonic() + off
    # what happens to the batch, as it happens (2026-11-20: "computing this
    # batch in-process" was printed for a batch whose child the kernel had
    # killed for memory, which `compute` then held, never computing it here)
    print(f"{_hms()} [parse pool] {type(exc).__name__}{where}: "
          + ("a child killed for memory: the batch is NOT computed again (the worker is held) "
             if killed else "the parts that answered kept, those in flight parsed again in processes "
                            "of their own, the rest computed in-process (a heavy document deferred) ")
          + f"(failure {failures} of {config.PARSE_MAX_FAILURES})"
          + (f"; children OFF for {off:.0f} s, then on probation" if off else ""),
          file=sys.stderr, flush=True)


def reset_failures() -> None:
    global _failures, _disabled, _disabled_until, _disabled_times
    with _lock:
        _failures = 0
        _disabled = False
        _disabled_until = 0.0
        _disabled_times = 0


def health() -> dict:
    return {"processes": processes(), "pool": _pool is not None,
            "failures": _failures, "disabled": _disabled,
            # read WITHOUT resetting: health is a question, not a meter
            # read — `_log_shares` owns the reset (see occupancy).
            "slots_in_flight": occupancy(reset=False)["slots_in_flight"]}


# ---- the work ---------------------------------------------------------------

_family_contexts: dict[str, object] = {}


def _family_context(collection):
    """`slicing.context_for`, once per child per collection (it reads the
    map member and the pack's codes; a row asks it 50,000 times a window)."""
    key = str(getattr(collection, "manifest_path", ""))
    if key not in _family_contexts:
        from .pack import slicing
        _family_contexts[key] = slicing.context_for(collection, log=lambda m: None)
    return _family_contexts[key]


def _one(kind: str, collection, row: dict):
    from . import layers, plugin_io
    plugin = collection.plugin_instance()
    if kind == "chunks":
        # ONE PARSE (docs/notes/one-parse.md): the fresh record's sections
        # where there is one, the plugin's parse otherwise — and the record's
        # `content_hash` on each chunk, which the embed stage and genesis
        # write beside its vector (embeddings.SOURCE_COLUMN)
        from . import record_source
        pair = layers.has_pair_layer(collection)
        title, sections, source = record_source.title_sections_and_source(collection, row, plugin, pair=pair)
        return layers.with_source(layers.chunks_for_document(collection, row, sections, title=title), source)
    if kind == "chunks+names":
        # one parse for both: the chunks the text index cuts, and the names a
        # record is found by (fts.index_names_of; wikidata)
        from . import fts
        doc = plugin_io.canonical_doc(plugin, row)
        return {"chunks": layers.chunks_for_document(collection, row, doc.sections, title=doc.title),
                "names": fts.index_names_of(collection, doc.extra)}
    if kind in ("fts", "fts+names"):
        # the text-index stage's whole per-document work: its chunks, its
        # names where the plugin gives them (wikidata), and the entities its
        # text mentions — the tagging the worker's pipeline thread did under
        # the GIL while this pool sat idle (app/entities.py `entity_ids_of`)
        from . import entities, refs
        if kind == "fts+names":
            from . import fts
            doc = plugin_io.canonical_doc(plugin, row)
            chunks = layers.chunks_for_document(collection, row, doc.sections, title=doc.title)
            names = fts.index_names_of(collection, doc.extra)
        else:
            from . import record_source
            title, sections, _source = record_source.title_sections_and_source(
                collection, row, plugin, pair=False)
            chunks = layers.chunks_for_document(collection, row, sections, title=title)
            names = None
        return {"chunks": chunks, "names": names,
                "entities": entities.entity_ids_of(row.get("title"), [c["text"] for c in chunks],
                                                   refs.RESOLVER, refs.read_store())}
    if kind == "slice-hint":
        # the slice a row is in, without parsing it (app/slice_scope.py): the
        # family context is built once per child and kept
        from .pack import slicing
        return slicing.slice_hint(collection.plugin, row, _family_context(collection))
    if kind == "discover-hash":
        # a scan's fetch-and-hash of a ref whose signature moved, and of the
        # unit its row still names where it never recorded a hash
        # (app/discovery.py `_hashes`): a dated dump's next run is all this
        from .discovery import hash_one
        return hash_one(plugin, row)
    if kind == "metadata":
        return plugin_io.metadata(plugin, row)
    if kind == "prepare":
        return plugin_io.prepare(plugin, row)
    if kind == "origin-line":
        # records-vs-origin's re-parse (app/hold/check.py `verdict`), in a
        # child: the record's origin unit under the collection's base (a local
        # origin), parsed by the Codex's toolchain, its line compared here —
        # the line that travels back is a word, not the record
        from .hold.check import verdict
        from .pack import build as pack_build, records as pack_records
        local, path = plugin_io.local_locator(plugin, row["locator"], collection.corpus_dir,
                                              run=row.get("run"))
        if not local.exists():
            raise FileNotFoundError(str(local))
        record, _layer, _blob, _plates = pack_build._record_of(
            collection, plugin, {"uid": row["uid"], "native_id": row["uid"].rsplit(":", 1)[-1],
                                 "path": path})
        shape = int(getattr(collection.codex, "record_line", 1) or 1)
        return verdict(row["line"], pack_records.canonical_line(record, record_line=shape))
    if kind == "doc":
        # one unit's parsed text, plain (app/ask/history.py `_fact`: a version
        # fetched on request, read under the pool's bounds, never the
        # worker's own process — 2026-11-21, V5); a unit named in part is
        # never read (invariant 11)
        why = plugin_io.stale_unit(plugin, row, collection)
        if why:
            raise plugin_io.StaleUnit(f"{row['uid']}: {why}")
        doc = plugin_io.canonical_doc(plugin, row)
        return {"title": doc.title or "", "sections": [[a or "", b or ""] for a, b in doc.sections],
                "extra": dict(doc.extra or {})}
    raise ValueError(f"unknown work kind {kind!r}")


def _compute(kind: str, collection, rows: list[dict]) -> list[tuple[bool, object]]:
    """Runs in a child (or inline): one (ok, value) per row, in order.
    A failure is (False, {"type", "msg"}) — never an exception out of the
    task, so one bad document never poisons its sub-batch."""
    if kind in ("record", "held-record"):
        # the records build's task: the line encoded here too, in the child
        # (`with_line`), the task's lines shipped together and its layers
        # resolved once (`compute_records`; held: the part's lookups in two
        # queries, `prefetch_held`)
        from .pack.build import compute_records
        return compute_records(kind, collection, rows)
    out: list[tuple[bool, object]] = []
    # THE WORKER WRITES DOWN WHAT IT PARSES IN ITS OWN PROCESS (app/parse_journal.py):
    # a document on which the parse never ends holds the process, the dead-man's
    # switch ends it, and its successor must not walk into the same document. A
    # no-op in a child and in any process that keeps no journal
    from . import parse_journal
    where = parse_journal.where_of(collection)
    for row in rows:
        try:
            with parse_journal.parsing(where, row.get("uid"), kind):
                out.append((True, _one(kind, collection, row)))
        except Exception as e:   # noqa: BLE001 — recorded per document by the caller
            out.append((False, {"type": type(e).__name__, "msg": str(e)[:500]}))
    return out


def raise_for(err: dict):
    """Re-raise a child's failure with the class the worker's error
    handling keys on (an UnindexableDocument stays a corpus fact)."""
    if err.get("type") == "MemoryWait":
        raise MemoryWait(err.get("msg", ""))
    if err.get("type") == "Stopped":
        raise Stopped(err.get("msg", ""))
    from .plugins.base import UnindexableDocument
    if err.get("type") == "UnindexableDocument":
        raise UnindexableDocument(err.get("msg", ""))
    if err.get("type") in ("PlatformMissing", "TokenizerMissing"):
        # the node's condition, deferred by the worker (`worker.platform_missing`
        # keys on the CLASS): a RuntimeError naming it in its message was an
        # error on every document that crossed the pool (2026-10-20)
        from .chunking import PlatformMissing
        raise PlatformMissing(f"{err.get('type')}: {err.get('msg')}")
    raise RuntimeError(f"{err.get('type')}: {err.get('msg')}")


# ----------------------------------------------------------------- admission
#
# WORK-CONSERVING, by what is HELD. The rule here used to be a fair share by
# PRESENCE: divide the children by however many collections had touched the
# pool in the last 30 s. That is wrong in the one way that matters — it hands
# half the machine to a collection that has no work for the pool at all.
# Live on the node, 2026-09-23: gutenberg only EMBEDS, and the embed path
# chunks IN-PROCESS on its own thread (worker._load_chunks) and never enters
# the pool; wikipedia is the only collection parsing and indexing, and it was
# sized for half the children anyway, so six of twelve cores had nothing to
# run on them.
#
# So a collection takes every task slot nobody else is occupying, recomputed
# each time the window is refilled. Alone — the live case — it takes all of
# them.
#
# The old fair share survives as the FLOOR, because it was protecting
# something real: a collection whose documents cost 30 s each would
# otherwise hold every child for its whole batch, and the collection waiting
# behind it holds `BEGIN IMMEDIATE` on its manifest the whole time it
# crawls. So a collection that is inside `compute` counts as wanting the
# pool, and is never admitted below `budget // wanting` however much the
# others are already holding — the surplus queues and the incumbent stops
# refilling past its own share, so the two converge within one wave.
#
# What this is still NOT: CPU weighting. How the kernel divides CPU BETWEEN
# running children is cgroups' job (§11), and it says nothing about the GPU
# feed — the embed thread does not use the pool, and children sit at the
# worker's nice + 10, so feeding the encoder outranks parsing by
# construction (docs/notes/scheduling.md §12).
_held: dict[str, int] = {}                 # collection name -> slots in flight
_wanting: dict[str, int] = {}              # collection name -> batches inside compute
_held_lock = threading.Lock()
#: task slots in flight per child: one running, one queued behind it, so a
#: child never idles while the parent notices the previous result.
IN_FLIGHT_PER_CHILD = 2
#: never fewer than one child and a task queued behind it, whatever the
#: arithmetic says — a share of zero is a stalled collection.
MIN_SLOTS = 2
#: how many tasks a batch is cut into per child (see `compute`).
#:
#: TWO, and it was measured back DOWN to two. Eight was deployed on
#: 2026-09-23 to stop a dear document idling a child, and it cost 4.6x:
#: wikipedia's rdf went from 6.3 to 28.8 ms/doc and fts from 10.2 to 27.2,
#: with the CPU UP (77% -> 193% of 1200%) and throughput DOWN (54.2 -> 24.3
#: docs/s). More CPU for less work is REDUNDANT COMPUTATION — that much the
#: numbers settle. Which redundancy was not isolated; the likeliest is
#: locality, since the parts are cut by equal COUNT and wikipedia's
#: documents come out of shared multistream blocks, so every child holding
#: one of a block's documents decompresses that whole block and four times
#: the parts is four times the re-decompression. (`PARSE_TASKS_PER_CHILD`
#: — a different knob with a confusingly similar name — recycles a child
#: every 500 tasks, so finer parts also recycle more often; that was ~45
#: minutes apart and too rare to be the main term.)
#:
#: Either way the lesson holds and is the reason for the ceiling: a part's
#: cost is NOT independent of the other parts, so balance cannot be bought
#: with granularity. The cut has to follow the blocks first
#: (docs/notes/scheduling.md §11).
PARTS_PER_CHILD = 2
#: rows below which pickling a part outweighs the work in it
MIN_PART = 4


def slots(name: str | None, budget: int) -> int:
    """How many task slots this collection may hold right now: the budget
    less what the OTHERS are actually holding, and never less than its share
    among the collections that are currently asking."""
    with _held_lock:
        others = sum(v for k, v in _held.items() if k != name)
        asking = len(_wanting) + (0 if name in _wanting else 1)
    return max(MIN_SLOTS, budget // max(1, asking), budget - others)


# ---------------------------------------------------------------- occupancy
#
# THE MEASUREMENT THIS MODULE DID NOT HAVE. A starved pool and a slow pool
# look identical from outside: both show long batches. The batch log has
# duration and size, `docker stats` has the container, and the sizer will
# faithfully fit a cost model to a machine running on one core — so on
# 2026-09-23 admission handed wikipedia 3 of 11 children for a day and
# every instrument agreed things were merely slow.
#
# WHAT THIS IS, EXACTLY — read this before quoting it. It is the time
# average of TASKS IN FLIGHT (submitted and not yet collected), capped at
# the pool size. It answers the ADMISSION question: did the pool have work
# it was allowed to run? It does NOT say how many children were running,
# and the two diverge badly, because `compute` submits a whole batch at
# once (parts ~= PARTS_PER_CHILD x n = the cap) and then drains it: the
# slots stay "in flight" all the way through the straggler tail, while the
# children that finished early sit idle.
#
# Measured on the node the night this was written: this metric read 73-81 %
# while children actually in state R averaged 3.7 of 11 (34 %) — sampled at
# 1 Hz, `tools/sched_measure.sh`. Both numbers are true and they are
# answers to different questions. It was first shipped labelled
# "busy_children", which was simply wrong, and the log row it produced
# overstated the change it was measuring.
#
# Integrated over time, not sampled — a sample lands between batches as
# often as inside one.
_busy_slots = 0                    # tasks in flight, all collections
_busy_integral = 0.0               # child-seconds accrued since _busy_read_at
_busy_since = time.monotonic()     # last point the integral was advanced to
_busy_read_at = time.monotonic()   # last occupancy(reset=True)


def _advance(now: float, n: int) -> None:
    """Accrue child-seconds up to `now`. Caller holds _held_lock."""
    global _busy_integral, _busy_since
    if now > _busy_since:
        _busy_integral += min(_busy_slots, n) * (now - _busy_since)
    _busy_since = now


def occupancy(reset: bool = True) -> dict:
    """How busy the pool has been SINCE THE LAST READ: the time-average
    number of children with work, and that as a share of the pool.

    Published by the beat thread, never by a pipeline thread (CLAUDE.md:
    telemetry is published by someone other than the thread doing the work).
    """
    global _busy_integral, _busy_read_at
    n = processes()
    with _held_lock:
        now = time.monotonic()
        _advance(now, n)
        span = now - _busy_read_at
        child_s = _busy_integral
        if reset:
            _busy_integral = 0.0
            _busy_read_at = now
    busy = child_s / span if span > 0 else 0.0
    return {"slots_in_flight": round(busy, 2), "processes": n,
            "utilisation": round(busy / n, 4) if n else 0.0,
            "elapsed_s": round(span, 1)}


def _occupy(name: str | None, delta: int) -> None:
    global _busy_slots
    n = processes()
    with _held_lock:
        _advance(time.monotonic(), n)
        _busy_slots = max(0, _busy_slots + delta)
        got = _held.get(name, 0) + delta
        if got > 0:
            _held[name] = got
        else:
            _held.pop(name, None)


def _want(name: str | None, delta: int) -> None:
    global _last_active
    with _held_lock:
        _last_active = time.monotonic()     # entering or leaving: not idle now
        got = _wanting.get(name, 0) + delta
        if got > 0:
            _wanting[name] = got
        else:
            _wanting.pop(name, None)


def held() -> dict[str, int]:
    with _held_lock:
        return dict(_held)


def wanting() -> dict[str, int]:
    with _held_lock:
        return dict(_wanting)


def forget_users() -> None:
    global _busy_slots, _busy_integral, _busy_since, _busy_read_at
    with _held_lock:
        _held.clear()
        _wanting.clear()
        _busy_slots = 0
        _busy_integral = 0.0
        _busy_since = _busy_read_at = time.monotonic()


def _spans(collection, rows) -> list[tuple[str, int]] | None:
    """`plugin_io.work_span` for every row, or None when this plugin has no
    shared work units (then the plain equal-count cut is equal work)."""
    try:
        from . import plugin_io
        plugin = collection.plugin_instance()
        out = [plugin_io.work_span(plugin, r) for r in rows]
    except Exception:   # noqa: BLE001 — a hint, never a reason to fail a batch
        return None
    return out if any(s is not None for s in out) else None


def _partition(collection, rows: list[dict], want: int) -> list[list[dict]]:
    """Cut `rows` into at most `want` CONTIGUOUS parts.

    Contiguous because the rows arrive in the corpus's own order, which for
    a dump is stream order — so neighbouring rows already share work, and
    any reordering would scatter it.

    By COST and on UNIT BOUNDARIES when the plugin says what the units are
    (`plugin_io.work_span`), because equal COUNT is not equal WORK:
    wikipedia's pages come in multistream chunks of ~100, a chunk costs its
    decompression whoever touches it, and `_stream_pages` caches one chunk
    per process. Cutting mid-chunk decompresses it twice; cutting by count
    leaves parts of wildly different cost, and the batch then ends with ten
    children idle behind one. Both were measured on the node 2026-09-23 —
    4.6x for the first, 3.7 of 11 children running for the second.

    Equal count otherwise, which is what gutenberg and pubmed want: their
    documents are independent files and a row IS the unit.

    SIMULATED against real locators sampled from the node's manifest before
    it was deployed (55 pages per chunk; chunk bytes p10 6.9k, p50 26k, max
    186k — a 27x spread, which is the straggler), cutting into ~22 parts for
    eleven children and scheduling the parts onto them:

        rows/chunks      total work      makespan      worst part
        2200 /  48         0.72x          0.64x        242k -> 92k
        6000 / 109         0.89x          0.74x        452k -> 186k
        3000 /  39         0.70x          0.46x        412k -> 169k

    Work falls because chunks stop being decompressed by two children;
    makespan falls further because the worst part is what the batch waits
    for. A simulation is not the node — this must still be measured there.
    """
    spans = _spans(collection, rows)
    if spans is None:
        size = max(MIN_PART, -(-len(rows) // want))
        return [rows[i:i + size] for i in range(0, len(rows), size)]

    # RUNS: the contiguous rows sharing one unit. A run is indivisible — the
    # whole point — so the cut happens between runs and never inside one. A
    # unit is charged ONCE, to the run, plus the cheap per-row parse.
    runs: list[tuple[list[dict], float]] = []
    for i, row in enumerate(rows):
        key = spans[i][0] if spans[i] else None
        prev_key = spans[i - 1][0] if i and spans[i - 1] else None
        if runs and key is not None and key == prev_key:
            runs[-1][0].append(row)
            runs[-1] = (runs[-1][0], runs[-1][1] + 1.0)
        else:
            runs.append(([row], float(spans[i][1]) if spans[i] else 1.0))
    target = sum(c for _, c in runs) / max(1, want)

    parts: list[list[dict]] = []
    cur: list[dict] = []
    acc = 0.0

    def close() -> None:
        nonlocal cur, acc
        if cur:
            parts.append(cur)
            cur, acc = [], 0.0

    # `want` IS A TARGET SIZE, NOT A CAP — and that distinction was worth
    # 2x. Refusing to open part number `want` forces every remaining run
    # into the last one: simulated on real locators, the final part cost
    # 607k against equal-count's worst of 242k, and the makespan across
    # eleven children came out 2.03x WORSE even though total work fell to
    # 0.72x. Extra parts are free here in the way they were not before,
    # because a part never splits a unit: nothing is decompressed twice
    # however many parts there are. Overshooting `want` by a third beats
    # one part carrying the tail.
    for rows_of, cost in runs:
        # CLOSE BEFORE OVERSHOOTING, not after. Closing only once the budget
        # is already spent bundles a dear unit with everything ahead of it:
        # with one unit costing 100x its neighbours the part that gets it
        # carried five others too, and that part IS the straggler tail.
        if cur and acc + cost > target:
            close()
        cur.extend(rows_of)
        acc += cost
        if acc >= target:
            close()
    close()
    return parts


# ------------------------------------------------------- the large-document lane
#
# THE NODE, 2026-11-19 (München): 41 OOM kills of the worker in a day, its
# 24 GiB cap reached. EUR-Lex5's largest acts are 180-204 MB of Formex each,
# in 24 languages that sit next to one another in rowid order. Measured in a
# container of its own, one of them (32021R2017 el, 204,218,240 bytes)
# peaks at 5.25 GB to chunk (25.7x its bytes) in 63.1 s, and 2.06 GB for its
# metadata (10.1x) in 21.4 s. Three in flight at once, beside the parse
# pool's other children (~0.93 GB each) and the main process (6-7 GB), pass
# the cap. And a batch of them outlived `compute`'s 120 s deadline (the
# worker's log: 3 documents, 351 s of parse), so the pool was thrown away
# and the whole batch parsed AGAIN in the main process — the main process
# was what the kernel killed at 19:14 and 20:23.
#
# So a plugin whose rows' `size` is the bytes their parse reads declares how
# much a parse holds per byte (`PARSE_PEAK_PER_BYTE`; EUR-Lex 26, measured).
# A row whose peak reaches LARGE_PEAK_BYTES is a part of its own, never more
# than one such part is in flight in the process, it starts only when the
# worker's cgroup has room for its peak, its batch's deadline is extended by
# the time its bytes take, and it is never parsed in the main process: where
# the pool cannot take it, it is DEFERRED (`MemoryWait`), never an error on
# the document (CLAUDE.md, "deferral is not failure").
#
# Standardised alternative, not taken: Kubernetes-style per-task memory
# requests on a scheduler that places tasks by them (Ray's `memory=` on a
# task, Dask's worker resources). Adopting one would replace this pool; it
# would pay once many plugins declare peaks — one does today.

#: WHAT THE POOL HOLDS NOW, for a memory kill's hold (app/oom_hold.py): per
#: part in flight, its collection, its kind, its rows' uids and sizes. Written
#: by the batch that submits (two dict operations a part), read by the
#: worker's watcher only when the kernel has killed.
_in_flight: dict[int, dict] = {}
_in_flight_lock = threading.Lock()
#: the rows named per part in a hold (the rest are counted)
IN_FLIGHT_ROWS = 20


def _enter(key: int, kind: str, collection, part: list[dict], *, peak: int = 0) -> None:
    with _in_flight_lock:
        _in_flight[key] = {"collection": getattr(collection, "name", None), "kind": kind, "rows": len(part),
                           "bytes": sum(int(r.get("size") or 0) for r in part),
                           "parse_bytes": sum(parse_size_of(r) for r in part), "estimated_peak": peak,
                           "uids": [[r.get("uid"), r.get("size")] for r in part[:IN_FLIGHT_ROWS]]}


def _leave(key: int) -> None:
    with _in_flight_lock:
        _in_flight.pop(key, None)


def in_flight() -> list[dict]:
    """The parts the pool holds now, largest first."""
    with _in_flight_lock:
        return sorted((dict(v) for v in _in_flight.values()), key=lambda v: -v["bytes"])


#: a row whose estimated parse peak reaches this takes the large lane
LARGE_PEAK_BYTES = 2 * 2 ** 30
#: the time a large row's parse may take, per byte: 32021R2017's 63.1 s for
#: 204,218,240 bytes, alone, times four for the children it shares the cores with
LARGE_SECONDS_PER_BYTE = 4 * 63.1 / 204_218_240
#: how long a large row waits for the lane and for room before it is
#: deferred: bounded, and short enough that a stop is honoured soon after.
#: Once: a row deferred is asked again, never waited for (`deferred_before`)
LARGE_WAIT_S = 120.0
_large_lane = threading.BoundedSemaphore(1)


class MemoryWait(RuntimeError):
    """A document too large to parse beside what runs now: deferred, never
    an error on it (the worker's `transient_failure`); retried next round."""


class MemoryKilled(MemoryWait):
    """A process of this cgroup was killed for memory (app/oom_hold.py;
    operator, 2026-11-19: "not relauched until the cause has been
    determined"): what it held is NOT computed again — not on a pool of
    half the size, not in this process, where the same part would kill the
    worker itself. In the worker a deferral, the worker being held and
    stopping (`oom_hold.killed` ran its handler); a pass ends with exit 137
    (app/louise.py `main`), which its supervisor holds."""

    def __init__(self, before: int, after: int, what: str) -> None:
        super().__init__(f"the kernel killed a parse-pool child for memory ({what}; the cgroup's oom_kill "
                         f"{before} -> {after}): not computed again until the cause is known")
        self.before, self.after = before, after


def _raise_if_killed(what: str, cause: BaseException | None = None) -> None:
    from . import oom_hold
    k = oom_hold.killed()
    if k is not None:
        raise MemoryKilled(k[0], k[1], what) from cause


# ---- a batch's STOP and its HEARTBEAT (2026-11-21, P4 and P7 of the review) -----
#
# P7: a stop was not looked at inside `compute`: a large part runs ~730 s
# (32021R2017 el, 4 x 63.1 s x its bytes) against docker's 120 s grace, and
# the worker was SIGKILLed mid-unit. P4: a look-ahead let go of only cancelled
# its Future; its `compute` went on holding the large lane while the stage
# parsed the same rows again. So the caller of `compute` may hand it, for the
# calling thread (`controlled`), a STOP — anything with `is_set()`: the
# worker's stop, its mode holding parsing, a look-ahead abandoned — honoured
# at PART BOUNDARIES: nothing more is submitted, a part already answered is
# kept, the parts in flight are let go of (a large part's own process
# terminated, its lane and room given back), and every row not computed is
# answered (False, {"type": "Stopped"}) — a deferral (`raise_for` raises
# `Stopped`), never an error on the document. Every wait inside `compute` is
# a slice of at most WAIT_SLICE_S, so a stop is seen within one. And a BEAT:
# called at each slice while `compute` waits within its own bound, and at
# each part answered — what a look-ahead's taker reads to tell a batch still
# parsing (wait for it, never parse it twice) from one gone quiet.
# Thread-local, so the `*_for_rows` calls and their callers keep their
# signatures. The standard it follows: cooperative cancellation by a token
# (.NET's CancellationToken; Python's own `threading.Event` as the token).

#: the longest a batch waits before it looks at its stop and beats again
WAIT_SLICE_S = 1.0
_ctl = threading.local()


class Stopped(RuntimeError):
    """The batch's stop was set before this row was computed: a deferral,
    the document untouched (the worker's `transient_failure`)."""


@contextmanager
def controlled(stop=None, beat=None):
    """`compute` on this thread honours `stop` (`is_set()`) at its part
    boundaries and calls `beat()` while it waits within its bound."""
    old = (getattr(_ctl, "stop", None), getattr(_ctl, "beat", None))
    _ctl.stop, _ctl.beat = stop, beat
    try:
        yield
    finally:
        _ctl.stop, _ctl.beat = old


def _stopped() -> bool:
    stop = getattr(_ctl, "stop", None)
    try:
        return bool(stop is not None and stop.is_set())
    except Exception:   # noqa: BLE001 — a stop that cannot answer stops nothing
        return False


def _beat() -> None:
    beat = getattr(_ctl, "beat", None)
    if beat is not None:
        try:
            beat()
        except Exception:   # noqa: BLE001 — a heartbeat is telemetry, never a failure
            pass


def _slice() -> float:
    """One wait: WAIT_SLICE_S, and never more than a quarter of the
    watchdog's bound, so a heartbeat is never late for it."""
    return max(0.01, min(WAIT_SLICE_S, float(config.PARSE_TIMEOUT_S) / 4))


def _stopped_rows(part: list[dict]) -> list[tuple[bool, object]]:
    return [(False, {"type": "Stopped", "msg": "its batch was stopped before it was computed "
                                               "(the worker stopping, or its parse let go of)"})] * len(part)


def peak_per_byte(collection) -> float:
    """The plugin's `PARSE_PEAK_PER_BYTE`: what its parse holds per byte of
    a row's `size`; 0 where it declares none (its rows' size is a
    container's, or it was never measured) — those rows never take the
    lane. Read once a batch: `plugin_instance` checks a fetched copy's
    integrity."""
    try:
        return float(getattr(type(collection.plugin_instance()), "PARSE_PEAK_PER_BYTE", 0) or 0)
    except Exception:   # noqa: BLE001 — a collection without a plugin here: no lane
        return 0.0


def peak_of(k: float, row: dict) -> int:
    """The bytes a parse of `row` is expected to hold (`peak_per_byte` × the
    bytes its parse reads, `parse_size_of`)."""
    try:
        return int(k * parse_size_of(row)) if k else 0
    except (TypeError, ValueError):
        return 0


# WHAT A PARSE READS IS NOT ALWAYS THE ROW'S `size` (2026-11-21, P3 of the
# review). A unit's `size` is its files' bytes on disk — discovery's change
# test, and it stays that. A Formex PACKAGE (a .zip the unit holds, opened by
# app/plugins/eurlex/formex.py `unpack`) deflates ~4.3x, so its estimate was
# a quarter of what its parse holds: a big package in an uncapped shared
# child. A plugin may say what its parse reads (`plugin_io.parse_size`: the
# eurlex source counts a package's members unpacked, from the zip's central
# directory, without inflating one); asked once per unit and kept, keyed on
# the unit's PATH and size (physical identity; a unit that changes has
# another size and is asked again).
_parse_sizes: "OrderedDict[tuple[str, int], int]" = OrderedDict()
_parse_sizes_lock = threading.Lock()
#: the units whose parse size is kept (FIFO): ~150 bytes each
PARSE_SIZES_KEPT = 65_536


def _size_key(row: dict) -> tuple[str, int] | None:
    """A manifest row's unit: its path and size. None for a task that
    carries no size (a discovery hash's): it is estimated as before, at 0."""
    path, size = row.get("path"), row.get("size")
    if not path or size is None:
        return None
    try:
        return (str(path), int(size))
    except (TypeError, ValueError):
        return None


def parse_size_of(row: dict) -> int:
    """The bytes a parse of `row` reads: its plugin's answer where one was
    learnt (`learn_parse_sizes`), else the row's `size`."""
    key = _size_key(row)
    got = _parse_sizes.get(key) if key is not None else None
    if got is not None:
        return got
    return int(row.get("size") or 0)


def learn_parse_sizes(collection, rows: list[dict]) -> int:
    """Ask the plugin what each row's parse reads, for the rows not asked
    yet — only for a plugin that declares a peak per byte and answers
    `parse_size`. Never raises (an answer is a hint; `size` stands in).
    Returns how many rows were asked."""
    try:
        from . import plugin_io
        plugin = collection.plugin_instance()
    except Exception:   # noqa: BLE001 — no plugin here: the sizes stand
        return 0
    if not plugin_io.answers_parse_size(plugin):
        return 0
    asked = 0
    for row in rows:
        key = _size_key(row)
        if key is None or key in _parse_sizes:
            continue
        try:
            got = plugin_io.parse_size(plugin, row)
        except Exception:   # noqa: BLE001 — a unit that cannot be read is the parse's to say
            got = None
        asked += 1
        with _parse_sizes_lock:
            _parse_sizes[key] = int(got) if got is not None and got >= 0 else key[1]
            while len(_parse_sizes) > PARSE_SIZES_KEPT:
                _parse_sizes.popitem(last=False)
    return asked


def _large(k: float, part: list[dict]) -> int:
    """A large part's peak (it holds one row), else 0."""
    peak = peak_of(k, part[0]) if len(part) == 1 else 0
    return peak if peak >= LARGE_PEAK_BYTES else 0


def _isolate_large(k: float, parts: list[list[dict]]) -> list[list[dict]]:
    """Every row whose peak reaches LARGE_PEAK_BYTES alone in a part, the
    parts' order and every other row's place kept."""
    out: list[list[dict]] = []
    for part in parts:
        run: list[dict] = []
        for row in part:
            if peak_of(k, row) >= LARGE_PEAK_BYTES:
                if run:
                    out.append(run)
                    run = []
                out.append([row])
            else:
                run.append(row)
        if run:
            out.append(run)
    return out


def _room(peak: int) -> bool:
    """Has the worker's cgroup room for `peak` more bytes? True where no
    limit reads (no cgroup v2): the lane alone bounds it then."""
    from .scheduled_checks import cgroup_memory
    got = cgroup_memory()
    if got is None:
        return True
    limit, used = got
    return limit - used >= peak


def never_fits(peak: int, *, note: str | None = None) -> str | None:
    """Why a parse of `peak` bytes can never run in this worker, or None: an
    estimate past the cgroup's whole limit is not a moment's shortage, and a
    deferral would ask again every round for ever (München's park of
    2026-11-19: 32015R2446R(01) de, 911 MB, ~24 GB at 26x, under a 24 GiB
    cap). It is the document's error, said with both numbers, so a person
    sees it — raise the worker's memory, or make the parse hold less."""
    from .scheduled_checks import cgroup_memory
    got = cgroup_memory()
    if got is None or peak < got[0] * NEVER_FITS_FRACTION:
        return None
    return (f"its parse is estimated at {peak / 2 ** 30:.1f} GB ({note or LARGE_PEAK_NOTE}), more than "
            f"{NEVER_FITS_FRACTION:.0%} of the worker's whole memory limit ({got[0] / 2 ** 30:.1f} GB), "
            f"the room it can ever have beside the worker's own process: it is never admitted until "
            f"the limit is raised or the parse holds less")


#: the share of the cgroup's limit a parse can ever be given: the main
#: process alone held 6-7 GB of the 24 GiB at the kills of 2026-11-19
#: (München), so room past two thirds of the limit never comes
NEVER_FITS_FRACTION = 2 / 3
#: what the estimate is, for the message a too-large document carries
LARGE_PEAK_NOTE = "the plugin's PARSE_PEAK_PER_BYTE times its size"


# THE POOL'S MEMORY (2026-11-20, München: nine OOM kills of the worker from
# 03:39Z to 04:08Z under EUR-Lex5's text index, the killed processes holding
# 4.5-8.6 GB, every other collection idle). The lane above counts ONE large
# row; the 40-82 MB acts below its threshold went through the ordinary pool,
# eleven slots of them, several to a part — and a child holds every result
# of its part until the part ends, beside the parse in hand. So, for a plugin
# that declares its peak per byte:
#   - a part is CUT where its rows' estimated peaks sum past PART_PEAK_BYTES
#     (a large row stays alone, as before): the estimate of a part is the sum
#     of its rows', which covers the results a child keeps;
#   - every part in flight RESERVES its estimate from one budget per process,
#     POOL_MEMORY_FRACTION of the cgroup's limit; a part that does not fit
#     waits for the parts in flight to finish — at once where its batch has
#     some, bounded by LARGE_WAIT_S where it has none — and is then deferred
#     (MemoryWait), never parsed in the main process.
# A plugin that declares no peak reserves nothing and is cut as before; with
# no cgroup limit to read there is no budget (the lane's rules still hold).
# Measured before it, not assumed: glibc gives lxml's memory back after a
# 64 MB parse (RssAnon 391 MB at the peak, 73 MB after, with or without
# malloc_trim), so a child's high-water is not what is kept: what it holds
# is what its part has computed.
# Standardised: a weighted semaphore (bytes), the admission Ray and Dask do
# with a task's declared `memory` resource; see the note above on adopting one.

#: the share of the cgroup's limit the pool's estimates may hold at once: the
#: room a parse can ever have beside the main process (NEVER_FITS_FRACTION)
POOL_MEMORY_FRACTION = NEVER_FITS_FRACTION
#: a part's rows are cut where their estimates sum past this: the lane's
#: threshold, so an ordinary part never holds more than a large row does
PART_PEAK_BYTES = LARGE_PEAK_BYTES
_reserved = 0
_reserved_cv = threading.Condition()


def pool_memory() -> int | None:
    """The bytes the pool's estimates may hold at once, or None (no limit)."""
    from .scheduled_checks import cgroup_memory
    got = cgroup_memory()
    return int(got[0] * POOL_MEMORY_FRACTION) if got is not None else None


def part_peak(k: float, part: list[dict]) -> int:
    """A part's estimate: the sum of its rows' (`peak_of`)."""
    return sum(peak_of(k, r) for r in part) if k else 0


def _cut_by_peak(k: float, parts: list[list[dict]]) -> list[list[dict]]:
    """`_isolate_large`, and every run cut where its estimates would sum past
    PART_PEAK_BYTES; the order of the rows kept. Nothing moves for a plugin
    that declares no peak."""
    if not k:
        return parts
    out: list[list[dict]] = []
    for part in _isolate_large(k, parts):
        if len(part) == 1:
            out.append(part)
            continue
        run: list[dict] = []
        held = 0
        for row in part:
            p = peak_of(k, row)
            if run and held + p > PART_PEAK_BYTES:
                out.append(run)
                run, held = [], 0
            run.append(row)
            held += p
        out.append(run)
    return out


#: THE MEASURED SIDE (2026-11-20, the first OOM hold, 14:42:50Z): the
#: estimates in flight were 7.12 GB, inside the 16 GB budget, while the
#: cgroup already held 18.5 GB — the main process 4.12 GB and eleven children
#: at 0.69-1.88 GB, four of them with nothing in flight: what a child keeps
#: between parts is outside every estimate. The two killed held 2.5 and 2.9 GB,
#: its keep plus its part. So a part is admitted only where the cgroup's
#: WORKING SET (`cgroup_memory`: current less inactive_file, the kubelet's
#: figure), plus every estimate in flight, plus its own, stays within this
#: share of the limit. The estimates in flight are counted whole, the part of
#: them already resident twice: the safe side, and its cost is fewer heavy
#: parts at once when the children are large.
ADMIT_FRACTION = 0.85
#: a batch of ONE row is computed in the main process (no pickling) only
#: below this estimate; above it, it is admitted like any part
IN_PROCESS_PEAK_BYTES = 256 * 2 ** 20


def _measured_room(need: int) -> bool:
    """Room in the cgroup, as measured now, for `need` beside what is in
    flight; True where no limit reads."""
    from .scheduled_checks import cgroup_memory
    got = cgroup_memory()
    if got is None:
        return True
    limit, used = got
    return used + _reserved + need <= limit * ADMIT_FRACTION


def _reserve(need: int, *, wait: bool, until: float) -> bool:
    """`need` bytes of the pool's budget, at once or — `wait` — until
    `until`: within the budget (granted where nothing is reserved: a part
    that fits the limit at all runs alone; `never_fits` refused the others)
    AND within the cgroup's measured room (`_measured_room`, never
    exempted: a pool whose idle children hold the room defers the part, it
    does not start it). False when it is not to be had."""
    global _reserved
    budget = pool_memory()
    with _reserved_cv:
        while ((budget is not None and _reserved and _reserved + need > budget)
               or not _measured_room(need)):
            if not wait or time.monotonic() >= until or _stopped():
                return False
            _reserved_cv.wait(min(_slice(), max(0.01, until - time.monotonic())))
            _beat()                         # a bounded wait of its own: alive
        _reserved += need
        return True


#: a child gives back the heap a heavy part freed (glibc's `malloc_trim`,
#: after `gc.collect`): above this resident memory, after a part that
#: reserved an estimate. Measured where it runs: each give-back of at least
#: TRIM_SAY_BYTES is said, so whether a child's keep was free heap or held
#: objects is read off the worker's log, not assumed (a 64 MB lxml parse
#: alone kept nothing, 2026-11-20; the node's idle children kept ~1 GB).
TRIM_ABOVE_BYTES = 512 * 2 ** 20
TRIM_SAY_BYTES = 64 * 2 ** 20


def _rss_anon() -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("RssAnon:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


#: this process's give-back threshold: TRIM_ABOVE_BYTES, raised past what a
#: give-back could not free (held objects, not free heap), so a child whose
#: heap is held is collected again only once it has grown TRIM_ABOVE_BYTES / 2
#: beyond it, never on every part
_trim_floor = TRIM_ABOVE_BYTES


def _give_back() -> tuple[int, int] | None:
    """(before, after) when this process held more than its threshold and
    gave back what it could; None otherwise. Never raises."""
    global _trim_floor
    before = _rss_anon()
    if before is None or before < _trim_floor:
        return None
    try:
        import ctypes
        import gc
        gc.collect()
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        return None
    after = _rss_anon()
    if after is None:
        return None
    _trim_floor = (TRIM_ABOVE_BYTES if before - after >= TRIM_SAY_BYTES
                   else max(TRIM_ABOVE_BYTES, after + TRIM_ABOVE_BYTES // 2))
    return before, after


def _compute_heavy(kind: str, collection, rows: list[dict]) -> list[tuple[bool, object]]:
    """`_compute`, then the child gives back what the part freed, EVERY part
    (2026-11-24). Only parts with an estimate (a plugin declaring its peak,
    EUR-Lex) did: every other plugin's children kept their peak heap for
    life, and Normattiva's full fts re-index at chunker v4 grew twelve of
    them to 1.6-1.75 GB. With the main process and a light compaction's
    Lance child, that crossed the worker's 24 GB cap, and the kernel killed
    one. Below the threshold it is one read of /proc/self/status; past it a
    collect and a trim, said when they free TRIM_SAY_BYTES, and raised past
    what they cannot free (`_trim_floor`)."""
    out = _compute(kind, collection, rows)
    got = _give_back()
    if got is not None and got[0] - got[1] >= TRIM_SAY_BYTES:
        # the collection and the rows, since 2026-11-24: five parts of 40-53 MB
        # ended in the 13 s before a batch of THREE documents, and the line
        # could not say whose they were (CLAUDE.md: every log line names its
        # collection)
        print(f"{_hms()} [parse pool] child {os.getpid()} gave back {(got[0] - got[1]) / 2 ** 20:.0f} MB after "
              f"a {kind} part of {sum(int(r.get('size') or 0) for r in rows) / 2 ** 20:.1f} MB "
              f"({len(rows)} row{'s' if len(rows) != 1 else ''} of {getattr(collection, 'name', '?')}; "
              f"{got[0] / 2 ** 30:.2f} -> {got[1] / 2 ** 30:.2f} GB resident)", file=sys.stderr, flush=True)
    return out


def _reservation_releaser(need: int):
    """The reservation given back ONCE: the part's future done, or its batch
    failed (as `_lane_releaser`)."""
    done = threading.Lock()

    def release(_f=None) -> None:
        global _reserved
        if done.acquire(blocking=False):
            with _reserved_cv:
                _reserved = max(0, _reserved - need)
                _reserved_cv.notify_all()
    return release


def reserved() -> int:
    """The bytes the parts in flight have reserved now."""
    with _reserved_cv:
        return _reserved


# A ROW DEFERRED IS ASKED AGAIN, NEVER WAITED FOR AGAIN (2026-11-23, defect 3
# of the EUR-Lex5 review). A row the lane or the pool's memory deferred stays
# pending and untouched, so the next round selects it FIRST again
# (`_RDF_PENDING`, `_FTS_FRESH`, the look-ahead's carry): part 0, nothing of
# its batch in flight, and the admission below waited LARGE_WAIT_S for it
# EVERY round while other large parses held the lane — the batch's other rows
# with it — and taking the lane is a poll, not a turn: the wait bought it no
# place. A part holding such a row is now ASKED (one try of the lane and the
# room, no wait) and, refused, deferred at once; the rows behind it go on. A
# fresh row still waits once, bounded, as before. Kept per row, keyed on the
# manifest's path and the uid (physical identity, `_row_key`), until the row
# is admitted; FIFO-bounded.
# Not taken, "put it last in the next batch": the admission waits for a large
# part whenever nothing of its batch is in flight, so at the batch's end it
# would wait LARGE_WAIT_S all the same, the other rows' answers held unwritten.
# Standardised alternative for the TURN itself, not taken: a fair (FIFO)
# semaphore for the lane (Java's `Semaphore(1, true)`, a ticket lock); a
# deferred row would have to hold its ticket across rounds, between which it
# waits for nothing — worth it only if a deferred row is seen starved.
_deferred_rows: "OrderedDict[tuple[str, str], None]" = OrderedDict()
_deferred_rows_lock = threading.Lock()
#: the rows remembered as deferred (FIFO): ~150 bytes each
DEFERRED_KEPT = 4_096


def deferred_before(collection, part: list[dict]) -> bool:
    """Whether a row of `part` was deferred by the lane or the pool's memory
    and has not been admitted since."""
    with _deferred_rows_lock:
        return any(_row_key(collection, r) in _deferred_rows for r in part)


def _note_deferred(collection, part: list[dict], deferred: bool = True) -> None:
    with _deferred_rows_lock:
        for r in part:
            key = _row_key(collection, r)
            if not deferred:
                _deferred_rows.pop(key, None)
                continue
            _deferred_rows[key] = None
            _deferred_rows.move_to_end(key)
        while len(_deferred_rows) > DEFERRED_KEPT:
            _deferred_rows.popitem(last=False)


def forget_deferred() -> None:
    """Tests: no row deferred."""
    with _deferred_rows_lock:
        _deferred_rows.clear()


def _waited(again: bool) -> str:
    """How long a deferred part was waited for, for its message."""
    return "now (deferred before: asked again, not waited for)" if again else f"within {LARGE_WAIT_S:.0f} s"


def _take_lane(peak: int, *, wait: bool, until: float) -> bool:
    """The large lane, with room for `peak`: at once, or — `wait` — until
    `until`. False when it is not to be had."""
    while True:
        if _large_lane.acquire(blocking=False):
            if _room(peak):
                return True
            _large_lane.release()
        if not wait or time.monotonic() >= until or _stopped():
            return False
        time.sleep(min(1.0, _slice()))
        _beat()                             # a bounded wait of its own: alive


def _lane_releaser():
    """The lane given back ONCE, by whichever comes first: the part's future
    finishing, or its batch failing (a pool whose manager is stuck never
    finishes its futures, and the lane would stay taken for good)."""
    done = threading.Lock()

    def release(_f=None) -> None:
        if done.acquire(blocking=False):
            _large_lane.release()
    return release


def _deferred(kind: str, collection, part: list[dict], why: str) -> list[tuple[bool, object]]:
    """A large part's rows, deferred: never parsed in the main process."""
    return [(False, {"type": "MemoryWait", "msg": why})] * len(part)


def _in_process(kind: str, collection, rows: list[dict], k: float) -> list[tuple[bool, object]]:
    """The fallback of a pool that cannot answer: the rows computed here, but
    a large one deferred — parsing it in the main process is how the worker
    was killed (2026-11-19)."""
    if _stopped():
        return _stopped_rows(rows)
    if not any(peak_of(k, r) >= LARGE_PEAK_BYTES for r in rows):
        return _compute(kind, collection, rows)
    out: list[tuple[bool, object]] = []
    for row in rows:
        if peak_of(k, row) >= LARGE_PEAK_BYTES:
            out.extend(_deferred(kind, collection, [row], "a large document waits for the parse pool"))
        else:
            out.extend(_compute(kind, collection, [row]))
    return out


def _here(kind: str, collection, rows: list[dict], k: float) -> list[tuple[bool, object]]:
    """What a pool that could not answer leaves to this process — after a
    failure, or while it is off (`_disabled`): the LIGHT rows computed here
    (`_in_process`), every row whose estimate reaches IN_PROCESS_PEAK_BYTES
    deferred until the pool takes it (P2 of the 2026-11-21 review: nothing
    heavy runs in the main process — a 1.5 GB act parsed beside its 4-7 GB
    was how the worker itself was killed, 2026-11-19). In the rows' order."""
    heavy = [peak_of(k, r) >= IN_PROCESS_PEAK_BYTES for r in rows]
    if not any(heavy):
        return _in_process(kind, collection, rows, k)
    light = [r for r, h in zip(rows, heavy) if not h]
    done = iter(_in_process(kind, collection, light, k) if light else [])
    return [_deferred(kind, collection, [r], "a heavy document waits for the parse pool, never parsed in "
                                             "the worker's own process")[0] if h else next(done)
            for r, h in zip(rows, heavy)]


# ---- WHAT WAS IN FLIGHT WHEN THE POOL FAILED IS NEVER PARSED HERE (2026-11-24) ---------
#
# A batch's wait ends in a failure when no part answered for PARSE_TIMEOUT_S
# or the pool broke. The parts in flight then hold, or may hold, the document
# a child hung on or died of. Until 2026-11-24 the light ones were parsed
# again in THIS process: on the node, a pool child hung on one EUR-Lex
# directive (a regex that never ended), the watchdog fired after 120 s, and
# the worker's pipeline thread then parsed the same document itself — one C
# call, the GIL with it, for 21 minutes: no collection embedded, SIGTERM was
# not honoured, the node needed an agent. The bounded wait's fallback had
# turned a hung child into a hung worker.
#
# Now those parts are parsed again in PROCESSES OF THEIR OWN (`_lane_executor`:
# one process, one task, gone after it), each bounded by PARSE_TIMEOUT_S: a
# part first, then, where its process does not come back, each of its rows
# alone. A row that does not come back alone is that document and nothing
# else: it is answered as an ERROR on it (`ParseNoAnswer`), which takes it
# out of every selection until someone looks. The failure was then the
# document's, not the pool's, and does not count toward switching the pool
# off (`_document_failed`). Parts never submitted were not in flight: they
# go on as before (`_here`).
#
# The standard for this is a subprocess with a timeout (POSIX has no way to
# stop a thread; CPython's `re` holds the GIL and takes no deadline). The
# third-party `regex` module takes a `timeout=` per call: adopting it would
# bound the citation patterns themselves, at the price of a dependency and
# of porting every pattern; it would not bound a parser written in C.
#: how many of a failed batch's parts are parsed again at once, each in a
#: process of its own: two fresh interpreters beside the worker and the
#: pool's replacement, never a second pool
ISOLATE_AT_ONCE = 2
#: what a fresh interpreter is given to start and import before its
#: PARSE_TIMEOUT_S begins: the pool's children are warm, this one is not
ISOLATE_START_S = 10.0


class _CannotIsolate(RuntimeError):
    """No process of its own could be started."""


def _alone(kind: str, collection, groups: list[list[dict]], bound_s: float) -> list[list | None]:
    """Each group of rows parsed in a process of its own, ISOLATE_AT_ONCE at
    a time: its rows' answers, or None for a group whose process did not
    answer within `bound_s` — terminated — or ended without answering. The
    caller's stop ends it (`Stopped`, what is running terminated)."""
    out: list[list | None] = [None] * len(groups)
    running: dict[int, tuple] = {}
    nxt = 0
    try:
        while nxt < len(groups) or running:
            if _stopped():
                raise Stopped("stopped while a failed batch's parts were parsed again")
            while nxt < len(groups) and len(running) < ISOLATE_AT_ONCE:
                ex = None
                try:
                    ex = _lane_executor()
                    # as a large part goes (`_LaneTask`), with no data limit set
                    f = ex.submit(_compute_bounded, kind, collection, groups[nxt], None)
                except (RuntimeError, OSError) as e:
                    if ex is not None:
                        discard(ex)
                    raise _CannotIsolate(f"{type(e).__name__}: {e}") from e
                running[nxt] = (ex, f, time.monotonic() + bound_s)
                nxt += 1
            live = [w for w in (_waitable(f) for _ex, f, _d in running.values())
                    if w is not None and not w.done()]
            if live:
                wait(live, timeout=_slice(), return_when=FIRST_COMPLETED)
            now = time.monotonic()
            for i, (ex, f, deadline) in list(running.items()):
                w = _waitable(f)
                if w is not None and not w.done():
                    if now >= deadline:
                        discard(ex)              # terminated: it did not come back
                        del running[i]
                    continue
                try:
                    # done — or a future-like that answers when asked, within its bound
                    got = f.result(timeout=0 if w is not None else max(0.0, deadline - now))
                except Exception:   # noqa: BLE001 — its process ended without answering
                    got = None
                out[i] = got if isinstance(got, list) and len(got) == len(groups[i]) else None
                ex.shutdown(wait=False)
                del running[i]
            _beat()
    finally:
        for ex, f, _d in running.values():
            f.cancel()
            discard(ex)
    return out


#: (manifest path, uid) of the rows that did not come back ALONE, in a process
#: of their own, in this process's life (`_isolated`): answered at once the
#: next time they are asked — never sent to the pool again, never computed
#: here. One that failed as a peer of its batch is left pending by its stage
#: and comes back, in the end as its batch's ONLY row: and one light row is
#: computed in this process (`compute`), which is the hang again.
_gone_alone: "OrderedDict[tuple[str, str], None]" = OrderedDict()
_gone_alone_lock = threading.Lock()
GONE_ALONE_KEPT = 4_096


def gone_alone(collection, row: dict) -> bool:
    with _gone_alone_lock:
        return _row_key(collection, row) in _gone_alone


def forget_gone_alone() -> None:
    """Tests: no row remembered."""
    with _gone_alone_lock:
        _gone_alone.clear()


def _no_answer(bound_s: float) -> tuple[bool, object]:
    return (False, {"type": "ParseNoAnswer",
                    "msg": f"its parse did not come back from a process of its own within {bound_s:.0f} s "
                           f"(the parse pool had failed with it in flight; alone, it failed again): "
                           f"never parsed in the worker's own process"})


def _isolated(kind: str, collection, parts: list[list[dict]], k: float,
              cannot: Callable[[list[dict]], list[tuple[bool, object]]] | None = None,
              were: str = "were in flight") -> tuple[list[list[tuple[bool, object]]], int]:
    """The answers of the parts that were IN FLIGHT when their pool failed,
    none of them computed in this process, and how many documents did not
    come back alone (each an error on itself). A row whose estimate reaches
    IN_PROCESS_PEAK_BYTES is deferred, as `_here` defers it. `cannot` answers
    the rows when no process can be started (default: a deferral)."""
    bound = float(config.PARSE_TIMEOUT_S) + ISOLATE_START_S
    name = getattr(collection, "name", collection)
    answers: list[list] = [[None] * len(part) for part in parts]
    groups: list[list[dict]] = []
    where: list[tuple[int, list[int]]] = []
    for pi, part in enumerate(parts):
        light = []
        for ri, row in enumerate(part):
            if peak_of(k, row) >= IN_PROCESS_PEAK_BYTES:
                answers[pi][ri] = _deferred(kind, collection, [row], "a heavy document waits for the parse "
                                            "pool, never parsed in the worker's own process")[0]
            else:
                light.append(ri)
        if light:
            groups.append([part[ri] for ri in light])
            where.append((pi, light))
    print(f"{_hms()} [parse pool] {name}: {sum(len(g) for g in groups)} document(s) of {len(groups)} part(s) "
          f"{were}: parsed again in processes of their own, {ISOLATE_AT_ONCE} at a time, none in this one",
          file=sys.stderr, flush=True)
    failed = 0

    def gone(pi: int, ri: int, row: dict) -> None:
        nonlocal failed
        failed += 1
        answers[pi][ri] = _no_answer(bound)
        with _gone_alone_lock:
            _gone_alone[_row_key(collection, row)] = None
            while len(_gone_alone) > GONE_ALONE_KEPT:
                _gone_alone.popitem(last=False)
        print(f"{_hms()} [parse pool] {name}: document {row.get('uid')} did not come back from a process of its own "
              f"within {bound:.0f} s: an error on it", file=sys.stderr, flush=True)

    def rest(answer: Callable[[dict], tuple[bool, object]]) -> None:
        for pi, part in enumerate(parts):
            for ri, row in enumerate(part):
                if answers[pi][ri] is None:
                    answers[pi][ri] = answer(row)
    try:
        singles: list[list[dict]] = []
        at: list[tuple[int, int]] = []
        for (pi, ris), rows, got in zip(where, groups, _alone(kind, collection, groups, bound)):
            if got is not None:
                for ri, a in zip(ris, got):
                    answers[pi][ri] = a
            elif len(rows) == 1:
                gone(pi, ris[0], rows[0])
            else:
                singles.extend([row] for row in rows)
                at.extend((pi, ri) for ri in ris)
        if singles:
            for (pi, ri), rows, got in zip(at, singles, _alone(kind, collection, singles, bound)):
                if got is not None:
                    answers[pi][ri] = got[0]
                else:
                    gone(pi, ri, rows[0])
    except Stopped:
        rest(lambda row: _stopped_rows([row])[0])
    except _CannotIsolate as e:
        print(f"{_hms()} [parse pool] {name}: no process of its own could be started ({e})", file=sys.stderr, flush=True)
        why = ("in flight when the parse pool failed, and no process of its own could be started: asked "
               "again, never parsed in the worker's own process")
        rest((lambda row: cannot([row])[0]) if cannot is not None
             else (lambda row: _deferred(kind, collection, [row], why)[0]))
    return answers, failed


def _document_failed() -> None:
    """The failure just counted (`_note_failure`) was a DOCUMENT's: it failed
    again alone, in a process of its own. The pool is not at fault, and is
    not switched off for it — off, its work would run in this process."""
    global _failures, _disabled, _disabled_until, _disabled_times
    with _lock:
        _failures = max(0, _failures - 1)
        if _disabled and _failures < max(1, int(config.PARSE_MAX_FAILURES)):
            _disabled = False
            _disabled_until = 0.0
            _disabled_times = max(0, _disabled_times - 1)


def _waitable(f) -> Future | None:
    """The `concurrent.futures.Future` behind a part in flight — the pool's,
    or a large part's own process's — or None for a future-like that answers
    only when asked (a test's stand-in): that one is waited for by its own
    `result(timeout)`, as before."""
    if isinstance(f, Future):
        return f
    inner = getattr(f, "future", None)
    return inner if isinstance(inner, Future) else None


def _answered(f):
    """A part in flight's rows when it finished cleanly, else None."""
    w = _waitable(f)
    if w is None or not w.done() or w.cancelled():
        return None
    try:
        if w.exception(timeout=0) is not None:
            return None
        return f.result(timeout=0)
    except Exception:   # noqa: BLE001 — a part that did not finish is not kept
        return None


def _in_flight_count(futures) -> int:
    """Parts of a batch still computing: a part answered and waiting for its
    turn holds no child, so it holds no slot (`compute_pages` learnt it on
    2026-10-25). A future-like that answers only when asked counts as
    computing until it is asked."""
    n = 0
    for _part, f in futures:
        if isinstance(f, list):
            continue
        w = _waitable(f)
        n += 1 if w is None or not w.done() else 0
    return n


def _watchdog(progress: float, futures, ext: dict[int, float]) -> float:
    """When a batch that hears nothing gives up: PARSE_TIMEOUT_S after its
    last answer — or after its first part went in — and never before a large
    part in flight has had the time its bytes take (`ext`)."""
    bound = progress + float(config.PARSE_TIMEOUT_S)
    for _part, f in futures:
        if id(f) in ext:
            w = _waitable(f)
            if w is None or not w.done():
                bound = max(bound, ext[id(f)])
    return bound


def compute(kind: str, collection, rows) -> list[tuple[bool, object]]:
    """chunks | metadata | record for every row, in order. `rows` may be
    sqlite3.Row objects (converted; only plain fields travel). The calling
    thread's stop and heartbeat (`controlled`) are honoured at part
    boundaries: a row not computed by then is answered `Stopped`."""
    plain = [dict(r) if not isinstance(r, dict) else r for r in rows]
    if not plain:
        return []
    if _stopped():
        return _stopped_rows(plain)
    if any(gone_alone(collection, r) for r in plain):
        # a row that did not come back alone, in a process of its own, is
        # answered as it was then: not asked of the pool again, and never
        # computed here — where one light row would be (below)
        known = [gone_alone(collection, r) for r in plain]
        rest = iter(compute(kind, collection, [r for r, g in zip(plain, known) if not g]))
        bound = float(config.PARSE_TIMEOUT_S) + ISOLATE_START_S
        return [_no_answer(bound) if g else next(rest) for g in known]
    from . import parse_journal
    if parse_journal.any_suspect():
        # A SUSPECT — in progress in the worker's own process when that process
        # ended (app/parse_journal.py) — is parsed ALONE, in a process of its
        # own, never here and never beside others in the pool: what comes back
        # decides, its answer or an error on it. A heavy one is left to the
        # large lane, which is a process of its own already
        k0 = peak_per_byte(collection)
        where = parse_journal.where_of(collection)
        sus = [parse_journal.strikes(where, r.get("uid")) > 0 and peak_of(k0, r) < IN_PROCESS_PEAK_BYTES
               for r in plain]
        if any(sus):
            alone, _failed = _isolated(kind, collection, [[r] for r, s in zip(plain, sus) if s], 0.0,
                                       were="were in progress in the worker's own process when it last ended")
            alone_it = iter(alone)
            rest = iter(compute(kind, collection, [r for r, s in zip(plain, sus) if not s]))
            out_s: list[tuple[bool, object]] = []
            for r, s in zip(plain, sus):
                if not s:
                    out_s.append(next(rest))
                    continue
                got = next(alone_it)[0]
                if got[0] or not (isinstance(got[1], dict) and got[1].get("type") in ("MemoryWait", "Stopped")):
                    parse_journal.resolved(where, r.get("uid"))     # answered for good, either way
                out_s.append(got)
            return out_s
    ex = pool()
    k = peak_per_byte(collection)
    if k:
        learn_parse_sizes(collection, plain)    # a package counted unpacked (P3)
    big = any(peak_of(k, r) >= LARGE_PEAK_BYTES for r in plain)
    if ex is None:
        # serial by configuration: everything here, as always (a large row
        # deferred); the pool OFF for now: only the light rows (`_here`)
        return (_here if processes() > 1 else _in_process)(kind, collection, plain, k)
    if len(plain) == 1 and not big and part_peak(k, plain) < IN_PROCESS_PEAK_BYTES:
        # one light row is cheaper here than pickled; one that carries an
        # estimate goes through the pool's admission, never into the main
        # process unmeasured (2026-11-20)
        return _compute(kind, collection, plain)
    n = processes()
    parts = _partition(collection, plain, n * PARTS_PER_CHILD)
    if k:
        parts = _cut_by_peak(k, parts)      # the large rows alone, every part within PART_PEAK_BYTES
    # ADMISSION (docs/notes/scheduling.md §10, B) — see `slots` above. The
    # budget is the whole pool; the cap is recomputed on every refill, so a
    # collection alone holds all of it and gives slots up only to a
    # collection that is really using them.
    name = getattr(collection, "name", None)
    budget = n * IN_FLIGHT_PER_CHILD
    # THE NODE PROFILE'S CORES (app/cpu_limit.py), read once per unit: at
    # most that many parts in flight. A unit is never stalled half-way: under
    # a pause (0) the one already begun finishes on one child, and the pause
    # itself is its caller's, at the unit boundary (worker._cpu_paused)
    lim = cpu_limit.current()
    # BOUNDED: the pool is an optimisation, and a caller that waits on it
    # forever is worse than no pool at all — live 2026-08-28, pubmed's
    # pipeline thread sat in the executor with every child gone, so the
    # collection stopped and SIGTERM could not have been honoured either.
    # THE WATCHDOG IS PER PART, not per batch (2026-11-21, P2 of the review:
    # one deadline for the whole batch; a batch of three large acts outlived
    # it, and the WHOLE batch was parsed again in the main process, the rows
    # already answered included — the main process the kernel killed): the
    # batch gives up when NO part has answered for PARSE_TIMEOUT_S, a large
    # part in flight having the time its bytes take (`_watchdog`), as
    # `compute_pages` does. Then what answered is KEPT, the parts that did not
    # are computed here if light and deferred if heavy (`_here`), and the
    # pool is rebuilt; after PARSE_MAX_FAILURES in a row it is off for
    # PARSE_RETRY_S and comes back on probation (`_retry_due`). A pool broken
    # by the MEMORY killer is not: MemoryKilled, a deferral, the worker held
    # (2026-11-19). Every wait is a slice (`_slice`): the stop is looked at,
    # the heartbeat given, and a part that answers out of order frees its
    # slot at once (`_in_flight_count`).
    #
    # THE WINDOW IS KEPT FULL, not refilled a wave at a time. It used to
    # submit a wave of tasks, wait for ALL of them, and only then submit the
    # next — so at every wave boundary the children drained to idle while the
    # last task of the wave finished alone, and a batch cut into three waves
    # paid that three times. MEASURED on the node, 2026-09-22: a document
    # costs 59 ms of CPU on its own, and the pipeline thread saw 41 ms per
    # document with ELEVEN children — a speedup of 1.4 where the pool exists
    # to give ten. Now a slot is refilled the moment its result is taken, so
    # the window is full from the first submission to the last. Results are
    # still consumed in order, so a failing document still costs itself and
    # never its neighbours.
    from collections import deque
    futures: deque = deque()              # (part, a future, or its rows answered here), in order
    lanes: list = []                      # this batch's large-lane releases (one-shot)
    gives_of: dict[int, list] = {}        # id(future) -> its own releases
    ext: dict[int, float] = {}            # id(future) of a large part -> the time its bytes take
    short_of_room = False                 # a part deferred for the cgroup's room (`recycle_for_room`)
    stopped = False
    mine = 0
    out: list[tuple[bool, object]] = []
    nxt = 0
    _want(name, 1)
    try:
        progress = time.monotonic()
        while nxt < len(parts) or futures:
            if _stopped():
                stopped = True
                break
            cap = cpu_limit.cap(slots(name, budget), lim, floor=1)
            while nxt < len(parts) and _in_flight_count(futures) < cap:
                part = parts[nxt]
                peak = _large(k, part)
                seen = remembered(collection, part[0]) if peak else 0
                peak = max(peak, seen)
                too_big = (never_fits(peak, note="twice the room its parse passed before" if seen > 0 else None)
                           if peak else None)
                if too_big:
                    futures.append((part, [(False, {"type": "TooLarge", "msg": too_big})]))
                    nxt += 1
                    continue
                gives: list = []                  # this part's one-shot releases
                # a part the pool deferred before is asked, never waited for
                # again (`deferred_before`; defect 3 of the EUR-Lex5 review)
                again = bool(k) and deferred_before(collection, part)
                if peak:
                    # one large part in flight in the process, with room for
                    # its peak: wait for it only when nothing else is in
                    # flight here to drain meanwhile
                    if not _take_lane(peak, wait=not futures and not again,
                                      until=time.monotonic() + LARGE_WAIT_S):
                        if futures or _stopped():
                            break
                        _note_deferred(collection, part)
                        futures.append((part, _deferred(kind, collection, part,
                                                        f"no room for a {peak / 2 ** 30:.1f} GB parse "
                                                        f"{_waited(again)}")))
                        nxt += 1
                        continue
                    gives.append(_lane_releaser())
                need = max(part_peak(k, part), peak)
                if need:
                    # THE POOL'S MEMORY: the estimates of every part in flight
                    # within the budget, waited for as the lane is
                    if not _reserve(need, wait=not futures and not again,
                                    until=time.monotonic() + LARGE_WAIT_S):
                        for give in gives:
                            give()
                        if futures or _stopped():
                            break
                        # deferred with nothing of its batch in flight: where the
                        # cgroup has no room, the children's keep is what holds it
                        short_of_room = short_of_room or not _measured_room(need)
                        _note_deferred(collection, part)
                        futures.append((part, _deferred(kind, collection, part,
                                                        f"no room in the parse pool's memory for a part estimated "
                                                        f"at {need / 2 ** 30:.1f} GB {_waited(again)}")))
                        nxt += 1
                        continue
                    gives.append(_reservation_releaser(need))
                if again:
                    _note_deferred(collection, part, deferred=False)     # admitted: forgotten
                lanes.extend(gives)
                idle = _in_flight_count(futures) == 0
                try:
                    # a LARGE part in a process of its own, bounded by the room
                    # granted; every other part in the shared pool
                    f = (_LaneTask(kind, collection, part, _allowance(need)) if peak else
                         ex.submit(_compute_heavy, kind, collection, part))
                except BaseException:
                    for give in gives:
                        give()
                    raise
                for give in gives:
                    f.add_done_callback(give)
                gives_of[id(f)] = gives
                if peak:
                    ext[id(f)] = (time.monotonic() + config.PARSE_TIMEOUT_S
                                  + LARGE_SECONDS_PER_BYTE * parse_size_of(part[0]))
                if idle:
                    progress = time.monotonic()   # the watchdog counts from a part in flight
                _enter(id(f), kind, collection, part, peak=need)
                futures.append((part, f))
                nxt += 1
                mine += 1
                _occupy(name, 1)
            if not futures:
                continue                          # a stop seen while admitting: said at the top
            part, oldest = futures[0]
            if isinstance(oldest, list):          # a part deferred, nothing in flight for it
                futures.popleft()
                out.extend(oldest)
                continue
            w = _waitable(oldest)
            if w is not None and not w.done():
                # the oldest still computing: wait for ANY part, a slice at a time
                now = time.monotonic()
                bound = _watchdog(progress, futures, ext)
                if now >= bound:
                    raise TimeoutError(f"no part answered for {config.PARSE_TIMEOUT_S:.0f} s")
                live = [x for x in (_waitable(g) for _p, g in futures if not isinstance(g, list))
                        if x is not None and not x.done()]
                done, _ = wait(live, timeout=min(_slice(), max(0.0, bound - now)), return_when=FIRST_COMPLETED)
                if done:
                    progress = time.monotonic()
                _beat()
                continue
            # answered — or a future-like that answers when asked, within its bound
            bound = max(progress + config.PARSE_TIMEOUT_S, ext.get(id(oldest), 0.0))
            got = oldest.result(timeout=max(1.0, bound - time.monotonic()))
            futures.popleft()
            out.extend(got)
            _leave(id(oldest))
            ext.pop(id(oldest), None)
            gives_of.pop(id(oldest), None)
            mine -= 1
            _occupy(name, -1)
            progress = time.monotonic()
            _beat()
        if stopped:
            # THE STOP, at this part boundary: what answered is kept, what is
            # in flight let go of (a large part's own process terminated, its
            # lane and room given back), and nothing more is computed
            for part, f in futures:
                if isinstance(f, list):
                    out.extend(f)
                    continue
                got = _answered(f)
                if got is None:
                    f.cancel()
                    if isinstance(f, _LaneTask):
                        for give in gives_of.get(id(f), ()):
                            give()
                _leave(id(f))
                out.extend(got if got is not None else _stopped_rows(part))
            futures.clear()
            for part in parts[nxt:]:
                out.extend(_stopped_rows(part))
            return out
        _note_success()
        return out
    except (RuntimeError, TimeoutError, OSError, CancelledError) as e:
        # RuntimeError: BrokenProcessPool, or "cannot schedule new futures
        # after shutdown" from a pool another caller stopped meanwhile
        from . import oom_hold
        # FIRST, while its parts are still in flight: a child killed for
        # memory runs the worker's handler, which records them in the hold
        kill = oom_hold.killed()
        kept: list = []                    # (part, its rows, or None: in flight and not answered)
        for part, f in futures:
            if isinstance(f, list):
                kept.append((part, f))
                continue
            got = _answered(f)
            if got is None:
                f.cancel()
            _leave(id(f))
            kept.append((part, got))
        futures.clear()
        for give_back in lanes:
            give_back()
        _note_failure(e, ex, where=f" in {kind} of {getattr(collection, 'name', collection)}",
                      **({"killed": True} if kill is not None else {}))
        if kill is not None:
            # its rows are not parsed again here, where they would kill the
            # worker itself (2026-11-19: 43 kills in a day)
            raise MemoryKilled(kill[0], kill[1], f"{kind} of {getattr(collection, 'name', collection)}") from e
        # the parts that answered are KEPT. Those IN FLIGHT and not answered
        # are parsed again in processes of their own, never here: one of them
        # may be what the child hung on or died of (`_isolated`, 2026-11-24).
        # Those never submitted, here — the light ones; a heavy one deferred
        # (`_here`)
        lost = [part for part, got in kept if got is None]
        again: list = []
        if lost and not _stopped():
            again, failed = _isolated(kind, collection, lost, k)
            if failed:
                _document_failed()
        again_it = iter(again)
        for part, got in kept:
            if got is None:
                got = next(again_it, None) or _stopped_rows(part)
                _beat()
            out.extend(got)
        for part in parts[nxt:]:
            out.extend(_stopped_rows(part) if _stopped() else _here(kind, collection, part, k))
            _beat()
        return out
    finally:
        for _part, f in futures:
            if not isinstance(f, list):
                _leave(id(f))
        if short_of_room:
            recycle_for_room(name, own_slots=mine)
        # whatever is still held on the way out — the timeout and the broken
        # pool leave a window's worth — or the next collection inherits a
        # budget that has been eaten by a batch that is long over.
        if mine:
            _occupy(name, -mine)
        _want(name, -1)


def _finished(f):
    """A future's result when it finished cleanly, else None."""
    try:
        return f.result(timeout=0) if f.done() and not f.cancelled() and f.exception(timeout=0) is None else None
    except Exception:   # noqa: BLE001 — a part that did not finish is computed again
        return None


#: ROWS `compute_pages` may hold read ahead of the page its caller takes
#: next — the memory bound of the stream, counted in rows because pages are
#: not one size (≤500 records parsed, ≤4,000 held). 2026-10-25, München's
#: profile at 12 children: at two pages ahead, one long article in the
#: oldest page held back every later page. 2026-10-30, wikidata's build at
#: 16 pages of 500 (8,000 rows, ~4 s of work): the children waited 47 % of
#: their samples in the pool's get while the parent waited 51 % for the next
#: part — one large entity in the oldest page stalled the look-ahead. 64,000
#: rows is the 16 pages of 4,000 the held build already had, and ~30 s of
#: wikidata's parsing; a record waiting in it is a few KB.
ROWS_AHEAD = 64_000


def _running(inflight) -> int:
    """Parts submitted and not answered yet: the children they occupy."""
    return sum(1 for *_x, f in inflight if f is not None and not f.done())


class Interrupted(Exception):
    """`compute_pages` was paused by the node profile and its `stop` was set
    meanwhile: what was in flight was handed over, nothing more is coming.
    The caller handles it as its stop at a page boundary (invariant 2)."""


def _submit(ex, kind, collection, part):
    """A part to the pool, or None — broken, or stopped by another caller
    meanwhile — and then it is computed in-process."""
    try:
        return ex.submit(_compute_heavy, kind, collection, part)
    except RuntimeError as e:
        _note_failure(e, ex, where=f" in {kind} of {getattr(collection, 'name', collection)}")
        return None


def compute_pages(kind: str, collection, pages, *, stop=None, say=None):
    """`compute` over a STREAM of pages: one result list per page, in order,
    with the pool's window kept full ACROSS pages — the parts of page k+1
    are submitted while page k's are still running and while the caller
    works on page k's results.

    WHY (München's profile of wikipedia-v8's records build, 2026-10-24):
    `compute` per page of ≤500 documents waits for its slowest part before
    the next page is even read, so every page ended on one long article with
    the other children idle — 47.8 % of all samples were children waiting
    for work, two children did the parsing, ~1.3 of 4 cpus busy — and its
    deadline covered the WHOLE batch, so a page past PARSE_TIMEOUT_S was
    thrown away and parsed again serially (15 times in 18 h).

    Here the timeout bounds the wait for the NEXT result (no progress for
    PARSE_TIMEOUT_S), the standard watchdog; on a timeout or a broken pool
    the parts not yet answered — and only they — are computed in-process,
    the finished ones kept, and the next pages go to a fresh pool. Parts are
    cut per page by `_partition` (unit boundaries, `work_span`), as before.
    A generator: closing it cancels what is in flight.

    THE NODE PROFILE (app/cpu_limit.py, operator 2026-10-27): parts are
    submitted one at a time while the parts RUNNING are below
    `min(the window, the profile's cores)`, the cores re-read at each page
    boundary (`say` gets each change, once). `0` pauses: nothing new is
    submitted or read, what is in flight finishes and its pages are handed
    over (the caller commits what they complete), and then the stream waits,
    bounded, until a profile allows cores again — or raises `Interrupted`
    when `stop` is set meanwhile. Results never depend on the limit: a part
    is the same part whenever it runs, and pages come out in order."""
    from collections import deque
    name = getattr(collection, "name", None)
    watch = cpu_limit.Watch(say)
    lim = watch.limit()                   # re-read at each page boundary, below
    pages = iter(pages)
    inflight: deque = deque()            # (page_no, part_no, part_rows, future or None = in-process)
    queued: deque = deque()              # (page_no, part_no, part_rows): cut, waiting for a slot
    owner: dict = {}                      # future -> the executor it was submitted to
    results: dict[int, list] = {}         # page_no -> list of per-part results, in part order
    page_rows: dict[int, int] = {}        # page_no -> its rows, until it is yielded
    ahead = 0                             # rows read and not yielded yet (ROWS_AHEAD)
    next_page = 0                         # pages read so far
    emit = 0                              # the next page to yield
    exhausted = False
    _want(name, 1)
    mine = 0
    deadline = time.monotonic() + config.PARSE_TIMEOUT_S

    def submit_queued(ex, cap: int) -> None:
        """What waits, oldest first, while the parts RUNNING are below the
        cap — a part that answered but waits behind a slower one for its
        page's turn holds no child, so it holds no slot either."""
        nonlocal mine
        while queued and (ex is None or _running(inflight) < cap):
            page_no, i, part = queued.popleft()
            fut = _submit(ex, kind, collection, part) if ex is not None else None
            if fut is not None:
                owner[fut] = ex
                mine += 1
                _occupy(name, 1)
            inflight.append((page_no, i, part, fut))

    try:
        while True:
            ex = pool()
            n = processes()
            # THE PROFILE BOUNDS THE PARTS COMPUTING, and a part queued behind
            # a busy child computes nothing. Clamped to the cores, a window of
            # exactly one part per child left every child that finished idle
            # until this generator ran again — and it is suspended while the
            # build works on a page: wikidata-bge's held build, 12 children,
            # cap 12, children ~32 % busy, the parent 76 % idle (München,
            # 2026-10-31). Where the profile allows every child, the window
            # keeps IN_FLIGHT_PER_CHILD (a task queued behind each); below
            # that, the cores cap it, as before.
            window = slots(name, n * IN_FLIGHT_PER_CHILD)
            cap = window if lim.cores is None or lim.cores >= n else cpu_limit.cap(window, lim)
            if lim.cores != 0:
                submit_queued(ex, cap)
            # read pages ahead, once what was read is all submitted
            while not exhausted and not queued and lim.cores != 0 \
                    and (ex is None or _running(inflight) < cap) and (ahead == 0 or ahead < ROWS_AHEAD):
                page = next(pages, None)
                if page is None:
                    exhausted = True
                    break
                rows = [dict(r) if not isinstance(r, dict) else r for r in page]
                page_rows[next_page] = len(rows)
                ahead += len(rows)
                parts = ([rows] if ex is None or len(rows) <= 1
                         else _partition(collection, rows, n * PARTS_PER_CHILD)) if rows else []
                results[next_page] = [None] * len(parts)
                if ex is None or len(rows) <= 1:
                    inflight.extend((next_page, i, part, None) for i, part in enumerate(parts))
                else:
                    queued.extend((next_page, i, part) for i, part in enumerate(parts))
                    submit_queued(ex, cap)
                next_page += 1
                if ex is None:
                    break                 # serial: one page at a time
            # yield every page whose parts are all in
            while emit < next_page and all(r is not None for r in results[emit]):
                out: list = []
                for r in results.pop(emit):
                    out.extend(r)
                ahead -= page_rows.pop(emit)
                emit += 1
                yield out
                # a page boundary: the profile's cores, again (cached, TTL)
                lim = watch.limit()
                # the caller's time with a page is not the pool's silence
                deadline = time.monotonic() + config.PARSE_TIMEOUT_S
            if not inflight:
                if exhausted and not queued:
                    return
                if lim.cores == 0:
                    # PAUSED, with nothing left in flight: wait at this
                    # boundary, bounded, honouring the caller's stop
                    if not cpu_limit.wait_while_paused(stop, watch):
                        raise Interrupted("stopped while the node profile paused the pool")
                    lim = watch.limit()
                    deadline = time.monotonic() + config.PARSE_TIMEOUT_S
                continue
            page_no, i, part, fut = inflight[0]
            if fut is None:
                inflight.popleft()
                results[page_no][i] = _compute(kind, collection, part)
                continue
            try:
                # THE WATCHDOG: no part answered for PARSE_TIMEOUT_S, not
                # "this part took longer" — one long article on a busy core
                # is progress as long as its neighbours keep finishing
                if not fut.done():
                    waiting = [f for *_x, f in inflight if f is not None and not f.done()]
                    done, _ = wait(waiting, timeout=max(0.0, deadline - time.monotonic()),
                                   return_when=FIRST_COMPLETED)
                    if done:
                        # progress, though perhaps not the oldest part: back to
                        # the top, which refills the slots the finished ones freed
                        # (2026-10-25: waiting on the oldest alone left 11 of 12
                        # children idle behind one long article)
                        deadline = time.monotonic() + config.PARSE_TIMEOUT_S
                        continue
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"no part answered for {config.PARSE_TIMEOUT_S:.0f} s")
                    continue
                inflight.popleft()
                deadline = time.monotonic() + config.PARSE_TIMEOUT_S
                got = fut.result()
                owner.pop(fut, None)
                mine -= 1
                _occupy(name, -1)
                _note_success()
                results[page_no][i] = got
            except (BrokenProcessPool, TimeoutError, OSError, CancelledError) as e:
                if inflight and inflight[0][3] is fut:
                    inflight.popleft()        # the oldest part, computed in-process below
                mine -= 1
                _occupy(name, -1)
                for _p, _i, _part, f in inflight:
                    if f is not None:
                        f.cancel()
                        mine -= 1
                        _occupy(name, -1)
                from . import oom_hold
                _note_failure(e, owner.get(fut), where=f" in {kind} of {getattr(collection, 'name', collection)}",
                              **({"killed": True} if oom_hold.killed() is not None else {}))
                owner.clear()
                _raise_if_killed(f"{kind} of {getattr(collection, 'name', collection)}", e)
                # what finished is kept. What was IN FLIGHT and not answered is
                # parsed again in processes of its own, never here (`_isolated`,
                # 2026-11-24): one of those parts may be what the child hung on
                # or died of. What was never submitted, in-process
                done = [(p_no, j, rest, _finished(f) if f is not None else None, f is not None)
                        for p_no, j, rest, f in inflight]
                lost = [part] + [rest for _p, _j, rest, got, flown in done if flown and got is None]
                again, failed = _isolated(kind, collection, lost, 0.0,
                                          cannot=lambda rows: _compute(kind, collection, rows))
                if failed:
                    _document_failed()
                again_it = iter(again)
                results[page_no][i] = next(again_it)
                for p_no, j, rest, got, flown in done:
                    results[p_no][j] = got or (next(again_it) if flown else _compute(kind, collection, rest))
                inflight.clear()
    finally:
        for _p, _i, _part, f in inflight:
            if f is not None:
                f.cancel()
        for _ in range(max(0, mine)):
            _occupy(name, -1)
        _want(name, -1)


def chunks_for_rows(collection, rows) -> list[tuple[bool, object]]:
    return compute("chunks", collection, rows)


def chunks_names_for_rows(collection, rows) -> list[tuple[bool, object]]:
    """`chunks_for_rows` plus each record's names: one `{chunks, names}` per
    row, for a collection whose plugin gives names (fts.names_capable)."""
    return compute("chunks+names", collection, rows)


def fts_for_rows(collection, rows, *, names: bool) -> list[tuple[bool, object]]:
    """The text-index stage's work per row: `{chunks, names, entities}`."""
    return compute("fts+names" if names else "fts", collection, rows)


def metadata_for_rows(collection, rows) -> list[tuple[bool, object]]:
    return compute("metadata", collection, rows)


# ---- prefetch: prepare what the NEXT batches will need, in the background --

_prefetched: OrderedDict = OrderedDict()      # (collection, key) -> True
_MAX_PREFETCH_MEMO = 512


def prefetch_allowed() -> bool:
    """A pool, and no node-profile limit below its size. A prefetch is fired
    and forgotten, outside any window, so it is not counted in flight: under
    a limit it is not sent, and the stage that needs the preparation pays
    for it inside its own limited window, as it did before prefetch existed
    (app/cpu_limit.py)."""
    if pool() is None:
        return False
    cores = cpu_limit.current().cores
    return cores is None or cores >= processes()


def prefetch(collection, rows) -> list[str]:
    """Fire-and-forget: ask the pool to PREPARE what documents ahead of the
    current batch will need — a plugin's derived cache, built once and
    reused (pubmed: the per-container pack, 17 s of every ~38 s of its
    pipeline before this existed, paid by the batch that first touched a
    container while eleven pool children idled).

    Deduplicated on the plugin's `prepare_key` (the container, not the
    document), so a container is submitted once. A no-op when the pool is
    serial: there is no background to do it in, and doing it inline would
    just move the same cost earlier. Never waits, never raises — a failed
    preparation simply means the stage pays for it later, as before."""
    from . import plugin_io
    ex = pool()
    if ex is None or not prefetch_allowed():
        return []
    try:
        plugin = collection.plugin_instance()
    except Exception:   # noqa: BLE001
        return []
    submitted = []
    for row in rows:
        row = dict(row) if not isinstance(row, dict) else row
        key = plugin_io.prepare_key(plugin, row)
        if not key:
            continue
        memo_key = (collection.name, key)
        with _lock:
            if memo_key in _prefetched:
                _prefetched.move_to_end(memo_key)
                continue
            _prefetched[memo_key] = True
            while len(_prefetched) > _MAX_PREFETCH_MEMO:
                _prefetched.popitem(last=False)
        try:
            ex.submit(_compute, "prepare", collection, [row])
        except Exception:   # noqa: BLE001 — a broken pool is handled by the next compute()
            with _lock:
                _prefetched.pop(memo_key, None)
            break
        submitted.append(key)
    return submitted


def forget_prefetched() -> None:
    with _lock:
        _prefetched.clear()
