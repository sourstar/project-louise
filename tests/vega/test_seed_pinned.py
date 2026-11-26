"""Every model behind VeGa pins its seed (deploy/vega/stage/seed_pinned.py).

Tensor, 2026-11-26: a model file that pins no seed gets one drawn by LocalAI
at each load, and the seed is in the options the wrapper states — every load
is another run. For an encoder that is a run nobody validated."""
from __future__ import annotations

import builtins
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sp():
    spec = importlib.util.spec_from_file_location("seed_pinned", ROOT / "deploy/vega/stage/seed_pinned.py")
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["seed_pinned"] = mod
    spec.loader.exec_module(mod)
    return mod


def _json(path: Path, name: str, backend: str, *, embeddings=False, seed="absent", top=False) -> None:
    cfg = {"name": name, "backend": backend, "parameters": {"model": f"{name}.gguf"}}
    if embeddings:
        cfg["embeddings"] = True
    if seed != "absent":
        (cfg if top else cfg["parameters"])["seed"] = seed
    path.write_text("# a comment LocalAI's YAML reader skips\n" + json.dumps(cfg, indent=2), encoding="utf-8")


def test_the_staged_model_files_all_pin_their_seed(sp):
    found, unread = sp.look(ROOT / "deploy/vega/stage/models")
    assert unread == [] and len(found) >= 15 and all(f.pinned for f in found), \
        [f.file for f in found if not f.pinned]
    assert any(f.embeddings for f in found)


def test_an_embedder_behind_vega_with_no_seed_is_named_with_the_line_to_add(sp, tmp_path, capsys):
    _json(tmp_path / "vega-bge-m3.yaml", "vega-bge-m3", "vega-louise-llama-cpp", embeddings=True, seed=0)
    _json(tmp_path / "vega-new.yaml", "vega-new", "vega-louise-llama-cpp", embeddings=True)
    _json(tmp_path / "direct.yaml", "louise-translate-madlad", "louise-sycl-f16-llama-cpp-ed93e3c1b2e2")
    before = sorted(p.name for p in tmp_path.iterdir())
    assert sp.main(["--models", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "2 model file(s) behind VeGa, 1 pin their seed" in out
    assert "vega-new.yaml (vega-new, vega-louise-llama-cpp) is an EMBEDDER and pins NO seed" in out
    assert "Add `seed: 0` under `parameters:`" in out and "louise-translate-madlad" not in out, \
        "a model the engine serves directly states no run: not this check's"
    assert sorted(p.name for p in tmp_path.iterdir()) == before, "it reads, and nothing else"


def test_a_model_that_is_no_embedder_is_said_and_nothing_is_refused(sp, tmp_path, capsys):
    """The node, 2026-11-26: 59 files behind a vega package, 58 with seed 0,
    one with none — index-translate-9b, a translation model outside Louise's
    set. Each of its loads states another run; no vector depends on it."""
    _json(tmp_path / "vega-bge-m3.yaml", "vega-bge-m3", "vega-louise-llama-cpp", embeddings=True, seed=0)
    _json(tmp_path / "index-translate-9b.yaml", "index-translate-9b", "vega-louise-llama-cpp")
    assert sp.main(["--models", str(tmp_path), "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["status"] == 0 and got["behind_vega"] == 2
    assert got["no_seed"] == [{"file": "index-translate-9b.yaml", "model": "index-translate-9b",
                               "backend": "vega-louise-llama-cpp", "embedder": False}]
    assert any("said, nothing refused" in line for line in got["says"])


def test_the_seed_is_read_where_localai_reads_it_and_a_negative_one_is_not_pinned(sp, tmp_path):
    _json(tmp_path / "a.yaml", "a", "vega-louise-openvino", embeddings=True, seed=0, top=True)
    _json(tmp_path / "b.yaml", "b", "vega-louise-openvino", embeddings=True, seed=-1)
    _json(tmp_path / "c.yaml", "c", "vega-louise-openvino", embeddings=True, seed=True)
    found, _ = sp.look(tmp_path)
    assert {f.name: f.pinned for f in found} == {"a": True, "b": False, "c": False}


def test_plain_yaml_is_read_without_a_yaml_library_too(sp, tmp_path, monkeypatch):
    """The node's own files are plain YAML, and its host may have no PyYAML:
    a check that skipped what it could not parse would pass by reading
    nothing."""
    (tmp_path / "vega-dar.yaml").write_text(
        "# the legal encoder\nname: vega-dar-legal-it\nbackend: vega-louise-llama-cpp\nembeddings: true\n"
        "parameters:\n  model: dar.gguf\n  seed: 0\n", encoding="utf-8")
    (tmp_path / "vega-loose.yaml").write_text(
        "name: vega-loose\nbackend: vega-louise-llama-cpp\nembeddings: true\nparameters:\n  model: x.gguf\n"
        "# seed: 0 was here once\n", encoding="utf-8")
    real = builtins.__import__

    def no_yaml(name, *a, **k):
        if name == "yaml":
            raise ImportError("no PyYAML on this host")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_yaml)
    found, unread = sp.look(tmp_path)
    assert unread == [] and {f.name: f.pinned for f in found} == {"vega-dar-legal-it": True, "vega-loose": False}
    assert all(f.embeddings for f in found), "a commented-out seed pins nothing"


def test_a_file_that_cannot_be_read_decides_nothing_and_says_so(sp, tmp_path, capsys):
    _json(tmp_path / "vega-bge-m3.yaml", "vega-bge-m3", "vega-louise-llama-cpp", embeddings=True, seed=0)
    (tmp_path / "broken.yaml").write_text("{ this is : [ not a model file", encoding="utf-8")
    status = sp.main(["--models", str(tmp_path)])
    out = capsys.readouterr().out
    assert status in (0, 2), "a YAML library may read what a JSON reader cannot; a file with no backend is no model"
    if status == 2:
        assert "broken.yaml could not be read as a model file: nothing is decided for it" in out
    with pytest.raises(SystemExit, match="cannot list the models path"):
        sp.main(["--models", str(tmp_path / "nowhere")])
