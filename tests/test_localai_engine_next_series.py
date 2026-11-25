"""The LIVE engine, b190673dbdbf (München, 2026-11-25 00:55:18Z): the live
series, then the OmniVoice port and LocalAI's speech call, the three fixes,
parking and its test switch, and the experts' record — every new switch off
(deploy/localai/patches/staged/engine-next.series).

An engine's name is the digest of its series file and of every patch in it,
comments included. Held here: that the file and its patches still name the
engine that is running — a word changed in any of them is another engine,
which is built and gated again or it is not what the node runs."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
SERIES = PATCHES / "staged" / "engine-next.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
LIVE = "b190673dbdbf"
AFTER_THE_SERIES = ["staged/0009-llama-cpp-omnivoice.patch",
                    "staged/0010-llama-cpp-tts-voice-modes.patch",
                    "staged/0012-llama-cpp-sycl-reorder-experts-switch.patch",
                    "staged/0013-llama-cpp-sycl-pool-refusal.patch",
                    "staged/0014-llama-cpp-second-signal-exit.patch",
                    "staged/0015-llama-cpp-park-weights.patch",
                    "staged/0016-llama-cpp-sycl-pool-refuse-switch.patch",
                    "staged/0017-llama-cpp-moe-record.patch"]


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
        "engine-next.series or one of its patches changed: that is ANOTHER engine than the live one. "
        "Build it, gate every run on it, and only then change the name here and in the staged README")


def test_it_is_the_live_series_and_these_eight_patches_after_it():
    assert _lines(SERIES) == _lines(PATCHES / "series") + AFTER_THE_SERIES
    for patch in AFTER_THE_SERIES:
        assert (PATCHES / patch).is_file(), patch


def test_the_readme_and_the_status_say_what_is_live_and_what_a_rollback_puts_back():
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    assert f"engine `{LIVE}`: `engine-next.series`" in readme and "LIVE since 2026-11-25 00:55:18Z" in readme
    assert "engine `9ea95e93a83c`, and the engine a rollback puts back: `engine-fixes.series`" in readme
    # the first cut was built and never gated: nothing may say it ran
    assert "`5f926f4efa04`, its first cut, was built and never gated" in readme
    status = (ROOT / "docs" / "notes" / "STATUS.md").read_text(encoding="utf-8")
    assert f"LIVE since 2026-11-25 00:55:18Z: engine `{LIVE}`" in status
