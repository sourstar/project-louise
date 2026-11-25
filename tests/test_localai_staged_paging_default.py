"""Staged 0021: on SYCL a model pages by default (operator, 2026-11-25,
relayed by the architecture session: "Set the new paging mode as the default
in the engine"; docs/notes/room-on-the-card.md §3).

Page instead of unload needs two engine switches on every model of the card.
They defaulted the other way, so each model's file had to carry them and a
model added later could not page. 0021 flips the two defaults and nothing
else. Held here: that it is exactly that; that a model's own file still
decides (the architecture session's condition: one model can be held back
without another engine); that it is a candidate after the refusal's, not
built before the refusal holds; and that the keeper's default and the
deploy's agree with it."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
STAGED = PATCHES / "staged"
PATCH = STAGED / "0021-llama-cpp-sycl-paging-is-the-default.patch"
SERIES = STAGED / "engine-page.series"
BEFORE = STAGED / "engine-refusal.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
INNER = "backend/cpp/llama-cpp/patches/0020-sycl-paging-is-the-default.patch"
NOTE = (ROOT / "docs" / "notes" / "room-on-the-card.md").read_text(encoding="utf-8")


def _added(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _removed(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")]


def _inner() -> str:
    outer = PATCH.read_text(encoding="utf-8")
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
    # what a deployment that pages must do beside it is said to whoever reads the patch upstream
    assert "memory reclaimer" in message and "A model's own `env` still decides" in " ".join(message.split())
    # a tensor above the 2 GiB cap is NOT refused (the allocator gives it a buffer of its own): the patch must not
    # say that a model needs a line in its file to load - it was written so once, from memory, and it is not true
    assert "to load" not in PATCH.read_text(encoding="utf-8").replace("a load the", "").replace("a load after", "")
    assert "it gets a buffer of its own size" in " ".join(message.split())


def test_it_flips_the_two_defaults_and_nothing_else():
    inner = _inner()
    inner = inner[inner.index("\n---\n"):]          # its diff: the message above has lines that begin with a dash
    added, removed = _added(inner), _removed(inner)
    assert set(re.findall(r"^diff --git a/(\S+)", inner, re.M)) == {"ggml/src/ggml-sycl/ggml-sycl.cpp"}
    code_removed = [line.strip() for line in removed if line.strip() and not line.strip().startswith("//")]
    assert code_removed == [
        "int g_ggml_sycl_bmg_vram_check = 1;",
        "int g_ggml_sycl_host_pinned_mem_2g = 0;",
        'g_ggml_sycl_bmg_vram_check = ggml_sycl_get_env("GGML_SYCL_BMG_VRAM_CHECK", 1);',
        'ggml_sycl_get_env("GGML_SYCL_HOST_PINNED_MEM_2G", 0) & g_ggml_sycl_enable_host_pinned_mem;']
    code_added = [line.strip().split("  //")[0].strip() for line in added
                  if line.strip() and not line.strip().startswith("//")]
    assert code_added == [
        "int g_ggml_sycl_bmg_vram_check = 0;",
        "int g_ggml_sycl_host_pinned_mem_2g = 1;",
        'g_ggml_sycl_bmg_vram_check = ggml_sycl_get_env("GGML_SYCL_BMG_VRAM_CHECK", 0);',
        'ggml_sycl_get_env("GGML_SYCL_HOST_PINNED_MEM_2G", 1) & g_ggml_sycl_enable_host_pinned_mem;']


@pytest.mark.skipif(shutil.which("g++") is None, reason="no C++ compiler here")
def test_a_model_s_own_file_still_wins_over_the_new_default(tmp_path):
    """The architecture session's condition. Both switches are still read through the backend's
    `ggml_sycl_get_env(name, default)` — the patch only changes the second argument — and that function is
    upstream's (ggml-sycl/common.cpp at the pin, copied here; the engine's own object was run under Intel's compiler
    by tools/measure/sycl_env_default_check.cpp): the environment's number wins, anything else is the default."""
    added = "\n".join(_added(_inner()))
    calls = re.findall(r'ggml_sycl_get_env\("(GGML_SYCL_[A-Z0-9_]+)", (\d)\)', added)
    assert calls == [("GGML_SYCL_BMG_VRAM_CHECK", "0"), ("GGML_SYCL_HOST_PINNED_MEM_2G", "1")]
    program = r'''
#include <cstdio>
#include <cstdlib>
// ggml-sycl/common.cpp, llama.cpp 78e0529
int ggml_sycl_get_env(const char *env_name, int default_val) {
    char *user_device_string = getenv(env_name);
    int user_number = default_val;

    unsigned n;
    if (user_device_string != NULL &&
        sscanf(user_device_string, " %u", &n) == 1) {
        user_number = (int)n;
    } else {
        user_number = default_val;
    }
    return user_number;
}
int main() {
    printf("%d %d\n", ggml_sycl_get_env("''' + calls[0][0] + '", ' + calls[0][1] + '''), ggml_sycl_get_env("''' + calls[1][0] + '", ' + calls[1][1] + '''));
    return 0;
}
'''
    (tmp_path / "t.cpp").write_text(program, encoding="utf-8")
    built = subprocess.run(["g++", "-std=c++17", "-Wall", "-Werror", str(tmp_path / "t.cpp"), "-o", str(tmp_path / "t")],
                           capture_output=True, text=True, timeout=120)
    assert built.returncode == 0, built.stderr
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GGML_SYCL_")}

    def run(**env: str) -> str:
        return subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, timeout=30, env=dict(clean, **env)).stdout.strip()
    assert run() == "0 1", "nothing set: the model pages"
    assert run(GGML_SYCL_BMG_VRAM_CHECK="1") == "1 1", "a model's file brings the engine's check back for it"
    assert run(GGML_SYCL_HOST_PINNED_MEM_2G="0") == "0 0", "a model's file keeps its large buffers"
    assert run(GGML_SYCL_BMG_VRAM_CHECK="1", GGML_SYCL_HOST_PINNED_MEM_2G="0") == "1 0"
    assert run(GGML_SYCL_BMG_VRAM_CHECK="yes") == "0 1", "a value that is no number is the default"
    tool = (ROOT / "tools" / "measure" / "sycl_env_default_check.cpp").read_text(encoding="utf-8")
    assert "GGML_SYCL_BMG_VRAM_CHECK: 0  GGML_SYCL_HOST_PINNED_MEM_2G: 1" in tool, "the engine's own object, run"


def test_it_is_a_candidate_after_the_refusal_s_and_waits_for_it():
    assert _lines(SERIES) == _lines(BEFORE) + ["staged/" + PATCH.name]
    for series in (BEFORE, STAGED / "engine-next.series", PATCHES / "series"):
        assert PATCH.name not in series.read_text(encoding="utf-8"), series.name
    names = {_name(SERIES), _name(BEFORE), _name(STAGED / "engine-next.series")}
    assert len(names) == 3 and all(len(n) == 12 for n in names)
    head = SERIES.read_text(encoding="utf-8")
    assert "NOT BEFORE 0018's ROW HAS PASSED" in head and "every gate again" in head
    assert "is merged only once THIS engine is the live" in head


