"""Staged 0032 and 0033: the ZFS ARC counted by the server's park check, and the
engine's copy to host memory paced on a reserve (docs/notes/parking-step-2.md
§15; the operator's "C", 2026-11-26).

Held here: that 0032 is the SERVER's and 0033 the engine's, each where it
belongs; the rule each applies, and that unset the engine runs as before;
the one constraint between the reserve and ZFS, said wherever the switch is;
the engine's own test, compiled and run from the patch; the candidate and its
name."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
SERVER = STAGED / "0032-model-park-counts-the-zfs-arc.patch"
ENGINE = STAGED / "0033-llama-cpp-sycl-park-paced-by-the-host-reserve.patch"
SERIES = STAGED / "engine-pace.series"
LIVE = STAGED / "engine-fade.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
NOTE = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")
NAME = "3216ace3205f"
INNER = "backend/cpp/llama-cpp/patches/0025-sycl-park-paced-by-the-host-reserve.patch"


def _added(text: str) -> str:
    return "\n".join(line[1:] for line in text[text.index("\n---\n"):].splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


def _inner(outer: Path) -> str:
    text = outer.read_text(encoding="utf-8")
    body = text[text.index("\n---\n"):]
    return "\n".join(line[1:] for line in body.splitlines()
                     if line.startswith("+") and not line.startswith("+++ b/backend/")) + "\n"


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_each_is_a_mail_under_localai_s_policy_where_it_belongs():
    for patch, files in ((SERVER, ["pkg/model/park.go", "pkg/model/park_internal_test.go",
                                   "pkg/xsysinfo/memory_headroom.go", "pkg/xsysinfo/memory_headroom_test.go"]),
                         (ENGINE, [INNER])):
        text = patch.read_text(encoding="utf-8")
        assert text.startswith("From 0000000000000000000000000000000000000000 ")
        message = text[:text.index("\n---\n")]
        assert re.search(r"^Assisted-by: ", message, re.M)
        assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
        assert re.findall(r"^diff --git a/(\S+)", text, re.M) == files
    assert set(re.findall(r"^diff --git a/(\S+)", _inner(ENGINE), re.M)) == {
        "ggml/src/ggml-sycl/ggml-sycl.cpp", "ggml/src/ggml-sycl/park-pace.hpp", "tests/test-sycl-park-pace.cpp"}
    for series in (*STAGED.glob("*.series"),):
        assert SERVER.name not in series.read_text(encoding="utf-8"), f"{series.name}: 0032 is the server's"


def test_the_server_counts_the_evictable_part_with_its_margin_and_its_caps():
    added = _added(SERVER.read_text(encoding="utf-8"))
    for said in ('case "mru_evictable_data", "mru_evictable_metadata", "mfu_evictable_data", "mfu_evictable_metadata":',
                 "const ARCMargin = uint64(1) << 30", "can := min(r.ARCEvictable, r.ARCSize-r.ARCMin)",
                 "best := r.HostAvailable + r.ARCCounted()", "free, saw = reading.ForParking(), reading.String()"):
        assert said in added, said
    rule = added[added.index("func (r MemoryReading) ForParking() uint64 {"):]
    rule = rule[:rule.index("\n}")]
    assert "if c.Limited && c.Headroom < best {" in rule, "a cgroup's room still caps it: the ARC is the host's"
    assert "func (r MemoryReading) Allocatable() uint64 {" not in added, "Allocatable is not touched"
    assert 'Expect(err).NotTo(HaveOccurred(), "the ARC gives back what the copy needs")' in added


def test_the_engine_waits_on_the_reserve_and_unset_runs_as_before():
    inner = _inner(ENGINE)
    added = "\n".join(line[1:] for line in inner.splitlines() if line.startswith("+") and not line.startswith("+++"))
    assert "static constexpr size_t PIECE = 256ull << 20;" in added
    assert 'from(std::getenv("GGML_SYCL_PARK_HOST_RESERVE"), std::getenv("GGML_SYCL_PARK_HOST_WAIT_MS"))' in added
    assert "int64_t wait_ms = 30000;" in added and "int64_t step_ms = 50;" in added
    # unset: the copy as it was, one call to the old copy, no reading
    to_host = added[added.index("static bool ggml_sycl_park_copy_to_host("):]
    to_host = to_host[:to_host.index("\n}\n")]
    assert "if (pace.reserve == 0) {\n        ggml_sycl_park_copy(q, dst, src, size);\n        return true;\n    }" in to_host
    # at the bound: the host copy freed, the device never released, NO_MEMORY
    refusal = added[added.index("if (!ggml_sycl_park_copy_to_host("):]
    refusal = refusal[:refusal.index("return GGML_BACKEND_PARK_NO_MEMORY;")]
    assert "free_aligned_mem_host(mem->host);" in refusal and "mem->host = nullptr;" in refusal
    assert "mem->release()" not in refusal
    # the constraint, said where the switch is
    assert "THE RESERVE MUST NOT BE ABOVE WHAT ZFS KEEPS FREE (zfs_arc_sys_free)" in added


@pytest.mark.skipif(shutil.which("c++") is None or shutil.which("git") is None, reason="no C++ compiler or git here")
def test_the_engine_s_own_test_passes_from_the_patch(tmp_path):
    (tmp_path / "inner.patch").write_text(_inner(ENGINE), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path / "tree")], check=True)
    done = subprocess.run(["git", "apply", "--include=ggml/src/ggml-sycl/park-pace.hpp",
                           "--include=tests/test-sycl-park-pace.cpp", str(tmp_path / "inner.patch")],
                          cwd=tmp_path / "tree", capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    tree = tmp_path / "tree"
    built = subprocess.run(["c++", "-std=c++17", "-w", "-I", str(tree / "ggml" / "src" / "ggml-sycl"),
                            str(tree / "tests" / "test-sycl-park-pace.cpp"), "-o", str(tmp_path / "t")],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr[-2000:]
    ran = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stdout
    lines = ran.stdout.splitlines()
    assert lines[-1] == "all passed" and sum(line.startswith("ok:") for line in lines) == 12


def test_the_candidate_is_the_live_engine_plus_0033_and_its_name_is_held():
    assert _lines(SERIES) == _lines(LIVE) + ["staged/" + ENGINE.name]
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == NAME


def test_the_notes_say_the_constraint_and_what_was_not_measured():
    section = NOTE[NOTE.index("## 15. The ZFS ARC counted, the copy paced"):]
    for said in ("**THE ONE CONSTRAINT: the reserve must not be above `zfs_arc_sys_free`.**", NAME, "1222e3616d6b569cc577cc9a0c27f014476d11ae",
                 "15,032,385,536", "| a park paced on the card, the ARC giving back under it | NOT measured"):
        assert said in section, said
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert f"**A candidate after the live engine, a park paced by the host: `engine-pace.series`** (`{NAME}`;" in readme
