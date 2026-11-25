#!/usr/bin/env python3
"""The gate of the OmniVoice port (docs/notes/omnivoice-on-our-backend.md §7.4).

OmniVoice's decoding is chaotic under rounding: no two implementations give
the same codes, so the llama.cpp port is compared with omnivoice.cpp step by
step, on the same input each time:

  prompt    the token ids of the conditional sequence, identical
  steps     the logits of both on the SAME codes, against the reference's own
            rounding noise at that step (the same forward with +1 on every
            allowed attention position)
  scoring   omnivoice.cpp's own scoring code fed the port's logits must give
            the port's codes after each of the 32 steps
  decoder   the port's codes through both decoders: the samples equal but for
            the tail of a chunk, where omnivoice.cpp pads two transposed
            convolutions with a zero and the port computes them
  itself    two runs of the port, byte-identical

The port's side is its trace (`MTMD_DEBUG_OMNIVOICE=<dir> llama-tts ...`);
the reference's side is omnivoice.cpp with
tools/measure/omnivoice_reference_hooks.patch (`OV_HARD_MASK`,
`OV_FORCE_TOKENS`, `OV_REPLAY_LOGITS`), built on the engine's ggml. Both dump
`int32 n_dims, int32 shape[n_dims], float32 data`. `OV_THREADS=<n>` in the
same patch sets the reference's thread count (it takes half the machine's
by default; the port takes `-t`): a speed comparison sets both.

    python3 -I tools/measure/omnivoice_port_gate.py --port TRACE \\
        --reference DUMP --forced 16=DIR --noise 16=DIR ... --replay DIR \\
        --reference-audio DUMP/output-audio.bin --wav REF.wav PORT.wav \\
        --again PORT2.wav
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

MASK_ID = 1024
# the port may differ from the reference by this many times the reference's own rounding noise
NOISE_FACTOR = 1.5
# samples: the two decoders agree to rounding before the tail of a chunk (measured 1.25e-6)
BODY_MAX = 1e-5
TAIL = 2000
# the same tail in the file: after it come the fade and 0.1 s of padding
WAV_TAIL = 5000


def load(path: Path | str) -> np.ndarray:
    raw = Path(path).read_bytes()
    (n_dims,) = struct.unpack_from("<i", raw, 0)
    shape = struct.unpack_from(f"<{n_dims}i", raw, 4)
    data = np.frombuffer(raw, dtype="<f4", offset=4 + 4 * n_dims)
    if data.size != int(np.prod(shape)):
        raise ValueError(f"{path}: {data.size} values for shape {shape}")
    return data.reshape(shape)


@dataclass
class Closeness:
    cosine: float
    max_abs: float
    rms: float


def closeness(a: np.ndarray, b: np.ndarray) -> Closeness:
    if a.shape != b.shape:
        raise ValueError(f"shapes differ: {a.shape} and {b.shape}")
    x, y = a.astype(np.float64).ravel(), b.astype(np.float64).ravel()
    d = x - y
    norm = float(np.linalg.norm(x) * np.linalg.norm(y))
    return Closeness(cosine=float(x @ y) / norm if norm else 1.0,
                     max_abs=float(np.abs(d).max()), rms=float(np.sqrt((d * d).mean())))


def same_prompt(reference: Path, port: Path) -> bool:
    a, b = load(reference / "prompt-cond-ids.bin"), load(port / "prompt-cond-ids.bin")
    return a.shape == b.shape and bool((a == b).all())


@dataclass
class Step:
    step: int
    row: str
    port: Closeness          # the port against the reference, the same codes
    noise: Closeness | None  # the reference against itself
    ok: bool


def compare_step(step: int, port: Path, forced: Path, noise: Path | None, forced_name: str = "forced-logits-{}") -> list[Step]:
    """The logits of both programs on the port's codes before `step`."""
    out = []
    for row in ("cond", "uncond"):
        ref = load(forced / f"{forced_name.format(row)}.bin")
        got = closeness(ref, load(port / f"step-{step:02d}-logits-{row}.bin"))
        own = closeness(ref, load(noise / f"forced-logits-{row}.bin")) if noise else None
        # a row the neutral change leaves untouched (step 0, unconditional) says nothing about noise
        ok = own is None or own.rms == 0.0 or got.rms <= NOISE_FACTOR * own.rms
        out.append(Step(step, row, got, own, ok))
    return out


def scoring_differences(replay: Path, port: Path) -> tuple[int, int]:
    """(steps compared, codes differing): the reference's scoring on the port's logits against the port's codes."""
    steps = sorted(port.glob("step-*-codes-after.bin"))
    differing = 0
    for p in steps:
        differing += int((load(replay / p.name) != load(p)).sum())
    return len(steps), differing


