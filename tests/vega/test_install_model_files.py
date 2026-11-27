"""deploy/vega/stage/install_model_files.py: a package and its model files,
together (compute precision, stated; docs/notes/approved-encoders.md). The
wrapper states no run for a model file that does not say what it computes in,
so build_vega_package.sh writes the files' statements FIRST and builds nothing
when one cannot be stated — the order that would be an outage is not
reachable."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from .conftest import ROOT, load_wrapper

vw = load_wrapper()
_spec = importlib.util.spec_from_file_location(
    "install_model_files_under_test", ROOT / "deploy" / "vega" / "stage" / "install_model_files.py")
imf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = imf                       # its dataclasses look themselves up
_spec.loader.exec_module(imf)

STAGE = ROOT / "deploy" / "vega" / "stage"
SYCL = "louise-sycl-f16-llama-cpp-95d9f59679df-stack-7c301a7ba437"
PKG = "vega-louise-llama-cpp"
KEY = vw.PRECISION_ENV


def _plan(models, staged, backend=SYCL, package=PKG):
    return imf.plan(vw, package=package, backend=backend, models=models, staged=staged)


def _write(d: Path, name: str, text: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    return d / name


#: a chat model's file as LocalAI writes one from a gallery entry: YAML, keys sorted
GALLERY_YAML = """backend: vega-louise-llama-cpp
context_size: 16384
env:
    GGML_SYCL_ENABLE_DNN: "0"
    GGML_SYCL_FA_ONEDNN: "0"
name: louise-sum-x
options:
    - use_jinja:true
    - parallel:1
parameters:
    model: sum-x.gguf
    seed: 0
template:
    chat: |
        {{ .Input }}
        key: not an option
