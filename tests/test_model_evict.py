"""The card's eviction: the oldest REAL use goes first, and Louise chooses it
before LocalAI's reclaimer has to (operator, 2026-11-16: "some embedders have
not been used in a long time; those ones shall always be evincted instead of
the ones that are actively used, either for embedding of for querying";
app/model_evict.py, app/model_use.py).

Measured before (München, 12 h of LocalAI's log, 2026-11-16): the reclaimer
evicted louise-translate-madlad 9 times out of 10, minutes after its use,
while omnivoice, the rerankers, medcpt, qwen3-4b and dar-legal-it, unused for
hours, stayed — because the keeper's warms and the run check's texts count
as use in LocalAI's own LRU."""
from __future__ import annotations

import time
from collections import Counter
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app import (config, embeddings, gpu, model_evict, model_load, model_pins, model_use, node_state, resident)
from app.hold import runs as runs_mod
from tests.test_resident import _card, _system, node  # noqa: F401 — `node` is a fixture

#: the runner's `/system` reader as shipped (conftest replaces it)
REAL_FETCH = model_load._fetch_system
S = "http://localai:8080"
MIB = 1 << 20
NOW = time.time()          # the use rows are real epoch seconds
H = 3600.0


@pytest.fixture(autouse=True)
def _now_is_taken_when_the_test_starts():
    """NOW is the TEST's moment, not the module's collection: in the full
    ordered suite a test runs ~30 min after its module is imported, and the
    eviction's "last used 9.0 h ago", read against the real clock, said 9.4 h
    (München's node run, 2026-11-18)."""
    global NOW
    NOW = time.time()


class Runner:
    """LocalAI as the eviction sees it: `/system` with sizes and CPU, the
    reclaimer's setting, each model's pin, the backend shutdown — every
    request counted; and the GPU probe, whose count follows what is loaded."""

    def __init__(self, models_mib: dict[str, int], *, extra_mib: int = 0, cpu=None, pinned=()):
        self.models = dict(models_mib)
        self.extra = extra_mib
        self.cpu = dict(cpu or {})
        self.pinned = set(pinned)
        self.calls: Counter = Counter()
        self.unloaded: list[str] = []

    def get(self, url, **kw):
        if url.endswith("/system"):
            self.calls["system"] += 1
            rows = [{"id": m, "size_vram": v * MIB, "process": {"pid": 10 + i, "cpu_percent": self.cpu.get(m, 0.0)}}
                    for i, (m, v) in enumerate(sorted(self.models.items()))]
            return SimpleNamespace(status_code=200, json=lambda: {"loaded_models": rows})
        if url.endswith("/api/settings"):
            self.calls["settings"] += 1
            return SimpleNamespace(status_code=200, json=lambda: {"memory_reclaimer_threshold": 0.95})
        if "/api/models/config-json/" in url:
            self.calls["pin"] += 1
            m = url.rsplit("/", 1)[1]
            return SimpleNamespace(status_code=200, json=lambda: {"name": m, "pinned": m in self.pinned})
        raise AssertionError(f"unexpected GET {url}")

    def put(self, url, **kw):
        *_, m, action = url.split("/")
        (self.pinned.add if action == "pin" else self.pinned.discard)(m)
        return SimpleNamespace(status_code=200)

    def post(self, url, json=None, **kw):
        if url.endswith("/backend/shutdown"):
            self.calls["shutdown"] += 1
            self.unloaded.append(json["model"])
            self.models.pop(json["model"], None)
            return SimpleNamespace(status_code=200)
        raise AssertionError(f"unexpected POST {url}")

    def gpu(self, url):
        self.calls["gpu"] += 1
        used = (sum(self.models.values()) + self.extra) * MIB
        return {"devices": [{"card": "card1", "pdev": "0000:03:00.0", "driver": "xe", "vendor_id": "0x8086",
                             "device_id": "0xe223", "vendor": "Intel", "name": "Arc Pro B70",
                             "vram_total": 32_656 * MIB, "vram_used": used,
                             "processes": [{"pid": 7, "label": "x", "command": "llama-server",
                                            "container": "localai", "vram": used, "host": 0}]}]}


