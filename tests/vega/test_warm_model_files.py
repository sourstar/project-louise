"""KEEP THE EMBEDDERS' WEIGHT FILES IN THE FILE CACHE (operator, 2026-11-25:
"cache the model to reload it from disk warm"; deploy/vega/warm_model_files.py).

A model unloaded for room outside query mode is loaded again when it is next
asked for, and that load is several times slower from the drive than from
the file cache (docs/notes/model-profile.md: qwen3-embedding-4b 33.7 s
against about 6). The host-side step reads the embedders' weight files
again on a timer: which files is the system's to say (LocalAI's own model
files, the container's mounts), it reads and writes nothing, and each pass
says what it found."""
from __future__ import annotations

import importlib.util
import json
import sys
from types import SimpleNamespace

import pytest

from .conftest import ROOT


def _load():
    name = "warm_model_files_under_test"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / "vega" / "warm_model_files.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture
def w():
    mod = _load()
    mod._stop = False
    yield mod
    mod._stop = False


@pytest.fixture
def node(tmp_path):
    """LocalAI's models path and the weights mounted into it from elsewhere,
    as on the node: `<models>/weights/<name>` in the container is another
    directory of the host."""
    models, weights = tmp_path / "localai" / "models", tmp_path / "apps" / "models"
    models.mkdir(parents=True)
    (weights / "bge-m3").mkdir(parents=True)
    (weights / "bge-m3" / "bge-m3-FP16.gguf").write_bytes(b"g" * 300_000)
    (weights / "medcpt" / "medcpt-article-ov").mkdir(parents=True)
    (weights / "medcpt" / "medcpt-article-ov" / "openvino_model.bin").write_bytes(b"o" * 120_000)
    (weights / "medcpt" / "medcpt-article-ov" / "openvino_model.xml").write_bytes(b"<x/>" * 100)
    (weights / "chat").mkdir()
    (weights / "chat" / "chat-9b.gguf").write_bytes(b"c" * 500_000)
    # our staged files: JSON under comment lines ("JSON, which is YAML")
    (models / "vega-bge-m3.yaml").write_text(
        '# vega-bge-m3 — staged\n' + json.dumps({"name": "vega-bge-m3", "embeddings": True,
                                                 "parameters": {"model": "weights/bge-m3/bge-m3-FP16.gguf"}}))
    (models / "vega-medcpt-article-ov.yaml").write_text(json.dumps(
        {"name": "vega-medcpt-article-ov", "embeddings": True,
         "parameters": {"model": "weights/medcpt/medcpt-article-ov"}}))
    (models / "louise-chat.yaml").write_text(json.dumps(
        {"name": "louise-chat", "parameters": {"model": "weights/chat/chat-9b.gguf"}}))
    (models / "gallery.yaml").write_text(json.dumps(
        {"name": "gallery", "embeddings": True, "parameters": {"model": "huggingface://org/repo/file.gguf"}}))
    (models / "notes.txt").write_text("not a model")
    mounts = [("/models/weights/bge-m3", str(weights / "bge-m3")), ("/models/weights/medcpt", str(weights / "medcpt")),
              ("/models/weights/chat", str(weights / "chat")), ("/models", str(models))]
    return SimpleNamespace(models=models, weights=weights, mounts=mounts)


def test_the_embedders_are_the_models_whose_own_file_says_so(w, node):
    found = w.models_in(node.models, "/models")
    assert [(m.name, m.embeddings) for m in found] == [
        ("louise-chat", False), ("vega-bge-m3", True), ("vega-medcpt-article-ov", True)], \
        "every model with local weights, by name; a remote reference has no file to keep warm"
    assert found[1].container_path == "/models/weights/bge-m3/bge-m3-FP16.gguf", "as LocalAI's container names it"


def test_a_containers_path_is_found_on_the_host_through_its_mounts(w, node):
    mounts = sorted(node.mounts, key=lambda m: -len(m[0]))
    assert w.host_path("/models/weights/bge-m3/bge-m3-FP16.gguf", mounts) == node.weights / "bge-m3" / "bge-m3-FP16.gguf", \
        "the longest destination that holds it: the weights are mounted INTO the models path"
    assert w.host_path("/models/vega-bge-m3.yaml", mounts) == node.models / "vega-bge-m3.yaml"
    assert str(w.host_path("/elsewhere/x.gguf", mounts)) == "/elsewhere/x.gguf", "under no mount: as it stands"

    def docker(cmd, **kw):
        assert cmd[:3] == ["docker", "inspect", "ix-localai-localai-1"]
        rows = [{"Destination": d, "Source": s, "Type": "bind"} for d, s in node.mounts]
        return SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr="")
    got = w.container_mounts("ix-localai-localai-1", run=docker)
    assert got[0][0].startswith("/models/weights/") and got[-1] == ("/models", str(node.models)), "longest first"
    assert w.container_mounts("x", run=lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="no")) == []


