"""ONE LOOK OF A COLLECTION AT A TIME, BESIDE ITS CALLER (app/run_check.py
`_Look`; the node, 2026-11-27).

gutenberg after its refit: one SYMMETRIC encoder (vega-qwen3-embedding-4b
serves the document and the query side), one new run, one gate twin
`…--gate` for both sides. Its gate measured inside the look of whichever
worker thread looked first, for some 1,984 s a side, holding that thread for
all of it; the other caller of the same collection, let past the model's look
lock by `_twin_busy`, looked again every FAIL_TTL_S, and its two sides found
the twin claimed by their own sibling and deferred as "another collection's
gate" — an error, "embedding PAUSED", every time.

What is proved here, on fakes (the gate, the twin, the statements):

  - the two sides of one run are gated in ONE look, the document side first,
    under ONE claim of the twin, and neither ever defers behind the other;
  - the caller is not held for the measurement: it is answered beside the
    look after GATE_WAIT_S, and every later caller too, without a probe;
  - two DIFFERENT collections on one twin still defer each other — said as a
    wait, never an error — and both pass in time;
  - the verdict the look reached is kept, by the look itself, after its caller
    has gone; a restart reads it from the kept statements;
  - SIGTERM mid-gate: the look ends at the gate's next slice, keeping
    nothing, its twin let go and its claim released, and the worker's stop
    waits for it.
"""
import contextlib
import dataclasses
import fcntl
import json
import os
import threading
import time

import pytest

from app import collections as collections_mod, db, encoder_use, run_check, worker
from app.hold import collection as col, runs as hold_runs
from app.verify import backend_gate
from tests.hold.conftest import KEY, NOW, PLATFORM
from tests.hold.test_template import VALIDATION, recipe as a_recipe
from tests.test_run_check import endpoint, node, opening, opening_query  # noqa: F401  (the fixtures)

SERVER = "http://localai:8080"
MODEL = "vega-qwen3-embedding-4b"
TWIN = f"{MODEL}--gate"
RUN = "sha256:" + "7a" * 32
#: gutenberg's document gate on the node (approved-encoders.md, the fade pass),
#: taken for each side; the settle thread beats every 60 s (worker.SETTLE_BEAT_S)
GATE_S, BEAT_S = 1984, 60


