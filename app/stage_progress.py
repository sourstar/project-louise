"""A STAGE THAT MAKES NO PROGRESS SAYS SO BY ITSELF (the lessons of
EUR-Lex5.next, row G3: "Stop rules (hangs, 'done not rising', …) lived in an
operator's monitor. They become the worker's own health checks, reported").

The monitor München ran beside EUR-Lex5.next knew two things the worker did
not say: a stage whose step had not come back ("hang"), and a stage that
kept being tried and committed nothing ("done not rising"). The pool's own
watcher (app/pool_progress.py) says the first where the step waits on the
parse pool. This says both wherever the step is — a tantivy commit, a lock,
an encoder call, a deferral repeated round after round:

  1. ONE STEP HAS NOT ENDED for STALL_S: the collection, the stage, the
     document it started on, how long;
  2. STEPS ARE TRIED AND NONE COMMITS for STALL_S: how many were deferred
     since the last one that committed, and the document of the last;
  and, once a stall was said, ONE LINE when the stage commits again, with how
  long it stood.

"Did work" is not "made progress" (CLAUDE.md): a step that returns without
committing — a deferral — is a try, never progress.

HOW. The thread that works a stage notes each step (`step`: two dictionary
writes under a lock at its start and end — it changes nothing of what runs
when); a watcher thread of the worker's reads the notes every CHECK_EVERY_S
and says (telemetry is published by someone other than the thread doing the
work). STALL_S is the Library's own bound for the same judgement ("no
progress in the last 30 minutes", app/status.py) — one number for the page
and the log.

IT REPORTS; IT PAUSES NOTHING. The monitor paused the collection where a
rule fired. Whether the worker should is the operator's decision and is not
taken here.

WHAT IT DOES NOT SEE: a stage with work pending whose steps are never
started — held behind a wait. Each wait says its own line at every report
(#885: the slice scope, the preload, a memory yield); a wait with no line is
a defect of that wait.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

#: a stage with no commit for this long is said (the Library's "no progress
#: in the last 30 minutes", app/status.py `stall_s`)
STALL_S = 1800.0
#: and again this often while it stays
SAY_EVERY_S = 1800.0
CHECK_EVERY_S = 60.0

_lock = threading.Lock()
#: (collection, stage) -> what its thread last noted
_flows: dict[tuple[str, str], dict] = {}


def reset() -> None:
    with _lock:
        _flows.clear()


def _flow(collection: str, stage: str) -> dict:
    flow = _flows.get((collection, stage))
    if flow is None:
        flow = _flows[(collection, stage)] = {
            "in_step": None,        # (uid, since) while a step runs
            "idle_since": None,     # when its tries began to commit nothing
            "last_try": None,       # when a step last started or ended
            "tries": 0,             # steps ended without a commit since the last that committed
            "last_uid": None,       # the document of the last such step
            "said": None,           # when the stall was last said; None: not said
            "said_since": None,     # the start of the stall that was said
        }
    return flow


class step:
    """One step of a stage, noted: `with stage_progress.step(name, stage,
    uid) as s: s.did = <the step committed>`. A step left by an exception
    is a try that committed nothing."""

    def __init__(self, collection: str, stage: str, uid: str | None, *, now: float | None = None) -> None:
        self.key, self.uid, self.did = (collection, stage), uid, False
        self._now = now

    def __enter__(self) -> "step":
        at = time.monotonic() if self._now is None else self._now
        with _lock:
            flow = _flow(*self.key)
            # NOT TRIED FOR A WHOLE BOUND (paused, held by a mode, nothing
            # selected): what stood before is not a stall of the stage, and
            # the clock starts again with this step
            if flow["last_try"] is not None and at - flow["last_try"] > STALL_S:
                flow["idle_since"], flow["tries"], flow["last_uid"] = None, 0, None
            flow["in_step"] = (self.uid, at)
            flow["last_try"] = at
            if flow["idle_since"] is None:
                flow["idle_since"] = at          # the first step since the last commit starts the clock
        return self

    def __exit__(self, *exc) -> None:
        self.end()

    def end(self, *, now: float | None = None) -> None:
        at = time.monotonic() if now is None else now
        with _lock:
            flow = _flow(*self.key)
            flow["in_step"] = None
            flow["last_try"] = at
            if self.did:
                flow["tries"], flow["last_uid"] = 0, None
                flow["idle_since"] = None
                # `said` is left for the watcher: it says the stage moves again
            else:
                flow["tries"] += 1
                flow["last_uid"] = self.uid


def _for(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def look(say: Callable[[str], None], *, now: float | None = None) -> list[str]:
    """One look at every stage's notes; says what is owed and returns it."""
    at = time.monotonic() if now is None else now
    out: list[str] = []
    with _lock:
        for (collection, stage), flow in sorted(_flows.items()):
            since = flow["idle_since"]
            if since is None:
                if flow["said"] is not None:
                    out.append(f"{collection}: {stage} commits again — it had made no progress for "
                               f"{_for(flow['said'] - flow['said_since'])} or more when last said")
                    flow["said"] = flow["said_since"] = None
                continue
            stood = at - since
            if stood < STALL_S or (flow["said"] is not None and at - flow["said"] < SAY_EVERY_S):
                continue
            running = flow["in_step"]
            if running is None and at - (flow["last_try"] or since) > STALL_S:
                continue        # nobody tries it any more (paused, held): not a stall of the stage
            if running is not None and flow["tries"] == 0:
                line = (f"{collection}: {stage} has been in ONE step for {_for(at - running[1])} — it started "
                        f"on {running[0] or 'a document it did not name'} and has not come back; nothing of "
                        f"the stage has been committed since")
            else:
                line = (f"{collection}: {stage} has made NO PROGRESS for {_for(stood)}: {flow['tries']} "
                        f"step(s) ended without a commit (deferred or failed), the last on "
                        f"{flow['last_uid'] or 'a document it did not name'}"
                        + (f"; one is running now, on {running[0]}, for {_for(at - running[1])}"
                           if running is not None else ""))
            flow["said"], flow["said_since"] = at, since
            out.append(line + " (reported, nothing is paused; the reason is in the stage's own lines above)")
    for line in out:
        say(line)
    return out


def watch(stop, say: Callable[[str], None], *, every_s: float = CHECK_EVERY_S) -> None:
    """The worker's watcher thread: a look every `every_s`, until `stop`.
    Never raises — a reading that fails is the next look's."""
    while not stop.wait(every_s):
        try:
            look(say)
        except Exception:   # noqa: BLE001 — telemetry never ends its thread
            pass
