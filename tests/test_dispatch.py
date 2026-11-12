"""Model work by priority (app/rage/dispatch.py, docs/notes/scheduling.md §12).

The operator's ladder, lowest first: corpus embedding, translation, chat,
reranking, query embedding. Every request passes its server's gate, which
admits the server's capacity at a time — strictly by level, then fairly by
service time within a level. The node's interactive claim is held while any
interactive call runs; the GPU governor reads it (tests/test_gpu_governor.py).
"""
import threading
import time

import httpx
import pytest

from app import config, worker
from app.rage import dispatch
from app.rage.dispatch import Level


@pytest.fixture(autouse=True)
def fresh_gates():
    dispatch.reset()
    yield
    dispatch.reset()


def _hold(gate, level, plugin, started: threading.Event, release: threading.Event, order: list):
    def run():
        with gate.admit(level, plugin, timeout=10):
            order.append((level, plugin))
            started.set()
            release.wait(10)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _queue(gate, level, plugin, order: list):
    def run():
        with gate.admit(level, plugin, timeout=10):
            order.append((level, plugin))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _waiting(gate, n):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if gate.snapshot()["waiting"] >= n:
            return
        time.sleep(0.01)
    raise AssertionError(f"{n} waiters never queued: {gate.snapshot()}")


def test_the_highest_level_goes_first_whatever_arrived_first():
    g = dispatch.gate("http://enc", 1)
    order, started, release = [], threading.Event(), threading.Event()
    holder = _hold(g, Level.CORPUS, "a", started, release, order)
    started.wait(5)
    waiters = [_queue(g, Level.CORPUS, "a", order)]
    _waiting(g, 1)
    waiters += [_queue(g, Level.TRANSLATE, "web", order)]
    _waiting(g, 2)
    waiters += [_queue(g, Level.QUERY, "web", order)]
    _waiting(g, 3)
    release.set()
    for t in [holder, *waiters]:
        t.join(5)
    assert [lvl for lvl, _ in order] == [Level.CORPUS, Level.QUERY, Level.TRANSLATE, Level.CORPUS]


def test_within_a_level_the_least_served_plugin_goes_first():
    """Fair by SERVICE TIME, not by request count: a plugin whose requests
    are long (gutenberg's 1,500-token chunks) does not take the share of
    one whose requests are short (pubmed's abstracts)."""
    g = dispatch.gate("http://enc", 1)
    with g.admit(Level.CORPUS, "long"):
        time.sleep(0.2)
    with g.admit(Level.CORPUS, "short"):
        pass
    order, started, release = [], threading.Event(), threading.Event()
    holder = _hold(g, Level.CORPUS, "long", started, release, order)
    started.wait(5)
    a = _queue(g, Level.CORPUS, "long", order)
    _waiting(g, 1)
    b = _queue(g, Level.CORPUS, "short", order)
    _waiting(g, 2)
    release.set()
    for t in (holder, a, b):
        t.join(5)
    assert [p for _, p in order] == ["long", "short", "long"]


def test_a_plugin_back_from_idle_banks_nothing():
    """Idle must not earn credit (§10): a plugin that was away starts level
    with the least-served plugin present, not at zero — or it would take the
    gate for as long as the others had been working."""
    g = dispatch.gate("http://enc", 1)
    with g.admit(Level.CORPUS, "busy"):
        time.sleep(0.2)
    order, started, release = [], threading.Event(), threading.Event()
    holder = _hold(g, Level.CORPUS, "busy", started, release, order)
    started.wait(5)
    back = _queue(g, Level.CORPUS, "back", order)
    _waiting(g, 1)
    assert g._served["back"] >= g._served["busy"] - 1e-9
    release.set()
    holder.join(5)
    back.join(5)


def test_an_unbounded_gate_orders_nothing_and_waits_for_nothing():
    g = dispatch.gate("http://chat", None)
    inside = threading.Event()
    with g.admit(Level.TRANSLATE, "web"):
        t = threading.Thread(target=lambda: g.admit(Level.QUERY, "web").__enter__() or inside.set())
        t.start()
        assert inside.wait(2), "an unbounded gate admitted at once"


