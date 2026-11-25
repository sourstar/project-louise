"""Staged 0020: the speech port keeps nothing of a saved voice in the
backend's memory between two requests (docs/notes/omnivoice-on-our-backend.md
§13; the architecture session's condition, 2026-11-25: a deleted voice's
samples must not outlive it).

0009's pipeline kept the last clip — samples and rows — until another clip
came or the model was unloaded. Its behaviour was checked where it was
written (the same seeded audio five ways). Held here: what the patch removes
and what it puts in its place, that only a clip with no file is still kept
and never by its samples, and that the candidate carries it."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
PATCH = STAGED / "0020-llama-cpp-omnivoice-nothing-kept-between-requests.patch"
INNER = "backend/cpp/llama-cpp/patches/0019-mtmd-omnivoice-nothing-kept-between-requests.patch"
NOTE = (ROOT / "docs" / "notes" / "omnivoice-on-our-backend.md").read_text(encoding="utf-8")


def _added(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _removed(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")]


def _inner() -> str:
    outer = PATCH.read_text(encoding="utf-8")
    return "\n".join(_added(outer[outer.index("\n---\n"):]))


def test_it_is_a_mail_under_localai_s_policy_and_adds_one_engine_patch():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == [INNER]
    inner = _inner()
    assert set(re.findall(r"^diff --git a/(\S+)", inner, re.M)) == {"tools/mtmd/mtmd-helper-gen.cpp"}


def test_the_samples_are_no_longer_kept_anywhere():
    inner = _inner()
    added, removed = "\n".join(_added(inner)), "\n".join(_removed(inner))
    # the member that held the last clip's samples is gone, with every use of it
    assert "std::vector<float> kept_clip, kept_rows;" in removed
    assert "kept_clip" not in added
    # the clip is known by its digest from the moment it arrives, and zeroed on every way out
    assert "const std::array<uint8_t, 32> digest = hash_sha256(clip.data(), clip.size() * sizeof(float));" in added
    assert added.count("wipe(clip);") == 2, "when the rows are in hand, and when the encoding fails"
    assert re.search(r"static void wipe\(std::vector<T> & v\) \{\n\s+volatile unsigned char \* p", added), \
        "zeroed through a volatile pointer: a plain fill before a free is the compiler's to drop"


def test_a_clip_with_a_file_leaves_nothing_between_requests_and_one_without_is_kept_by_digest():
    added = "\n".join(_added(_inner()))
    assert "const bool own_file = inp->speaker_ref_cache && inp->speaker_ref_cache[0];" in added
    # memory is used only for a clip with no file, and by the digest
    assert "if (!own_file && kept && kept_digest == digest) {" in added
    assert re.search(r"wipe\(kept_rows\);\n\s+kept = false;\n\s+if \(!own_file\) \{", added), \
        "what was kept goes at every clip; it is kept again only without a file"
    # the request's own rows go with the request, and at the next one's reset
    assert "wipe(voice_rows); // the voice was the request's" in added
    assert re.search(r"t_us = \{\};\n-\s+voice_rows\.clear\(\);\n\+\s+wipe\(voice_rows\);", _inner()), \
        "reset() zeroes, where it only cleared"
    # the file's code takes the digest: no samples travel to it, and its own copies of the rows are zeroed
    assert "bool clip_cache_read(const char * path, const std::array<uint8_t, 32> & digest," in added
    assert "void clip_cache_write(const char * path, const std::array<uint8_t, 32> & digest," in added
    assert added.count("wipe(buf);") == 3
    assert "clip_cache_write(inp->speaker_ref_cache, digest, voice_rows, voice_rms);" in added


def test_the_file_format_does_not_move_and_the_log_names_no_voice():
    inner = _inner()
    assert "OVCLIP2" not in "\n".join(_added(inner) + _removed(inner)), "an encoding written by the live engine is still read"
    added = "\n".join(_added(inner))
    # nothing of a clip is logged but eight bytes of its digest
    assert added.count('snprintf(hex + 2 * i, 3, "%02x", digest[i]);') == 2
    assert "hash_sha256_hex(clip.data()" not in added


def test_the_candidate_carries_it_and_the_note_says_what_holds_now():
    series = (STAGED / "engine-refusal.series").read_text(encoding="utf-8")
    assert series.rstrip().endswith("staged/" + PATCH.name)
    assert "THE PORT BECOMES THE LIVE VOICE ONLY ON THIS ENGINE" in series
    assert PATCH.name not in (STAGED / "engine-next.series").read_text(encoding="utf-8")
    section = NOTE[NOTE.index("**What the engine keeps of a saved voice between requests: nothing"):]
    for said in ("it kept the last\nclip's SAMPLES and its rows in the backend's memory", "the condition holds at three layers",
                 "never its samples", "sha afa8db3d2b59e621", 'none "kept"'):
        assert said in section, said
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert f"`{PATCH.name}`" in readme and "engine `d531d5d804f1`" in readme
