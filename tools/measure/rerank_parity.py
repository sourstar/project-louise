"""MEASUREMENT (not product code): one reranker against another on the SAME
candidate lists - a model moved to another engine against the run it replaces
(docs/notes/medcpt-on-llama-cpp.md §6: MedCPT's cross-encoder from OpenVINO,
`louise-rerank-medcpt`, to our llama.cpp).

    python -m tools.measure.rerank_parity --capture SET.jsonl --queries 40 --per-query 50 \\
        --url http://localai:8080/v1/rerank --model <the candidate> --reference-model louise-rerank-medcpt \\
        [--reference-url URL] [--loads 3] [--cut 7] [--calibration A,B] [--out parity.json]

SET.jsonl is a rerank_bench capture: the lists the live search sends the
reranker (`python -m tools.measure.rerank_bench capture umbrela <judged.jsonl>
--collection pubmed --out SET.jsonl`, the judged set the collection's
calibration was fitted on). Both models are asked the same query with the same
documents (cut alike to `nemotron_rerank_parity.MAX_CHARS`).

Per query: the Kendall tau of the two orders over all documents and over the
reference's top 10, the share of the reference's top 10 the candidate keeps,
whether the first document is the same (a tie for first in the reference -
scores equal to the last bit, as a sigmoid saturates at 1.0 - counts as the
same), the largest score difference. And the CANDIDATE AGAINST ITSELF, bit for
bit (the defect OpenVINO's #112 fixed: a score depending on its batch-mates):
the same request again, the documents reversed, the documents sent `--cut` at
a time, and after each of `--loads` fresh loads (`POST /backend/shutdown`, the
next request loads it again). Latency: each model's whole-list request, warm,
p50 and p95. `--calibration A,B`: the collection's Platt calibration
(app/collections.py `Calibration`) applied to both, the largest difference of
calibrated relevance.

THE MODEL'S OWN SCORES (`--torch-cross <hf snapshot>`, a cross-encoder): the
same lists scored as the model card scores them - transformers'
AutoModelForSequenceClassification, float32 on the CPU, the pair tokenized
(query, document) with truncation to 512, the logit's sigmoid - so each
engine's distance to the model is read, not only their distance to each
other. Then the delta line asks the candidate to be no farther from the model
than the reference is (or within MAX_DELTA of it): an engine is held to the
model, not to the error of the engine it replaces (2026-11-27: llama.cpp
against OpenVINO on the card 0.0102, over the first line, measured before the
model's own scores were in the run).

THE PASS LINES ARE PROPOSED here, not measured before (measuring.md: say
which): the first document the same for every query; the median top-10 share
>= 0.9; the largest score difference <= 0.01; the candidate reproducing itself
exactly in every way above. Latency and the calibration's difference are
REPORTED, not lines: a slower engine, or a calibration to fit again, is the
operator's decision.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from tools.measure.nemotron_rerank_parity import cut, engine_scorer, kendall_tau, pairs_from_capture

MIN_TOP10_SHARE = 0.9
MAX_DELTA = 0.01

Scorer = Callable[[str, list[str]], list[float]]


@dataclass
class QueryResult:
    query: str
    reference: list[float]
    candidate: list[float]
    tau: float
    tau_top10: float
    top10_share: float
    same_top1: bool
    max_abs_delta: float
    reproduces: bool
    candidate_ms: float = 0.0
    reference_ms: float = 0.0
    max_calibrated_delta: float | None = None
    candidate_vs_model: float | None = None
    reference_vs_model: float | None = None
    notes: list[str] = field(default_factory=list)


def top(scores: Sequence[float], k: int) -> list[int]:
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))[:k]


def same_first(reference: Sequence[float], candidate: Sequence[float]) -> bool:
    """The candidate's first is among the reference's firsts (equal to the last bit), or the other way round."""
    if not reference:
        return True
    best_r, best_c = max(reference), max(candidate)
    return (reference[top(candidate, 1)[0]] == best_r) or (candidate[top(reference, 1)[0]] == best_c)


def platt(a: float, b: float, score: float) -> float:
    z = a * score + b
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def compare(query: str, reference: Sequence[float], candidate: Sequence[float],
            reruns: dict[str, Sequence[float]], calibration: tuple[float, float] | None = None) -> QueryResult:
    """One query's verdict. `reruns`: the candidate's other answers to the same documents, mapped back to their
    order, by how they were asked ("again", "reversed", "cut by 7", "load 2", ...)."""
    notes = [f"{how}: scored differently" for how, scores in reruns.items() if list(scores) != list(candidate)]
    head = top(reference, 10)
    r = QueryResult(query=query, reference=list(reference), candidate=list(candidate),
                    tau=round(kendall_tau(reference, candidate), 4),
                    tau_top10=round(kendall_tau([reference[i] for i in head], [candidate[i] for i in head]), 4),
                    top10_share=round(len(set(head) & set(top(candidate, 10))) / max(1, len(head)), 4),
                    same_top1=same_first(reference, candidate),
                    max_abs_delta=round(max((abs(x - y) for x, y in zip(reference, candidate)), default=0.0), 6),
                    reproduces=not notes, notes=notes)
    if calibration is not None:
        a, b = calibration
        r.max_calibrated_delta = round(max((abs(platt(a, b, x) - platt(a, b, y))
                                            for x, y in zip(reference, candidate)), default=0.0), 6)
    return r


def verdict(results: Sequence[QueryResult]) -> dict[str, Any]:
    def p(xs: list[float], q: float) -> float | None:
        return round(statistics.quantiles(xs, n=100)[int(q) - 1], 1) if len(xs) >= 2 else (xs[0] if xs else None)
    shares = [r.top10_share for r in results]
    out = {"queries": len(results),
           "same_top1": round(sum(r.same_top1 for r in results) / len(results), 4) if results else 0.0,
           "median_top10_share": round(statistics.median(shares), 4) if shares else 0.0,
           "median_tau": round(statistics.median([r.tau for r in results]), 4) if results else 0.0,
           "median_tau_top10": round(statistics.median([r.tau_top10 for r in results]), 4) if results else 0.0,
           "max_abs_delta": max((r.max_abs_delta for r in results), default=None),
           "candidate_reproduces": all(r.reproduces for r in results),
           "candidate_ms_p50": p([r.candidate_ms for r in results], 50),
           "candidate_ms_p95": p([r.candidate_ms for r in results], 95),
           "reference_ms_p50": p([r.reference_ms for r in results], 50),
           "reference_ms_p95": p([r.reference_ms for r in results], 95)}
    cal = [r.max_calibrated_delta for r in results if r.max_calibrated_delta is not None]
    if cal:
        out["max_calibrated_delta"] = max(cal)
    delta_ok = out["max_abs_delta"] is not None and out["max_abs_delta"] <= MAX_DELTA
    if results and all(r.candidate_vs_model is not None for r in results):
        out["candidate_vs_model"] = max(r.candidate_vs_model for r in results)
        out["reference_vs_model"] = max(r.reference_vs_model for r in results)
        # held to the model: no farther from it than the engine it replaces, or within the line
        delta_ok = out["candidate_vs_model"] <= max(out["reference_vs_model"], MAX_DELTA)
    out["pass"] = (bool(results) and out["same_top1"] == 1.0 and out["median_top10_share"] >= MIN_TOP10_SHARE
                   and delta_ok and out["candidate_reproduces"])
    out["lines"] = {"same_top1": 1.0, "median_top10_share": MIN_TOP10_SHARE, "max_abs_delta": MAX_DELTA,
                    "candidate_reproduces": True, "status": "proposed, not measured before"}
    if "candidate_vs_model" in out:
        out["lines"]["delta"] = "candidate_vs_model <= max(reference_vs_model, the max_abs_delta line)"
    return out


def timed(score: Scorer, query: str, docs: list[str]) -> tuple[list[float], float]:
    t0 = time.monotonic()
    out = score(query, docs)
    return out, (time.monotonic() - t0) * 1000


def in_pieces(score: Scorer, query: str, docs: list[str], k: int) -> list[float]:
    out: list[float] = []
    for s in range(0, len(docs), k):
        out.extend(score(query, docs[s:s + k]))
    return out


def run(pairs: list[dict[str, Any]], reference: Scorer, candidate: Scorer, *, loads: int = 1, cut_by: int = 0,
        reload: Callable[[], None] | None = None, calibration: tuple[float, float] | None = None,
        model: Scorer | None = None) -> list[QueryResult]:
    """Every query asked of both; the candidate's reruns asked as `compare` reads them. The fresh loads come after
    the first pass: `reload()` unloads the candidate, and each query's first answer after it is that load's."""
    docs_of = [[cut(d) for d in p["documents"]] for p in pairs]
    if docs_of and docs_of[0]:
        reference(pairs[0]["query"], docs_of[0][:1])          # warm both before anything is timed
        candidate(pairs[0]["query"], docs_of[0][:1])
    firsts, refs, ms_c, ms_r, reruns = [], [], [], [], []
    for p, docs in zip(pairs, docs_of):
        ref, mr = timed(reference, p["query"], docs)
        cand, mc = timed(candidate, p["query"], docs)
        again = candidate(p["query"], docs)
        rev = list(reversed(candidate(p["query"], list(reversed(docs)))))
        extra = {"again": again, "reversed": rev}
        if cut_by > 0:
            extra[f"cut by {cut_by}"] = in_pieces(candidate, p["query"], docs, cut_by)
        firsts.append(cand); refs.append(ref); ms_c.append(mc); ms_r.append(mr); reruns.append(extra)
    for n in range(2, loads + 1):
        if reload is None:
            raise ValueError("fresh loads need a way to unload the candidate")
        reload()
        for p, docs, extra in zip(pairs, docs_of, reruns):
            extra[f"load {n}"] = candidate(p["query"], docs)
    results = []
    for p, docs, ref, cand, extra, mc, mr in zip(pairs, docs_of, refs, firsts, reruns, ms_c, ms_r):
        r = compare(p["query"], ref, cand, extra, calibration)
        r.candidate_ms, r.reference_ms = round(mc, 1), round(mr, 1)
        if model is not None:
            truth = model(p["query"], docs)
            r.candidate_vs_model = round(max(abs(x - y) for x, y in zip(cand, truth)), 6)
            r.reference_vs_model = round(max(abs(x - y) for x, y in zip(ref, truth)), 6)
        results.append(r)
    return results


def cross_encoder_scorer(hf_dir: str, max_length: int = 512) -> Scorer:
    '''The model card's own scoring of a cross-encoder: float32 on the CPU, one pair at a time, the logit's sigmoid
    (as `rerank_score:sigmoid` and the OpenVINO backend answer).'''
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    torch.use_deterministic_algorithms(True)
    tok = AutoTokenizer.from_pretrained(hf_dir)
    m = AutoModelForSequenceClassification.from_pretrained(hf_dir, torch_dtype=torch.float32).eval()

    def score(query: str, docs: list[str]) -> list[float]:
        out = []
        with torch.inference_mode():
            for d in docs:
                enc = tok([[query, d]], truncation=True, max_length=max_length, return_tensors="pt")
                out.append(platt(1.0, 0.0, float(m(**enc).logits.view(-1)[0])))
        return out
    return score


def unloader(url: str, model: str, timeout: float = 120.0) -> Callable[[], None]:
    """LocalAI's own unload of one model: the next request loads it again."""
    import httpx
    base = url.split("/v1/")[0]

    def reload() -> None:
        r = httpx.post(f"{base}/backend/shutdown", json={"model": model}, timeout=timeout)
        r.raise_for_status()
    return reload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--capture", type=Path, required=True, help="a rerank_bench capture file")
    ap.add_argument("--queries", type=int, default=40)
    ap.add_argument("--per-query", type=int, default=50)
    ap.add_argument("--url", required=True, help="the candidate's /v1/rerank, full URL")
    ap.add_argument("--model", required=True, help="the candidate's model name")
    ap.add_argument("--reference-model", required=True, help="the run it replaces")
    ap.add_argument("--reference-url", help="the reference's /v1/rerank (default: --url)")
    ap.add_argument("--loads", type=int, default=3, help="fresh loads of the candidate, the first included")
    ap.add_argument("--cut", type=int, default=7, help="also send the documents this many at a time (0: not)")
    ap.add_argument("--calibration", help="A,B: the collection's Platt calibration, applied to both")
    ap.add_argument("--torch-cross", help="the cross-encoder's HF snapshot: the model's own scores beside both")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    calibration = tuple(float(x) for x in a.calibration.split(",")) if a.calibration else None
    pairs = pairs_from_capture(a.capture, a.queries, a.per_query)
    results = run(pairs, engine_scorer(a.reference_url or a.url, a.reference_model), engine_scorer(a.url, a.model),
                  loads=a.loads, cut_by=a.cut, reload=unloader(a.url, a.model), calibration=calibration,
                  model=cross_encoder_scorer(a.torch_cross) if a.torch_cross else None)
    report = {"verdict": verdict(results), "queries": [r.__dict__ for r in results]}
    if a.out:
        a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["verdict"], indent=1))
    return 0 if report["verdict"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
