"""tools/measure/omnivoice_port_gate.py: the step-by-step comparison of the
llama.cpp port of OmniVoice with omnivoice.cpp (docs/notes/omnivoice-on-our-backend.md §7.4)."""
import struct
import wave

import numpy as np

from tools.measure import omnivoice_port_gate as gate


def _dump(path, values) -> None:
    a = np.asarray(values, dtype="<f4")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack(f"<i{a.ndim}i", a.ndim, *a.shape) + a.tobytes())


def _wav(path, samples) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def test_a_dump_is_read_with_its_shape_and_a_short_one_is_refused(tmp_path):
    _dump(tmp_path / "a.bin", np.arange(6).reshape(2, 3))
    assert gate.load(tmp_path / "a.bin").shape == (2, 3)
    (tmp_path / "short.bin").write_bytes(struct.pack("<iii", 2, 2, 3) + b"\0" * 8)
    try:
        gate.load(tmp_path / "short.bin")
    except ValueError as e:
        assert "values for shape" in str(e)
    else:
        raise AssertionError("a truncated dump was accepted")


def test_the_port_passes_a_step_within_the_references_own_noise_and_fails_beyond_it(tmp_path):
    rng = np.random.default_rng(0)
    ref = rng.normal(size=(2, 4, 8)) * 80
    for row in ("cond", "uncond"):
        _dump(tmp_path / "forced" / f"forced-logits-{row}.bin", ref)
        _dump(tmp_path / "noise" / f"forced-logits-{row}.bin", ref + rng.normal(size=ref.shape) * 0.05)
        _dump(tmp_path / "port" / f"step-16-logits-{row}.bin", ref + rng.normal(size=ref.shape) * 0.05)
        _dump(tmp_path / "bad" / f"step-16-logits-{row}.bin", ref + rng.normal(size=ref.shape) * 0.5)
    good = gate.compare_step(16, tmp_path / "port", tmp_path / "forced", tmp_path / "noise")
    assert [s.row for s in good] == ["cond", "uncond"] and all(s.ok for s in good)
    assert all(s.port.cosine > 0.99999 for s in good)
    assert not any(s.ok for s in gate.compare_step(16, tmp_path / "bad", tmp_path / "forced", tmp_path / "noise"))


def test_a_row_the_neutral_change_leaves_untouched_is_not_a_noise_measure(tmp_path):
    ref = np.ones((1, 2, 4))
    for row in ("cond", "uncond"):
        _dump(tmp_path / "forced" / f"forced-logits-{row}.bin", ref)
        _dump(tmp_path / "noise" / f"forced-logits-{row}.bin", ref)
        _dump(tmp_path / "port" / f"step-00-logits-{row}.bin", ref + 0.01)
    assert all(s.ok for s in gate.compare_step(0, tmp_path / "port", tmp_path / "forced", tmp_path / "noise"))


def test_the_scoring_counts_every_code_that_differs_after_a_step(tmp_path):
    for step, codes in enumerate(([1024, 5, 1024, 7], [3, 5, 9, 7])):
        _dump(tmp_path / "port" / f"step-{step:02d}-codes-after.bin", np.asarray(codes).reshape(2, 2))
        _dump(tmp_path / "same" / f"step-{step:02d}-codes-after.bin", np.asarray(codes).reshape(2, 2))
        _dump(tmp_path / "other" / f"step-{step:02d}-codes-after.bin", np.asarray([3, 5, 9, 8]).reshape(2, 2))
    assert gate.scoring_differences(tmp_path / "same", tmp_path / "port") == (2, 0)
    assert gate.scoring_differences(tmp_path / "other", tmp_path / "port") == (2, 4)


def test_the_decoders_must_agree_before_the_tail_of_a_chunk_only(tmp_path):
    a = np.linspace(-0.5, 0.5, 6000)
    tail = a.copy()
    tail[-100:] += 1e-3
    body = a.copy()
    body[100] += 1e-3
    _dump(tmp_path / "ref.bin", a)
    _dump(tmp_path / "tail.bin", tail)
    _dump(tmp_path / "body.bin", body)
    assert gate.compare_decoders(tmp_path / "ref.bin", tmp_path / "tail.bin")["ok"]
    assert not gate.compare_decoders(tmp_path / "ref.bin", tmp_path / "body.bin")["ok"]
    _dump(tmp_path / "short.bin", a[:-960])
    assert not gate.compare_decoders(tmp_path / "ref.bin", tmp_path / "short.bin")["ok"]


def test_rounding_against_truncation_is_one_unit_and_more_than_two_fails(tmp_path):
    _wav(tmp_path / "ref.wav", [0, 100, -100, 3000])
    _wav(tmp_path / "port.wav", [0, 101, -100, 3001])
    _wav(tmp_path / "far.wav", [0, 104, -100, 3000])
    near = gate.compare_wav(tmp_path / "ref.wav", tmp_path / "port.wav")
    assert near["ok"] and near["identical"] == 0.5 and near["one_unit"] == 0.5 and near["more"] == 0
    assert not gate.compare_wav(tmp_path / "ref.wav", tmp_path / "far.wav")["ok"]
    assert gate.same_file(tmp_path / "ref.wav", tmp_path / "ref.wav")
    assert not gate.same_file(tmp_path / "ref.wav", tmp_path / "port.wav")


def test_the_verdict_fails_on_one_differing_code(tmp_path, capsys):
    port, replay = tmp_path / "port", tmp_path / "replay"
    _dump(port / "step-00-codes-after.bin", [[1, 2]])
    _dump(replay / "step-00-codes-after.bin", [[1, 2]])
    assert gate.main(["--port", str(port), "--replay", str(replay)]) == 0
    _dump(replay / "step-00-codes-after.bin", [[1, 3]])
    assert gate.main(["--port", str(port), "--replay", str(replay)]) == 1
    assert capsys.readouterr().out.rstrip().endswith("FAIL")