@pytest.fixture
def card(monkeypatch, tmp_path, isolated_config):
    from app import httpc

    def make(models_mib, **kw) -> Runner:
        r = Runner(models_mib, **kw)
        monkeypatch.setattr(model_load, "_fetch_system", REAL_FETCH)
        monkeypatch.setattr(httpc, "get", r.get)
        monkeypatch.setattr(httpc, "post", r.post)
        monkeypatch.setattr(httpc, "put", r.put)
        monkeypatch.setattr(config, "GPU_PROBE_URL", "http://gpu-probe:8080")
        monkeypatch.setattr(gpu, "_get_json", r.gpu)
        return r
    monkeypatch.setattr(model_pins, "_path", lambda: tmp_path / "state" / "model-pins.json")
    monkeypatch.setattr(model_evict, "ROOM_WAIT_S", 0.0)
    monkeypatch.setattr(model_load, "_record", lambda c: None)     # no cold-start line from a thread
    said: list[str] = []
    resident.reset()
    resident.set_log(lambda level, text: said.append(text))
    model_load.reset()
    model_use.reset()
    node_state.set_worker_state("running")
    make.said = said
    yield make
    resident.reset()
    resident.set_log(model_pins.node_event)
    model_load.reset()


def _used(**ago_h: float) -> None:
    """Real use rows: each model last used `ago_h` hours before NOW."""
    for m, h in ago_h.items():
        node_state.touch_encoder(S, m.replace("_", "-"), now=NOW - h * H)


def _in_set(*models: str) -> None:
    """Models of the resident set (known to Louise, used or not)."""
    with resident._lock:
        resident._last = {(S, m): resident.Resident(S, m, ("query encoder",)) for m in models}


def _known(**mib: int) -> None:
    with model_load._lock:
        for m, v in mib.items():
            model_load._sizes[(S, m.replace("_", "-"))] = v * MIB


# ---- the order -------------------------------------------------------------------

def test_the_order_is_oldest_real_use_first_never_used_first_of_all():
    loaded = [model_evict.Loaded("chat", 7_000, NOW - 60), model_evict.Loaded("omnivoice", 1_700, NOW - 9 * H),
              model_evict.Loaded("medcpt", 400, None), model_evict.Loaded("qwen3-4b", 2_900, NOW - 5 * H),
              model_evict.Loaded("busy", 4_000, None, busy=True)]
    assert [v.model for v in model_evict.eligible(loaded, NOW)] == ["medcpt", "omnivoice", "qwen3-4b", "chat"]
    assert [v.model for v in model_evict.eligible(loaded, NOW - 6 * H)] == ["medcpt", "omnivoice"], \
        "only what was used before the horizon"
    assert model_evict.eligible(loaded, None) == [], "a never-used model makes no one give way"
    assert [v.model for v in model_evict.victims(loaded, need=2_000, free=0, horizon=NOW)] == \
        ["medcpt", "omnivoice"], "the fewest, in the order"
    assert model_evict.victims(loaded, need=1_000, free=1_000, horizon=NOW) == []
    assert model_evict.victims(loaded, need=99_000, free=0, horizon=NOW) is None


# ---- (a) the oldest real use goes first ------------------------------------------

def test_the_unpinned_model_with_the_oldest_real_use_is_unloaded_first(card, monkeypatch):
    """The night of 2026-11-16, in small: MADLAD used 5 min ago must not go
    while omnivoice and qwen3-4b sat unused for hours."""
    r = card({"louise-translate-madlad": 8_000, "omnivoice-cpp": 1_700, "vega-qwen3-embedding-4b": 2_900,
              "louise-chat": 7_300}, extra_mib=11_000)              # 30,900 used: 123 MiB under the line
    _used(louise_translate_madlad=5 / 60, omnivoice_cpp=9, vega_qwen3_embedding_4b=5, louise_chat=0.1)
    _known(vega_dar_legal_it=2_300)
    out = model_evict.make_room_for(S, "vega-dar-legal-it", horizon=NOW)
    assert out.fits is True and r.unloaded == ["omnivoice-cpp", "vega-qwen3-embedding-4b"]
    assert "louise-translate-madlad" in r.models and "louise-chat" in r.models
    line = [t for t in card.said if t.startswith("card: unloaded")]
    assert len(line) == 1 and "omnivoice-cpp (last used 9.0 h ago, 1,700 MiB)" in line[0]