def test_corpus_stands_aside_on_the_whole_card_while_a_person_s_query_runs(monkeypatch):
    """Operator, 2026-10-25: "Embedding shall have the lower priority and
    query processing the highest". Corpus work's next request, on ANY model,
    waits while the node's claim is held — then goes at once. (It did not
    from 2026-09-20: alone on the card the Arc dropped its clock and slowed
    chat; the GPU governor now holds the clock for the claim's lifetime.)"""
    claim_until = time.monotonic() + 0.3
    monkeypatch.setattr(dispatch, "interactive_active", lambda: time.monotonic() < claim_until)
    g = dispatch.gate("http://gpu-enc", 1)
    t0 = time.monotonic()
    with g.admit(Level.CORPUS, "normattiva", timeout=5):
        waited = time.monotonic() - t0
    assert 0.25 <= waited < 2, "it waited for the query, and not longer"
    for level in (Level.TRANSLATE, Level.QUERY, Level.RERANK, Level.CHAT):
        claim_until = time.monotonic() + 5
        t0 = time.monotonic()
        with g.admit(level, "web", timeout=1):
            assert time.monotonic() - t0 < 0.2, f"a query's {level.name.lower()} step never stands aside"


def test_standing_aside_is_bounded_and_a_deferral_and_wakes_on_stop(monkeypatch):
    monkeypatch.setattr(dispatch, "interactive_active", lambda: True)
    g = dispatch.gate("http://gpu-enc", 1)
    with pytest.raises(dispatch.GateTimeout, match="stood aside for a person's query") as err:
        with g.admit(Level.CORPUS, "normattiva", timeout=0.2):
            pass
    assert worker.transient_failure(err.value), "a deferral: nothing is written on the document"
    got = []

    def corpus():
        try:
            with g.admit(Level.CORPUS, "normattiva", timeout=60):
                got.append("admitted")
        except dispatch.GateTimeout:
            got.append("deferred")
    t = threading.Thread(target=corpus, daemon=True)
    t.start()
    time.sleep(0.2)
    dispatch.stop_all()
    t.join(2)
    assert got == ["deferred"], "SIGTERM wakes a corpus request standing aside"


def test_a_stand_aside_is_counted_only_when_it_waited(monkeypatch):
    """München, 2026-11-07: no line said whether wikidata's embedding stood
    aside at all while a person searched — the wait happens before the gate,
    so the gate's own counters never saw it. Now each collection's waits,
    their seconds and the deferrals among them are counted."""
    claim_until = 0.0
    monkeypatch.setattr(dispatch, "interactive_active", lambda: time.monotonic() < claim_until)
    g = dispatch.gate("http://gpu-enc", 1)
    with g.admit(Level.CORPUS, "wikidata", timeout=5):
        pass
    assert dispatch.stood_aside() == {}, "no claim, no wait: nothing is counted"

    claim_until = time.monotonic() + 0.3
    with g.admit(Level.CORPUS, "wikidata", timeout=5):
        pass
    got = dispatch.stood_aside()["wikidata"]
    assert got["waits"] == 1 and got["deferred"] == 0
    assert 0.25 <= got["seconds"] < 2, got

    claim_until = time.monotonic() + 60
    with pytest.raises(dispatch.GateTimeout):
        with g.admit(Level.CORPUS, "wikidata", timeout=0.2):
            pass
    got = dispatch.stood_aside()["wikidata"]
    assert got["waits"] == 2 and got["deferred"] == 1, "the deadline's deferral is counted"
    with g.admit(Level.RERANK, "", timeout=1):
        pass
    assert set(dispatch.stood_aside()) == {"wikidata"}, "a query's step never stands aside"

    dispatch.reset()
    assert dispatch.stood_aside() == {}


def test_the_warm_s_stand_aside_is_counted_for_its_collection(monkeypatch):
    claim_until = time.monotonic() + 0.2
    monkeypatch.setattr(dispatch, "interactive_active", lambda: time.monotonic() < claim_until)
    with dispatch.working_as(Level.CORPUS, "normattiva"):
        dispatch.stand_aside("http://gpu-enc", 5)
    assert dispatch.stood_aside()["normattiva"]["waits"] == 1


