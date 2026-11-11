"""RAGE's keep: for how long a model keeps its context after an answer
(docs/notes/idle-context-brief.md §1.3; operator, 2026-11-11).

A llama.cpp model started with `context_on_demand` holds its weights and
creates its context — the KV cache and the compute buffers — when a request
arrives, and frees it when the work is done. A request says how long the
context is kept after its answer: `keep_context_ms`, in the request's
`metadata`. Back to back requests must not pay a creation each, and memory
nobody is using must not stay held. The operator: "Louise RAGE creates the
work batches, thus the stay warm flag keeps the context also between jobs";
"Let's start with a 20 ms expiration time, the timeout shall be trimmed per
job type: several collections share the same model, but their way to queue
the jobs may be different"; "I choose option B, trimmed by RAGE"; "The
trimming time shall not be modified if the expiry occurred because of a job
missed scheduling (for instance when a chat model takes over)".

A JOB TYPE IS A STREAM: one plugin's one level of work on one model of one
server (`Stream`). Each stream has its own keep. The server knows nothing of
streams: it frees the context when the LATEST deadline any request asked has
passed, so a stream's keep covers its own rhythm and never shortens
another's.

THE TRIM (`Keeps.send`), judged when a stream's next request is about to go:

- The context EXPIRED before this request if the model had no request of
  ours in flight and the request goes later than every deadline we asked
  (less `TRANSIT_MS`: our clock runs behind the server's by the answer's way
  back and the request's way there, so a borderline case counts as expired —
  the error that costs a longer keep, never a hidden creation).
- A STREAM IS JUDGED ON WHEN ITS REQUEST WAS READY, NOT ON WHEN IT WAS SENT.
  Ready before the deadline and held back — standing aside for a person's
  query, waiting at its gate, a model loading, a deferral — the expiry is
  EXCUSED: the keep does not move, and the interval earns nothing toward
  its decay. (The worker's producers stand aside inside their request, per
  chunk, so this sees it. The rollback rotation — `worker.query_waiting`,
  WORKER_EMBED_PRODUCERS=0 — holds back BETWEEN documents instead: a
  question shorter than the window can read there as the stream's own
  pause.)
- Ready after its own deadline by no more than `PREMATURE_WINDOW_MS`, the
  expiry is PREMATURE: the stream came back while holding the context would
  have cost less than creating it again. The keep DOUBLES.
- Ready later than that, the stream had paused: the expiry was right.
- No expiry: a clean interval. After `DECAY_EVERY_S` of them the keep
  shrinks by `DECAY`, so a keep raised for a rhythm that has passed comes
  back down; one premature expiry per descent is the price.

Between `FLOOR_MS` and `CEILING_MS`. A stream therefore reaches its level in
a handful of creations whatever the start.

NOT PERSISTED. A restart starts every stream at `START_MS` again and pays
those few creations once more — app/sizing.py's rule for its own windows
("after a restart the honest starting point is the configured default"), and
invariant 10: a runtime value that is no file's and survives nothing.

ONLY WHERE THE MODEL TAKES IT. `for_request` gives a keep only for a model
whose load options LocalAI lists with `context_on_demand`
(`model_load.context_on_demand`, from `/system`): a runner that does not know
the field is never sent it. Nothing here is typed by a person (RULE 2).

THE NAMED WHEELS (CLAUDE.md, "do not reinvent the wheel"). The request field
is Ollama's `keep_alive`, applied to the context and in milliseconds. The
window is the break-even of SKI RENTAL (Karlin, Manasse, Rudolph, Sleator,
"Competitive snoopy caching", 1988): hold an idle resource for about as long
as getting it back costs — stretched here by `PREMATURE_FACTOR`, because an
idle context costs memory nobody was waiting for while a creation costs card
time on the embedding path (CLAUDE.md: performance is second only to
consistency). The raise and the decay are AIMD turned over — multiplicative
increase on the loss signal, slow decrease — as app/resident.py's penalty is
(RFC 5681's shape). THE STANDARD ALTERNATIVE would be the server deciding by
itself, as Ollama's one `keep_alive` per model does: it needs no client, and
it cannot tell one collection's rhythm from another's nor a pause from a
request held back, which is what the operator asked for.

Telemetry: `snapshot()` is read by whoever publishes (never the thread doing
the work); a keep that moves is logged once, with its collection.
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator, NamedTuple

log = logging.getLogger(__name__)

#: the operator's start (2026-11-11), and the least a stream is ever given
START_MS = 20
FLOOR_MS = 20
#: the most: past it a stream that keeps expiring pays its creations, and the
#: memory is free between two of its requests (the review's constant, to be
#: set from the first observation period)
CEILING_MS = 2000

#: a creation's time until a measured one is given (`Keeps.creation_ms`): the
#: brief's acceptance bound is 250 ms, its expectation tens to hundreds
CREATION_MS = 100
#: an expiry is premature when the stream is ready again within this many
#: creations' time of its own deadline (module docstring, ski rental)
PREMATURE_FACTOR = 10
PREMATURE_WINDOW_MS = PREMATURE_FACTOR * CREATION_MS

#: our clock against the server's: the answer's way back plus the next
#: request's way there (LocalAI, the VeGa wrapper, its receipt). Counted
#: against us, see the module docstring
TRANSIT_MS = 10

#: the keep shrinks by this after so many seconds of clean intervals
DECAY = 0.9
DECAY_EVERY_S = 60.0

#: the request's field, in its `metadata` (LocalAI carries the map to the
#: backend: backend.proto `PredictOptions.Metadata`)
FIELD = "keep_context_ms"

#: the level of a stream whose keep is FIXED by its caller (`fixed`): the
#: gate's keep-0 pass. Never judged, never trimmed, and no part of the
#: stream the same caller has at its own level
FIXED_LEVEL = "fixed"

#: why an expiry did not move a keep
EXCUSED_HELD = "held back"          # ready in time, sent late: stood aside, the gate, a load
EXCUSED_DEFERRED = "deferred"       # the request before it was deferred or refused


class Stream(NamedTuple):
    """A job type: one plugin's one level of work on one model of one server."""
    server: str
    model: str
    plugin: str
    level: str