class Node:
    """The fakes: which run answers, what is kept, the twin, the gate."""

    def __init__(self, root, monkeypatch):
        self.root = root
        self.kept: dict[tuple[str, str], dict] = {}
        self.gates: list[tuple[str, str, str]] = []        # (collection, role, model asked)
        self.claims: list[str] = []
        self.let_go: list[str] = []
        self.probes: list[str] = []                         # the thread of each probe
        self.clock = [time.time()]
        self.block: dict[tuple[str, str], threading.Event] = {}
        self.measuring = threading.Event()
        self.deaf = False               # a slice longer than any bound: the stop is not asked
        self.on_beat = None
        self.beats = GATE_S // BEAT_S
        mp = monkeypatch
        mp.setattr(run_check.time, "time", lambda: self.clock[0])
        mp.setattr(run_check, "GATE_WAIT_S", 0.2, raising=False)

        def recipe_for(c, *a, **k):
            base = real_recipe_for(c, *a, **k)
            return dataclasses.replace(base, url=SERVER, model=MODEL, query_url=SERVER,
                                       query_model=MODEL, api="vega")
        real_recipe_for = collections_mod.recipe_for
        mp.setattr(collections_mod, "recipe_for", recipe_for)

        def answering_run(recipe, *, query=False, probe_item=None):
            self.probes.append(threading.current_thread().name)
            return RUN
        mp.setattr(run_check, "answering_run", answering_run)

        def check(root, name, codex, answered, *, signer, role="document", **kw):
            if (name, role) not in self.kept:
                raise hold_runs.RunUnvalidated(f"{name}: run {RUN[:23]}… has not been tested", RUN)
        mp.setattr(hold_runs, "check", check)
        mp.setattr(hold_runs, "statements_for_space",
                   lambda root, name, codex, role="document", **kw:
                   {RUN: self.kept[(name, role)]} if (name, role) in self.kept else {})
        mp.setattr(hold_runs, "passing", lambda body, codex: True)
        mp.setattr(run_check, "recheck", lambda *a, **k: True)
        mp.setattr(run_check, "_foreign", lambda *a, **k: None)

        def twin_for(recipe, role, name, codex):
            field = "query_model" if role == "query" else "model"
            return dataclasses.replace(recipe, **{field: TWIN}), SERVER, TWIN
        mp.setattr(run_check, "_twin_for", twin_for)
        mp.setattr(run_check, "_let_go", lambda server, twin: self.let_go.append(twin))
        mp.setattr(run_check, "_pinned", lambda *a, **k: contextlib.nullcontext())
        mp.setattr(encoder_use, "held", lambda *a, **k: contextlib.nullcontext())
        real_claim = run_check._claim_twin

        def claim(server, twin, name):
            self.claims.append(name)
            return real_claim(server, twin, name)
        mp.setattr(run_check, "_claim_twin", claim)
        mp.setattr(run_check, "_validate", self.gate)

    def gate(self, root, name, codex, recipe, *, role="document", stop=None, **kw):
        """The gate's measurement: GATE_S of it, in beats; the gate asks
        `stop` between slices (`run_check._stopping`), so does this."""
        if not self.deaf:
            run_check._stopping(stop, name)
        self.gates.append((name, role, recipe.query_model if role == "query" else recipe.model))
        self.measuring.set()
        wait = self.block.get((name, role))
        while wait is not None and not wait.wait(0.01):
            if not self.deaf:
                run_check._stopping(stop, name)
        for _ in range(self.beats):
            self.clock[0] += BEAT_S
            if not self.deaf:
                run_check._stopping(stop, name)
            if self.on_beat is not None:
                self.on_beat()
        body = {"run": RUN, "role": role, "passed": 16, "median": "0.99999", "batch_invariant": True}
        # kept as `runs.validate` keeps it: the real `keep`, with the look's
        # refusal where the look has one
        refuse = {"refuse": run_check._keep_refused} if hasattr(run_check, "_keep_refused") else {}
        hold_runs.keep(root, name, {"body": body}, **refuse)
        self.kept[(name, role)] = body
        return body


@pytest.fixture
def sym(node, endpoint, monkeypatch):
    root, collection, codex = node
    assert codex.space.query.encoder is not None, "a query side to gate"
    n = Node(root, monkeypatch)
    assert run_check._flights(collection) == [f"{SERVER} {MODEL}"], "symmetric: one model, one twin"
    real_join = getattr(run_check, "join_gates", None)     # a test may stub it
    yield n, collection
    for e in n.block.values():
        e.set()
    real_join(10) if real_join is not None else None
    for t in _started:          # a caller held by its gate ends before the fixtures go
        t.join(30)
    _started.clear()


def _events(collection) -> list[tuple[str, str]]:
    con = db.connect(collection)
    try:
        return [(r[0], r[1]) for r in con.execute(
            "SELECT level, message FROM events WHERE stage='run-check' ORDER BY rowid")]
    finally:
        con.close()


_started: list[threading.Thread] = []


def _in_thread(fn, timeout: float = 30.0):
    """`fn()` on a thread of its own, as another worker thread calls it."""
    out: dict = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as e:   # noqa: BLE001
            out["error"] = e
    t = threading.Thread(target=run, name="settle-runs")
    _started.append(t)
    t.start()
    t.join(timeout)
    return t, out


def _settle(collection, stop=None):
    """The settle thread's call: its own connection, as each worker thread has."""
    def call():
        con = db.connect(collection)
        try:
            return backend_gate.embed_allowed(con, collection, stop=stop)
        finally:
            con.close()
    return call


def _join_gates():
    return getattr(run_check, "join_gates", lambda timeout: True)(30)