def test_the_stats_beat_publishes_the_stand_aside_naming_each_collection(
        isolated_config, monkeypatch):
    """Published by the worker's beat (`_log_shares`), not by the thread that
    waited: one cumulative line naming every collection that stood aside."""
    from app import db
    claim_until = time.monotonic() + 0.15
    monkeypatch.setattr(dispatch, "interactive_active", lambda: time.monotonic() < claim_until)
    with dispatch.gate("http://gpu-enc", 1).admit(Level.CORPUS, "wikidata", timeout=5):
        pass
    con = db.connect()
    worker._log_shares(con)
    lines = [r[0] for r in con.execute(
        "SELECT message FROM events WHERE stage='sched' AND message LIKE 'stood aside%'")]
    assert len(lines) == 1, lines
    assert "wikidata: 1 requests" in lines[0] and "deferred" not in lines[0], lines[0]


def test_a_query_s_later_step_goes_first_at_a_shared_gate():
    """"The subsequent steps of a query in process shall be processed first
    to avoid wasting the effort spent for the previous steps": its answer,
    then its rerank, then its embedding, then its translation, then corpus."""
    g = dispatch.gate("http://shared", 1)
    started, release, order = threading.Event(), threading.Event(), []
    holder = _hold(g, Level.CORPUS, "a", started, release, order)
    started.wait(5)
    waiters = []
    for level in (Level.CORPUS, Level.TRANSLATE, Level.QUERY, Level.RERANK, Level.CHAT):
        waiters.append(_queue(g, level, "web" if level else "a", order))
        _waiting(g, len(waiters))
    release.set()
    for t in [holder, *waiters]:
        t.join(5)
    assert [lvl for lvl, _ in order] == [Level.CORPUS, Level.CHAT, Level.RERANK, Level.QUERY,
                                         Level.TRANSLATE, Level.CORPUS]


def _held(g):
    """A request holding the gate's one slot until released."""
    started, release = threading.Event(), threading.Event()
    _hold(g, Level.QUERY, "web", started, release, [])
    started.wait(5)
    return release


def test_a_wait_is_bounded_and_is_a_deferral_not_an_error():
    """CLAUDE.md: a pipeline thread never waits without a bound. The bound
    is the request's own timeout, and running out of it is the endpoint
    being busy — worker.transient_failure says so, so no error is ever
    written onto the document."""
    g = dispatch.gate("http://gpu-enc", 1)
    release = _held(g)
    try:
        with pytest.raises(dispatch.GateTimeout) as err:
            with g.admit(Level.CORPUS, "gutenberg", timeout=0.2):
                pass
    finally:
        release.set()
    assert isinstance(err.value, httpx.ConnectTimeout)
    assert worker.transient_failure(err.value)
    assert g.snapshot()["waiting"] == 0, "a waiter that gave up leaves the queue"


def test_stopping_wakes_every_waiter():
    """SIGTERM: a request not yet admitted is not a unit in flight."""
    g = dispatch.gate("http://gpu-enc", 1)
    release = _held(g)
    got = []

    def corpus():
        try:
            with g.admit(Level.CORPUS, "gutenberg", timeout=60):
                got.append("admitted")
        except dispatch.GateTimeout:
            got.append("deferred")
    t = threading.Thread(target=corpus, daemon=True)
    t.start()
    _waiting(g, 1)
    dispatch.stop_all()
    t.join(2)
    release.set()
    assert got == ["deferred"]


