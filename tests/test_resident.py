"""Every model Ask uses preloaded on the card — a TRIAL (operator,
2026-11-08, option A: "keep every model Ask uses resident on the card"; "The
load all is a test"; the final rule: "It applies to any collection, on demand
or all embedding preloads the model. Chat, speech, rerankers, madlad are
preloaded too, nothing is evicted only because it's idle, only if space is
claimed."; app/resident.py).

The set is the system's: the chat, translation and reranking models, the
encoders of every served collection whose embed is on-demand, all or p2p,
and the speech models. PRELOADED, NOT PINNED: the reclaimer evicts the
least-recently-used model when space is claimed, so nothing here pins; a
model not loaded is loaded only when the card has room for it under the
reclaimer's line, and one evicted again soon after its load backs off. The
node's mode wins (paused or seed-only preloads nothing); nothing is unloaded
for being idle."""
from __future__ import annotations

import inspect
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import (collections as collections_mod, config, cpu_limit, db, embeddings, gpu, llm, model_load,
                 model_pins, node_state, resident, retrieval, translate, worker)
from tests.test_config_tiers_embed_mode import _collection
from tests.test_model_pins import REAL_APPLIES, Runner

#: the runner's `/system` reader as shipped (the fixture replaces it)
REAL_FETCH = model_load._fetch_system
MIB = 1 << 20

SERVER = "http://localai:8080"


def _with(c, model, mode, query_model=None, reranker=None):
    recipe = collections_mod.EmbedRecipe(url=SERVER, api="vega", model=model, dtype="f16", ctx=512,
                                         query_model=query_model or model)
    rr = collections_mod.RerankRecipe(url=f"{SERVER}/v1/rerank", api="jina", model=reranker) \
        if reranker else None
    return replace(c, embed=recipe, embed_mode_default=mode, reranker=rr)


def _mode(c) -> str:
    return c.embed_mode_default


@pytest.fixture
def node(monkeypatch, tmp_path, isolated_config, config_dir):
    """A LocalAI that lists what it holds, pins and unpins, and loads on a
    warm; the node running; the chat and translation models set."""
    from app import httpc
    r = Runner()
    held: set[str] = set()
    warmed: list[str] = []
    unloads: list[str] = []
    settings = {"memory_reclaimer_threshold": 0.95, "api_keys": ["sk-secret"], "p2p_token": "tok-secret"}

    def get(url, **kw):
        if url.endswith("/api/settings"):
            return SimpleNamespace(status_code=200, json=lambda: dict(settings))
        return r.get(url, **kw)
    monkeypatch.setattr(httpc, "get", get)
    monkeypatch.setattr(httpc, "put", r.put)
    pin_calls: list[str] = []
    for name in ("hold", "release", "hold_for_process", "_set"):
        real = getattr(model_pins, name)
        monkeypatch.setattr(model_pins, name,
                            lambda *a, _n=name, _r=real, **k: (pin_calls.append(_n), _r(*a, **k))[1])
    monkeypatch.setattr(model_pins, "_path", lambda: tmp_path / "state" / "model-pins.json")
    monkeypatch.setattr(model_pins, "_applies", REAL_APPLIES)
    monkeypatch.setattr(model_load, "_fetch_system", lambda server: frozenset(held))
    monkeypatch.setattr(model_load, "activity", lambda server: {m: 0.0 for m in held})
    monkeypatch.setattr(model_load, "_record", lambda c: None)    # no cold-start line from a thread

    def post(url, json=None, timeout=None, data=None, files=None, **kw):
        model = (json or {}).get("model") or (data or {}).get("model")
        if url.endswith("/backend/shutdown"):
            unloads.append(model)
            held.discard(model)
        else:
            warmed.append(model)
            held.add(model)
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(httpc, "post", post)

    def loads(name):
        def warm(*a, **k):
            warmed.append(name)
            held.add(name)
        return warm
    monkeypatch.setattr(llm, "warm", loads("louise-chat"))
    monkeypatch.setattr(translate, "warm", loads("louise-translate"))
    monkeypatch.setattr(retrieval, "_warm_reranker", lambda rr: loads(rr.model)())
    monkeypatch.setattr(embeddings, "warm_side",
                        lambda c, *, query, recipe=None: loads((recipe.query_model if query else recipe.model))())
    monkeypatch.setattr(config, "CHAT_API", "openai")
    monkeypatch.setattr(config, "CHAT_URL", SERVER)
    monkeypatch.setattr(config, "CHAT_MODEL", "louise-chat")
    monkeypatch.setattr(config, "TRANSLATE_URL", SERVER)
    monkeypatch.setattr(config, "TRANSLATE_MODEL", "louise-translate")
    monkeypatch.setattr(config, "RERANK_URL", "")
    model_load.reset()
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(model_pins, "_say", lambda level, text: said.append((level, text)))
    resident.reset()
    resident.set_log(lambda level, text: said.append((level, text)))
    node_state.set_worker_state("running")
    yield SimpleNamespace(runner=r, held=held, warmed=warmed, unloads=unloads, said=said,
                          platform=config_dir / "platform.toml", settings=settings, pin_calls=pin_calls)
    resident.wait_warm(5)
    resident.reset()
    resident.set_log(model_pins.node_event)
    model_load.reset()


def _beat(served, **kw):
    out = resident.beat(served, embed_mode=kw.pop("embed_mode", _mode), **kw)
    resident.wait_warm(5)
    with model_load._lock:
        model_load._systems.clear()          # the next beat reads the runner afresh
    return out


def _card(monkeypatch, total_mib: int, used_mib: int):
    """A GPU probe answering one card, as app/gpu_probe.py's GET /gpu does."""
    card = {"used": used_mib}
    monkeypatch.setattr(config, "GPU_PROBE_URL", "http://gpu-probe:8080")

    def answer(url):
        assert url == "http://gpu-probe:8080/gpu"
        return {"devices": [{"card": "card1", "pdev": "0000:03:00.0", "driver": "xe", "vendor_id": "0x8086",
                             "device_id": "0xe223", "vendor": "Intel", "name": "Arc Pro B70",
                             "vram_total": total_mib * MIB, "vram_used": card["used"] * MIB,
                             "processes": [{"pid": 7, "label": "louise-chat", "command": "llama-server",
                                            "container": "localai", "vram": card["used"] * MIB,
                                            "host": 0}]}]}
    monkeypatch.setattr(gpu, "_get_json", answer)
    return card


def _texts(node) -> str:
    return " ".join(t for _, t in node.said)


def _models(rs) -> dict[str, tuple[str, ...]]:
    return {r.model: r.roles for r in rs}


# ---- the set ---------------------------------------------------------------------

def test_the_set_is_the_question_path_s_models_by_embed_mode(tmp_path, node):
    """off: not preloaded; on-demand, all and p2p: both encoders; every
    served collection's reranker; the chat and translation models; and the
    speech models, by default."""
    node.platform.write_text('[speech]\nstt_model = "whisper"\nvad_model = "silero"\n'
                             'tts_model = "omnivoice"\n')
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy", reranker="rerank-bge")
    full = _with(_collection(tmp_path, "wikidata"), "vega-nemotron", "all")
    p2p = _with(_collection(tmp_path, "pubmed"), "vega-medcpt-article", "p2p",
                query_model="vega-medcpt-query", reranker="rerank-medcpt")
    off = _with(_collection(tmp_path, "wikipedia"), "vega-bge-m3", "off", reranker="rerank-bge")
    got = resident.wanted([lazy, full, p2p, off], embed_mode=_mode)
    assert _models(got) == {
        "louise-translate": ("translation",),
        "vega-4b": ("query encoder", "encoder"),
        "rerank-bge": ("reranker",),
        "vega-nemotron": ("query encoder", "encoder"),
        "vega-medcpt-query": ("query encoder",),
        "vega-medcpt-article": ("encoder",),
        "rerank-medcpt": ("reranker",),
        "louise-chat": ("chat",),
        "whisper": ("speech to text",), "silero": ("voice activity",), "omnivoice": ("text to speech",)}
    assert "vega-bge-m3" not in _models(got), "embed off: loaded at its first search, as before"
    bge = next(r for r in got if r.model == "rerank-bge")
    assert bge.collections == ("gutenberg", "wikipedia"), "one entry, both collections named"
    assert {r.switch for r in got if r.roles[0].startswith(("speech", "voice", "text"))} == {resident.SPEECH}