def compare_decoders(reference_audio: Path, port_audio: Path) -> dict:
    a, b = load(reference_audio).astype(np.float64), load(port_audio).astype(np.float64)
    if a.shape != b.shape:
        return {"ok": False, "samples": [int(a.size), int(b.size)]}
    d = np.abs(a - b)
    body, tail = d[:-TAIL], d[-TAIL:]
    return {"ok": bool(body.max() <= BODY_MAX), "samples": int(a.size), "body_max": float(body.max()),
            "tail_max": float(tail.max()), "tail_samples": TAIL}


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.int64)


def compare_wav(reference: Path, port: Path) -> dict:
    """16-bit files of the same codes: the port rounds where omnivoice.cpp truncates, so half the samples differ by 1.
    The end of the file is the tail of the last chunk and the padding after it: reported, not judged."""
    a, b = read_wav(reference), read_wav(port)
    if a.size != b.size:
        return {"ok": False, "samples": [int(a.size), int(b.size)]}
    d = np.abs(a - b)
    body, tail = d[:-WAV_TAIL], d[-WAV_TAIL:]
    return {"ok": bool(body.size == 0 or body.max() <= 2), "samples": int(a.size), "identical": float((d == 0).mean()),
            "one_unit": float((d == 1).mean()), "body_more": int((body > 1).sum()),
            "body_max": int(body.max()) if body.size else 0, "tail_max": int(tail.max()), "tail_samples": WAV_TAIL}


def same_file(a: Path, b: Path) -> bool:
    return hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest()


def _pairs(values: list[str]) -> dict[int, Path]:
    out = {}
    for v in values:
        step, _, path = v.partition("=")
        out[int(step)] = Path(path)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=Path, required=True, help="the port's trace directory")
    ap.add_argument("--reference", type=Path, help="omnivoice.cpp --dump directory of the same text (mask blocked)")
    ap.add_argument("--forced", action="append", default=[], metavar="STEP=DIR", help="OV_FORCE_OUT of the port's codes before STEP")
    ap.add_argument("--noise", action="append", default=[], metavar="STEP=DIR", help="the same with OV_HARD_MASK=shift")
    ap.add_argument("--replay", type=Path, help="OV_REPLAY_OUT of the port's logits")
    ap.add_argument("--reference-audio", type=Path, help="output-audio.bin of the reference decoder on the port's codes")
    ap.add_argument("--wav", type=Path, nargs=2, metavar=("REFERENCE", "PORT"))
    ap.add_argument("--again", type=Path, help="the WAV of a second run of the port")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report: dict = {}
    ok = True
    if args.reference:
        report["prompt_identical"] = same_prompt(args.reference, args.port)
        ok &= report["prompt_identical"]
    forced, noise = _pairs(args.forced), _pairs(args.noise)
    steps = [s for step in sorted(forced) for s in compare_step(step, args.port, forced[step], noise.get(step))]
    report["steps"] = [asdict(s) for s in steps]
    ok &= all(s.ok for s in steps)
    if args.replay:
        n, differing = scoring_differences(args.replay, args.port)
        report["scoring"] = {"steps": n, "codes_differing": differing}
        ok &= n > 0 and differing == 0
    if args.reference_audio:
        report["decoder"] = compare_decoders(args.reference_audio, args.port / "output-audio.bin")
        ok &= report["decoder"]["ok"]
    if args.wav:
        report["wav"] = compare_wav(*args.wav)
        ok &= report["wav"]["ok"]
        if args.again:
            report["reproduces"] = same_file(args.wav[1], args.again)
            ok &= report["reproduces"]
    report["pass"] = bool(ok)

    if args.json:
        print(json.dumps(report, indent=1))
        return 0 if ok else 1
    if "prompt_identical" in report:
        print("prompt ids identical:", report["prompt_identical"])
    if steps:
        print("step row     port vs reference: cosine max|d| rms    reference vs itself: cosine max|d| rms")
        for s in steps:
            own = f"{s.noise.cosine:.9f} {s.noise.max_abs:.4f} {s.noise.rms:.4f}" if s.noise else "not measured"
            print(f"{s.step:4d} {s.row:6s}  {s.port.cosine:.9f} {s.port.max_abs:.4f} {s.port.rms:.4f}    {own}"
                  f"{'' if s.ok else '   FAIL'}")
    for key in ("scoring", "decoder", "wav", "reproduces"):
        if key in report:
            print(f"{key}: {report[key]}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
