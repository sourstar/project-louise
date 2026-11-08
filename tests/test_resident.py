"""Every model Ask uses kept resident on the card — a TRIAL (operator,
2026-11-08, option A: "keep every model Ask uses resident on the card"; "The
load all is a test"; "If embedding is set on all or on demand preload the
model. Preload the speech related models by default."; app/resident.py).

The set is the system's: the chat, translation and reranking models, the
encoders of every served collection whose embed is on-demand, all or p2p,
and the speech models. One holder pins them through app/model_pins.py; what
leaves the set is given back; the node's mode wins (paused or seed-only
releases, the worker then unloads as before); the switches off are the node
as it was."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import (collections as collections_mod, config, cpu_limit, db, embeddings, llm, model_load,
                 model_pins, node_state, resident, retrieval, translate, worker)
from tests.test_config_tiers_embed_mode import _collection
from tests.test_model_pins import REAL_APPLIES, Runner

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
    monkeypatch.setattr(httpc, "get", r.get)
    monkeypatch.setattr(httpc, "put", r.put)
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
                          platform=config_dir / "platform.toml")
    resident.wait_warm(5)
    resident.reset()
    resident.set_log(model_pins.node_event)
    model_load.reset()


def _beat(served, **kw):
    out = resident.beat(served, embed_mode=kw.pop("embed_mode", _mode), **kw)
    resident.wait_warm(5)
    model_load.reset()
    return out


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


# ---- holding -----------------------------------------------------------------------

def test_a_beat_pins_the_set_loads_what_is_missing_and_says_so(tmp_path, node):
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    node.held.add("louise-chat")
    out = _beat([lazy])
    assert set(node.runner.pinned) == {"louise-translate", "vega-4b", "louise-chat"}
    assert all(node.runner.pinned.values())
    assert model_pins.held_by(resident.HOLDER) == {(SERVER, m) for m in node.runner.pinned}
    assert sorted(node.warmed) == ["louise-translate", "vega-4b"], "the loaded one is not loaded again"
    assert out["holds"] and set(out["warming"]) == {(SERVER, "louise-translate"), (SERVER, "vega-4b")}
    text = " ".join(t for _, t in node.said)
    assert "holding 3 model(s) for Ask" in text and "vega-4b — query encoder, encoder (gutenberg)" in text


def test_a_mode_change_takes_effect_at_the_next_beat(tmp_path, node):
    """The Library sets gutenberg to off: given back at the next beat, its
    state before restored; back to on-demand: held and loaded again."""
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    modes = {"gutenberg": "lazy"}
    _beat([c], embed_mode=lambda c: modes[c.name])
    assert node.runner.pinned.get("vega-4b") is True
    modes["gutenberg"] = "off"
    _beat([c], embed_mode=lambda c: modes[c.name])
    assert node.runner.pinned["vega-4b"] is False, "unpinned: its state before"
    assert (SERVER, "vega-4b") not in model_pins.held_by(resident.HOLDER)
    assert node.runner.pinned["louise-chat"] is True, "the rest stays held"
    assert any("released vega-4b" in t for _, t in node.said)
    node.held.discard("vega-4b")
    for mode in ("all", "p2p", "lazy"):
        modes["gutenberg"] = mode
        _beat([c], embed_mode=lambda c: modes[c.name])
        assert node.runner.pinned["vega-4b"] is True and "vega-4b" in node.held, mode


def test_a_model_pinned_before_stays_pinned_when_released(tmp_path, node):
    node.runner.pinned["louise-chat"] = True               # the operator pinned it by hand
    _beat([])
    node_state.set_worker_state("paused")
    _beat([])
    assert node.runner.pinned["louise-chat"] is True and "louise-chat" not in node.runner.puts()


@pytest.mark.parametrize("mode", ["paused", "seed-only"])
def test_paused_or_seed_only_releases_the_holder_and_running_holds_it_again(tmp_path, node, mode):
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    _beat([c])
    assert model_pins.held_by(resident.HOLDER)
    node_state.set_worker_state(mode)
    node.held.clear()
    node.warmed.clear()
    out = _beat([c])
    assert not out["holds"] and model_pins.held_by(resident.HOLDER) == set()
    assert not any(node.runner.pinned.values()), "every pin back to its state before"
    assert node.warmed == [], "nothing loaded while the operator has the GPU"
    assert resident.kept() == frozenset()
    assert any(f"the node is {mode}" in t for _, t in node.said)
    node_state.set_worker_state("running")
    _beat([c])
    assert all(node.runner.pinned.values()) and "vega-4b" in node.warmed


def test_acquire_only_holds_it_and_a_profile_s_seed_only_releases_it(tmp_path, node, monkeypatch):
    """acquire-only embeds nothing for the corpus but serves every search;
    a profile never limits a query in acquire-only (the quiet windows), and
    the stricter mode wins where a profile says seed-only or paused."""
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    node_state.set_worker_state("acquire-only")
    assert _beat([c])["holds"] and node.runner.pinned["vega-4b"] is True
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.Limit(cores=12, profile="quiet",
                                                                      mode="acquire-only"))
    assert _beat([c])["holds"]
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.Limit(cores=12, profile="quiet",
                                                                      mode="seed-only"))
    out = _beat([c])
    assert not out["holds"] and "profile quiet" in out["why"]
    assert node.runner.pinned["vega-4b"] is False


def test_both_switches_off_hold_nothing_and_load_nothing(tmp_path, node):
    node.platform.write_text('[models]\nkeep_ask_resident = false\npreload_speech = false\n'
                             '[speech]\ntts_model = "omnivoice"\n')
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    out = _beat([c])
    assert out["held"] == [] and node.runner.calls == [] and node.warmed == []
    assert resident.kept() == frozenset() and node.said == []


def test_switched_off_while_held_gives_everything_back(tmp_path, node):
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    _beat([c])
    node.platform.write_text('[models]\nkeep_ask_resident = false\n')
    _beat([c])
    assert model_pins.held_by(resident.HOLDER) == set() and not any(node.runner.pinned.values())
    assert any("keep_ask_resident = false" in t for _, t in node.said)


def test_a_stopping_worker_gives_the_pins_back(tmp_path, node):
    _beat([])
    resident.release()
    assert model_pins.held_by(resident.HOLDER) == set() and not any(node.runner.pinned.values())


def test_a_killed_worker_s_holder_is_dropped_at_the_next_boot(tmp_path, node):
    _beat([])
    model_pins.reconcile(boot=True)
    assert model_pins.held_by(resident.HOLDER) == set()
    _beat([])
    assert model_pins.held_by(resident.HOLDER), "the booted worker holds the set again"


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


def test_speech_models_are_loaded_with_one_request_of_their_kind(tmp_path, node):
    node.platform.write_text('[models]\nkeep_ask_resident = false\n[speech]\nstt_model = "whisper"\n'
                             'vad_model = "silero"\ntts_model = "omnivoice"\n')
    _beat([])
    assert sorted(node.warmed) == ["omnivoice", "silero", "whisper"]
    assert all(node.runner.pinned[m] for m in ("omnivoice", "silero", "whisper"))


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


# ---- the worker's unload paths ----------------------------------------------------

def test_neither_unload_path_unloads_a_resident_model(tmp_path, node):
    """2026-10-14's rule unloaded an on-demand collection's encoder after the
    start-up run check and once idle for IDLE_BEATS; a resident one stays."""
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    node.held |= {"vega-4b", "vega-granite"}
    _beat([lazy])
    assert worker._release_embedder(lazy, others=[lazy]) is False
    assert worker._sweep_unheld_embedders([lazy]) == ["vega-granite"], \
        "a model no held collection names still goes"
    assert "vega-4b" in node.held and node.unloads == ["vega-granite"]


def test_with_the_node_paused_the_sweep_behaves_exactly_as_before(tmp_path, node):
    """München, 2026-11-08: the worker sweeps embedders while paused. Paused,
    the holder is released and the sweep unloads an idle on-demand encoder
    as it always did."""
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    full = _with(_collection(tmp_path, "wikidata"), "vega-nemotron", "all")
    node.held |= {"vega-4b", "vega-nemotron"}
    _beat([lazy, full])
    assert worker._sweep_unheld_embedders([lazy, full]) == [], "running: resident"
    node_state.set_worker_state("paused")
    assert resident.kept() == frozenset(), "paused: nothing protected, even before the next beat"
    model_load.reset()
    assert worker._sweep_unheld_embedders([lazy, full]) == ["vega-4b"]
    assert "vega-nemotron" in node.held and "vega-4b" not in node.held, \
        "the 'all' collection's encoder stays, as before"
    _beat([lazy, full])
    assert model_pins.held_by(resident.HOLDER) == set()


def test_switched_off_the_unload_paths_are_today_s(tmp_path, node):
    node.platform.write_text('[models]\nkeep_ask_resident = false\n')
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    node.held.add("vega-4b")
    _beat([lazy])
    assert worker._release_embedder(lazy, others=[lazy]) is True and node.unloads == ["vega-4b"]


def test_the_keeper_beats_at_once_reads_the_library_s_mode_and_gives_back_at_stop(tmp_path, node,
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
    held_at_beat: list[set] = []

    class Stop:
        def wait(self, s):
            beats.append(s)
            held_at_beat.append(set(model_pins.held_by(resident.HOLDER)))
            return True

        def is_set(self):
            return bool(beats)
    monkeypatch.setattr(worker, "_stop", Stop())
    worker._resident_keeper()
    resident.wait_warm(5)
    assert (SERVER, "vega-4b") not in held_at_beat[0], "the Library's off, not the file's all"
    assert (SERVER, "louise-chat") in held_at_beat[0], "the first beat ran before the first wait"
    assert model_pins.held_by(resident.HOLDER) == set(), "given back at stop"


def test_node_resident_show(tmp_path, node, monkeypatch):
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    monkeypatch.setattr(resident, "served_now", lambda: ([c], {"wikipedia": "paused (`node pause`)"}))
    node.held.add("louise-chat")
    _beat([c])
    out = resident.show()
    assert out["switches"] == {"keep_ask_resident": True, "preload_speech": True}
    assert out["mode"] == "running" and out["holds"] is True
    rows = {r["model"]: r for r in out["models"]}
    assert rows["louise-chat"]["loaded"] is True and rows["louise-chat"]["pinned"] is True
    assert rows["vega-4b"]["held_by_resident"] is True and rows["vega-4b"]["collections"] == ["gutenberg"]
    assert out["not_served"] == {"wikipedia": "paused (`node pause`)"}
