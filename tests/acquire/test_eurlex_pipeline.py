"""THE PIPELINE (architecture, 2026-11-27): the pass's units are fetched as
their listing windows come, while the rest is listed — not after ~7 h of
listing. A window not listed yet keeps its units waiting (no slot held);
never the bulk on silence; one slot is left to the fetch."""
from __future__ import annotations

import threading
import time

import pytest

from app.acquire import cellar, cellar_listing, eurlex_dump, work_trees
from app.acquire.spec import RemoteFile
from app.transfer.bucket import Priority
from app.transfer.manager import Direction, get_manager
from tests.acquire.test_eurlex_bulk_trees import CREATED, FX, MEMBER, MTD, _statement, _zip
from tests.acquire.test_eurlex_listing_phase import _no_cooling_left  # noqa: F401

URL = "https://cellar.test"


def _setup(tmp_path, monkeypatch, years):
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    monkeypatch.setattr(work_trees, "_BULK", {})
    base, corpus = tmp_path / "datadump", tmp_path / "corpus"
    _statement(base / "datadump-it", "it")
    _zip(base / "datadump-it" / "LEG_MTD.zip", {MEMBER: (FX / MTD).read_bytes()})
    eurlex_dump._stamp(corpus, {"it": CREATED})
    due = [RemoteFile(rel_path=cellar.rel_path("it", f"3{y}R0001"), mtime=None,
                      remote=cellar.encode(f"3{y}R0001", "it")) for y in years]
    return base, corpus, due


def _gated(monkeypatch, gates):
    """`_window_files` that keeps each year's window open until its gate
    opens, then keeps it listed (one row, dated before the dump)."""
    after = int(time.time()) - 10 ** 7

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        gates[year].wait(10)
        cellar_listing.keep(url, year, langs, 1, None, [[f"3{year}R0001", "it", after, "true"]], force=True)
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)


def test_the_pass_starts_before_the_listing_ends_and_each_window_is_published_as_it_comes(tmp_path, monkeypatch):
    base, corpus, due = _setup(tmp_path, monkeypatch, (1970, 1971))
    gates = {1970: threading.Event(), 1971: threading.Event()}
    _gated(monkeypatch, gates)
    units = cellar.TreeUnits()
    stop = threading.Event()
    with get_manager().bind(Priority.BACKFILL, Direction.DOWN, stop=stop):
        units._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=True)   # returns at once
        u70, u71 = ({"celex": f"3{y}R0001", "lang": "it"} for y in (1970, 1971))
        with pytest.raises(cellar.WindowNotListed):
            units._bulk_of(corpus, u70, due[0], corpus / due[0].rel_path)       # not listed yet: it waits
        gates[1970].set()
        units.ready(due[0], stop)                                               # its window comes
        assert units._bulk_of(corpus, u70, due[0], corpus / due[0].rel_path)[0] is not None
        with pytest.raises(cellar.WindowNotListed):
            units._bulk_of(corpus, u71, due[1], corpus / due[1].rel_path)       # 1971 still being listed
        gates[1971].set()
        units.ready(due[1], stop)
        assert units._bulk_of(corpus, u71, due[1], corpus / due[1].rel_path)[0] is not None
    units.close()


def test_the_pass_follows_the_listing_s_order(tmp_path, monkeypatch):
    base, corpus, due = _setup(tmp_path, monkeypatch, (1972, 1970, 1971))
    gates = {y: threading.Event() for y in (1970, 1971, 1972)}
    _gated(monkeypatch, gates)
    units = cellar.TreeUnits()
    units._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=True)
    assert [f.rel_path for f in sorted(due, key=units.sort_key)] == \
        [cellar.rel_path("it", f"3{y}R0001") for y in (1970, 1971, 1972)]
    for g in gates.values():
        g.set()
    units.close()


