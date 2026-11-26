"""tools/measure/speech_tail.py: how a spoken clip ends, read as the engine's
trim reads it (docs/notes/omnivoice-on-our-backend.md §15). Synthetic clips
whose ends are known: a word cut at the pad, one with its own silence, and the
model's raw audio as the engine dumps it."""
import json
import math
import struct
import wave

import pytest

from tools.measure import speech_tail as tool

SR = 24_000


def _tone(seconds: float, amp: float = 0.1) -> list[float]:
    return [amp * math.sin(2 * math.pi * 220 * i / SR) for i in range(int(seconds * SR))]


def _wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(struct.pack(f"<{len(samples)}h", *(int(round(max(-1, min(1, x)) * 32767)) for x in samples)))
    return path


def _raw(path, samples):
    path.write_bytes(struct.pack("<ii", 1, len(samples)) + struct.pack(f"<{len(samples)}f", *samples))
    return path


def test_a_word_cut_at_the_pad_ends_in_sound_with_no_gap(tmp_path):
    clip = _wav(tmp_path / "cut.wav", _tone(1.0) + [0.0] * (SR // 10))
    got = tool.tail(*tool.read_wav(clip), padded=True)
    assert got["pad_ms"] == 100 and got["gap_ms"] == 0 and got["ends_in_sound"]
    assert -24 < got["last_5ms_db"] < -22, "a 0.1 sine is -23 dBFS"
    assert len(got["chunks_db_from_the_end"]) == 30


def test_a_clip_with_its_own_silence_says_how_much(tmp_path):
    clip = _wav(tmp_path / "kept.wav", _tone(1.0) + [0.0001] * (SR * 60 // 1000) + [0.0] * (SR // 10))
    got = tool.tail(*tool.read_wav(clip), padded=True)
    assert got["gap_ms"] == 60 and not got["ends_in_sound"] and got["pad_ms"] == 100
    assert got["chunks_db_from_the_end"][6] > -50 > got["chunks_db_from_the_end"][5]


def test_the_raw_audio_has_no_pad_and_is_read_as_the_engine_dumps_it(tmp_path):
    raw = _raw(tmp_path / "audio-joined.bin", _tone(0.5) + [0.0] * (SR * 30 // 1000))
    got = tool.tail(tool.read_raw(raw), tool.RAW_RATE, padded=False)
    assert got["pad_ms"] == 0 and got["gap_ms"] == 30 and not got["ends_in_sound"]
    with pytest.raises(SystemExit):
        (tmp_path / "cut.bin").write_bytes(raw.read_bytes()[:-8])
        tool.read_raw(tmp_path / "cut.bin")


def test_the_command_line_prints_lines_or_json(tmp_path, capsys):
    clip = _wav(tmp_path / "c.wav", _tone(0.4) + [0.0] * (SR // 10))
    raw = _raw(tmp_path / "r.bin", _tone(0.4))
    assert tool.main([str(clip), "--raw", str(raw)]) == 0
    out = capsys.readouterr().out
    assert "c.wav (clip): 0.500 s, pad 100 ms; before the pad: 0 ms under -50 dBFS, ENDS IN SOUND" in out
    assert "r.bin (raw): 0.400 s; before its end: 0 ms under -50 dBFS, ENDS IN SOUND" in out
    assert tool.main([str(clip), "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["kind"] == "clip" and got["gap_ms"] == 0
    with pytest.raises(SystemExit):
        tool.main([])


def test_the_threshold_and_the_chunk_are_the_engine_s():
    port = (tool.Path(__file__).resolve().parents[1] / "deploy" / "localai" / "patches" / "staged"
            / "0009-llama-cpp-omnivoice.patch").read_text(encoding="utf-8")
    assert "ov::remove_silence(audio, info.sample_rate, 500, 100, 100, -50.0);" in port
    assert "int chunk_n = sr / 100;  // 10 ms" in port
    assert tool.SILENT_DB == -50.0 and tool.CHUNK_S == 0.010