def test_the_two_sides_of_one_run_are_gated_in_one_look_and_never_defer_each_other(sym):
    """THE NODE'S LOOP, REPRODUCED: the producer looks first and the gate
    measures; the settle thread beats every 60 s beside it. Before: each of
    its looks probed the model beside the measurement, and both sides found
    the twin claimed by their own sibling — "another collection's gate",
    an error. Now the settle thread is answered beside the look, the two
    sides are gated in turn under one claim, and the verdict is reached."""
    n, collection = sym
    n.on_beat = lambda: _in_thread(_settle(collection))
    con = db.connect(collection)
    backend_gate.embed_allowed(con, collection)               # the producer: the look starts
    assert _join_gates(), "the look ended"
    events = _events(collection)
    sibling = [m for _, m in events if "another collection's gate" in m]
    errors = [m for lvl, m in events if lvl == "error"]
    beside = [t for t in n.probes if t == "settle-runs"]
    assert not sibling and not errors and n.claims == ["gutenberg"], (
        f"{len(sibling)} side(s) deferred behind their own sibling, {len(beside)} probe(s) of the "
        f"model by the second caller beside the measurement, {len(errors)} error event(s), "
        f"{len(n.claims)} twin claim attempt(s) for two sides, over {2 * GATE_S} s of gate")
    assert not beside, "no probe of the model beside its own gate"
    assert n.gates == [("gutenberg", "document", TWIN), ("gutenberg", "query", TWIN)], \
        "the document side, then the query side, each once, on the twin"
    assert n.let_go == [TWIN, TWIN], "the twin stopped after each side, before the claim went"
    assert backend_gate.embed_allowed(con, collection) is True, "the verdict is reached"
    assert run_check._verdicts[run_check._where(collection)][5] == RUN, "the query side settled"


def test_the_caller_is_not_held_for_the_measurement(sym):
    """The look's gate measures as long as it takes; its caller is answered
    beside it after GATE_WAIT_S, and so is every later caller — no second
    look, no probe beside the measurement, nothing said for them."""
    n, collection = sym
    n.beats = 0
    n.block[("gutenberg", "document")] = threading.Event()
    t0 = time.monotonic()
    p, out = _in_thread(_settle(collection), timeout=3)
    held = time.monotonic() - t0
    try:
        assert not p.is_alive() and n.measuring.is_set(), \
            f"the caller was held for the measurement ({held:.1f} s and counting)"
        assert out.get("value") is False and held < 5
        probes, events = len(n.probes), len(_events(collection))
        for _ in range(3):
            t, again = _in_thread(_settle(collection), timeout=10)
            assert not t.is_alive() and again.get("value") is False
        assert len(n.probes) == probes and len(_events(collection)) == events, \
            "a later caller does not look again beside the look running"
        assert run_check.gate_running(collection)
    finally:
        n.block[("gutenberg", "document")].set()
    assert _join_gates()
    assert not run_check.gate_running(collection)
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    assert [r for _, r, _ in n.gates] == ["document", "query"]


def _second_collection(tmp_path, root):
    """Another collection, another space's worth of statements, on the same
    model: its looks meet the same twin."""
    codex = col.create_from_recipe(
        root, "gutenberg", a_recipe(validation={**VALIDATION, "sample": {"records": {"shards": "0.10"}, "chunks": {"shards": "0.10"}}}), platform_image=PLATFORM,
        embed=opening, embed_query=opening_query, name="wikisource", key=KEY, now=NOW)
    (tmp_path / "config" / "collections" / "wikisource.toml").write_text(
        f'codex = "{codex.collection_id}"\nembed = "on-demand"\n'
        f'[encoder]\nurl = "{SERVER}"\nquery_url = "{SERVER}"\n'
        f'embed_model = "{MODEL}"\nquery_model = "{MODEL}"\n', encoding="utf-8")
    return next(c for c in collections_mod.load_all() if c.name == "wikisource")


