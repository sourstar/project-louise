"""The endpoint cool-down and pacing (app/acquire/backoff.py; the
architecture session, 2026-11-14): in the transfer manager, beside the
endpoint semaphore, on its clock; a cooled mirror ranked last; a slot
waited for at most SLOT_WAIT_BOUND_S, then the unit deferred."""
import threading

from app.transfer import pool
from app.transfer.clock import FakeClock
from tests.transfer.test_manager import _Endpoint, _manager


class _Et:
    def __init__(self, min_interval_s=None, max_conns=4):
        self.min_interval_s, self.max_conns = min_interval_s, max_conns


class _Spec:
    def __init__(self, min_interval_s=None):
        self.etiquette = _Et(min_interval_s)


def test_a_cool_down_lasts_its_seconds_and_a_shorter_one_never_shortens_it():
    clock = FakeClock()
    m = _manager(clock=clock)
    m.cool_down("a", 60, "429 to GET x")
    m.cool_down("a", 10, "503")
    assert m.cooled_for("a") == 60 and m.cooled_why("a") == "429 to GET x"
    clock.advance(59)
    assert m.cooled_for("a") == 1
    clock.advance(1)
    assert m.cooled_for("a") == 0 and m.cooled_why("a") is None
    m.cool_down("a", 0, "nothing")
    assert m.cooled_for("a") == 0


def test_pace_holds_min_interval_between_starts_and_the_cool_down():
    clock = FakeClock()
    m = _manager(clock=clock)
    assert m.pace("a", 5) == 0, "the first start is now"
    assert m.pace("a", 5) == 5
    clock.advance(5)
    assert m.pace("a", 5) == 0
    m.cool_down("a", 30)
    assert m.pace("a", 5) == 30
    assert m.pace("b", None) == 0, "another endpoint is not paced by this one"


def test_a_cooled_mirror_is_ranked_last():
    m = _manager()
    m.record_throughput("fast", 10_000, 1)
    m.record_throughput("slow", 1_000, 1)
    m.cool_down("fast", 60)
    assert [k for k, _ in m.rank_endpoints([("fast", 1), ("slow", 2), ("new", 3)])] == ["slow", "new", "fast"]


def test_a_slot_skips_a_cooling_mirror_for_a_free_one():
    m = _manager()
    m.cool_down("a", 60)
    got = pool.acquire_endpoint_slot(m, [("a", _Endpoint()), ("b", _Endpoint())], _Spec())
    assert got is not None and got[0] == "b"
    got[2].release()


def test_every_mirror_cooling_past_the_bound_defers_the_unit_holding_nothing():
    m = _manager()
    m.cool_down("a", 600)
    sem = m.endpoint_semaphore("a", 4)
    assert pool.acquire_endpoint_slot(m, [("a", _Endpoint())], _Spec(), bound_s=120) is None
    assert all(sem.acquire(blocking=False) for _ in range(4)), "no slot left taken"


def test_pacing_past_the_bound_releases_the_slot_and_defers():
    m = _manager()
    assert m.pace("a", 600) == 0
    sem = m.endpoint_semaphore("a", 1)
    assert pool.acquire_endpoint_slot(m, [("a", _Endpoint(max_conns=1))], _Spec(600), bound_s=1) is None
    assert sem.acquire(blocking=False), "the slot was given back"


def test_a_stop_while_paced_gives_the_slot_back():
    m = _manager()
    m.pace("a", 600)
    stop = threading.Event()
    stop.set()
    sem = m.endpoint_semaphore("a", 1)
    assert pool.acquire_endpoint_slot(m, [("a", _Endpoint(max_conns=1))], _Spec(600), stop=stop) is None
    assert sem.acquire(blocking=False)
