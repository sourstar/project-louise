"""The engine a ROLLBACK puts back since 2026-11-27 14:45:24Z, aea955022a8f (live from
2026-11-26 23:32Z, München; the operator's yes): the park engine's series, then a clip's last
word kept (0030) (deploy/localai/patches/staged/engine-fade.series).

An engine's name is the digest of its series file and of every patch in it,
comments included. Held here: that the file and its patches still name the
engine a rollback puts back, that it is the engine before it plus 0030, and
that the notes say it is the rollback - and that a rollback to it, which parks
and paces nothing, takes the server's 0032 out first. What 0030 is and how it was measured is
tests/test_localai_staged_omnivoice_fade_out.py's."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
SERIES = PATCHES / "staged" / "engine-fade.series"
ROLLBACK = PATCHES / "staged" / "engine-park.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
LIVE = "aea955022a8f"   # live 2026-11-26 23:32Z to 2026-11-27 14:45:24Z; the rollback engine since
FADE = "staged/0030-llama-cpp-omnivoice-fade-out-ends-with-the-kept-silence.patch"


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_the_file_and_its_patches_still_name_the_engine_that_runs():
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == LIVE, (
        "engine-fade.series or one of its patches changed: that is ANOTHER engine than the one a rollback puts "
        "back, and the live engine's series starts with its lines")


def test_it_is_the_rollback_engine_and_the_fade():
    assert _lines(SERIES) == _lines(ROLLBACK) + [FADE]
    for absent in ("0024-model-readiness-poll", "0027-model-park", "0029-watchdog-settings-update-hang",
                   "0031-model-park-floor-refusal"):
        assert not any(absent in line for line in _lines(SERIES)), f"{absent}: the server's, in no engine"


def test_the_readme_and_the_status_say_it_is_the_rollback_and_how_a_rollback_goes():
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    assert (f"**Live 2026-11-26 23:32Z to 2026-11-27 14:45:24Z, engine `{LIVE}`, and the engine a rollback puts back: "
            "`engine-fade.series`**") in readme
    assert readme.count("and the engine a rollback puts back") == 1, "one engine is the rollback engine"
    assert readme.count("LIVE since ") == 1, "one engine is live"
    assert "the five llama.cpp gates\nexact with every fingerprint unchanged" in readme
    # it parks and paces nothing: with the server's 0032 a rollback to it would copy every ARC-counted park at full speed
    assert ("**A ROLLBACK to `aea955022a8f` (it parks, and paces nothing) takes the server's 0032 out FIRST, back to 0031: left in,\n"
            "every park admitted on the ARC copies at full speed.**") in readme
    status = (ROOT / "docs" / "notes" / "STATUS.md").read_text(encoding="utf-8")
    assert f"**Live 2026-11-26 23:32Z to 2026-11-27 14:45:24Z, and the engine a rollback puts back: engine `{LIVE}`" in status
    assert "ONCE THE SERVER'S 0032 IS LIVE A ROLLBACK TAKES 0032 OUT FIRST" in status
    bugs = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
    assert f"in engine `{LIVE}` to 2026-11-27 14:45:24Z" in next(
        line for line in bugs.splitlines() if line.startswith("| **P28 (staged)** |"))
