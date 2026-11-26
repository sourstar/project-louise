"""app/worker.py's _acquire() — gated by Collection.acquire_enabled, off by
default so an existing hand-populated deployment sees zero behavior change
and no surprise outbound network access on restart.
"""
from app import collections as collections_mod, db, node_state, worker
from app.acquire.spec import AcquisitionSpec, Filter, Transport


def _collection(tmp_path, acquire_enabled):
    d = tmp_path / "corpus"
    d.mkdir()
    return collections_mod.Collection(
        name="test", plugin="gutenberg", profile="demo", embed_mode_default=None,
        corpus_dir=d, manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb",
        acquire_enabled=acquire_enabled, license_ack=None,
    )


def test_acquire_noop_when_disabled(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(worker.acquire_engine, "run_acquisition",
                         lambda *a, **kw: called.append(1))
    collection = _collection(tmp_path, acquire_enabled=False)
    con = db.connect(collection)
    worker._acquire(con, collection, "test")
    assert called == []


def test_acquire_runs_when_enabled_and_spec_registered(tmp_path, monkeypatch):
    called = []

    def fake_run(con, collection, spec, profile, stop=None):
        called.append((profile, stop))
        return {"fetched": 0, "skipped": 0, "failed": 0, "aborted": 0, "total": 0}
    monkeypatch.setattr(worker.acquire_engine, "run_acquisition", fake_run)

    spec = AcquisitionSpec(source_id="gutenberg", transport=Transport.HTTPS, endpoints=(),
                            layout="single-file", profiles={"demo": Filter()},
                            estimated_bytes={"demo": 1})
    monkeypatch.setitem(worker.ACQUIRE_REGISTRY, "gutenberg", spec)
    collection = _collection(tmp_path, acquire_enabled=True)
    con = db.connect(collection)
    worker._acquire(con, collection, "test")
    worker._acquire_threads[collection.name].join(timeout=10)   # async now
    profile, stop = called[0]
    assert profile == "demo"    # the collection's declared profile, passed through
    # P0.7: the worker's stop event must reach acquisition, or a SIGTERM
    # during a long fetch is invisible until the whole run finishes.
    # the composite of SIGTERM and this collection's removal signal, so a
    # retired collection stops fetching without waiting for a process exit
    assert stop.is_set() is False
    worker._gone_event("test").set()
    assert stop.is_set() is True
    worker._gone.pop("test", None)
    # completion raised the flag the main loop turns into a discovery scan
    with worker._acquire_lock:
        assert collection.name in worker._acquire_done
        worker._acquire_done.pop(collection.name, None)


def test_slow_acquisition_does_not_block_the_worker(tmp_path, monkeypatch):
    """The live incident: a 45 GB `grow full` fetch inside the round loop
    froze every collection for hours. The fetch must run beside the rounds."""
    import threading as threading_mod
    import time as time_mod
    release = threading_mod.Event()

    def slow_run(con, collection, spec, profile, stop=None):
        release.wait(timeout=30)
        return {"fetched": 1, "skipped": 0, "failed": 0, "aborted": 0, "total": 1}
    monkeypatch.setattr(worker.acquire_engine, "run_acquisition", slow_run)

    spec = AcquisitionSpec(source_id="gutenberg", transport=Transport.HTTPS, endpoints=(),
                            layout="single-file", profiles={"demo": Filter()},
                            estimated_bytes={"demo": 1})
    monkeypatch.setitem(worker.ACQUIRE_REGISTRY, "gutenberg", spec)
    collection = _collection(tmp_path, acquire_enabled=True)
    con = db.connect(collection)

    t0 = time_mod.time()
    worker._acquire(con, collection, "test")
    assert time_mod.time() - t0 < 5, "_acquire must return while the fetch runs"

    # a second call while one is in flight is a no-op, not a second download
    worker._acquire(con, collection, "again")
    alive = [t for t in [worker._acquire_threads[collection.name]] if t.is_alive()]
    assert len(alive) == 1

    release.set()
    worker._acquire_threads[collection.name].join(timeout=10)
    with worker._acquire_lock:
        assert collection.name in worker._acquire_done
        worker._acquire_done.pop(collection.name, None)


def test_acquire_noop_when_no_spec_registered(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(worker.acquire_engine, "run_acquisition",
                         lambda *a, **kw: called.append(1))
    monkeypatch.delitem(worker.ACQUIRE_REGISTRY, "gutenberg", raising=False)
    collection = _collection(tmp_path, acquire_enabled=True)
    con = db.connect(collection)
    worker._acquire(con, collection, "test")
    assert called == []


# ---- a download honours pause and seed-only --------------------------------
#
# Measured 2026-09-13: seed-only was set at 22:45 and cleared at 08:50, and
# 204,486 gutenberg files (160.3 GB) were fetched INSIDE that window, in
# every one of its ten hours. docs/07 defines `node pause` as "no work";
# seed-only is "downloads and processing stand down exactly like pause".
# The main loop set `_paused` for both — and the fetch's stop was
# SIGTERM-or-removed only, so a fetch already in flight never saw it.

import threading
import time

_SPEC = AcquisitionSpec(source_id="gutenberg", transport=Transport.HTTPS, endpoints=(),
                        layout="single-file", profiles={"demo": Filter()},
                        estimated_bytes={"demo": 1})
_DONE = {"fetched": 0, "skipped": 0, "failed": 0, "aborted": 0, "total": 0}


def _wired(tmp_path, monkeypatch, run):
    monkeypatch.setattr(worker.acquire_engine, "run_acquisition", run)
    monkeypatch.setitem(worker.ACQUIRE_REGISTRY, "gutenberg", _SPEC)
    collection = _collection(tmp_path, acquire_enabled=True)
    return collection, db.connect(collection)


def _forget(collection):
    worker._paused.clear()
    worker._acquire_only.clear()
    with worker._acquire_lock:
        worker._acquire_done.pop(collection.name, None)
        worker._paused_aborts.discard(collection.name)
    worker._take_discovery_request(collection.name)
    worker._main_loop_live.clear()


def test_the_fetch_stop_includes_the_pause(tmp_path, monkeypatch):
    captured = []
    collection, con = _wired(tmp_path, monkeypatch,
                             lambda *a, stop=None, **k: captured.append(stop) or _DONE)
    try:
        worker._acquire(con, collection, "test")
        worker._acquire_threads[collection.name].join(timeout=10)
        stop = captured[0]
        assert stop.is_set() is False
        worker._paused.set()          # what the main loop does for paused AND seed-only
        assert stop.is_set() is True, "a pause must reach the fetch's stop"
    finally:
        _forget(collection)


def test_a_fetch_ALREADY_RUNNING_stops_when_the_node_pauses(tmp_path, monkeypatch):
    """The exact incident: the fetch was in flight when seed-only was set,
    and ran on for ten hours. Bounded now — it sees the pause at its next
    check (rsync polls every 1.0 s) and exits through the SIGTERM path."""
    started, seen = threading.Event(), {}

    def run(con, collection, spec, profile, stop=None):
        started.set()
        deadline = time.time() + 30
        while time.time() < deadline:
            if stop.is_set():
                seen["stopped_after_s"] = time.time()
                return {**_DONE, "aborted": 1, "total": 1}
            time.sleep(0.02)
        seen["stopped_after_s"] = None
        return _DONE

    collection, con = _wired(tmp_path, monkeypatch, run)
    try:
        worker._acquire(con, collection, "test")
        assert started.wait(10), "the fetch must start while running"
        paused_at = time.time()
        worker._paused.set()
        thread = worker._acquire_threads[collection.name]
        thread.join(timeout=10)
        assert not thread.is_alive(), "a paused node must not keep downloading"
        assert seen["stopped_after_s"] is not None
        assert seen["stopped_after_s"] - paused_at < 2.0, "and promptly, not at the next file"
    finally:
        _forget(collection)


def test_a_start_refused_by_the_pause_is_remembered(tmp_path, monkeypatch):
    """The first version of this fix returned here and recorded nothing,
    believing "the next discovery after resume calls here again". That
    discovery is DISCOVER_INTERVAL_MIN away — 360 minutes."""
    runs = []
    collection, con = _wired(tmp_path, monkeypatch,
                             lambda *a, stop=None, **k: runs.append(1) or _DONE)
    try:
        worker._paused.set()
        worker._acquire(con, collection, "while paused")
        assert runs == [], "no fetch starts while paused"
        assert collection.name in worker._paused_aborts, "but it is remembered"
    finally:
        _forget(collection)


def test_a_fetch_the_pause_stopped_is_rearmed_WITH_ACQUIRE_on_resume(tmp_path, monkeypatch):
    """THE TEST THE FIRST VERSION NEEDED AND DID NOT HAVE.

    Adversarial review, 2026-09-13: after a pause stopped the fetch, the
    post-fetch rescan was queued acquire=False — and `_request_discovery`
    is first-reason-wins, so no later acquire=True could replace it. The
    scan stamped the discovery clock and the next fetch waited the full
    DISCOVER_INTERVAL_MIN: 360 minutes of no downloading after every
    resume. A NEW gap, since before the fix a pause never stopped a fetch.

    The test it replaces called `_acquire` by hand and passed while the
    real wiring never made that call. This one drives the three real
    functions in the order the worker runs them."""
    def run(con, collection, spec, profile, stop=None):
        worker._paused.set()                  # operator clicks seed-only mid-fetch
        assert stop.is_set(), "the fetch sees the pause"
        return {**_DONE, "fetched": 3, "aborted": 1, "total": 10}

    collection, con = _wired(tmp_path, monkeypatch, run)
    last_discover = {}
    try:
        worker._acquire_worker(collection, _SPEC, "demo", "test")
        assert collection.name in worker._paused_aborts

        worker._finish_completed_acquires({}, [collection], last_discover, pipeline=True)
        assert worker._take_discovery_request(collection.name) is None, \
            "a pause-stopped fetch must NOT get the acquire=False post-fetch rescan"

        worker._paused.clear()                # resume
        assert worker._rearm_paused_aborts([collection], last_discover, True) \
            == [collection.name]
        assert worker._take_discovery_request(collection.name) == \
            ("resume after pause", True), "the resume must FETCH, not only scan"
    finally:
        _forget(collection)


def test_a_pause_lifted_while_the_fetch_unwinds_still_rearms(tmp_path, monkeypatch):
    """rsync gets a 10 s terminate grace. A resume inside that window must
    not lose the restart — so WHY the fetch stopped is read from the stop
    itself (`_AnyStop.fired`), never from `_paused` at exit."""
    def run(con, collection, spec, profile, stop=None):
        worker._paused.set()
        assert stop.is_set()
        worker._paused.clear()                # resumed before the thread exits
        return {**_DONE, "fetched": 1, "aborted": 1, "total": 5}

    collection, con = _wired(tmp_path, monkeypatch, run)
    try:
        worker._acquire_worker(collection, _SPEC, "demo", "test")
        assert collection.name in worker._paused_aborts
    finally:
        _forget(collection)


def test_a_normal_completion_behaves_exactly_as_before(tmp_path, monkeypatch):
    """No pause, no change: the post-fetch rescan is still acquire=False,
    which exists to stop the acquire<->discover tight loop."""
    collection, con = _wired(tmp_path, monkeypatch,
                             lambda *a, stop=None, **k: {**_DONE, "fetched": 2, "total": 2})
    last_discover = {}
    try:
        worker._acquire_worker(collection, _SPEC, "demo", "test")
        assert collection.name not in worker._paused_aborts
        worker._finish_completed_acquires({}, [collection], last_discover, pipeline=True)
        assert worker._take_discovery_request(collection.name) == \
            ("background fetch finished", False)
    finally:
        _forget(collection)


def test_a_retirement_is_not_mistaken_for_a_pause(tmp_path, monkeypatch):
    def run(con, collection, spec, profile, stop=None):
        worker._gone_event(collection.name).set()
        assert stop.is_set()
        return {**_DONE, "aborted": 1, "total": 1}

    collection, con = _wired(tmp_path, monkeypatch, run)
    try:
        worker._acquire_worker(collection, _SPEC, "demo", "test")
        assert collection.name not in worker._paused_aborts
    finally:
        worker._gone.pop(collection.name, None)
        _forget(collection)


def test_a_collection_retired_during_the_pause_is_not_rearmed(tmp_path, monkeypatch):
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, stop=None, **k: _DONE)
    try:
        with worker._acquire_lock:
            worker._paused_aborts.add(collection.name)
        assert worker._rearm_paused_aborts([], {}, True) == [], "not in the live set"
        assert collection.name not in worker._paused_aborts, "and not kept forever"
        assert worker._take_discovery_request(collection.name) is None
    finally:
        _forget(collection)


def test_a_stale_acquire_false_request_cannot_beat_the_resume(tmp_path, monkeypatch):
    """First-reason-wins is the trap: an acquire=False already queued would
    silently keep its place and the resume would only scan."""
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, stop=None, **k: _DONE)
    try:
        worker._request_discovery(collection.name, "stale", acquire=False)
        with worker._acquire_lock:
            worker._paused_aborts.add(collection.name)
        worker._rearm_paused_aborts([collection], {}, True)
        assert worker._take_discovery_request(collection.name) == \
            ("resume after pause", True)
    finally:
        _forget(collection)