def test_two_collections_on_one_twin_still_defer_each_other_and_both_pass_in_time(sym, tmp_path):
    n, gutenberg = sym
    wikisource = _second_collection(tmp_path, n.root)
    n.beats = 1
    n.block[("gutenberg", "document")] = threading.Event()
    _in_thread(_settle(gutenberg), timeout=10)
    assert n.measuring.wait(5)
    try:
        con = db.connect(wikisource)
        assert backend_gate.embed_allowed(con, wikisource) is False
        said = [(lvl, m) for lvl, m in _events(wikisource) if "[wikisource] embedding" in m]
        assert said and all(lvl == "info" for lvl, _ in said), said
        assert "embedding waits for its gate" in said[-1][1] and "another collection's gate" in said[-1][1]
        assert ("wikisource", "document") not in [(c, r) for c, r, _ in n.gates], "not measured beside it"
    finally:
        n.block[("gutenberg", "document")].set()
    assert _join_gates()
    con_g = db.connect(gutenberg)
    assert backend_gate.embed_allowed(con_g, gutenberg) is True
    assert not [m for _, m in _events(gutenberg) if "another collection's gate" in m], \
        "the first collection never waited behind the second"
    n.clock[0] += run_check.FAIL_TTL_S + 1                    # the wait's bound passes
    backend_gate.embed_allowed(con, wikisource)
    assert _join_gates()
    assert backend_gate.embed_allowed(con, wikisource) is True, "the second passes in its turn"
    assert [(c, r) for c, r, _ in n.gates] == [("gutenberg", "document"), ("gutenberg", "query"),
                                               ("wikisource", "document"), ("wikisource", "query")]
    assert not [m for lvl, m in _events(wikisource) + _events(gutenberg) if lvl == "error"]


def test_the_verdict_is_kept_by_the_look_after_its_caller_has_gone(sym):
    n, collection = sym
    n.beats = 0
    n.block[("gutenberg", "document")] = threading.Event()
    p, out = _in_thread(_settle(collection), timeout=10)
    assert not p.is_alive() and out.get("value") is False      # answered beside it
    n.block[("gutenberg", "document")].set()
    assert _join_gates()
    where = run_check._where(collection)
    kept = run_check._verdicts[where]
    assert kept[0] is True and kept[3] == RUN and kept[5] == RUN, kept[:6]
    con = db.connect(collection)
    assert json.loads(db.get_control(con, backend_gate.GATE_STATE_KEY))["allowed"] is True
    assert any(lvl == "info" and "embedding enabled" in m for lvl, m in _events(collection)), \
        "said by the look itself, on its own connection"
    probes = len(n.probes)
    assert backend_gate.embed_allowed(con, collection) is True
    assert len(n.probes) == probes, "served from the verdict kept, no look"
    run_check.reset_cache()                                     # a restart
    assert backend_gate.embed_allowed(con, collection) is True
    assert len(n.gates) == 2, "the kept statements: nothing gated again"


