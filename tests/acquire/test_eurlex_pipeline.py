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
    thread, _ = next(iter(units._runs.values()))
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
