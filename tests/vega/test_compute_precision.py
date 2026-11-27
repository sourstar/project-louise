"""COMPUTE PRECISION, STATED (operator, 2026-11-27, on learning that OpenVINO's
GPU plugin runs MedCPT in f16 by its default: "HALF FUCKING PRECISION AS A
DEFAULT!!!"; docs/notes/approved-encoders.md "Compute precision, stated").

Every model file the repo stages says what its model computes in —
`LOUISE_COMPUTE_PRECISION` in its `env`, so the stated run carries it — and
says what it REALLY computes in: f16 for every llama.cpp model (the node's
SYCL-F16 build by its build; the CPU, CUDA and HIP builds by their code, the
weights here being F16 or quantized), what torch was given for the torch ones.
VeGa's wrapper states no run that leaves it unstated, nor one claiming what its
build does not compute (vega_wrapper.stated_precision), as it does for the
determinism contract.

EXEMPT, BY NAME, each with its reason (as invariant 5's list,
tests/test_invariant_5_bandwidth.py): files left exactly as they were, which
state nothing and are stated by the wrapper as before. The wrapper names the
same loads (vega_wrapper.PRECISION_EXEMPT); a stale entry fails here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.vega import loadopts

from .conftest import ROOT
from .test_wrapper_logic import LOAD, _backend_dir, _run_kw, vw

STAGE = ROOT / "deploy" / "vega" / "stage"
#: every directory of model files the repo stages, and the real backend behind
#: each `backend` a file names there (the wrapper sees the real one's name):
#: the node (approved-encoders.md, "The wrapper each package runs"), the NVIDIA
#: peer (deploy/localai-nvidia/README.md), the AMD desktop (localai-desktop)
SYCL = "louise-sycl-f16-llama-cpp-95d9f59679df-stack-7c301a7ba437"
DIRS: dict[str, dict[str, str]] = {
    "deploy/vega/stage/models": {"vega-louise-llama-cpp": SYCL,
                                 "vega-louise-llama-cpp-cpu": "louise-cpu-llama-cpp-ea2417d91dcc",
                                 "vega-louise-openvino": "louise-python-20261030",
                                 "vega-louise-pytorch": "louise-python-20261021a"},
    "deploy/vega/stage/gallery": {"vega-louise-llama-cpp": SYCL},
    "deploy/localai-nvidia/models": {"vega-louise-llama-cpp": "cuda13-llama-cpp",
                                     "vega-louise-python": "louise-python"},
    "deploy/localai-desktop/models": {"vega-louise-llama-cpp": "rocm-llama-cpp"},
}

_DAR = vw.PRECISION_EXEMPT["task:dar-legal-it"]
_MEDCPT = "held until gutenberg-nemo is done (operator, 2026-11-27)"
#: the files left as they were, by path, with the reason
EXEMPT: dict[str, str] = {
    "deploy/vega/stage/models/vega-dar-legal-it.yaml": _DAR,
    "deploy/vega/stage/models/vega-dar-legal-it-cpu.yaml": _DAR,
    "deploy/localai-nvidia/models/vega-dar-legal-it.yaml": _DAR,
    "deploy/vega/stage/models/vega-medcpt-article-ov.yaml": _MEDCPT,
    "deploy/vega/stage/models/vega-medcpt-article-ov-cpu.yaml": _MEDCPT,
    "deploy/vega/stage/models/vega-medcpt-article.yaml": _MEDCPT,
    "deploy/vega/stage/models/vega-medcpt-query.yaml": _MEDCPT,
    "deploy/vega/stage/models/vega-medcpt-query-cpu.yaml": _MEDCPT,
    "deploy/vega/stage/models/louise-rerank-medcpt.yaml": _MEDCPT,
}
#: what a model that is not llama.cpp states, and why it is that (today's
#: behaviour, never a claim: changing one is a new run, gated)
NOT_LLAMA: dict[str, str] = {
    # the remote code's config keeps float32 wherever CUDA is not available (the B70 is XPU)
    "deploy/vega/stage/models/vega-jina-v3.yaml": "f32",
    "deploy/vega/stage/models/vega-jina-v3-query.yaml": "f32",
    # its `dtype:bfloat16`
    "deploy/vega/stage/models/louise-translate-madlad-torch.yaml": "bf16",
    # the same remote code on CUDA keeps its config's bfloat16 ("auto"): what it ran unstated
    "deploy/localai-nvidia/models/vega-jina-v3.yaml": "bf16",
}


def _doc(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)["overrides"]
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    try:
        return json.loads(body)
    except ValueError:
        return yaml.safe_load(text)


def _files() -> list[tuple[str, dict, str]]:
    """(repo path, the model's settings, the real backend's name) of every staged file."""
    out = []
    for directory, real in DIRS.items():
        for path in sorted((ROOT / directory).glob("*.json" if "gallery" in directory else "*.yaml")):
            doc = _doc(path)
            backend = str(doc.get("backend") or "")
            out.append((f"{directory}/{path.name}", doc, real.get(backend, backend)))
    return out


def _canon(doc: dict) -> loadopts.Canon:
    model = (doc.get("parameters") or {}).get("model") or doc.get("name") or "m"
    return vw.canon_from_protojson(json.dumps({
        "Model": model, "ModelFile": f"/models/{model}", "Options": doc.get("options") or [],
        "EnvVars": doc.get("env") or {}}))


FILES = _files()


def test_the_files_are_read():
    assert len(FILES) >= 70
    assert {d for d in DIRS} == {p.rsplit("/", 1)[0] for p, _d, _r in FILES}


@pytest.mark.parametrize("where, doc, real", FILES, ids=[p for p, _d, _r in FILES])
def test_every_staged_model_states_what_it_computes_in_or_is_exempt_by_name(where, doc, real):
    said = (doc.get("env") or {}).get(vw.PRECISION_ENV)
    if where in EXEMPT:
        assert said is None, f"{where}: exempt, so left as it was — it states nothing"
        assert vw.precision_exempt(_canon(doc)) == EXEMPT[where], \
            f"{where}: the wrapper names it exempt for the same reason"
        return
    assert said in vw.PRECISIONS, f"{where}: states no compute precision ({vw.PRECISION_ENV})"
    if "llama" in real.split("-"):
        assert said == "f16", (f"{where}: llama.cpp takes f16 activations into its products "
                               f"(the SYCL-F16 build by its build; every staged GGUF is F16 or "
                               f"quantized, so the CPU, CUDA and HIP builds too, by their code)")
    else:
        assert NOT_LLAMA.get(where) == said, f"{where}: {said}, not the precision named here"
    assert vw.stated_precision(real, _canon(doc)) == said, "the wrapper takes it"


def test_every_exemption_and_every_named_precision_is_a_staged_file():
    paths = {p for p, _d, _r in FILES}
    assert set(EXEMPT) <= paths, f"stale exemptions: {sorted(set(EXEMPT) - paths)}"
    assert set(NOT_LLAMA) <= paths, f"stale entries: {sorted(set(NOT_LLAMA) - paths)}"


def test_the_exemptions_are_the_operators_with_the_date():
    assert set(vw.PRECISION_EXEMPT) == {
        "task:dar-legal-it", "task:medcpt-article", "task:medcpt-rerank",
        "weights:MedCPT-Article-Encoder-f16.gguf", "weights:MedCPT-Query-Encoder-f16.gguf"}
    assert "Don't fucking touch dar legal" in _DAR and "2026-11-27" in _DAR
    assert {r for k, r in vw.PRECISION_EXEMPT.items() if "medcpt" in k.lower()} == {_MEDCPT}


# ---- the wrapper ------------------------------------------------------------------

def _load(env, options=("parallel:1",), model="bge-m3.gguf"):
    return vw.canon_from_protojson(json.dumps(json.loads(LOAD) | {
        "EnvVars": env, "Options": list(options), "Model": model, "ModelFile": f"/models/{model}"}))


def test_a_run_whose_model_states_no_precision_is_never_stated(tmp_path):
    kw = _run_kw(tmp_path, "louise-cpu-llama-cpp-ea2417d91dcc", "cpu")
    with pytest.raises(loadopts.Unstatable, match=f"states no compute precision.*{vw.PRECISION_ENV}"):
        vw.state_run(canon=_load({}), environ={}, **kw)
    for word in ("fp32", "F16", "half", ""):
        env = {vw.PRECISION_ENV: word}
        with pytest.raises(loadopts.Unstatable, match="is not one of f32, bf16, f16"):
            vw.state_run(canon=_load(env), environ=env, **kw)


def test_a_chat_model_states_its_precision_too(tmp_path):
    """Not only an embedder: every model the wrapper launches (determinism is
    an embedder's contract, the precision everyone's)."""
    kw = _run_kw(tmp_path, SYCL)
    chat = vw.canon_from_protojson(json.dumps(json.loads(LOAD) | {
        "Embeddings": False, "EnvVars": {}, "Options": ["parallel:1"]}))
    assert not vw.is_embedder(chat)
    with pytest.raises(loadopts.Unstatable, match="states no compute precision"):
        vw.state_run(canon=chat, environ={}, **kw)


@pytest.mark.parametrize("claim", ["f32", "bf16"])
def test_a_sycl_f16_engine_states_f16_and_another_claim_is_refused(tmp_path, claim):
    """GGML_SYCL_F16: its products take half activations (upstream-bugs.md S1)."""
    mode = {"GGML_SYCL_ENABLE_DNN": "0", "GGML_SYCL_FA_ONEDNN": "0"}
    for name in (SYCL, "intel-sycl-f16-llama-cpp", "louise-llama-cpp-sycl_f16-ed93e3c1b2e2"):
        assert vw.build_precision(name) == "f16", name
        kw = _run_kw(tmp_path / name, name)
        env = mode | {vw.PRECISION_ENV: claim}
        with pytest.raises(loadopts.Unstatable, match=f"{claim} on {name}, whose build computes in f16"):
            vw.state_run(canon=_load(env), environ=env, **kw)
        env = mode | {vw.PRECISION_ENV: "f16"}
        _rid, run = vw.state_run(canon=_load(env), environ=env, **kw)
        assert run["engine"]["env"][vw.PRECISION_ENV] == "f16", "the run records what it computes in"


def test_a_build_the_wrapper_does_not_know_is_held_to_its_statement_only():
    """The CPU, CUDA and HIP builds compute in what their weights take (stated
    in the file, not measured): the wrapper knows the SYCL-F16 build alone."""
    for name in ("louise-cpu-llama-cpp-ea2417d91dcc", "cuda13-llama-cpp", "rocm-llama-cpp",
                 "louise-python-20261021a", "intel-sycl-f32-llama-cpp", "fake-embed"):
        assert vw.build_precision(name) is None, name


def test_the_precision_is_in_the_run_so_two_precisions_are_two_runs(tmp_path):
    kw = _run_kw(tmp_path, "louise-cpu-llama-cpp-ea2417d91dcc", "cpu")
    ids = set()
    for precision in vw.PRECISIONS:
        env = {vw.PRECISION_ENV: precision}
        rid, run = vw.state_run(canon=_load(env), environ=env, **kw)
        assert run["engine"]["env"] == env
        ids.add(rid)
    assert len(ids) == 3


def test_a_torch_dtype_option_must_say_the_stated_precision(tmp_path):
    kw = _run_kw(tmp_path, "louise-python-20261021a", "the card")
    opts = ("task:madlad", "device:xpu", "dtype:bfloat16")
    env = {"LOUISE_TORCH_DETERMINISTIC": "1", vw.PRECISION_ENV: "f32"}
    with pytest.raises(loadopts.Unstatable, match="dtype:bfloat16 and LOUISE_COMPUTE_PRECISION=f32"):
        vw.state_run(canon=_load(env, opts), environ=env, **kw)
    env = env | {vw.PRECISION_ENV: "bf16"}
    vw.state_run(canon=_load(env, opts), environ=env, **kw)


def test_an_exempt_load_is_stated_as_before_and_only_while_it_states_nothing(tmp_path):
    """DAR-legal-it and MedCPT (operator, 2026-11-27): their files are not
    touched, so the wrapper asks them nothing — and one that does state a
    precision is held to it like any other."""
    torch_kw = _run_kw(tmp_path / "torch", "louise-python-20261021a", "the card")
    on = {"LOUISE_TORCH_DETERMINISTIC": "1"}
    _rid, run = vw.state_run(canon=_load(on, ("task:dar-legal-it", "device:xpu")), environ=on,
                             **torch_kw)
    assert run["engine"]["env"] == on, "stated exactly as before the rule"
    with pytest.raises(loadopts.Unstatable, match="states no compute precision"):
        vw.state_run(canon=_load(on, ("task:jina-v3", "device:xpu")), environ=on, **torch_kw)
    ov_kw = _run_kw(tmp_path / "ov", "louise-python-20261030", "the card")
    measured = {vw.MEASURED_ENV: "louise-python-20261030"}
    for task in ("medcpt-article", "medcpt-rerank"):
        vw.state_run(canon=_load(measured, (f"task:{task}", "device:xpu")), environ=measured, **ov_kw)
    sycl_kw = _run_kw(tmp_path / "sycl", SYCL)
    mode = {"GGML_SYCL_ENABLE_DNN": "0", "GGML_SYCL_FA_ONEDNN": "0"}
    for weights in ("MedCPT-Query-Encoder-f16.gguf", "MedCPT-Article-Encoder-f16.gguf"):
        vw.state_run(canon=_load(mode, model=f"weights/medcpt/{weights}"), environ=mode, **sycl_kw)
        wrong = mode | {vw.PRECISION_ENV: "f32"}
        with pytest.raises(loadopts.Unstatable, match="whose build computes in f16"):
            vw.state_run(canon=_load(wrong, model=f"weights/medcpt/{weights}"), environ=wrong, **sycl_kw)
    with pytest.raises(loadopts.Unstatable, match="states no compute precision"):
        vw.state_run(canon=_load(mode, model="weights/bge-m3/bge-m3-FP16.gguf"), environ=mode, **sycl_kw)
