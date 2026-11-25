"""Staged 0028: a context canary the device REFUSES FOR MEMORY is not a canary
that failed (docs/notes/localai-bugs.md B23; parking-step-2.md §6b).

The node, 2026-11-25 09:40:55Z, on the live engine: one context of an embedding
model discarded as "context canary failed" in the second the card filled at a
worker's start. Louise takes the engine at its word (invariant 12): the run's
statements set aside, the collection gated again, forty minutes. From the code,
and measured on a CPU build with a stand-in device, a canary whose graph is
refused for memory was answered exactly so - and at the load left the model
served unchecked. The fix existed inside the parking patch only; it is cut out
for the next engine.

Held here: what the patch changes and what it leaves alone; that Louise's
client reads its answer as a deferral, by the very words it tests for; the
candidate's series and its engine's name; that the parking patch carries the
SAME fix, so the two cannot drift, and that the park series does not list both;
and the measuring tool's verdicts."""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
FIX = STAGED / "0028-llama-cpp-context-canary-refused-for-memory.patch"
PARK = STAGED / "0023-llama-cpp-park-when-told.patch"
SERIES = STAGED / "engine-canary.series"
BEFORE = STAGED / "engine-stream.series"
PARK_SERIES = STAGED / "engine-park.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
NAME = "619655e5833b"
INNER = "backend/cpp/llama-cpp/patches/0023-context-canary-refused-for-memory.patch"
TOOL = (ROOT / "tools" / "measure" / "canary_refused_test.py").read_text(encoding="utf-8")
BUGS = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
NOTE = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")
CLIENT = (ROOT / "app" / "embeddings.py").read_text(encoding="utf-8")