def test_without_pipeline_threads_the_resume_discovers_itself(tmp_path, monkeypatch):
    """With WORKER_PIPELINE_THREADS=0 there are no discovery requests. The
    first version cleared the interval stamp and left the interval check to
    run `_discover` — and with DISCOVER_INTERVAL_MIN=0 ("startup only") that
    check never runs, so the fetch was lost until a restart. Second review,
    2026-09-13. The re-arm now runs `_discover` itself."""
    from app import config
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, stop=None, **k: _DONE)
    discovered = []
    monkeypatch.setattr(worker, "_discover",
                        lambda c, coll, reason, stop=None: discovered.append(reason))
    monkeypatch.setattr(config, "DISCOVER_INTERVAL_MIN", 0)
    try:
        with worker._acquire_lock:
            worker._paused_aborts.add(collection.name)
        worker._rearm_paused_aborts([collection], {}, False, cons={collection.name: con})
        assert discovered == ["resume after pause"], "even with the interval off"
    finally:
        _forget(collection)


def test_the_stats_beat_sets_the_pause_but_never_clears_it():
    """Fast to stop, normal to resume. The main loop sees a state change
    once per ROUND, and a round can run minutes (a 120 s embed stream, an
    unbounded bundle-inbox import); the stats beat is every 10 s. It only
    ever sets — so it can never make the node do work it was told not to."""
    try:
        worker._paused.clear()
        worker._note_pause_from_state("running")
        assert not worker._paused.is_set()
        for state in ("paused", "seed-only"):
            worker._paused.clear()
            worker._note_pause_from_state(state)
            assert worker._paused.is_set(), state
        for state in ("running", "acquire-only"):
            worker._note_pause_from_state(state)
            assert worker._paused.is_set(), f"{state}: a resume is the main loop's to make"
    finally:
        worker._paused.clear()


