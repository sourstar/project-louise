"""MEASUREMENT (not product code): does our llama.cpp score
nvidia/llama-nemotron-rerank-1b-v2 as NVIDIA's own code does, and does it give
back its own scores?

The reranker benchmark's arm A3 (operator, 2026-11-10: "Patch llama.cpp to
support nemo reranker, tomorrow I want to see its benchmarks too") runs on a
llama.cpp port of an architecture upstream has not merged (ggml-org/llama.cpp
#24083; deploy/localai/patches/staged/0008-llama-cpp-nemotron-rerank.patch).
Before any of its numbers is read, this check compares, pair by pair:

- the REFERENCE: the model card's own code — `AutoModelForSequenceClassification`
  with `trust_remote_code`, float32 on the CPU, the card's prompt
  `f"question:{q} \\n \\n passage:{p}"`, one pair at a time (no padding);
- the ENGINE: our `/v1/rerank` (LocalAI, the patched backend, the f16 GGUF),
  asked with the same query and documents.

and reports, per query: the Kendall tau of the two orders, whether the first
document is the same, the largest score difference; and the engine's own
reproducibility: the same request twice, and the documents reversed, must give
the same score to the same document (bit for bit: the strict settings).

    python -m tools.measure.nemotron_rerank_parity --hf /mnt/ssd5/apps/models/nemotron-rerank-1b-v2 \\
        --url http://localai:8080/v1/rerank --model louise-rerank-nemotron-1b \\
        [--capture /data/rerank-bench/gutenberg-speed.jsonl --queries 20] [--out parity.json]

Without --capture it uses its own few multilingual pairs (the card's example,
Italian, German, French). The PASS lines are PROPOSED here, not measured
anywhere before (measuring.md: say which): every query's first document the
same, median per-query tau >= 0.95, and the engine reproducing itself exactly.
f16 weights against a float32 reference move scores by a little; an order
that flips beyond near-ties is a broken port.
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

TEMPLATE = "question:{q} \n \n passage:{p}"
#: the PROPOSED acceptance lines (module docstring)
MIN_TOP1 = 1.0
MIN_MEDIAN_TAU = 0.95
#: a document is cut to this many characters on both sides alike, so the pair
#: fits the engine's context (4,096 tokens) whatever its tokenizer makes of it
MAX_CHARS = 6000

DEFAULT_PAIRS: list[dict[str, Any]] = [
    {"query": "how much protein should a female eat?", "documents": [
        "As a general guideline, the CDC's average requirement of protein for women ages 19 to 70 is 46 grams "
        "per day. But, as you can see from this chart, you'll need to increase that if you're expecting or "
        "training for a marathon.",
        "Definition of summit for English Language Learners. : 1  the highest point of a mountain : the top of "
        "a mountain. : 2  the highest level.",
        "Calorie intake should not fall below 1,200 a day in women or 1,500 a day in men, except under the "
        "supervision of a health professional."]},
    {"query": "chi può revocare il testamento?", "documents": [
        "Il testamento può essere revocato dal testatore in qualsiasi momento, con un nuovo testamento o con "
        "un atto ricevuto da notaio in presenza di due testimoni.",
        "La prescrizione estingue il diritto quando il titolare non lo esercita per il tempo determinato "
        "dalla legge.",
        "Il Colosseo è un anfiteatro di epoca romana situato nel centro di Roma."]},
    {"query": "Wann wurde die Berliner Mauer gebaut?", "documents": [
        "Der Eiffelturm wurde für die Weltausstellung 1889 in Paris errichtet.",
        "Die Berliner Mauer wurde ab dem 13. August 1961 von der DDR errichtet und fiel am 9. November 1989.",
        "Berlin ist die Hauptstadt der Bundesrepublik Deutschland."]},
    {"query": "qui a écrit Les Misérables ?", "documents": [
        "Les Misérables est un roman de Victor Hugo publié en 1862.",
        "Madame Bovary est un roman de Gustave Flaubert.",
        "The Count of Monte Cristo is an adventure novel by Alexandre Dumas."]},
]


@dataclass
class QueryResult:
    query: str
    reference: list[float]
    engine: list[float]
    tau: float
    same_top1: bool
    max_abs_delta: float
    reproduces: bool
    engine_ms: float = 0.0
    notes: list[str] = field(default_factory=list)


def kendall_tau(a: Sequence[float], b: Sequence[float]) -> float:
    """Kendall's tau-b of two score lists over the same items (ties counted as
    neither concordant nor discordant). 1.0 for fewer than two items."""
    n = len(a)
    if n < 2:
        return 1.0
    conc = disc = ties_a = ties_b = 0
    for i in range(n):
        for j in range(i + 1, n):
            da, db = a[i] - a[j], b[i] - b[j]
            if da == 0 and db == 0:
                continue
            if da == 0:
                ties_a += 1
            elif db == 0:
                ties_b += 1
            elif (da > 0) == (db > 0):
                conc += 1
            else:
                disc += 1
    denom = math.sqrt((conc + disc + ties_a) * (conc + disc + ties_b))
    return (conc - disc) / denom if denom else 1.0


def argmax(xs: Sequence[float]) -> int:
    return max(range(len(xs)), key=lambda i: xs[i])


def compare(query: str, reference: Sequence[float], engine: Sequence[float],
            again: Sequence[float], reversed_scores: Sequence[float]) -> QueryResult:
    """One query's verdict. `again`: the same request a second time;
    `reversed_scores`: the documents sent in reverse order, mapped back."""
    reproduces = list(engine) == list(again) == list(reversed_scores)
    notes = []
    if list(engine) != list(again):
        notes.append("the same request scored differently")
    if list(engine) != list(reversed_scores):
        notes.append("the documents reversed scored differently")
    return QueryResult(query=query, reference=list(reference), engine=list(engine),
                       tau=round(kendall_tau(reference, engine), 4),
                       same_top1=argmax(reference) == argmax(engine),
                       max_abs_delta=round(max(abs(x - y) for x, y in zip(reference, engine)), 4),
                       reproduces=reproduces, notes=notes)


def verdict(results: Sequence[QueryResult]) -> dict[str, Any]:
    taus = [r.tau for r in results]
    top1 = sum(r.same_top1 for r in results) / len(results) if results else 0.0
    median_tau = statistics.median(taus) if taus else 0.0
    exact = all(r.reproduces for r in results)
    return {"queries": len(results), "same_top1": round(top1, 4), "median_tau": round(median_tau, 4),
            "min_tau": round(min(taus), 4) if taus else None,
            "max_abs_delta": max((r.max_abs_delta for r in results), default=None),
            "engine_reproduces": exact,
            "engine_ms_p50": round(statistics.median([r.engine_ms for r in results]), 1) if results else None,
            "pass": bool(results) and top1 >= MIN_TOP1 and median_tau >= MIN_MEDIAN_TAU and exact,
            "lines": {"same_top1": MIN_TOP1, "median_tau": MIN_MEDIAN_TAU, "engine_reproduces": True,
                      "status": "proposed, not measured before"}}


def pairs_from_capture(path: Path, queries: int, per_query: int) -> list[dict[str, Any]]:
    """The first `queries` rows of a rerank_bench capture: each row's query as
    sent and up to `per_query` of its candidates' chunk texts."""
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "header" in row:
            continue
        docs: list[str] = []
        q = None
        for cand in row.get("candidates") or []:
            q = q or cand.get("query_sent")
            for ch in cand.get("chunks") or []:
                if ch.get("text"):
                    docs.append(ch["text"])
            if len(docs) >= per_query:
                break
        if docs:
            out.append({"query": q or row.get("query") or "", "documents": docs[:per_query]})
        if len(out) >= queries:
            break
    return out