@dataclass
class _State:
    keep_ms: int = START_MS
    answered: float | None = None       # when its last answer came (monotonic s)
    asked_ms: int = START_MS            # the keep that answer carried
    changed: float = 0.0                # when the keep last moved or was confirmed
    excuse: str | None = None           # the next expiry is not the stream's
    # counted since start
    requests: int = 0
    premature: int = 0
    excused: dict[str, int] = field(default_factory=dict)
    paused: int = 0
    highest_ms: int = START_MS


@dataclass
class _Model:
    in_flight: int = 0
    deadline: float = float("-inf")     # the latest any of our requests asked (monotonic s)


class Sent(NamedTuple):
    """One request's keep, from `send` to `answered` or `failed`."""
    stream: Stream
    keep_ms: int


class Keeps:
    """Every stream's keep in this process. Thread-safe; O(1) per request."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._streams: dict[Stream, _State] = {}
        self._models: dict[tuple[str, str], _Model] = {}
        self._creation_ms: dict[tuple[str, str], int] = {}

    # -- what a creation costs, when someone measured it ------------------------------------
    def creation_ms(self, server: str, model: str, ms: int | None = None) -> int:
        """A creation's time for `model`, set when `ms` is given (measured:
        the engine's own count); `CREATION_MS` until then."""
        with self._lock:
            if ms is not None and ms > 0:
                self._creation_ms[(server, model)] = int(ms)
            return self._creation_ms.get((server, model), CREATION_MS)

    def _window_ms(self, s: Stream) -> int:
        return PREMATURE_FACTOR * self._creation_ms.get((s.server, s.model), CREATION_MS)

    # -- a request ---------------------------------------------------------------------------
    def send(self, stream: Stream, ready: float) -> Sent:
        """The keep for the request of `stream` that became ready at `ready`
        and goes now: the interval since the stream's last answer is judged
        first (module docstring), then the request counts as in flight until
        `answered` or `failed`."""
        now = self._clock()
        with self._lock:
            st = self._streams.setdefault(stream, _State(changed=now))
            md = self._models.setdefault((stream.server, stream.model), _Model())
            moved = self._judge(stream, st, md, ready, now)
            st.requests += 1
            md.in_flight += 1
            keep = st.keep_ms
        if moved is not None:
            log.info("[rage] keep of %s (%s) on %s at %s: %d -> %d ms — %s",
                     stream.plugin or "-", stream.level, stream.model or "-", stream.server,
                     moved[0], keep, moved[1])
        return Sent(stream, keep)

    def send_fixed(self, stream: Stream, keep_ms: int) -> Sent:
        """A request whose keep its caller fixed (`fixed`): in flight like any
        other, so that the expiry it leaves behind is seen, and judged never."""
        with self._lock:
            st = self._streams.setdefault(stream, _State(changed=self._clock()))
            md = self._models.setdefault((stream.server, stream.model), _Model())
            st.keep_ms = st.highest_ms = int(keep_ms)
            st.requests += 1
            md.in_flight += 1
        return Sent(stream, int(keep_ms))

    def answered(self, sent: Sent) -> None:
        """The request's answer is complete: the server keeps the context for
        its keep from now."""
        now = self._clock()
        with self._lock:
            st = self._streams.get(sent.stream)
            md = self._models.get((sent.stream.server, sent.stream.model))
            if st is None or md is None:
                return
            md.in_flight = max(0, md.in_flight - 1)
            md.deadline = max(md.deadline, now + sent.keep_ms / 1000.0)
            st.answered = now
            st.asked_ms = sent.keep_ms

    def failed(self, sent: Sent) -> None:
        """The request did not answer — deferred, refused, an error: whatever
        expires before the stream's next request is not its doing."""
        with self._lock:
            st = self._streams.get(sent.stream)
            md = self._models.get((sent.stream.server, sent.stream.model))
            if st is None or md is None:
                return
            md.in_flight = max(0, md.in_flight - 1)
            st.excuse = EXCUSED_DEFERRED

    def _judge(self, stream: Stream, st: _State, md: _Model, ready: float,
               now: float) -> tuple[int, str] | None:
        """Move `st.keep_ms` by the rule; `(the keep before, why)` when it moved."""
        excuse, st.excuse = st.excuse, None
        if st.answered is None:
            return None                         # the stream's first request: nothing to judge
        transit = TRANSIT_MS / 1000.0
        expired = md.in_flight == 0 and now + transit > md.deadline
        if not expired:
            return self._clean(st, now)
        if excuse is not None:
            st.excused[excuse] = st.excused.get(excuse, 0) + 1
            return None
        if ready + transit <= md.deadline:
            st.excused[EXCUSED_HELD] = st.excused.get(EXCUSED_HELD, 0) + 1
            return None
        own_deadline = st.answered + st.asked_ms / 1000.0
        late_ms = (ready + transit - own_deadline) * 1000.0
        if late_ms > self._window_ms(stream):
            st.paused += 1                      # it had stopped: freeing was right
            st.changed = now
            return None
        st.premature += 1
        before = st.keep_ms
        st.keep_ms = min(CEILING_MS, max(FLOOR_MS, st.keep_ms * 2))
        st.highest_ms = max(st.highest_ms, st.keep_ms)
        st.changed = now
        if st.keep_ms == before:
            return None
        return before, (f"its context expired {late_ms:.0f} ms before its next request was "
                        f"ready (premature expiry {st.premature} of {st.requests} request(s))")

    def _clean(self, st: _State, now: float) -> tuple[int, str] | None:
        if now - st.changed < DECAY_EVERY_S or st.keep_ms <= FLOOR_MS:
            return None
        before = st.keep_ms
        st.keep_ms = max(FLOOR_MS, int(st.keep_ms * DECAY))
        st.changed = now
        if st.keep_ms == before:
            return None
        return before, f"no premature expiry for {DECAY_EVERY_S:.0f} s"

    # -- reading it back ---------------------------------------------------------------------
    def snapshot(self) -> list[dict]:
        """Per stream: its keep now and what was counted since start."""
        with self._lock:
            return [{"server": k.server, "model": k.model, "plugin": k.plugin, "level": k.level,
                     "keep_ms": s.keep_ms, "highest_ms": s.highest_ms, "requests": s.requests,
                     "premature": s.premature, "excused": dict(s.excused), "paused": s.paused}
                    for k, s in sorted(self._streams.items())]

    def reset(self) -> None:
        with self._lock:
            self._streams.clear()
            self._models.clear()
            self._creation_ms.clear()


