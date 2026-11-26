#!/usr/bin/env python3
"""How a spoken clip ENDS: its last 300 ms, read the way the speech engine's own
trim reads it (docs/notes/omnivoice-on-our-backend.md §15). Standard library only.

    python3 -I tools/measure/speech_tail.py CLIP.wav [CLIP.wav ...]
    python3 -I tools/measure/speech_tail.py --raw audio-joined.bin [--raw ...]
    python3 -I tools/measure/speech_tail.py CLIP.wav --raw audio-joined.bin     # the same sentence, both

A CLIP is a WAV as the engine answers it and the node keeps it (16-bit mono):
the pad is the run of zero samples at its end (with the end of a fade that
rounds to zero in 16 bits: so where the fade ENDS); before it, in 10 ms chunks
counted back from the pad as the engine's trim counts them, each chunk's level
in dBFS; `gap` is how much of the end before the pad is under -50 dBFS (the
trim's threshold) - the silence the fade-out may take. A gap of 0 ms with sound
at -20...-35 dBFS right to the pad is the cut word of 2026-11-26.

A RAW file is `audio-joined.bin`, the audio as the model gave it before any
trim, fade or pad (written by the engine with MTMD_DEBUG_OMNIVOICE=<dir>; int32
n_dims, int32 shape, float32 samples): whether the MODEL's own length ends in
sound - the open question (b) - is read there, not in the clip.

Exit 0 always; the numbers are the answer, and what they mean is for the ear.
`--json` prints one object per file instead of lines.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import wave
from pathlib import Path

#: the engine's trim threshold and chunk (vendor/omnivoice/audio-postproc.h: -50 dBFS, 10 ms)
SILENT_DB = -50.0
CHUNK_S = 0.010
TAIL_S = 0.300
#: the speech pipeline's sample rate (OmniVoice: 24 kHz); a raw dump carries no rate of its own
RAW_RATE = 24_000


def level_db(samples: list[float]) -> float:
    """RMS in dBFS of samples in [-1, 1]; -inf for none or digital silence."""
    if not samples:
        return -math.inf
    rms = math.sqrt(sum(x * x for x in samples) / len(samples))
    return 20.0 * math.log10(rms) if rms > 0 else -math.inf


def read_wav(path: Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise SystemExit(f"{path}: not a 16-bit mono WAV ({w.getsampwidth() * 8}-bit, {w.getnchannels()} channels)")
        rate, raw = w.getframerate(), w.readframes(w.getnframes())
    return [s / 32768.0 for s in struct.unpack(f"<{len(raw) // 2}h", raw)], rate


def read_raw(path: Path) -> list[float]:
    data = path.read_bytes()
    (n_dims,) = struct.unpack_from("<i", data, 0)
    shape = struct.unpack_from(f"<{n_dims}i", data, 4)
    n = math.prod(shape)
    offset = 4 + 4 * n_dims
    if len(data) != offset + 4 * n:
        raise SystemExit(f"{path}: {len(data)} bytes, not the {offset + 4 * n} its header says")
    return list(struct.unpack_from(f"<{n}f", data, offset))


def tail(samples: list[float], rate: int, *, padded: bool) -> dict:
    """The end of `samples`: the pad (where `padded`), then the last TAIL_S before it in chunks from the end."""
    end = len(samples)
    pad = 0
    if padded:
        while end > 0 and samples[end - 1] == 0.0:
            end -= 1
        pad = len(samples) - end
    chunk = max(1, int(rate * CHUNK_S))
    chunks = []                                   # from the end backwards, as the trim reads them
    for k in range(int(TAIL_S / CHUNK_S)):
        hi = end - k * chunk
        lo = max(0, hi - chunk)
        if hi <= 0:
            break
        chunks.append(level_db(samples[lo:hi]))
    quiet = 0
    for db in chunks:
        if db >= SILENT_DB:
            break
        quiet += 1
    ms = lambda n: round(n * 1000.0 / rate, 1)
    return {"seconds": round(len(samples) / rate, 3), "pad_ms": ms(pad), "gap_ms": round(quiet * CHUNK_S * 1000),
            "ends_in_sound": bool(chunks) and chunks[0] >= SILENT_DB,
            "last_5ms_db": _round(level_db(samples[max(0, end - int(rate * 0.005)):end])),
            "last_20ms_db": _round(level_db(samples[max(0, end - int(rate * 0.020)):end])),
            "last_100ms_db": _round(level_db(samples[max(0, end - int(rate * 0.100)):end])),
            "chunks_db_from_the_end": [_round(db) for db in chunks]}


def _round(db: float) -> float | None:
    return None if db == -math.inf else round(db, 1)


def _said(name: str, kind: str, t: dict) -> str:
    db = lambda v: "silence" if v is None else f"{v:.1f} dBFS"
    head = (f"{name} ({kind}): {t['seconds']:.3f} s" + (f", pad {t['pad_ms']:.0f} ms" if kind == "clip" else "")
            + f"; before {'the pad' if kind == 'clip' else 'its end'}: {t['gap_ms']} ms under {SILENT_DB:.0f} dBFS, "
            + ("ENDS IN SOUND" if t["ends_in_sound"] else "ends in silence")
            + f"; the last 5 ms {db(t['last_5ms_db'])}, 20 ms {db(t['last_20ms_db'])}, 100 ms {db(t['last_100ms_db'])}")
    row = " ".join("  ." if v is None else f"{v:4.0f}" for v in t["chunks_db_from_the_end"])
    return head + f"\n  10 ms chunks from the end back, dBFS: {row}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("clips", nargs="*", type=Path, help="WAVs as the engine answers them")
    ap.add_argument("--raw", action="append", type=Path, default=[], help="audio-joined.bin, the model's own audio")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.clips and not a.raw:
        ap.error("a clip or --raw")
    out = []
    for path in a.clips:
        samples, rate = read_wav(path)
        out.append((str(path), "clip", tail(samples, rate, padded=True)))
    for path in a.raw:
        out.append((str(path), "raw", tail(read_raw(path), RAW_RATE, padded=False)))
    for name, kind, t in out:
        print(json.dumps({"file": name, "kind": kind, **t}) if a.json else _said(name, kind, t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
