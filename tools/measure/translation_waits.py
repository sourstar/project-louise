"""Do a question's translations wait for another's? Read from the admin's
query traces (app/query_trace.py, `state/query-traces/<UTC day>.jsonl`).

THE QUESTION IT ANSWERS (llama-cpp-encoder-decoder.md; docs/notes/localai-bugs.md
P9, staged and incomplete). MADLAD on our llama.cpp encodes a wave of slots
together and decodes them together (P4). A request that arrives while a wave
decodes waits for that wave to end before it is encoded. Keeping the
encoder's output per sequence (P9, stage 1) would let it join the next decode
step. Is that wait real on the node, and how long? A rewrite is decided on
this measurement, not before it (the default the operator was given,
2026-11-27).

WHAT IT READS. Each line's `started` and `timings.languages` (the language plan:
detection, and the translator's calls when the question is translated;
app/retrieval.py `plan_ms`). The language stage is taken to begin at the
question's start: the entity lookup before it takes milliseconds. Two questions
OVERLAP when their language stages share time. Both overlapped and alone, it
reports how many there are and the stage's p50, p95 and max. A stage of at least
`--translated-ms` is counted as one that called the translator: a cache hit or
detection alone takes a few milliseconds.

    python -m tools.measure.translation_waits state/query-traces/2026-11-27.jsonl [more days ...] \\
        [--translated-ms 200] [--json]

What it cannot see: the worker's own translations (document jobs) meeting a
question in the same wave; the translator's queue is not in a trace. If the
alone and overlapped stages are alike, a wait of that kind is not in these
questions either.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.measure import jsonl  # noqa: E402

TRANSLATED_MS = 200.0


def _when(iso: str) -> float:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def stages(lines: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each traced question with a language stage: its start, end (s) and length (ms)."""
    out = []
    for line in lines:
        timings = line.get("timings") or {}
        ms = timings.get("languages") if isinstance(timings, dict) else None
        if not isinstance(ms, (int, float)) or not line.get("started"):
            continue
        t0 = _when(line["started"])
        out.append({"query": line.get("query"), "kind": line.get("kind"), "start": t0,
                    "end": t0 + ms / 1000.0, "ms": float(ms)})
    return sorted(out, key=lambda s: s["start"])


def mark_overlaps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`overlapped`: another question's language stage shares time with this one's."""
    for i, a in enumerate(rows):
        a["overlapped"] = any(j != i and b["start"] < a["end"] and a["start"] < b["end"]
                              for j, b in enumerate(rows))
    return rows


def _pct(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def summary(rows: Sequence[dict[str, Any]], translated_ms: float = TRANSLATED_MS) -> dict[str, Any]:
    def group(sel: Sequence[dict[str, Any]]) -> dict[str, Any]:
        ms = [r["ms"] for r in sel]
        return {"questions": len(sel), "p50_ms": _pct(ms, 0.5), "p95_ms": _pct(ms, 0.95),
                "max_ms": max(ms) if ms else None}
    translated = [r for r in rows if r["ms"] >= translated_ms]
    return {"questions": len(rows), "translated": len(translated), "translated_ms_at_least": translated_ms,
            "translated_alone": group([r for r in translated if not r["overlapped"]]),
            "translated_overlapped": group([r for r in translated if r["overlapped"]]),
            "all_overlapped": sum(r["overlapped"] for r in rows)}


def read(paths: Sequence[Path]) -> list[dict[str, Any]]:
    lines = []
    for p in paths:
        for raw in jsonl.lines(p.read_text(encoding="utf-8")):
            if raw.strip():
                try:
                    lines.append(json.loads(raw))
                except ValueError:
                    continue                      # a line cut by a crash is not a question
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="translation_waits")
    p.add_argument("traces", nargs="+", type=Path)
    p.add_argument("--translated-ms", type=float, default=TRANSLATED_MS)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    rows = mark_overlaps(stages(read(a.traces)))
    s = summary(rows, a.translated_ms)
    if a.json:
        print(json.dumps(s, indent=2))
        return 0
    print(f"{s['questions']} questions with a language stage, {s['translated']} translated "
          f"(>= {a.translated_ms:.0f} ms), {s['all_overlapped']} overlapping another's")
    for name in ("translated_alone", "translated_overlapped"):
        g = s[name]
        print(f"  {name:22} {g['questions']:4}  p50 {g['p50_ms']}  p95 {g['p95_ms']}  max {g['max_ms']} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