def test_a_pass_reads_every_file_of_every_embedder_whole_and_writes_nothing(w, node, monkeypatch):
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in node.weights.rglob("*") if p.is_file()}
    embedders = [m for m in w.models_in(node.models, "/models") if m.embeddings]
    ticks = iter(range(100))
    reads = w.warm(embedders, sorted(node.mounts, key=lambda m: -len(m[0])), clock=lambda: float(next(ticks)))
    assert [(r.name, r.files, r.bytes) for r in reads] == [
        ("vega-bge-m3", 1, 300_000), ("vega-medcpt-article-ov", 2, 120_400)], "a directory model: every file under it"
    assert not any(r.missing for r in reads)
    assert {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in node.weights.rglob("*") if p.is_file()} == before
    assert sorted(p.name for p in node.models.iterdir()) == sorted(
        ["vega-bge-m3.yaml", "vega-medcpt-article-ov.yaml", "louise-chat.yaml", "gallery.yaml", "notes.txt"])


def test_the_rate_says_whether_a_file_came_from_memory_or_from_the_drive(w):
    warm = w.Read("vega-qwen3-embedding-4b", files=1, bytes=2_500 * w.MIB, seconds=0.4)
    cold = w.Read("vega-dar-legal-it", files=1, bytes=4_500 * w.MIB, seconds=9.0)
    small = w.Read("vega-medcpt-query", files=3, bytes=30 * w.MIB, seconds=0.01)
    assert "6,250 MiB/s) — from memory" in warm.says()
    assert "500 MiB/s) — FROM THE DRIVE: it had been dropped" in cold.says()
    assert "MiB/s" not in small.says(), "too small for its rate to say anything"
    assert w.summary([warm, cold, small]) == \
        "warm: 3 model(s), 7,030 MiB read in 9.4 s — FROM THE DRIVE: vega-dar-legal-it"
    assert w.summary([warm, small]).endswith("— all from memory")


def test_weights_that_are_not_on_this_host_are_said_and_the_pass_goes_on(w, node):
    gone = w.Model("vega-gone", "/models/weights/gone/x.gguf", True)
    here = w.models_in(node.models, "/models")[1]
    reads = w.warm([gone, here], sorted(node.mounts, key=lambda m: -len(m[0])))
    assert "its weights are not on this host" in reads[0].says() and reads[1].bytes == 300_000


def test_one_pass_by_default_the_embedders_alone_and_every_model_with_all(w, node, monkeypatch, capsys):
    monkeypatch.setattr(w, "container_mounts", lambda container, run=None: sorted(node.mounts, key=lambda m: -len(m[0])))
    assert w.main(["--models", str(node.models)]) == 0
    out = capsys.readouterr().out
    assert "vega-bge-m3: " in out and "vega-medcpt-article-ov: " in out and "louise-chat" not in out
    assert out.strip().splitlines()[-1].startswith("warm: 2 model(s), ")
    assert w.main(["--models", str(node.models), "--all", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert [m["name"] for m in got["models"]] == ["louise-chat", "vega-bge-m3", "vega-medcpt-article-ov"]
    assert got["models"][0]["bytes"] == 500_000 and got["says"].startswith("warm: 3 model(s)")


def test_a_stop_ends_the_loop_after_the_file_in_hand(w, node, monkeypatch, capsys):
    monkeypatch.setattr(w, "container_mounts", lambda container, run=None: sorted(node.mounts, key=lambda m: -len(m[0])))
    real = w.read_whole
    seen = []

    def read(path):
        seen.append(path.name)
        w._stop = True                               # the stop arrives during the first file
        return real(path)
    monkeypatch.setattr(w, "read_whole", read)
    assert w.main(["--models", str(node.models), "--loop", "300"]) == 0, "a loop told to stop ends cleanly"
    assert seen == ["bge-m3-FP16.gguf"], "the file in hand is read whole; no other is begun"
    w._stop = False
    monkeypatch.setattr(w, "read_whole", read)
    seen.clear()
    assert w.main(["--models", str(node.models)]) == 130, "a single pass cut short says so"
