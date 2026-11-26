"""Architecture's probes for a69741dd (the listing phase in parallel), kept
as tests: they assert what SHOULD hold."""
from __future__ import annotations

import errno
import threading
import time

import pytest

from app.acquire import cellar, cellar_listing, eurlex_dump, work_trees
from app.acquire.spec import RemoteFile, TransferAborted
from app.transfer.bucket import Priority
from app.transfer.manager import Direction, current_handle, get_manager
from tests.acquire.test_eurlex_bulk_trees import CREATED, FX, MEMBER, MTD, _statement, _zip


@pytest.fixture(autouse=True)
def _no_cooling_left():
    """The manager is the process's: a cool-down one test earns (a 503's
    Retry-After 300) must not hold the listing of the tests after it."""
    yield
    mgr = get_manager()
    with mgr._lock:
        mgr._cool.pop(cellar.LISTING_KEY, None)


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


def test_listing_threads_carry_the_pass_handle_bound_to_the_phase(tmp_path, monkeypatch):
    base, corpus, due = _setup(tmp_path, monkeypatch, range(1970, 1978))
    seen, lock = [], threading.Lock()

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        h = current_handle()
        with lock:
            seen.append((threading.current_thread().name, h))
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)
    stop = threading.Event()
    with get_manager().bind(Priority.INTERACTIVE, Direction.DOWN, stop=stop):
        cellar.TreeUnits()._prepare_bulk(base, corpus, ["it"], due, "eu", "https://cellar.test")
    assert len(seen) == 8
    assert all(n.startswith("cellar-listing") for n, _ in seen)
    assert all(h.priority == Priority.INTERACTIVE and h.endpoint_key == cellar.LISTING_KEY for _, h in seen)


def test_the_sparql_answer_thread_charges_under_the_pass_handle():
    stop = threading.Event()
    got = {}
    with get_manager().bind(Priority.INTERACTIVE, Direction.DOWN, stop=stop) as handle:
        cellar._answer_of(lambda: got.setdefault("h", current_handle()))
    assert got["h"] is handle


def test_a_local_fault_ends_the_phase_promptly(tmp_path, monkeypatch):
    base, corpus, due = _setup(tmp_path, monkeypatch, range(1970, 1982))
    started, lock, fault_at = [], threading.Lock(), []

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        with lock:
            started.append((year, time.monotonic()))
        if year == 1970:
            cellar._answer_of(lambda: time.sleep(4.0))
        elif year == 1971:
            fault_at.append(time.monotonic())
            raise OSError(errno.ENOSPC, "No space left on device")
        else:
            cellar._answer_of(lambda: time.sleep(0.3))
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)
    stop = threading.Event()
    with get_manager().bind(Priority.BACKFILL, Direction.DOWN, stop=stop):
        t0 = time.monotonic()
        with pytest.raises(OSError):
            cellar.TreeUnits()._prepare_bulk(base, corpus, ["it"], due, "eu", "https://cellar.test")
        took = time.monotonic() - t0
    after = [y for y, t in started if t > fault_at[0] + 0.05]
    assert not after and took < 1.5, f"windows begun after the fault: {after}; the phase raised {took:.1f} s in"


def test_a_stop_ends_every_listing_thread_within_a_second_and_resumes(tmp_path, monkeypatch):
    base, corpus, due = _setup(tmp_path, monkeypatch, range(1970, 1978))
    asked, lock = [], threading.Lock()

    def sparql(client, base_, query, *, what="", charge=None):
        with lock:
            asked.append(what)
        if "1970" in what:
            if "count" in what:
                return [{"n": {"value": "1"}, "max": {"value": "2020-01-01"}}]
            return [{"celex": {"value": "31970R0001"}, "language": {"value": cellar._LANGUAGE_URI + "ITA"},
                     "last": {"value": "2020-01-01"}}]
        return cellar._answer_of(lambda: time.sleep(30.0))
    monkeypatch.setattr(cellar, "sparql", sparql)
    stop = threading.Event()
    threading.Timer(1.0, stop.set).start()
    with get_manager().bind(Priority.BACKFILL, Direction.DOWN, stop=stop):
        t0 = time.monotonic()
        with pytest.raises(TransferAborted):
            cellar.TreeUnits()._prepare_bulk(base, corpus, ["it"], due, "eu", "https://cellar.test")
        took = time.monotonic() - t0
    assert took < 3.0, f"stopped after {took:.1f} s"
    assert not [t for t in threading.enumerate() if t.name.startswith("cellar-listing")]
    cover = cellar_listing.coverage({("31970R0001", "it")}, {"it": 0.0}, "https://cellar.test")[1]
    assert list(cover) == [(1970, "it")]
    asked.clear()
    monkeypatch.setattr(cellar, "sparql",
                        lambda c, b, q, *, what="", charge=None: (asked.append(what), [{"n": {"value": "0"}}])[1])
    cellar.TreeUnits()._prepare_bulk(base, corpus, ["it"], due, "eu", "https://cellar.test")
    assert not [w for w in asked if "1970" in w], asked
    assert len([w for w in asked if "count" in w]) == 7