def _outer_added(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return "\n".join(line[1:] for line in text[text.index("\n---\n"):].splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


def _inner_added(outer_added: str) -> str:
    """the lines llama.cpp's own patch ADDS (the outer patch adds the inner patch as a file)"""
    return "\n".join(line[1:] for line in outer_added.splitlines() if line.startswith("+") and not line.startswith("+++"))


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_it_is_a_mail_under_localai_s_policy_that_adds_one_llama_cpp_patch():
    text = FIX.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == [INNER], "no line of LocalAI's own backend moves"
    inner = _outer_added(FIX)
    assert re.findall(r"^diff --git a/(\S+)", inner, re.M) == [
        "common/common.cpp", "common/common.h", "include/llama.h", "src/llama-context.cpp",
        "tools/server/README.md", "tools/server/server-context.cpp"]
    # the event is said as what it is: seen, not proven to be this
    assert "that event is not proven" in message and "what a full device does is measured" in message


def test_a_refused_canary_is_told_from_every_other_failure():
    added = _inner_added(_outer_added(FIX))
    # the library says a graph it had no memory for, where it said -2 like every other failure
    assert "#define LLAMA_PROCESS_NO_MEMORY (-4)" in added
    assert added.count("case GGML_STATUS_ALLOC_FAILED: return LLAMA_PROCESS_NO_MEMORY;") == 2, "encode and decode"
    # the canary says why it was not computed
    assert "COMMON_CANARY_NO_MEMORY," in added
    assert added.count("no_memory = ret == LLAMA_PROCESS_NO_MEMORY;") == 2
    assert "context canary: the device had no memory for its graph on this context" in added
    # a canary that could not be computed for any OTHER reason is still a mismatch: the line stays, below the new test
    inner = _outer_added(FIX)
    check = inner[inner.index("common_canary_status common_init_result::canary_check"):]
    assert check.index("return COMMON_CANARY_NO_MEMORY;") < check.index(
        ' COM_WRN("%s", "context canary: its output could not be computed on this context\\n");')


def test_at_a_request_it_is_a_context_that_cannot_be_created_and_at_the_load_the_load_fails():
    inner = _outer_added(FIX)
    added = _inner_added(inner)
    request = added[added.index("if (canary == COMMON_CANARY_NO_MEMORY) {"):]
    request = request[:request.index("return CONTEXT_NOT_CREATED;")]
    assert "llama_init->free_context();" in request, "the context is discarded"
    assert "the device had no memory for the canary of the context created, it is discarded" in request
    assert "n_ctx_canary_fail" not in request, "counted nowhere as a canary that failed"
    assert "weights_park();" in request, "a request that is refused leaves the device as it found it"
    # it returns BEFORE the code of a canary that failed (its test switch, then its count and words), which the
    # patch does not touch: the first of those lines is the hunk's own context, right below the new branch
    server = inner[inner.index("diff --git a/tools/server/server-context.cpp"):]
    assert re.search(r"^\+\s+return CONTEXT_NOT_CREATED;\n \s+\}\n \s+if \(ctx_canary_fail > 0\) \{", server, re.M)
    assert "n_ctx_canary_fail" not in added and "does not reproduce the canary" not in added
    load = added[added.index("case COMMON_CANARY_NO_MEMORY:"):]
    assert "the device had no memory for the context canary: the load fails rather than serve" in load
    assert re.search(r"contexts that are not checked\\n\"\);\n\s+return false;", load)
    # nothing else of the engine moves: no new error type, no park, no answer of a request's own failure
    for absent in ("ERROR_TYPE_NO_ROOM_ON_DEVICE", "no_room_on_device", "llama_model_park", "send_error("):
        assert absent not in added, absent


def test_louise_reads_its_answer_as_a_deferral_by_the_words_it_tests_for():
    """Through LocalAI every engine error is a 5xx with the engine's message: app/embeddings.py tells a
    canary failure by its words, and every other 5xx is a busy endpoint - the batch deferred, nothing gated."""
    assert 'CANARY_FAILED = "context canary failed"' in CLIENT
    assert "if status >= 500 and CANARY_FAILED in body:" in CLIENT
    assert "if status in BUSY_STATUS or status >= 500:\n        return EndpointBusy(" in CLIENT
    inner = _outer_added(FIX)
    # the words the refused canary is answered with are the engine's own for a context it cannot create - a context line
    # of the patch, not one it adds - and they are not the canary's
    assert "the context cannot be created now" in FIX.read_text(encoding="utf-8")
    assert "context canary failed" not in _inner_added(inner).replace("`context canary failed`", "")
    assert f'CANARY_FAILED = "context canary failed"' in TOOL, "the tool tests for the same words the client does"


def test_the_candidate_is_the_stream_engine_plus_it_and_its_name_is_held():
    assert _lines(SERIES) == _lines(BEFORE) + ["staged/" + FIX.name]
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == NAME
    head = SERIES.read_text(encoding="utf-8")
    for said in ("ONE BUILD, ONE GATING", "it stands in 792f050e318e's place", "GGML_SYCL_GRAPH_REFUSE=2",
                 "engine-park.series does NOT list 0028 and must not"):
        assert said in head, said


def test_the_parking_patch_carries_the_same_fix_and_its_series_does_not_list_both():
    """0028 was cut out of 0023, which keeps its own form of it on the same lines: applied one after the
    other they would collide, and a change to one that the other does not get would be two engines that
    disagree about what a refused canary is."""
    assert "staged/" + FIX.name not in _lines(PARK_SERIES)
    assert _lines(PARK_SERIES)[:-1] == _lines(BEFORE), "the park series is the stream series plus 0023"
    cut, park = _inner_added(_outer_added(FIX)), _inner_added(_outer_added(PARK))
    for same in ("#define LLAMA_PROCESS_NO_MEMORY (-4)",
                 "case GGML_STATUS_ALLOC_FAILED: return LLAMA_PROCESS_NO_MEMORY;",
                 "COMMON_CANARY_NO_MEMORY,",
                 "no_memory = ret == LLAMA_PROCESS_NO_MEMORY;",
                 'COM_WRN("%s", "context canary: the device had no memory for its graph, it is not recorded\\n");',
                 'COM_WRN("%s", "context canary: the device had no memory for its graph on this context\\n");',
                 "if (canary == COMMON_CANARY_NO_MEMORY) {",
                 'SRV_WRN("%s", "the device had no memory for the canary of the context created, it is discarded\\n");',
                 'SRV_ERR("%s", "the device had no memory for the context canary: the load fails rather than serve "'):
        assert same in cut and same in park, same
    # where they differ is the type of the answer, which is the parking patch's alone
    assert "ERROR_TYPE_NO_ROOM_ON_DEVICE" in park and "ERROR_TYPE_NO_ROOM_ON_DEVICE" not in cut


def test_the_tool_says_defect_for_the_old_answers_and_tests_a_real_mismatch_too():
    assert "DEFECT: \" + what" in TOOL
    assert "a canary refused for memory was answered as a canary that FAILED" in TOOL
    assert "AT THE LOAD left the model served UNCHECKED" in TOOL
    # the refusal is of BOTH graphs a new context computes, as a full device refuses them
    assert '"GGML_CPU_REFUSE_GRAPHS": "2"' in TOOL and "the warmup's empty run, whose failure\n# llama.cpp ignores" in TOOL
    # and the check itself is still a check: a canary that does not reproduce is still answered as one
    assert '{"LLAMA_SERVER_CTX_CANARY_FAIL": "1"}' in TOOL
    assert "a canary that does NOT reproduce is still answered" in TOOL
    assert "DEFECT, twice; with staged 0028, 12 of 12" in TOOL


def test_no_model_file_carries_a_test_switch():
    files = [f for d in sorted((ROOT / "deploy").glob("*/models")) + [ROOT / "deploy" / "vega" / "stage" / "models"]
             for f in sorted(d.glob("*.yaml"))]
    assert len(files) > 40
    for f in files:
        text = f.read_text(encoding="utf-8")
        for switch in ("GGML_SYCL_GRAPH_REFUSE", "LLAMA_SERVER_CTX_CANARY_FAIL", "GGML_CPU_REFUSE"):
            assert switch not in text, f"{f.relative_to(ROOT)}: {switch}, a test switch in a model's file"


def test_the_bug_log_and_the_note_say_what_was_seen_what_was_measured_and_what_was_not():
    row = next(line for line in BUGS.splitlines() if line.startswith("| B23 | defect |"))
    for said in ("09:40:55Z", "1 of 493 creations", "NOT proven to be this", "staged 0028", "engine-canary.series", NAME):
        assert said in row, said
    patch = next(line for line in BUGS.splitlines() if line.startswith("| **P25 (staged)**"))
    for said in ("LLAMA_PROCESS_NO_MEMORY", "DEFECT twice before, 12 of 12 after", "NOT measured: a card"):
        assert said in patch, said
    section = NOTE[NOTE.index("## 6b. "):NOTE.index("## 7. ")]
    for said in ("THE WORDS THAT TELL THE TWO APART", "failed with error -2",
                 "context canary: its output could not be computed on this context",
                 "values differ", "| | pros | cons |", NAME, "792f050e318e", "NOT measured: a card",
                 "the warmup", "If the operator says nothing"):
        assert said in section, said