#: this process's keeps
KEEPS = Keeps()


_fixed: contextvars.ContextVar[int | None] = contextvars.ContextVar("louise_keep_fixed", default=None)


@contextmanager
def fixed(keep_ms: int) -> Iterator[None]:
    """Every request this thread sends inside carries exactly `keep_ms`,
    whatever its stream's keep: the gate's pass with keep 0, where each text
    must meet a context of its own (invariant 12, the brief §1.4)."""
    token = _fixed.set(int(keep_ms))
    try:
        yield
    finally:
        _fixed.reset(token)


def for_request(url: str, model: str | None, ready: float) -> Sent | None:
    """The keep to send with a request to `model` at `url` that became ready
    at `ready` (monotonic), or None when the model does not take one: not
    loaded with `context_on_demand`, or not known to be."""
    from .. import model_load
    from . import dispatch
    if not model or not model_load.context_on_demand(url, model):
        return None
    level, plugin = dispatch.current()
    server = model_load.server_of(url)
    forced = _fixed.get()
    if forced is not None:
        return KEEPS.send_fixed(Stream(server, model, plugin, FIXED_LEVEL), forced)
    return KEEPS.send(Stream(server, model, plugin, level.name.lower()), ready)


class Request:
    """One request's keep while it is out (`request`)."""

    def __init__(self, sent: Sent | None) -> None:
        self.sent = sent
        self._failed = False

    @property
    def body(self) -> dict[str, dict[str, str]]:
        """What the request's JSON body gains."""
        return metadata(self.sent)

    def fail(self) -> None:
        """The answer was no answer (an error status): `failed` at the end."""
        self._failed = True


@contextmanager
def request(url: str, model: str | None, ready: float) -> Iterator[Request]:
    """A request to `model` that became ready at `ready`, from the moment it
    goes to the moment its answer is complete: `with request(…) as k`, the
    body sent with `**k.body`. An exception, or `k.fail()`, counts it as not
    answered — whatever expires before the stream's next request is then
    excused."""
    k = Request(for_request(url, model, ready))
    try:
        yield k
    except BaseException:
        failed(k.sent)
        raise
    if k._failed:
        failed(k.sent)
    else:
        answered(k.sent)


def metadata(sent: Sent | None) -> dict[str, dict[str, str]]:
    """What a request's body gains: `{"metadata": {"keep_context_ms": "…"}}`,
    or nothing. The map's values are strings (LocalAI's schema)."""
    return {} if sent is None else {"metadata": {FIELD: str(sent.keep_ms)}}


def answered(sent: Sent | None) -> None:
    if sent is not None:
        KEEPS.answered(sent)


def failed(sent: Sent | None) -> None:
    if sent is not None:
        KEEPS.failed(sent)


def snapshot() -> list[dict]:
    return KEEPS.snapshot()


def reset() -> None:
    KEEPS.reset()
