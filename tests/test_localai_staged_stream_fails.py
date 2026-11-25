"""Staged 0025 and 0026: a stream that fails after its first chunk FAILS, and
the switch that lets a card show it (docs/notes/localai-bugs.md B21; the
architecture session, 2026-11-25: "THE STREAM FIX IS A DEFECT OF TODAY'S
ENGINE … 'fix it after' is not an option for a truncated answer delivered as
whole").

LocalAI's llama.cpp backend tested only the FIRST result of a stream for an
error; a later one became an empty reply and the call returned OK. It was run
where it was written, on a CPU build with a stand-in that refuses a graph:
before, OK after 4 of 50 chunks; after, INTERNAL after 3 of 50, the next answer
whole. Held here: what the fix is and where; that the test switch is off
unless a model's env sets both its variables and decides nothing else; the
candidate that carries both and its engine's name; and the test tool's
verdicts, the one that says DEFECT included."""
import importlib.util
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
FIX = STAGED / "0025-llama-cpp-stream-fails-after-its-first-chunk.patch"
SWITCH = STAGED / "0026-llama-cpp-sycl-graph-refuse-switch.patch"
SERIES = STAGED / "engine-stream.series"
BEFORE = STAGED / "engine-lang.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
NAME = "792f050e318e"
BUGS = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")

spec = importlib.util.spec_from_file_location("stream_fails_test", ROOT / "tools" / "measure" / "stream_fails_test.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def _added(text: str) -> str:
    return "\n".join(line[1:] for line in text[text.index("\n---\n"):].splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_both_are_mails_under_localai_s_policy():
    for patch, files in ((FIX, ["backend/cpp/llama-cpp/grpc-server.cpp"]),
                         (SWITCH, ["backend/cpp/llama-cpp/patches/0022-sycl-graph-refuse-switch.patch"])):
        text = patch.read_text(encoding="utf-8")
        assert text.startswith("From 0000000000000000000000000000000000000000 ")
        message = text[:text.index("\n---\n")]
        assert re.search(r"^Assisted-by: ", message, re.M)
        assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
        assert re.findall(r"^diff --git a/(\S+)", text, re.M) == files


def test_a_later_error_ends_the_stream_as_the_first_one_does():
    text = FIX.read_text(encoding="utf-8")
    added = _added(text)
    # the test is on every result after the first, before it is turned into a reply
    assert re.search(r"^ \s+if \(result == nullptr\) \{\n \s+break;\n \s+\}\n\+\s+if \(result->is_error\(\)\) \{", text, re.M)
    assert 'return grpc::Status(grpc::StatusCode::INTERNAL, error_json.value("message", "Error occurred"));' in added
    assert "writer->Write" not in added, "the error is not written as a reply: it is the call's status"
    # nothing else of the backend moves in it: no park, no new status
    assert "RESOURCE_EXHAUSTED" not in added and "park" not in added.lower()
    assert len([line for line in text.splitlines() if line.startswith("+") and not line.startswith("+++")]) == 9


def test_the_switch_needs_both_variables_and_refuses_before_anything_is_submitted():
    inner = _added(SWITCH.read_text(encoding="utf-8"))
    added = "\n".join(line[1:] for line in inner.splitlines() if line.startswith("+") and not line.startswith("+++"))
    assert re.findall(r"^diff --git a/(\S+)", inner, re.M) == ["ggml/src/ggml-sycl/ggml-sycl.cpp"]
    assert "std::atomic<int> g_ggml_sycl_graph_refuse{0};" in added
    assert 'g_ggml_sycl_graph_refuse = ggml_sycl_get_env("GGML_SYCL_GRAPH_REFUSE", 0);' in added
    assert 'getenv("GGML_SYCL_GRAPH_REFUSE_ARM")' in added
    hook = added[added.index("if (g_ggml_sycl_graph_refuse > 0 && !g_ggml_sycl_graph_refuse_arm.empty()) {"):]
    hook = hook[:hook.index("return GGML_STATUS_ALLOC_FAILED;")]
    assert "fopen(g_ggml_sycl_graph_refuse_arm.c_str()" in hook and "g_ggml_sycl_graph_refuse.fetch_sub(1) > 0" in hook
    # it sits before the graph is computed: the device is as it was
    assert re.search(r"^\+\s+return GGML_STATUS_ALLOC_FAILED;\n(?:\+.*\n){0,4}? \s+try \{", inner, re.M), \
        "the added lines end right above the function's own try"
    # set without its file's name it refuses nothing, and says so
    assert "GGML_SYCL_GRAPH_REFUSE is set without GGML_SYCL_GRAPH_REFUSE_ARM: nothing is refused" in added


def test_no_model_file_carries_the_test_switch():
    files = [f for d in sorted((ROOT / "deploy").glob("*/models")) + [ROOT / "deploy" / "vega" / "stage" / "models"]
             for f in sorted(d.glob("*.yaml"))]
    assert len(files) > 40
    for f in files:
        assert "GGML_SYCL_GRAPH_REFUSE" not in f.read_text(encoding="utf-8"), f"{f.relative_to(ROOT)}: a test switch in a model's file"


def test_the_candidate_is_the_language_tag_engine_plus_the_two_and_its_name_is_held():
    assert _lines(SERIES) == _lines(BEFORE) + ["staged/" + FIX.name, "staged/" + SWITCH.name]
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == NAME
    head = SERIES.read_text(encoding="utf-8")
    assert "ONE BUILD, ONE GATING" in head and "stream_fails_test.py --addr ADDR --arm" in head


def test_the_tool_says_defect_for_a_stream_that_ends_ok_and_short():
    whole, after = ("OK", 50, ""), ("OK", 50, "")
    passed, lines = tool.verdict(whole, ("OK", 4, ""), after)
    assert not passed and lines[-1].startswith("DEFECT: the stream ended OK after 4 of 50 chunks")
    passed, lines = tool.verdict(whole, ("INTERNAL", 3, "Compute error."), after)
    assert passed and "ended as a failure (INTERNAL) after 3 of 50 chunks" in lines[-1]
    passed, lines = tool.verdict(whole, ("RESOURCE_EXHAUSTED", 3, "no_room_on_device: Compute error"), after)
    assert passed, "the park engine's status for the same failure is a failure too"


def test_the_tool_does_not_pass_a_run_that_showed_nothing():
    passed, lines = tool.verdict(("OK", 50, ""), ("OK", 50, ""), ("OK", 50, ""))
    assert not passed and lines[-1].startswith("NOT RUN: no graph was refused")
    passed, lines = tool.verdict(("INTERNAL", 0, "x"), ("INTERNAL", 0, "x"), ("OK", 1, ""))
    assert not passed and lines[-1].startswith("NOT RUN: the model does not stream")
    passed, lines = tool.verdict(("OK", 50, ""), ("INTERNAL", 3, "Compute error."), ("OK", 12, ""))
    assert not passed and lines[-1].startswith("FAIL: the stream after the failure is not the whole answer")


def test_the_bug_log_says_where_it_is_fixed_and_what_was_measured():
    row = next(line for line in BUGS.splitlines() if line.startswith("| B21 | defect |"))
    for said in ("staged 0025", "engine-stream.series", NAME, "OK after 4 of 50 chunks", "INTERNAL after 3 of 50"):
        assert said in row, said
