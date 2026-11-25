"""Parking, step 2, the engine part — SENT FOR REVIEW, not staged
(docs/notes/parking-step-2.md §6; deploy/localai/patches/review/).

The backend's `Free` parks a model that can park, a `LoadModel` for the model
already held brings it back, and every way a request finds the device full
is one answer. Its behaviour was run where it was written, on CPU builds with
a stand-in device (29/29 over gRPC, 43/43 and 17/17 over llama.cpp's server).
Held here: the interface a caller builds against — the bytes of the message
the wrapper keys on, which code each outcome is, compiled and run from the
patch itself where a compiler is at hand — what the patch changes and what it
leaves, and that nothing builds an engine from a patch still under review."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
REVIEW = PATCHES / "review"
PATCH = REVIEW / "0023-llama-cpp-park-when-told.patch"
BACKEND = "backend/cpp/llama-cpp/"
INNER = BACKEND + "patches/0022-server-park-when-told.patch"
NOTE = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")


def _text() -> str:
    return PATCH.read_text(encoding="utf-8")


def _sections() -> dict[str, str]:
    """each file's part of the outer patch, by its path"""
    text = _text()
    parts = re.split(r"^diff --git a/(\S+) b/\S+\n", text[text.index("\n---\n"):], flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def _added(diff: str) -> str:
    return "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))


def _removed(diff: str) -> str:
    return "\n".join(line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))


def _inner() -> str:
    return _added(_sections()[INNER])


def test_it_is_a_mail_under_localai_s_policy_and_carries_one_engine_patch():
    text = _text()
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert set(_sections()) == {BACKEND + name for name in (
        "CMakeLists.txt", "grpc-server.cpp", "park_reply.h", "park_reply_test.cpp", "prepare.sh",
        "patches/0022-server-park-when-told.patch")}
    assert set(re.findall(r"^diff --git a/(\S+)", _inner(), re.M)) == {
        "common/common.cpp", "common/common.h", "ggml/src/ggml-sycl/ggml-sycl.cpp", "include/llama.h",
        "src/llama-model.cpp", "src/llama-model.h", "tools/server/README.md", "tools/server/server-common.cpp",
        "tools/server/server-common.h", "tools/server/server-context.cpp", "tools/server/server-context.h",
        "tools/server/server-task.cpp", "tools/server/server-task.h", "tools/server/server.cpp",
        "tools/server/tests/unit/test_context_on_demand.py", "tools/server/tests/unit/test_park_weights.py"}
    # the dev stand-ins never travel: no CPU buffer that "parks", no CPU graph that refuses
    assert "GGML_CPU_PARK_TEST" not in text and "GGML_CPU_REFUSE" not in text and "DEV ONLY" not in text


def test_it_is_under_review_in_no_series_and_not_staged():
    assert not (PATCHES / "staged" / PATCH.name).exists(), "staged only after the review (parking-step-2.md §4)"
    for series in (PATCHES / "series", *(PATCHES / "staged").glob("*.series")):
        assert "park-when-told" not in series.read_text(encoding="utf-8"), series.name
    readme = (REVIEW / "README.md").read_text(encoding="utf-8")
    assert PATCH.name in readme and "in no series" in readme.lower()
    assert "## 6. The engine part, as built" in NOTE and "FOR REVIEW" in NOTE


def test_no_model_file_can_park_before_the_server_part_is_live():
    """An UNPATCHED LocalAI calls Free before it stops a process, so from this engine on every stop of a model that
    CAN park first copies its weights to the host (0.4 s and 6 GiB for MADLAD) - acceptable only while no model's file
    says it can (the architecture session, 2026-11-25). And the measuring switch is never a live model's."""
    files = [f for d in sorted((ROOT / "deploy").glob("*/models")) + [ROOT / "deploy" / "vega" / "stage" / "models"]
             for f in sorted(d.glob("*.yaml"))]
    assert len(files) > 40, "the staged model files were not found"
    for f in files:
        text = f.read_text(encoding="utf-8")
        for switch in ("GGML_SYCL_PARKABLE", "LLAMA_SERVER_PARK_WEIGHTS", "GGML_SYCL_PARK_REFUSE", "GGML_SYCL_PARK_VERIFY"):
            assert switch not in text, f"{f.relative_to(ROOT)} sets {switch}: no live model parks before the server part"
    assert "no live model sets `GGML_SYCL_PARKABLE=1` before the server part is live" in NOTE
    assert "never set in a live model's file" in NOTE