def _claim_free(path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def test_sigterm_mid_gate_ends_the_look_at_its_next_slice_and_keeps_nothing(sym, monkeypatch):
    n, collection = sym
    n.beats = 0
    stopping = threading.Event()
    n.block[("gutenberg", "document")] = threading.Event()     # measuring until the stop
    p, out = _in_thread(_settle(collection, stop=stopping.is_set), timeout=10)
    assert not p.is_alive() and n.measuring.wait(5)
    lock = run_check.twin_lock_path(SERVER, TWIN)
    assert not _claim_free(lock), "the look holds the twin"
    real_join, asked = run_check.join_gates, []
    monkeypatch.setattr(run_check, "join_gates", lambda timeout: asked.append(timeout) or True)
    worker._join_pipeline_threads(timeout=1.0)
    monkeypatch.setattr(run_check, "join_gates", real_join)
    assert asked, "the worker's stop waits for the look"
    stopping.set()                                              # SIGTERM
    assert real_join(5) and not run_check.gate_running(collection), "ended at its next slice"
    assert n.kept == {}, "nothing kept"
    assert n.let_go == [TWIN] and _claim_free(lock), "its twin let go, its claim released"
    said = [(lvl, m) for lvl, m in _events(collection) if "embedding" in m]
    assert said and said[-1][0] == "info" and "the node is stopping" in said[-1][1], said
    assert not [m for lvl, m in _events(collection) if lvl == "error"]
    # the next start: gated from scratch, and it passes
    n.block.clear()
    n.clock[0] += run_check.FAIL_TTL_S + 1
    con = db.connect(collection)
    backend_gate.embed_allowed(con, collection)
    t0 = time.monotonic()
    while run_check.gate_running(collection) and time.monotonic() - t0 < 10:
        time.sleep(0.01)
    assert backend_gate.embed_allowed(con, collection) is True
    assert [r for _, r, _ in n.gates] == ["document", "document", "query"]


# ---- the read of 1847af32: a look meets its collection's retirement ------------------

def _gone_or_moved(n, how: str):
    """A forget takes the collection's directory away; a rename moves it."""
    import shutil
    here = n.root / "gutenberg"
    if how == "forget":
        shutil.rmtree(here)
    elif how == "rename":
        os.rename(here, n.root / "gutenberg-renamed")


def _nothing_kept_anywhere(n) -> None:
    assert n.kept == {}, "no statement kept"
    assert not (n.root / "gutenberg").exists() or \
        not list(hold_runs.directory(n.root, "gutenberg").glob("*.json"))
    moved = hold_runs.directory(n.root, "gutenberg-renamed")
    assert not moved.is_dir() or not list(moved.glob("*.json")), "nor under its new name"


@pytest.mark.parametrize("how", ["pause", "forget", "rename"])
def test_a_retirement_mid_look_ends_it_within_the_bound(sym, how):
    """`worker.retire_collection` — a pause, a forget, a rename — joined every
    thread of the collection but its look, whose stop was the worker's alone:
    the gate went on measuring, the twin held. Now the look is retired: it
    ends at the gate's next slice within the retirement's bound, its twin let
    go and its claim released, nothing kept, no verdict and no backoff left —
    and a resume looks at once."""
    n, collection = sym
    n.beats = 0
    n.block[("gutenberg", "document")] = threading.Event()     # measuring, asking the stop
    _in_thread(_settle(collection), timeout=10)
    assert n.measuring.wait(5)
    lock = run_check.twin_lock_path(SERVER, TWIN)
    t0 = time.monotonic()
    worker.retire_collection("gutenberg", timeout=5.0)
    took = time.monotonic() - t0
    assert not run_check.gate_running(collection) and took < 5.0, \
        f"the look still measures {took:.1f} s after its collection's retirement"
    assert _claim_free(lock) and n.let_go == [TWIN], "its twin let go, its claim released"
    where = run_check._where(collection)
    assert where not in run_check._verdicts and run_check._gate_slow == {}, "no verdict, no backoff"
    events = _events(collection)
    assert not [m for _, m in events if "embedding " in m and ("waits" in m or "PAUSED" in m)], events
    _gone_or_moved(n, how)
    _nothing_kept_anywhere(n)
    if how == "pause":                                          # the resume: its lock taken again
        n.block.clear()
        worker._hold_locks([collection], set())
        try:
            con = db.connect(collection)
            backend_gate.embed_allowed(con, collection)
            assert _join_gates() and backend_gate.embed_allowed(con, collection) is True
        finally:
            worker._release_locks()
    else:
        assert how != "forget" or not (n.root / "gutenberg").exists()


@pytest.mark.parametrize("how", ["forget", "rename"])
def test_a_look_that_outlives_its_retirement_makes_nothing_again(sym, how):
    """A slice longer than the retirement's bound: the look is still in its
    gate when the directory goes. Its own connection was `db.connect`, which
    made the forgotten collection's directory and manifest again, and the
    query side's statement then landed in it. Now a retired look writes
    nothing, opens no manifest that is not there, and keeps nothing."""
    n, collection = sym
    n.beats, n.deaf = 0, True
    n.block[("gutenberg", "document")] = threading.Event()     # deaf to the stop
    _in_thread(_settle(collection), timeout=10)
    assert n.measuring.wait(5)
    worker.retire_collection("gutenberg", timeout=0.2)        # its bound passes
    assert run_check.gate_running(collection), "still in the slice"
    _gone_or_moved(n, how)
    n.block[("gutenberg", "document")].set()
    assert _join_gates()
    assert not (n.root / "gutenberg").exists(), "nothing made again where the collection was"
    _nothing_kept_anywhere(n)


def test_a_retired_look_keeps_nothing_for_either_side(sym, tmp_path):
    """`runs.keep` refuses, for each side, a look retired meanwhile — and a
    collection whose directory is gone."""
    n, _ = sym
    look = run_check._Look("place", None)
    run_check._pass_local.look = look
    try:
        for role in ("document", "query"):
            body = {"run": RUN, "role": role, "passed": 16}
            assert hold_runs.keep(n.root, "gutenberg", {"body": body},
                                  refuse=run_check._keep_refused).is_file()
        look.retired.set()
        for role in ("document", "query"):
            with pytest.raises(run_check.emb.GateDeferred, match="paused, forgotten or renamed"):
                hold_runs.keep(n.root, "gutenberg", {"body": {"run": RUN, "role": role}},
                               refuse=run_check._keep_refused)
    finally:
        run_check._pass_local.look = None
    with pytest.raises(hold_runs.CodexError, match="no such collection"):
        hold_runs.keep(tmp_path, "gone", {"body": {"run": RUN, "role": "document"}})


def test_join_gates_skips_a_look_not_started_and_one_join_never_skips_the_others(sym, monkeypatch):
    """`join_gates` met a look whose thread was not started yet (it was set
    before `start()`): RuntimeError, and `_join_pipeline_threads` then skipped
    the scans and fetches after it."""
    n, collection = sym
    look = run_check._Look("not-started", collection)
    look.thread = threading.Thread(target=lambda: None)
    monkeypatch.setitem(run_check._in_flight, "not-started", look)
    assert run_check.join_gates(0.5) is True

    class Scan:
        joined: list = []

        def join(self, timeout=None):
            self.joined.append(timeout)

    def raises(timeout):
        raise RuntimeError("cannot join thread before it is started")
    monkeypatch.setattr(run_check, "join_gates", raises)
    scan = Scan()
    monkeypatch.setitem(worker._scan_threads, "a-scan", scan)
    worker._join_pipeline_threads(timeout=1.0)
    assert scan.joined, "the scans are joined after a join that raised"


def test_a_caller_beside_a_look_stands_aside_once_the_statements_changed(sym, monkeypatch):
    """Beside a look, a caller was answered from the verdict there was, about
    the same run — even where the kept statements had changed since (one set
    aside, the look gating again): its stream would disturb its own
    collection's re-gate. The stamp is held to as the cache holds it."""
    n, collection = sym
    n.beats = 0
    con = db.connect(collection)
    backend_gate.embed_allowed(con, collection)
    assert _join_gates() and backend_gate.embed_allowed(con, collection) is True
    n.clock[0] += run_check.PASS_TTL_S + 1                   # the verdict is due again
    held = threading.Event()
    monkeypatch.setattr(run_check, "recheck", lambda *a, **k: held.wait(10) or True)
    try:
        t, out = _in_thread(_settle(collection), timeout=10)
        assert not t.is_alive() and run_check.gate_running(collection)
        assert out.get("value") is True, "beside it, the verdict about the same run stands"
        (doc,) = hold_runs.directory(n.root, "gutenberg").glob("document-*.json")
        doc.rename(doc.with_name(doc.name + ".aside"))          # set aside meanwhile
        t, out = _in_thread(_settle(collection), timeout=10)
        assert out.get("value") is False, "the statements changed: the caller stands aside"
    finally:
        held.set()
    assert _join_gates()


def test_sigterm_for_real_the_shutdown_ends_a_look_within_its_deadline(sym):
    """No stub: the worker's own stop and its own shutdown join, a look in the
    middle of its measurement."""
    n, collection = sym
    n.beats = 0
    n.block[("gutenberg", "document")] = threading.Event()
    _in_thread(_settle(collection, stop=worker._stop.is_set), timeout=10)
    assert n.measuring.wait(5) and run_check.gate_running(collection)
    lock = run_check.twin_lock_path(SERVER, TWIN)
    worker._stop.set()                                          # SIGTERM
    try:
        t0 = time.monotonic()
        worker._join_pipeline_threads(timeout=5.0)
        took = time.monotonic() - t0
    finally:
        worker._stop.clear()
    assert not run_check.gate_running(collection) and took < 5.0, f"{took:.1f} s"
    assert _claim_free(lock) and n.let_go == [TWIN] and n.kept == {}


# ---- the second read of 20ebd29e ------------------------------------------------------

def _wait_for(pred, bound: float = 5.0) -> bool:
    t0 = time.monotonic()
    while not pred():
        if time.monotonic() - t0 > bound:
            return False
        time.sleep(0.01)
    return True


def test_no_look_starts_after_its_collections_retirement_until_its_lock_is_taken_again(sym):
    """THE PROBE'S RACE: the settle thread reads the pauses once a beat, and
    the producer asks only the worker's stop — so a caller that read "not
    paused" before the pause looked AFTER the retirement, a look nobody
    retires. The retirement leaves a tombstone: no look, no probe, until the
    worker takes the collection's lock again."""
    n, collection = sym
    n.beats = 0
    worker.retire_collection("gutenberg", timeout=1.0)        # nothing running: the pause
    assert _settle(collection)() is False                       # the beat that read "not paused"
    assert n.probes == [] and n.gates == [] and not run_check.gate_running(collection), \
        f"{len(n.probes)} probe(s), {len(n.gates)} gate(s) after the retirement"
    worker._hold_locks([collection], set())                     # the resume: its lock taken again
    try:
        con = db.connect(collection)
        backend_gate.embed_allowed(con, collection)
        assert _join_gates() and backend_gate.embed_allowed(con, collection) is True
    finally:
        worker._release_locks()


def test_a_retirement_that_outlives_its_bound_is_said(sym, capsys):
    n, collection = sym
    n.beats, n.deaf = 0, True
    n.block[("gutenberg", "document")] = threading.Event()
    _in_thread(_settle(collection), timeout=10)
    assert n.measuring.wait(5)
    capsys.readouterr()
    worker.retire_collection("gutenberg", timeout=0.2)
    said = capsys.readouterr().out
    assert "gutenberg: its run check's look (thread run-check-gutenberg) still runs after the " \
           "retirement's bound of 0.2 s" in said, said


def test_a_retired_looks_next_request_is_held_to_what_is_left_of_the_bound(sym, monkeypatch):
    """The request in flight keeps its own bound; the look's NEXT one is held
    to what is left of the retirement's, not the card's 300 s."""
    n, collection = sym
    asked = []
    monkeypatch.setattr(run_check.embeddings, "embed_calibration",
                        lambda recipe, texts, **kw: asked.append(kw["timeout"]) or [[0.0]] * len(texts))
    recipe = collections_mod.recipe_for(collection)
    look = run_check._Look("place", collection)
    run_check._pass_local.look = look
    try:
        run_check.answers(recipe, how="one")(["a text"])
        look.retire_by = time.monotonic() + 2.0
        look.retired.set()
        run_check.answers(recipe, how="one")(["a text"])
        run_check.answers(recipe, how="contribute")(["a text", "another"])
    finally:
        run_check._pass_local.look = None
    assert asked[0] == 300 and all(t <= 2.0 for t in asked[1:]), asked


def test_the_node_pause_stops_a_look_as_a_collections_pause_does(sym):
    """`node pause` (and seed-only, download only): nothing embeds, so a look
    running stops at its gate's next slice — nothing kept, its twin let go,
    no verdict left — and none starts until the mode embeds again."""
    n, collection = sym
    n.beats = 0
    n.block[("gutenberg", "document")] = threading.Event()
    _in_thread(_settle(collection), timeout=10)
    assert n.measuring.wait(5)
    lock = run_check.twin_lock_path(SERVER, TWIN)
    worker._paused.set()                                        # node pause
    try:
        assert _wait_for(lambda: not run_check.gate_running(collection)), "still measuring, the node paused"
        assert _claim_free(lock) and n.kept == {} and n.let_go == [TWIN]
        assert run_check._where(collection) not in run_check._verdicts, "no verdict left"
        probes = len(n.probes)
        assert _settle(collection)() is False and len(n.probes) == probes, "no look while paused"
    finally:
        worker._paused.clear()
    n.block.clear()
    con = db.connect(collection)
    backend_gate.embed_allowed(con, collection)
    assert _join_gates() and backend_gate.embed_allowed(con, collection) is True


def test_a_manifest_that_cannot_be_set_to_wal_is_not_left_open(sym, monkeypatch):
    n, collection = sym
    db.connect(collection).close()                              # the manifest is there
    opened = []

    class Recorded(db.ManifestConnection):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            opened.append(self)
    monkeypatch.setattr(db, "ManifestConnection", Recorded)

    def wal(con, **k):
        raise db.sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db, "wal", wal)
    with pytest.raises(db.sqlite3.OperationalError):
        run_check._existing_manifest(collection)
    (con,) = opened
    with pytest.raises(db.sqlite3.ProgrammingError):
        con.execute("SELECT 1")                                 # closed


