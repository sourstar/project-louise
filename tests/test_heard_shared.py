"""WHO HEARS A RUN (app/run_check.py; München, 2026-11-26).

bge-m3 was restarted with another env — its three parking lines, so another
run. One question was answered from it; four minutes later the worker had
said nothing about wikipedia or a run. `run_check._answering` was each
process's own: the WEB had heard the new run, the WORKER — which gates —
hears a run only from its own embed stage, and wikipedia had nothing to
embed. "Gated at once, whether or not it has anything to embed" (03 §3.3)
held only for a run the worker itself heard; and the keeper already counted
the model as one it may park.

  - what any process hears is written where every process reads it, and the
    worker's own round gates a run a question heard first;
  - the same for an asymmetric space's QUERY encoder, whose run only a
    question ever hears;
  - the keeper PARKS an encoder only as a run admitted to park — its cycles
    counted for every collection that uses it — and stops it otherwise.
"""
from __future__ import annotations

import fcntl
import json
import time

import pytest

from app import collections as collections_mod, copy_state, db, model_evict, run_check, worker
from app.hold import runs as hold_runs
from app.verify import backend_gate
from tests.test_model_load import REAL_FETCH  # taken before conftest's stand-in replaces it
from tests.test_run_check import (DOC_URL, ENDPOINT, QUERY_URL, R1, R2, endpoint, node,  # noqa: F401
                                  statements_kept)


def _as_another_process(fn):
    """Run `fn` as a process that is not this one would: what it hears goes
    to the shared record, and this process's own memory is as it was."""
    mine = dict(run_check._answering), dict(run_check._answering_at)
    try:
        return fn()
    finally:
        run_check._answering.clear()
        run_check._answering.update(mine[0])
        run_check._answering_at.clear()
        run_check._answering_at.update(mine[1])


def _recipe(collection):
    return collections_mod.recipe_for(collection)


def test_a_run_a_question_heard_first_is_gated_by_the_worker_with_nothing_to_embed(node, endpoint, monkeypatch):
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True        # the worker's start: R1 gated
    assert [b["run"] for b in statements_kept(root)] == [R1]
    gate_cost = endpoint.texts
    # THE MODEL IS STARTED AGAIN WITH ANOTHER ENV: another run answers — and the
    # first to hear it is a question, in the web
    # (`check_query` hears it for the side that embeds a question: the document
    # encoder itself in a symmetric space as wikipedia's — this fixture's space
    # has a query encoder, which the next test is about)
    endpoint.run = R2
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}))
    assert run_check._answering[((DOC_URL, "medcpt-article"), "document")] == R1, "this process heard nothing new"
    said = json.loads(run_check.heard_path().read_text())
    assert said["kind"] == "louise-heard"
    assert said["heard"][f"{DOC_URL}\nmedcpt-article\ndocument"]["run"] == R2

    # AS IT WAS: the worker reads its own record, finds R1's statement, and looks no further
    with monkeypatch.context() as m:
        m.setattr(run_check, "_shared", lambda: {})
        assert copy_state.of(collection).test("gate").status == copy_state.PASSED
        worker._settle_the_run(con, collection)
        assert endpoint.texts == gate_cost and [b["run"] for b in statements_kept(root)] == [R1]

    # NOW: its next round, with nothing to embed (a copy's state is kept
    # `copy_state.TTL_S` seconds: the round after that many, at the latest)
    assert copy_state.TTL_S <= 60
    copy_state._states.clear()
    assert run_check.answering(collection)["document"] == R2, "told by the other process, and later than its own"
    gate = copy_state.of(collection).test("gate")
    assert gate.status == copy_state.OPEN and "which this peer has not tested" in gate.detail
    worker._settle_the_run(con, collection)
    assert sorted(b["run"] for b in statements_kept(root)) == sorted([R1, R2]), "gated at once, by itself"
    assert endpoint.texts >= gate_cost + 512
    copy_state._states.clear()
    assert copy_state.of(collection).test("gate").status == copy_state.PASSED
    events = [r[0] for r in con.execute("SELECT message FROM events ORDER BY rowid")]
    assert any(f"run {R2[:23]}… is new to this peer; running the gate against it" in e for e in events), events[-4:]
    # and the round after costs nothing: the verdict is about the run that answers
    cost = endpoint.texts
    worker._settle_the_run(con, collection)
    assert backend_gate.embed_allowed(con, collection) is True and endpoint.texts == cost


