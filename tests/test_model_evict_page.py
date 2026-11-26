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
    assert platform_config.load().models.make_room == "unload" and model_evict.rule() == ("unload", 12 * GIB, 3 * GIB)
    (config_dir / "platform.toml").write_text('[models]\nmake_room = "page"\npage_floor_gib = 20\npage_over_gib = 5\n',
                                           encoding="utf-8")
    assert model_evict.rule() == ("page", 20 * GIB, 5 * GIB)
    for bad, says in (('make_room = "swap"', 'models.make_room is "unload" or "page"'),
                      ("make_room = true", 'models.make_room is "unload" or "page"'),
                      ('make_room = "page"\npage_floor_gib = 0', "page_floor_gib: must be >= 1"),
                      ('make_room = "page"\npage_over_gib = "all"', "page_over_gib: expected an integer")):
        (config_dir / "platform.toml").write_text(f"[models]\n{bad}\n", encoding="utf-8")
        with pytest.raises(ScheduleError, match=re.escape(says)):
            platform_config.load()
        assert model_evict.rule() == ("unload", 0, 0), "a file that does not load changes nothing"


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
    assert r.calls["system"] == 1, "one read, to count what the card is committed to; no victim is chosen"
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


def test_never_further_over_the_card_than_was_measured(paging):
    """The architecture session's blocking point: a model whose context is
    made on demand reserves it from the card at each request, and a refused
    reservation kills its backend. The driver fills the card before it
    pages, so the reserve cannot be kept free; the measured envelope can be
    kept — 3 GiB over the card at most (S5 held 3.5, a 1.9 GiB context made
    on demand in it)."""
    r = paging({"louise-chat": 20_000, "vega-qwen3-embedding-4b": 9_000})
    _used(louise_chat=1, vega_qwen3_embedding_4b=9)
    kw = dict(horizon=NOW, for_what="louise-translate-madlad", exclude=("louise-translate-madlad",), page=True)
    # 29,000 + 6,700 − 32,656 = 3,044 MiB over: inside the 3 GiB
    assert model_evict.make_room(S, need=6_700 * MIB, **kw).paged and r.unloaded == []
    # 29,000 + 6,800 − 32,656 = 3,144 MiB over: past it — the oldest real use is unloaded instead
    out = model_evict.make_room(S, need=6_800 * MIB, **kw)
    assert out.fits and out.paged == 0 and r.unloaded == ["vega-qwen3-embedding-4b"]
    said = [s for s in paging.said if "is not paged" in s]
    assert len(said) == 1 and "3,144 MiB over its size, past the 3,072 MiB that were measured to hold" in said[0]


def test_a_model_paged_out_or_idle_on_demand_counts_at_the_largest_it_was_seen(paging, monkeypatch):
    """A paged-out model reads small on the card, and a model whose context
    is made on demand holds it only while it works: the commitment is what
    they COULD take back, not what they hold this second."""
    r = paging({"louise-chat": 20_000, "vega-qwen3-embedding-4b": 2_700})
    monkeypatch.setattr(model_load, "size_vram",
                        lambda server, model: 5_685 * MIB if model == "vega-qwen3-embedding-4b" else None)
    assert model_evict.committed(S) == (20_000 + 5_685) * MIB
    assert r.unloaded == []