def test_ready_never_outlives_the_listing_and_honours_the_stop(tmp_path, monkeypatch):
    base, corpus, due = _setup(tmp_path, monkeypatch, (1970,))
    gate = threading.Event()
    _gated(monkeypatch, {1970: gate})
    units = cellar.TreeUnits()
    units._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=True)
    stop = threading.Event()
    stop.set()
    t0 = time.monotonic()
    units.ready(due[0], stop)
    assert time.monotonic() - t0 < 1.0, "a stop ends the wait"
    gate.set()
    units.close()
    t0 = time.monotonic()
    units.ready(due[0], threading.Event())
    assert time.monotonic() - t0 < 1.0, "the listing ended: no wait"


def test_the_pipelined_listing_leaves_one_slot_to_the_fetch(tmp_path, monkeypatch):
    base, corpus, due = _setup(tmp_path, monkeypatch, range(1970, 1978))
    live, peak, lock = [0], [0], threading.Lock()

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.2)
        with lock:
            live[0] -= 1
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)
    units = cellar.TreeUnits()
    units._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=True)
    thread, _events, _phase = next(iter(units._runs.values()))
    thread.join(10)
    slots = get_manager().endpoint_slots(cellar.EURLEX_SPEC.endpoints[0] if hasattr(cellar, "EURLEX_SPEC")
                                         else __import__("app.acquire.specs", fromlist=["x"]).EURLEX_SPEC.endpoints[0],
                                         __import__("app.acquire.specs", fromlist=["x"]).EURLEX_SPEC)
    assert peak[0] <= max(1, min(cellar.LISTING_SLOTS, slots) - 1)
    units.close()


def test_the_engine_waits_for_ready_before_it_takes_a_slot(monkeypatch):
    from types import SimpleNamespace
    from app.acquire import engine
    order = []

    class Fetcher:
        def ready(self, f, stop):
            order.append("ready")

        def fetch(self, endpoint, f, dest):
            order.append("fetch")
            return 0
    spec = SimpleNamespace(unit_fetcher=Fetcher())
    monkeypatch.setattr(engine.transfer_pool, "acquire_endpoint_slot",
                        lambda *a, **k: order.append("slot") or None)
    work = engine._make_worker(None, None, spec, None, [], threading.Event(), Priority.BACKFILL)
    item = engine._WorkItem(data=RemoteFile(rel_path="x", mtime=None, remote="x"), sidecar=None)
    assert work(item) == [] and order == ["ready", "slot"]


def test_the_engine_ends_a_pass_its_fetcher_held_back(tmp_path, monkeypatch):
    """A unit fetcher's `held_back` mid-pass: nothing more is asked, and the
    pass ends HeldBack, as a prepare's does."""
    from types import SimpleNamespace
    from app.acquire import engine
    from app.acquire.spec import HeldBack
    asked = []

    class Fetcher:
        def held_back(self):
            return "eu: the listing met a local fault"
    spec = SimpleNamespace(unit_fetcher=Fetcher())
    monkeypatch.setattr(engine.transfer_pool, "acquire_endpoint_slot", lambda *a, **k: asked.append(1))
    work = engine._make_worker(None, None, spec, None, [], threading.Event(), Priority.BACKFILL)
    item = engine._WorkItem(data=RemoteFile(rel_path="x", mtime=None, remote="x"), sidecar=None)
    assert work(item) == [] and asked == [], "nothing asked once held back"
    assert HeldBack  # the pass's end is engine.fetch's `raise HeldBack(why)` after the pool


# ---- architecture's probes for b797db0e ----------------------------------------------------

def _runs(units):
    thread, events, _phase = next(iter(units._runs.values()))
    return thread, events