def test_a_query_run_only_a_question_hears_is_gated_too(node, endpoint):
    """An asymmetric space's query encoder embeds no document: the worker
    asks it only when it looks, and a verdict was held to the DOCUMENT run
    alone — a new query run would have waited the half hour out."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    assert [b["run"] for b in statements_kept(root, "query")] == [R1]
    cost = endpoint.texts
    endpoint.by_url[QUERY_URL] = R2
    time.sleep(0.01)
    _as_another_process(lambda: run_check.check_query(collection, _recipe(collection), {R2}))   # a question, in the web
    assert run_check._answering[((QUERY_URL, "medcpt-query"), "query")] == R1, "this process heard nothing new"
    copy_state._states.clear()
    assert copy_state.of(collection).test("gate").status == copy_state.OPEN
    worker._settle_the_run(con, collection)
    assert sorted(b["run"] for b in statements_kept(root, "query")) == sorted([R1, R2])
    assert [b["run"] for b in statements_kept(root)] == [R1], "the document side was not gated again"
    assert endpoint.texts > cost
    cost = endpoint.texts
    worker._settle_the_run(con, collection)
    assert backend_gate.embed_allowed(con, collection) is True and endpoint.texts == cost


def test_news_is_told_once_and_the_later_hearing_wins(node, endpoint):
    root, collection, codex = node
    recipe = _recipe(collection)
    key = f"{DOC_URL}\nmedcpt-article\ndocument"
    path = run_check.heard_path()
    run_check.heard(recipe, {R1})
    first = path.stat().st_mtime_ns, path.read_bytes()
    time.sleep(0.01)
    for _ in range(5):
        run_check.heard(recipe, {R1})                    # every answer of every batch: no write
    assert (path.stat().st_mtime_ns, path.read_bytes()) == first
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    # another process tells R2, later: taken
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(recipe, {R2}))
    assert run_check.answering(collection)["document"] == R2
    # this process then hears R1 from the endpoint itself: its own, later, wins — and is told
    time.sleep(0.01)
    run_check.heard(recipe, {R1})
    assert run_check.answering(collection)["document"] == R1
    assert json.loads(path.read_text())["heard"][key]["run"] == R1
    # THE ROLLBACK: this process last heard R1; another told R2; the model is put
    # back and this process hears R1 AGAIN — nothing changed in its own memory,
    # and the record must still say R1, or the others keep R2 for ever
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(recipe, {R2}))
    assert json.loads(path.read_text())["heard"][key]["run"] == R2
    time.sleep(0.01)
    run_check.heard(recipe, {R1})
    assert json.loads(path.read_text())["heard"][key]["run"] == R1
    assert run_check.answering(collection)["document"] == R1


def test_a_record_that_cannot_be_written_costs_a_search_nothing(node, endpoint, monkeypatch, tmp_path):
    root, collection, codex = node
    recipe = _recipe(collection)
    # a lock another process holds: left untold within the bound, never waited for
    path = run_check.heard_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run_check, "HEARD_WAIT_S", 0.1)
    with open(path.with_name(path.name + ".lock"), "a+b") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        began = time.monotonic()
        run_check.heard(recipe, {R1})
        assert time.monotonic() - began < 2 and not path.exists()
    assert run_check.answering(collection)["document"] == R1, "this process knows what it heard all the same"
    run_check.heard(recipe, {R1})
    assert path.exists(), "untold news is told by the next answer"
    # a state folder that cannot be made, a record that is not one
    monkeypatch.setattr(run_check, "heard_path", lambda: tmp_path / "a-file" / "state" / "heard.json")
    (tmp_path / "a-file").write_text("not a folder")
    run_check.heard(recipe, {R2})
    assert run_check.answering(collection)["document"] == R2
    monkeypatch.setattr(run_check, "heard_path", lambda: tmp_path / "heard.json")
    (tmp_path / "heard.json").write_text('{"kind": "something else", "heard": {"x": 1}}')
    run_check._heard_read.clear()
    assert run_check._shared() == {} and run_check.answering(collection)["document"] == R2


# ---- a park is for a run admitted to park --------------------------------------------------

def _counted(root, collection, codex, run_id=R1, role="document", **more):
    body = next(b for b in statements_kept(root, role) if b["run"] == run_id)
    hold_runs.record_parks(root, collection.name, role, run_id, cycles=64, closest=1.0, park_s=0.2,
                           return_s=0.3, at="2026-11-26T08:20:00Z", statement=hold_runs.statement_digest(body),
                           **more)


@pytest.fixture
def pids(monkeypatch):
    """The backend processes the runner's listing names — (pid, started_at)
    — as model_load last read them, and since when this process knows each
    (long enough, unless a test says otherwise)."""
    from app import model_load
    now = {"medcpt-article": (101, "2026-11-26T08:00:00Z"), "medcpt-query": (102, "2026-11-26T08:00:00Z")}
    seen = {}

    def known(server, model):
        proc = now.get(model)
        return None if proc is None else (proc, seen.get(model, time.monotonic() - 2 * run_check.BIND_AFTER_S))
    monkeypatch.setattr(model_load, "known_process", known)
    return now, seen


def test_an_encoder_may_be_parked_only_as_a_run_whose_cycles_are_counted(node, endpoint, pids, monkeypatch):
    """The operator, 2026-11-26: "Either a model is validated or it does not
    embed." A write was held to the counted cycles; a park was not."""
    root, collection, codex = node
    con = db.connect(collection)
    assert run_check.park_admitted(ENDPOINT, "louise-chat", (7, "x")) is None, "no collection's encoder: LocalAI's word stands"
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is False, "nobody has heard its run yet"
    assert backend_gate.embed_allowed(con, collection) is True
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is False, "gated, its cycles never counted"
    _counted(root, collection, codex)
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is True, "asked afresh: no cache to outlive"
    assert run_check.park_admitted(ENDPOINT, "medcpt-query", (102, "2026-11-26T08:00:00Z")) is False, "the query model has cycles of its own"
    _counted(root, collection, codex, role="query")
    assert run_check.park_admitted(ENDPOINT, "medcpt-query", (102, "2026-11-26T08:00:00Z")) is True
    # ANOTHER RUN ANSWERS (the model started again): not admitted until ITS cycles are counted — at once
    time.sleep(0.01)
    run_check.heard(_recipe(collection), {R2})
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is False
    # what cannot be read is a stop, never a park
    monkeypatch.setattr(collections_mod, "load_all", lambda **k: (_ for _ in ()).throw(OSError("unreadable")))
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is False


def test_a_run_admitted_for_one_process_is_not_for_the_next(node, endpoint, pids):
    """THE REVIEWS: a model NAME stood for a process, then a PID did. Admitted
    R1; the model started again — a new env, a new run R2 — and nothing of
    Louise asked it yet (the keeper's warm reads no run). A heard run is
    bound to the process it was heard from: its pid AND its start, since
    LocalAI's container counts pids again from small numbers at each restart."""
    now, seen = pids
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    _counted(root, collection, codex)
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is True
    said = json.loads(run_check.heard_path().read_text())["heard"][f"{DOC_URL}\nmedcpt-article\ndocument"]
    assert said["run"] == R1 and said["proc"] == [101, "2026-11-26T08:00:00Z"]
    # another process under the same name: stopped, not parked
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (202, "2026-11-26T09:00:00Z")) is False
    # THE SAME PID after LocalAI's container restarted — another start: not admitted either
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T09:00:20Z")) is False
    # the new process is heard (a restart with the same env: R1 again), once this process has known it
    # long enough for no answer of the old one to be in flight: bound to it, admitted again
    now["medcpt-article"] = (101, "2026-11-26T09:00:20Z")
    run_check.heard(_recipe(collection), {R1})
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T09:00:20Z")) is True
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", None) is False, "no process named: no park"


def test_a_run_is_bound_only_where_the_answer_can_only_be_that_processs(node, endpoint, pids):
    """THE SECOND READ, run: a slice answered by two runs across a restart
    bound the newer process to whichever run sorted last; an old process's
    answer read after the listing had moved on bound the old run to the new
    process. Neither binds now: one run in the answer, from a process this
    process has known longer than any request lasts."""
    now, seen = pids
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    endpoint.run = R2
    run_check._verdicts.clear()
    assert backend_gate.embed_allowed(con, collection) is True        # R2 gated too
    for run in (R1, R2):
        _counted(root, collection, codex, run_id=run)
    key = f"{DOC_URL}\nmedcpt-article\ndocument"
    # a NEW process, seen a moment ago: whatever it is heard answering is not bound to it
    now["medcpt-article"] = (202, "2026-11-26T09:30:00Z")
    seen["medcpt-article"] = time.monotonic()
    time.sleep(0.01)
    run_check.heard(_recipe(collection), {R1})
    assert json.loads(run_check.heard_path().read_text())["heard"][key]["proc"] is None
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (202, "2026-11-26T09:30:00Z")) is False
    # known long enough — but the answer named TWO runs: bound to neither
    seen.pop("medcpt-article")
    time.sleep(0.01)
    run_check.heard(_recipe(collection), {R1, R2})
    assert json.loads(run_check.heard_path().read_text())["heard"][key]["proc"] is None
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (202, "2026-11-26T09:30:00Z")) is False
    # one run, a process known long enough: bound, admitted
    time.sleep(0.01)
    run_check.heard(_recipe(collection), {R2})
    assert json.loads(run_check.heard_path().read_text())["heard"][key]["proc"] == [202, "2026-11-26T09:30:00Z"]
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (202, "2026-11-26T09:30:00Z")) is True


