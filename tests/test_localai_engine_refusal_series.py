"""The LIVE engine, d531d5d804f1 (München, 2026-11-25 05:52:18Z):
b190673dbdbf's series, then the pool's refusal reaching the graph (0018) and
the speech port keeping nothing of a saved voice between requests (0020)
(deploy/localai/patches/staged/engine-refusal.series).

An engine's name is the digest of its series file and of every patch in it,
comments included. Held here: that the file and its patches still name the
engine that is running, and that the notes say which engine is live and
which a rollback puts back."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
SERIES = PATCHES / "staged" / "engine-refusal.series"
BEFORE = PATCHES / "staged" / "engine-next.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
LIVE = "d531d5d804f1"


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def _name(series: Path) -> str:
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(series), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_the_file_and_its_patches_still_name_the_engine_that_runs():
    assert _name(SERIES) == LIVE, (
        "engine-refusal.series or one of its patches changed: that is ANOTHER engine than the live one. "
        "Build it, gate every run on it, and only then change the name here and in the staged README")


def test_it_is_the_engine_before_it_and_the_two_fixes():
    assert _lines(SERIES) == _lines(BEFORE) + ["staged/0018-llama-cpp-sycl-pool-refusal-reaches-the-graph.patch",
                                               "staged/0020-llama-cpp-omnivoice-nothing-kept-between-requests.patch"]
    assert "staged/0019-voiceprofile-abandoned-leases.patch" not in _lines(SERIES), "the server's patch is in no engine"


def test_the_readme_and_the_status_say_what_is_live_and_what_a_rollback_puts_back():
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    assert f"LIVE since 2026-11-25 05:52:18Z, engine `{LIVE}`: `engine-refusal.series`" in readme
    assert "engine `b190673dbdbf`, and the engine a rollback puts back: `engine-next.series`" in readme
    assert "MIRRORS, not sources" in readme and "stay the source of every build and of an engine's name" in readme
    status = (ROOT / "docs" / "notes" / "STATUS.md").read_text(encoding="utf-8")
    assert f"LIVE since 2026-11-25 05:52:18Z: engine `{LIVE}`" in status