def test_each_switch_off_takes_its_part_out(tmp_path, node):
    node.platform.write_text('[models]\nkeep_ask_resident = false\n[speech]\ntts_model = "omnivoice"\n')
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    assert _models(resident.wanted([lazy], embed_mode=_mode)) == {"omnivoice": ("text to speech",)}
    node.platform.write_text('[models]\npreload_speech = false\n[speech]\ntts_model = "omnivoice"\n')
    assert "omnivoice" not in _models(resident.wanted([lazy], embed_mode=_mode))
    assert "vega-4b" in _models(resident.wanted([lazy], embed_mode=_mode))


def test_a_switch_that_is_not_a_bool_is_refused_at_load(node):
    from app import platform_config
    node.platform.write_text('[models]\nkeep_ask_resident = "yes"\n')
    with pytest.raises(platform_config.schedule_mod.ScheduleError, match="keep_ask_resident"):
        platform_config.load()
    assert resident.switches().error, "a broken file keeps the last reading, and says so"


def test_served_is_neither_refused_nor_paused(tmp_path, node, monkeypatch):
    a = _with(_collection(tmp_path, "a"), "vega-a", "lazy")
    b = _with(_collection(tmp_path, "b"), "vega-b", "lazy")
    refused = _with(_collection(tmp_path, "r"), "vega-r", "lazy")
    monkeypatch.setattr(worker, "_live_collections", {"a": a, "b": b})     # the boot check refused r
    node_state.set_paused("b", True)
    assert [c.name for c in worker._served_now([a, b, refused])] == ["a"]
    node_state.set_paused("b", False)
    assert [c.name for c in worker._served_now([a, b, refused])] == ["a", "b"]


# ---- preloading -------------------------------------------------------------------

def test_a_beat_loads_what_is_missing_pins_nothing_and_says_so(tmp_path, node):
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    node.held.add("louise-chat")
    out = _beat([lazy])
    assert sorted(node.warmed) == ["louise-translate", "vega-4b"], "the loaded one is not loaded again"
    assert out["preloads"] and set(out["warming"]) == {(SERVER, "louise-translate"), (SERVER, "vega-4b")}
    assert out["set"] == sorted({(SERVER, m) for m in ("louise-chat", "louise-translate", "vega-4b")})
    assert node.runner.puts() == [] and node.pin_calls == [], "preloaded, not pinned"
    text = _texts(node)
    assert "preloading 3 model(s) for Ask, not pinned" in text
    assert "vega-4b — query encoder, encoder (gutenberg)" in text and "loading vega-4b" in text
    node.warmed.clear()
    _beat([lazy])
    assert node.warmed == [], "loaded: nothing to do"


def test_the_set_is_never_pinned(tmp_path, node, monkeypatch):
    """"nothing is evicted only because it's idle, ONLY IF SPACE IS CLAIMED"
    (operator, 2026-11-08): a pinned model is never evicted by the
    reclaimer, so the set is not pinned — not by a beat, a mode change, a
    switch to off, a pause or a stop. app/model_pins.py is the processing's."""
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy", reranker="rerank-bge")
    modes = {"gutenberg": "lazy"}
    _beat([c], embed_mode=lambda c: modes[c.name])
    modes["gutenberg"] = "off"
    _beat([c], embed_mode=lambda c: modes[c.name])
    resident.forget("gutenberg")
    node_state.set_worker_state("paused")
    _beat([c])
    resident.clear()
    assert node.pin_calls == [] and node.runner.puts() == []
    assert model_pins.status()["pins"] == []
    src = inspect.getsource(resident)
    for call in ("model_pins.hold(", "model_pins.release(", "model_pins.hold_for_process(",
                 "model_pins.holding(", "model_pins.using("):
        assert call not in src, call


def test_a_mode_change_takes_effect_at_the_next_beat(tmp_path, node):
    """The Library sets gutenberg to off: no longer preloaded, and left
    loaded; back to on-demand, all or p2p: loaded again once evicted."""
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    modes = {"gutenberg": "lazy"}
    _beat([c], embed_mode=lambda c: modes[c.name])
    assert "vega-4b" in node.held
    modes["gutenberg"] = "off"
    out = _beat([c], embed_mode=lambda c: modes[c.name])
    assert (SERVER, "vega-4b") not in out["set"] and "vega-4b" in node.held, "left loaded"
    assert node.unloads == []
    assert any("no longer preloaded vega-4b" in t and "left loaded until LocalAI needs the space" in t
               for _, t in node.said)
    for mode in ("all", "p2p", "lazy"):
        node.held.discard("vega-4b")                       # the reclaimer took it meanwhile
        modes["gutenberg"] = mode
        resident.reset()                                   # no back-off history: a fresh worker
        _beat([c], embed_mode=lambda c: modes[c.name])
        assert "vega-4b" in node.held, mode


@pytest.mark.parametrize("mode", ["paused", "seed-only"])
def test_paused_or_seed_only_preloads_nothing_and_unloads_nothing(tmp_path, node, mode):
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    _beat([c])
    node_state.set_worker_state(mode)
    node.warmed.clear()
    out = _beat([c])
    assert not out["preloads"] and out["set"] == []
    assert {"vega-4b", "louise-chat"} <= node.held and node.unloads == [], "nothing unloaded either"
    node.held.clear()                                       # the operator's load evicted them
    _beat([c])
    assert node.warmed == [], "nothing loaded while the operator has the GPU"
    assert any(f"the node is {mode}" in t for _, t in node.said)
    node_state.set_worker_state("running")
    resident.reset()
    _beat([c])
    assert "vega-4b" in node.warmed


def test_acquire_only_preloads_and_a_profile_s_seed_only_does_not(tmp_path, node, monkeypatch):
    """acquire-only embeds nothing for the corpus but serves every search;
    a profile never limits a query in acquire-only (the quiet windows), and
    the stricter mode wins where a profile says seed-only or paused."""
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    node_state.set_worker_state("acquire-only")
    assert _beat([c])["preloads"] and "vega-4b" in node.held
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.Limit(cores=12, profile="quiet",
                                                                      mode="acquire-only"))
    assert _beat([c])["preloads"]
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.Limit(cores=12, profile="quiet",
                                                                      mode="seed-only"))
    out = _beat([c])
    assert not out["preloads"] and "profile quiet" in out["why"] and out["set"] == []


def test_both_switches_off_preload_nothing(tmp_path, node):
    node.platform.write_text('[models]\nkeep_ask_resident = false\npreload_speech = false\n'
                             '[speech]\ntts_model = "omnivoice"\n')
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    out = _beat([c])
    assert out["set"] == [] and node.runner.calls == [] and node.warmed == []
    assert node.said == []


def test_switched_off_while_preloaded_says_so_and_unloads_nothing(tmp_path, node):
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    _beat([c])
    node.platform.write_text('[models]\nkeep_ask_resident = false\n')
    out = _beat([c])
    assert out["set"] == [] and node.unloads == [] and "vega-4b" in node.held
    assert any("keep_ask_resident = false" in t for _, t in node.said)


