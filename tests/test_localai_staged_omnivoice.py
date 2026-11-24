"""The staged OmniVoice patches carry what the card hand-off reads, and
nothing that was only a measurement.

docs/notes/omnivoice-on-our-backend.md §10–§11. München's run on the B70
reads two things from the patched engine: the step traces
(`MTMD_DEBUG_OMNIVOICE=<dir>`, the gate's input) and the line each request
logs saying where its time went — on a card the proportions are not the
CPU's, and that line is how they are learned. Two switches timed the
changes here and were removed: a patch that shipped one would decode
differently under an env nobody documents. And the note says which changes
were kept and which was taken out for want of a measured gain; the patch
must say the same.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
OMNIVOICE = STAGED / "0009-llama-cpp-omnivoice.patch"
VOICE_MODES = STAGED / "0010-llama-cpp-tts-voice-modes.patch"
NOTE = (ROOT / "docs" / "notes" / "omnivoice-on-our-backend.md").read_text(encoding="utf-8")
GATE_TOOL = (ROOT / "tools" / "measure" / "omnivoice_port_gate.py").read_text(encoding="utf-8")
HOOKS = (ROOT / "tools" / "measure" / "omnivoice_reference_hooks.patch").read_text(encoding="utf-8")


def _added(patch: str) -> list[str]:
    """The lines a patch adds, without their `+`."""
    return [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _inner() -> str:
    """The llama.cpp patch the LocalAI patch adds as a file."""
    outer = OMNIVOICE.read_text(encoding="utf-8")
    assert "backend/cpp/llama-cpp/patches/0012-mtmd-omnivoice.patch" in outer
    return "\n".join(_added(outer[outer.index("\n---\n"):]))


def _pipeline() -> str:
    """What the llama.cpp patch adds, as one text."""
    return "\n".join(_added(_inner()))


def test_the_staged_patches_are_mails_under_localai_s_policy():
    """As the series' patches (tests/test_localai_patches.py): `git am`
    applies them, no agent signs off, the assistance is said."""
    for path in (OMNIVOICE, VOICE_MODES):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("From 0000000000000000000000000000000000000000 "), path.name
        assert re.search(r"^Subject: \[PATCH\] \S", text, re.M), path.name
        message = text[:text.index("\n---\n")]
        assert not re.search(r"^Signed-off-by:", message, re.M | re.I), path.name
        assert not re.search(r"^Co-Authored-By:", message, re.M | re.I), path.name
        assert re.search(r"^Assisted-by: ", message, re.M), path.name


def test_a_request_logs_where_its_time_went():
    added = _pipeline()
    parts = ("voice clip", "backbone", "audio heads", "scoring", "decoder")
    for part in parts:
        assert f"{part} %.2f s" in added, f"the timing line names the {part}"
    assert "omnivoice: %zu chunk(s), %d steps: " in added
    quoted = re.search(r"`omnivoice: 1\s+chunk\(s\), 32 steps: ([^`]+)`", NOTE)
    assert quoted, "the note quotes the line the card run reads"
    assert [p for p in parts if p in quoted.group(1)] == list(parts), "and in the patch's order"


def test_the_only_switch_the_pipeline_reads_is_the_trace():
    added = _pipeline()
    switches = set(re.findall(r'getenv\("([A-Z0-9_]*OMNIVOICE[A-Z0-9_]*)"\)', added))
    assert switches == {"MTMD_DEBUG_OMNIVOICE"}, "a measurement switch left in the patch"
    assert "MTMD_OMNIVOICE_" not in OMNIVOICE.read_text(encoding="utf-8")
    assert "MTMD_DEBUG_OMNIVOICE=<dir>" in NOTE and "MTMD_DEBUG_OMNIVOICE" in GATE_TOOL


def test_the_patch_holds_the_changes_the_note_says_were_kept():
    added = _pipeline()
    section = NOTE[NOTE.index("## 11. "):]
    kept = [row for row in section.splitlines() if row.startswith("| ") and row.rstrip().endswith("| yes |")]
    assert len(kept) == 3, "three changes kept (§11)"
    assert "kept_clip == clip" in added, "the last clip's frames are reused only for the same samples"
    assert "kept_clip.clear()" in added, "a clip that failed to encode is not kept"
    assert re.search(r"each_part\(n_slots, ", added) and "std::thread" in added, "the scoring uses the threads"
    assert re.search(r"if \(codes\[i\] != mask_id\) \{\s+// decoded already", added), "a decoded slot is skipped"
    # the one taken out: every row's state is still asked for
    assert "**no, taken out**" in section
    assert "llama_batch_ext_set_output_embd(batch.get(), idx, true);" in added


def test_the_reference_s_hooks_are_the_ones_the_gate_tool_names():
    named = set(re.findall(r"`(OV_[A-Z_]+)[=`]", GATE_TOOL))
    assert {"OV_HARD_MASK", "OV_FORCE_TOKENS", "OV_REPLAY_LOGITS", "OV_THREADS"} <= named
    for hook in sorted(named):
        assert f'getenv("{hook}")' in HOOKS, f"{hook} is in tools/measure/omnivoice_reference_hooks.patch"