def test_a_model_never_used_goes_before_one_used(card, monkeypatch):
    r = card({"vega-medcpt-query": 2_000, "omnivoice-cpp": 1_700}, extra_mib=27_000)
    _in_set("vega-medcpt-query")
    _used(omnivoice_cpp=20)
    _known(louise_chat=1_500)
    assert model_evict.make_room_for(S, "louise-chat", horizon=NOW).fits is True
    assert r.unloaded == ["vega-medcpt-query"]


def test_nothing_is_unloaded_when_even_all_would_not_make_the_room(card, monkeypatch):
    r = card({"omnivoice-cpp": 1_700}, extra_mib=29_000)
    _used(omnivoice_cpp=20)
    _known(big=8_000)
    out = model_evict.make_room_for(S, "big", horizon=NOW)
    assert out.fits is False and r.unloaded == [], "their reloads would be paid for nothing"


def test_a_model_louise_does_not_know_is_never_touched(card, monkeypatch):
    """The operator's 35B, loaded by hand: no use row, not in the set."""
    r = card({"qwen3.6-35b": 20_000}, extra_mib=10_000)
    _known(louise_chat=7_300)
    assert model_evict.make_room_for(S, "louise-chat", horizon=NOW).fits is False and r.unloaded == []


def test_a_busy_backend_is_left_alone(card, monkeypatch):
    r = card({"vega-bge-m3": 2_000, "omnivoice-cpp": 1_700}, extra_mib=26_500, cpu={"vega-bge-m3": 90.0})
    _used(vega_bge_m3=30, omnivoice_cpp=2)
    _known(louise_chat=2_500)
    assert model_evict.make_room_for(S, "louise-chat", horizon=NOW).fits is True
    assert r.unloaded == ["omnivoice-cpp"]


# ---- (b) a warm or a check is no use ----------------------------------------------

def test_warm_and_check_traffic_writes_no_use(card, monkeypatch):
    """The run check's sixteen texts at every start, a gate, a calibration
    — `embed_calibration` and `Answers` — and a block marked `not_work`
    write no row; a document embed and a query do."""
    recipe = SimpleNamespace(url=S, model="vega-qwen3-embedding-4b", query_url=None, query_model=None,
                             api="vega")

    def fake_embed(texts, *a, recipe=None, **kw):
        embeddings._touch(recipe)                       # as `_post` does on every answer
        return [[1.0] for _ in texts]
    monkeypatch.setattr(embeddings, "_embed_texts", fake_embed)
    monkeypatch.setattr(embeddings, "_embed_one_at_a_time", lambda texts, recipe=None, **kw: fake_embed(texts, recipe=recipe))
    monkeypatch.setattr(embeddings, "_side", lambda r, q: r)
    monkeypatch.setattr(embeddings, "_pin", lambda r, **kw: nullcontext())
    embeddings._touched.clear()
    node_state.touch_encoder(S, "seed", now=NOW)        # the store exists, as on a node
    embeddings.embed_calibration(recipe, ["a", "b"])
    embeddings.embed_calibration(recipe, ["a"], one_at_a_time=True)
    runs_mod.Answers(lambda texts, seen: fake_embed(texts, recipe=recipe))(["a"])
    with model_use.not_work():
        model_use.touch(S, "louise-chat")
    assert set(node_state.encoder_last_used()) == {(S, "seed")}, "no warm, no check counts"
    embeddings.embed_query(SimpleNamespace(name="c"), "a question", recipe=recipe)
    model_use.touch(S, "louise-chat")
    assert {(S, "vega-qwen3-embedding-4b"), (S, "louise-chat")} <= set(node_state.encoder_last_used())