def test_a_level_crosses_into_embed_texts_pool_threads(monkeypatch):
    """A pool thread starts with an EMPTY context. Without handing the
    caller's over, a corpus request made from embed_texts' sub-batch pool
    would reach the gate as QUERY — ahead of every person's request."""
    from app import collections as collections_mod, embeddings
    seen = []
    lock = threading.Lock()

    def fake_batch(batch, timeout, recipe):
        with lock:
            seen.append(dispatch.current())
        return [[0.0] for _ in batch]
    monkeypatch.setattr(embeddings, "_embed_batch", fake_batch)
    monkeypatch.setattr(config, "EMBED_BATCH", 1)
    monkeypatch.setattr(config, "EMBED_CONCURRENCY", 3)
    recipe = collections_mod.EmbedRecipe(url="http://enc", api="openai", model="m",
                                         dtype="f16", ctx=512)
    with dispatch.working_as(Level.CORPUS, "gutenberg"):
        embeddings._embed_texts(["a", "b", "c", "d"], recipe=recipe)
    assert seen and set(seen) == {(Level.CORPUS, "gutenberg")}


def test_every_embedding_request_passes_its_servers_gate(monkeypatch):
    from app import collections as collections_mod, embeddings
    calls = []

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": [1.0]}]}

    def fake_post(url, json, timeout, headers=None):
        calls.append(dispatch.gate("http://enc").snapshot()["busy"])
        return Resp()
    monkeypatch.setattr(embeddings.httpc, "post", fake_post)
    recipe = collections_mod.EmbedRecipe(url="http://enc", api="openai", model="m",
                                         dtype="f16", ctx=512)
    embeddings._request_vectors(["x"], 5, recipe)
    assert calls == [1], "the request was made INSIDE the gate"


def test_the_claim_is_held_across_nested_calls_and_cleared_by_the_last(
        tmp_path, isolated_config):
    """Cleared when the last interactive call ends, not a TTL later — a TTL
    later would idle the encoders for nothing after every search."""
    from app import node_state
    node_state.connect().close()
    with dispatch.interactive(Level.CHAT):
        dispatch._claim_seen.clear()
        assert dispatch.interactive_active()
        with dispatch.interactive(Level.TRANSLATE):
            pass
        dispatch._claim_seen.clear()
        assert dispatch.interactive_active(), "the outer call is still running"
    dispatch._claim_seen.clear()
    assert not dispatch.interactive_active()


def test_a_claim_never_creates_the_node_store(tmp_path, isolated_config):
    """The worker makes the store at boot. A process without one has no
    worker to stand aside, and a claim that made one would write a node's
    live state (conftest's guard exists because two tests once did)."""
    from app import node_state
    assert not node_state.path().exists()
    with dispatch.interactive(Level.QUERY):
        pass
    assert not node_state.path().exists()
    assert dispatch.interactive_active() is False


def test_interactive_endpoints_are_bounded_only_by_configuration(monkeypatch):
    monkeypatch.setattr(config, "GATE_CAPACITY", "http://chat:11434=1, http://mt=2")
    assert dispatch.configured_capacity("http://chat:11434/") == 1
    assert dispatch.configured_capacity("http://mt") == 2
    assert dispatch.configured_capacity("http://rerank") is None


def test_a_streamed_answer_ends_cleanly_though_each_step_runs_in_another_context(
        isolated_config):
    """The Ask page's streamed answer ended with "[error: <Token … was created
    in a different Context]" (operator, 2026-09-19). `llm.chat_stream` holds
    `dispatch.call` across its `yield`s, and Starlette steps a generator in
    its threadpool with a FRESH COPY of the request's context for every
    step: the level was set in the first copy and reset in the last."""
    import contextvars

    from app import node_state
    node_state.connect().close()
    seen = []

    def stream():
        with dispatch.call("http://chat", Level.CHAT, timeout=1):
            seen.append(dispatch.current()[0])      # the request is made here, at CHAT
            yield "one"
            yield "two"

    g = stream()
    step = lambda: contextvars.copy_context().run(next, g)   # noqa: E731 — Starlette's way
    assert step() == "one" and step() == "two"
    assert dispatch.interactive_active()
    with pytest.raises(StopIteration):
        step()                                      # the block is left HERE, in another copy
    assert seen == [Level.CHAT]
    dispatch._claim_seen.clear()
    assert not dispatch.interactive_active(), "the claim was released"
    assert dispatch.gate("http://chat").snapshot()["busy"] == 0, "and the gate"


