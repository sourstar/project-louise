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
SERIES = STAGED / "engine-next.series"
NOTE = (ROOT / "docs" / "notes" / "parking-prototype.md").read_text(encoding="utf-8")
STANDIN = ROOT / "tools" / "measure" / "park_cpu_standin.patch"
CPU_TEST = ROOT / "tools" / "measure" / "park_cpu_test.py"
INNER = "backend/cpp/llama-cpp/patches/0015-park-weights.patch"
REFUSE = STAGED / "0016-llama-cpp-sycl-pool-refuse-switch.patch"
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


def test_its_engine_is_the_next_live_candidate_where_it_is_off():
    """One build for everything due (2026-11-24, agreed with the architecture session): a build costs every run
    its gate, so parking and its test switch ride in the next live engine, OFF, and are measured on copies."""
    lines = _lines(SERIES)
    assert lines[:len(_lines(PATCHES / "series"))] == _lines(PATCHES / "series"), "the live series first, unchanged"
    staged = [line for line in lines if line.startswith("staged/")]
    assert staged == sorted(staged) and "staged/" + PATCH.name in staged and "staged/" + REFUSE.name in staged
    for line in staged:
        assert (PATCHES / line).is_file(), line
    build = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
    names = [subprocess.run(["sh", str(build), "sycl_f16", "/nonexistent"], capture_output=True, text=True, cwd=ROOT,
                            env=dict(os.environ, SERIES_FILE=str(s), NAME_ONLY="1")).stdout.strip()
             for s in (SERIES, STAGED / "engine-fixes.series", PATCHES / "series")]
    assert len(set(names)) == 3 and all(len(n) == 12 for n in names), names
    live = (PATCHES / "series").read_text(encoding="utf-8")
    assert PATCH.name not in live and REFUSE.name not in live
    head = SERIES.read_text(encoding="utf-8")
    assert "With every switch off it must gate as 9ea95e93a83c does" in head
    assert not (STAGED / "parking-prototype.series").exists(), "no prototype engine of its own"
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert PATCH.name in readme and "NEVER RUN ON A CARD" in readme


def test_the_pool_s_refusal_can_be_provoked_without_starving_the_card():
    """Staged 0013 was not provoked on the card (2026-11-24: at 31,701 of 32,002 MiB the driver paged a neighbour
    out instead of refusing). 0016 refuses a pool's GROWTH on demand, raising what the device raises, so the path
    after it is the real one; a pool's first commit is left alone or no model would load."""
    text = REFUSE.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == ["backend/cpp/llama-cpp/patches/0016-sycl-pool-refuse-switch.patch"]
    inner = "\n".join(_added(text[text.index("\n---\n"):]))
    code = "\n".join(_added(inner))
    assert "std::atomic<int> g_ggml_sycl_pool_refuse{0};" in code, "off unless asked"
    assert 'ggml_sycl_get_env("GGML_SYCL_POOL_REFUSE", 0)' in code
    assert "if (pool_size > 0 && g_ggml_sycl_pool_refuse > 0 && g_ggml_sycl_pool_refuse.fetch_sub(1) > 0) {" in code
    assert "throw sycl::exception(sycl::make_error_code(sycl::errc::memory_allocation)," in code, \
        "the device's own kind of exception, raised inside the try the fix catches"
    # in the patch it is placed before the device is asked, inside 0013's try
    assert re.search(r"^ +try \{\n(\+.*\n)+ +phys\.emplace\(dev, ctx, reserve_size\);", inner, re.M)
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert REFUSE.name in readme and "NOT PROVOKED" in readme and "31,701 of 32,002 MiB" in readme
    room = (ROOT / "docs" / "notes" / "room-on-the-card.md").read_text(encoding="utf-8")
    assert "NOT PROVOKED" in room and "GGML_SYCL_POOL_REFUSE=N" in room
    assert "| THE POOL'S REFUSAL (staged 0013), provoked |" in NOTE and "GGML_SYCL_POOL_REFUSE=2" in NOTE


def test_the_note_says_what_ran_without_a_card_and_what_the_node_measures():
    assert "nothing\nof it has run on the card yet" in NOTE
    tested = NOTE[NOTE.index("## 3. "):NOTE.index("## 4. ")]
    assert "16 of 16" in tested and "21 of 21" in tested and "Intel's compiler 2025.3.3" in tested
    assert "What that does NOT show: anything about the card." in tested
    node = NOTE[NOTE.index("## 4. "):NOTE.index("## 5. ")]
    for run in ("| R0, the control |", "| R1, parkable and not parking |", "| R2, parking, verified |",
                "| R3, parking, timed |", "| the floor |", "| a refusal"):
        assert run in node, run
    assert "engine-next.series" in node and "keep_context_ms:0" in node and "verbosity: 4" in node
    assert "The first thing the run says" in node, "a load on mapped memory that fails stops the prototype"
    # the stand-in and its test are in the repository and name each other
    standin = STANDIN.read_text(encoding="utf-8")
    assert "DEV ONLY, NOT IN THE PATCH" in standin and "memset(base, 0xA5, buffer->size);" in standin
    for env in ("GGML_CPU_PARK_TEST", "GGML_CPU_PARK_TEST_REFUSE", "GGML_CPU_PARK_TEST_LOSE"):
        assert env in standin and env in CPU_TEST.read_text(encoding="utf-8"), env
    assert STANDIN.name in CPU_TEST.read_text(encoding="utf-8") and STANDIN.name in NOTE and CPU_TEST.name in NOTE
    # the stand-in is not in the engine: the patch does not carry it
    assert "GGML_CPU_PARK_TEST" not in PATCH.read_text(encoding="utf-8")