def test_the_keeper_s_warm_writes_no_use(node, monkeypatch):           # noqa: F811
    """A preload is no use: a warm that writes the row (as a real request's
    call site would) writes nothing inside the keeper's load."""
    from app import translate
    node.held.add("louise-chat")
    node_state.touch_encoder(S, "seed", now=NOW)
    monkeypatch.setattr(translate, "warm", lambda: (model_use.touch(S, "louise-translate"),
                                                    node.held.add("louise-translate")))
    resident.beat([], embed_mode=lambda c: "off")
    resident.wait_warm(5)
    assert "louise-translate" in node.held
    assert (S, "louise-translate") not in node_state.encoder_last_used()



# ---- (c) a pinned model is never unloaded ----------------------------------------

def test_a_pinned_model_is_never_unloaded(card, monkeypatch):
    """Pinned for a holder in Louise's record (an embed batch, a gate), or
    pinned in LocalAI only (the operator's): both stay, however old."""
    r = card({"vega-nemotron": 4_400, "omnivoice-cpp": 3_500, "rerank-bge": 1_500},
             extra_mib=20_000, pinned={"rerank-bge"})
    _used(vega_nemotron=50, omnivoice_cpp=1, rerank_bge=40)
    monkeypatch.setattr(model_pins, "_applies", lambda server, model: True)
    assert model_pins.hold(S, "vega-nemotron", "worker embed wikidata") is True
    r.calls.clear()
    _known(louise_chat=3_000)
    assert model_evict.make_room_for(S, "louise-chat", horizon=NOW).fits is True
    assert r.unloaded == ["omnivoice-cpp"], "nemotron (held) and rerank-bge (LocalAI's pin) stay"
    assert r.calls["pin"] == 1 + 1, "rerank-bge's pin read, then omnivoice's — one per victim tried"
    _known(more=9_000)
    assert model_evict.make_room_for(S, "more", horizon=NOW).fits is False
    assert "vega-nemotron" in r.models and "rerank-bge" in r.models


# ---- (d) the keeper never evicts a more recent use for a preload -------------------

def _keeper_setup(monkeypatch, node, *, old_h: float):                 # noqa: F811
    monkeypatch.setattr(model_evict, "ROOM_WAIT_S", 0.0)
    sizes = {"louise-chat": 16_000, "vega-old": 6_000, "vega-recent": 2_000, "louise-translate": 7_900}
    node.held |= {"louise-chat", "vega-old", "vega-recent"}
    _system(monkeypatch, node, sizes)
    with model_load._lock:
        model_load._sizes[(S, "louise-translate")] = 7_900 * MIB
    _card(monkeypatch, 32_656, 24_000)                                 # 7,023 MiB under the line
    node_state.touch_encoder(S, "louise-translate", now=NOW - 1 * H)
    node_state.touch_encoder(S, "louise-chat", now=NOW - 120)
    node_state.touch_encoder(S, "vega-recent", now=NOW - 10)
    node_state.touch_encoder(S, "vega-old", now=NOW - old_h * H)


def test_the_keeper_preloads_by_unloading_a_model_used_longer_ago(node, monkeypatch):   # noqa: F811
    _keeper_setup(monkeypatch, node, old_h=5)
    resident.beat([], embed_mode=lambda c: "off")
    resident.wait_warm(5)
    assert node.unloads == ["vega-old"] and "louise-translate" in node.held
    assert {"louise-chat", "vega-recent"} <= node.held


def test_the_keeper_does_not_preload_what_would_evict_a_more_recent_use(node, monkeypatch):  # noqa: F811
    """MADLAD's thrash, ended: the preload waits rather than take the room of
    a model used since."""
    _keeper_setup(monkeypatch, node, old_h=0.5)                         # used after MADLAD
    resident.beat([], embed_mode=lambda c: "off")
    resident.wait_warm(5)
    assert node.unloads == [] and "louise-translate" not in node.held
    waits = [t for _, t in node.said if "stays unloaded" in t]
    assert len(waits) == 1 and "louise-translate" in waits[0] and "used more recently" in waits[0]


# ---- (e) the cost: one `/system` per load decision ---------------------------------

