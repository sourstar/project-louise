"""Staged 0035 and 0036, two defects the node found on 2026-11-27, staged with MedCPT
as one candidate (engine-guard.series): a refused speech input says why (P33), and a
full card fails the request inside an op instead of ending the process (P34).

Held here: what each changes and where; the refusal rule, compiled and run from the
patch; that every op handler and CHECK_TRY_ERROR ask it, only within a graph; the
test switch; the speech reasons from every refusal; the candidate and its name."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
SPEECH = STAGED / "0035-llama-cpp-speech-refusal-says-why.patch"
REFUSAL = STAGED / "0036-llama-cpp-sycl-op-refusal-fails-the-request.patch"
SERIES = STAGED / "engine-guard.series"
BASE = STAGED / "engine-pair.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
NAME = "4b0da8dfcd96"


def _inner(outer: Path) -> str:
    text = outer.read_text(encoding="utf-8")
    body = text[text.index("\n---\n"):]
    return "\n".join(line[1:] for line in body.splitlines()
                     if line.startswith("+") and not line.startswith("+++ b/backend/")) + "\n"


def _sections(patch: str) -> dict[str, str]:
    parts = re.split(r"(?m)^(?=diff --git a/)", patch)
    return {re.match(r"diff --git a/(\S+)", p).group(1): p for p in parts if p.startswith("diff --git")}


def _added(section: str) -> str:
    return "\n".join(line[1:] for line in section.splitlines() if line.startswith("+") and not line.startswith("+++ "))


def test_each_is_a_mail_under_localai_s_policy_and_adds_one_engine_patch():
    for outer, inner, files in (
            (SPEECH, "0027-mtmd-gen-audio-says-why-an-input-is-invalid.patch",
             {"tools/mtmd/mtmd-helper.h", "tools/mtmd/mtmd-helper-gen.cpp", "tools/server/server-context.cpp"}),
            (REFUSAL, "0028-sycl-op-refusal-fails-the-graph.patch",
             {"ggml/src/ggml-sycl/common.hpp", "ggml/src/ggml-sycl/ggml-sycl.cpp", "ggml/src/ggml-sycl/mmq.cpp",
              "ggml/src/ggml-sycl/op-refusal.hpp", "tests/test-sycl-op-refusal.cpp"})):
        text = outer.read_text(encoding="utf-8")
        assert text.startswith("From 0000000000000000000000000000000000000000 ")
        message = text[:text.index("\n---\n")]
        assert re.search(r"^Assisted-by: ", message, re.M)
        assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
        assert re.findall(r"^diff --git a/(\S+)", text, re.M) == ["backend/cpp/llama-cpp/patches/" + inner]
        assert set(_sections(_inner(outer))) == files


def test_every_op_handler_and_the_call_wrapper_ask_the_rule_only_within_a_graph():
    files = _sections(_inner(REFUSAL))
    sycl = _added(files["ggml/src/ggml-sycl/ggml-sycl.cpp"]) + _added(files["ggml/src/ggml-sycl/mmq.cpp"])
    assert sycl.count("  ggml_sycl_op_exception(exc, __FILE__, __LINE__);") == 37
    common = _added(files["ggml/src/ggml-sycl/common.hpp"])
    assert "      ggml_sycl_refusal_from(e, __FILE__, __LINE__);                     \\" in common
    assert "struct ggml_sycl_out_of_device_memory {" in common          # the pool's refusal, shared by every op file
    added = _added(files["ggml/src/ggml-sycl/ggml-sycl.cpp"])
    # the flag: set by the computation that catches the refusal, and only there (a SYCL graph's recording is not)
    assert ("+        in_graph during;\n         ggml_backend_sycl_graph_compute_impl(sycl_ctx, cgraph);"
            in files["ggml/src/ggml-sycl/ggml-sycl.cpp"])
    assert "thread_local bool g_ggml_sycl_in_graph = false;" in added
    # outside the rule: the process ends as it always did, with the same words
    assert 'std::cerr << exc.what() << "Exception caught at file:" << file << ", line:" << line << std::endl;\n    std::exit(1);' in added
    # the test switch raises the node's own words, only once armed
    assert '"level_zero backend failed with error: 39 (UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY)"' in added
    assert 'g_ggml_sycl_op_refuse = ggml_sycl_get_env("GGML_SYCL_OP_REFUSE", 0);' in added
    assert "if (g_ggml_sycl_op_refuse > 0 && !g_ggml_sycl_graph_refuse_arm.empty()) {" in added


@pytest.mark.skipif(shutil.which("c++") is None or shutil.which("git") is None, reason="no C++ compiler or git here")
def test_the_rule_passes_from_the_patch(tmp_path):
    (tmp_path / "p.patch").write_text(_inner(REFUSAL), encoding="utf-8")
    tree = tmp_path / "tree"
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    done = subprocess.run(["git", "apply", "--include=ggml/src/ggml-sycl/op-refusal.hpp", "--include=tests/test-sycl-op-refusal.cpp",
                           str(tmp_path / "p.patch")], cwd=tree, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    built = subprocess.run(["c++", "-std=c++17", "-w", "-I", str(tree / "ggml" / "src" / "ggml-sycl"),
                            str(tree / "tests" / "test-sycl-op-refusal.cpp"), "-o", str(tmp_path / "t")],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr[-2000:]
    ran = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stdout
    lines = ran.stdout.splitlines()
    assert lines[-1] == "all passed" and sum(line.startswith("ok:") for line in lines) == 7
    assert "ok:   the node's error 39 within a graph fails the graph" in lines
    assert "ok:   the same outside a graph (a load) ends the process as before" in lines


def test_every_speech_refusal_gives_its_reason_and_the_server_answers_with_it():
    files = _sections(_inner(SPEECH))
    gen = _added(files["tools/mtmd/mtmd-helper-gen.cpp"])
    assert gen.count("return gen_audio_invalid(ctx, ") == 7
    assert 'input_error = "the voice description: " + err;' in gen
    assert "MTMD_API const char * mtmd_helper_gen_audio_input_error(const mtmd_helper_gen_audio * ctx);" in _added(files["tools/mtmd/mtmd-helper.h"])
    server = _added(files["tools/server/server-context.cpp"])
    assert 'const std::string why = slot.tts.ctx.input_error();' in server and '"invalid TTS input: " + why' in server


def test_the_candidate_is_the_medcpt_engine_plus_both_and_its_name_is_held():
    lines = lambda p: [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    assert lines(SERIES) == lines(BASE) + ["staged/" + SPEECH.name, "staged/" + REFUSAL.name]
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == NAME
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert f"**A candidate after the MedCPT one, built and gated once with it: `engine-guard.series`** (`{NAME}`;" in readme
    bugs = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
    assert "| **P33 (staged)** |" in bugs and "| **P34 (staged)** |" in bugs