def test_acquire_only_keeps_fetching(tmp_path, monkeypatch):
    """acquire-only exists to keep the corpus growing while the heavy
    stages stand down — it must NOT be caught by this fix. It sets
    `_acquire_only` and clears `_paused`, so its fetch sees no stop."""
    captured = []
    collection, con = _wired(tmp_path, monkeypatch,
                             lambda *a, stop=None, **k: captured.append(stop) or _DONE)
    try:
        worker._acquire_only.set()
        worker._acquire(con, collection, "test")
        worker._acquire_threads[collection.name].join(timeout=10)
        assert captured, "acquire-only must still START a fetch"
        assert captured[0].is_set() is False, "and must not stop it"
    finally:
        _forget(collection)


def test_the_archive_download_honours_the_pause_too(tmp_path, monkeypatch):
    """The same omission in the second acquisition stream: `_acquire_archive`
    checked `_paused` before STARTING a shard, but a multi-GB shard already
    downloading (NCBI's shard 8 was 3.05 GB) ignored it."""
    captured = []

    def fetcher(con, collection, *, stop=None, fetch=None, log_state=None):
        captured.append(stop)
        return {"fetched": [], "reason": "test"}

    monkeypatch.setattr(worker.bundle_genesis_fetch, "run_fetcher", fetcher)
    collection = _collection(tmp_path, acquire_enabled=True)
    try:
        worker._archive_worker(collection, "test")
        stop = captured[0]
        assert stop.is_set() is False
        worker._paused.set()
        assert stop.is_set() is True, "an archive shard must stop on pause too"
    finally:
        _forget(collection)