def test_a_collection_file_that_cannot_be_read_makes_no_model_parkable(node, endpoint, pids, monkeypatch):
    """THE SECOND READ: with an unreadable collection file skipped, its
    encoder was taken for "no collection's encoder" and parked."""
    root, collection, codex = node
    real = collections_mod.load_all
    monkeypatch.setattr(collections_mod, "load_all", lambda **k: ([], [("gutenberg", "a typo")]))
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is False
    assert run_check.park_admitted(ENDPOINT, "louise-chat", (7, "x")) is False, "even what may be no encoder: a stop"
    # a READABLE collection that ADMITS it, and another file not read: still a stop (it may be stricter)
    monkeypatch.setattr(collections_mod, "load_all", lambda **k: (real(**k)[0], [("another", "a typo")]))
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    _counted(root, collection, codex)
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is False
    monkeypatch.setattr(collections_mod, "load_all", lambda **k: (real(**k)[0], []))
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is True, \
        "the same, every file read: admitted"
    monkeypatch.setattr(collections_mod, "load_all", lambda **k: ([], []))
    assert run_check.park_admitted(ENDPOINT, "louise-chat", (7, "x")) is None


def test_a_query_look_that_failed_is_looked_at_again_after_its_bound_not_at_every_step(node, endpoint, monkeypatch):
    """THE SECOND READ: the verdict recorded the query run the WEB heard after
    the worker's look had failed. THE THIRD: storing "none" instead made every
    document step look again — twenty looks in ten rounds, a twin load and
    stop on each, for a query model LocalAI held in its cooldown. A failed
    look is looked at again after FAIL_TTL_S, as a paused verdict is."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    endpoint.by_url[QUERY_URL] = R2
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}, role="query"))
    real = run_check.answering_run
    looks = []

    def failing(recipe, query=False, **k):
        if query:
            looks.append(1)
            raise OSError("the query model did not answer")
        return real(recipe, query=query, **k)
    monkeypatch.setattr(run_check, "answering_run", failing)
    run_check._verdicts.clear()
    assert backend_gate.embed_allowed(con, collection) is True
    kept = run_check._verdicts[run_check._where(collection)][5]
    assert kept[0] == run_check.QUERY_UNSETTLED and looks == [1]
    for _ in range(10):                                   # ten idle rounds, ten document steps
        worker._settle_the_run(con, collection)
        assert backend_gate.embed_allowed(con, collection) is True
    assert looks == [1], "not looked at again within its bound"
    assert not run_check.verdict_outdated(collection)
    # ANOTHER QUERY RUN heard meanwhile (the model came back with another env):
    # looked at at once, not after the bound (the fourth read)
    w = run_check._where(collection)
    assert run_check._verdicts[w][5][2] == R2
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R1}, role="query"))
    assert run_check.verdict_outdated(collection), "a run heard since the failure is looked at now"
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}, role="query"))
    # the bound passed: looked at again — and this time it answers
    v = run_check._verdicts[w]
    run_check._verdicts[w] = (*v[:5], (run_check.QUERY_UNSETTLED, v[5][1] - run_check.FAIL_TTL_S - 1, v[5][2]))
    assert run_check.verdict_outdated(collection)
    monkeypatch.setattr(run_check, "answering_run", real)
    worker._settle_the_run(con, collection)
    assert run_check._verdicts[w][5] == R2 and not run_check.verdict_outdated(collection)


def test_a_look_that_failed_slowly_is_marked_when_it_ended(node, endpoint, monkeypatch):
    """THE FOURTH READ: the mark was stamped before the look, and a probe that
    took its whole timeout (300 s, as FAIL_TTL_S) was stored expired — every
    step looked again."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    real, clock = run_check.answering_run, [1_000_000.0]
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])

    def slow(recipe, query=False, **k):
        if query:
            clock[0] += run_check.FAIL_TTL_S + 1          # the probe ran out its timeout
            raise OSError("timed out")
        return real(recipe, query=query, **k)
    monkeypatch.setattr(run_check, "answering_run", slow)
    run_check._verdicts.clear()
    backend_gate.embed_allowed(con, collection)
    kept = run_check._verdicts[run_check._where(collection)][5]
    assert kept[0] == run_check.QUERY_UNSETTLED and kept[1] == clock[0], "stamped when the look ENDED"
    assert not run_check.verdict_outdated(collection)


def test_a_process_that_cannot_bind_never_erases_a_binding(node, endpoint, pids):
    """THE THIRD READ: the web (which had known the process a minute) and the
    worker (which had known it longer) unbound and bound the run in turn at
    every answer, and every restart of either unbound it: a binding is only
    ever replaced by another binding, or by another run."""
    now, seen = pids
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    key = f"{DOC_URL}\nmedcpt-article\ndocument"
    assert json.loads(run_check.heard_path().read_text())["heard"][key]["proc"] == [101, "2026-11-26T08:00:00Z"]
    stamp = run_check.heard_path().stat().st_mtime_ns
    seen["medcpt-article"] = time.monotonic()             # this process has only just seen it
    for _ in range(5):
        run_check.heard(_recipe(collection), {R1})
    assert json.loads(run_check.heard_path().read_text())["heard"][key]["proc"] == [101, "2026-11-26T08:00:00Z"]
    assert run_check.heard_path().stat().st_mtime_ns == stamp, "and nothing written"
    # ANOTHER RUN, unbindable yet: its record is unbound (the old binding is the old run's)
    time.sleep(0.01)
    run_check.heard(_recipe(collection), {R2})
    said = json.loads(run_check.heard_path().read_text())["heard"][key]
    assert said["run"] == R2 and said["proc"] is None


