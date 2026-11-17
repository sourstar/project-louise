"""The worker's update keeper (app/worker.py `_update_beat`; app/hold/update.py):
at night only, one collection at a time, each pass in a process of its own;
the steps that need the collection's writers run with the collection PAUSED
by the keeper (RULE 1: a tool that needs the worker's writers asks for a
stop), and only its own pause is given back; an operator's pause is left
alone; a pass that waits is asked again later, never every beat."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import collections as collections_mod
from app import node_state, worker
from app.hold import update


@pytest.fixture
def keeper(isolated_config, data_dir, monkeypatch):
    cols = [SimpleNamespace(name=n, codex=object(), source="origin", acquire_enabled=True,
                            manifest_path=data_dir / "collections" / n / "state" / "manifest.sqlite")
            for n in ("wikipedia", "wikidata")]
    monkeypatch.setattr(collections_mod, "load_all", lambda **kw: cols)
    plan = {"wikipedia": update.Next("fetch", "2026-11 may be due"), "wikidata": update.Next(None, "nothing")}
    monkeypatch.setattr(update, "next_step", lambda root, name, codex, **kw: plan[name])
    monkeypatch.setattr(update, "window_open", lambda now=None: (True, "profile night"))
    node_state.set_worker_state("running", by="the test")
    ran: list[tuple[str, dict | None]] = []

    def child(name, *, stop, grace_s):
        ran.append((name, node_state.pause_of(name)))
        return keeper.code, {"says": f"exit {keeper.code}"}
    keeper = SimpleNamespace(cols=cols, plan=plan, ran=ran, child=child, code=0, rechecked={})
    return keeper


def beat(k):
    return worker._update_beat(k.rechecked, run_child=k.child)


def test_nothing_runs_outside_the_night(keeper, monkeypatch):
    monkeypatch.setattr(update, "window_open", lambda now=None: (False, "profile day"))
    assert beat(keeper) == [] and keeper.ran == []


def test_one_pass_per_collection_with_work_and_no_pause_for_a_fetch(keeper):
    got = beat(keeper)
    assert [g["collection"] for g in got] == ["wikipedia"]
    assert keeper.ran == [("wikipedia", None)], "a fetch needs no writer: nothing paused"


def test_a_step_that_needs_the_writers_runs_paused_and_the_pause_is_given_back(keeper):
    keeper.plan["wikipedia"] = update.Next("records", "resumes 2026-11 at records")
    beat(keeper)
    (name, pause), = keeper.ran
    assert name == "wikipedia" and worker.UPDATE_PAUSE_BY in pause["by"]
    assert node_state.pause_of("wikipedia") is None, "given back once the pass ends"


def test_an_operators_pause_is_left_alone(keeper):
    node_state.set_paused("wikipedia", True, by="louise node pause (the operator)")
    keeper.plan["wikipedia"] = update.Next("records", "resumes 2026-11 at records")
    assert beat(keeper) == [] and keeper.ran == []
    assert "the operator" in node_state.pause_of("wikipedia")["by"]


def test_a_pause_a_stopped_worker_left_is_given_back(keeper):
    node_state.set_paused("wikipedia", True, by=worker.UPDATE_PAUSE_BY)
    keeper.plan["wikipedia"] = update.Next(None, "waits for a human")
    beat(keeper)
    assert node_state.pause_of("wikipedia") is None and keeper.ran == []


def test_a_pass_that_waits_is_asked_again_later_not_every_beat(keeper):
    keeper.code = update.EXIT_WAITING
    beat(keeper)
    beat(keeper)
    assert len(keeper.ran) == 1, "UPDATE_RECHECK_S before the origin is asked again"


def test_a_step_the_mode_does_not_run_waits(keeper, monkeypatch):
    keeper.plan["wikipedia"] = update.Next("records", "resumes 2026-11 at records")
    monkeypatch.setattr(node_state, "worker_state", lambda con=None: "acquire-only")
    assert beat(keeper) == [] and keeper.ran == []
    assert node_state.pause_of("wikipedia") is None