# ---- second review, 2026-09-13: the race and the two-sources flap ----------

def _named(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return collections_mod.Collection(
        name=name, plugin="gutenberg", profile="demo", embed_mode_default=None,
        corpus_dir=d, manifest_path=d / "manifest.db", fts_dir=d / "fts",
        lance_dir=d / "lancedb", acquire_enabled=True, license_ack=None)


def test_a_pending_name_is_kept_while_its_old_fetch_thread_is_alive(tmp_path, monkeypatch):
    """Re-arming now would reach `_acquire`, which refuses a second fetch
    beside a live one and returns silently. Kept for the next round."""
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, stop=None, **k: _DONE)
    release = threading.Event()
    alive = threading.Thread(target=release.wait, args=(10,), daemon=True)
    alive.start()
    try:
        with worker._acquire_lock:
            worker._acquire_threads[collection.name] = alive
            worker._paused_aborts.add(collection.name)
        assert worker._rearm_paused_aborts([collection], {}, True) == []
        assert collection.name in worker._paused_aborts, "kept, not lost"
        release.set()
        alive.join(timeout=5)
        assert worker._rearm_paused_aborts([collection], {}, True) == [collection.name]
    finally:
        release.set()
        with worker._acquire_lock:
            worker._acquire_threads.pop(collection.name, None)
        _forget(collection)


def test_a_resume_during_the_threads_closing_write_does_not_lose_the_restart(
        tmp_path, monkeypatch):
    """Reproduced deterministically by the second review, now asserting the
    FIXED behaviour. The fetch is stopped by the pause, then blocks in its
    closing log write (a busy manifest waits out busy_timeout). The
    operator resumes inside that window. Before: the re-arm was consumed,
    `_acquire` saw the old thread alive and returned, the `finally`
    reported done, and the next round queued acquire=False — no fetch for
    360 minutes. After: the mark is published with "done", in the
    `finally`, so nothing is consumed early and the restart happens."""
    runs = []

    def run(con, collection, spec, profile, stop=None):
        runs.append(1)
        if len(runs) > 1:
            return _DONE                           # the restarted fetch
        worker._paused.set()                       # operator: seed-only
        assert stop.is_set()
        return {**_DONE, "fetched": 1, "aborted": 1, "total": 5}

    collection, con = _wired(tmp_path, monkeypatch, run)
    hold = threading.Event()
    real_log = worker.db.log_event

    def slow_log(con_, level, stage, message, *a, **k):
        if (threading.current_thread().name.startswith("acquire-")
                and message.startswith("corpus fetch (start)")):
            hold.wait(timeout=30)                  # the manifest is busy
        return real_log(con_, level, stage, message, *a, **k)

    monkeypatch.setattr(worker.db, "log_event", slow_log)
    last = {}
    try:
        worker._acquire(con, collection, "start")
        thread = worker._acquire_threads[collection.name]
        deadline = time.time() + 10
        while time.time() < deadline and not worker._paused.is_set():
            time.sleep(0.01)
        time.sleep(0.1)
        assert thread.is_alive(), "blocked in its closing write"

        worker._paused.clear()                     # resume, inside the window
        worker._finish_completed_acquires({}, [collection], last, pipeline=True)
        assert worker._rearm_paused_aborts([collection], last, True) == [], \
            "nothing may be consumed while the thread is still unwinding"

        hold.set()
        thread.join(timeout=10)
        assert collection.name in worker._paused_aborts, "pending, not lost"

        worker._finish_completed_acquires({}, [collection], last, pipeline=True)
        assert worker._take_discovery_request(collection.name) is None, \
            "and never the acquire=False post-fetch rescan"
        assert worker._rearm_paused_aborts([collection], last, True) == [collection.name]
        assert worker._take_discovery_request(collection.name) == \
            ("resume after pause", True)
        worker._acquire(con, collection, "resume after pause")
        worker._acquire_threads[collection.name].join(timeout=10)
        assert runs == [1, 1], "the fetch really starts again"
    finally:
        hold.set()
        _forget(collection)


def test_the_beat_reads_the_node_state_not_any_collection(isolated_config, tmp_path):
    """Reproduced by the second review: the beat read `load_all()[0]`, the
    main loop its FILTERED `all_collections[0]`. A collection with no
    worker_state reads 'paused' — so when the two differed (add one whose
    name sorts first) the beat set `_paused` every 10 s and the main loop
    cleared it and re-armed every round: a fetch stopped and restarted per
    beat. The node state now lives in state/node.sqlite, which both read;
    a first-sorting collection with no key changes nothing."""
    first = _named(tmp_path, "aaa-new")
    assert db.worker_state(db.connect(first)) == "paused", "no key reads as paused"
    node_state.set_worker_state("running")
    try:
        worker._main_loop_live.set()
        assert worker._control_state_for_beat(node_state.connect()) == "running", \
            "the node's state, not the first-sorting collection's"
    finally:
        worker._main_loop_live.clear()


def test_the_beat_does_nothing_without_a_main_loop(isolated_config, tmp_path):
    """Why five pipeline tests failed in the full suite: `test_stats.py`
    runs a real `_stats_publisher` with no main loop, against a fresh
    config whose state defaults to 'paused'. The beat set `_paused`,
    nothing ever cleared it, and every pipeline thread afterwards idled."""
    worker._main_loop_live.clear()
    assert worker._control_state_for_beat(node_state.connect()) is None


