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
PROFILE_OPT_IN = STAGED / "0011-config-voice-cloning-opt-in-unknown-backend.patch"
ACTIONS = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
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
    for path in (OMNIVOICE, VOICE_MODES, PROFILE_OPT_IN):
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


def test_a_request_through_localai_can_set_the_seed_the_steps_and_the_guidance():
    """Without them the gate's replay has no seed to share with the reference
    and the 16-step choice of §11 cannot be heard on the node: LocalAI's TTS
    call carried none of the three."""
    added = "\n".join(_added(VOICE_MODES.read_text(encoding="utf-8")))
    for field in ("seed", "flow_steps", "cfg_scale"):
        assert f'params.find("{field}")' in added, f"params[{field!r}] is parsed"
        assert f"task.tts_inp.data.{field} = " in added, f"{field} reaches the pipeline's input"
    assert "task.params.sampling.seed = " in added, "the sampler takes the request's seed too"
    section = NOTE[NOTE.index("## 10. "):]
    assert """`params: {"seed": "0"}`""" in section, "the card gate's request names its seed"
    assert """`params: {"flow_steps": "16"}`""" in section, "and §11 says how 16 steps are asked for"


def test_a_model_on_our_engine_may_be_given_a_voice_profile_by_its_own_word():
    """localai-bugs.md B17, found on the card: LocalAI refused a saved voice
    profile by the backend's NAME before it read the model's
    `tts.voice_cloning`. The server patch honours the field for a backend
    its table does not know — and for nothing else; it touches the server
    only, so it applies with or without the two llama.cpp patches."""
    patch = PROFILE_OPT_IN.read_text(encoding="utf-8")
    touched = set(re.findall(r"^diff --git a/(\S+)", patch, re.M))
    assert touched == {"core/config/backend_capabilities.go", "core/config/backend_capabilities_test.go"}
    added = "\n".join(_added(patch))
    assert re.search(r"if capability == nil \{\s+if cfg\.Backend != \"\" && cfg\.VoiceCloning != nil && "
                     r"\*cfg\.VoiceCloning \{\s+return referenceVoiceCloning\(\)", added)
    for case in ("is refused without the model's word, as before", "stays refused on an explicit false",
                 "does not open a known backend that cannot clone"):
        assert case in added, f"LocalAI's test file holds: {case}"
    # the operator's list of what is owed upstream names the defect and its fix, with the file
    assert "| B17 | defect |" in ACTIONS and "### B17. " in ACTIONS
    row = re.search(r"^\| \*\*P13 \(staged\)\*\* \|.*$", ACTIONS, re.M)
    assert row and PROFILE_OPT_IN.name in row.group(0) and "to file upstream" in row.group(0)
    assert "### P13 (staged). " in ACTIONS


