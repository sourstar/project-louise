"""The one retry policy (app/acquire/backoff.py; operator 2026-11-14, "be
gentle with the corpus download policy"; the architecture session's shape)."""
import threading

import pytest

from app.acquire import backoff, engine
from app.acquire.spec import RateLimit, TransferAborted, TransportError
from app.transfer.clock import FakeClock
from tests.transfer.test_manager import _manager

MAX = lambda lo, hi: hi                                     # noqa: E731 — the draw at its ceiling
ZERO = lambda lo, hi: 0.0                                   # noqa: E731


@pytest.mark.parametrize("status, retried", [
    (500, True), (502, True), (503, True), (504, True), (429, True), (408, True),
    (400, False), (401, False), (403, False), (404, False), (410, False), (200, False), (206, False),
])
def test_only_the_origin_s_episodes_are_retried(status, retried):
    assert backoff.retryable_status(status) is retried


def test_retry_after_reads_both_forms():
    assert backoff.retry_after("120") == 120
    assert backoff.retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480 - 60) == 60
    assert backoff.retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480 + 60) == 0
    assert backoff.retry_after(None) is None and backoff.retry_after("soon") is None


def test_the_wait_is_the_largest_of_retry_after_the_duration_the_interval_and_the_draw():
    p = backoff.Policy(base_s=30, cap_s=900, min_interval_s=5)
    assert [backoff.wait_s(k, p, draw=MAX) for k in (1, 2, 3)] == [30, 60, 120]
    assert backoff.wait_s(1, p, draw=ZERO) == 5, "full jitter's ~0 s held up by min_interval"
    assert backoff.wait_s(1, p, took=235.5, draw=ZERO) == 235.5, "a heavy request not repeated sooner"
    assert backoff.wait_s(1, p, took=5000, draw=ZERO) == 900, "the duration floor capped"
    assert backoff.wait_s(1, p, asked=3600, draw=ZERO) == 3600, "Retry-After wins whatever its size"


def test_a_spec_overrides_only_what_it_states():
    class Spec:
        etiquette = RateLimit(max_conns=2, min_interval_s=5.0, backoff_base_s=60.0)
    p = backoff.Policy.of(Spec())
    assert (p.base_s, p.cap_s, p.min_interval_s, p.attempts) == (60.0, backoff.DEFAULT_CAP_S, 5.0, 4)
    assert backoff.Policy.of(None) == backoff.Policy()


def test_episodes_retry_then_give_up_worded_as_a_deferral():
    slept = []
    r = backoff.Retry("GET x", backoff.Policy(base_s=1, attempts=3), sleep=slept.append, draw=MAX,
                      key="", manager=None)
    tries = 0
    with pytest.raises(TransportError) as e:
        for _ in r:
            tries += 1
            r.episode("503 to GET x")
        r.give_up()
    assert tries == 3 and sum(slept) == pytest.approx(1 + 2, abs=0.01)
    assert engine.transient(str(e.value)) and "after 3 attempts" in str(e.value)


def test_an_episode_cools_the_bound_endpoint_down_for_the_wait_it_takes():
    clock = FakeClock()
    m = _manager(clock=clock)
    r = backoff.Retry("the count of 1990", backoff.Policy(base_s=30), key="cellar", manager=m,
                      sleep=lambda s: None, draw=MAX)
    it = iter(r)
    next(it)
    r.episode("503", retry_after="45")
    assert m.cooled_for("cellar") == 45 and "the count of 1990" in m.cooled_why("cellar")


def test_a_wait_longer_than_a_round_holds_a_thread_defers_the_unit():
    r = backoff.Retry("the count of 1990", backoff.Policy(), key="", manager=None,
                      sleep=lambda s: pytest.fail("never slept"), draw=ZERO)
    it = iter(r)
    next(it)
    r.episode("ReadTimeout", retry_after="3600")
    with pytest.raises(backoff.Deferred) as e:
        next(it)
    assert engine.transient(str(e.value)) and "the count of 1990" in str(e.value)


def test_a_stop_ends_the_wait():
    stop = threading.Event()
    stop.set()
    r = backoff.Retry("GET x", backoff.Policy(base_s=1), key="", manager=None, stop=stop,
                      sleep=lambda s: None, draw=MAX)
    it = iter(r)
    next(it)
    r.episode("503")
    with pytest.raises(TransferAborted):
        next(it)