def test_an_answer_that_named_no_run_binds_nothing(node, endpoint, pids):
    """THE THIRD READ: {R1, None} bound R1 — and the answer that named none may
    be the other process's."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    key = f"{DOC_URL}\nmedcpt-article\ndocument"
    now, seen = pids
    now["medcpt-article"] = (303, "2026-11-26T10:00:00Z")
    time.sleep(0.01)
    run_check.heard(_recipe(collection), {R1, None})
    said = json.loads(run_check.heard_path().read_text())["heard"][key]
    assert said["proc"] == [101, "2026-11-26T08:00:00Z"], "not rebound to 303 on an answer that may not be its"
    run_check.heard(_recipe(collection), {R1})
    assert json.loads(run_check.heard_path().read_text())["heard"][key]["proc"] == [303, "2026-11-26T10:00:00Z"]


def test_the_runners_listing_names_a_process_by_its_pid_and_its_start(monkeypatch):
    """THE THIRD READ: every test faked `known_process`; the listing's own
    reading of it, here."""
    import httpx
    from app import httpc, model_load
    rows = {"loaded_models": [{"id": "vega-bge-m3", "backend": "vega", "size_vram": 900 << 20,
                               "process": {"pid": 57, "started_at": "2026-11-26T08:06:22Z", "cpu_percent": 0.0}},
                              {"id": "louise-chat", "size_vram": 7 << 30, "process": {"pid": 58}}]}
    monkeypatch.setattr(model_load, "_fetch_system", REAL_FETCH)
    monkeypatch.setattr(httpc, "get", lambda url, **k: httpx.Response(200, request=httpx.Request("GET", url), json=rows))
    model_load.reset()
    assert model_load.loaded_models("http://localai:8080", max_age_s=0.0) == {"vega-bge-m3", "louise-chat"}
    (proc, first) = model_load.known_process("http://localai:8080/v1/embeddings", "vega-bge-m3")
    assert proc == (57, "2026-11-26T08:06:22Z")
    assert model_load.known_process("http://localai:8080", "louise-chat") is None, "no start named: no process"
    time.sleep(0.01)
    model_load.loaded_models("http://localai:8080", max_age_s=0.0)
    assert model_load.known_process("http://localai:8080", "vega-bge-m3")[1] == first, "first seen is kept"
    rows["loaded_models"][0]["process"]["started_at"] = "2026-11-26T09:00:20Z"     # the same pid, restarted
    model_load.loaded_models("http://localai:8080", max_age_s=0.0)
    proc2, first2 = model_load.known_process("http://localai:8080", "vega-bge-m3")
    assert proc2 == (57, "2026-11-26T09:00:20Z") and first2 > first, "another process, seen from now"
    model_load.reset()


def test_cycles_refused_are_not_admitted_from_that_moment(node, endpoint, pids):
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    _counted(root, collection, codex)
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is True
    hold_runs.refuse_parks(root, collection.name, codex, "document", R1, why="a return that failed",
                           at="2026-11-26T08:30:00Z", signer=run_check.signer())
    assert run_check.park_admitted(ENDPOINT, "medcpt-article", (101, "2026-11-26T08:00:00Z")) is False, "not half a minute later: now"


def test_the_question_card_parks_only_an_admitted_run_too(node, endpoint, monkeypatch):
    """THE SECOND REVIEW, blocking: a question gives the card back by
    `question_card._give_back`, which parked every corpus encoder that says
    it parks and never asked the rule."""
    from app import model_evict, model_load, question_card
    asked, verbs = [], []
    monkeypatch.setattr(model_load, "held_sizes", lambda server, max_age_s=None: {"vega-nemotron": (55, 2 << 30)})
    monkeypatch.setattr(model_load, "parks", lambda server: {"vega-nemotron"})
    monkeypatch.setattr(model_load, "parked", lambda server: {})
    monkeypatch.setattr(model_load, "known_process", lambda server, model: ((55, "t0"), 0.0))
    monkeypatch.setattr(run_check, "park_admitted", lambda server, model, proc: asked.append((model, proc)) or False)
    monkeypatch.setattr(model_evict, "give_back", lambda server, v: verbs.append(
        ("park" if v.parks else "stop", v.unadmitted)) or (model_evict.UNLOADED, v.size))
    assert question_card._give_back("http://localai:8080", "vega-nemotron") is True
    assert asked == [("vega-nemotron", (55, "t0"))] and verbs == [("stop", True)]
    monkeypatch.setattr(run_check, "park_admitted", lambda server, model, proc: True)
    question_card._give_back("http://localai:8080", "vega-nemotron")
    assert verbs[-1] == ("park", False)


def test_an_idle_worker_rechecks_a_tested_run_heard_again(node, endpoint):
    """THE SECOND REVIEW: R1 and R2 both gated; the worker restarts with R1
    answering; the model goes back to R2 and a question hears it. Three idle
    rounds saw the gate test PASSED and did nothing, and a node that
    withholds kept the vector leg waiting for the worker's re-check."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True            # R1
    endpoint.run = R2
    run_check._verdicts.clear()
    run_check._answering.clear()
    assert backend_gate.embed_allowed(con, collection) is True            # R2 gated
    assert sorted(b["run"] for b in statements_kept(root)) == sorted([R1, R2])
    # the worker starts again: R1 answers, its statement re-checked
    endpoint.run = R1
    run_check.reset_cache()
    backend_gate.reset_cache()
    assert backend_gate.embed_allowed(con, collection) is True
    assert not run_check.verdict_outdated(collection)
    # the model goes back to R2, and a QUESTION (another process) hears it
    endpoint.run = R2
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}))
    copy_state._states.clear()
    assert copy_state.of(collection).test("gate").status == copy_state.PASSED, "tested before: not OPEN"
    assert run_check.verdict_outdated(collection), "but the verdict is about R1"
    body = next(b for b in statements_kept(root) if b["run"] == R2)
    assert not run_check.rechecked(codex, "document", R2, body)
    worker._settle_the_run(con, collection)
    assert run_check.rechecked(codex, "document", R2, body), "re-checked by the idle round itself"
    assert not run_check.verdict_outdated(collection)


def test_the_manual_door_says_to_pause_the_collection_first(node, endpoint):
    """`hold embedder check wikipedia` on the running node: "held by the node
    (worker) … retry when it is done" — which never comes while it runs."""
    root, collection, codex = node
    copy_state._states.clear()
    gate = copy_state.of(collection).test("gate")
    assert gate.status == copy_state.OPEN
    assert (f"`louise node pause {collection.name}`, `louise hold embedder check {collection.name}`, "
            f"`louise node resume {collection.name}`") in gate.clears


# ---- the fifth read (2026-11-26) -------------------------------------------------------

def test_a_look_that_took_its_whole_timeout_is_fresh_from_when_it_ended(node, endpoint, monkeypatch):
    """THE FIFTH READ, run: the verdict's own expiry was `now + FAIL_TTL_S`
    with `now` taken before both looks. The document side paused (it refuses
    at once), the query probe running out its 300 s: stored expired, and every
    embed step looked again — d, q, d, q, d, q, each a 300 s hang."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    real, clock, looks = run_check.answering_run, [1_000_000.0], []
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])

    def paused_and_slow(recipe, query=False, **k):
        looks.append("q" if query else "d")
        if query:
            clock[0] += run_check.FAIL_TTL_S + 1          # the probe ran out its timeout
            raise OSError("timed out")
        raise OSError("the document model is paused")
    monkeypatch.setattr(run_check, "answering_run", paused_and_slow)
    run_check._verdicts.clear()
    assert backend_gate.embed_allowed(con, collection) is False
    for _ in range(3):
        assert backend_gate.embed_allowed(con, collection) is False
        worker._settle_the_run(con, collection)
    assert looks == ["d", "q"], "looked at once, not at every step"
    clock[0] += run_check.FAIL_TTL_S + 1
    backend_gate.embed_allowed(con, collection)
    assert looks == ["d", "q", "d", "q"], "and again after its bound"


def test_a_query_run_heard_during_a_failed_look_is_looked_at_once_it_ends(node, endpoint, monkeypatch):
    """THE FIFTH READ, run: the run heard was read AFTER the look — a query
    probe hanging while the web heard R2 recorded R2 as looked at, and R2's
    gate waited FAIL_TTL_S."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    real = run_check.answering_run

    def hangs(recipe, query=False, **k):
        if query:
            time.sleep(0.01)
            _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}, role="query"))
            raise OSError("timed out")
        return real(recipe, query=query, **k)
    monkeypatch.setattr(run_check, "answering_run", hangs)
    run_check._verdicts.clear()
    assert backend_gate.embed_allowed(con, collection) is True
    kept = run_check._verdicts[run_check._where(collection)][5]
    assert kept[0] == run_check.QUERY_UNSETTLED and kept[2] == R1, "the run heard before the look"
    assert run_check.verdict_outdated(collection), "R2, heard during it, is looked at now"