def test_a_rearmed_fetch_is_not_also_treated_as_a_normal_completion(tmp_path, monkeypatch):
    """Third review, 2026-09-13, reproduced: `_finish_completed_acquires`
    and `_rearm_paused_aborts` read the mark at different moments of one
    round. A fetch whose `finally` landed between them was re-armed AND kept
    `_acquire_done` — so the next round ran the acquire=False post-fetch
    rescan too (or, without pipeline threads, a second full inline scan).
    Nothing was lost; the work was paid twice."""
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, stop=None, **k: _DONE)
    last = {}
    try:
        # the fetch's finally lands AFTER this round's _finish already ran:
        # it publishes done and the pause mark together
        worker._finish_completed_acquires({}, [collection], last, pipeline=True)
        with worker._acquire_lock:
            worker._acquire_done[collection.name] = True
            worker._paused_aborts.add(collection.name)
        assert worker._rearm_paused_aborts([collection], last, True) == [collection.name]
        assert worker._take_discovery_request(collection.name) == \
            ("resume after pause", True)
        # the next round
        worker._finish_completed_acquires({}, [collection], last, pipeline=True)
        assert worker._take_discovery_request(collection.name) is None, \
            "no second, acquire=False scan for a fetch the resume already re-armed"
    finally:
        _forget(collection)


# ---- third review, 2026-09-13 ----------------------------------------------

def test_one_collections_failed_resume_scan_does_not_lose_the_others(tmp_path, monkeypatch):
    """Reproduced by the third review: without pipeline threads the re-arm
    cleared EVERY pending name, then ran `_discover` for each with no guard.
    One scan raising left the function and aborted the round — every name
    after it already cleared, never discovered, its stamp still fresh: back
    360 minutes later, or never with the interval off. A regression against
    the version before, which only popped stamps and so retried. Now each
    name is guarded, and one that fails is kept for the next round."""
    c, d = _named(tmp_path, "c"), _named(tmp_path, "d")
    cons = {x.name: db.connect(x) for x in (c, d)}
    calls = []

    def discover(con, coll, reason, stop=None):
        calls.append(coll.name)
        if coll.name == "c":
            raise OSError(5, "Input/output error")   # the corpus mount fails

    monkeypatch.setattr(worker, "_discover", discover)
    try:
        with worker._acquire_lock:
            worker._paused_aborts.update({"c", "d"})
        assert worker._rearm_paused_aborts([c, d], {}, False, cons=cons) == ["d"]
        assert calls == ["c", "d"], "d is still discovered after c raised"
        with worker._acquire_lock:
            assert worker._paused_aborts == {"c"}, "c is kept for the next round"
        monkeypatch.setattr(worker, "_discover",           # the mount is back
                            lambda con, coll, reason, stop=None: calls.append(coll.name))
        assert worker._rearm_paused_aborts([c, d], {}, False, cons=cons) == ["c"]
    finally:
        with worker._acquire_lock:
            worker._paused_aborts.difference_update({"c", "d"})


def test_an_idle_round_does_not_walk_the_collection_list(monkeypatch):
    """CLAUDE.md: a per-round cost scales with the work available, never
    with the size of a list. This runs every round the node is not paused
    and nearly always has nothing to do."""
    touched = []
    monkeypatch.setattr(worker, "_take_discovery_request", lambda n: touched.append(n))

    class Unwalkable:
        def __iter__(self):
            raise AssertionError("walked the collection list with nothing pending")

    with worker._acquire_lock:
        worker._paused_aborts.clear()
    assert worker._rearm_paused_aborts(Unwalkable(), {}, True) == []
    assert touched == []


# ---- a fetch another writer's claim refused, and a collection's resume (2026-11-26, D6 / D7) ----

def _retries_ended(timeout=5.0):
    """The retried fetches run on threads of their own (`_acquire_retried`)."""
    deadline = time.monotonic() + timeout
    for t in [t for t in threading.enumerate() if t.name.startswith("acquire-retry-")]:
        t.join(max(0.0, deadline - time.monotonic()))


def _forget_more(*names):
    with worker._acquire_lock:
        for n in names:
            worker._claim_retries.pop(n, None)
            worker._paused_waiting.discard(n)
            worker._rearm_reason.pop(n, None)
            worker._paused_aborts.discard(n)
    for n in names:
        worker._own_pauses.pop(n, None)


