"""tools/measure/gguf_largest_tensor.py: the largest tensor of a GGUF file,
read from its header and tensor table alone (docs/notes/room-on-the-card.md
§3: with buffers of 2 GiB at most a tensor larger than that gets a buffer of
its own and moves whole — counted per model, not inferred from its shape)."""
from __future__ import annotations

import importlib.util
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "measure" / "gguf_largest_tensor.py"


def _tool():
    spec = importlib.util.spec_from_file_location("gguf_largest_tensor", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _s(text: str) -> bytes:
    raw = text.encode()
    return struct.pack("<Q", len(raw)) + raw


def write_gguf(path: Path, sizes: dict[str, int], alignment: int = 32) -> None:
    """A GGUF v3 with a string, a scalar array, an array of strings and the tensors named, their data zeros."""
    kv = [_s("general.name") + struct.pack("<I", 8) + _s("a model"),
          _s("general.alignment") + struct.pack("<II", 4, alignment),
          _s("tokenizer.scores") + struct.pack("<IIQ", 9, 6, 3) + struct.pack("<3f", 0.0, 1.0, 2.0),
          _s("tokenizer.tokens") + struct.pack("<IIQ", 9, 8, 2) + _s("a") + _s("bc")]
    infos, offset = b"", 0
    for name, size in sizes.items():
        infos += _s(name) + struct.pack("<I", 2) + struct.pack("<2Q", size, 1) + struct.pack("<IQ", 24, offset)   # I8
        offset += (size + alignment - 1) // alignment * alignment
    head = b"GGUF" + struct.pack("<IQQ", 3, len(sizes), len(kv)) + b"".join(kv) + infos
    head += b"\0" * (-len(head) % alignment)
    path.write_bytes(head + b"\0" * offset)


def test_it_reads_the_table_and_names_the_largest_tensor(tmp_path):
    tool = _tool()
    path = tmp_path / "m.gguf"
    write_gguf(path, {"token_embd.weight": 4096, "blk.0.attn_q.weight": 640, "output.weight": 1000})
    found = tool.tensors(path)
    assert [name for name, _, _ in found] == ["token_embd.weight", "blk.0.attn_q.weight", "output.weight"]
    assert [size for _, _, size in found] == [4096, 640, 1024], "its bytes, plus at most one alignment of padding"
    assert found[0][1] == (4096, 1)
    # another alignment is the file's own to say
    write_gguf(path, {"a": 100, "b": 7}, alignment=64)
    assert [size for _, _, size in tool.tensors(path)] == [128, 64]


def test_it_counts_the_tensors_above_a_size_and_runs_with_the_standard_library_alone(tmp_path):
    path = tmp_path / "m.gguf"
    write_gguf(path, {"small": 1 << 20, "large": 3 << 20})
    other = tmp_path / "not-a-model.bin"
    other.write_bytes(b"something else")
    done = subprocess.run([sys.executable, "-I", str(TOOL), "--over-mib", "2", str(path), str(other)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 1, "a file that is not read is said, and the run says so"
    lines = done.stdout.splitlines()
    assert lines[1].startswith("m.gguf | 2 | 4 | large | ") and lines[1].endswith("| 3.0 | 1")
    assert "above: large" in lines[2] and "not read: not a GGUF file" in lines[3]
    done = subprocess.run([sys.executable, "-I", str(TOOL), str(path)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.splitlines()[1].endswith("| 0"), "2 GiB is the default"
