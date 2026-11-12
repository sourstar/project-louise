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