def test_loads_run_one_at_a_time_and_stop_on_a_pause(tmp_path, node, monkeypatch):
    """The loads run in the budget's order (2026-11-08: tier 1 first, chat
    first, so after a boot the question path is warm first and MADLAD is
    never first) — so the chat model loads first here, where the order a
    question meets its models loaded MADLAD first; the pause in the chat's
    load stops MADLAD's."""
    order: list[str] = []

    def slow(name):
        def warm(*a, **k):
            order.append(name)
            if name == "louise-chat":
                node_state.set_worker_state("paused")          # the operator takes the GPU mid-way
            node.held.add(name)
        return warm
    monkeypatch.setattr(translate, "warm", slow("louise-translate"))
    monkeypatch.setattr(llm, "warm", slow("louise-chat"))
    resident.beat([], embed_mode=_mode)
    assert resident._start_warm([], lambda: False) == [], "a second beat starts no second load"
    resident.wait_warm(5)
    assert order == ["louise-chat"], "the next load is not started once paused"


def test_a_failed_load_is_said_and_the_keeper_goes_on(tmp_path, node, monkeypatch):
    def fails(*a, **k):
        raise model_load.LoadTimeout("model loading took longer than 120 s (louise-translate)",
                                     model="louise-translate", bound_s=120.0)
    monkeypatch.setattr(translate, "warm", fails)
    _beat([])
    assert "louise-chat" in node.warmed
    assert any(level == "warn" and "louise-translate" in t and "not loaded" in t for level, t in node.said)
    node.warmed.clear()
    _beat([])
    assert not any("back-off" in t for _, t in node.said), "a failed load is no eviction"


def test_speech_models_are_loaded_with_one_request_of_their_kind(tmp_path, node):
    node.platform.write_text('[models]\nkeep_ask_resident = false\n[speech]\nstt_model = "whisper"\n'
                             'vad_model = "silero"\ntts_model = "omnivoice"\n')
    _beat([])
    assert sorted(node.warmed) == ["omnivoice", "silero", "whisper"]
    assert node.runner.puts() == []


# ---- the room check --------------------------------------------------------------

def test_the_room_check_refuses_a_warm_that_does_not_fit_and_says_it_once(tmp_path, node, monkeypatch):
    """MADLAD was 7,879 MiB on the card; the B70 is 32,656 MiB and 29,000
    used: 0.95 × 32,656 − 29,000 = 2,023 MiB under the line, so loading it
    would only make the reclaimer evict something. It waits, said once, and
    is loaded once space is given back."""
    node.held.add("louise-chat")
    card = _card(monkeypatch, 32_656, 29_000)
    with model_load._lock:
        model_load._sizes[(SERVER, "louise-translate")] = 7_879 * MIB
    for _ in range(3):
        _beat([])
    assert node.warmed == []
    waits = [t for _, t in node.said if "stays unloaded" in t]
    assert len(waits) == 1, "said once"
    assert waits[0].startswith("resident: louise-translate — translation — stays unloaded: the card has "
                               "room for it only after space is given back (needs 7,879 MiB, 2,023 MiB "
                               "free under the 0.95 line)")
    card["used"] = 20_000                                  # the operator's 35B went
    _beat([])
    assert node.warmed == ["louise-translate"]
    assert any("loading louise-translate" in t and "free under the 0.95 line" in t for _, t in node.said)
    node.held.discard("louise-translate")
    card["used"] = 31_000
    resident._track.clear()                                # an eviction long after: no back-off
    _beat([])
    assert len([t for _, t in node.said if "stays unloaded" in t]) == 2, "a new wait is said again"


def test_the_line_is_localai_s_own_threshold_and_nothing_else_of_its_settings(tmp_path, node, monkeypatch):
    node.held.add("louise-chat")
    _card(monkeypatch, 32_656, 23_000)
    with model_load._lock:
        model_load._sizes[(SERVER, "louise-translate")] = 7_879 * MIB
    node.settings["memory_reclaimer_threshold"] = 0.85     # 27,757 MiB: 4,757 free, too little
    _beat([])
    assert node.warmed == [] and any("under the 0.85 line" in t for _, t in node.said)
    assert "secret" not in _texts(node), "the settings carry api_keys: never said"
    node.settings["memory_reclaimer_threshold"] = "high"   # unreadable: the node's 0.95, 8,023 free
    resident.reset()
    _beat([])
    assert node.warmed == ["louise-translate"]
    assert resident._threshold(SERVER) == resident.DEFAULT_THRESHOLD


def test_a_model_never_seen_loaded_or_an_unread_card_is_loaded(tmp_path, node, monkeypatch):
    """The first time, its size is not known: allowed. A probe that does not
    answer decides nothing either: the load is made, as before."""
    node.held.add("louise-chat")
    _card(monkeypatch, 32_656, 32_000)
    _beat([])
    assert node.warmed == ["louise-translate"], "never seen: allowed"
    node.held.discard("louise-translate")
    node.warmed.clear()
    resident.reset()
    with model_load._lock:
        model_load._sizes[(SERVER, "louise-translate")] = 7_879 * MIB
    monkeypatch.setattr(gpu, "_get_json", lambda url: (_ for _ in ()).throw(ConnectionError("probe gone")))
    _beat([])
    assert node.warmed == ["louise-translate"], "the card unread: allowed"


def test_a_model_s_size_is_remembered_after_it_goes(monkeypatch):
    """`/system`'s `size_vram`, the largest seen, kept when the model leaves
    the card: the size the room check needs is a model's that is not loaded."""
    from app import httpc
    rows = [{"id": "louise-translate", "size_vram": 7_000 * MIB, "process": {"pid": 9}}]
    monkeypatch.setattr(httpc, "get", lambda url, **kw: SimpleNamespace(
        status_code=200, json=lambda: {"loaded_models": [dict(r) for r in rows]}))
    model_load.reset()
    assert REAL_FETCH(SERVER) == frozenset({"louise-translate"})
    rows[0]["size_vram"] = 7_879 * MIB                      # its compute buffers grew
    REAL_FETCH(SERVER)
    rows[0]["size_vram"] = 7_500 * MIB
    REAL_FETCH(SERVER)
    rows.clear()                                            # evicted
    assert REAL_FETCH(SERVER) == frozenset()
    assert model_load.size_vram(SERVER, "louise-translate") == 7_879 * MIB
    assert model_load.size_vram(SERVER + "/v1/chat/completions", "louise-translate") == 7_879 * MIB
    assert model_load.size_vram(SERVER, "louise-chat") is None
    model_load.reset()


# ---- the back-off ----------------------------------------------------------------

