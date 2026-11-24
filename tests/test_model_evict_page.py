"""PAGE INSTEAD OF UNLOAD (operator, 2026-11-24: "Extend page instead of
unload to the dense models"; app/model_evict.py, platform.toml `[models]
make_room`, 07 §7.8).

Measured on the B70 that morning: a card overcommitted by 3.5 GiB kept every
model loaded, the driver paging the idle ones — 0.2–0.4 s more on a small
model's next request against 7.5–8.7 s after an unload. The rule here: a
load WORK waits on unloads nothing while the host can hold what the card
cannot; the keeper's own preloads, a host under its floor and a model whose
paged load failed keep the unload of 2026-11-16; off by default, and
LocalAI's reclaimer off with it."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from app import model_evict, model_load, model_use, platform_config, resident
from tests.test_model_evict import MIB, NOW, H, S, Runner, _used, card  # noqa: F401 — `card` is a fixture
from tests.test_resident import node  # noqa: F401 — `card` builds on it

GIB = 1 << 30
ROOT = Path(__file__).resolve().parents[1]
ScheduleError = platform_config.schedule_mod.ScheduleError


@pytest.fixture
def paging(card, config_dir, monkeypatch):
    """The node's file says "page", floor 12 GiB; the host has 40 GiB
    available unless the test says otherwise."""
    (config_dir / "platform.toml").write_text('[models]\nmake_room = "page"\n', encoding="utf-8")
    host = {"available": 40 * GIB}
    monkeypatch.setattr(model_evict, "host_available", lambda: host["available"])
    model_evict.reset()
    card.host = host
    yield card
    model_evict.reset()


def _full(card) -> Runner:
    """A card with 3 GiB free under the line and two idle models on it."""
    r = card({"vega-qwen3-embedding-4b": 5_000, "omnivoice-cpp": 1_700}, extra_mib=21_250)
    _used(vega_qwen3_embedding_4b=9, omnivoice_cpp=5)
    return r


# ---- the setting -------------------------------------------------------------

def test_the_rule_is_the_files_and_unload_unless_it_says_page(isolated_config, config_dir):
    assert platform_config.load().models.make_room == "unload" and model_evict.rule() == ("unload", 12 * GIB)
    (config_dir / "platform.toml").write_text('[models]\nmake_room = "page"\npage_floor_gib = 20\n', encoding="utf-8")
    assert model_evict.rule() == ("page", 20 * GIB)
    for bad, says in (('make_room = "swap"', 'models.make_room is "unload" or "page"'),
                      ("make_room = true", 'models.make_room is "unload" or "page"'),
                      ('make_room = "page"\npage_floor_gib = 0', "page_floor_gib: must be >= 1")):
        (config_dir / "platform.toml").write_text(f"[models]\n{bad}\n", encoding="utf-8")
        with pytest.raises(ScheduleError, match=re.escape(says)):
            platform_config.load()
        assert model_evict.rule() == ("unload", 0), "a file that does not load changes nothing"


# ---- the rule ----------------------------------------------------------------

def test_off_by_default_a_load_that_does_not_fit_unloads_as_before(card):
    r = _full(card)
    out = model_evict.make_room(S, need=6_000 * MIB, horizon=NOW, for_what="louise-translate-madlad",
                                exclude=("louise-translate-madlad",), page=True)
    assert out.fits and out.paged == 0 and r.unloaded == ["vega-qwen3-embedding-4b"], "the oldest real use"


def test_work_that_does_not_fit_unloads_nothing_while_the_host_can_hold_the_difference(paging):
    r = _full(paging)
    out = model_evict.make_room(S, need=6_000 * MIB, horizon=NOW, for_what="louise-translate-madlad",
                                exclude=("louise-translate-madlad",), page=True)
    assert out.fits and r.unloaded == [] and r.calls["shutdown"] == 0
    free = int(0.95 * 32_656 * MIB) - (5_000 + 1_700 + 21_250) * MIB
    assert out.paged == 6_000 * MIB - free and out.why == "paged"
    assert r.calls["system"] == 0, "nothing is read to choose a victim: there is none"
    said = [s for s in paging.said if "nothing unloaded for louise-translate-madlad" in s]
    assert len(said) == 1 and "the driver pages" in said[0] and "floor 12,288" in said[0]


def test_a_load_that_fits_is_not_paged_and_says_nothing(paging):
    r = paging({"omnivoice-cpp": 1_700})
    out = model_evict.make_room(S, need=6_000 * MIB, horizon=NOW, for_what="m", exclude=("m",), page=True)
    assert out.fits and out.paged == 0 and paging.said == [] and r.unloaded == []


def test_the_keepers_preload_keeps_the_unload_whatever_the_file_says(paging):
    """The architecture session's review: a preload is never urgent, and
    must not push what is in use off the card. Only the caller that says
    `page` — work — is let over the line."""
    r = _full(paging)
    out = model_evict.make_room(S, need=6_000 * MIB, horizon=NOW, for_what="the preload of m", exclude=("m",))
    assert out.fits and out.paged == 0 and r.unloaded == ["vega-qwen3-embedding-4b"]
    assert not [s for s in paging.said if "pages" in s]


def test_only_counted_work_asks_to_page(paging, monkeypatch):
    asked: list[bool] = []
    monkeypatch.setattr(model_evict, "need_of", lambda server, model: 6_000 * MIB)
    monkeypatch.setattr(model_evict, "make_room",
                        lambda server, **kw: asked.append(kw["page"]) or model_evict.Outcome(True))
    paging({})
    model_evict.before_load(S, "louise-translate-madlad")
    with model_use.not_work():
        model_evict.before_load(S, "louise-translate-madlad")
    assert asked == [True, False], "a request's load, then Louise's own (a warm, a check, a gate)"


@pytest.mark.parametrize("available_gib, why", [(14, "would be under its floor"), (None, "cannot be read")])
def test_a_host_that_cannot_hold_it_or_cannot_be_read_unloads_as_before(paging, available_gib, why):
    r = _full(paging)
    paging.host["available"] = None if available_gib is None else available_gib * GIB
    out = model_evict.make_room(S, need=6_000 * MIB, horizon=NOW, for_what="louise-translate-madlad",
                                exclude=("louise-translate-madlad",), page=True)
    assert out.fits and out.paged == 0 and r.unloaded == ["vega-qwen3-embedding-4b"]
    said = [s for s in paging.said if "is not paged" in s]
    assert len(said) == 1 and why in said[0] and "by unloading, as before" in said[0]


def test_a_model_whose_paged_load_failed_gets_its_room_by_unloading_the_next_time(paging, monkeypatch):
    """An llama.cpp model without the engine's switch refuses an allocation
    the card cannot hold: let over the line it would fail at every attempt."""
    r = _full(paging)
    kw = dict(need=6_000 * MIB, horizon=NOW, for_what="louise-translate-madlad",
              exclude=("louise-translate-madlad",), page=True)
    assert model_evict.make_room(S, **kw).paged and r.unloaded == []
    model_evict.load_ended(S, "louise-translate-madlad", False)
    warned = [s for s in paging.said if "did not load over the line" in s]
    assert len(warned) == 1 and "GGML_SYCL_BMG_VRAM_CHECK=0" in warned[0]
    out = model_evict.make_room(S, **kw)
    assert out.fits and out.paged == 0 and r.unloaded == ["vega-qwen3-embedding-4b"], "unloaded for, this time"
    # another model is still paged, and this one again once the hour is over
    other = dict(kw, for_what="louise-rerank-nemotron-1b", exclude=("louise-rerank-nemotron-1b",))
    assert model_evict.make_room(S, **dict(other, need=9_000 * MIB)).paged
    monkeypatch.setattr(model_evict, "PAGE_AGAIN_S", 0.0)
    assert model_evict.make_room(S, **dict(kw, need=9_000 * MIB)).paged


def test_a_paged_load_that_succeeds_is_forgotten_and_an_unpaged_ones_failure_says_nothing(paging):
    _full(paging)
    kw = dict(need=6_000 * MIB, horizon=NOW, for_what="m", exclude=("m",), page=True)
    assert model_evict.make_room(S, **kw).paged
    model_evict.load_ended(S, "m", True)
    model_evict.load_ended(S, "m", False)          # no paged load is pending: nothing to learn
    model_evict.load_ended(S, "never-paged", False)
    assert not [s for s in paging.said if "did not load" in s] and model_evict.make_room(S, **kw).paged


def test_the_cold_load_tells_the_outcome(paging, monkeypatch):
    told: list[tuple[str, bool]] = []
    monkeypatch.setattr(model_evict, "load_ended", lambda server, model, ok: told.append((model, ok)))
    monkeypatch.setattr(model_load, "_make_room", lambda server, model: None)
    monkeypatch.setattr(model_load, "loaded_models", lambda server, max_age_s: set())
    paging({})

    class Answer:
        def __init__(self, status):
            self.status_code, self.text = status, ""
    model_load.ensure_loaded(S, "m-ok", lambda bound: Answer(200), caller="test")
    model_load.ensure_loaded(S, "m-refused", lambda bound: Answer(500), caller="test")
    assert told == [("m-ok", True), ("m-refused", False)]


# ---- what the operator sees, and LocalAI's reclaimer -------------------------

def test_show_says_the_rule_and_what_it_stands_on(paging, monkeypatch):
    from app import httpc
    r = paging({})
    assert resident._make_room_shown([])["rule"] == "page"
    shown = resident._make_room_shown([])
    assert shown["host_floor_mib"] == 12 * 1024 and shown["host_available_mib"] == 40 * 1024 and "notes" not in shown
    paging.host["available"] = 8 * GIB
    assert "under its floor now" in resident._make_room_shown([])["notes"][0]
    want = [resident.Resident(S, "m", ("chat",), ())]
    notes = resident._make_room_shown(want)["notes"]
    assert any("memory reclaimer is not readable" in n or "memory reclaimer is ON" in n for n in notes), \
        "the Runner's settings carry no memory_reclaimer_enabled: said, never assumed off"
    monkeypatch.setattr(httpc, "get", lambda url, **kw: type("R", (), {
        "status_code": 200, "json": staticmethod(lambda: {"memory_reclaimer_enabled": False})})())
    assert not any("reclaimer" in n for n in resident._make_room_shown(want).get("notes", []))
    assert r.unloaded == []


def test_show_in_unload_says_how_to_page(card):
    card({})
    shown = resident._make_room_shown([])
    assert shown["rule"] == "unload" and 'make_room = "page"' in shown["says"]


def test_localais_reclaimer_follows_the_nodes_file(tmp_path):
    sys.path.insert(0, str(ROOT / "deploy" / "vega" / "stage"))
    try:
        import localai_settings as settings
    finally:
        sys.path.pop(0)
    live = dict(settings.DESIRED)
    assert settings.make_room_of(None) == "unload" and not settings.drift(live)
    f = tmp_path / "platform.toml"
    f.write_text('[node]\nname = "nas"\n', encoding="utf-8")
    assert settings.make_room_of(str(f)) == "unload"
    f.write_text('[models]\nmake_room = "page"\n', encoding="utf-8")
    assert settings.make_room_of(str(f)) == "page"
    want = settings.desired_for(live, "page")
    assert want["memory_reclaimer_enabled"] is False, "a card paging idle models reads full at every tick"
    assert want["memory_reclaimer_threshold"] == settings.DESIRED["memory_reclaimer_threshold"], \
        "the keeper reads the line with the reclaimer off"
    assert settings.drift(live, want) == {"memory_reclaimer_enabled": True}, "the deploy would turn it off"
    assert settings.merged(live, want)["memory_reclaimer_enabled"] is False
    f.write_text('[models]\nmake_room = "swap"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="make_room"):
        settings.make_room_of(str(f))
    deploy = (ROOT / "deploy" / "16f" / "deploy-louise-madlad-t5.sh").read_text(encoding="utf-8")
    assert deploy.count('localai_settings.py" $PLATFORM_ARG') == 2, "applied AND checked with the node's file"