def _backend_files() -> dict[str, str]:
    """{path: 0010's diff for it} (LocalAI's own files, not a llama.cpp patch)."""
    text = VOICE_MODES.read_text(encoding="utf-8")
    parts = re.split(r"^diff --git a/(\S+) b/\S+\n", text[text.index("\n---\n"):], flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def _new_backend_file(diff: str) -> str:
    assert "new file mode" in diff
    body = diff[diff.index("\n@@ "):].split("\n", 2)[2]
    return "\n".join(line[1:] for line in body.splitlines() if line.startswith("+")) + "\n"


def test_a_clip_s_encoding_is_kept_in_one_file_that_holds_no_samples():
    """§13: every load paid the clip's encoding on its first sentence (3.22 s against 2.27 s on the card). The
    port keeps it in ONE file the caller names — the rows and the clip's SHA-256, never the samples (the rows are
    the voice as much as the clip is; a second copy of the recording would be one more thing to delete)."""
    added = _pipeline()
    assert "const char * speaker_ref_cache;" in added, "the input names the file; the pipeline reads no directory"
    assert "clip_cache_read(inp->speaker_ref_cache, clip, voice_rows, voice_rms)" in added
    assert "clip_cache_write(inp->speaker_ref_cache, kept_clip, kept_rows, kept_rms)" in added
    head = added[added.index("struct clip_cache_head {"):]
    head = head[:head.index("};")]
    assert "uint8_t  clip_sha256[32];" in head and "float" in head and "samples" not in head
    write = added[added.index("void clip_cache_write("):]
    write = write[:write.index("\n    }\n") + 1]
    assert write.count("put(") == 4, "the magic, the head, the rows, the checksum: nothing else is written"
    assert "put(rows.data(), rows.size() * sizeof(float));" in write and "put(clip.data()" not in write
    assert "O_WRONLY | O_CREAT | O_EXCL, 0600" in write and "rename(tmp.c_str(), path)" in write
    # what the rows depend on is in the key: the clip, both models, this build of the engine
    assert 'static const char build[] = __DATE__ " " __TIME__;' in added
    assert "hash_sha256(clip.data(), clip.size() * sizeof(float))" in added
    # a file that does not match: one line with the digest, the clip encoded - never a failed request, never a path
    warn = re.findall(r'LOG_WRN\("mtmd_helper_gen_audio: omnivoice: (the (?:kept )?encoding[^"]*)"', added)
    assert len(warn) == 2 and all("%s" in w and "clip %s" in w for w in warn), warn
    assert ".substr(0, 16)" in added
    # the file is never named by a request: llama-server's own routes do not read it
    server = _inner()
    assert "speaker_ref_cache" not in "\n".join(
        line for line in server.splitlines() if "json_value(" in line), "no request body names a file to write"
    section = NOTE[NOTE.index("## 13. "):]
    assert "NEVER THE SAMPLES" in section and "| a new process | **read, 0.01 s** | the same bytes |" in section


def test_under_localai_the_encoding_sits_beside_the_profile_s_clip_and_goes_with_it(tmp_path):
    """The architecture session's condition: a deleted voice's encoding does not outlive it. LocalAI hands a
    profile's clip as a hard link under .leases/ and deletes a profile with RemoveAll of its directory: the backend
    finds the profile's own file by the link's inode and names the file beside it."""
    files = _backend_files()
    main = "\n".join(_added(files["backend/cpp/llama-cpp/grpc-server.cpp"]))
    assert "llama_grpc::set_speaker_ref_cache(task.tts_inp, opts.voice_path);" in main
    assert '#include "tts_voice_cache.h"' in main
    assert "+cp -r tts_voice_cache.h llama.cpp/tools/grpc-server/" in files["backend/cpp/llama-cpp/prepare.sh"]
    header = _new_backend_file(files["backend/cpp/llama-cpp/tts_voice_cache.h"])
    assert 'leases.filename() != ".leases"' in header and "want.st_nlink < 2" in header
    assert "have.st_dev == want.st_dev && have.st_ino == want.st_ino" in header
    section = NOTE[NOTE.index("## 13. "):]
    assert "**A deleted voice's encoding does not outlive it**" in section
    assert "**delete a test profile: its directory is gone, the encoding with it**" in section
    import shutil
    import subprocess
    if shutil.which("g++") is None:
        return
    (tmp_path / "tts_voice_cache.h").write_text(header, encoding="utf-8")
    (tmp_path / "tts_voice_cache_test.cpp").write_text(
        _new_backend_file(files["backend/cpp/llama-cpp/tts_voice_cache_test.cpp"]), encoding="utf-8")
    built = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-pthread", f"-I{tmp_path}",
                            str(tmp_path / "tts_voice_cache_test.cpp"), "-o", str(tmp_path / "t")],
                           capture_output=True, text=True, timeout=180)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr
    for said in ("ok:   a lease leads to a file beside the profile's own clip", "ok:   a lease that is a copy gets no file",
                 "ok:   a deleted profile's lease gets no file", "ok:   an engine that does not is not"):
        assert said in ran.stderr, said


def test_the_longer_clip_is_answered_with_its_cost_not_promised_away():
    section = NOTE[NOTE.index("## 13. "):]
    # the estimate of 2026-11-24 said no; the card said yes, and the note says both
    assert '**The longer clip: I wrote "no" here, and the card says yes' in section
    assert "| reference frames | 249 | 673 |" in section and "5–8 s — WRONG: 2.70 s measured" in section
    table = section[section.index("| choice for Philippe's clip | pros | cons |"):]
    assert table.count("\n| ") >= 4, "the status quo and three alternatives, each with its cons"
    card = section[section.index("### On the card (München, 2026-11-25"):]
    assert "| his ORIGINAL 27.75 s clip | **2.70 s** (backbone 2.20)" in card and "| Philippe's 11 s clip | **2.24 s**" in card
    measured = card[card.index("| Philippe's clip | pros | cons |"):]
    assert measured.count("\n| ") >= 4 and "Philippe\nstays on the 11 s clip if he says nothing" in card
    assert "the first sentence\nof a NEW PROCESS still takes 8.74 s, and the clip is not why" in card
    # the operator's ear, 2026-11-25: the original clip at the model's own steps - and the steps
    # are the node's, so the note says what that means for the synthetic voices
    decided = card[card.index('**DECIDED (the operator, 2026-11-25, his ear on the takes above)'):]
    assert '"Philippe\nvoice 32 steps / Original clip (the longer one)"' in decided
    assert "| 32 | 2.12–2.13 s | 2.53 s |" in card and "2.53 s a sentence on the card" in decided
    assert "one number for every voice" in decided and "A step count per voice does not\n  exist" in decided
    # and what the node holds, as München counted it: the number set, not left to a default
    assert '**On the node (München, 2026-11-25): DONE' in decided and 'is "32", set explicitly' in decided
    assert "27,748 ms, 24 kHz mono,\n1,331,940 bytes" in decided and "the node was never lower" in decided


def test_the_reference_s_hooks_are_the_ones_the_gate_tool_names():
    named = set(re.findall(r"`(OV_[A-Z_]+)[=`]", GATE_TOOL))
    assert {"OV_HARD_MASK", "OV_FORCE_TOKENS", "OV_REPLAY_LOGITS", "OV_THREADS"} <= named
    for hook in sorted(named):
        assert f'getenv("{hook}")' in HOOKS, f"{hook} is in tools/measure/omnivoice_reference_hooks.patch"
