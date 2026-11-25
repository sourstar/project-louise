"""The LIVE engine, 619655e5833b (München, 2026-11-25 15:18:30Z; the operator:
"Build 619655e5833b now, swap it after the drain, if it still applies", then
"Deploy and engine swap"): 792f050e318e's series - built, never gated, not
swapped - then a canary refused for memory not being a canary that failed
(0028) (deploy/localai/patches/staged/engine-canary.series).

An engine's name is the digest of its series file and of every patch in it,
comments included. Held here: that the file and its patches still name the
engine that is running, that one swap carried the five patches the notes say
it carried, and that the notes say which engine is live and which a rollback
puts back. What 0028 is and how it was measured is
tests/test_localai_staged_canary_refused.py's."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
SERIES = PATCHES / "staged" / "engine-canary.series"
ROLLBACK = PATCHES / "staged" / "engine-refusal.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
LIVE = "619655e5833b"
SINCE_THE_ROLLBACK_ENGINE = ["staged/0021-llama-cpp-sycl-paging-is-the-default.patch",
                             "staged/0022-llama-cpp-omnivoice-language-tag.patch",
                             "staged/0025-llama-cpp-stream-fails-after-its-first-chunk.patch",
                             "staged/0026-llama-cpp-sycl-graph-refuse-switch.patch",
                             "staged/0028-llama-cpp-context-canary-refused-for-memory.patch"]


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_the_file_and_its_patches_still_name_the_engine_that_runs():
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == LIVE, (
        "engine-canary.series or one of its patches changed: that is ANOTHER engine than the live one. "
        "Build it, gate every run on it, and only then change the name here and in the staged README")


def test_one_swap_carried_these_five_patches_over_the_engine_a_rollback_puts_back():
    assert _lines(SERIES) == _lines(ROLLBACK) + SINCE_THE_ROLLBACK_ENGINE
    for patch in SINCE_THE_ROLLBACK_ENGINE:
        assert (PATCHES / patch).is_file(), patch
    # parking is not in it, and neither is anything of LocalAI's server
    for absent in ("0023-llama-cpp-park-when-told", "0024-model-readiness-poll", "0027-model-park"):
        assert not any(absent in line for line in _lines(SERIES)), absent


def test_the_readme_and_the_status_say_what_is_live_and_what_a_rollback_puts_back():
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    assert f"LIVE since 2026-11-25 15:18:30Z, engine `{LIVE}`: `engine-canary.series`" in readme
    assert "engine `d531d5d804f1`, and the engine a rollback puts back: `engine-refusal.series`" in readme
    assert "`792f050e318e`, built 09:58:24Z, never gated, not swapped" in readme
    assert readme.count("LIVE since ") == 1, "one engine is live"
    status = (ROOT / "docs" / "notes" / "STATUS.md").read_text(encoding="utf-8")
    assert f"LIVE since 2026-11-25 15:18:30Z: engine `{LIVE}`" in status
    assert "every fingerprint identical to before" in status and "20 × exit 0" in status
    # the keeper's side is the mode's, not a node-wide default: the operator's decision of the same evening
    assert "THE KEEPER'S RULE IS NOT A NODE-WIDE DEFAULT" in status and "QUERY MODE'S ONLY" in status
    assert "was not taken and is reverted on the branch" in status
    assert "The keeper pages in QUERY MODE only" in readme
    from app import platform_config
    assert platform_config.ModelsConfig().make_room == "unload", "the file's rule is still unload unless the file says page"


def test_the_staged_speech_model_names_the_live_engine():
    """The node's direct models' files were moved to the new engine's backend at the swap (München)."""
    text = (ROOT / "deploy" / "vega" / "stage" / "models" / "louise-tts-omnivoice.yaml").read_text(encoding="utf-8")
    assert f'"backend": "louise-sycl-f16-llama-cpp-{LIVE}-stack-7c301a7ba437"' in text
    assert "d531d5d804f1" in text, "the engine its card rows were taken on is still said, as what it was"