def test_a_repeat_eviction_backs_off_10_20_40_minutes_up_to_two_hours(tmp_path, node, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(resident, "_now", lambda: clock["t"])
    node.held.add("louise-chat")

    def at(t):
        clock["t"] = float(t)
        node.warmed.clear()
        return _beat([])

    at(0)
    assert node.warmed == ["louise-translate"]
    waits = []
    t = 0.0
    for _ in range(6):
        node.held.discard("louise-translate")              # the reclaimer evicted it
        t += 120                                           # found two minutes after its load
        out = at(t)
        assert node.warmed == [] and out["backing_off"] == [(SERVER, "louise-translate")]
        wait = resident._track[(SERVER, "louise-translate")].next_try - t
        waits.append(wait)
        assert at(t + wait - 60)["backing_off"], "still backing off"
        t += wait
        at(t)
        assert node.warmed == ["louise-translate"], "loaded again once the back-off ends"
    assert waits == [600, 1200, 2400, 4800, 7200, 7200]
    said = [t for _, t in node.said if "back-off" in t]
    assert len(said) == 6, "each back-off said once"
    assert said[0].startswith("resident: louise-translate — translation — off the card again 2 min after "
                              "its load: space is claimed, so it is not loaded again for 10 min (back-off 1")
    # it stayed longer than ten minutes: an eviction then starts the count again
    t += 900
    node.held.discard("louise-translate")
    at(t)
    assert node.warmed == ["louise-translate"] and resident._track[(SERVER, "louise-translate")].strikes == 0


def test_an_eviction_of_a_model_another_caller_loaded_is_no_back_off(tmp_path, node, monkeypatch):
    """Only a load this keeper made counts: a model a search loaded and the
    reclaimer evicted is loaded at the next beat (if it fits)."""
    clock = {"t": 0.0}
    monkeypatch.setattr(resident, "_now", lambda: clock["t"])
    node.held |= {"louise-chat", "louise-translate"}
    _beat([])
    node.held.discard("louise-translate")
    clock["t"] = 60.0
    out = _beat([])
    assert out["backing_off"] == [] and node.warmed == ["louise-translate"]


def test_warm_side_loads_the_side_a_search_asks(tmp_path, monkeypatch, isolated_config):
    """`embeddings.warm_side`: the query side's model on the query side's
    URL, one text — the request a search would send first."""
    c = _with(_collection(tmp_path, "pubmed"), "vega-medcpt-article", "lazy",
              query_model="vega-medcpt-query")
    seen = []

    def ensure(url, model, warm, *, caller, max_age_s=None):
        seen.append((url, model, caller))
        return None
    monkeypatch.setattr(model_load, "ensure_loaded", ensure)
    embeddings.warm_side(c, query=True)
    embeddings.warm_side(c, query=False)
    assert seen == [(SERVER, "vega-medcpt-query", "resident query encoder (pubmed)"),
                    (SERVER, "vega-medcpt-article", "resident encoder (pubmed)")]


# ---- the worker's unload paths (amended 2026-11-08: nothing unloaded for being idle) ----

def test_neither_unload_path_unloads_a_model_a_held_collection_names(tmp_path, node):
    """"nothing is evicted only because it's idle, only if space is claimed"
    (operator, 2026-11-08): preloaded or not, an idle encoder a held
    collection names stays; a `vega-` model no held collection names goes."""
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    off = _with(_collection(tmp_path, "wikipedia"), "vega-bge-m3", "off")
    node.held |= {"vega-4b", "vega-bge-m3", "vega-granite"}
    _beat([lazy, off])
    assert worker._release_embedder(lazy) == [], "on-demand: preloaded, nothing dropped"
    assert worker._release_embedder(off) == [], "off: not in the set"
    assert worker._sweep_unheld_embedders([lazy, off]) == ["vega-granite"]
    assert {"vega-4b", "vega-bge-m3"} <= node.held and node.unloads == ["vega-granite"]
    assert node.runner.puts() == []


def test_a_switch_to_off_drops_it_from_the_set_at_once_and_unloads_nothing(tmp_path, node):
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy", reranker="rerank-bge")
    shared = _with(_collection(tmp_path, "gutenberg-plates"), "vega-4b-plates", "lazy")
    con = db.connect(c)
    db.apply_desired_embed_mode(con, c)
    _beat([c, shared])
    assert "vega-4b" in node.held
    entry = resident._last[(SERVER, "vega-4b")]
    scope = db.get_scope(con)
    scope["embed_mode"] = "off"                                    # the Library's switch
    db.set_scope(con, scope)
    assert worker._release_embedder(c, con, reason="the switch") == ["vega-4b"]
    assert (SERVER, "vega-4b") not in resident._last, "dropped now, not at the next beat"
    assert "vega-4b" in node.held and node.unloads == [], "and left loaded"
    assert (SERVER, "rerank-bge") in resident._last, "its reranker is not its embed's"
    assert (SERVER, "vega-4b-plates") in resident._last, "another collection's encoder stays"
    # a load behind the last beat does not make it once it was dropped
    node.held.discard("vega-4b")
    node.warmed.clear()
    resident._warm_all([entry], lambda: False)
    assert node.warmed == []
    msgs = [r[0] for r in con.execute("SELECT message FROM events WHERE stage='control'")]
    assert any("vega-4b no longer preloaded on the switch" in m for m in msgs)
    con.close()


def test_with_the_node_paused_nothing_is_preloaded_and_nothing_unloaded(tmp_path, node):
    """München, 2026-11-08: the worker sweeps embedders while paused. Paused,
    nothing is preloaded, and the sweep unloads nothing a held collection
    names — no active unload; an orphan still goes."""
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    full = _with(_collection(tmp_path, "wikidata"), "vega-nemotron", "all")
    node.held |= {"vega-4b", "vega-nemotron", "vega-granite"}
    _beat([lazy, full])
    node_state.set_worker_state("paused")
    assert _beat([lazy, full])["set"] == []
    assert worker._sweep_unheld_embedders([lazy, full]) == ["vega-granite"]
    assert {"vega-4b", "vega-nemotron"} <= node.held


def test_switched_off_nothing_is_preloaded_and_nothing_unloaded(tmp_path, node):
    node.platform.write_text('[models]\nkeep_ask_resident = false\n')
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    node.held.add("vega-4b")
    _beat([lazy])
    assert node.runner.puts() == [] and node.warmed == []
    assert worker._release_embedder(lazy) == [] and worker._sweep_unheld_embedders([lazy]) == []
    assert node.unloads == [] and "vega-4b" in node.held


def test_the_keeper_beats_at_once_reads_the_library_s_mode_and_forgets_at_stop(tmp_path, node,
                                                                               monkeypatch):
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "all")
    con = db.connect(c)
    scope = db.get_scope(con)
    scope["embed_mode"] = "off"                                    # the Library's override
    db.set_scope(con, scope)
    con.close()
    monkeypatch.setattr(worker.collections_mod, "load_all", lambda: [c])
    monkeypatch.setattr(worker, "_live_collections", {"gutenberg": c})
    beats: list[float] = []
    set_at_beat: list[set] = []

    class Stop:
        def wait(self, s):
            beats.append(s)
            set_at_beat.append(set(resident._last))
            return True

        def is_set(self):
            return bool(beats)
    monkeypatch.setattr(worker, "_stop", Stop())
    worker._resident_keeper()
    resident.wait_warm(5)
    assert (SERVER, "vega-4b") not in set_at_beat[0], "the Library's off, not the file's all"
    assert (SERVER, "louise-chat") in set_at_beat[0], "the first beat ran before the first wait"
    assert resident._last == {}, "forgotten at stop"
    assert node.runner.puts() == [], "nothing pinned, nothing to give back"


def test_node_resident_show(tmp_path, node, monkeypatch):
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    monkeypatch.setattr(resident, "served_now", lambda: ([c], {"wikipedia": "paused (`node pause`)"}))
    node.held.add("louise-chat")
    _beat([c])
    with model_load._lock:
        model_load._sizes[(SERVER, "louise-chat")] = 5_791 * MIB
    out = resident.show()
    assert out["switches"] == {"keep_ask_resident": True, "preload_speech": True}
    assert out["mode"] == "running" and out["preloads"] is True
    assert "preloaded, not pinned" in out["rule"]
    rows = {r["model"]: r for r in out["models"]}
    assert rows["louise-chat"]["loaded"] is True and rows["louise-chat"]["size_mib"] == 5_791
    assert rows["louise-chat"]["pinned"] is False and rows["louise-chat"]["holders"] == []
    assert rows["vega-4b"]["collections"] == ["gutenberg"] and rows["vega-4b"]["size_mib"] is None
    assert out["not_served"] == {"wikipedia": "paused (`node pause`)"}