def test_a_document_run_heard_since_a_failed_look_is_looked_at_once(node, endpoint, monkeypatch):
    """THE FIFTH READ, run: a failed document look stored run None, and None
    passed the cache's check for every run heard after it — a run the web
    heard (München's case, a look during the restart) waited FAIL_TTL_S."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    real, looks = run_check.answering_run, []

    def away(recipe, query=False, **k):
        if not query:
            looks.append(1)
            raise OSError("the document model did not answer")
        return real(recipe, query=query, **k)
    monkeypatch.setattr(run_check, "answering_run", away)
    run_check._verdicts.clear()
    assert backend_gate.embed_allowed(con, collection) is False
    assert run_check._verdicts[run_check._where(collection)][3] == R1, "the run heard before it"
    worker._settle_the_run(con, collection)
    assert looks == [1] and not run_check.verdict_outdated(collection), "the same run: within its bound"
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}))
    assert run_check.verdict_outdated(collection), "another run heard since: looked at now"
    worker._settle_the_run(con, collection)
    assert looks == [1, 1]
    for _ in range(3):
        worker._settle_the_run(con, collection)
        backend_gate.embed_allowed(con, collection)
    assert looks == [1, 1], "and no loop: that look failed holding R2, heard before it"


def test_a_binding_survives_a_restart_and_a_stale_read(node, endpoint, pids, monkeypatch):
    """THE FIFTH READ: the keep rule (a None process never erases a binding
    for the same run, resolved under the lock) had no test that reached it.
    A process just started knows no run and has only just seen the backend
    process; a stale read of the record says nothing."""
    now, seen = pids
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    key = f"{DOC_URL}\nmedcpt-article\ndocument"
    bound = [101, "2026-11-26T08:00:00Z"]
    assert json.loads(run_check.heard_path().read_text())["heard"][key]["proc"] == bound
    seen["medcpt-article"] = time.monotonic()             # cannot bind yet
    run_check._answering.clear()                          # a restart: its own memory empty
    run_check._answering_at.clear()
    run_check.heard(_recipe(collection), {R1})
    assert json.loads(run_check.heard_path().read_text())["heard"][key]["proc"] == bound, "a restart"
    monkeypatch.setattr(run_check, "_shared", lambda: {})  # the record read stale: "nothing said"
    run_check.heard(_recipe(collection), {R1})
    assert json.loads(run_check.heard_path().read_text())["heard"][key]["proc"] == bound, "a stale read"


# ---- the sixth read (2026-11-26) -------------------------------------------------------

def _hung_clock(monkeypatch) -> list:
    """The clock a look is timed on (`run_check._look_clock`), for a probe to
    move as a hung one holds its caller; the encoders' backoff fresh."""
    mono = [1000.0]
    monkeypatch.setattr(run_check, "_look_clock", lambda: mono[0])
    monkeypatch.setattr(run_check, "_slow", {})
    monkeypatch.setattr(run_check, "_gate_slow", {})
    return mono


def test_a_look_that_did_not_settle_is_looked_at_again_on_the_idle_round(node, endpoint, monkeypatch):
    """THE SIXTH READ, run: a tested run heard again (R2, by the web) on a
    collection with nothing to embed: the idle round looked, the encoder was
    busy (or the re-check deferred), and the verdict held R2 — never looked at
    again, the vector leg held back until a restart. A look that did not
    settle expires on the idle round too; a run its gate REFUSED does not."""
    from app.hold import embedder as hold_emb
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    real, clock = run_check.answering_run, [time.time()]
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])
    for failure, settles in ((OSError("busy"), False), (hold_emb.GateDeferred("no room on the card"), False),
                             (hold_emb.GateError("refused"), True)):
        def failing(recipe, query=False, _e=failure, **k):
            if not query:
                raise _e
            return real(recipe, query=query, **k)
        monkeypatch.setattr(run_check, "answering_run", failing)
        run_check._verdicts.clear()
        assert backend_gate.embed_allowed(con, collection) is False
        assert not run_check.verdict_outdated(collection), "within its bound"
        clock[0] += run_check.FAIL_TTL_S + 1
        assert run_check.verdict_outdated(collection) is (not settles), type(failure).__name__


def test_a_query_look_that_keeps_failing_for_one_run_is_put_off_longer_each_time(node, endpoint, monkeypatch):
    """THE SIXTH READ, run: the bound (300 s) equals the probe's timeout, so a
    query model that times out held the worker's main loop half the time."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    real, clock, looks = run_check.answering_run, [time.time()], []
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])
    mono = _hung_clock(monkeypatch)

    def failing(recipe, query=False, **k):
        if query:
            looks.append(clock[0])
            mono[0] += 300.0                             # HUNG: the probe held its caller (the ninth read)
            raise OSError("timed out")
        return real(recipe, query=query, **k)
    monkeypatch.setattr(run_check, "answering_run", failing)
    run_check._verdicts.clear()
    start = clock[0]
    for _ in range(80):                                  # 80 steps a minute apart
        backend_gate.embed_allowed(con, collection)
        clock[0] += 60
    gaps = [round(b - a) for a, b in zip(looks, looks[1:])]
    assert 300 <= gaps[0] <= 360 and 600 <= gaps[1] <= 660 and 1200 <= gaps[2] <= 1260, gaps
    assert all(1800 <= g <= 1860 for g in gaps[3:]), "the cap, from the fourth failure on"
    # ANOTHER run heard: looked at at once, whatever the count
    n = len(looks)
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}, role="query"))
    backend_gate.embed_allowed(con, collection)
    assert len(looks) == n + 1


def test_a_run_heard_after_a_look_that_heard_nothing_is_looked_at_at_once(node, endpoint, monkeypatch):
    """THE SIXTH READ: a failed look on a fresh worker or a model never asked
    holds no run; a run heard after it must not pass the cache."""
    root, collection, codex = node
    con = db.connect(collection)
    real, looks = run_check.answering_run, []

    def away(recipe, query=False, **k):
        looks.append("q" if query else "d")
        raise OSError("not answering")
    monkeypatch.setattr(run_check, "answering_run", away)
    run_check._answering.clear()
    run_check._answering_at.clear()
    run_check._heard_read.clear()
    run_check.heard_path().unlink(missing_ok=True)
    run_check._verdicts.clear()
    assert backend_gate.embed_allowed(con, collection) is False and looks == ["d", "q"]
    v = run_check._verdicts[run_check._where(collection)]
    assert v[3] is None and v[5][2] is None, "nothing heard before it, nothing by it"
    assert backend_gate.embed_allowed(con, collection) is False and looks == ["d", "q"], "within its bound"
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}))
    assert run_check.verdict_outdated(collection), "the idle round looks"
    backend_gate.embed_allowed(con, collection)
    assert looks == ["d", "q", "d"], "the embed path looks at the document side; the query side keeps its bound"
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}, role="query"))
    assert run_check.verdict_outdated(collection), "and a query run heard since"



# ---- the seventh read (2026-11-26) -----------------------------------------------------

def test_a_failing_document_side_does_not_take_the_query_probe_along(node, endpoint, monkeypatch):
    """THE SEVENTH READ, run: with the document look failing too, the query
    probe followed its FAIL_TTL_S pace — 35 probes in 3 h where the backoff
    allows about 6. Hung probes, both (the ninth read: only a look that held
    its caller is backed off)."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    clock, q = [time.time()], []
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])
    mono = _hung_clock(monkeypatch)

    def failing(recipe, query=False, **k):
        if query:
            q.append(clock[0])
        mono[0] += 300.0
        raise OSError("not answering")
    monkeypatch.setattr(run_check, "answering_run", failing)
    run_check._verdicts.clear()
    for _ in range(180):                                  # 3 h, a step a minute
        backend_gate.embed_allowed(con, collection)
        clock[0] += 60
    assert len(q) <= 9, f"{len(q)} query probes in 3 h"


