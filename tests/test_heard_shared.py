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
    run_check._park_admitted.clear()


def test_an_encoder_may_be_parked_only_as_a_run_whose_cycles_are_counted(node, endpoint, monkeypatch):
    """The operator, 2026-11-26: "Either a model is validated or it does not
    embed." A write was held to the counted cycles; a park was not."""
    root, collection, codex = node
    con = db.connect(collection)
    assert run_check.park_admitted(ENDPOINT, "louise-chat") is None, "no collection's encoder: LocalAI's word stands"
    assert run_check.park_admitted(ENDPOINT, "medcpt-article") is False, "nobody has heard its run yet"
    run_check._park_admitted.clear()
    assert backend_gate.embed_allowed(con, collection) is True
    assert run_check.park_admitted(ENDPOINT, "medcpt-article") is False, "gated, and its park cycles never counted"
    _counted(root, collection, codex)
    assert run_check.park_admitted(ENDPOINT, "medcpt-article") is True
    assert run_check.park_admitted(ENDPOINT, "medcpt-query") is False, "the query model has cycles of its own to count"
    _counted(root, collection, codex, role="query")
    assert run_check.park_admitted(ENDPOINT, "medcpt-query") is True
    # ANOTHER RUN ANSWERS (the model started again): not admitted until ITS cycles are counted
    time.sleep(0.01)
    run_check.heard(_recipe(collection), {R2})
    run_check._park_admitted.clear()
    assert run_check.park_admitted(ENDPOINT, "medcpt-article") is False
    # kept half a minute a model: the evictor asks for every candidate of every load
    asked = []
    real = collections_mod.load_all
    monkeypatch.setattr(collections_mod, "load_all", lambda: asked.append(1) or real())
    for _ in range(5):
        assert run_check.park_admitted(ENDPOINT, "medcpt-article") is False
    assert asked == []
    # what cannot be read is a stop, never a park
    run_check._park_admitted.clear()
    monkeypatch.setattr(collections_mod, "load_all", lambda: (_ for _ in ()).throw(OSError("unreadable")))
    assert run_check.park_admitted(ENDPOINT, "medcpt-article") is False


def test_cycles_that_were_refused_admit_nothing(node, endpoint):
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    _counted(root, collection, codex)
    assert run_check.park_admitted(ENDPOINT, "medcpt-article") is True
    hold_runs.refuse_parks(root, collection.name, codex, "document", R1, why="a return that failed",
                           at="2026-11-26T08:30:00Z", signer=run_check.signer())
    run_check._park_admitted.clear()
    assert run_check.park_admitted(ENDPOINT, "medcpt-article") is False


def test_the_manual_door_says_to_pause_the_collection_first(node, endpoint):
    """`hold embedder check wikipedia` on the running node: "held by the node
    (worker) … retry when it is done" — which never comes while it runs."""
    root, collection, codex = node
    copy_state._states.clear()
    gate = copy_state.of(collection).test("gate")
    assert gate.status == copy_state.OPEN
    assert (f"`louise node pause {collection.name}`, `louise hold embedder check {collection.name}`, "
            f"`louise node resume {collection.name}`") in gate.clears
