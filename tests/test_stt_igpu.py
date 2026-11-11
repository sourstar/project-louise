"""tools/stt_igpu.py: the STT model's YAMLs for the processor's Radeon and
the measurement (docs/notes/stt-on-igpu.md). The speech processing is not
changed: the whole clip, at the end of the speech."""
from __future__ import annotations

import io
import json
import wave

import pytest

from tools import stt_igpu as si

LIVE = """# parakeet, from LocalAI's gallery
name: parakeet-tdt-0.6b-v3
backend: parakeet-cpp
parameters:
  model: parakeet-tdt-0.6b-v3.gguf
env:
  OMP_NUM_THREADS: "6"
"""


def _wav(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(si.RATE)
        w.writeframes(b"\0\0" * int(seconds * si.RATE))
    return buf.getvalue()


def test_the_live_model_is_pinned_to_the_cpu_build_and_its_twin_to_the_radeon(tmp_path):
    """§4 step 3.1 and 3.4: the alias `parakeet-cpp` names the concrete CPU
    build before a second build is installed; the twin keeps the weights and
    the env, and adds the device pin."""
    a, b = si.write_yamls(si.load_model(LIVE), tmp_path, "vulkan-parakeet-cpp",
                          {"GGML_VK_VISIBLE_DEVICES": "1"})
    cpu, gpu = si.load_model(a.read_text()), si.load_model(b.read_text())
    assert a.name == "parakeet-tdt-0.6b-v3.yaml" and b.name == "parakeet-tdt-0.6b-v3-igpu.yaml"
    assert cpu["backend"] == "cpu-parakeet-cpp" and cpu["parameters"] == {"model": "parakeet-tdt-0.6b-v3.gguf"}
    assert gpu == {"name": "parakeet-tdt-0.6b-v3-igpu", "backend": "vulkan-parakeet-cpp",
                   "parameters": {"model": "parakeet-tdt-0.6b-v3.gguf"},
                   "env": {"GGML_VK_VISIBLE_DEVICES": "1", "OMP_NUM_THREADS": "6"}}
    assert "louise node speech set --stt parakeet-tdt-0.6b-v3" in b.read_text(), "the way back"
    assert json.loads("".join(l for l in b.read_text().splitlines(True) if not l.startswith("#"))) == gpu


def test_a_device_not_pinned_is_refused(tmp_path):
    """The bundle carries ANV (the B70) and lavapipe (the CPU) beside RADV."""
    with pytest.raises(SystemExit, match="not pinned"):
        si.write_yamls(si.load_model(LIVE), tmp_path, "vulkan-parakeet-cpp", {})
    assert not list(tmp_path.iterdir()), "nothing written"


def test_rocm_runs_gfx1036_as_gfx1030():
    got = si.igpu_twin(si.load_model(LIVE), "rocm-parakeet-cpp", {"HIP_VISIBLE_DEVICES": "0"})
    assert got["env"]["HSA_OVERRIDE_GFX_VERSION"] == "10.3.0"
    with pytest.raises(SystemExit, match="--backend"):
        si.igpu_twin(si.load_model(LIVE), "sycl-parakeet-cpp", {"PARAKEET_DEVICE": "Vulkan1"})


def test_only_a_parakeet_cpu_model_is_pinned():
    with pytest.raises(SystemExit, match="not parakeet's CPU build"):
        si.pinned_cpu(si.load_model(LIVE.replace("backend: parakeet-cpp", "backend: whisper")))
    assert si.pinned_cpu(si.load_model(LIVE.replace("parakeet-cpp", "cpu-parakeet-cpp")))["backend"] == \
        "cpu-parakeet-cpp"


def test_bench_times_warm_calls_only_and_labels_the_first(tmp_path, monkeypatch):
    (tmp_path / "clips" / "fr").mkdir(parents=True)
    (tmp_path / "clips" / "q.wav").write_bytes(_wav(2.0))
    (tmp_path / "clips" / "fr" / "q1.wav").write_bytes(_wav(4.0))
    calls: list[tuple[str | None, int]] = []
    took = iter([3.0, 9.0, 0.5, 0.4, 0.6, 9.0, 1.0, 0.8, 1.2])

    def call(server, model, data, lang):
        calls.append((lang, len(data)))
        return f"text {lang}", next(took)
    monkeypatch.setattr(si, "unload", lambda server, model: calls.append(("unload", 0)))
    out = tmp_path / "res" / "cpu.json"
    out.parent.mkdir()
    got = si.bench(tmp_path / "clips", "http://localai:8080", "m", runs=3, first=True, pid=None,
                   pci="0000:99:00.0", out=out, call=call)
    assert calls[0] == ("unload", 0)
    assert got["first_call"]["seconds"] == 3.0
    q, fr = got["clips"]
    assert (q["clip"], q["language"], q["warm_median_s"], q["rtf"]) == ("q.wav", None, 0.5, 0.25), \
        "the untimed warm call (9.0 s) is not in it"
    assert (fr["clip"], fr["language"], fr["warm_median_s"], fr["rtfx"]) == ("fr/q1.wav", "fr", 1.0, 4.0)
    assert got["label"] == "measured" and got["igpu_before"] is None
    assert got["free_bytes_before"] > 0 and got["free_bytes_after"] > 0


def test_a_clip_not_in_the_pages_form_is_refused(tmp_path):
    (tmp_path / "c").mkdir()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\0" * 400)
    (tmp_path / "c" / "x.wav").write_bytes(buf.getvalue())
    with pytest.raises(SystemExit, match="16 kHz mono"):
        si.bench(tmp_path / "c", "u", "m", runs=1, first=False, pid=None, pci="x", out=tmp_path / "o.json",
                 call=lambda *a: ("", 0.1))


def test_diff_compares_word_by_word():
    ref = {"model": "cpu", "clips": [{"clip": "a.wav", "text": "Quelle est la loi ?", "warm_median_s": 0.6},
                                     {"clip": "b.wav", "text": "same words", "warm_median_s": 0.4}]}
    other = {"model": "igpu", "clips": [{"clip": "a.wav", "text": "Quelle et la loi ?", "warm_median_s": 1.2},
                                        {"clip": "b.wav", "text": "same words", "warm_median_s": 0.8}]}
    got = si.compare(ref, other)
    assert (got["equal"], got["compared"], got["median_speed"]) == (1, 2, 0.5)
    assert got["clips"][0]["differences"] == [{"op": "replace", "ref": "est", "other": "et"}]


def test_the_readings(tmp_path):
    hw = tmp_path / "hwmon"
    (hw / "hwmon0").mkdir(parents=True)
    (hw / "hwmon0" / "name").write_text("amdgpu\n")
    (hw / "hwmon3").mkdir()
    (hw / "hwmon3" / "name").write_text("k10temp\n")
    (hw / "hwmon3" / "temp1_label").write_text("Tctl\n")
    (hw / "hwmon3" / "temp1_input").write_text("71250\n")
    assert si.tctl_path(hw) == hw / "hwmon3" / "temp1_input"
    proc = tmp_path / "proc" / "42"
    proc.mkdir(parents=True)
    tick = __import__("os").sysconf("SC_CLK_TCK")
    fields = ["S"] + ["0"] * 10 + [str(3 * tick), str(tick)] + ["0"] * 30
    (proc / "stat").write_text("42 (parakeet (cpp)) " + " ".join(fields))
    assert si.cpu_seconds(42, tmp_path / "proc") == 4.0
    dev = tmp_path / "pci" / "0000:13:00.0"
    dev.mkdir(parents=True)
    (dev / "mem_info_vram_total").write_text("536870912\n")
    got = si.igpu_memory("0000:13:00.0", tmp_path / "pci")
    assert got["mem_info_vram_total"] == 536870912 and got["mem_info_gtt_used"] is None
