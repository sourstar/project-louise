"""A gate never monopolises the model (operator, 2026-11-12: "gating a
collection cannot monopolize the GPU"): it measures on `<model>--gate`, the
same run on a load of its own, made from the model's config by LocalAI's
import API; the collections on the model keep embedding and none stands
aside. Where no twin can be had, or it states another run, the gate measures
on the shared load as before — said."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app import config, gate_twin, node_state, run_check

SERVER = "http://localai:8080"
MAIN = {"name": "vega-nemotron", "backend": "vega-louise-llama-cpp", "embeddings": True,
        "pinned": True, "parameters": {"model": "weights/n.gguf"}, "options": ["parallel:1"]}


class Server:
    """LocalAI's model-config API, as the node answered it (2026-11-12)."""

    def __init__(self):
        self.configs = {"vega-nemotron": dict(MAIN)}
        self.imports, self.shutdowns = [], []

    def get(self, url, **kw):
        name = url.rsplit("/", 1)[-1]
        req = httpx.Request("GET", url)
        if name in self.configs:
            return httpx.Response(200, request=req, json=self.configs[name])
        return httpx.Response(404, request=req, json={"error": "not found"})

    def post(self, url, **kw):
        req = httpx.Request("POST", url)
        if url.endswith("/models/import"):
            assert kw["headers"]["Content-Type"] == "application/x-yaml"
            body = json.loads(kw["content"])          # JSON is YAML
            self.imports.append(body)
            self.configs[body["name"]] = body
            return httpx.Response(200, request=req, json={"success": True})
        if url.endswith("/backend/shutdown"):
            self.shutdowns.append(kw["json"]["model"])
            return httpx.Response(200, request=req, json={"message": "model stopped"})
        return httpx.Response(404, request=req)


@pytest.fixture
def server(monkeypatch):
    s = Server()
    monkeypatch.setattr(gate_twin.httpc, "get", s.get)
    monkeypatch.setattr(gate_twin.httpc, "post", s.post)
    return s


def test_the_twin_is_the_models_config_under_its_own_name_and_unpinned(server):
    assert gate_twin.ensure(SERVER, "vega-nemotron") == "vega-nemotron--gate"
    (made,) = server.imports
    assert made == {**MAIN, "name": "vega-nemotron--gate", "pinned": False}
    assert gate_twin.ensure(SERVER, "vega-nemotron") == "vega-nemotron--gate"
    assert len(server.imports) == 1, "an unchanged twin is not written again"
    server.configs["vega-nemotron"]["options"] = ["parallel:1", "ubatch:512"]
    gate_twin.ensure(SERVER, "vega-nemotron")
    assert len(server.imports) == 2 and server.imports[-1]["options"] == ["parallel:1", "ubatch:512"], \
        "a twin behind the model's config is made again"


