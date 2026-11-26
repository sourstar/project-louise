"""Architecture's probes against 4076ab4c (Z1-Z5, 2026-11-26), INVERTED: the
inline re-ask of an aged window must not cool Cellar for the pass, must not
ask a failed window again for every unit, and lets a unit wait where its
listing cannot be written."""
from __future__ import annotations

import errno
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from app.acquire import backoff, cellar, cellar_listing, datadump
from app.transfer import pool
from app.transfer.bucket import Priority
from app.transfer.manager import Direction
from tests.acquire.test_eurlex_bulk_trees import CELEX, CREATED
from tests.acquire.test_eurlex_bulk_trees_probes import URL, _setup, _unit

DAY = 24 * 3600.0


def _aged(tmp_path, monkeypatch, n_units=1):
    base, corpus = _setup(tmp_path, monkeypatch)
    celexes = [CELEX] + [f"31972R{9000 + i:04d}" for i in range(n_units - 1)]
    units = cellar.TreeUnits()
    key = str(corpus.resolve())
    after = datadump._stamp(CREATED).timestamp()
    units._bulk[key] = {"it": object()}
    units._after[key] = {"it": after}
    units._cover[key] = {(1972, "it"): time.time() - DAY - 3600}
    units._dates[key] = {(c, "it"): int(after) - 10 for c in celexes}
    units._due[key] = {(c, "it") for c in celexes}
    units._base_url[key] = URL
    return units, corpus, celexes


def test_Z1_a_window_that_cannot_be_asked_is_asked_once_not_for_every_unit(tmp_path, monkeypatch):
    units, corpus, celexes = _aged(tmp_path, monkeypatch, n_units=50)
    asked = []

    def timed_out(client, base, query, *, what="", charge=None):
        asked.append(what)
        raise cellar.TransportError(f"cellar: ReadTimeout on {what} after 900 s (connection timed out)")
    monkeypatch.setattr(cellar, "sparql", timed_out)
    for c in celexes:
        f = _unit(c)
        with pytest.raises(cellar.WindowNotListed):
            units._bulk_of(corpus, {"celex": c, "lang": "it"}, f, corpus / f.rel_path)
    assert len(asked) == 1


def _slow_retry(monkeypatch):
    class Clock:
        t = 0.0

        def __call__(self):
            Clock.t += 900.0
            return Clock.t
    real = backoff.Retry

    class SlowRetry(real):
        def __init__(self, what, policy=None, **kw):
            super().__init__(what, policy, clock=Clock(), sleep=lambda s: None, **kw)
    monkeypatch.setattr(backoff, "Retry", SlowRetry)


class _TimeoutClient:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def stream(self, *a, **k):
        raise httpx.ReadTimeout("timed out")


def test_Z2_a_timed_out_count_in_a_fetch_thread_does_not_cool_cellar(tmp_path, monkeypatch):
    from app import platform_config as pc
    from app.transfer import manager as tm
    from app.transfer.manager import TransferManager
    monkeypatch.setattr(backoff, "SCALE", 1.0)
    m = TransferManager(platform=pc.PlatformConfig())
    monkeypatch.setattr(tm, "_manager", m, raising=False)
    units, corpus, celexes = _aged(tmp_path, monkeypatch)
    _slow_retry(monkeypatch)
    monkeypatch.setattr(cellar.httpx, "Client", _TimeoutClient)
    stop = threading.Event()
    f = _unit()
    with m.bind(Priority.BACKFILL, Direction.DOWN, stop=stop, endpoint_key="cellar"):
        with pytest.raises(cellar.WindowNotListed):
            units._bulk_of(corpus, {"celex": CELEX, "lang": "it"}, f, corpus / f.rel_path)
    assert m.cooled_for("cellar") <= pool.SLOT_WAIT_BOUND_S
    ranked = [("cellar", SimpleNamespace(url=URL, max_conns=4))]
    assert pool.acquire_endpoint_slot(m, ranked, SimpleNamespace(etiquette=None), stop=stop) is not None


def test_Z3_a_listing_write_that_fails_at_fetch_time_lets_the_unit_wait(tmp_path, monkeypatch):
    units, corpus, celexes = _aged(tmp_path, monkeypatch)

    def sparql(client, base, query, *, what="", charge=None):
        if "COUNT(" in query:
            return [{"n": {"value": "1"}, "max": {"value": "2026-11-20T00:00:00Z"}}]
        return [{"celex": {"value": CELEX}, "language": {"value": "http://x/ITA"},
                 "last": {"value": "2026-11-20T00:00:00Z"}, "force": {"value": "true"}}]
    monkeypatch.setattr(cellar, "sparql", sparql)
    monkeypatch.setattr(cellar_listing, "_keep",
                        lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "No space left on device")))
    f = _unit()
    with pytest.raises(cellar.WindowNotListed):
        units.fetch(SimpleNamespace(url=URL), f, corpus / f.rel_path)


def test_Z5_a_window_that_moves_between_its_count_and_its_pages_is_paged_once(tmp_path, monkeypatch):
    units, corpus, celexes = _aged(tmp_path, monkeypatch, n_units=10)
    asked = []

    def sparql(client, base, query, *, what="", charge=None):
        asked.append(what)
        if "COUNT(" in query:
            return [{"n": {"value": "2"}, "max": {"value": "2026-11-20T00:00:00Z"}}]
        return [{"celex": {"value": c}, "language": {"value": "http://x/ITA"},
                 "last": {"value": "2026-11-20T00:00:00Z"}, "force": {"value": "true"}} for c in celexes[:3]]
    monkeypatch.setattr(cellar, "sparql", sparql)
    for c in celexes:
        f = _unit(c)
        with pytest.raises(cellar.WindowNotListed):
            units._bulk_of(corpus, {"celex": c, "lang": "it"}, f, corpus / f.rel_path)
    assert len([a for a in asked if "page" in a]) == 1


def test_a_failed_window_is_asked_again_after_its_hold(tmp_path, monkeypatch):
    units, corpus, celexes = _aged(tmp_path, monkeypatch)
    asked = []

    def timed_out(client, base, query, *, what="", charge=None):
        asked.append(what)
        raise cellar.TransportError("cellar: 503 to the count (an episode)")
    monkeypatch.setattr(cellar, "sparql", timed_out)
    f = _unit()
    for _ in range(2):
        with pytest.raises(cellar.WindowNotListed):
            units._bulk_of(corpus, {"celex": CELEX, "lang": "it"}, f, corpus / f.rel_path)
    t = time.time()
    monkeypatch.setattr(time, "time", lambda: t + cellar.WINDOW_FAILED_HOLD_S + 1)
    with pytest.raises(cellar.WindowNotListed):
        units._bulk_of(corpus, {"celex": CELEX, "lang": "it"}, f, corpus / f.rel_path)
    assert len(asked) == 2
