"""Leaving the interpreter without its finalization, where lancedb crashes.

lancedb 0.38 crashes at interpreter exit: a `lancedb-tokio-w` thread calls
into libpython while Py_FinalizeEx tears it down (`_lancedb.abi3.so`
+0x8dc90ab -> `libpython3.12.so` +0x273130). Measured on the node on
2026-09-18: 9 of 80 short-lived processes that had run a search died with
SIGSEGV (exit 139) AFTER their work had committed, with pylance installed
or not. The data was never at risk; the exit code was wrong. docs/07 §7.7's
"exits 130 on a signal" read 139 about one stop in nine, and any `louise`
command could report a failure it did not have.

`run(main)` does everything a normal exit does that anyone can observe,
in CPython's own order: threading's shutdown (executors stopped,
non-daemon threads joined), then the atexit callbacks (logging,
multiprocessing), then a flush of stdout and stderr. Then it leaves with
os._exit, skipping only the module teardown where the crash lives.
"""
from __future__ import annotations

import atexit
import os
import sys
import threading
import traceback
from typing import Callable, NoReturn


def _code(value: object) -> int:
    """The status `sys.exit(value)` would give."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    print(value, file=sys.stderr)
    return 1


def _stdout_widowed() -> bool:
    """Whether stdout is a pipe nobody reads any more (`louise ... | head`):
    poll reports POLLERR on its write end. Python's "Note on SIGPIPE" is the
    standard: SIGPIPE stays ignored, and the closed stdout is a quiet exit —
    never SIG_DFL, which also kills the process on a pipe of its own, such
    as a process pool's to a child the kernel killed."""
    import select
    try:
        p = select.poll()
        p.register(sys.stdout.fileno(), select.POLLOUT)
        return any(ev & select.POLLERR for _, ev in p.poll(0))
    except (AttributeError, OSError, ValueError):
        return False


#: how long the observable half of an exit may take — threads joined, the
#: atexit callbacks run — before the process leaves anyway (`finish`). Long
#: enough for the work an exit legitimately finishes (a thread pool's queued
#: writes, `parallel`'s own stop, the atexit callbacks); a stuck join is
#: hours, never minutes
FINISH_BOUND_S = 300.0


def _flush() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 — a closed pipe has nothing left to say
            pass


def _leave_after(code: int, bound_s: float) -> None:
    """THE EXIT IS BOUNDED (CLAUDE.md: a thread never waits without a bound).
    `threading._shutdown` joins every non-daemon thread with none: a parse
    pool's manager that outlived `parallel.discard` held gutenberg.next's
    refused build for two hours, twice (the node, 2026-11-19, "a stopped
    pool's manager is still waiting after 5 s", then nothing). This daemon
    waits the bound, says which threads are still running — every stack, by
    faulthandler, the standard library's — and leaves with the SAME code:
    the work's outcome was already decided, only its exit was stuck."""
    if threading.Event().wait(bound_s):
        return
    alive = sorted(t.name for t in threading.enumerate()
                   if t is not threading.current_thread() and t.is_alive() and not t.daemon)
    print(f"[exit] {bound_s:.0f} s after the end, still running: {', '.join(alive) or '(none)'}; "
          f"leaving with {code}", file=sys.stderr, flush=True)
    try:
        import faulthandler
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    except Exception:  # noqa: BLE001, S110 — the stacks are a help, never a condition of leaving
        pass
    _flush()
    os._exit(code)


def finish(code: int, bound_s: float | None = None) -> NoReturn:
    """Exit with `code` after the observable half of a normal exit, bounded
    (`_leave_after`). Called from the main thread, once, as the last thing
    the process does."""
    bound = FINISH_BOUND_S if bound_s is None else bound_s
    threading.Thread(target=_leave_after, args=(code, bound), name="exit-bound", daemon=True).start()
    try:
        threading._shutdown()          # Py_FinalizeEx's first step: join threads
        atexit._run_exitfuncs()        # its second
    finally:
        _flush()
        os._exit(code)


def run(main: Callable[[], object]) -> NoReturn:
    """`if __name__ == "__main__": process_exit.run(main)` — the exit
    status of `sys.exit(main())`, without the teardown."""
    try:
        code = _code(main())
    except SystemExit as e:
        code = _code(e.code)
    except KeyboardInterrupt:
        traceback.print_exc()
        code = 130                     # what the shell reports for an unhandled Ctrl-C
    except BrokenPipeError:
        if not _stdout_widowed():      # a pipe of the command's own: an error like any other
            traceback.print_exc()
        else:                          # `| head` read what it wanted: nothing left to say
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        code = 1
    except BaseException:  # noqa: BLE001 — reported exactly as the interpreter would
        traceback.print_exc()
        code = 1
    finish(code)
