"""An unload Louise made itself is no eviction (app/own_unloads.py; the
keeper's back-off and penalty, app/resident.py `_due`).

München, 2026-11-25 22:40Z, embed mode, ten minutes after the switch: a
question took the card, the encoders it unloaded were taken by the keeper for
the reclaimer's work — "off the card again 10 min after its load: space is
claimed, so it is not loaded again for 10 min", "the budget shrank by 8,453
MiB … the penalty on the margin is 8,880 MiB, halved after each 2 h"."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import httpc, model_load, own_unloads, resident

S = "http://localai:8080"


def test_a_note_is_read_by_any_process_of_the_node_and_ages_out(isolated_config):
    assert own_unloads.since((S, "vega-dar-legal-it"), 600) is None, "no record: nothing known"
    own_unloads.note(S, "vega-dar-legal-it", "a question", now=1_000.0)
    own_unloads.note(S, "louise-translate-madlad", now=1_100.0)
    assert own_unloads.since((S, "vega-dar-legal-it"), 600, now=1_500.0) == "a question"
    assert own_unloads.since((S, "louise-translate-madlad"), 600, now=1_500.0) == ""
    assert own_unloads.since((S, "vega-dar-legal-it"), 600, now=1_601.0) is None, "older than what was asked"
    assert own_unloads.since((S, "never"), 600, now=1_500.0) is None
    own_unloads.note(S, "x", now=1_000.0 + own_unloads.KEEP_S + 1)
    assert own_unloads.since((S, "vega-dar-legal-it"), 10 ** 9, now=1_000.0 + own_unloads.KEEP_S + 2) is None, \
        "pruned at the next write: the file does not grow"
    assert own_unloads._path().parent.name == "state"


def test_a_record_that_cannot_be_written_or_read_never_raises(isolated_config, monkeypatch):
    monkeypatch.setattr(own_unloads, "_path", lambda: (_ for _ in ()).throw(OSError("read-only")))
    own_unloads.note(S, "m")
    assert own_unloads.since((S, "m"), 600) is None


@pytest.fixture
def runner(monkeypatch, isolated_config):
    answers = {"shutdown": 200, "park": (200, {"message": "model parked", "parked": {
        "size_host": 1, "size_vram_needed": 1, "since": "2026-11-25T21:00:00Z"}})}
    monkeypatch.setattr(model_load, "loaded_models", lambda server, max_age_s=None: frozenset({"m"}))

    def post(url, json=None, **kw):
        if url.endswith("/backend/shutdown"):
            return SimpleNamespace(status_code=answers["shutdown"])
        status, body = answers["park"]
        return SimpleNamespace(status_code=status, json=lambda: body, headers={})
    monkeypatch.setattr(httpc, "post", post)
    return answers


def test_every_stop_and_park_the_runner_took_is_noted_and_a_refused_one_is_not(runner):
    assert model_load.unload(S, "m") is True and own_unloads.since((S, "m"), 60) == ""
    own_unloads._path().unlink()
    runner["shutdown"] = 500
    assert model_load.unload(S, "m") is False and own_unloads.since((S, "m"), 60) is None
    assert model_load.park(S, "m")[0] == model_load.PARKED and own_unloads.since((S, "m"), 60) == ""
    own_unloads._path().unlink()
    runner["park"] = (409, {"error": {"type": "model_busy", "code": 409, "message": "busy"}})
    assert model_load.park(S, "m")[0] == model_load.PARK_BUSY and own_unloads.since((S, "m"), 60) is None


def test_a_park_that_broke_its_backend_is_louises_own_and_a_model_already_gone_is_not(runner):
    runner["park"] = (500, {"error": {"type": "model_stopped", "code": 500, "message": "stopped"}})
    assert model_load.park(S, "m")[0] == model_load.PARK_GONE and own_unloads.since((S, "m"), 60) == ""
    own_unloads._path().unlink()
    runner["park"] = (404, {"error": {"type": "model_not_loaded", "code": 404, "message": "not loaded"}})
    assert model_load.park(S, "m")[0] == model_load.PARK_GONE and own_unloads.since((S, "m"), 60) is None


def test_a_warm_that_left_its_model_unloaded_is_no_load_to_be_evicted_from(isolated_config, monkeypatch):
    """The review of 2026-11-25: a parked model's return the card refused
    (503: it stays parked) ended the keeper's warm without raising; marked as
    a load, the next beat found it "off the card again" and took a back-off
    and a penalty for an eviction nobody made."""
    said, penalized = [], []
    resident.reset()
    resident.set_log(lambda level, text: said.append(text))
    monkeypatch.setattr(resident, "_penalize", lambda r, stayed: penalized.append(r.model))
    state = {"loaded": False}
    r = resident.Resident(S, "vega-bge-m3", ("query encoder",), ("wikipedia",), warm=lambda: None)
    try:
        with resident._lock:
            resident._last = {r.key: r}
        monkeypatch.setattr(resident, "preloads", lambda state=None: (True, ""))
        monkeypatch.setattr(resident, "_loaded", lambda r_: state["loaded"])
        monkeypatch.setattr(resident, "_plan_now", lambda want, mem=None: None)
        monkeypatch.setattr(resident, "room", lambda r_: None)
        resident._warm_all([r], lambda: False)
        with resident._lock:
            t = resident._track[r.key]
            assert t.warmed_at is None and t.next_try > resident._now(), "not a load: asked again in two minutes"
        assert resident._due(r, resident._now() + resident.RETRY_UNLOADED_S + 1) is True
        assert penalized == [] and not [x for x in said if "off the card again" in x]
        state["loaded"] = None                      # the runner cannot say: taken as loaded, as before
        resident._warm_all([r], lambda: False)
        state["loaded"] = False
        with resident._lock:
            resident._track[r.key].warmed_at = None
            resident._track[r.key].next_try = 0.0
        resident._warm_all([r], lambda: False)
    finally:
        resident.reset()
        from app import model_pins
        resident.set_log(model_pins.node_event)


def _warmed(r: resident.Resident, ago_s: float) -> float:
    now = resident._now()
    with resident._lock:
        t = resident._track.setdefault(r.key, resident._Track())
        t.warmed_at, t.strikes, t.next_try = now - ago_s, 0, 0.0
    return now


def test_a_model_louise_unloaded_itself_is_due_at_once_with_no_strike_and_no_penalty(isolated_config, monkeypatch):
    said, penalized = [], []
    resident.reset()
    resident.set_log(lambda level, text: said.append(text))
    monkeypatch.setattr(resident, "_penalize", lambda r, stayed: penalized.append((r.model, stayed)))
    dar = resident.Resident(S, "vega-dar-legal-it", ("query encoder", "encoder"), ("Normattiva-DAR",))
    try:
        # the reclaimer's eviction, as before: a back-off and a penalty
        now = _warmed(dar, 590.0)
        assert resident._due(dar, now) is False and penalized == [("vega-dar-legal-it", pytest.approx(590.0))]
        assert any("off the card again 10 min after its load" in t for t in said)

        # the same absence after Louise's own unload: no strike, no penalty, due now
        said.clear()
        penalized.clear()
        with resident._lock:
            resident._track[dar.key] = resident._Track()
        now = _warmed(dar, 590.0)
        own_unloads.note(S, "vega-dar-legal-it", "a question")
        assert resident._due(dar, now) is True and penalized == [] and said == []
        with resident._lock:
            assert resident._track[dar.key].strikes == 0 and resident._track[dar.key].warmed_at is None

        # an unload of Louise's from BEFORE this load says nothing about it
        now = _warmed(dar, 30.0)
        own_unloads.note(S, "vega-dar-legal-it", now=__import__("time").time() - 300.0)
        assert resident._due(dar, now) is False and len(penalized) == 1, "evicted 30 s after a load made since"
    finally:
        resident.reset()
        from app import model_pins
        resident.set_log(model_pins.node_event)