@pytest.mark.parametrize("how", ["pause", "forget", "gone"])
def test_a_retirement_during_the_query_side_writes_and_makes_nothing(sym, how):
    """The document side kept, the QUERY side measuring when the collection
    goes: paused (its manifest there: the look writes nothing to it),
    forgotten (retired, its directory gone), or gone under it without a
    retirement (its manifest is never made again: no `db.connect`)."""
    import shutil
    n, collection = sym
    n.beats, n.deaf = 0, True
    n.block[("gutenberg", "query")] = threading.Event()
    _in_thread(_settle(collection), timeout=10)
    assert _wait_for(lambda: ("gutenberg", "query", TWIN) in n.gates), n.gates
    assert ("gutenberg", "document") in n.kept
    if how in ("pause", "forget"):
        worker.retire_collection("gutenberg", timeout=0.2)    # the slice outlives it
    written = len(_events(collection))
    if how in ("forget", "gone"):
        shutil.rmtree(n.root / "gutenberg")
    n.block[("gutenberg", "query")].set()
    assert _join_gates()
    if how == "pause":
        assert len(_events(collection)) == written, _events(collection)[written:]
    else:
        assert not (n.root / "gutenberg").exists(), "the manifest made again where the collection was"
    assert ("gutenberg", "query") not in n.kept