def test_a_fetch_refused_by_anothers_claim_is_asked_again_once_and_only_when_it_is_no_longer_held(
        tmp_path, monkeypatch):
    """München, 2026-11-26 (D6): "corpus fetch (startup) failed: AcquisitionBusy
    … re-run once the claim goes stale after 300s" — an ERROR, and nothing
    re-ran it. THE READS OF THE FIX: re-armed blindly when the claim's age ran
    out, a holder renewing every 60 s cost a full discovery every five
    minutes; and a discovery queues behind the scan running and walks the
    corpus again. The claim is read first; once it is free, the FETCH."""
    import json
    from app.acquire import claim as claim_mod
    clock = [time.time()]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])   # the claim's clock too: one module
    collection, con = _wired(tmp_path, monkeypatch, None)
    holder = {"pid": 8, "holder": "8@4026533985", "source_id": "gutenberg", "boot": claim_mod.boot_id()}

    def renewed():
        db.set_control(con, claim_mod.CLAIM_KEY, json.dumps({**holder, "ts": clock[0]}))

    def run(con_, collection_, spec, profile, stop=None):
        with claim_mod.acquisition_claim(con_, spec.source_id):
            raise AssertionError("fetched beside a live holder")
    monkeypatch.setattr(worker.acquire_engine, "run_acquisition", run)
    renewed()
    clock[0] += 179
    try:
        worker._acquire_worker(collection, _SPEC, "demo", "startup")
        rows = con.execute("SELECT level, message FROM events WHERE stage = 'acquire' ORDER BY id").fetchall()
        assert rows[-1][0] == "warn" and "deferred — another writer holds the acquisition claim " \
            "(8@4026533985, claimed 179 s ago); asked again in 126 s" in rows[-1][1]
        assert not [r for r in rows if r[0] == "error"], "a refusal is a deferral, never a failure"
        assert worker._claim_retries[collection.name] == clock[0] + 126
        fetched, asked = [], []
        monkeypatch.setattr(worker, "_acquire", lambda c, coll, why: fetched.append((coll.name, why)))
        monkeypatch.setattr(worker, "_request_discovery", lambda n, why, acquire=False: asked.append(n))
        cons = {collection.name: con}
        # THE COST: the holder renews for an hour; the retry falls due a dozen times
        for _ in range(12):
            renewed()
            clock[0] += 310
            renewed()                                   # renewed just before the retry reads it
            assert worker._rearm_paused_aborts([collection], {}, True, cons=cons, known=[collection]) == []
        assert fetched == [] and asked == [], "nothing asked while a live holder renews: no fetch, no scan"
        assert worker._claim_retries[collection.name] > clock[0], "put off to the claim's next staleness"
        db.set_control(con, claim_mod.CLAIM_KEY, "")    # the holder ends
        clock[0] += 310
        worker._rearm_paused_aborts([collection], {}, True, cons=cons, known=[collection])
        _retries_ended()
        assert fetched == [(collection.name, "the claim that refused it is no longer held")], "THE FETCH, once"
        assert asked == [], "never a discovery: it queues behind the running scan and walks the corpus"
        assert collection.name not in worker._claim_retries
        worker._rearm_paused_aborts([collection], {}, True, cons=cons, known=[collection])
        assert len(fetched) == 1
    finally:
        _forget_more(collection.name)
        _forget(collection)


def test_a_claim_that_cannot_be_read_is_read_again_later_not_every_round(tmp_path, monkeypatch):
    from app.acquire import claim as claim_mod
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, **k: _DONE)
    reads = []
    monkeypatch.setattr(claim_mod, "busy_for", lambda c: reads.append(1) or (_ for _ in ()).throw(
        __import__("sqlite3").OperationalError("database is locked")))
    try:
        with worker._acquire_lock:
            worker._claim_retries[collection.name] = time.time() - 1
        for _ in range(10):
            worker._rearm_paused_aborts([collection], {}, True, cons={collection.name: con}, known=[collection])
        assert reads == [1], "put off CLAIM_READ_AGAIN_S"
        assert worker._claim_retries[collection.name] > time.time() + worker.CLAIM_READ_AGAIN_S - 5
    finally:
        _forget_more(collection.name)
        _forget(collection)


def test_a_fetch_started_forgets_the_retry_a_claim_left(tmp_path, monkeypatch):
    """THE SECOND READ, run: a fetch started inside the retry window (the
    `acquire_enabled` toggle, an unfreeze) then met the retry, read its own
    claim as free and paid a full discovery after a fetch that had run."""
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, stop=None, **k: _DONE)
    try:
        with worker._acquire_lock:
            worker._claim_retries[collection.name] = time.time() + 100
        worker._acquire(con, collection, "toggle")
        worker._acquire_threads[collection.name].join(timeout=10)
        assert collection.name not in worker._claim_retries
    finally:
        _forget_more(collection.name)
        _forget(collection)


def test_the_stop_keeps_the_first_event_that_stopped_it():
    a, b = threading.Event(), threading.Event()
    stop = worker._AnyStop(a, b)
    b.set()
    assert stop.is_set() and stop.fired is b
    a.set()
    assert stop.is_set() and stop.fired is b, "a later event never rewrites why it stopped"


def test_a_fetch_its_collections_own_pause_stopped_is_asked_again_by_its_resume(tmp_path, monkeypatch):
    """München, 2026-11-26 (D7): `node pause` / `node resume EUR-Lex5.next`
    asked no fetch: the pause retired the fetch in flight (`_hold_locks` →
    `retire_collection` sets its gone event), and only the NODE's pause was
    re-armed. THE SECOND READ, run: a resume while the fetch still unwinds
    cleared the pause before its next poll, which read the retirement — no
    re-arm. Only what a pause cut is asked again."""
    def run(con, collection, spec, profile, stop=None):
        worker._own_pause_event(collection.name).set()     # `node pause <collection>`: `_hold_locks`
        worker._gone_event(collection.name).set()          # then `retire_collection`
        assert stop.is_set()
        worker._own_pause_event(collection.name).clear()   # resumed while it unwinds (the lock taken again)
        assert stop.is_set(), "rsync's grace: polled again"
        return _DONE
    collection, con = _wired(tmp_path, monkeypatch, run)
    asked = []
    monkeypatch.setattr(worker, "_request_discovery", lambda n, why, acquire=False: asked.append((n, why, acquire)))
    try:
        worker._acquire_worker(collection, _SPEC, "demo", "startup")
        worker._gone.pop(collection.name, None)            # as `retire_collection` ends
        assert collection.name in worker._paused_aborts
        assert worker._rearm_paused_aborts([], {}, True, known=[collection]) == [], "paused: not run"
        assert collection.name in worker._paused_waiting and asked == []
        worker._runs_again(collection.name)                # `_hold_locks` holds it again
        assert worker._rearm_paused_aborts([collection], {}, True, known=[collection]) == [collection.name]
        assert asked == [(collection.name, "the collection resumed", True)], "a FETCH, said as what it is"
        assert worker._rearm_paused_aborts([collection], {}, True, known=[collection]) == [], "once"
        assert len(asked) == 1, "a resume with nothing cut: nothing asked"
    finally:
        _forget_more(collection.name)
        _forget(collection)


