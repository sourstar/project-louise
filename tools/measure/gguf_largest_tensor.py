#!/usr/bin/env python3
"""The largest tensor of each GGUF file, and how many are above a size.

Why: with buffers of 2 GiB at most (the engine's GGML_SYCL_HOST_PINNED_MEM_2G,
the default from staged 0021) a model's weights sit on the card in pieces the
driver can page one by one. A tensor is never split: one larger than 2 GiB
gets a buffer of its own, of its own size, and moves whole. This counts
whether a model has such a tensor instead of inferring it from its shape.

    python3 -I tools/measure/gguf_largest_tensor.py [--over-mib 2048] FILE.gguf [FILE.gguf ...]

Standard library only; reads the header and the tensor table, never the
weights. A tensor's size is the distance to the next tensor's offset (its
bytes plus at most one alignment of padding)."""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

MIB = 1 << 20
_SCALAR = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}


def _take(f, fmt: str):
    size = struct.calcsize("<" + fmt)
    data = f.read(size)
    if len(data) != size:
        raise ValueError("the file ends inside its header")
    return struct.unpack("<" + fmt, data)


def _string(f) -> str:
    (n,) = _take(f, "Q")
    return f.read(n).decode("utf-8", errors="replace")


def _value(f, kind: int):
    if kind in _SCALAR:
        return _take(f, _SCALAR[kind])[0]
    if kind == 8:
        return _string(f)
    if kind == 9:
        item, count = _take(f, "IQ")
        if item in _SCALAR:                       # skipped in one step: a vocabulary's scores, its token types
            f.seek(count * struct.calcsize("<" + _SCALAR[item]), 1)
            return None
        for _ in range(count):
            _value(f, item)
        return None
    raise ValueError(f"unknown GGUF value type {kind}")


def tensors(path: Path) -> list[tuple[str, tuple[int, ...], int]]:
    """[(name, shape, bytes)] of a GGUF file, in file order."""
    size = path.stat().st_size
    with path.open("rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError("not a GGUF file")
        _version, n_tensors, n_kv = _take(f, "IQQ")
        alignment = 32
        for _ in range(n_kv):
            key = _string(f)
            (kind,) = _take(f, "I")
            value = _value(f, kind)
            if key == "general.alignment" and isinstance(value, int) and value > 0:
                alignment = value
        infos = []
        for _ in range(n_tensors):
            name = _string(f)
            (n_dims,) = _take(f, "I")
            shape = _take(f, f"{n_dims}Q")
            _kind, offset = _take(f, "IQ")
            infos.append((name, shape, offset))
        data = (f.tell() + alignment - 1) // alignment * alignment
    ends = sorted({offset for _, _, offset in infos} | {size - data})
    after = {offset: ends[i + 1] for i, offset in enumerate(ends[:-1])}
    return [(name, shape, after.get(offset, size - data) - offset) for name, shape, offset in infos]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--over-mib", type=int, default=2048, help="count the tensors above this size (default 2048)")
    args = ap.parse_args(argv)
    over = args.over_mib * MIB
    print(f"file | tensors | all, MiB | the largest tensor | its shape | MiB | tensors above {args.over_mib} MiB")
    bad = 0
    for path in args.files:
        try:
            found = tensors(path)
        except (OSError, ValueError, struct.error) as e:
            print(f"{path.name} | not read: {e}")
            bad += 1
            continue
        if not found:
            print(f"{path.name} | 0 | 0 | - | - | - | 0")
            continue
        name, shape, size = max(found, key=lambda t: t[2])
        above = [t for t in found if t[2] > over]
        print(f"{path.name} | {len(found)} | {sum(t[2] for t in found) / MIB:,.0f} | {name} | "
              f"{' x '.join(map(str, shape))} | {size / MIB:,.1f} | {len(above)}"
              + ("".join(f"\n    above: {n} ({' x '.join(map(str, s))}) {b / MIB:,.1f} MiB" for n, s, b in above)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
