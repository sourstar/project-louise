"""The parking prototype's engine patch, `staged/0015-llama-cpp-park-weights.patch`
(docs/notes/parking-prototype.md; `models-beyond-the-card.md` §6, step 1).

A model's weights leave the device and come back at the SAME addresses. It
cannot run where it is written (no card), so what is held here is its shape:
that nothing changes unless a model's env says so, that the address-stable
contract and the refusal are in it, that its engine is the fixes' candidate
and this one patch, and that the note says what was tested without a card
and what the node measures."""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
STAGED = PATCHES / "staged"
PATCH = STAGED / "0015-llama-cpp-park-weights.patch"
SERIES = STAGED / "parking-prototype.series"
NOTE = (ROOT / "docs" / "notes" / "parking-prototype.md").read_text(encoding="utf-8")
STANDIN = ROOT / "tools" / "measure" / "park_cpu_standin.patch"
CPU_TEST = ROOT / "tools" / "measure" / "park_cpu_test.py"
INNER = "backend/cpp/llama-cpp/patches/0015-park-weights.patch"
SWITCHES = ("GGML_SYCL_PARKABLE", "GGML_SYCL_PARK_VERIFY", "LLAMA_SERVER_PARK_WEIGHTS")


def _added(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _inner() -> str:
    """The llama.cpp patch the LocalAI patch adds as a file."""
    outer = PATCH.read_text(encoding="utf-8")
    return "\n".join(_added(outer[outer.index("\n---\n"):]))


def _code() -> str:
    return "\n".join(_added(_inner()))


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_it_is_a_mail_under_localai_s_policy_and_adds_one_engine_patch():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == [INNER]
    assert "STAGED, a prototype" in message and "has not run where it was" in message


def test_nothing_changes_unless_a_model_s_env_says_so():
    code = _code()
    assert "int g_ggml_sycl_parkable = 0;" in code and "int g_ggml_sycl_park_verify = 0;" in code
    assert 'ggml_sycl_get_env("GGML_SYCL_PARKABLE", 0)' in code and 'ggml_sycl_get_env("GGML_SYCL_PARK_VERIFY", 0)' in code
    # the only place a buffer is allocated differently is behind the switch AND the caller's word that it is weights
    assert ("if (g_ggml_sycl_parkable && ggml_backend_get_alloc_usage_hint() == GGML_BACKEND_BUFFER_USAGE_WEIGHTS)"
            in code)
    assert 'getenv("LLAMA_SERVER_PARK_WEIGHTS")' in code and "bool park_weights = false;" in code
    assert "LLAMA_SERVER_PARK_WEIGHTS needs context on demand" in code, "a model is parked only with no context"
    assert "the weights of this model cannot be parked on this backend, ignored" in code
    message = PATCH.read_text(encoding="utf-8")
    for switch in SWITCHES:
        assert switch in message and f"`{switch}" in NOTE, switch
    assert "No load option and no RPC" in NOTE, "so the wrapper needs no rebuild for this step"
    assert not re.search(r"^\+.*strcmp\(optname", _inner(), re.M), "no new load option in the backend"


def test_the_addresses_stay_and_a_full_device_refuses():
    code = _code()
    # the hook is optional and address-stable by contract
    assert re.search(r"bool\s+\(\*park\)\s+\(ggml_backend_buffer_t buffer\);", code)
    assert re.search(r"bool\s+\(\*unpark\)\s+\(ggml_backend_buffer_t buffer\);", code)
    assert "commits memory at\n        // the same addresses" in code or "commits memory at" in code
    # SYCL: one reserved range, memory released and committed again INTO it
    assert code.count("sycl::ext::oneapi::experimental::reserve_virtual_mem(size, ctx)") == 1, "reserved once, at creation"
    assert "phys.map(addr + off, n, sycl::ext::oneapi::experimental::address_access_mode::read_write)" in code
    assert "sycl::ext::oneapi::experimental::unmap(m.ptr, m.phys.size(), ctx)" in code
    # a device with no room: the commit fails softly, the buffer stays parked, the request is refused
    assert re.search(r"if \(!mem->commit\(\)\) \{\s+GGML_LOG_WARN\(\"%s: no room on the device", code)
    assert "return CONTEXT_NOT_CREATED;" in code and "the weights cannot come back to the device" in code
    # a verified unpark that reads back other bytes parks the buffer again and fails
    assert "is NOT what was parked; it is parked again" in code
    # nothing reads or writes a parked buffer, and no context is made on a parked model
    assert code.count("ggml_sycl_buffer_on_device(ctx);") == 5
    assert "the model is parked, llama_model_unpark() comes first" in code
    # all parts or none
    assert "could not be %s again after part %zu failed" in code and "could not be %s again after buffer %zu failed" in code


def test_its_engine_is_the_fixes_candidate_and_this_patch_never_the_live_series():
    assert _lines(SERIES) == _lines(STAGED / "engine-fixes.series") + ["staged/" + PATCH.name]
    build = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
    names = [subprocess.run(["sh", str(build), "sycl_f16", "/nonexistent"], capture_output=True, text=True, cwd=ROOT,
                            env=dict(os.environ, SERIES_FILE=str(s), NAME_ONLY="1")).stdout.strip()
             for s in (SERIES, STAGED / "engine-fixes.series", PATCHES / "series")]
    assert len(set(names)) == 3 and all(len(n) == 12 for n in names), names
    assert PATCH.name not in (PATCHES / "series").read_text(encoding="utf-8")
    head = SERIES.read_text(encoding="utf-8")
    assert "INSTALLED BESIDE THE LIVE ENGINE AND NEVER SWAPPED" in head
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert PATCH.name in readme and "NEVER RUN ON A CARD" in readme


def test_the_note_says_what_ran_without_a_card_and_what_the_node_measures():
    assert "nothing\nof it has run on the card yet" in NOTE
    tested = NOTE[NOTE.index("## 3. "):NOTE.index("## 4. ")]
    assert "16 of 16" in tested and "21 of 21" in tested and "Intel's compiler 2025.3.3" in tested
    assert "What that does NOT show: anything about the card." in tested
    node = NOTE[NOTE.index("## 4. "):NOTE.index("## 5. ")]
    for run in ("| R0, the control |", "| R1, parkable and not parking |", "| R2, parking, verified |",
                "| R3, parking, timed |", "| the floor |", "| a refusal"):
        assert run in node, run
    assert "parking-prototype.series" in node and "keep_context_ms:0" in node and "verbosity: 4" in node
    assert "The first thing the run says" in node, "a load on mapped memory that fails stops the prototype"
    # the stand-in and its test are in the repository and name each other
    standin = STANDIN.read_text(encoding="utf-8")
    assert "DEV ONLY, NOT IN THE PATCH" in standin and "memset(base, 0xA5, buffer->size);" in standin
    for env in ("GGML_CPU_PARK_TEST", "GGML_CPU_PARK_TEST_REFUSE", "GGML_CPU_PARK_TEST_LOSE"):
        assert env in standin and env in CPU_TEST.read_text(encoding="utf-8"), env
    assert STANDIN.name in CPU_TEST.read_text(encoding="utf-8") and STANDIN.name in NOTE and CPU_TEST.name in NOTE
    # the stand-in is not in the engine: the patch does not carry it
    assert "GGML_CPU_PARK_TEST" not in PATCH.read_text(encoding="utf-8")
