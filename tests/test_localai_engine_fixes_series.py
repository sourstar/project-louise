"""The candidate engine München builds on the operator's word of 2026-11-24:
the live series and the three staged engine fixes, nothing else
(deploy/localai/patches/staged/engine-fixes.series).

A candidate series is a file build.sh takes by SERIES_FILE; its name is the
digest of the file and of every patch in it. Held here: that it IS the live
series with exactly the three staged patches after it — a candidate that
dropped or reordered a live patch would be another engine under the same
intent — and that build.sh reads it."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
CANDIDATE = PATCHES / "staged" / "engine-fixes.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
FIXES = ["staged/0012-llama-cpp-sycl-reorder-experts-switch.patch",
         "staged/0013-llama-cpp-sycl-pool-refusal.patch",
         "staged/0014-llama-cpp-second-signal-exit.patch"]


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def _name(series: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(series), NAME_ONLY="1"), cwd=ROOT)


def test_it_is_the_live_series_and_the_three_fixes_after_it():
    assert _lines(CANDIDATE) == _lines(PATCHES / "series") + FIXES
    for patch in FIXES:
        assert (PATCHES / patch).is_file(), patch


def test_build_sh_reads_it_and_names_another_engine_than_the_live_one():
    candidate, live = _name(CANDIDATE), _name(PATCHES / "series")
    assert candidate.returncode == 0 and live.returncode == 0, candidate.stderr + live.stderr
    assert len(candidate.stdout.strip()) == 12 and candidate.stdout != live.stdout


def test_the_staged_readme_says_how_it_is_built_and_when_it_goes():
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    assert "SERIES_FILE=deploy/localai/patches/staged/engine-fixes.series" in readme
    assert "`engine-fixes.series`" in readme