def test_a_document_look_that_keeps_failing_is_put_off_longer_each_time_and_holds_nobody_else(
        node, endpoint, monkeypatch):
    """THE SEVENTH READ, run: N collections on one hung encoder probed it every
    FAIL_TTL_S, and on the main loop held it up to 98 %. THE EIGHTH: a hold
    shared by the encoder paused every collection's embedding, for deferrals
    the embed stage does not hold for. Each collection's own look backs off;
    nothing else is held."""
    import httpx
    from app import encoder_timeouts
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    clock, looks = [time.time()], []
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])

    mono = _hung_clock(monkeypatch)

    def hung(recipe, query=False, **k):
        if not query:
            looks.append(clock[0])
        mono[0] += 300.0                                     # it held its caller its timeout
        raise httpx.ReadTimeout("timed out")
    monkeypatch.setattr(run_check, "answering_run", hung)
    encoder_timeouts._holds.clear()
    run_check._verdicts.clear()
    for _ in range(120):                                   # 2 h, a step a minute
        backend_gate.embed_allowed(con, collection)
        clock[0] += 60
    gaps = [round(b - a) for a, b in zip(looks, looks[1:])]
    assert 300 <= gaps[0] <= 360 and 600 <= gaps[1] <= 660 and 1200 <= gaps[2] <= 1260, gaps
    assert all(g <= run_check.FAIL_CAP_S + 60 for g in gaps), gaps
    assert not encoder_timeouts.any_held(), "no other collection held by this one's look"
    # another run heard: looked at at once
    n = len(looks)
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}))
    backend_gate.embed_allowed(con, collection)
    assert len(looks) == n + 1


def test_a_suspect_query_run_is_sent_back_even_while_its_failed_look_is_put_off(node, endpoint, monkeypatch):
    """THE EIGHTH READ, run: a query look failed (its side put off up to 30
    min), then a context of the query run failed its canary — every embed step
    missed the cache and probed the document side, and the query run was never
    sent back to the gate until the bound passed."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    real, q = run_check.answering_run, []

    def query_fails(recipe, query=False, **k):
        if query:
            q.append(1)
            raise OSError("not answering")
        return real(recipe, query=query, **k)
    monkeypatch.setattr(run_check, "answering_run", query_fails)
    run_check._verdicts.clear()
    backend_gate.embed_allowed(con, collection)
    assert q == [1]
    monkeypatch.setattr(run_check, "answering_run", real)
    suspect = {"on": True}
    real_suspect = run_check._suspect
    monkeypatch.setattr(run_check, "_suspect",
                        lambda c, role="document": suspect["on"] if role == "query" else real_suspect(c, role))
    monkeypatch.setattr(run_check, "_canary_counts", lambda c: (0, 1))   # a failure the verdict did not see
    looked = []
    real_look_q = run_check._look_at_the_query_side
    monkeypatch.setattr(run_check, "_look_at_the_query_side",
                        lambda *a, **k: (looked.append(1), suspect.update(on=False), real_look_q(*a, **k))[2])
    for _ in range(5):
        backend_gate.embed_allowed(con, collection)
    assert looked == [1], "the suspect query run is looked at — and sent back — at once, once"


def test_a_settled_collection_is_looked_at_again_on_the_search_beat_when_its_verdict_is_outdated(
        node, endpoint, monkeypatch):
    """THE SEVENTH READ, run: with embed producers (the node's setting) the
    round's `_settle_the_run` never runs; a collection on `off`, settled once,
    never gated the run a question heard after. The search beat looks at a
    settled collection again when its verdict is outdated — no manifest opened
    otherwise."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    assert not worker._settle_wanted(collection), "settled and current: nothing to look at"
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}))
    assert worker._settle_wanted(collection), "a run heard since: looked at on the beat"


# ---- the ninth read (2026-11-26) -------------------------------------------------------