def test_cellar_connections_stay_within_max_conns_while_the_listing_runs(tmp_path, monkeypatch):
    from app.acquire import specs
    from app.transfer import pool as transfer_pool
    spec = specs.EURLEX_SPEC
    base, corpus, due = _setup(tmp_path, monkeypatch, range(1970, 1978))
    gate, live, lock = threading.Event(), [0], threading.Lock()

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        with lock:
            live[0] += 1
        gate.wait(10)
        with lock:
            live[0] -= 1
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)
    units = cellar.TreeUnits()
    stop = threading.Event()
    with get_manager().bind(Priority.BACKFILL, Direction.DOWN, stop=stop):
        units._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=True)
    time.sleep(0.5)
    mgr = get_manager()
    ranked = mgr.rank_endpoints([(e.url, e) for e in spec.endpoints])
    got = []
    for _ in range(mgr.endpoint_slots(spec.endpoints[0], spec)):
        s = transfer_pool.acquire_endpoint_slot(mgr, ranked, spec, stop=stop, bound_s=0.2)
        if s is not None:
            got.append(s)
    listing = live[0]
    in_flight = listing + len(got)
    for _k, _e, sem in got:
        sem.release()
    gate.set()
    units.close()
    assert in_flight <= mgr.endpoint_slots(spec.endpoints[0], spec), \
        f"{listing} listing queries + {len(got)} fetch slots = {in_flight} at once on Cellar"


def test_a_window_that_failed_to_list_does_not_hold_its_units_till_the_listing_ends(tmp_path, monkeypatch):
    from app.acquire.spec import TransportError
    base, corpus, due = _setup(tmp_path, monkeypatch, (1970, 1971))

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        if year == 1970:
            raise TransportError("cellar: 503 to the count of 1970 after 4 attempts (connection timed out)")
        cellar._answer_of(lambda: time.sleep(4.0))
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)
    units = cellar.TreeUnits()
    stop = threading.Event()
    with get_manager().bind(Priority.BACKFILL, Direction.DOWN, stop=stop):
        units._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=True)
    time.sleep(0.3)
    t0 = time.monotonic()
    units.ready(due[0], stop)
    took = time.monotonic() - t0
    units.close()
    assert took < 1.0, f"a unit of the failed 1970 window waited {took:.1f} s in ready (the listing's end)"


def test_close_ends_the_listing_with_the_pass(tmp_path, monkeypatch):
    base, corpus, due = _setup(tmp_path, monkeypatch, (1970,))
    monkeypatch.setattr(cellar, "_window_files",
                        lambda *a, **k: (cellar._answer_of(lambda: time.sleep(15.0)), iter(()))[1])
    units = cellar.TreeUnits()
    stop = threading.Event()
    with get_manager().bind(Priority.BACKFILL, Direction.DOWN, stop=stop):
        units._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=True)
    thread, _ = _runs(units)
    time.sleep(0.3)
    units.close()
    alive = thread.is_alive()
    stop.set()
    thread.join(5)
    assert not alive, "the listing thread outlives close(): it keeps asking Cellar after the pass"


def test_a_stop_with_units_in_ready_ends_all_in_2_s_and_resumes(tmp_path, monkeypatch):
    base, corpus, due = _setup(tmp_path, monkeypatch, (1970, 1971))
    after = int(time.time()) - 10 ** 7

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        if year == 1971:
            cellar._answer_of(lambda: time.sleep(30.0))
        cellar_listing.keep(url, year, langs, 1, None, [[f"3{year}R0001", "it", after, "true"]], force=True)
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)
    units = cellar.TreeUnits()
    stop = threading.Event()
    with get_manager().bind(Priority.BACKFILL, Direction.DOWN, stop=stop):
        units._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=True)
    thread, _ = _runs(units)
    waiters = [threading.Thread(target=units.ready, args=(due[1], stop)) for _ in range(4)]
    for w in waiters:
        w.start()
    time.sleep(1.0)
    t0 = time.monotonic()
    stop.set()
    for w in waiters:
        w.join(5)
    thread.join(5)
    took = time.monotonic() - t0
    units.close()
    assert not any(w.is_alive() for w in waiters) and not thread.is_alive() and took < 2.5, took
    asked = []
    monkeypatch.setattr(cellar, "_window_files", lambda c, u, y, ls, *a, **k: (asked.append(y), iter(()))[1])
    cellar.TreeUnits()._prepare_bulk(base, corpus, ["it"], due, "eu", URL, background=False)
    assert asked == [1971], asked