def test_a_block_left_in_its_own_context_still_restores_the_level():
    with dispatch.working_as(Level.CORPUS, "gutenberg"):
        with dispatch.working_as(Level.QUERY):
            assert dispatch.current() == (Level.QUERY, "")
        assert dispatch.current() == (Level.CORPUS, "gutenberg")


# ---- the capacity, independent of who called last (2026-11-01) -------------
# gpu-saturation-plan.md's survey: `gate()` set the capacity to whatever the
# latest caller passed, so wikidata (4) and wikipedia (2) on one URL flipped
# it by arrival order.

def _running(gate, plugin, n, release):
    started = []
    threads = []
    for _ in range(n):
        ev = threading.Event()

        def run(ev=ev):
            with gate.admit(Level.CORPUS, plugin, timeout=10):
                ev.set()
                release.wait(10)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        threads.append(t)
        started.append(ev)
    return started, threads


def test_the_limit_is_the_largest_declared_by_the_collections_present_not_the_latest():
    g = dispatch.gate("http://enc")
    with dispatch.working_as(Level.CORPUS, "wikidata"):
        dispatch.gate("http://enc", 4)
    with dispatch.working_as(Level.CORPUS, "wikipedia"):
        dispatch.gate("http://enc", 2)              # the latest caller
    release = threading.Event()
    started, threads = _running(g, "wikipedia", 4, release)
    _waiting(g, 2)
    assert g.snapshot()["busy"] == 2 and g.snapshot()["capacity"] == 2, \
        "wikipedia alone: its own 2, whatever wikidata declared before"
    more, more_threads = _running(g, "wikidata", 1, release)
    deadline = time.monotonic() + 5
    while g.snapshot()["busy"] < 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert g.snapshot()["busy"] == 4 and g.snapshot()["capacity"] == 4, \
        "both present: the larger, 4, in any order of arrival"
    release.set()
    for t in threads + more_threads:
        t.join(5)
    assert g.snapshot()["capacity"] is None, "nobody present: the fallback, none given"


def test_a_collection_that_changes_its_concurrency_is_taken_at_its_word():
    g = dispatch.gate("http://enc", 1)
    with dispatch.working_as(Level.CORPUS, "wikidata"):
        dispatch.gate("http://enc", 4)
        dispatch.gate("http://enc", 6)
    assert g.snapshot()["declared"]["wikidata"] == 6


def test_unnamed_callers_keep_the_largest_they_declared():
    """A person's queries in the web carry no collection name: the latest
    of theirs would flip it by which collection was asked last."""
    g = dispatch.gate("http://q", 2)
    dispatch.gate("http://q", 4)
    dispatch.gate("http://q", 1)
    assert g.snapshot()["declared"][""] == 4


def test_a_question_holds_the_claim_between_its_calls_and_lingers_after(tmp_path, isolated_config, monkeypatch):
    """Operator, 2026-11-09, B (München's trace the same day: between the
    steps of one question the claim fell, the corpus came back, and the
    reclaimer took the question's own chat and query encoder). The claim
    holds from the question's start to QUESTION_LINGER_S after its end,
    with no call running in between; a corpus request that stands aside
    runs the hooks once."""
    from app import node_state
    node_state.connect().close()
    hooks = []
    monkeypatch.setattr(dispatch, "_aside_hooks", [])
    dispatch.on_stand_aside(lambda: hooks.append(1))
    monkeypatch.setattr(dispatch, "QUESTION_LINGER_S", 0.3)
    with dispatch.question():
        with dispatch.interactive(Level.QUERY):
            pass
        dispatch._claim_seen.clear()
        assert dispatch.interactive_active(), "between two steps: still the question's"
    dispatch._claim_seen.clear()
    assert dispatch.interactive_active(), "lingers for the answer the page asks next"
    import threading as _t
    ended = _t.Event()
    _t.Thread(target=lambda: (dispatch._stand_aside("enc", None, None, "wikidata"), ended.set()),
              daemon=True).start()
    assert ended.wait(5), "the linger ends by itself"
    assert hooks == [1], "the hooks ran once, as the wait began"
    dispatch._claim_seen.clear()
    assert not dispatch.interactive_active()