def test_no_config_is_no_twin_and_says_why(server, monkeypatch):
    with pytest.raises(gate_twin.TwinUnavailable, match="holds no config for vega-other"):
        gate_twin.ensure(SERVER, "vega-other")
    monkeypatch.setattr(gate_twin.httpc, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    with pytest.raises(gate_twin.TwinUnavailable, match="could not be asked"):
        gate_twin.ensure(SERVER, "vega-nemotron")


def _recipe():
    from app.collections import EmbedRecipe
    import dataclasses
    fields = {f.name: None for f in dataclasses.fields(EmbedRecipe)}
    return dataclasses.replace(EmbedRecipe(**fields), url=SERVER, model="vega-nemotron",
                               api="vega")


def test_the_gate_measures_on_the_twin_and_nobody_stands_aside(server, monkeypatch, isolated_config, tmp_path):
    """legi's gate, 2026-11-12, held vega-nemotron-3-embed-1b from
    normattiva-nemo and wikidata for 11 minutes. On the twin: the gate is
    asked on `--gate`, `gating_elsewhere` names nobody meanwhile, the card
    says the check runs beside them, and the twin is stopped after."""
    monkeypatch.setattr(config, "GATE_TWIN", True)
    monkeypatch.setattr(run_check, "answering_run", lambda recipe, **kw: "sha256:" + "e9" * 32)
    monkeypatch.setattr(run_check, "probe_for", lambda codex, role="document": "probe")
    seen = {}

    def gate(root, name, codex, recipe, **kw):
        seen["model"] = recipe.model
        seen["aside"] = dict(run_check._gating)
        seen["card"] = node_state.gates_running()
        return {"run": "sha256:" + "e9" * 32}
    monkeypatch.setattr(run_check, "_validate", gate)
    monkeypatch.setattr(run_check, "_pinned", lambda *a, **k: __import__("contextlib").nullcontext())
    body = run_check.validate(tmp_path, "legi", SimpleNamespace(), _recipe())
    assert body["run"].startswith("sha256:e9")
    assert seen["model"] == "vega-nemotron--gate", "measured on the twin"
    assert seen["aside"] == {}, "nobody stands aside"
    (card,) = seen["card"]
    assert card["collection"] == "legi" and card["model"] == "vega-nemotron" and card["alone"] is False
    assert server.shutdowns == ["vega-nemotron--gate"], "its memory given back"
    assert node_state.gates_running() == []


def test_the_twin_is_leased_while_its_gate_runs_so_the_sweep_leaves_it(
        server, monkeypatch, isolated_config, tmp_path):
    """NeuRIS, 2026-11-12: no held collection names `<model>--gate`, and the
    worker's sweep of unheld embedders unloaded the twin every minute under
    the gate — ten runs deferred in three hours. The twin is leased for the
    gate: the sweep, run meanwhile, leaves it; the lease goes with the gate."""
    from app import model_load, worker
    monkeypatch.setattr(config, "GATE_TWIN", True)
    monkeypatch.setattr(run_check, "answering_run", lambda recipe, **kw: "sha256:" + "e9" * 32)
    monkeypatch.setattr(run_check, "probe_for", lambda codex, role="document": "probe")
    monkeypatch.setattr(run_check, "_pinned", lambda *a, **k: __import__("contextlib").nullcontext())
    loaded = {"vega-nemotron", "vega-nemotron--gate"}
    monkeypatch.setattr(model_load, "activity", lambda srv: {m: 0.0 for m in loaded})
    unloaded = []
    monkeypatch.setattr(model_load, "unload", lambda srv, m: unloaded.append(m) or True)
    monkeypatch.setattr(worker.collections_mod, "recipe_for", lambda c: _recipe())
    held = SimpleNamespace(name="neuris")               # names vega-nemotron, never the twin
    seen = {}

    def gate(root, name, codex, recipe, **kw):
        seen["leased"] = node_state.leased_encoders()
        seen["swept"] = worker._sweep_unheld_embedders([held])
        return {"run": "sha256:" + "e9" * 32}
    monkeypatch.setattr(run_check, "_validate", gate)
    run_check.validate(tmp_path, "neuris", SimpleNamespace(), _recipe())
    assert (model_load.server_of(SERVER), "vega-nemotron--gate") in seen["leased"]
    assert seen["swept"] == [] and unloaded == [], "the sweep leaves a gate's twin alone"
    assert node_state.leased_encoders() == set(), "the lease goes with the gate"
    assert worker._sweep_unheld_embedders([held]) == ["vega-nemotron--gate"], \
        "a twin nobody leases is an orphan, unloaded as before"


@pytest.mark.parametrize("why", ["another run", "no config"])
def test_without_a_same_run_twin_the_gate_holds_the_shared_load_and_says_so(
        server, monkeypatch, isolated_config, tmp_path, caplog, why):
    monkeypatch.setattr(config, "GATE_TWIN", True)
    monkeypatch.setattr(run_check, "probe_for", lambda codex, role="document": "probe")
    if why == "another run":
        monkeypatch.setattr(run_check, "answering_run",
                            lambda recipe, **kw: "sha256:" + ("aa" if recipe.model.endswith("--gate") else "e9") * 32)
    else:
        server.configs.clear()
    seen = {}

    def gate(root, name, codex, recipe, **kw):
        seen["model"] = recipe.model
        seen["aside"] = dict(run_check._gating)
        return {"run": "x"}
    monkeypatch.setattr(run_check, "_validate", gate)
    monkeypatch.setattr(run_check, "_pinned", lambda *a, **k: __import__("contextlib").nullcontext())
    with caplog.at_level("WARNING"):
        run_check.validate(tmp_path, "legi", SimpleNamespace(), _recipe())
    assert seen["model"] == "vega-nemotron" and seen["aside"], "the shared load, held as before"
    assert "the gate measures on the shared load" in caplog.text
    if why == "another run":
        assert "not the same run" in caplog.text and server.shutdowns == ["vega-nemotron--gate"]


from tests.test_run_check import endpoint, node  # noqa: E402,F401  (the fixtures)


def test_a_collection_on_the_shared_load_is_not_turned_away_by_a_twin_gate(node, endpoint):
    """The look lock is one per model (`_flights`): a gate holds it for its
    whole length, and a collection whose verdict expired meanwhile met it
    and had no embedding that round — the monopoly again by another door.
    While the gate measures on its twin, the lock turns nobody away."""
    from app import db
    from app.verify import backend_gate
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    keys = run_check._flights(collection)
    for k in keys:
        run_check._looking.setdefault(k, __import__("threading").Lock()).acquire()
    try:
        run_check.reset_cache()
        assert backend_gate.embed_allowed(con, collection) is False, "a gate on the shared load"
        run_check._twin_busy.update(keys)
        run_check.reset_cache()
        assert backend_gate.embed_allowed(con, collection) is True, "a gate on its twin"
    finally:
        run_check._twin_busy.difference_update(keys)
        for k in keys:
            run_check._looking[k].release()


def _held_elsewhere(path):
    """The lock as another process holds it: its own open file, flocked
    (flock is per open file, so this process's claim meets it too)."""
    import fcntl
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_a_gate_in_another_process_holds_the_twin_and_this_one_defers(
        server, monkeypatch, isolated_config, tmp_path):
    """`hold embedder check` gates in a process of its own beside the worker
    (NeuRIS, 2026-11-12): the twin's claim is the node's, an flock, so two
    gates on one model never share a twin nor stop each other's."""
    import fcntl
    import os
    from app.hold import embedder as emb
    monkeypatch.setattr(config, "GATE_TWIN", True)
    monkeypatch.setattr(run_check, "_twin_for", lambda *a, **k: pytest.fail("the twin was touched"))
    path = run_check.twin_lock_path(SERVER, "vega-nemotron--gate")
    fd = _held_elsewhere(path)
    try:
        with pytest.raises(emb.GateDeferred, match="another process"):
            run_check.validate(tmp_path, "neuris", SimpleNamespace(), _recipe())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert server.imports == [] and server.shutdowns == []
    lock = run_check._looking[f"{SERVER} vega-nemotron--gate"]
    assert lock.acquire(blocking=False), "this process's lock given back with the deferral"
    lock.release()


def test_the_twin_is_stopped_before_the_claim_goes(server, monkeypatch, isolated_config, tmp_path):
    """The claim is released only after the twin is stopped: released first,
    the next gate could load its twin and this one's stop would end it."""
    import fcntl
    import os
    monkeypatch.setattr(config, "GATE_TWIN", True)
    monkeypatch.setattr(run_check, "answering_run", lambda recipe, **kw: "sha256:" + "e9" * 32)
    monkeypatch.setattr(run_check, "probe_for", lambda codex, role="document": "probe")
    monkeypatch.setattr(run_check, "_validate", lambda *a, **k: {"run": "sha256:" + "e9" * 32})
    monkeypatch.setattr(run_check, "_pinned", lambda *a, **k: __import__("contextlib").nullcontext())
    path = run_check.twin_lock_path(SERVER, "vega-nemotron--gate")
    held_at_stop = []
    real = gate_twin.release

    def release(srv, twin):
        fd = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held_at_stop.append(False)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            held_at_stop.append(True)
        finally:
            os.close(fd)
        real(srv, twin)
    monkeypatch.setattr(gate_twin, "release", release)
    run_check.validate(tmp_path, "legi", SimpleNamespace(), _recipe())
    assert held_at_stop == [True], "stopped while still claimed"
    assert server.shutdowns == ["vega-nemotron--gate"]
    fd = _held_elsewhere(path)                         # free again for the next gate
    os.close(fd)


# ---- room made before the twin loads (München, 2026-11-12: xe overcommits silently) ----

MIB = 1 << 20


@pytest.fixture
def card(monkeypatch):
    """A 32,656 MiB card, its residents and their sizes, as the probe reads them."""
    from app import model_load, model_pins, resident
    state = {"used": 30_000 * MIB, "unloaded": [], "busy": {}, "pinned": set()}
    sizes = {"vega-nemotron": 2_390 * MIB, "vega-bge-m3": 1_180 * MIB, "vega-qwen3": 5_089 * MIB,
             "vega-medcpt-query": 359 * MIB, "louise-chat": 5_791 * MIB}
    roles = {"vega-nemotron": ("encoder",), "vega-bge-m3": ("encoder",), "vega-qwen3": ("encoder",),
             "vega-medcpt-query": ("query encoder",), "louise-chat": ("chat",)}
    monkeypatch.setattr(resident, "_card", lambda: (32_656 * MIB, state["used"]))
    monkeypatch.setattr(resident, "current", lambda: [resident.Resident(SERVER, m, roles[m]) for m in sizes])
    monkeypatch.setattr(model_load, "size_vram", lambda server, model: sizes.get(model))
    monkeypatch.setattr(model_load, "activity", lambda server: dict(state["busy"]))
    monkeypatch.setattr(model_pins, "read_pinned", lambda server, model: model in state["pinned"])

    def unload(server, model):
        state["unloaded"].append(model)
        state["used"] -= sizes[model]
        return True
    monkeypatch.setattr(model_load, "unload", unload)
    return state


def test_a_twin_that_fits_unloads_nothing(card):
    card["used"] = 20_000 * MIB
    assert gate_twin.make_room(SERVER, "vega-nemotron") == (2_390 * MIB, [])
    assert card["unloaded"] == []


def test_room_is_made_from_idle_document_encoders_before_the_twin_loads(card):
    """Short by ~146 MiB: the largest idle document encoder goes first (one
    unload, not several); never the chat model, nor the model gated."""
    card["used"] = 29_900 * MIB
    assert gate_twin.make_room(SERVER, "vega-nemotron") == (2_390 * MIB, ["vega-qwen3"])


def test_a_busy_or_pinned_encoder_is_never_unloaded_for_the_twin(card):
    """The document encoders embedding now or pinned by a pass stay; an idle
    query encoder goes before anything the question uses."""
    card["used"] = 29_900 * MIB
    card["busy"] = {"vega-qwen3": 80.0}                 # embedding now: left alone
    card["pinned"] = {"vega-bge-m3"}                    # a pass's: left alone
    assert gate_twin.make_room(SERVER, "vega-nemotron") == (2_390 * MIB, ["vega-medcpt-query"])
    assert "louise-chat" not in card["unloaded"]


def test_a_card_that_cannot_hold_the_twin_defers_the_gate_never_the_shared_load(
        card, server, monkeypatch, tmp_path, isolated_config):
    card["used"] = 32_000 * MIB
    card["busy"] = {"vega-bge-m3": 50.0, "vega-qwen3": 50.0, "vega-medcpt-query": 50.0}
    with pytest.raises(gate_twin.RoomShort, match="needs ~2,902 MiB free"):
        gate_twin.make_room(SERVER, "vega-nemotron")
    monkeypatch.setattr(config, "GATE_TWIN", True)
    monkeypatch.setattr(run_check, "probe_for", lambda codex, role="document": "probe")
    monkeypatch.setattr(run_check, "_validate", lambda *a, **k: pytest.fail("measured anyway"))
    from app.hold import embedder as emb
    with pytest.raises(emb.GateDeferred, match="asked again at the next round"):
        run_check.validate(tmp_path, "legi", SimpleNamespace(), _recipe())
    assert run_check._gating == {}, "nobody stood aside"


def test_the_twins_room_is_held_from_the_keepers_budget_while_the_gate_runs():
    from app import resident
    mem = resident.Memory()
    mem.total = 32_656 * MIB
    want = [resident.Resident(SERVER, "vega-bge-m3", ("encoder",))]
    free = resident.plan(want, mem, threshold=0.95, used={}, now=0.0)
    held = resident.plan(want, mem, threshold=0.95, used={}, now=0.0, reserved=2_390 * MIB)
    assert free.budget - held.budget == 2_390 * MIB


def test_nothing_is_unloaded_when_the_unloads_cannot_make_the_room(card):
    """Every idle encoder together is short of the twin: none is unloaded for
    a gate that defers anyway (their reloads would be the only result)."""
    card["used"] = 32_400 * MIB
    card["busy"] = {"vega-qwen3": 80.0}                 # what is left: 1,539 MiB of 2,902 - 256
    with pytest.raises(gate_twin.RoomShort, match="256 MiB are;"):
        gate_twin.make_room(SERVER, "vega-nemotron")
    assert card["unloaded"] == []


def test_the_twin_waits_for_the_driver_to_show_the_room_bounded(card, monkeypatch):
    """The driver's count lags a stopped backend: the twin loads only once the
    card SHOWS the room, and a reading that never does defers the gate."""
    from app import model_load
    card["used"] = 29_900 * MIB
    lagging = {"reads": 0}

    def unload(server, model):                          # given back two reads later
        card["unloaded"].append(model)
        lagging["reads"] = 2
        return True
    from app import resident
    monkeypatch.setattr(model_load, "unload", unload)

    def read():
        if lagging["reads"]:
            lagging["reads"] -= 1
            if not lagging["reads"]:
                card["used"] -= 5_089 * MIB
        return 32_656 * MIB, card["used"]
    monkeypatch.setattr(resident, "_card", read)
    monkeypatch.setattr(gate_twin, "ROOM_POLL_S", 0.0)
    assert gate_twin.make_room(SERVER, "vega-nemotron") == (2_390 * MIB, ["vega-qwen3"])
    card["used"], card["unloaded"] = 29_900 * MIB, []
    monkeypatch.setattr(model_load, "unload", lambda server, model: card["unloaded"].append(model) or True)
    with pytest.raises(gate_twin.RoomShort, match="after unloading vega-qwen3"):
        gate_twin.make_room(SERVER, "vega-nemotron", wait_s=0.0)
