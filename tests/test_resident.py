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
    order: list[str] = []

    def slow(name):
        def warm(*a, **k):
            order.append(name)
            if name == "louise-translate":
                node_state.set_worker_state("paused")          # the operator takes the GPU mid-way
            node.held.add(name)
        return warm
    monkeypatch.setattr(translate, "warm", slow("louise-translate"))
    monkeypatch.setattr(llm, "warm", slow("louise-chat"))
    resident.beat([], embed_mode=_mode)
    assert resident._start_warm([], lambda: False) == [], "a second beat starts no second load"
    resident.wait_warm(5)
    assert order == ["louise-translate"], "the next load is not started once paused"


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