def test_a_call_that_ends_after_a_question_never_shortens_its_linger(tmp_path, isolated_config, monkeypatch):
    """docs/notes/ask-card-workflow.md spec 6: corpus work resumes five
    minutes after the question's last step. The models warmed behind a
    search (app/question_card.py `after_search`) are interactive calls that
    end after the question: ending, each wrote the claim to 0 and the five
    minutes were gone. A linger already asked for stands."""
    from app import node_state
    node_state.connect().close()
    monkeypatch.setattr(dispatch, "_board", dispatch._Board())
    monkeypatch.setattr(dispatch, "QUESTION_LINGER_S", 300.0)
    import threading as _t
    inside, release = _t.Event(), _t.Event()

    def warm_behind():
        with dispatch.interactive(Level.CHAT):
            inside.set()
            release.wait(5)
    with dispatch.question():
        t = _t.Thread(target=warm_behind, daemon=True)
        t.start()
        assert inside.wait(5)
    release.set()
    t.join(5)
    dispatch._claim_seen.clear()
    assert dispatch.interactive_active(), "the question's five minutes outlive the call behind it"
    # a call with no question pending still lets the claim go at once
    monkeypatch.setattr(dispatch, "_board", dispatch._Board())
    with dispatch.interactive(Level.QUERY):
        pass
    dispatch._claim_seen.clear()
    assert not dispatch.interactive_active()


def test_a_harness_holds_no_claim_and_keeps_its_level(tmp_path, isolated_config):
    """A pilot is not a person (München, 2026-11-12): tools/legal_pilot.py's
    searches, back to back over a jf100 pool, each took the node's claim and
    kept normattiva-nemo's and legi's embedding aside for the whole run. In a
    harness process neither a question nor an interactive call writes the
    claim; each call still declares its level for the servers' gates."""
    from app import node_state
    node_state.connect().close()
    dispatch.as_harness()
    with dispatch.question():
        with dispatch.interactive(Level.RERANK):
            assert dispatch.current()[0] == Level.RERANK, "the level, for the gates' order"
            dispatch._claim_seen.clear()
            assert not dispatch.interactive_active(), "no claim: the corpus goes on"
    dispatch._claim_seen.clear()
    assert not dispatch.interactive_active(), "and no linger after the question"
    dispatch.as_harness(False)                      # `--as-person`: as a person's question
    with dispatch.interactive(Level.QUERY):
        dispatch._claim_seen.clear()
        assert dispatch.interactive_active()


def test_the_harnesses_hold_no_claim_unless_asked_to(monkeypatch):
    """tools/legal_pilot.py, chat_eval, rerank_bench and encoder_bakeoff mark
    themselves harnesses; `--as-person` keeps a person's claim for timings."""
    import importlib
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for rel, argv in (("tools/legal_pilot.py", ["fetch", "--out", "/nonexistent/x"]),
                      ("tools/measure/rerank_bench.py", ["capture", "gutenberg", "/nonexistent",
                                                         "--out", "/nonexistent/x"])):
        path = root / rel
        spec = importlib.util.spec_from_file_location(f"_harness_{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        for as_person in (False, True):
            dispatch.reset()
            seen = {}
            # stop right after the mark: the command itself is not under test
            def stop(*a, **k):
                seen["harness"] = dispatch.is_harness()
                raise SystemExit(0)
            for name in dir(mod):
                if name.startswith("cmd_") or name in ("fetch", "capture"):
                    monkeypatch.setattr(mod, name, stop, raising=False)
            try:
                mod.main((["--as-person"] if as_person else []) + argv)
            except (SystemExit, Exception):
                pass
            assert dispatch.is_harness() is (not as_person), (rel, as_person)
    dispatch.reset()


def test_every_search_harness_marks_itself():
    """chat_eval and encoder_bakeoff need a node to run; their mark is held
    by its line, right after the arguments are read."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for rel in ("tools/legal_pilot.py", "tools/measure/chat_eval.py",
                "tools/measure/rerank_bench.py", "tools/encoder_bakeoff.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert '_dispatch.as_harness(not getattr(a, "as_person", False))' in src, rel
        assert '"--as-person"' in src, rel