def cut(text: str, max_chars: int = MAX_CHARS) -> str:
    return text if len(text) <= max_chars else text[:max_chars]


def reference_scorer(model_dir: str) -> Callable[[str, list[str]], list[float]]:
    """The model card's own code, float32 on the CPU, one pair at a time."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    torch.use_deterministic_algorithms(True)
    tok = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_dir, trust_remote_code=True, torch_dtype=torch.float32).eval()
    if model.config.pad_token_id is None:
        model.config.pad_token_id = tok.eos_token_id

    def score(query: str, docs: list[str]) -> list[float]:
        out = []
        with torch.inference_mode():
            for d in docs:
                enc = tok(TEMPLATE.format(q=query, p=d), return_tensors="pt", truncation=True,
                          max_length=8192)
                out.append(float(model(**enc).logits.view(-1)[0]))
        return out
    return score


def engine_scorer(url: str, model: str, timeout: float = 300.0) -> Callable[[str, list[str]], list[float]]:
    """Our /v1/rerank: every document's score in the order sent."""
    import httpx

    def score(query: str, docs: list[str]) -> list[float]:
        r = httpx.post(url, json={"model": model, "query": query, "documents": docs, "top_n": len(docs)},
                       timeout=timeout)
        r.raise_for_status()
        got = {int(x["index"]): float(x["relevance_score"]) for x in r.json()["results"]}
        return [got[i] for i in range(len(docs))]
    return score


def run(pairs: list[dict[str, Any]], reference, engine) -> list[QueryResult]:
    results = []
    for p in pairs:
        docs = [cut(d) for d in p["documents"]]
        ref = reference(p["query"], docs)
        t0 = time.monotonic()
        eng = engine(p["query"], docs)
        ms = (time.monotonic() - t0) * 1000
        again = engine(p["query"], docs)
        rev = list(reversed(engine(p["query"], list(reversed(docs)))))
        r = compare(p["query"], ref, eng, again, rev)
        r.engine_ms = round(ms, 1)
        results.append(r)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hf", required=True, help="the HF model directory (weights + custom code)")
    ap.add_argument("--url", required=True, help="the engine's /v1/rerank, full URL")
    ap.add_argument("--model", required=True, help="the engine's model name")
    ap.add_argument("--capture", type=Path, help="a rerank_bench capture file to take pairs from")
    ap.add_argument("--queries", type=int, default=20)
    ap.add_argument("--per-query", type=int, default=10)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    pairs = pairs_from_capture(a.capture, a.queries, a.per_query) if a.capture else DEFAULT_PAIRS
    results = run(pairs, reference_scorer(a.hf), engine_scorer(a.url, a.model))
    report = {"verdict": verdict(results), "queries": [r.__dict__ for r in results]}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    print(json.dumps(report["verdict"], indent=1))
    return 0 if report["verdict"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
