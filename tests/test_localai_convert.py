"""deploy/localai/llama-cpp/convert.sh: a GGUF made with the llama.cpp OF AN ENGINE
SERIES, reproducible from the series and the model's files alone
(docs/notes/medcpt-on-llama-cpp.md §6; München, 2026-11-27: the patched checkout
lived only inside the build container).

Held here, without the network: that it names a series as build.sh does (one
series, one name), that it builds its tree as build.sh and LocalAI's prepare.sh do,
and what it takes as the input - the repository's name, no model card, no licence,
safetensors over pytorch_model.bin, a cache directory's one snapshot. Measured once by
hand (2026-11-27): MedCPT's article and cross encoders converted from the series give
the GGUFs whose sha256 the MedCPT note gives."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy" / "localai" / "llama-cpp" / "convert.sh"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"


def _run(*args, **env):
    return subprocess.run(["sh", str(TOOL), *map(str, args)], capture_output=True, text=True, cwd=ROOT,
                          env=dict(os.environ, **env))


@pytest.mark.parametrize("series", sorted(STAGED.glob("engine-*.series"))[:6] + [ROOT / "deploy" / "localai" / "patches" / "series"],
                         ids=lambda p: p.name)
def test_it_names_a_series_as_the_build_does(series):
    built = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True, cwd=ROOT,
                           env=dict(os.environ, SERIES_FILE=str(series), NAME_ONLY="1"))
    converted = _run(series, "/nonexistent", "/nonexistent", NAME_ONLY="1")
    assert built.returncode == 0 and converted.returncode == 0, (built.stderr, converted.stderr)
    assert converted.stdout.strip() == built.stdout.strip()


def test_its_tree_is_the_build_s_and_prepare_s():
    text = TOOL.read_text(encoding="utf-8")
    build = BUILD.read_text(encoding="utf-8")
    for line in ('BASE=$(sed -n \'s/^base //p\' "$SERIES_FILE")',
                 "SERIES=$(grep -v '^#' \"$SERIES_FILE\" | grep -v '^base ' | grep .)",
                 'HEAD=$(cd "$PATCHES" && cat "$SERIES_FILE" $SERIES | sha256sum | cut -c1-12)',
                 'fetch -q --depth 1 https://github.com/mudler/LocalAI.git "$BASE"'):
        assert line in text and line in build, line
    # llama.cpp at the version LocalAI's backend pins, its patches as prepare.sh applies them
    assert "LLAMA_VERSION=$(sed -n 's/^LLAMA_VERSION?=//p' \"$LL/Makefile\")" in text
    assert 'for p in $(ls "$LL/patches"); do\n  patch -s -d "$WORK/llama.cpp" -p1 < "$LL/patches/$p"' in text
    assert "requirements/requirements-convert_hf_to_gguf.txt" in text and "python:3.12-slim" in text


def _snapshot(d: Path, *names):
    d.mkdir(parents=True)
    for n in names:
        (d / n).write_text(n, encoding="utf-8")
    return d


def test_the_input_is_the_repository_s_files_under_its_name(tmp_path):
    snap = _snapshot(tmp_path / "models--ncbi--MedCPT-Article-Encoder" / "snapshots" / "4f6",
                     "config.json", "tokenizer.json", "vocab.txt", "model.safetensors", "pytorch_model.bin",
                     "README.md", "LICENSE", ".gitattributes", "special_tokens_map.json")
    done = _run(STAGED / "engine-guard.series", snap.parents[1], tmp_path / "x.gguf", STAGE_ONLY="1")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[0] == "model_name MedCPT-Article-Encoder", "the repository's name, as the converter writes it"
    assert sorted(lines[1:]) == ["config.json", "model.safetensors", "special_tokens_map.json", "tokenizer.json", "vocab.txt"]


def test_a_snapshot_with_weights_only_as_bin_keeps_them_and_a_name_can_be_given(tmp_path):
    snap = _snapshot(tmp_path / "MedCPT-Cross-Encoder", "config.json", "pytorch_model.bin", "README.md")
    done = _run(STAGED / "engine-guard.series", snap, tmp_path / "x.gguf", STAGE_ONLY="1")
    assert done.stdout.splitlines() == ["model_name MedCPT-Cross-Encoder", "config.json", "pytorch_model.bin"]
    named = _run(STAGED / "engine-guard.series", snap, tmp_path / "x.gguf", STAGE_ONLY="1", MODEL_NAME="other")
    assert named.stdout.splitlines()[0] == "model_name other"


def test_a_cache_without_exactly_one_snapshot_is_refused(tmp_path):
    cache = tmp_path / "models--ncbi--MedCPT-Query-Encoder"
    for rev in ("a", "b"):
        _snapshot(cache / "snapshots" / rev, "config.json")
    done = _run(STAGED / "engine-guard.series", cache, tmp_path / "x.gguf", STAGE_ONLY="1")
    assert done.returncode == 2 and "with one snapshot (2)" in done.stderr