def test_a_look_that_fails_quickly_is_asked_again_at_its_bound_and_embedding_resumes(node, endpoint, monkeypatch):
    """THE NINTH READ, run (BLOCKING): every unsettled look was backed off —
    a refused connection (LocalAI restarting), a twin busy, a room short — and
    embedding resumed up to 25 min after the encoder was back. Only a look that
    held its caller is backed off; a quick one is asked again at FAIL_TTL_S."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    real, clock, looks = run_check.answering_run, [time.time()], []
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])
    _hung_clock(monkeypatch)                              # the look clock does not move: every failure quick
    down = {"on": True}

    def restarting(recipe, query=False, **k):
        if not query:
            looks.append(clock[0])
        if down["on"]:
            raise OSError("connection refused")
        return real(recipe, query=query, **k)
    monkeypatch.setattr(run_check, "answering_run", restarting)
    run_check._verdicts.clear()
    for _ in range(40):                                   # 40 min down, a step a minute
        assert backend_gate.embed_allowed(con, collection) is False
        clock[0] += 60
    gaps = [round(b - a) for a, b in zip(looks, looks[1:])]
    assert gaps and all(300 <= g <= 360 for g in gaps), gaps
    down["on"] = False
    back = clock[0]
    while not backend_gate.embed_allowed(con, collection):
        clock[0] += 60
        assert clock[0] - back <= run_check.FAIL_TTL_S + 60, "embedding resumes within one bound"


def test_a_hung_encoder_is_asked_by_one_look_for_every_collection_on_it(node, endpoint, monkeypatch):
    """THE NINTH READ, run: with the backoff per collection, six collections on
    one hung encoder probed it six times a period on one thread (98 % held).
    The backoff is the ENCODER's: a collection whose own verdict is new is not
    asked while another's look found the encoder hung — and a run heard since
    (an answer, some process) is looked at at once."""
    root, collection, codex = node
    con = db.connect(collection)
    _hung_clock(monkeypatch)
    looks = []
    real = run_check.answering_run
    monkeypatch.setattr(run_check, "answering_run",
                        lambda recipe, query=False, **k: (looks.append(query), real(recipe, query=query, **k))[1])
    now = time.time()
    heard = run_check.answering(collection)
    run_check._slow[run_check._flight(collection)] = (2, now + 600, heard.get("document"))   # another's hung look
    if run_check._flights_query(collection) is not None:
        run_check._slow[run_check._flights_query(collection)] = (2, now + 600, heard.get("query"))
    run_check._verdicts.clear()
    assert backend_gate.embed_allowed(con, collection) is False and looks == [], "not asked again"
    v = run_check._verdicts[run_check._where(collection)]
    assert "held its last look" in v[1] and v[2] >= now + 590, v[:3]
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R1}))
    assert backend_gate.embed_allowed(con, collection) is True and False in looks, "a run heard: asked"


def test_a_suspect_run_whose_re_gate_cannot_finish_is_not_looked_at_every_step(node, endpoint, monkeypatch):
    """THE NINTH READ, run: a suspect query run whose re-gate the card had no
    room for — twenty steps, twenty probes of each side. A verdict keeps the
    canary failures its look saw; only a new one sends it back before it
    expires (the run stays suspect, `check_write` refuses meanwhile)."""
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    monkeypatch.setattr(run_check, "_suspect", lambda c, role="document": role == "query")
    counts = {"q": 1}
    monkeypatch.setattr(run_check, "_canary_counts", lambda c: (0, counts["q"]))
    looked = []
    monkeypatch.setattr(run_check, "_look_at_the_query_side",
                        lambda *a, **k: looked.append(1))        # the re-gate deferred: nothing settled
    for _ in range(20):
        backend_gate.embed_allowed(con, collection)
    assert looked == [1], f"{len(looked)} query looks in 20 steps"
    counts["q"] = 2                                                # ANOTHER canary failure: at once
    backend_gate.embed_allowed(con, collection)
    assert looked == [1, 1]


# ---- the tenth read (2026-11-26) -------------------------------------------------------

def test_a_new_run_is_gated_at_once_while_the_encoder_is_marked_slow(node, endpoint, monkeypatch):
    """THE TENTH READ, run: the model marked slow by another collection's look;
    bge-m3 restarted with another env, `check_write` dropped this collection's
    verdict for R2 — and the next look took the backoff's path, R2 ungated
    until the mark ended (up to 30 min). A run other than the one heard when
    the mark was set passes it over."""
    root, collection, codex = node
    con = db.connect(collection)
    _hung_clock(monkeypatch)
    assert backend_gate.embed_allowed(con, collection) is True
    heard = run_check.answering(collection)
    now = time.time()
    run_check._slow[run_check._flight(collection)] = (2, now + 1800, heard.get("document"))
    looks = []
    real = run_check.answering_run
    monkeypatch.setattr(run_check, "answering_run",
                        lambda recipe, query=False, **k: (looks.append(query), real(recipe, query=query, **k))[1])
    time.sleep(0.01)
    _as_another_process(lambda: run_check.heard(_recipe(collection), {R2}))
    run_check.forget(collection)                                   # `check_write` dropped it for R2
    backend_gate.embed_allowed(con, collection)
    assert False in looks, "R2 looked at — and its gate run — at once"


def test_a_long_gate_that_defers_does_not_back_the_encoder_off(node, endpoint, monkeypatch):
    """THE TENTH READ, run: a look's time included its gate, and a 240 s gate
    that deferred ("another request held the context") backed the whole
    encoder off to 30 min — the encoder had answered every probe. A look that
    heard a run did not hang."""
    from app.hold import embedder as hold_emb
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    mono = _hung_clock(monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])
    real_look = run_check._look

    def long_deferred(c, stop, log):
        mono[0] += 240.0
        return False, str(hold_emb.GateDeferred("another request held the context")), R1, False
    monkeypatch.setattr(run_check, "_look", long_deferred)
    run_check._verdicts.clear()
    for _ in range(5):
        backend_gate.embed_allowed(con, collection)
        clock[0] += run_check.FAIL_TTL_S + 1
    assert run_check._slow == {}, "no encoder backoff for looks the encoder answered"
    monkeypatch.setattr(run_check, "_look", real_look)


def test_another_collections_query_mark_does_not_send_every_step_to_the_document_encoder(
        node, endpoint, monkeypatch):
    """THE TENTH READ, run (nit): while another collection kept the query model
    marked, this one's unsettled query side was kept past its own bound — the
    verdict never fresh, the document encoder probed at every step (30 in 30)."""
    root, collection, codex = node
    if run_check._flights_query(collection) is None:
        pytest.skip("the fixture's space has no query encoder of its own")
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    _hung_clock(monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])
    looks = []
    real = run_check.answering_run
    monkeypatch.setattr(run_check, "answering_run",
                        lambda recipe, query=False, **k: (looks.append(query), real(recipe, query=query, **k))[1])
    key = run_check._verdicts[run_check._where(collection)]
    heard_q = run_check.answering(collection).get("query")
    run_check._verdicts[run_check._where(collection)] = key[:5] + (
        (run_check.QUERY_UNSETTLED, clock[0] - 10_000, heard_q, 1),) + key[6:]       # its own bound long past
    run_check._slow[run_check._flights_query(collection)] = (3, clock[0] + 3600, heard_q)   # past the 30 steps
    for _ in range(30):
        backend_gate.embed_allowed(con, collection)
        clock[0] += 60
    assert looks.count(False) <= 6, f"{looks.count(False)} document probes in 30 steps"
    # THE DELTA READ, run: re-stamped from now, the kept side outlived a mark the
    # encoder's answer cleared, by its own bound (up to 28 min). Kept as long as
    # the mark and no longer: the mark gone, the query side is looked at
    where = run_check._where(collection)
    v = run_check._verdicts[where]
    run_check._verdicts[where] = (v[0], v[1], clock[0] - 1) + v[3:]      # a look now, with the mark still live
    backend_gate.embed_allowed(con, collection)
    run_check._slow.pop(run_check._flights_query(collection), None)       # the encoder answered: cleared
    n = looks.count(True)
    backend_gate.embed_allowed(con, collection)
    assert looks.count(True) == n + 1, "the mark gone: the query side looked at at once"


def test_a_gate_whose_request_timed_out_backs_its_own_collection_off_and_holds_no_other(
        node, endpoint, monkeypatch):
    """THE DELTA READ, run (BLOCKING): a gate whose first request waited its
    300 s timeout counted as answered and was asked again every 600 s — the
    main loop held half the time. THE ELEVENTH, run: marked on the ENCODER, one
    collection's own gate timeouts held another whose gate would pass, for as
    long as the first kept timing out. A probe the encoder answered marks no
    encoder; the hung gate backs ITS collection off, longer each time."""
    import httpx
    from app.hold import embedder as hold_emb
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    mono = _hung_clock(monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])
    real = run_check.answering_run
    monkeypatch.setattr(run_check, "answering_run",
                        lambda recipe, query=False, **k: real(recipe, query=query, **k) if query else R2)
    gates = []

    def hung_gate(*a, **k):
        gates.append(clock[0])
        mono[0] += 300.0
        try:
            raise httpx.ReadTimeout("timed out")
        except httpx.ReadTimeout as e:
            raise hold_emb.GateDeferred("the encoder could not embed: timed out") from e
    monkeypatch.setattr(run_check, "validate", hung_gate)
    run_check._verdicts.clear()
    for _ in range(120):                                   # 2 h, a step a minute
        backend_gate.embed_allowed(con, collection)
        clock[0] += 60
    assert run_check._slow == {}, "the encoder answered its probe: no other collection is held"
    gaps = [round(b - a) for a, b in zip(gates, gates[1:])]
    assert 300 <= gaps[0] <= 360 and 600 <= gaps[1] <= 660 and 1200 <= gaps[2] <= 1260, gaps
    assert all(g <= run_check.FAIL_CAP_S + 60 for g in gaps), gaps


def test_a_chain_cut_on_purpose_is_not_read_for_a_timeout():
    """THE ELEVENTH READ, run: `raise … from None` cut the chain (VeGa's
    receipts read timing out, not the encoder) and `_timed_out` followed
    `__context__` past it — the encoder marked for another service's wait."""
    import httpx
    from app.hold import embedder as hold_emb
    try:
        try:
            raise httpx.ReadTimeout("vega receipts")
        except httpx.ReadTimeout:
            raise hold_emb.GateDeferred("receipts deferred") from None
    except hold_emb.GateDeferred as e:
        assert run_check._timed_out(e) is False
    try:
        try:
            raise httpx.ReadTimeout("the encoder")
        except httpx.ReadTimeout as inner:
            raise hold_emb.GateDeferred("could not embed") from inner
    except hold_emb.GateDeferred as e:
        assert run_check._timed_out(e) is True


