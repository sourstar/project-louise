"""Staged 0018: the SYCL pool's refusal is not a std::exception, so that it
reaches the graph (docs/notes/room-on-the-card.md §4; localai-bugs.md B18 /
P14 / P18).

Staged 0013 said a refused pool commit fails the request and leaves the
backend running. On the card it did not (München, 2026-11-25, engine
b190673dbdbf, GGML_SYCL_POOL_REFUSE=2): the refusal was a std::runtime_error,
the backend's own CHECK_TRY_ERROR catches every std::exception around an op,
and SYCL_CHECK then aborts. 0018 makes the refusal a type that only the
graph's own catch takes.

Held here: the type's shape, by building it from the two patches' own text
and throwing it through a handler of the kind that took it; that it is a new
patch after the live engine's, in a candidate of its own; and that the notes
say what the card showed, not what 0013 hoped."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
STAGED = PATCHES / "staged"
OLD = STAGED / "0013-llama-cpp-sycl-pool-refusal.patch"
PATCH = STAGED / "0018-llama-cpp-sycl-pool-refusal-reaches-the-graph.patch"
SERIES = STAGED / "engine-refusal.series"
LIVE = STAGED / "engine-next.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
INNER = "backend/cpp/llama-cpp/patches/0018-sycl-pool-refusal-is-not-a-std-exception.patch"


def _added(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _removed(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")]


def _inner(path: Path) -> str:
    outer = path.read_text(encoding="utf-8")
    return "\n".join(_added(outer[outer.index("\n---\n"):]))


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def _name(series: Path) -> str:
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(series), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_it_is_a_mail_under_localai_s_policy_and_adds_one_engine_patch():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == [INNER]
    assert "each gone at its first refusal" in " ".join(message.split()), "what the device showed is in the patch that answers it"


def test_the_refusal_is_no_longer_an_exception_any_op_can_take():
    inner = _inner(PATCH)
    added, removed = "\n".join(_added(inner)), "\n".join(_removed(inner))
    assert "struct ggml_sycl_out_of_device_memory : public std::runtime_error {" in removed
    # (the struct's closing brace is the old one's: a context line of the diff)
    assert "struct ggml_sycl_out_of_device_memory {\n    const char * what;\nstatic_assert(" in added
    # the build itself refuses the old shape, whoever writes it next
    assert "static_assert(!std::is_base_of<std::exception, ggml_sycl_out_of_device_memory>::value," in added
    assert "static_assert(!std::is_base_of<sycl::exception, ggml_sycl_out_of_device_memory>::value," in added
    assert "#include <type_traits>" in added
    assert 'throw ggml_sycl_out_of_device_memory{ "the SYCL pool could not commit device memory" };' in added
    assert 'e.what);' in added and 'e.what());' in removed
    # nothing else of 0013 moves: the same throw site, the same catch, the same answer
    assert set(re.findall(r"^diff --git a/(\S+)", inner, re.M)) == {"ggml/src/ggml-sycl/ggml-sycl.cpp"}
    assert "GGML_STATUS_ALLOC_FAILED" not in added + removed and "phys.emplace" not in added + removed


@pytest.mark.skipif(shutil.which("g++") is None, reason="no C++ compiler here")
def test_a_handler_for_std_exception_took_the_old_refusal_and_lets_the_new_one_pass(tmp_path):
    """The mechanism, with each patch's own type: CHECK_TRY_ERROR is `try { expr; } catch (std::exception const& e)`
    around the op (ggml-sycl/common.hpp; the real macros are run under Intel's compiler by
    tools/measure/sycl_refusal_macro_check.cpp)."""
    old = re.search(r"struct ggml_sycl_out_of_device_memory : public std::runtime_error \{\n.*?\n\};",
                    "\n".join(_added(_inner(OLD))), re.S)
    new = re.search(r"(struct ggml_sycl_out_of_device_memory \{\n    const char \* what;\n)(static_assert\(.*?\);)",
                    "\n".join(_added(_inner(PATCH))), re.S)
    assert old is not None and new is not None
    new_type = new.group(1) + "};\n" + new.group(2)   # the closing brace is a context line of the diff
    program = '''
#include <cstdio>
#include <exception>
#include <stdexcept>
#include <type_traits>
namespace before { %s }
namespace after  { %s }
// the op under the backend's macro: any std::exception is taken here, and SYCL_CHECK then aborts
template <typename F> static int check_try_error(F && op) {
    try { op(); return 0; } catch (std::exception const & e) { return 1; }
}
int main() {
    int taken_before = -1, taken_after = -1;
    try { taken_before = check_try_error([] { throw before::ggml_sycl_out_of_device_memory("refused"); }); }
    catch (const before::ggml_sycl_out_of_device_memory &) { taken_before = 0; }
    try { taken_after = check_try_error([] { throw after::ggml_sycl_out_of_device_memory{ "refused" }; }); }
    catch (const after::ggml_sycl_out_of_device_memory & e) { taken_after = 0; std::printf("the graph got: %%s\\n", e.what); }
    std::printf("taken by the op: before %%d, after %%d\\n", taken_before, taken_after);
    return taken_before == 1 && taken_after == 0 ? 0 : 1;
}
''' % (old.group(0), new_type)
    (tmp_path / "t.cpp").write_text(program, encoding="utf-8")
    built = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", str(tmp_path / "t.cpp"), "-o", str(tmp_path / "t")],
                           capture_output=True, text=True, timeout=180)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "taken by the op: before 1, after 0" in ran.stdout and "the graph got: refused" in ran.stdout


def test_it_is_a_candidate_of_its_own_after_the_live_engine_untouched():
    assert _lines(SERIES) == _lines(LIVE) + ["staged/" + PATCH.name,
                                             "staged/0020-llama-cpp-omnivoice-nothing-kept-between-requests.patch"], \
        "the live engine, line for line, and the two fixes its card rows led to"
    assert PATCH.name not in LIVE.read_text(encoding="utf-8"), "the live engine's file is its name: nothing is added to it"
    name = _name(SERIES)
    assert len(name) == 12 and name != _name(LIVE)
    head = SERIES.read_text(encoding="utf-8")
    assert "it must gate as b190673dbdbf does" in head and "GGML_SYCL_POOL_REFUSE=2" in head


def test_the_notes_say_what_the_card_showed_and_what_waits_for_the_fix():
    room = (ROOT / "docs" / "notes" / "room-on-the-card.md").read_text(encoding="utf-8")
    section = room[room.index("## 4. "):]
    assert "PROVOKED, AND 0013 DID NOT HOLD" in section and PATCH.name in section
    assert "line:4221" in section and "ggml_sycl_op_mul_mat" in section
    bugs = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
    assert "| **P18 (staged)** |" in bugs and "DID NOT HOLD ON THE CARD" in bugs
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert f"`{PATCH.name}`" in readme and "`engine-refusal.series`" in readme
    assert "IT DID NOT HOLD" in readme, "0013's row says so itself"
    # page mode was to be switched on once the refusal held: it does not hold yet
    assert "Page mode waits for 0018's row on the card" in readme
    for tool in ("sycl_refusal_macro_check.cpp", "refuse_cpu_test.py", "refuse_cpu_standin.patch"):
        assert (ROOT / "tools" / "measure" / tool).is_file() and tool in room, tool
