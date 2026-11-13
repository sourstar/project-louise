"""ONE retry policy for every acquisition request: gentle with the origin.

The operator, 2026-11-14: "As a general rule be gentle with the corpus
download policy". The architecture session's answer (the same day) fixed
the shape built here. Before it, six loops (cellar ×2, transport_https ×2,
engine, zipdelta) each retried five times one, two, four and eight seconds
apart, honoured no `Retry-After` and no 429: a Cellar query that had run
900 s and timed out was sent again one second later, up to five times.

THE POLICY
  - Retried: an EPISODE of the origin. A 5xx, 408 (Request Timeout), 429
    (Too Many Requests, RFC 6585), a timeout, a dropped connection. Never
    any other 4xx, which is about the request, not the origin's state.
  - At most `attempts` (4) per ROUND. After the last, the unit is DEFERRED
    to the next round, never written as an error (CLAUDE.md: deferral is
    not failure; the engine's DEFER_ATTEMPTS bounds the quiet across rounds).
  - The wait before attempt k (k >= 1) is
        max(Retry-After, the failed request's own duration (capped),
            min_interval_s, a draw uniform in [0, min(cap, base * 2^(k-1))])
    `Retry-After` (RFC 9110 §10.2.3, delta-seconds or an HTTP-date) wins
    whatever its size. The duration floor means a heavy request is never
    repeated sooner than it took. The draw is AWS's "full jitter"; on its
    own it can be ~0 s, which suits many competing clients. We are one
    client per endpoint, so the other floors keep the wait real.
  - Every episode COOLS THE ENDPOINT DOWN for every worker sharing it
    (`TransferManager.cool_down`, keyed on the endpoint key the engine binds):
    the next slot on that endpoint is not given before the wait ends, and
    `rank_endpoints` puts a cooled mirror last so the work moves to another.
  - A wait longer than INLINE_WAIT_S is not slept in the thread. The unit
    is deferred (`Deferred`, worded for `engine.transient`) and the
    cool-down stands for the next round. A shorter wait is slept in steps
    of a second, honouring the transfer's stop event (invariant 2).

Defaults are here. A spec overrides them in its `RateLimit` (`backoff_base_s`,
`backoff_cap_s`, `min_interval_s`) only where the origin publishes guidance.

THE STANDARDS, NOT ADOPTED, named so the choice can be revisited:
  - `urllib3.util.Retry` does this (status_forcelist, respect_retry_after_header,
    backoff_factor, backoff_jitter), but we use httpx, whose transport retries
    connect errors only.
  - `tenacity` (stop_after_attempt, wait_random_exponential, retry_if_*) would
    replace the loop, but it is a new dependency (CLAUDE.md: ask the operator
    first) and still knows nothing of Retry-After or a shared cool-down.
Adopting either would pay once a transport moves off httpx, or once the
operator admits tenacity. The wait formula and the cool-down here would
remain ours.
"""
from __future__ import annotations

import email.utils
import random
import time
from dataclasses import dataclass
from typing import Callable, Iterator

from .spec import TransferAborted, TransportError

#: the first draw's ceiling, seconds; doubling each attempt
DEFAULT_BASE_S = 30.0
#: no draw, and no duration floor, beyond this; Retry-After may exceed it
DEFAULT_CAP_S = 900.0
#: attempts per round, the first included
DEFAULT_ATTEMPTS = 4
#: the longest wait slept in the worker's thread; a longer one defers the unit
INLINE_WAIT_S = 120.0
#: the statuses that are an episode of the origin besides every 5xx
RETRY_4XX = frozenset({408, 429})


#: the default sleep, resolved at each wait: tests stand it in (tests/acquire/conftest.py)
_sleep = time.sleep
#: every wait and cool-down multiplied by this: 1 in production; the
#: acquisition test suite sets 0 so a mock origin's 503 costs no wall time
#: (the policy's own tests, tests/acquire/test_backoff.py, run at 1)
SCALE = 1.0


class Deferred(TransportError):
    """The origin asked for, or earned, a wait longer than this round
    should hold a thread: the unit is deferred to the next round. Worded
    so `engine.transient` reads it as the endpoint's episode."""


@dataclass(frozen=True)
class Policy:
    base_s: float = DEFAULT_BASE_S
    cap_s: float = DEFAULT_CAP_S
    attempts: int = DEFAULT_ATTEMPTS
    min_interval_s: float = 0.0

    def __post_init__(self) -> None:
        if self.attempts is None:
            object.__setattr__(self, "attempts", DEFAULT_ATTEMPTS)

    @classmethod
    def of(cls, spec=None, *, attempts: int | None = None) -> "Policy":
        """The spec's `RateLimit` overrides where it states them."""
        et = getattr(spec, "etiquette", None) if spec is not None else None
        return cls(base_s=float(getattr(et, "backoff_base_s", None) or DEFAULT_BASE_S),
                   cap_s=float(getattr(et, "backoff_cap_s", None) or DEFAULT_CAP_S),
                   attempts=int(attempts or DEFAULT_ATTEMPTS),
                   min_interval_s=float(getattr(et, "min_interval_s", None) or 0.0))