def test_a_re_arm_for_a_collection_not_run_waits_and_one_retired_is_dropped(tmp_path, monkeypatch):
    """Paused on its own, or another writer's lock: the round's list leaves it
    out, and the name was dropped as if retired — its fetch lost for good.
    While it waits, the round walks no list."""
    collection = _collection(tmp_path, acquire_enabled=True)
    gone = "retired-meanwhile"
    with worker._acquire_lock:
        worker._paused_aborts.update({collection.name, gone})
        worker._rearm_reason[gone] = "the collection resumed"

    class Unwalkable:
        def __iter__(self):
            raise AssertionError("walked a collection list with nothing pending")
    try:
        assert worker._rearm_paused_aborts([], {}, True, known=[collection]) == []
        assert collection.name in worker._paused_waiting, "waits while not run"
        assert gone not in worker._paused_waiting and gone not in worker._rearm_reason, "retired: dropped"
        assert worker._rearm_paused_aborts(Unwalkable(), {}, True, known=Unwalkable()) == [], \
            "the idle round, a name waiting: no list walked"
        worker._runs_again(collection.name)
        assert worker._rearm_paused_aborts([collection], {}, True, known=[collection]) == [collection.name]
        assert worker._take_discovery_request(collection.name) == ("resume after pause", True)
        assert not worker._paused_waiting
    finally:
        _forget_more(collection.name, gone)
        _forget(collection)


def test_a_claim_retry_for_a_collection_not_run_waits_and_says_why_when_it_comes_back(tmp_path, monkeypatch):
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, **k: _DONE)
    try:
        with worker._acquire_lock:
            worker._claim_retries[collection.name] = time.time() - 1
        worker._rearm_paused_aborts([], {}, True, cons={collection.name: con}, known=[collection])
        assert collection.name in worker._paused_waiting and collection.name not in worker._claim_retries
        worker._runs_again(collection.name)
        assert collection.name not in worker._paused_aborts, "never a discovery (the third read)"
        assert worker._claim_retries[collection.name] <= time.time(), "the FETCH again, due now"
        fetched = []
        monkeypatch.setattr(worker, "_acquire", lambda c, coll, why: fetched.append(why))
        worker._rearm_paused_aborts([collection], {}, True, cons={collection.name: con}, known=[collection])
        _retries_ended()
        assert fetched == ["the claim that refused it is no longer held"]
    finally:
        _forget_more(collection.name)
        _forget(collection)


def test_a_collection_removed_forgets_what_waited_to_fetch_it(tmp_path, monkeypatch):
    """THE SECOND READ, run: a name waiting for a collection removed while
    paused stayed for ever — and a collection made again under its name got a
    discovery labelled "the collection resumed"."""
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, **k: _DONE)
    monkeypatch.setattr(worker, "_sweep_unheld_embedders", lambda *a, **k: None)
    name = collection.name
    with worker._acquire_lock:
        worker._claim_retries[name] = time.time() + 100
        worker._paused_waiting.add(name)
        worker._rearm_reason[name] = "the collection resumed"
        worker._paused_aborts.add(name)
    worker._own_pause_event(name).set()
    try:
        worker._leave(name, {name: con}, [], {}, {}, "removed")
        assert name not in worker._claim_retries and name not in worker._paused_waiting
        assert name not in worker._rearm_reason and name not in worker._paused_aborts
        assert name not in worker._own_pauses
    finally:
        _forget_more(name)
        worker._gone.pop(name, None)
        _forget(collection)


def test_a_collections_own_pause_is_told_before_its_retirement_and_forgotten_once_held_again(
        tmp_path, monkeypatch):
    from app.hold import lock as lock_mod
    collection = _collection(tmp_path, acquire_enabled=True)
    seen = []

    class Lock:
        def release(self):
            pass
    monkeypatch.setattr(worker, "retire_collection",
                        lambda name, **k: seen.append((name, worker._own_pause_event(name).is_set())))
    monkeypatch.setattr(lock_mod, "take", lambda *a, **k: Lock())
    monkeypatch.setitem(worker._locks, collection.name, Lock())
    try:
        assert worker._hold_locks([collection], {collection.name}) == []
        assert seen == [(collection.name, True)], "told BEFORE its threads are retired"
        with worker._acquire_lock:
            worker._paused_waiting.add(collection.name)
        assert worker._hold_locks([collection], set()) == [collection]
        assert not worker._own_pause_event(collection.name).is_set(), "held again: forgotten"
        assert collection.name in worker._paused_aborts and not worker._paused_waiting, "its re-arm is back"
        worker._hold_locks([], set())                      # removed: a retirement, not a pause
        assert seen[-1] == (collection.name, False)
    finally:
        worker._locks.pop(collection.name, None)
        _forget_more(collection.name)



def test_a_claim_retry_due_while_the_refused_thread_still_ends_is_kept(tmp_path, monkeypatch):
    """THE THIRD READ, run: the retry fell due while the refused thread's log
    write waited out the busy timeout; `_acquire` returned silently beside the
    live thread, and nothing was left to ask the fetch again."""
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, **k: _DONE)
    alive = threading.Event()
    thread = threading.Thread(target=alive.wait, args=(10,), daemon=True)
    thread.start()
    fetched = []
    monkeypatch.setattr(worker, "_acquire", lambda c, coll, why: fetched.append(why))
    try:
        with worker._acquire_lock:
            worker._acquire_threads[collection.name] = thread
            worker._claim_retries[collection.name] = time.time() - 1
        worker._rearm_paused_aborts([collection], {}, True, cons={collection.name: con}, known=[collection])
        assert fetched == [] and worker._claim_retries[collection.name] > time.time() + 20, "kept"
    finally:
        alive.set()
        thread.join(5)
        with worker._acquire_lock:
            worker._acquire_threads.pop(collection.name, None)
        _forget_more(collection.name)
        _forget(collection)