@pytest.mark.parametrize("n", [3, 30])
def test_a_decision_reads_system_once_whatever_the_number_of_models(card, monkeypatch, n):
    models = {f"vega-{i}": 100 for i in range(n)} | {"omnivoice-cpp": 1_700}
    r = card(models, extra_mib=31_000 - sum(models.values()))
    for i in range(n):
        node_state.touch_encoder(S, f"vega-{i}", now=NOW - 60)
    _used(omnivoice_cpp=9)
    _known(louise_chat=1_500)
    r.calls.clear()
    assert model_evict.make_room_for(S, "louise-chat", horizon=NOW).fits is True
    assert r.unloaded == ["omnivoice-cpp"]
    assert r.calls["system"] == 1, "one `/system`, never one per model"
    assert r.calls["pin"] == 1 and r.calls["shutdown"] == 1, "one pin read per model unloaded, not per model"
    assert r.calls["settings"] == 1 and r.calls["gpu"] <= 2


def test_a_cold_load_makes_its_room_with_its_own_system_read(card, monkeypatch):
    """`ensure_loaded`'s cold-load hook: the room made before the load, from
    the `/system` read that found the model cold — one read in all; a real
    request may take the room of any older use."""
    r = card({"omnivoice-cpp": 1_700, "louise-chat": 7_300}, extra_mib=22_000)
    _used(omnivoice_cpp=9, louise_chat=0.01)
    _known(vega_bge_m3=1_500)
    loads = []
    model_load.ensure_loaded(S, "vega-bge-m3", lambda bound: loads.append(1), caller="query embedding",
                             max_age_s=0.0)
    assert loads == [1] and r.unloaded == ["omnivoice-cpp"] and r.calls["system"] == 1


def test_a_check_s_cold_load_takes_no_room_from_a_more_recent_use(card, monkeypatch):
    """A run check loading its encoder is Louise's own load: its horizon is
    the model's own last real use, so it never evicts the chat model used
    since — the load goes as before, and the reclaimer decides."""
    r = card({"louise-chat": 7_300}, extra_mib=22_500)
    _used(louise_chat=0.01, vega_qwen3_embedding_4b=6)
    _known(vega_qwen3_embedding_4b=2_900)
    with model_use.not_work():
        model_load.ensure_loaded(S, "vega-qwen3-embedding-4b", lambda bound: None, caller="run check",
                                 max_age_s=0.0)
    assert r.unloaded == []


def test_with_the_node_paused_a_cold_load_unloads_nothing(card, monkeypatch):
    r = card({"omnivoice-cpp": 1_700}, extra_mib=29_000)
    _used(omnivoice_cpp=9)
    _known(vega_bge_m3=1_500)
    node_state.set_worker_state("paused")
    model_load.ensure_loaded(S, "vega-bge-m3", lambda bound: None, caller="query embedding", max_age_s=0.0)
    assert r.unloaded == []


def test_a_room_refused_names_the_models_pinned_by_work_and_the_load_hook_says_it(card, monkeypatch):
    """München, 2026-11-26, the switch to query mode: "louise-chat-qwen3.6-35b-a3b
    is 10,172 MiB over the 0.98 line … room is made by unloading" — and nothing
    was unloaded and no line said why: the embed mode's batches still pinned
    their encoders. The load went into the full card and died 75 s later."""
    card({"vega-nemotron": 4_400, "vega-bge-m3": 900}, extra_mib=26_000)
    _used(vega_nemotron=1, vega_bge_m3=2)
    monkeypatch.setattr(model_pins, "_applies", lambda server, model: True)
    assert model_pins.hold(S, "vega-nemotron", "worker embed wikidata") is True
    assert model_pins.hold(S, "vega-bge-m3", "worker embed wikipedia") is True
    _known(chat_35b=21_715)
    got = model_evict.make_room_for(S, "chat-35b", horizon=NOW)
    assert got.fits is False and "vega-bge-m3, vega-nemotron pinned by work in flight" in got.why
    said = []
    monkeypatch.setattr(model_evict, "_say", lambda level, m: said.append(m))
    monkeypatch.setattr(model_evict, "room_before_load", lambda server, model: got)
    assert model_evict.before_load(S, "chat-35b") is got
    assert said and said[0].startswith("card: no room made for chat-35b before its load — ") \
        and "pinned by work in flight" in said[0]
