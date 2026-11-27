"""What precision MedCPT's OpenVINO encoder computes in on the card, and what f32 changes
(München for the operator, 2026-11-27, on OpenVINO's GPU default: "HALF FUCKING
PRECISION AS A DEFAULT!!!"; docs/notes/encoder-precision.md).

The live backend (louise-python) compiles MedCPT's IR with no precision hint
(`deploy/medcpt-rerank/server.py` `_compile`: CACHE_DIR only), so the plugin's own
default applies - on the GPU plugin, f16. This tool reads back, for each ARM, the
precision the compiled model actually has (`INFERENCE_PRECISION_HINT`,
`EXECUTION_MODE_HINT`), embeds a collection's sample with it, and compares every
arm with the vectors STORED for that sample and with the f32 CPU arm (the IR held
to torch fp32 by convert_article.py):

    ov     GPU as the live backend compiles it, GPU f32, CPU as it compiles by
           default, CPU f32 (the IR's own weights: their element types counted)

MedCPT only. vega-dar-legal-it is not measured here (the operator, 2026-11-27,
through München: "Don't fucking touch dar legal").

Runs INSIDE the backend's own directory, with its own interpreter, so the
libraries and the task code are the live ones (no venv: run.sh's python):

    python -m tools.cross_device_check pubmed --export /tmp/pubmed-sample.json   # on the node, the app
    D=<LocalAI's backends dir>/louise-python
    $D/python/bin/python3 -B -E -s tools/measure/encoder_precision.py --backend-dir $D \\
        ov --model-dir <models>/weights/medcpt/medcpt-article-ov --sample /tmp/pubmed-sample.json \\
        --context 512 --out medcpt-article-precision.json

A diagnostic: nothing is written but its report. Each arm loads the model once
more (the GPU arms on the card: a card at its limit refuses them, which the
report says).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

# an arm: its name, the device and the compile config
OV_ARMS = (("gpu-live", "GPU", {}), ("gpu-f32", "GPU", {"INFERENCE_PRECISION_HINT": "f32"}),
           ("cpu-default", "CPU", {}), ("cpu-f32", "CPU", {"INFERENCE_PRECISION_HINT": "f32"}))
REFERENCE_ARM = "cpu-f32"
OV_PROPERTIES = ("INFERENCE_PRECISION_HINT", "EXECUTION_MODE_HINT")


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else float("nan")


def max_abs_diff(a: Sequence[float], b: Sequence[float]) -> float:
    return max((abs(x - y) for x, y in zip(a, b)), default=0.0)


def unit(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else list(v)


def compare(got: Sequence[Sequence[float]], want: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Row by row: the worst and the median cosine, the largest |diff| of the two
    UNIT vectors (a stored vector may be normalised, a raw [CLS] row is not), how
    many rows are the same floats as given."""
    if len(got) != len(want):
        raise ValueError(f"{len(got)} vectors against {len(want)}")
    cos = sorted(cosine(g, w) for g, w in zip(got, want))
    return {"rows": len(cos), "min_cosine": cos[0] if cos else None,
            "median_cosine": cos[len(cos) // 2] if cos else None,
            "max_abs_diff_unit": max((max_abs_diff(unit(g), unit(w)) for g, w in zip(got, want)), default=0.0),
            "identical_rows": sum(list(g) == list(w) for g, w in zip(got, want))}


def read_sample(path: Path) -> tuple[list[str | tuple[str, str]], list[list[float]]]:
    """`cross_device_check --export`'s file: [{chunk, input, stored}] - a pair is two strings."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not rows:
        raise SystemExit(f"{path}: an empty sample")
    inputs = [tuple(r["input"]) if isinstance(r["input"], list) else r["input"] for r in rows]
    return inputs, [r["stored"] for r in rows]


@dataclass
class Arm:
    name: str
    read_back: dict[str, str] = field(default_factory=dict)
    vectors: list[list[float]] | None = None
    refused: str | None = None


def run_arms(arms: Iterable[tuple[str, Callable[[], tuple[dict[str, str], Callable[[Any], list[float]]]]]],
             inputs: Sequence[Any]) -> list[Arm]:
    """Each arm built (its read-back and its embed function), then every input embedded
    alone, as the live backend embeds. An arm that cannot be built is reported, not fatal."""
    out = []
    for name, build in arms:
        arm = Arm(name)
        try:
            arm.read_back, embed = build()
            arm.vectors = [embed(x) for x in inputs]
        except Exception as e:  # noqa: BLE001 - a refused arm is a result
            arm.refused = f"{type(e).__name__}: {e}"[:400]
        out.append(arm)
    return out


def report(arms: Sequence[Arm], stored: Sequence[Sequence[float]], reference: str = REFERENCE_ARM,
           extra: dict[str, Any] | None = None) -> dict[str, Any]:
    ref = next((a for a in arms if a.name == reference and a.vectors is not None), None)
    rows = []
    for a in arms:
        row: dict[str, Any] = {"arm": a.name, "read_back": a.read_back}
        if a.vectors is None:
            row["refused"] = a.refused
        else:
            row["against_stored"] = compare(a.vectors, stored)
            if ref is not None and a is not ref:
                row[f"against_{reference}"] = compare(a.vectors, ref.vectors)
        rows.append(row)
    return {"arms": rows, "reference_arm": reference if ref is not None else None, **(extra or {})}


# ---- OpenVINO (MedCPT) ----------------------------------------------------------------------------

def ir_weight_types(model: Any) -> dict[str, int]:
    """The IR's Constant nodes by element type: f16 here means weights the file itself holds in f16."""
    counts: dict[str, int] = {}
    for op in model.get_ops():
        if op.get_type_name() == "Constant":
            t = str(op.get_element_type())
            counts[t] = counts.get(t, 0) + 1
    return counts


def ov_arms(model_dir: Path, context: int):
    import openvino as ov
    import medcpt                                    # the live task's own code (deploy/medcpt-rerank/server.py)
    xml, tok = str(model_dir / "openvino_model.xml"), str(model_dir / "tokenizer.json")
    core = ov.Core()

    def build(device: str, config: dict[str, str]):
        def make():
            compiled = core.compile_model(xml, device, config)
            back = {p: str(compiled.get_property(p)) for p in OV_PROPERTIES}
            encode = medcpt.make_encoder(tok, context, fixed=device == "GPU")   # as the live backend
            request = compiled.create_infer_request()

            def embed(item):
                result = request.infer(encode([item]))
                hidden = next(iter(result.values()))
                return [float(x) for x in hidden[0, 0, :]] if hidden.ndim == 3 else [float(x) for x in hidden.reshape(-1)]
            return back, embed
        return make

    defaults = {}
    for device in ("GPU", "CPU"):
        try:
            defaults[device] = {p: str(core.get_property(device, p)) for p in OV_PROPERTIES}
        except Exception as e:  # noqa: BLE001
            defaults[device] = {"refused": f"{type(e).__name__}: {e}"[:200]}
    extra = {"device_defaults": defaults, "ir_weight_types": ir_weight_types(core.read_model(xml)),
             "openvino": ov.get_version()}
    return [(name, build(device, cfg)) for name, device, cfg in OV_ARMS], extra


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="encoder_precision")
    p.add_argument("--backend-dir", type=Path, required=True,
                   help="louise-python's directory: its task modules are imported from there")
    sub = p.add_subparsers(dest="engine", required=True)
    for name in ("ov",):
        s = sub.add_parser(name)
        s.add_argument("--model-dir", type=Path, required=True)
        s.add_argument("--sample", type=Path, required=True, help="tools.cross_device_check --export's file")
        s.add_argument("--context", type=int, required=True, help="the model's context_size")
        s.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    sys.path.insert(0, str(a.backend_dir))
    inputs, stored = read_sample(a.sample)
    arms, extra = ov_arms(a.model_dir, a.context)
    result = report(run_arms(arms, inputs), stored, REFERENCE_ARM, extra)
    text = json.dumps(result, indent=2)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
