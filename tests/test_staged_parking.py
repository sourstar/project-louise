"""A staged model file that parks says all of it (deploy/vega/stage/models;
docs/notes/parking-keeper.md §6): `park: true`, the engine's switch in its
`env`, and `context_on_demand` — together or not at all.

Why a test: the three live in three places of one file and fail three
different ways apart. `park: true` without the switch: LocalAI lists the model
as parking, every park is 412 and the evictor stops it as before — harmless
and useless. The switch without `park: true`: another run, gated for nothing.
Either without `context_on_demand`: the engine parks only a model that makes
its context per request."""
from __future__ import annotations

import json
from pathlib import Path

from app.hold import embedder as emb

MODELS = Path(__file__).parents[1] / "deploy" / "vega" / "stage" / "models"


def _staged() -> dict[str, dict]:
    out = {}
    for path in sorted(MODELS.glob("*.yaml")):
        text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                         if not line.lstrip().startswith("#"))
        out[path.name] = json.loads(text)
    return out


def test_a_staged_file_that_parks_says_all_three_and_one_that_does_not_says_none():
    files = _staged()
    assert files, "the staged model files are read"
    parking = []
    for name, body in files.items():
        parks = body.get("park") is True
        engine_can = emb.can_park({"env": body.get("env") or {}}) is True
        assert parks == engine_can, (f"{name}: `park: {body.get('park')}` and the engine's switch "
                                     f"{'set' if engine_can else 'not set'} in its env — together or not at all")
        if parks:
            parking.append(name)
            assert "context_on_demand" in (body.get("options") or []), \
                f"{name}: only a model on context on demand parks"
            assert body.get("backend") == "vega-louise-llama-cpp", f"{name}: the park engine is VeGa's llama.cpp"
    assert parking == ["vega-bge-m3.yaml"], \
        "bge-m3 first (operator, 2026-11-26: \"1. A then B\"); the five llama.cpp encoders follow its rows"


def test_bge_m3_keeps_the_byte_check_and_its_reproducibility_mode():
    body = _staged()["vega-bge-m3.yaml"]
    env = body["env"]
    assert env["GGML_SYCL_PARK_VERIFY"] == "1", "the operator: \"On but monitor if it creates overhead\""
    assert env["GGML_SYCL_ENABLE_DNN"] == "0", "invariant 12: Intel SYCL's reproducibility mode stays"
    assert body["pinned"] is False and body["parameters"]["seed"] == 0
    assert body["options"] == ["fit:off", "context_on_demand", "context_canary:true", "parallel:1",
                               "pooling:cls", "ubatch:8192"], "the node's live options, in their order"