def test_the_message_the_wrapper_keys_on_is_pinned_byte_for_byte():
    header = _added(_sections()[BACKEND + "park_reply.h"])
    assert 'inline constexpr const char * LOAD_ALREADY_LOADED = "Model already loaded";' in header
    assert 'inline constexpr const char * FREE_PARKED = "parked";' in header
    assert 'inline constexpr const char * ERROR_TYPE_NO_ROOM = "no_room_on_device";' in header
    # the backend answers it by the constant, on both paths (parked or not), and never by a second literal
    server = _sections()[BACKEND + "grpc-server.cpp"]
    assert 'result->set_message("Model already loaded");' in _removed(server)
    assert _added(server).count("result->set_message(llama_grpc::LOAD_ALREADY_LOADED);") == 1
    assert '"Model already loaded"' not in _added(server)
    # and the patch's own test compares the bytes, not a string that could be edited with it
    test = _added(_sections()[BACKEND + "park_reply_test.cpp"])
    assert "{ 'M','o','d','e','l',' ','a','l','r','e','a','d','y',' ','l','o','a','d','e','d' }" in test


@pytest.mark.skipif(shutil.which("g++") is None, reason="no C++ compiler here")
def test_the_patch_s_own_header_compiles_and_its_test_passes(tmp_path):
    sections = _sections()
    for name in ("park_reply.h", "park_reply_test.cpp"):
        (tmp_path / name).write_text(_added(sections[BACKEND + name]) + "\n", encoding="utf-8")
    built = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "park_reply_test.cpp", "-o", "t"],
                           cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run(["./t"], cwd=tmp_path, capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    assert "all passed" in ran.stderr and "FAIL" not in ran.stderr


def test_which_code_each_outcome_is():
    header = _added(_sections()[BACKEND + "park_reply.h"])
    request = header[header.index("inline ParkCode code_of_request_error("):header.index("// Free: the answer of the park task")]
    assert "return type == ERROR_TYPE_NO_ROOM ? ParkCode::ResourceExhausted : ParkCode::Internal;" in request
    free = header[header.index("inline ParkCode code_of_free_error("):header.index("// A repeated LoadModel")]
    assert re.search(r'"not_supported_error"\) \{\s+return ParkCode::Unimplemented;', free)
    assert re.search(r'"unavailable_error"\) \{\s+return ParkCode::FailedPrecondition;', free), \
        "busy is for now: never Unimplemented, which tells the caller to stop the process"
    assert "Unavailable" not in re.sub(r"//.*", "", header), "LocalAI reads UNAVAILABLE as a backend it cannot reach"
    server = _added(_sections()[BACKEND + "grpc-server.cpp"])
    for code in ("UNIMPLEMENTED", "FAILED_PRECONDITION", "RESOURCE_EXHAUSTED"):
        assert f"return grpc::StatusCode::{code};" in server


def test_every_request_rpc_answers_the_refusal_by_its_type_and_nothing_else_moves():
    server = _sections()[BACKEND + "grpc-server.cpp"]
    added, removed = _added(server), _removed(server)
    # PredictStream, Predict, Embedding, Rerank, TTS, TTSStream, Score, the slot cache, a decision's default
    assert added.count("grpc_code_of_request_error(") == 9 + 1          # and its own definition
    assert removed.count("grpc::StatusCode::INTERNAL") == 8, "each was INTERNAL; the decision's switch had a bare default"
    assert "default: code = grpc_code_of_request_error(results.error->to_json()); break;" in added
    # the canary's trailer on an Embedding is still said: a real canary failure is not a room refusal
    assert "CONTEXT_CANARY_TRAILER" not in removed


def test_free_status_and_the_return_exist_only_where_the_engine_can_park():
    server = _added(_sections()[BACKEND + "grpc-server.cpp"])
    guarded = server[server.index("#if LOCALAI_HAS_PARK\n    // Post the park task"):server.index("#endif // LOCALAI_HAS_PARK")]
    for said in ("server_task_result_ptr runPark(bool park)", "grpc::Status Free(", "grpc::Status Status("):
        assert said in guarded, said
    assert guarded.count("ctx_server.get_meta().can_park") == 2
    assert guarded.count('grpc::Status(grpc::StatusCode::UNIMPLEMENTED,') == 2, "a model that cannot park: as before"
    # the return: only for a model that can park, the load lock released first, the message after
    back = server[server.index("load_lock.unlock();"):server.index("result->set_message(llama_grpc::LOAD_ALREADY_LOADED);")]
    assert "#if LOCALAI_HAS_PARK" in back and "runPark(false)" in back and "code_of_return_error" in back
    prepare = _added(_sections()[BACKEND + "prepare.sh"])
    assert 'grep -q "SERVER_TASK_TYPE_PARK" llama.cpp/tools/server/server-task.h' in prepare
    assert "#define LOCALAI_HAS_PARK ${HAS_PARK}" in prepare
    assert "cp -r park_reply.h llama.cpp/tools/grpc-server/" in prepare


def test_the_engine_parks_when_told_and_never_by_itself():
    inner = _inner()
    added = _added(inner)
    assert "SERVER_TASK_TYPE_PARK, // the weights leave the device, or come back" in added
    # one added line among the tasks that need no context (the `return false` after it is the function's own)
    assert re.search(r"^\+\s+case SERVER_TASK_TYPE_PARK:\n \s+return false;", inner, re.M), \
        "a park needs no context: none is created for it"
    park = added[added.index("void process_park(const server_task & task) {"):added.index("CONTEXT_NOT_UNPARKED, // the parked weights could not come back")]
    assert park.index("if (!can_park) {") < park.index("slot.is_processing()") < park.index("context_release();") \
        < park.index("weights_park()")
    assert '"busy: a request is running, the model stays on the device", ERROR_TYPE_UNAVAILABLE' in park
    assert '"the weights cannot come back to the device now", ERROR_TYPE_NO_ROOM_ON_DEVICE' in park
    # between two requests nothing parks: the only automatic park is the measuring switch's, as before
    assert "can_park = params_base.context_on_demand && llama_model_can_park(model_tgt);" in added
    assert "if (park_weights) {\n            weights_park();" not in added, "context_release() is not given a second trigger"
    assert 'ctx_http.post("/park",' in added and 'ctx_http.post("/unpark",' in added


def test_one_type_for_no_room_and_the_canary_no_longer_takes_it_for_a_wrong_context():
    added = _added(_inner())
    assert re.search(r'case ERROR_TYPE_NO_ROOM_ON_DEVICE:\n\s+type_str = "no_room_on_device";\n\s+code = 503;', added)
    assert "return ret == -2 ? ERROR_TYPE_NO_ROOM_ON_DEVICE : ERROR_TYPE_SERVER;" in added
    assert "static enum error_type error_type_of_process(int32_t ret) {" in added
    assert added.count("error_type_of_process(ret)") == 2, "the decode and the encode"
    assert "COMMON_CANARY_NO_MEMORY," in added
    refused = added[added.index("if (canary == COMMON_CANARY_NO_MEMORY) {"):]
    refused = refused[:refused.index("return CONTEXT_NOT_CREATED;")]
    assert "llama_init->free_context();" in refused and "n_ctx_canary_fail" not in refused, \
        "a context that cannot be created, counted nowhere as a canary that failed"
    load = added[added.index("case COMMON_CANARY_NO_MEMORY:\n"):]
    assert "return false;" in load[:load.index("}")], "at the load it fails the load: never served unchecked"


def test_the_refusal_switch_touches_only_a_return_and_at_its_last_piece():
    added = _added(_inner())
    assert "std::atomic<int> g_ggml_sycl_park_refuse{0};" in added
    assert 'g_ggml_sycl_park_refuse = ggml_sycl_get_env("GGML_SYCL_PARK_REFUSE", 0);' in added
    assert re.search(r"if \(host != nullptr && off \+ n >= size && g_ggml_sycl_park_refuse > 0 &&\n"
                     r"\s+g_ggml_sycl_park_refuse\.fetch_sub\(1\) > 0\) \{\n"
                     r"\s+throw sycl::exception\(sycl::make_error_code\(sycl::errc::memory_allocation\),", added), \
        "host != nullptr: a parked buffer coming back, never a load; the last piece: what was committed is released"


def test_the_note_says_what_differs_from_the_design_and_what_was_not_measured():
    section = NOTE[NOTE.index("## 6. The engine part, as built"):]
    for said in ("FAILED_PRECONDITION", "Where it differs from §3.1", "COMMON_CANARY_NO_MEMORY", "29 of 29", "43 of 43",
                 "17 of 17", "NOT measured: anything on a card", "park_told.py cycles", "What an unpatched LocalAI does"):
        assert said in section, said