def test_listing_slots_follow_the_endpoint_etiquette(tmp_path, monkeypatch):
    import dataclasses
    from app.acquire import specs
    mgr = get_manager()
    plat = mgr._platform
    monkeypatch.setattr(mgr, "_platform",
                        dataclasses.replace(plat, transfer=dataclasses.replace(plat.transfer, per_endpoint=2)))
    assert mgr.endpoint_slots(specs.EURLEX_SPEC.endpoints[0], specs.EURLEX_SPEC) == 2
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
    cellar.TreeUnits()._prepare_bulk(base, corpus, ["it"], due, "eu", "https://cellar.test")
    assert peak[0] <= 2, f"{peak[0]} Cellar queries at once with per_endpoint = 2"


def test_an_episode_in_one_listing_thread_cools_the_others(tmp_path, monkeypatch):
    import httpx
    from app.acquire import backoff
    base, corpus, due = _setup(tmp_path, monkeypatch, range(1970, 1982))
    monkeypatch.setattr(backoff, "SCALE", 1.0)
    times, lock = [], threading.Lock()

    class Resp:
        status_code = 503
        headers = {"retry-after": "300"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self, n):
            return iter(())

    def stream(self, *a, **k):
        with lock:
            times.append(time.monotonic())
        return Resp()
    monkeypatch.setattr(httpx.Client, "stream", stream)
    stop = threading.Event()
    with get_manager().bind(Priority.BACKFILL, Direction.DOWN, stop=stop):
        cellar.TreeUnits()._prepare_bulk(base, corpus, ["it"], due, "eu", "https://cellar.test")
    assert len(times) <= cellar.LISTING_SLOTS, \
        f"{len(times)} requests within {max(times) - min(times):.2f} s of a 503 asking Retry-After 300"


def test_a_split_learned_reshapes_later_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(cellar, "LISTING_START_GROUP", 2)
    monkeypatch.setattr(cellar, "LISTING_SLOTS", 1)
    monkeypatch.setattr(cellar_listing, "_base", lambda: tmp_path / "listing")
    monkeypatch.setattr(work_trees, "_BULK", {})
    base, corpus = tmp_path / "datadump", tmp_path / "corpus"
    for lang in ("it", "fr"):
        _statement(base / f"datadump-{lang}", lang)
        _zip(base / f"datadump-{lang}" / "LEG_MTD.zip", {MEMBER: (FX / MTD).read_bytes()})
    eurlex_dump._stamp(corpus, {"it": CREATED, "fr": CREATED})
    calls = []

    def window(client, url, year, langs, code_of, charge, place=None, learned=None):
        calls.append((year, langs))
        if len(langs) > 1 and learned is not None:
            learned["group"] = 1
        return iter(())
    monkeypatch.setattr(cellar, "_window_files", window)
    due = [RemoteFile(rel_path=cellar.rel_path(lang, c), mtime=None, remote=cellar.encode(c, lang))
           for c in ("31970R0001", "31971R0002") for lang in ("it", "fr")]
    cellar.TreeUnits()._prepare_bulk(base, corpus, ["fr", "it"], due, "eu", "https://cellar.test")
    assert (1971, ("fr", "it")) not in calls, calls