def test_the_real_validate_hands_the_looks_refusal_to_keep(node, endpoint, monkeypatch):
    """`run_check._validate` itself, not a fake: a look retired meanwhile keeps
    no statement, because the real gate's keep is handed `_keep_refused`."""
    from app.hold import embedder as emb
    root, collection, codex = node
    recipe = collections_mod.recipe_for(collection)
    monkeypatch.setattr(run_check, "stated_run", lambda recipe, **k: emb.StatedRun(
        run_id=RUN, engine={"options": {}}, weights="sha256:" + "3c" * 32, driver="cpu"))

    def measured(root, name, codex, *, role, refuse=None, **kw):
        body = {"run": RUN, "role": role, "passed": 16}
        hold_runs.keep(root, name, {"body": body}, refuse=refuse)
        return body
    monkeypatch.setattr(hold_runs, "validate", measured)
    look = run_check._Look("place", collection)
    look.retired.set()
    run_check._pass_local.look = look
    try:
        with pytest.raises(emb.GateDeferred, match="paused, forgotten or renamed"):
            run_check._validate(root, "gutenberg", codex, recipe, role="document")
    finally:
        run_check._pass_local.look = None
    assert not hold_runs.path_for(root, "gutenberg", RUN, "document").exists(), "nothing kept"


def test_query_mode_s_run_mode_holds_no_look():
    """Download only is query mode's run mode (app/node_mode.py QUERY_RUN):
    a run a question hears is still gated there, so it holds no look. Only a
    paused node does."""
    from app import worker
    worker._paused.clear()
    worker._acquire_only.set()
    try:
        assert not worker._looks_held()
    finally:
        worker._acquire_only.clear()
    worker._paused.set()
    try:
        assert worker._looks_held()
    finally:
        worker._paused.clear()