# ---- the budget (2026-11-08: the set itself was larger than the card holds) ----------
#
# Measured by the node, 03:25: every collection on on-demand, every encoder
# preloaded — 11 models, 28.3–28.5 GB on the 32.7 GB B70 — and the compute
# buffers took the card past the reclaimer's 0.95 line: chat, silero,
# omnivoice and vega-medcpt-query evicted while idle. The room check passed
# each load; the SET did not fit. So the set is fitted to threshold × total −
# a MEASURED working margin, by priority, and the rest loads on demand.

def _system(monkeypatch, node, sizes_mib: dict[str, int]):
    """LocalAI's `/system` as it is: every model it holds, with its backend's
    pid and `size_vram` (0 for one on the CPU) — read by the real reader."""
    from app import httpc
    inner = httpc.get
    pids: dict[str, int] = {}
    monkeypatch.setattr(model_load, "_fetch_system", REAL_FETCH)

    def get(url, **kw):
        if url.endswith("/system"):
            rows = [{"id": m, "size_vram": sizes_mib.get(m, 0) * MIB,
                     "process": {"pid": pids.setdefault(m, 100 + len(pids))}} for m in sorted(node.held)]
            return SimpleNamespace(status_code=200, json=lambda: {"loaded_models": rows})
        return inner(url, **kw)
    monkeypatch.setattr(httpc, "get", get)


def _known(**sizes_mib: int) -> None:
    """Sizes this process remembers (`model_load.size_vram`), as after each
    model was seen loaded once."""
    with model_load._lock:
        for m, v in sizes_mib.items():
            model_load._sizes[(SERVER, m.replace("_", "-"))] = v * MIB


def _r(model, *roles, collections=()):
    return resident.Resident(SERVER, model, tuple(roles), tuple(collections))


def test_the_margin_is_measured_persisted_and_the_default_before_any_measurement(tmp_path, node, monkeypatch):
    """The card's count minus what LocalAI's models hold (`size_vram`), its
    peak over the last 6 h, in state/resident-budget.json so a restart keeps
    it; 3.0 GiB while nothing is measured in the window; no sample while the
    keeper's own load runs."""
    clock = {"t": 1_800_000_000.0}
    monkeypatch.setattr(resident, "_wall", lambda: clock["t"])
    assert resident.read_memory().margin(clock["t"]) == (resident.DEFAULT_MARGIN, False)
    assert resident.DEFAULT_MARGIN == 3_072 * MIB, "the night: 31.36 GB read − 28.4 GB at rest"
    node.held |= {"louise-chat", "louise-translate"}
    _system(monkeypatch, node, {"louise-chat": 5_791, "louise-translate": 7_879})
    card = _card(monkeypatch, 32_656, 5_791 + 7_879 + 300)          # 300 MiB no model holds
    _beat([])
    mem = resident.read_memory()
    assert resident.memory_path() == config.louise_root() / "state" / "resident-budget.json"
    assert mem.margin(clock["t"]) == (300 * MIB, True)
    assert mem.total == 32_656 * MIB
    assert mem.sizes == {(SERVER, "louise-chat"): 5_791 * MIB, (SERVER, "louise-translate"): 7_879 * MIB}
    resident.reset()                                                 # a restart: the file, not the process
    model_load.reset()
    assert resident._plan_now([_r("louise-chat", "chat")]).margin == 300 * MIB
    # the peak of the window: a lower sample later keeps it, a higher one raises it
    clock["t"] += 1_200
    card["used"] = 5_791 + 7_879 + 120
    _beat([])
    assert resident.read_memory().margin(clock["t"]) == (300 * MIB, True)
    clock["t"] += 1_200
    card["used"] = 5_791 + 7_879 + 1_900                             # a search's load, not yet listed
    _beat([])
    assert resident.read_memory().margin(clock["t"]) == (1_900 * MIB, True)
    # the keeper's own load is in the budget already: no sample while it runs
    clock["t"] += 1_200
    card["used"] = 5_791 + 7_879 + 6_000
    monkeypatch.setattr(resident, "_warm_thread", SimpleNamespace(is_alive=lambda: True))
    resident.beat([], embed_mode=_mode)
    monkeypatch.setattr(resident, "_warm_thread", None)
    assert resident.read_memory().margin(clock["t"]) == (1_900 * MIB, True)
    # past the window, nothing measured: the default again
    clock["t"] += resident.MARGIN_WINDOW_S + 1
    monkeypatch.setattr(gpu, "_get_json", lambda url: (_ for _ in ()).throw(ConnectionError("probe gone")))
    _beat([])
    mem = resident.read_memory()
    assert mem.margin(clock["t"]) == (resident.DEFAULT_MARGIN, False) and mem.peaks == {}
    assert mem.total == 32_656 * MIB, "the card's total is kept: an unread probe stops the samples only"


def test_a_runner_that_gives_no_sizes_gives_no_sample(tmp_path, node, monkeypatch):
    """The margin is what no loaded model holds; without LocalAI's sizes it
    cannot be told, and the default stands."""
    node.held.add("louise-chat")
    _card(monkeypatch, 32_656, 20_000)                               # the fixture's runner: ids only
    _beat([])
    assert resident.read_memory().peaks == {}
    assert resident._plan_now([_r("louise-chat", "chat")]).margin == resident.DEFAULT_MARGIN


