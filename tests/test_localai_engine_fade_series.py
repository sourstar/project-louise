"""The LIVE engine, aea955022a8f (München, 2026-11-26 23:32Z; the operator's yes): the
park engine's series, then a clip's last word kept (0030)
(deploy/localai/patches/staged/engine-fade.series).

An engine's name is the digest of its series file and of every patch in it,
comments included. Held here: that the file and its patches still name the
engine that is running, that it is the one a rollback puts back plus 0030,
that the notes say which engine is live, and that the node's speech model
names it. What 0030 is and how it was measured is
tests/test_localai_staged_omnivoice_fade_out.py's."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
SERIES = PATCHES / "staged" / "engine-fade.series"
ROLLBACK = PATCHES / "staged" / "engine-park.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
LIVE = "aea955022a8f"
FADE = "staged/0030-llama-cpp-omnivoice-fade-out-ends-with-the-kept-silence.patch"


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_the_file_and_its_patches_still_name_the_engine_that_runs():
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == LIVE, (
        "engine-fade.series or one of its patches changed: that is ANOTHER engine than the live one. "
        "Build it, gate every run on it, and only then change the name here and in the staged README")


def test_it_is_the_rollback_engine_and_the_fade():
    assert _lines(SERIES) == _lines(ROLLBACK) + [FADE]
    for absent in ("0024-model-readiness-poll", "0027-model-park", "0029-watchdog-settings-update-hang",
                   "0031-model-park-floor-refusal"):
        assert not any(absent in line for line in _lines(SERIES)), f"{absent}: the server's, in no engine"


def test_the_readme_and_the_status_say_what_is_live():
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    assert f"**LIVE since 2026-11-26 23:32Z, engine `{LIVE}`: `engine-fade.series`**" in readme
    assert readme.count("LIVE since ") == 1, "one engine is live"
    assert "the five llama.cpp gates\nexact with every fingerprint unchanged" in readme
    status = (ROOT / "docs" / "notes" / "STATUS.md").read_text(encoding="utf-8")
    assert f"**LIVE since 2026-11-26 23:32Z: engine `{LIVE}`" in status
    assert "the\n  rollback engine is `ed93e3c1b2e2` (it parks, so a rollback keeps parking)" in status
    bugs = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
    assert f"**LIVE in engine `{LIVE}` since 2026-11-26 23:32Z**" in next(
        line for line in bugs.splitlines() if line.startswith("| **P28 (staged)** |"))


def test_the_staged_speech_model_names_the_live_engine():
    """The node's direct models' files are moved to the new engine's backend at each swap (München). The stack is
    the park engine's (intel-stack-2026-11c.conf and its layer unchanged), so the name differs in the engine alone."""
    text = (ROOT / "deploy" / "vega" / "stage" / "models" / "louise-tts-omnivoice.yaml").read_text(encoding="utf-8")
    assert f'"backend": "louise-sycl-f16-llama-cpp-{LIVE}-stack-7c301a7ba437"' in text
    assert "d531d5d804f1" in text, "the engine its card rows were taken on is still said, as what it was"