# ---- the twelfth read (2026-11-26) -----------------------------------------------------

@pytest.mark.parametrize("why", ["timed out", "deferred late"])
def test_a_hung_gate_beside_a_quickly_failing_query_side_backs_off(node, endpoint, monkeypatch, why):
    """THE TWELFTH READ, run (BLOCKING): a collection's own gate backoff lived
    in its verdict's expiry only, so an asymmetric space's query side failing
    quickly (its shorter bound) let the document gate through every 600 s —
    50 % of the main loop, 91 % with two collections. And a gate that ran
    240 s and deferred with no timeout was asked again every 300 s (44 %). The
    GATE's backoff is read before every look, slow deferrals included."""
    import httpx
    from app.hold import embedder as hold_emb
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    mono = _hung_clock(monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])

    def probe(recipe, query=False, **k):
        if query:
            raise OSError("query model refused")          # a quick failure
        return R2                                          # answered: a run never gated
    monkeypatch.setattr(run_check, "answering_run", probe)
    gates, held = [], [0.0]

    def slow_gate(*a, role="document", **k):
        gates.append(clock[0])
        mono[0] += 300.0 if why == "timed out" else 240.0
        clock[0] += 300.0 if why == "timed out" else 240.0
        held[0] += 300.0 if why == "timed out" else 240.0
        if why == "timed out":
            try:
                raise httpx.ReadTimeout("timed out")
            except httpx.ReadTimeout as e:
                raise hold_emb.GateDeferred("the encoder could not embed: timed out") from e
        raise hold_emb.GateDeferred("another request held the context")
    monkeypatch.setattr(run_check, "validate", slow_gate)
    run_check._verdicts.clear()
    start = clock[0]
    while clock[0] - start < 3 * 3600:
        backend_gate.embed_allowed(con, collection)
        clock[0] += 60
    share = held[0] / (clock[0] - start)
    assert len(gates) <= 8 and share <= 0.21, (len(gates), round(share, 3))


# ---- the thirteenth read (2026-11-26) ---------------------------------------------------

def test_a_gate_that_hung_once_then_defers_quickly_is_not_asked_every_round(node, endpoint, monkeypatch):
    """THE THIRTEENTH READ, run (BLOCKING): `_note_gate`'s quick branch returned
    the gate's backoff EXPIRED — the verdict expired in 1 s and every round
    looked (171 gates in 3 h; main 36)."""
    import httpx
    from app.hold import embedder as hold_emb
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    mono = _hung_clock(monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])

    def probe(recipe, query=False, **k):
        if query:
            raise OSError("query model refused")
        return R2
    monkeypatch.setattr(run_check, "answering_run", probe)
    gates = []

    def gate(*a, role="document", **k):
        gates.append(clock[0])
        if len(gates) == 1:
            mono[0] += 300.0
            clock[0] += 300.0
            try:
                raise httpx.ReadTimeout("timed out")
            except httpx.ReadTimeout as e:
                raise hold_emb.GateDeferred("the encoder could not embed: timed out") from e
        raise hold_emb.GateDeferred("another collection's gate measures on the twin now")
    monkeypatch.setattr(run_check, "validate", gate)
    run_check._verdicts.clear()
    start = clock[0]
    while clock[0] - start < 3 * 3600:
        backend_gate.embed_allowed(con, collection)
        clock[0] += 60
    gaps = [round(b - a) for a, b in zip(gates, gates[1:])]
    assert len(gates) <= 40, (len(gates), gaps[:8])


def test_a_query_gate_that_defers_late_backs_off_and_is_the_gates(node, endpoint, monkeypatch):
    """THE THIRTEENTH READ, run (BLOCKING): the query side's `again` doubled only
    a look that TIMED OUT; a query gate that ran 240 s and deferred was asked
    every 300 s (44 %). And it is the GATE's backoff, shared by the space."""
    from app.hold import embedder as hold_emb
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    mono = _hung_clock(monkeypatch)
    clock = [time.time()]
    monkeypatch.setattr(run_check.time, "time", lambda: clock[0])
    monkeypatch.setattr(run_check, "answering_run",
                        lambda recipe, query=False, **k: R2 if query else R1)
    gates, held = [], [0.0]
    real = run_check.validate

    def gate(*a, role="document", **k):
        if role != "query":
            return real(*a, role=role, **k)
        gates.append(clock[0])
        mono[0] += 240.0
        clock[0] += 240.0
        held[0] += 240.0
        raise hold_emb.GateDeferred("another request held the context")
    monkeypatch.setattr(run_check, "validate", gate)
    start = clock[0]
    while clock[0] - start < 3 * 3600:
        assert backend_gate.embed_allowed(con, collection) is True
        clock[0] += 60
    share = held[0] / (clock[0] - start)
    assert share <= 0.21, (len(gates), round(share, 3))
    q_key = run_check._flights_query(collection)
    assert any(k[0] == q_key for k in run_check._gate_slow), "the query gate's backoff is the gate's"
