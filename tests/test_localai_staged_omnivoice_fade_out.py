"""Staged 0030: the speech port keeps a clip's last word - the fade-out lasts as
long as the trailing silence the trim kept (docs/notes/omnivoice-on-our-backend.md
§15; München, 2026-11-26: the operator hears the last word cut at full stops,
and 30 of 40 clips read aloud faded speech, one losing a syllable).

Held here: what the patch changes and nothing more; that it says where it
departs from upstream; that a clip with 100 ms of its own silence is faded with
upstream's very sample count; the patch's own test, compiled and run on the
header the staged patches build; the candidate that carries it and its name."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
PATCH = STAGED / "0030-llama-cpp-omnivoice-fade-out-ends-with-the-kept-silence.patch"
PORT = STAGED / "0009-llama-cpp-omnivoice.patch"
SERIES = STAGED / "engine-fade.series"
LIVE = STAGED / "engine-park.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
INNER = "backend/cpp/llama-cpp/patches/0024-mtmd-omnivoice-fade-out-ends-with-the-kept-silence.patch"
NOTE = (ROOT / "docs" / "notes" / "omnivoice-on-our-backend.md").read_text(encoding="utf-8")
NAME = "aea955022a8f"


def _added(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _removed(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")]


def _inner(outer_path: Path = PATCH) -> str:
    """The llama.cpp patch an outer patch adds, whole: every added line, its own `+++` headers included - only the
    outer patch's header of the file it creates is not the inner patch's."""
    outer = outer_path.read_text(encoding="utf-8")
    body = outer[outer.index("\n---\n"):]
    return "\n".join(line[1:] for line in body.splitlines()
                     if line.startswith("+") and not line.startswith("+++ b/backend/")) + "\n"


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
    assert set(re.findall(r"^diff --git a/(\S+)", _inner(), re.M)) == {
        "tools/mtmd/mtmd-helper-gen.cpp", "vendor/omnivoice/audio-postproc.h", "tools/mtmd/tests/test-trailing-fade.cpp"}


def test_the_fade_out_is_held_to_the_kept_silence_and_the_fade_in_is_upstream_s():
    inner = _inner()
    added, removed = "\n".join(_added(inner)), "\n".join(_removed(inner))
    # finish(): the trim says what it kept, the fade-out is held to it
    assert "ov::remove_silence(audio, info.sample_rate, 500, 100, 100, -50.0, &kept_trail);" in added
    assert "const int fade_out_n = ov::fade_out_for_kept_trail(kept_trail, info.sample_rate, 0.1);" in added
    assert "ov::fade_and_pad(audio, info.sample_rate, 0.1, 0.1, fade_out_n);" in added
    assert "ov::fade_and_pad(audio, info.sample_rate, 0.1, 0.1);" in removed
    # the rule: at most the fade-in's own sample count (so 100 ms of silence is upstream's fade), never under 5 ms
    rule = added[added.index("static int fade_out_for_kept_trail(int kept_trail_n, int sr, double fade_dur) {"):]
    rule = rule[:rule.index("\n}")]
    assert "const int most  = (int) (fade_dur * (double) sr);" in rule
    assert "int fade_n = (int) (fade_dur * (double) sr);" in "\n".join(_added(_inner(PORT))), "the fade-in's count, as ported"
    assert "const int least = std::min(sr / 200, most);" in rule
    assert "return std::max(std::min(kept_trail_n, most), least);" in rule
    # what the trim kept: what the scan found silent, less what it cut, up to the trail asked
    assert "*kept_trail_n = std::min(silent_trail - trim_trail, (int) s16.size());" in added
    # the default is upstream's: a caller that names no fade-out gets the fade-in's length
    assert "int fade_out_n = -1)" in added and "int out_n  = fade_out_n < 0 ? fade_n : fade_out_n;" in added
    # nothing else of the post-processing moves: the trim's parameters, the level, the pad
    for line in ("std::vector<float> padded((size_t) pad_n + a.size() + (size_t) pad_n, 0.0f);",
                 "ov::peak_normalize_half(audio);"):
        assert line not in removed, line


def test_it_says_where_it_departs_from_upstream_and_why():
    added = "\n".join(_added(_inner()))
    assert "with ONE DEPARTURE:" in added and "fade_and_pad_audio in omnivoice/utils/audio.py" in added
    assert "measured on 40 clips of the pipeline, 30 faded speech and" in added
    assert "THIS DEPARTS FROM upstream's fade_and_pad_audio" in _inner()
    # and the raw audio the model gave, for the separate question of the model's own length
    assert 'trace_dump("audio-joined", raw_joined, { (int32_t) raw_joined.size() });' in added
    assert re.search(r"std::vector<float> raw_joined;\n\s+if \(trace_dir\(\)\) \{\n\s+raw_joined = audio;", added)


def _header_from_the_patches(tmp_path: Path) -> Path:
    """vendor/omnivoice/ as the staged patches build it: the port's files (0009's inner patch), then 0030's."""
    for name, outer in (("port", PORT), ("fade", PATCH)):
        (tmp_path / f"{name}.patch").write_text(_inner(outer), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path / "tree")], check=True)
    for name in ("port", "fade"):
        done = subprocess.run(["git", "apply", "--include=vendor/omnivoice/*", "--include=tools/mtmd/tests/test-trailing-fade.cpp",
                               str(tmp_path / f"{name}.patch")], cwd=tmp_path / "tree", capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    return tmp_path / "tree"


@pytest.mark.skipif(shutil.which("c++") is None or shutil.which("git") is None, reason="no C++ compiler or git here")
def test_the_patch_s_own_test_passes_on_the_header_the_patches_build(tmp_path):
    tree = _header_from_the_patches(tmp_path)
    test = tree / "tools" / "mtmd" / "tests" / "test-trailing-fade.cpp"
    built = subprocess.run(["c++", "-std=c++17", "-w", "-I", str(tree / "vendor"), str(test), "-o", str(tmp_path / "t")],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr[-2000:]
    ran = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, timeout=120)
    assert ran.returncode == 0, ran.stdout
    lines = ran.stdout.splitlines()
    assert lines[-1] == "all passed" and sum(line.startswith("ok:") for line in lines) == 14
    for said in ("the tone is not attenuated beyond its last 5 ms", "the clip is the same bytes as upstream's",
                 "the pad and the fade-in at the start are upstream's, to the byte",
                 "(upstream faded more than 50 ms of the tone)"):
        assert f"ok:   {said}" in lines, said
    faded = next(line for line in lines if "the tone's samples attenuated at its end" in line)
    assert re.search(r": 0 \(upstream (\d+)\)", faded) and int(re.search(r"upstream (\d+)", faded).group(1)) > 1800


def test_the_candidate_is_the_live_engine_plus_this_patch_and_its_name_is_held():
    assert _lines(SERIES) == _lines(LIVE) + ["staged/" + PATCH.name]
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == NAME
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert f"engine-fade.series` (`{NAME}`)" in readme


def test_the_note_says_what_was_measured_and_what_was_not():
    section = NOTE[NOTE.index("## 15. A clip's last word is kept"):]
    for said in ("30 of 40", "f2ff332553", "MEASURED", "sample for sample", "-27.6 dBFS", "NOT measured", NAME,
                 "engine-fade.series", "audio-joined.bin", "the operator's word", "(b)",
                 # the card, after the swap: no fade on speech, and the model's own length cutting 2 of 3
                 "every clip ends in sound with NO FADE ON SPEECH", "last 5 ms -36.5",
                 "THE MODEL'S OWN LENGTH ENDS INSIDE THE LAST WORD IN\n2 OF 3."):
        assert said in section, said