def retryable_status(status: int) -> bool:
    return status >= 500 or status in RETRY_4XX


def retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Seconds a `Retry-After` header asks for: delta-seconds or an
    HTTP-date (RFC 9110 §10.2.3); None where absent or unreadable."""
    text = (value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


def wait_s(attempt: int, policy: Policy, *, asked: float | None = None, took: float = 0.0,
           draw: Callable[[float, float], float] = random.uniform) -> float:
    """The wait before attempt `attempt` (1 = the first retry)."""
    ceiling = min(policy.cap_s, policy.base_s * (2 ** max(0, attempt - 1)))
    return max(asked or 0.0, min(max(0.0, took), policy.cap_s), policy.min_interval_s,
               draw(0.0, ceiling))


def _manager_and_key():
    from ..transfer.manager import current_handle, get_manager
    handle = current_handle()
    key = getattr(handle, "endpoint_key", None) if handle is not None else None
    stop = getattr(handle, "stop", None) if handle is not None else None
    try:
        manager = get_manager() if key else None
    except Exception:                             # noqa: BLE001 — no platform config: no shared cool-down
        manager = None
    return manager, key, stop


def _sleep_stop_aware(seconds: float, stop, sleep: Callable[[float], None]) -> None:
    """`seconds` slept in steps of at most one, a stop checked before each."""
    left = float(seconds)
    while True:
        if stop is not None and stop.is_set():
            raise TransferAborted("stopped while backing off from the origin")
        if left <= 0:
            return
        step = min(1.0, left)
        sleep(step)
        left -= step


class Retry:
    """The loop every acquisition request runs:

        r = Retry("the count of 1990", policy)
        for _ in r:
            try:
                ... one request ...
            except <transient> as e:
                r.episode(f"ReadTimeout: {e}")         # retried
                continue
            if retryable_status(resp.status_code):
                r.episode(f"{resp.status_code} to …", retry_after=resp.headers.get("retry-after"))
                continue
            return ...
        r.give_up()                                     # raises, worded transient

    `episode` records why and how long the request took, and cools the bound
    endpoint down; the iterator waits before the next attempt. `key`, `stop`
    and `manager` default to the transfer bound on this thread (the engine
    binds its endpoint key). Outside a bound transfer (a listing, a CLI
    probe) the wait is the thread's alone."""

    def __init__(self, what: str, policy: Policy | None = None, *, key: str | None = None,
                 manager=None, stop=None, sleep: Callable[[float], None] | None = None,
                 draw: Callable[[float, float], float] = random.uniform,
                 clock: Callable[[], float] = time.monotonic,
                 inline_wait_s: float | None = None) -> None:
        self.what, self.policy = what, policy or Policy()
        m, k, s = _manager_and_key()
        self.key = key if key is not None else k
        self.manager = manager if manager is not None else (m if self.key else None)
        self.stop = stop if stop is not None else s
        # resolved at call time (`_sleep`), so a test suite can stand it in
        self.sleep = sleep if sleep is not None else (lambda seconds: _sleep(seconds))
        self.draw, self.clock = draw, clock
        self.inline_wait_s = INLINE_WAIT_S if inline_wait_s is None else inline_wait_s
        self.last = ""
        self.cause: BaseException | None = None
        self._asked: float | None = None
        self._took = 0.0
        self._t0 = 0.0
        self._attempt = 0
        self._next: float | None = None

    def __iter__(self) -> Iterator[int]:
        for k in range(self.policy.attempts):
            if k:
                self._wait(k)
            self._attempt = k
            self._t0 = self.clock()
            yield k

    def episode(self, reason: str, *, retry_after: str | float | None = None,
                cause: BaseException | None = None) -> None:
        """The origin's episode on this attempt: recorded, and the endpoint
        cooled down for the wait the next attempt will take."""
        self.last, self.cause = reason, cause
        self._took = self.clock() - self._t0
        self._asked = (float(retry_after) if isinstance(retry_after, (int, float))
                       else globals()["retry_after"](retry_after))
        # the wait the next attempt takes, drawn once: the endpoint is cooled
        # down for exactly that, so every worker sharing it waits alike
        self._next = SCALE * wait_s(self._attempt + 1, self.policy, asked=self._asked, took=self._took,
                                    draw=self.draw)
        if self.manager is not None and self.key:
            self.manager.cool_down(self.key, self._next, f"{self.what}: {reason}")

    def _wait(self, attempt: int) -> None:
        seconds = self._next if self._next is not None else wait_s(attempt, self.policy, draw=self.draw)
        self._next = None
        if seconds > self.inline_wait_s:
            raise Deferred(f"connection timed out: {self.what}: {self.last}; the origin's wait "
                           f"({seconds:.0f} s) is longer than a round holds a thread, deferred "
                           f"to the next round") from self.cause
        _sleep_stop_aware(seconds, self.stop, self.sleep)

    def give_up(self, prefix: str = "") -> None:
        """After the last attempt: the unit is deferred to the next round,
        in words `engine.transient` reads as the endpoint's episode."""
        raise TransportError(f"{prefix}{self.last} on {self.what} after {self.policy.attempts} "
                             f"attempts (connection timed out: deferred to the next round)") from self.cause
