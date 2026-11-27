"""The backends' half of COMPUTE PRECISION, STATED (operator, 2026-11-27:
"HALF FUCKING PRECISION AS A DEFAULT!!!"; docs/notes/approved-encoders.md
"Compute precision, stated"): what the model's env states is APPLIED and READ
BACK, never left to a library — torch's dtype and fp32 matmul precision
(jina-v3, MADLAD), OpenVINO's INFERENCE_PRECISION_HINT (louise_backend, for the
OpenVINO tasks once MedCPT is released: held until gutenberg-nemo is done,
operator 2026-11-27). Stand-ins for torch and OpenVINO, as the backends' other
tests (tests/test_localai_backends.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _exec(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lb = _exec("louise_backend_precision", ROOT / "deploy" / "localai" / "backends" / "louise_backend.py")
SERVERS = {"jina-v3": _exec("jina_v3_precision", ROOT / "deploy" / "jina-embeddings-v3" / "server.py"),
           "madlad": _exec("madlad_precision", ROOT / "deploy" / "madlad-translate" / "server.py")}


# ---- OpenVINO: the hint passed, then read back -------------------------------------

class OvType:
    """What `compiled.get_property("INFERENCE_PRECISION_HINT")` gives: an ov.Type."""

    def __init__(self, name: str) -> None:
        self.name = name

    def get_type_name(self) -> str:
        return self.name


class Compiled:
    def __init__(self, value) -> None:
        self.value, self.asked = value, []

    def get_property(self, key):
        self.asked.append(key)
        return self.value


def test_the_stated_precision_is_the_hint_openvino_compiles_with():
    assert lb.openvino_config("f32") == {"INFERENCE_PRECISION_HINT": "f32"}
    assert lb.openvino_config("f16", "/state/ov-cache") == {"INFERENCE_PRECISION_HINT": "f16",
                                                           "CACHE_DIR": "/state/ov-cache"}
    with pytest.raises(lb.LoadError, match="one of f32, bf16, f16"):
        lb.openvino_config("fp32")


@pytest.mark.parametrize("value", [OvType("f32"), "f32", "float32", "<Type: 'float32'>"])
def test_a_compiled_model_computing_in_the_stated_precision_loads(value):
    compiled = Compiled(value)
    lb.openvino_precision_holds(compiled, "f32")
    assert compiled.asked == ["INFERENCE_PRECISION_HINT"], "read back from the compiled model"


@pytest.mark.parametrize("stated, read", [("f32", OvType("f16")), ("bf16", OvType("f32")),
                                          ("f32", "<Type: 'bfloat16'>")])
def test_a_compiled_model_computing_in_another_precision_is_refused_in_words(stated, read):
    """The GPU plugin's default is f16, the CPU plugin's bf16 where the CPU has
    it; a hint the plugin cannot honour is not an error there, so it is read back."""
    with pytest.raises(lb.LoadError, match=f"compute in {lb.openvino_precision(Compiled(read))}.*"
                                           f"states {stated}"):
        lb.openvino_precision_holds(Compiled(read), stated)


def test_an_unstated_precision_is_refused_never_defaulted():
    with pytest.raises(lb.LoadError, match="LOUISE_COMPUTE_PRECISION"):
        lb.stated_precision({})
    assert lb.stated_precision({"LOUISE_COMPUTE_PRECISION": "bf16"}) == "bf16"


# ---- torch: the dtype applied and the fp32 matmul precision, read back --------------

class Tensor:
    def __init__(self, dtype: str, floating: bool = True) -> None:
        self.dtype, self.floating = dtype, floating

    def is_floating_point(self) -> bool:
        return self.floating


class Model:
    """A module whose `.to(dtype=…)` moves its floating tensors — or, `stuck`,
    leaves one behind (a buffer a model keeps in its own dtype)."""

    def __init__(self, dtype="float32", stuck: str | None = None) -> None:
        self.params = [Tensor(dtype), Tensor(dtype), Tensor("int64", floating=False)]
        self.bufs = [Tensor(dtype)] + ([Tensor(stuck)] if stuck else [])
        self.moves: list = []

    def to(self, dtype=None):
        self.moves.append(dtype)
        for t in (*self.params, *self.bufs[:1]):
            if t.floating:
                t.dtype = dtype
        return self

    def parameters(self):
        return list(self.params)

    def buffers(self):
        return list(self.bufs)


def _torch(reads_back: str | None = None):
    said: dict = {}
    return SimpleNamespace(float32="float32", bfloat16="bfloat16", float16="float16",
                           set_float32_matmul_precision=lambda p: said.__setitem__("p", p),
                           get_float32_matmul_precision=lambda: reads_back or said.get("p")), said


@pytest.mark.parametrize("server", sorted(SERVERS))
@pytest.mark.parametrize("precision, dtype", [("f32", "float32"), ("bf16", "bfloat16"),
                                              ("f16", "float16")])
def test_torchs_dtype_is_applied_and_read_back(server, precision, dtype):
    torch, said = _torch()
    model = Model()
    assert SERVERS[server].apply_precision(torch, model, precision) is model
    assert model.moves == [dtype], "model.to(dtype), explicitly"
    assert all(t.dtype == dtype for t in (*model.params[:2], *model.bufs))
    assert said["p"] == "highest", "fp32 matmuls at full precision: no TF32, no bf16 inside oneDNN"


@pytest.mark.parametrize("server", sorted(SERVERS))
def test_a_tensor_left_in_another_dtype_refuses_the_load(server):
    torch, _said = _torch()
    with pytest.raises(RuntimeError, match="holds float32 tensors.*stated bf16"):
        SERVERS[server].apply_precision(torch, Model(stuck="float32"), "bf16")


@pytest.mark.parametrize("server", sorted(SERVERS))
def test_an_fp32_matmul_precision_torch_does_not_read_back_refuses_the_load(server):
    torch, _said = _torch(reads_back="high")            # TF32 on CUDA, bf16 in oneDNN's
    with pytest.raises(RuntimeError, match="'high', not 'highest'"):
        SERVERS[server].apply_precision(torch, Model(), "f32")


@pytest.mark.parametrize("server", sorted(SERVERS))
def test_torch_reads_the_precision_from_the_models_env_and_refuses_none(server):
    s = SERVERS[server]
    assert s.PRECISION_ENV == lb.PRECISION_ENV == "LOUISE_COMPUTE_PRECISION", "one variable"
    assert s.stated_precision({"LOUISE_COMPUTE_PRECISION": "f16"}) == "f16"
    for env in ({}, {"LOUISE_COMPUTE_PRECISION": "float32"}):
        with pytest.raises(ValueError, match="states no compute precision"):
            s.stated_precision(env)


def test_madlads_dtype_must_say_the_stated_precision_before_a_weight_is_read(monkeypatch):
    """The backend's default `dtype` (bfloat16) never wins over a statement:
    the load is refused before torch is imported."""
    madlad = SERVERS["madlad"]
    monkeypatch.setenv("LOUISE_COMPUTE_PRECISION", "f32")
    with pytest.raises(ValueError, match="dtype:bfloat16 and LOUISE_COMPUTE_PRECISION=f32"):
        madlad.load("/w/madlad", "xpu", "bfloat16")
    monkeypatch.delenv("LOUISE_COMPUTE_PRECISION")
    with pytest.raises(ValueError, match="states no compute precision"):
        madlad.load("/w/madlad", "xpu", "bfloat16")


def test_dar_legal_it_and_medcpt_are_left_as_they_were():
    """DAR-legal-it ("Don't fucking touch dar legal", operator 2026-11-27) and
    MedCPT (held until gutenberg-nemo is done, operator 2026-11-27): their
    model code applies nothing yet."""
    for path in ("deploy/dar-legal-it/server.py", "deploy/medcpt-rerank/server.py",
                 "deploy/localai/backends/louise-dar-legal-it/backend.py",
                 "deploy/localai/backends/louise-medcpt-article-ov/backend.py",
                 "deploy/localai/backends/louise-medcpt-rerank/backend.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "LOUISE_COMPUTE_PRECISION" not in text and "openvino_precision" not in text, \
            f"{path}: exempt — when it is released, the rule's tests take it in"