def test_the_host_keeps_its_floor_and_a_contexts_worth_above_it(paging):
    """A context made on a full card pushes as much of something else to
    the host: the keeper's working-memory reserve is kept there too."""
    _full(paging)
    free = int(0.95 * 32_656 * MIB) - (5_000 + 1_700 + 21_250) * MIB
    short = 6_000 * MIB - free
    enough = 12 * GIB + short + resident.CONTEXT_RESERVE
    kw = dict(need=6_000 * MIB, horizon=NOW, for_what="m", exclude=("m",), page=True)
    paging.host["available"] = enough
    assert model_evict.make_room(S, **kw).paged
    paging.host["available"] = enough - 1
    assert model_evict.make_room(S, **kw).paged == 0
    assert any("plus a context's 3,262" in s for s in paging.said)


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
    assert shown["over_the_card_at_most_mib"] == 3 * 1024 and shown["context_reserve_mib"] == 3_262
    paging.host["available"] = 14 * GIB
    assert "under its floor plus a context's reserve now" in resident._make_room_shown([])["notes"][0]
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
    # A FRESH LocalAI (it says nothing of its reclaimer yet): the file decides
    fresh = {k: v for k, v in live.items() if k != "memory_reclaimer_enabled"}
    want = settings.desired_for(fresh, "page")
    assert want["memory_reclaimer_enabled"] is False, "a card paging idle models reads full at every tick"
    assert want["memory_reclaimer_threshold"] == settings.DESIRED["memory_reclaimer_threshold"], \
        "the keeper reads the line with the reclaimer off"
    assert settings.merged(fresh, want)["memory_reclaimer_enabled"] is False
    assert settings.desired_for(fresh, "unload")["memory_reclaimer_enabled"] is True
    # ONCE IT SAYS ON OR OFF, THE SWITCH IS THE KEEPER'S (2026-11-25, the deploy of bcdf0dc: the keeper
    # turned it off in query mode, this script put it on from the file's "unload", the next beat off again)
    for held in (True, False):
        now = {**fresh, "memory_reclaimer_enabled": held}
        for rule in ("unload", "page"):
            want = settings.desired_for(now, rule)
            assert "memory_reclaimer_enabled" not in want, "neither written"
            assert settings.drift(now, want) == {}, "nor counted as drift"
            assert settings.merged(now, want)["memory_reclaimer_enabled"] is held, "left as the keeper set it"
    said = settings._said({**fresh, "memory_reclaimer_enabled": False})
    assert said["memory_reclaimer_enabled"] is False and "keeper" in said["the_reclaimers_switch_is"]
    f.write_text('[models]\nmake_room = "swap"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="make_room"):
        settings.make_room_of(str(f))
    deploy = (ROOT / "deploy" / "16f" / "deploy-louise-madlad-t5.sh").read_text(encoding="utf-8")
    assert deploy.count('localai_settings.py" $PLATFORM_ARG') == 2, "applied AND checked with the node's file"


# ---- QUERY MODE PAGES (operator, 2026-11-25: "A for paging") -----------------
#
# The node that day, in query mode: about 45 GiB of models on a 32 GiB card,
# 13.3–14.9 GiB in the driver's gtt, the 35B at the speed of an unloaded card,
# a query encoder wholly in host memory back in 0.2–0.3 s against 5–24 s for
# a cold start. His choice: in query mode nothing is unloaded to make room
# while system memory allows.

@pytest.fixture
def query(card, config_dir, monkeypatch):
    """The node in query mode, its file saying nothing of `make_room` (the
    node's own: "unload"); the host has 28 GiB available, as it had."""
    from app import node_mode
    node_mode.switch("query", by="test")
    host = {"available": 28 * GIB}
    monkeypatch.setattr(model_evict, "host_available", lambda: host["available"])
    model_evict.reset()
    card.host = host
    yield card
    model_evict.reset()


def test_in_query_mode_the_rule_is_page_whatever_the_file_says_and_the_files_in_any_other(isolated_config, config_dir):
    from app import node_mode
    assert model_evict.rule() == ("unload", 12 * GIB, 3 * GIB), "custom mode: the file's"
    node_mode.switch("query", by="test")
    assert model_evict.rule() == ("page", 12 * GIB, None), "no bound over the card but the host's floor"
    (config_dir / "platform.toml").write_text('[models]\nmake_room = "unload"\npage_floor_gib = 16\n', encoding="utf-8")
    assert model_evict.rule() == ("page", 16 * GIB, None), "the file's floor, the mode's rule"
    node_mode.switch("embed", by="test")
    assert model_evict.rule() == ("unload", 16 * GIB, 3 * GIB), "embedding makes contexts all day: the measured 3 GiB"


def test_in_query_mode_no_load_unloads_anything_while_the_host_has_memory(query):
    r = _full(query)
    # a question's load, and the keeper's own preload — which says no `page`: both page here
    for for_what, page in (("louise-rerank-nemotron-1b", True), ("the preload of louise-chat", False)):
        out = model_evict.make_room(S, need=6_000 * MIB, horizon=NOW, for_what=for_what, exclude=("m",), page=page)
        assert out.fits and out.why == "paged" and out.paged > 0, for_what
    assert r.unloaded == [] and r.calls["shutdown"] == 0
    said = [s for s in query.said if "nothing unloaded for" in s]
    assert len(said) == 2 and all("query mode: nothing is unloaded while the host has memory" in s for s in said)


def test_in_query_mode_the_card_may_be_committed_far_over_its_size(query):
    # the day's own state: the card's counter at its brim (a card never counts more than it has), the
    # models reading what is LEFT of them on it, and 43 GiB of them when each is whole — committed 15 GiB
    # over the card with this load, five times the 3 GiB measured for embedding
    from tests.test_resident import _known
    r = query({"louise-chat-35b": 21_169, "louise-translate-madlad": 3_590, "vega-dar-legal-it": 1,
               "vega-qwen3-embedding-4b": 2_429}, extra_mib=5_200)
    _known(louise_chat_35b=21_449, louise_translate_madlad=8_005, vega_dar_legal_it=8_237,
           vega_qwen3_embedding_4b=5_685)
    _used(louise_chat_35b=1, louise_translate_madlad=2, vega_dar_legal_it=9, vega_qwen3_embedding_4b=3)
    assert model_evict.committed(S) == (21_449 + 8_005 + 8_237 + 5_685) * MIB
    out = model_evict.make_room(S, need=4_404 * MIB, horizon=NOW, for_what="vega-nemotron-3-embed-1b",
                                exclude=("vega-nemotron-3-embed-1b",))
    assert out.fits and out.why == "paged" and r.unloaded == []


def test_in_query_mode_a_host_under_its_floor_unloads_as_before(query):
    r = _full(query)
    query.host["available"] = 12 * GIB + resident.CONTEXT_RESERVE + 1_000 * MIB    # a load 3 GiB over would cross it
    out = model_evict.make_room(S, need=6_000 * MIB, horizon=NOW, for_what="louise-translate-madlad",
                                exclude=("louise-translate-madlad",))
    assert out.fits and out.paged == 0 and r.unloaded == ["vega-qwen3-embedding-4b"], \
        "while system memory allows — and not past it: the oldest real use goes"
    assert [s for s in query.said if "is not paged" in s and "under its floor" in s]


def test_resident_show_says_query_modes_rule(query):
    query({})
    shown = resident._make_room_shown([])
    assert shown["rule"] == "page" and shown["over_the_card_at_most_mib"] is None
    assert shown["says"].startswith("QUERY MODE (operator, 2026-11-25): no load unloads anything")


def test_in_query_mode_no_load_is_paged_over_models_pinned_by_work(query, monkeypatch):
    """THE READ OF 2026-11-26, run: the driver pages IDLE models only. Six
    encoders pinned by embed mode's batches, the host above its floor: "paged",
    and the 35B was sent into the full card — the node's 10:44Z death. A room
    whose shortfall is more than the idle models hold is made by unloading
    (which the pins refuse too: no load is let over the line on them)."""
    from app import model_pins
    r = _full(query)
    monkeypatch.setattr(model_pins, "_applies", lambda server, model: True)
    assert model_pins.hold(S, "vega-qwen3-embedding-4b", "worker embed wikipedia") is True
    for _ in range(3):                                    # a switch asks every few seconds
        out = model_evict.make_room(S, need=6_000 * MIB, horizon=NOW, for_what="chat-35b",
                                    exclude=("chat-35b",), page=True)
        assert out.why != "paged" and out.paged == 0
    said = [s for s in query.said if "chat-35b is" in s and "not paged" in s]
    assert len(said) == 1, "said once a minute, not at every ask"
    assert "only 1,700 MiB of it is idle models' memory the driver may page" in said[0]
    assert "vega-qwen3-embedding-4b pinned by work in flight or busy" in said[0]
    # the pin let go: paged, as the mode's rule says
    model_pins.release("worker embed wikipedia")
    model_evict.reset()
    out = model_evict.make_room(S, need=6_000 * MIB, horizon=NOW, for_what="chat-35b",
                                exclude=("chat-35b",), page=True)
    assert out.why == "paged" and r.unloaded == []