"""


def test_the_staged_statement_is_written_into_the_files_behind_the_package_and_nothing_else(tmp_path):
    staged, models = tmp_path / "staged", tmp_path / "models"
    _write(staged, "vega-a.yaml", json.dumps({"name": "vega-a", "backend": PKG,
                                               "env": {KEY: "f16", "NEO_CACHE_DIR": "/n"}}))
    _write(staged, "louise-sum-x.json", json.dumps({"name": "louise-sum-x", "overrides": {
        "backend": PKG, "env": {KEY: "f16"}}}))
    node_a = _write(models, "vega-a.yaml", "# the node's own\n" + json.dumps(
        {"name": "vega-a", "backend": PKG, "embeddings": True, "options": ["parallel:1"],
         "env": {"NEO_CACHE_DIR": "/n"}, "parameters": {"model": "a.gguf"}}, indent=2) + "\n")
    node_sum = _write(models, "louise-sum-x.yaml", GALLERY_YAML)
    held = _write(models, "vega-medcpt-query.yaml", json.dumps(
        {"name": "vega-medcpt-query", "backend": PKG, "options": ["parallel:1"],
         "parameters": {"model": "weights/medcpt/MedCPT-Query-Encoder-f16.gguf"}}))
    other = _write(models, "chat-elsewhere.yaml", "backend: another-backend\nname: x\n")
    before = {p: p.read_text() for p in (held, other)}
    p = _plan(models, [staged])
    assert p.refused == []
    assert p.stated == {"vega-a.yaml": "f16", "louise-sum-x.yaml": "f16"}
    assert p.exempt == {"vega-medcpt-query.yaml": vw.PRECISION_EXEMPT["weights:MedCPT-Query-Encoder-f16.gguf"]}
    imf.write(p)
    assert json.loads(node_a.read_text().split("\n", 1)[1])["env"] == {KEY: "f16", "NEO_CACHE_DIR": "/n"}
    assert node_a.read_text().startswith("# the node's own\n"), "the rest of the node's file kept"
    text = node_sum.read_text()
    assert text == GALLERY_YAML.replace('env:\n', f'env:\n    {KEY}: "f16"\n'), "one line, nothing else"
    assert imf.read_model(text)["env"] == {KEY: "f16", "GGML_SYCL_ENABLE_DNN": "0",
                                           "GGML_SYCL_FA_ONEDNN": "0"}
    assert {p: p.read_text() for p in (held, other)} == before, "held and foreign files untouched"
    assert sorted(f.name for f in models.iterdir()) == sorted(
        ["vega-a.yaml", "louise-sum-x.yaml", "vega-medcpt-query.yaml", "chat-elsewhere.yaml"])
    assert _plan(models, [staged]).writes == {}, "a second pass writes nothing"


@pytest.mark.parametrize("node, staged_env, why", [
    ({}, None, "no staged file of `vega-b` states one"),                       # nothing to install
    ({KEY: "f32"}, {KEY: "f16"}, "states LOUISE_COMPUTE_PRECISION=f32, its staged file f16"),
    ({KEY: "f32"}, None, "whose build computes in f16"),                       # the wrapper's own rule
])
def test_a_file_that_cannot_state_its_precision_refuses_everything(tmp_path, node, staged_env, why):
    staged, models = tmp_path / "staged", tmp_path / "models"
    _write(staged, "vega-a.yaml", json.dumps({"name": "vega-a", "backend": PKG, "env": {KEY: "f16"}}))
    if staged_env is not None:
        _write(staged, "vega-b.yaml", json.dumps({"name": "vega-b", "backend": PKG, "env": staged_env}))
    _write(models, "vega-a.yaml", json.dumps({"name": "vega-a", "backend": PKG, "env": {}}))
    _write(models, "vega-b.yaml", json.dumps({"name": "vega-b", "backend": PKG, "env": node,
                                               "parameters": {"model": "b.gguf"}}))
    snapshot = {f.name: f.read_text() for f in models.iterdir()}
    rc = imf.main(["--package", PKG, "--backend", SYCL, "--models", str(models),
                   "--staged", str(staged)])
    assert rc == 1
    assert {f.name: f.read_text() for f in models.iterdir()} == snapshot, "nothing written"
    assert any(why in line for line in _plan(models, [staged]).refused)


def test_check_writes_nothing_and_an_unreadable_file_behind_the_package_decides_nothing(tmp_path):
    staged, models = tmp_path / "staged", tmp_path / "models"
    _write(staged, "vega-a.yaml", json.dumps({"name": "vega-a", "backend": PKG, "env": {KEY: "f16"}}))
    a = _write(models, "vega-a.yaml", json.dumps({"name": "vega-a", "backend": PKG}))
    args = ["--package", PKG, "--backend", SYCL, "--models", str(models), "--staged", str(staged)]
    assert imf.main(args + ["--check"]) == 0 and KEY not in a.read_text()
    _write(models, "broken.yaml", f"backend {PKG}\n")
    assert imf.main(args) == 2 and KEY not in a.read_text()
    (models / "broken.yaml").write_text("not a model file at all\n")       # nothing of the package
    assert imf.main(args) == 0 and KEY in a.read_text()


def test_the_repos_files_as_the_node_held_them_before_the_rule_are_all_installable(tmp_path):
    """Every staged model file and gallery entry, with its precision taken
    out (the node's files today): each package's files are all written or
    exempt, none refused — so the package build that requires it can run."""
    models = tmp_path / "models"
    models.mkdir()
    for path in [*sorted((STAGE / "gallery").glob("*.json")), *sorted((STAGE / "models").glob("*.yaml"))]:
        doc = imf.read_model(path.read_text(encoding="utf-8"))
        doc["env"] = {k: v for k, v in (doc.get("env") or {}).items() if k != KEY}
        (models / f"{doc['name']}.yaml").write_text(json.dumps(doc, indent=2) + "\n")
    staged = [STAGE / "models", STAGE / "gallery"]
    real = {"vega-louise-llama-cpp": SYCL, "vega-louise-llama-cpp-cpu": "louise-cpu-llama-cpp-ea2417d91dcc",
            "vega-louise-openvino": "louise-python-20261030",
            "vega-louise-pytorch": "louise-python-20261021a"}
    exempt = set()
    for package, backend in real.items():
        p = _plan(models, staged, backend=backend, package=package)
        assert p.refused == [], (package, p.refused)
        assert set(p.writes) == {models / n for n in p.stated}, package
        exempt |= set(p.exempt)
    assert exempt == {"vega-dar-legal-it.yaml", "vega-dar-legal-it-cpu.yaml", "vega-medcpt-article-ov.yaml",
                      "vega-medcpt-article-ov-cpu.yaml", "vega-medcpt-article.yaml",
                      "vega-medcpt-query.yaml", "vega-medcpt-query-cpu.yaml", "louise-rerank-medcpt.yaml"}


def test_the_package_build_installs_its_model_files_first_and_requires_them():
    text = (STAGE / "build_vega_package.sh").read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
    install = next(i for i, l in enumerate(lines) if "install_model_files.py" in l)
    clean = next(i for i, l in enumerate(lines) if "-exec rm -rf" in l and "mindepth 1" in l)
    assert install < clean, "the files first: a refusal leaves the package as it was"
    assert any('"${MODELS:?' in l for l in lines[:install]), "no build without LocalAI's models directory"
    assert "set -eu" in lines, "a refusal stops the build"
    src = (STAGE / "stage_src.sh").read_text(encoding="utf-8")
    assert "deploy/localai-nvidia/models" in src and "deploy/localai-desktop/models" in src, \
        "a peer's own staged files reach its build"