def test_the_order_is_tier_then_recency_then_size_and_tier_one_is_never_pushed_out():
    """chat, rerankers, speech (TTS, then STT and VAD); query encoders; the
    document encoders that differ from their query encoder; MADLAD last.
    Within a tier the most recently used first, the never used after them,
    smaller first. First-fit: a lower tier only takes what the ones above
    left; one on the CPU costs nothing and always fits."""
    now = 1_800_000_000.0
    want = [_r("louise-translate", "translation"),
            _r("vega-4b", "query encoder", "encoder", collections=("gutenberg",)),
            _r("vega-medcpt-query", "query encoder", collections=("pubmed",)),
            _r("vega-medcpt-article", "encoder", collections=("pubmed",)),
            _r("vega-bge-m3", "query encoder", "encoder", collections=("wikipedia",)),
            _r("vega-tiny", "query encoder", collections=("eurlex",)),
            _r("rerank-bge", "reranker"), _r("rerank-medcpt", "reranker"),
            _r("louise-chat", "chat"),
            _r("whisper", "speech to text"), _r("silero", "voice activity"), _r("omnivoice", "text to speech")]
    mib = {"louise-translate": 7_879, "vega-4b": 9_900, "vega-medcpt-query": 400, "vega-medcpt-article": 400,
           "vega-bge-m3": 1_200, "vega-tiny": 300, "rerank-bge": 1_664, "rerank-medcpt": 800,
           "louise-chat": 7_441, "whisper": 1_500, "silero": 0, "omnivoice": 2_000}
    mem = resident.Memory(sizes={(SERVER, m): v * MIB for m, v in mib.items()}, total=32_656 * MIB)
    used = {(SERVER, "vega-4b"): now - 3_600, (SERVER, "vega-bge-m3"): now - 600,
            (SERVER, "louise-translate"): now - 60}                   # recent, and still last
    tier1 = 7_441 + 800 + 1_664 + 2_000 + 1_500
    # the budget: tier 1, vega-bge-m3, and 500 MiB more
    margin = int(0.95 * 32_656 * MIB) - (tier1 + 1_200 + 500) * MIB
    mem.peaks[int(now // resident.MARGIN_BUCKET_S * resident.MARGIN_BUCKET_S)] = margin
    p = resident.plan(want, mem, threshold=0.95, used=used, now=now)
    assert [x.resident.model for x in p.placed] == [
        "louise-chat", "rerank-medcpt", "rerank-bge", "omnivoice", "whisper", "silero",
        "vega-bge-m3", "vega-4b", "vega-tiny", "vega-medcpt-query", "vega-medcpt-article", "louise-translate"]
    assert [x.tier for x in p.placed] == [1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 4]
    fits = {x.resident.model: x.fits for x in p.placed}
    assert all(fits[m] for m in ("louise-chat", "rerank-medcpt", "rerank-bge", "omnivoice", "whisper",
                                 "silero")), "tier 1 first: nothing below displaces it"
    assert fits["vega-bge-m3"] and not fits["vega-4b"], "the recently used 4B does not fit"
    assert fits["vega-tiny"] and fits["vega-medcpt-query"] is False, "first-fit: 300 of the 500 left"
    assert not fits["vega-medcpt-article"] and not fits["louise-translate"]
    four_b = next(x for x in p.placed if x.resident.model == "vega-4b")
    assert four_b.detail.startswith("rank 8 of 12, tier 2 of 4: query encoders, last used 1 h ago, the least "
                                    "recently used of its tier; the models placed above it take")
    assert four_b.why == f"not preloaded — {p.says()}; {four_b.detail}"
    # the 4B used most recently of all: still never above tier 1
    used[(SERVER, "vega-4b")] = now
    p = resident.plan(want, mem, threshold=0.95, used=used, now=now)
    assert [x.resident.model for x in p.placed][:7] == [
        "louise-chat", "rerank-medcpt", "rerank-bge", "omnivoice", "whisper", "silero", "vega-4b"]
    assert all(x.fits for x in p.placed if x.tier == 1)
    # the CPU's model costs nothing: in, however full the card
    mem.peaks = {k: int(0.95 * 32_656 * MIB) for k in mem.peaks}       # a margin of the whole line
    p = resident.plan(want, mem, threshold=0.95, used=used, now=now)
    assert [x.resident.model for x in p.placed if x.fits] == ["silero"]
    # never seen: counted at the median of the set's known sizes
    del mem.sizes[(SERVER, "vega-medcpt-query")]
    mem.peaks = {}
    p = resident.plan(want, mem, threshold=0.95, used=used, now=now)
    q = next(x for x in p.placed if x.resident.model == "vega-medcpt-query")
    assert q.size is None and q.counted == p.median and p.median == (1_500 + 1_664) * MIB // 2


def test_a_set_larger_than_the_budget_preloads_only_the_top_part_and_says_so_once(tmp_path, node, monkeypatch):
    g = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy", reranker="rerank-bge")
    pm = _with(_collection(tmp_path, "pubmed"), "vega-medcpt-article", "lazy", query_model="vega-medcpt-query")
    _known(louise_chat=6_000, rerank_bge=1_600, vega_4b=18_000, vega_medcpt_query=1_100,
           vega_medcpt_article=4_000, louise_translate=7_900)
    node_state.touch_encoder(SERVER, "vega-medcpt-query", now=resident._wall() - 120)
    node_state.touch_encoder(SERVER, "vega-4b", now=resident._wall() - 3_600)
    _system(monkeypatch, node, {})
    _card(monkeypatch, 32_656, 1_000)                                # nothing loaded; 1,000 MiB elsewhere
    out = _beat([g, pm])
    assert sorted(node.warmed) == ["louise-chat", "rerank-bge", "vega-4b", "vega-medcpt-query"]
    assert out["out_of_budget"] == [(SERVER, "louise-translate"), (SERVER, "vega-medcpt-article")]
    assert out["budget"] // MIB == 30_023, "0.95 × 32,656 − 1,000 measured"
    fell = [t for _, t in node.said if "not preloaded —" in t]
    assert len(fell) == 1
    assert fell[0].startswith("resident: not preloaded — the budget is 30,023 MiB (the 0.95 line 31,023 MiB "
                              "− margin 1,000 MiB, measured over 6 h): ")
    assert ("vega-medcpt-article — encoder (pubmed) — rank 5 of 6, tier 3 of 4: document encoders, never "
            "used here; the models placed above it take 26,700 MiB and it needs 4,000 MiB — loaded at its "
            "first request, then left to the reclaimer's LRU") in fell[0]
    assert "louise-translate — translation — rank 6 of 6, tier 4 of 4: translation" in fell[0]
    node.warmed.clear()
    _beat([g, pm])
    assert node.warmed == [] and len([t for _, t in node.said if "not preloaded —" in t]) == 1, "said once"
    # the plan is made again before each load: one out of the budget is not loaded behind a beat
    resident._warm_all([resident._last[(SERVER, "louise-translate")]], lambda: False)
    assert node.warmed == []
    # room again (the 4B's collection set to off): back in, said, and loaded
    g_off = replace(g, embed_mode_default="off")
    _beat([g_off, pm])
    assert "louise-translate" in node.warmed and "vega-medcpt-article" in node.warmed
    assert any("back in the budget" in t and "louise-translate" in t for _, t in node.said)


def test_the_budget_never_unloads_anything(tmp_path, node, monkeypatch):
    """"nothing is evicted only because it's idle, only if space is claimed":
    a set loaded beyond the budget stays loaded; the budget decides only what
    is preloaded."""
    g = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy", reranker="rerank-bge")
    sizes = {"louise-chat": 6_000, "rerank-bge": 1_600, "vega-4b": 18_000, "louise-translate": 7_900}
    node.held |= set(sizes)
    _system(monkeypatch, node, sizes)
    _card(monkeypatch, 32_656, sum(sizes.values()) + 500)
    out = _beat([g])
    assert (SERVER, "louise-translate") in out["out_of_budget"]
    assert node.unloads == [] and set(sizes) <= node.held and node.warmed == []
    assert node.pin_calls == [] and node.runner.puts() == []
    src = inspect.getsource(resident)
    assert "model_load.unload(" not in src and "backend/shutdown" not in src


def test_a_model_out_of_the_budget_is_loaded_at_its_first_request(tmp_path, node, monkeypatch):
    """Not preloaded is not refused: the request's own warm loads it, as an
    `off` collection's encoder is, and the next beat leaves it there."""
    _known(louise_chat=6_000, louise_translate=7_900)
    _system(monkeypatch, node, {"louise-chat": 6_000, "louise-translate": 7_900})
    card = _card(monkeypatch, 32_656, 24_000)                        # 24,000 MiB no model holds
    out = _beat([])
    assert out["out_of_budget"] == [(SERVER, "louise-translate")] and node.warmed == ["louise-chat"]
    card["used"] = 6_000 + 7_900
    model_load.ensure_loaded(SERVER, "louise-translate", lambda bound_s: node.held.add("louise-translate"),
                             caller="translation", max_age_s=0.0)
    assert "louise-translate" in node.held
    node.warmed.clear()
    _beat([])
    assert "louise-translate" in node.held and node.unloads == [] and node.warmed == []


def test_a_back_off_names_the_model_s_tier(tmp_path, node, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(resident, "_now", lambda: clock["t"])
    node.held.add("louise-translate")
    _beat([])
    node.held.discard("louise-chat")                                 # the reclaimer evicted it
    clock["t"] = 60.0
    _beat([])
    said = [t for _, t in node.said if "back-off" in t]
    assert len(said) == 1 and said[0].startswith("resident: louise-chat — chat — off the card again")
    assert said[0].endswith("(back-off 1, at most 2 h; tier 1 of 4: chat, rerankers and speech)")


def test_the_translation_model_s_use_is_recorded_where_the_encoders_are(tmp_path, node, monkeypatch):
    """MADLAD's recency, from the request: the encoders' row (node_state
    `encoder_use`), local, nothing new drawn from the network."""
    monkeypatch.setattr(translate, "shape", lambda timeout=10.0: translate.SHAPE_BATCH)
    monkeypatch.setattr(translate, "_completion",
                        lambda request, timeout, headers=None, max_tokens=None:
                        {"translations": [f"{it['to']}:{it['text']}" for it in request["items"]]})
    translate.reset()
    assert (SERVER, "louise-translate") not in node_state.encoder_last_used()
    assert translate.translate([("castle", "de")]) == ["de:castle"]
    assert (SERVER, "louise-translate") in node_state.encoder_last_used()
    p = resident._plan_now([_r("louise-translate", "translation")])
    assert p.placed[0].last_used is not None


def test_node_resident_show_the_budget(tmp_path, node, monkeypatch):
    """Per model its rank, tier, size, what the budget counts, in the budget
    or not and why; the line, the margin (measured or the default) and the
    budget. Read from the worker's file; nothing written."""
    g = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy", reranker="rerank-bge")
    monkeypatch.setattr(resident, "served_now", lambda: ([g], {}))
    _card(monkeypatch, 32_656, 1_000)
    out = resident.show()
    assert out["budget"]["margin"] == "the default 3,072 MiB: nothing measured in the last 6 h"
    assert out["budget"]["line_mib"] == 31_023 and out["budget"]["budget_mib"] == 27_951
    assert out["budget"]["penalty_mib"] == 0 and out["budget"]["penalty"].startswith("none")
    assert not resident.memory_path().exists(), "show writes nothing"
    _known(louise_chat=6_000, rerank_bge=1_600, vega_4b=18_000, louise_translate=7_900)
    _system(monkeypatch, node, {})
    _beat([g])
    model_load.reset()                                               # the command's own process
    out = resident.show()
    b = out["budget"]
    assert b["margin_mib"] == 1_000 and b["margin"].startswith("measured: the peak of 1 sample period(s)")
    assert b["budget_mib"] == 30_023 and b["taken_mib"] == 6_000 + 1_600 + 18_000
    assert b["says"] == ("the budget is 30,023 MiB (the 0.95 line 31,023 MiB − margin 1,000 MiB, "
                         "measured over 6 h)")
    rows = out["models"]
    assert [(r["rank"], r["model"], r["tier"], r["size_mib"], r["in_budget"]) for r in rows] == [
        (1, "louise-chat", 1, 6_000, True), (2, "rerank-bge", 1, 1_600, True),
        (3, "vega-4b", 2, 18_000, True), (4, "louise-translate", 4, 7_900, False)]
    assert rows[0]["why"] == "in the budget: 6,000 of 30,023 MiB taken with it"
    assert rows[3]["why"].startswith("not preloaded — the budget is 30,023 MiB")
    assert rows[3]["counted_mib"] == 7_900 and rows[3]["tier_says"] == "translation"


def test_a_beat_s_reads_do_not_grow_with_the_set(tmp_path, node, monkeypatch):
    """A COST test (docs/notes/scheduling.md §6, 7): with the set loaded, a
    beat reads the probe once, LocalAI's settings once per server, `/system`
    three times per server (the TTL'd check, and the margin's two around the
    card) — for two collections as for twenty."""
    from app import httpc
    counts: dict[int, dict[str, int]] = {}
    plain = httpc.get                                                # the fixture's runner
    for n in (2, 20):
        monkeypatch.setattr(httpc, "get", plain)
        cs = [_with(_collection(tmp_path, f"c{n}-{i}"), f"vega-{n}-{i}", "lazy") for i in range(n)]
        sizes = {f"vega-{n}-{i}": 100 for i in range(n)} | {"louise-chat": 6_000, "louise-translate": 7_900}
        node.held |= set(sizes)
        _system(monkeypatch, node, sizes)
        _card(monkeypatch, 32_656, sum(sizes.values()) + 200)
        _beat(cs)                                                    # the sizes seen, the file written
        seen: dict[str, int] = {}

        def count(what: str) -> None:
            seen[what] = seen.get(what, 0) + 1
        get, probe = httpc.get, gpu._get_json
        monkeypatch.setattr(httpc, "get", lambda url, _g=get, **kw: (count(url.rsplit("/", 1)[1]),
                                                                    _g(url, **kw))[1])
        monkeypatch.setattr(gpu, "_get_json", lambda url, _p=probe: (count("gpu"), _p(url))[1])
        out = _beat(cs)
        assert out["warming"] == [] and out["out_of_budget"] == []
        counts[n] = seen
    assert counts[2] == counts[20] == {"system": 3, "gpu": 1, "settings": 1}


def test_the_margin_is_the_growth_over_the_at_rest_sizes(tmp_path, node, monkeypatch):
    """The budget counts each model AT REST — the fewest bytes seen, as its
    load left it — and the margin is what the card holds beyond the at-rest
    sizes of what is loaded: a request's compute buffers, a load in flight.
    Counted against the LARGEST sizes, the growth hid inside them and the
    margin read ~0 (the first build of this budget)."""
    sizes = {"louise-chat": 6_000, "louise-translate": 7_900}
    node.held |= set(sizes)
    _system(monkeypatch, node, sizes)
    card = _card(monkeypatch, 32_656, 13_900)                         # at rest: nothing beyond them
    _beat([])
    mem = resident.read_memory()
    assert mem.rest == {(SERVER, "louise-chat"): 6_000 * MIB, (SERVER, "louise-translate"): 7_900 * MIB}
    assert mem.margin(resident._wall()) == (0, True)
    sizes["louise-chat"] = 8_500                                      # a question grew the chat's buffers
    card["used"] = 8_500 + 7_900
    _beat([])
    mem = resident.read_memory()
    assert mem.sizes[(SERVER, "louise-chat")] == 8_500 * MIB, "the largest, for the room check"
    assert mem.rest[(SERVER, "louise-chat")] == 6_000 * MIB, "at rest, for the budget"
    assert mem.margin(resident._wall()) == (2_500 * MIB, True), "the growth is the margin"
    p = resident._plan_now([_r("louise-chat", "chat")])
    assert p.placed[0].counted == 6_000 * MIB and p.placed[0].largest == 8_500 * MIB


def test_the_night_s_set_drops_madlad_and_keeps_the_rest():
    """2026-11-08, 03:25: 11 models, ~28.4 GB at rest on the B70 (32,656 MiB,
    the 0.95 line 31,023 MiB). With the default margin, 3.0 GiB of growth,
    the lowest tier goes and everything else stays."""
    now = 1_800_000_000.0
    mib = {"louise-chat": 7_441, "rerank-bge": 1_664, "rerank-medcpt": 700, "omnivoice": 1_900,
           "whisper": 1_100, "silero": 0, "vega-4b": 5_616, "vega-bge-m3": 1_200, "vega-medcpt-query": 450,
           "vega-medcpt-article": 450, "louise-translate": 7_879}
    assert sum(mib.values()) == 28_400
    want = [_r("louise-translate", "translation"), _r("vega-4b", "query encoder", "encoder"),
            _r("vega-bge-m3", "query encoder", "encoder"), _r("vega-medcpt-query", "query encoder"),
            _r("vega-medcpt-article", "encoder"), _r("rerank-bge", "reranker"), _r("rerank-medcpt", "reranker"),
            _r("louise-chat", "chat"), _r("whisper", "speech to text"), _r("silero", "voice activity"),
            _r("omnivoice", "text to speech")]
    mem = resident.Memory(sizes={(SERVER, m): v * MIB for m, v in mib.items()},
                          rest={(SERVER, m): v * MIB for m, v in mib.items() if v}, total=32_656 * MIB)
    p = resident.plan(want, mem, threshold=0.95, used={}, now=now)
    assert not p.measured and p.margin == 3_072 * MIB and p.budget // MIB == 27_951
    assert [x.resident.model for x in p.placed if not x.fits] == ["louise-translate"]
    assert p.placed[0].resident.model == "louise-chat"


def test_an_eviction_shrinks_the_budget_and_the_penalty_halves_every_two_hours(tmp_path, node, monkeypatch):
    """THE FEEDBACK (AIMD, RFC 5681's shape): a beat can miss a 500 ms spike
    the reclaimer sees, so the reclaimer's eviction of a preloaded model —
    off the card again within 10 min of its load — adds that model's at-rest
    size to a penalty on the margin at once; the penalty halves after each
    2 h without another. Kept in the file, said, and shown."""
    clock = {"mono": 0.0}
    wall = {"t": 1_800_000_000.0}
    monkeypatch.setattr(resident, "_now", lambda: clock["mono"])
    monkeypatch.setattr(resident, "_wall", lambda: wall["t"])
    sizes = {"louise-chat": 6_000, "louise-translate": 7_900}
    _system(monkeypatch, node, sizes)
    card = _card(monkeypatch, 32_656, 1_000)
    _beat([])                                                         # both loaded by the keeper
    assert {"louise-chat", "louise-translate"} <= node.held
    _beat([])                                                         # their at-rest sizes seen
    assert resident.read_memory().penalty == 0
    node.held.discard("louise-translate")                             # the reclaimer took it
    clock["mono"] += 180
    card["used"] = 6_000 + 1_000
    out = _beat([])
    mem = resident.read_memory()
    assert mem.penalty == 7_900 * MIB and mem.penalty_at == wall["t"]
    said = [t for _, t in node.said if "the budget shrank by" in t]
    assert said == ["resident: the budget shrank by 7,900 MiB: louise-translate was evicted 3 min after its "
                    "load (tier 4 of 4: translation) — the penalty on the margin is 7,900 MiB, halved after "
                    "each 2 h without another such eviction"]
    assert out["budget"] == int(0.95 * 32_656 * MIB) - 1_000 * MIB - 7_900 * MIB
    assert resident._plan_now([_r("louise-chat", "chat")]).says().endswith("− 7,900 MiB for evictions)")
    resident.reset()                                                  # a restart keeps it
    assert resident._plan_now([_r("louise-chat", "chat")]).penalty == 7_900 * MIB
    wall["t"] += 2 * 3600
    assert resident._plan_now([_r("louise-chat", "chat")]).penalty == 3_950 * MIB
    wall["t"] += 2 * 3600
    assert resident._plan_now([_r("louise-chat", "chat")]).penalty == 1_975 * MIB
    # another eviction adds to what is left of it, and starts the halving again
    node.held.discard("louise-chat")
    clock["mono"] += 60
    _beat([])                                                         # the keeper loads it again
    assert "louise-chat" in node.held
    node.held.discard("louise-chat")                                  # and the reclaimer takes it again
    clock["mono"] += 60
    _beat([])
    mem = resident.read_memory()
    assert mem.penalty == (1_975 + 6_000) * MIB and mem.penalty_at == wall["t"]
    monkeypatch.setattr(resident, "served_now", lambda: ([], {}))
    shown = resident.show()["budget"]
    assert shown["penalty_mib"] == 7_975 and "halved after each 2 h without another" in shown["penalty"]


def test_the_loads_run_in_priority_order_chat_first(tmp_path, node, monkeypatch):
    """After a boot the question path is warm first: tier 1 in its order,
    then the query encoders, the document encoders, MADLAD last — whatever
    the order a question meets them."""
    node.platform.write_text('[speech]\nstt_model = "whisper"\nvad_model = "silero"\n'
                             'tts_model = "omnivoice"\n')
    pm = _with(_collection(tmp_path, "pubmed"), "vega-medcpt-article", "lazy", query_model="vega-medcpt-query",
               reranker="rerank-medcpt")
    _beat([pm])
    assert node.warmed == ["louise-chat", "rerank-medcpt", "omnivoice", "whisper", "silero",
                           "vega-medcpt-query", "vega-medcpt-article", "louise-translate"]


def test_a_model_partly_off_the_card_is_not_at_rest_and_the_old_margin_goes(tmp_path, node, monkeypatch):
    """München, 2026-11-09: xe moved an overcommitted card's buffers to host
    RAM and `size_vram` read what was left — Nemotron 31 of 4,404 MiB, MADLAD
    2,671 of 8,005, omnivoice 1,003 of 2,676 — taken as their AT-REST sizes.
    The budget (12,460 MiB) then held MADLAD beside chat, and the margin read
    the missing bytes as 12.5–17.5 GB of growth. A reading under half the
    largest is no rest; the file's are dropped, and its old margin with them."""
    assert resident.REST_FLOOR == model_load.REST_FLOOR
    real = {"louise-chat": (5_790, 7_288), "omnivoice": (1_003, 2_676), "vega-nemotron": (31, 4_404),
            "louise-translate": (2_671, 8_005), "rerank-bge": (866, 1_027)}
    now = resident._wall()
    resident._write_memory(resident.Memory(
        sizes={(SERVER, m): big * MIB for m, (_r_, big) in real.items()},
        rest={(SERVER, m): rest * MIB for m, (rest, _b) in real.items()},
        total=32_656 * MIB, peaks={int(now // 600 * 600): 16_657 * MIB}))
    raw = json.loads(resident.memory_path().read_text())
    raw.pop("margin_basis")                                          # the file the node holds today
    resident.memory_path().write_text(json.dumps(raw))
    mem = resident.read_memory()
    assert mem.peaks == {}, "a margin measured against partial readings is that error, not growth"
    want = [_r("louise-chat", "chat"), _r("omnivoice", "text to speech"), _r("rerank-bge", "reranker"),
            _r("vega-nemotron", "query encoder", "encoder"), _r("louise-translate", "translation")]
    p = resident.plan(want, mem, threshold=0.95, used={}, now=now)
    counted = {x.resident.model: x.counted // MIB for x in p.placed}
    assert counted == {"louise-chat": 5_790, "omnivoice": 2_676, "rerank-bge": 866, "vega-nemotron": 4_404,
                       "louise-translate": 8_005}, "a whole reading as at rest, a partial one at the largest"
    # the beat drops the file's partial rests and keeps the whole ones
    node.held |= {"louise-chat"}
    _system(monkeypatch, node, {"louise-chat": 7_000})
    _card(monkeypatch, 32_656, 7_400)
    _beat([])
    rest = resident.read_memory().rest
    assert rest[(SERVER, "louise-chat")] == 5_790 * MIB and rest[(SERVER, "rerank-bge")] == 866 * MIB
    assert not {(SERVER, m) for m in ("omnivoice", "vega-nemotron", "louise-translate")} & set(rest)
    assert json.loads(resident.memory_path().read_text())["margin_basis"] == resident.MARGIN_BASIS


def test_a_partial_reading_never_becomes_the_processes_at_rest_size(monkeypatch, node):
    _system(monkeypatch, node, {})
    with model_load._lock:
        model_load._sizes[(SERVER, "m")] = 4_404 * MIB
        model_load._rest[(SERVER, "m")] = 31 * MIB                   # recorded before the floor
    node.held.add("m")
    _system(monkeypatch, node, {"m": 1_000})
    model_load.loaded_models(SERVER, max_age_s=0.0)
    assert model_load.rest_vram(SERVER, "m") is None, "partial, and the old partial one dropped"
    _system(monkeypatch, node, {"m": 4_000})
    model_load.loaded_models(SERVER, max_age_s=0.0)
    assert model_load.rest_vram(SERVER, "m") == 4_000 * MIB