def test_acquisition_turned_on_again_while_the_stopped_fetch_unwinds_asks_it_again(tmp_path, monkeypatch):
    """THE THIRD READ, run, in the production order (the "on" edit applied
    first): `_acquire` found the old thread alive and the event clear, and
    returned — the fetch was lost."""
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, **k: _DONE)
    import dataclasses
    off = dataclasses.replace(collection, acquire_enabled=False)
    alive = threading.Event()
    thread = threading.Thread(target=alive.wait, args=(10,), daemon=True)
    thread.start()
    try:
        with worker._acquire_lock:
            worker._acquire_threads[collection.name] = thread
        worker._apply_acquire_switch(con, off, collection)
        assert collection.name in worker._paused_aborts, "asked again once that thread is gone"
    finally:
        alive.set()
        thread.join(5)
        with worker._acquire_lock:
            worker._acquire_threads.pop(collection.name, None)
        _forget_more(collection.name)
        _forget(collection)



def test_a_fetch_deferred_for_a_busy_manifest_is_asked_again(tmp_path, monkeypatch):
    """THE FOURTH READ, run: the completion flag asks nothing for a fetch that
    fetched nothing, and the busy deferral was lost until the interval's
    discovery (360 min, or never)."""
    import sqlite3

    def busy(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    collection, con = _wired(tmp_path, monkeypatch, busy)
    try:
        worker._acquire_worker(collection, _SPEC, "demo", "startup")
        assert worker._claim_retries[collection.name] > time.time()
        rows = con.execute("SELECT message FROM events WHERE stage = 'acquire' ORDER BY id").fetchall()
        assert "manifest busy" in rows[-1][0] and "retrying in 30 s" in rows[-1][0]
    finally:
        _forget_more(collection.name)
        _forget(collection)


def test_the_re_arm_acquisition_turned_on_left_is_forgotten_when_a_fresh_fetch_starts(tmp_path, monkeypatch):
    """THE FOURTH READ, run: the old thread ended before the change's
    discovery came, a fresh fetch ran — and the re-arm asked one more
    discovery and one more fetch after it."""
    import dataclasses
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, stop=None, **k: _DONE)
    off = dataclasses.replace(collection, acquire_enabled=False)
    alive = threading.Event()
    old = threading.Thread(target=alive.wait, args=(10,), daemon=True)
    old.start()
    try:
        with worker._acquire_lock:
            worker._acquire_threads[collection.name] = old
        worker._apply_acquire_switch(con, off, collection)
        assert collection.name in worker._paused_aborts
        alive.set()
        old.join(5)                                        # it ends before the change's discovery comes
        worker._acquire(con, collection, "config changed")
        worker._acquire_threads[collection.name].join(timeout=10)
        assert collection.name not in worker._paused_aborts, "the fresh fetch owes it nothing"
    finally:
        alive.set()
        _forget_more(collection.name)
        _forget(collection)



def test_only_a_busy_manifest_is_a_deferral_retried_and_said_so(tmp_path, monkeypatch):
    """THE FIFTH READ, run: a read-only manifest was retried every 30 s, for
    ever, listing the origin each time, and said "manifest busy"."""
    import sqlite3
    for err, retried in ((sqlite3.OperationalError("database is locked"), True),
                         (sqlite3.OperationalError("attempt to write a readonly database"), False)):
        def run(*a, _e=err, **k):
            raise _e
        sub = tmp_path / ("busy" if retried else "readonly")
        sub.mkdir()
        collection, con = _wired(sub, monkeypatch, run)
        try:
            worker._acquire_worker(collection, _SPEC, "demo", "startup")
            rows = con.execute("SELECT level, message FROM events WHERE stage = 'acquire' ORDER BY id").fetchall()
            assert (collection.name in worker._claim_retries) is retried, str(err)
            if retried:
                assert rows[-1][0] == "warn" and worker._retry_reason[collection.name] == "the manifest was busy"
            else:
                assert rows[-1][0] == "error" and "the manifest refused it" in rows[-1][1]
        finally:
            _forget_more(collection.name)
            worker._retry_reason.pop(collection.name, None)
            _forget(collection)


def test_a_retried_fetch_never_holds_the_main_loop_on_a_busy_manifest(tmp_path, monkeypatch):
    """THE FIFTH READ, run: `_acquire` wrote its line on the main loop's
    connection to the manifest a scan starved, and the round waited the busy
    timeout (60 s deployed)."""
    started = []
    collection, con = _wired(tmp_path, monkeypatch, lambda *a, **k: _DONE)
    monkeypatch.setattr(worker, "_acquire", lambda c, coll, why: (time.sleep(1.0), started.append(why)))
    try:
        with worker._acquire_lock:
            worker._claim_retries[collection.name] = time.time() - 1
            worker._retry_reason[collection.name] = "the manifest was busy"
        t0 = time.monotonic()
        worker._rearm_paused_aborts([collection], {}, True, cons={collection.name: con}, known=[collection])
        assert time.monotonic() - t0 < 0.5, "the round does not wait for it"
        _retries_ended()
        assert started == ["the manifest was busy"], "and the fetch says why it is asked"
    finally:
        _forget_more(collection.name)
        _forget(collection)
