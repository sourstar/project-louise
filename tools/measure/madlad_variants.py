"""MEASUREMENT (not product code): MADLAD-400 3B VARIANTS — weight precision
(f16, Q8_0) x context size — on human references (operator, 2026-11-10:
"Compare benchmarks of MADLAD with shorter context and higher quantization",
"Add to the benchmark also MADLAD Q8").

WHY. Nothing published compares MADLAD's quantizations or context sizes (the
one datapoint, a community en->dv 3B fine-tune, has Q8_0 and Q4_K_M within
0.03 chrF++ of bf16); the node's question is what Q8_0 (2.8 GB less weight on
the card) and a shorter context (less KV per slot) cost in quality and speed
on OUR call path.

THE REFERENCE SET. WMT24++ (google/wmt24pp, Apache-2.0, arXiv 2502.12404):
998 segments per pair, en -> xx, human post-edited (`target`). Both directions
are scored: en -> xx against `target`, xx -> en with `target` as the source and
`source` as the reference (translationese source; the same for every variant,
so it does not bias a comparison). Segments marked `is_bad_source` are
skipped; the N per pair are taken round-robin over the file's domains in file
order, so every domain is represented and the pick is deterministic.

THE CALL PATH. `translate`, run once per variant in the node's image with
TRANSLATE_MODEL set to the variant's LocalAI model, goes through Louise's own
`app.translate.translate` (segmenting, `<2xx>` prompts, greedy), BATCH items at
a time, one call after the other: what a results page costs.

    python -m tools.measure.madlad_variants translate --data DIR --pairs it_IT,de_DE
        [--n 50] [--batch 8] --out FILE.jsonl
    python -m tools.measure.madlad_variants score FILE.jsonl [FILE2.jsonl ...]
        [--base FILE.jsonl] [--out FILE.json]

`score` gives, per variant and per direction (and per pair), chrF++ and BLEU
against the references (sacrebleu), chrF++ against the BASE variant's own
output (how much the variant CHANGES the translations), the share of segments
with no translation, and seconds per segment. The standardized metrics are
sacrebleu's (Post 2018); COMET would be the stronger standard and needs a
model on the card, which is what is being measured (written down, not used).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path


def target_code(locale: str) -> str:
    """WMT24++'s locale ("it_IT", "zh_CN") -> MADLAD's target code ("it",
    "zh"); traditional Chinese is MADLAD's "zh_Hant"."""
    if locale == "zh_TW":
        return "zh_Hant"
    return locale.split("_")[0]


def select_rows(rows: Iterable[dict], n: int) -> list[dict]:
    """The first n good segments, round-robin over the domains in the order
    they first appear, each domain's in file order."""
    by_domain: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("is_bad_source") or not (r.get("source") or "").strip() \
                or not (r.get("target") or "").strip():
            continue
        by_domain.setdefault(r["domain"], []).append(r)
    out: list[dict] = []
    queues = list(by_domain.values())
    i = 0
    while len(out) < n and any(i < len(q) for q in queues):
        for q in queues:
            if i < len(q) and len(out) < n:
                out.append(q[i])
        i += 1
    return out


def jobs(locale: str, rows: list[dict]) -> list[dict]:
    """Both directions of one pair: en -> xx against `target`, xx -> en
    against `source`."""
    code = target_code(locale)
    out = []
    for r in rows:
        base = {"pair": locale, "domain": r["domain"], "seg": r["segment_id"]}
        out.append({**base, "dir": "en-xx", "text": r["source"], "to": code, "ref": r["target"]})
        out.append({**base, "dir": "xx-en", "text": r["target"], "to": "en", "ref": r["source"]})
    return out


def load_pair(data: Path, locale: str) -> list[dict]:
    with open(data / f"en-{locale}.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run_translate(args: argparse.Namespace) -> int:
    from app import config, translate                    # the node's image
    todo: list[dict] = []
    for locale in args.pairs.split(","):
        todo.extend(jobs(locale, select_rows(load_pair(args.data, locale), args.n)))
    done = 0
    with open(args.out, "w", encoding="utf-8") as out:
        t_all = time.monotonic()
        for k in range(0, len(todo), args.batch):
            batch = todo[k:k + args.batch]
            t0 = time.monotonic()
            hyps = translate.translate([(j["text"], j["to"]) for j in batch])
            dt = time.monotonic() - t0
            if not hyps:
                hyps = [None] * len(batch)
            for j, h in zip(batch, hyps):
                out.write(json.dumps({**j, "hyp": h, "batch_s": round(dt, 3),
                                      "batch_n": len(batch)}, ensure_ascii=False) + "\n")
            done += len(batch)
            print(f"{done}/{len(todo)} {dt:.1f}s", file=sys.stderr, flush=True)
        out.write(json.dumps({"_summary": {"model": config.TRANSLATE_MODEL, "n": len(todo),
                                           "wall_s": round(time.monotonic() - t_all, 1)}}) + "\n")
    return 0


def read_run(path: Path) -> tuple[dict, list[dict]]:
    summary, rows = {}, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if "_summary" in r:
                summary = r["_summary"]
            else:
                rows.append(r)
    return summary, rows


def key(r: dict) -> tuple:
    return (r["pair"], r["seg"], r["dir"])


def aggregate(rows: list[dict], base: dict[tuple, str] | None,
              chrf: Callable[[list[str], list[str]], float],
              bleu: Callable[[list[str], list[str], str], float]) -> dict:
    """Per direction, and per direction x pair: n, the share with no
    translation (scored as ""), chrF++ and BLEU against the references, chrF++
    against the base variant's output, seconds per segment."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["dir"], []).append(r)
        groups.setdefault(f'{r["dir"]} {r["pair"]}', []).append(r)
    out = {}
    for name in sorted(groups):
        g = groups[name]
        hyp = [r["hyp"] or "" for r in g]
        ref = [r["ref"] for r in g]
        lang = g[0]["to"] if len({r["to"] for r in g}) == 1 else "en"
        row = {"n": len(g),
               "missing": round(sum(1 for r in g if not r["hyp"]) / len(g), 4),
               "chrF++": round(chrf(hyp, ref), 2),
               "BLEU": round(bleu(hyp, ref, lang), 2),
               "s_per_seg": round(sum(r["batch_s"] / r["batch_n"] for r in g) / len(g), 4)}
        if base is not None:
            pairs = [(r["hyp"] or "", base[key(r)]) for r in g if key(r) in base]
            if pairs:
                row["chrF++_vs_base"] = round(chrf([p[0] for p in pairs], [p[1] for p in pairs]), 2)
        out[name] = row
    return out


def _sacrebleu():
    import sacrebleu
    chrf_m = sacrebleu.CHRF(word_order=2)

    def chrf(hyp: list[str], ref: list[str]) -> float:
        return chrf_m.corpus_score(hyp, [ref]).score

    def bleu(hyp: list[str], ref: list[str], lang: str) -> float:
        tok = {"zh": "zh", "zh_Hant": "zh", "ja": "char", "ko": "char", "th": "char"}.get(lang, "13a")
        return sacrebleu.corpus_bleu(hyp, [ref], tokenize=tok).score

    return chrf, bleu


def run_score(args: argparse.Namespace) -> int:
    chrf, bleu = _sacrebleu()
    base = None
    if args.base:
        base = {key(r): r["hyp"] or "" for r in read_run(args.base)[1]}
    report = {}
    for path in args.files:
        summary, rows = read_run(path)
        name = summary.get("model") or path.stem
        report[name] = {"summary": summary, "scores": aggregate(rows, base, chrf, bleu)}
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    for name, r in report.items():
        print(f"== {name}  wall {r['summary'].get('wall_s')} s")
        for g in ("en-xx", "xx-en"):
            s = r["scores"].get(g)
            if s:
                print(f"  {g}: chrF++ {s['chrF++']}  BLEU {s['BLEU']}  vs-base "
                      f"{s.get('chrF++_vs_base', '-')}  missing {s['missing']}  "
                      f"{s['s_per_seg']} s/seg  n={s['n']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="madlad_variants")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("translate")
    t.add_argument("--data", type=Path, required=True)
    t.add_argument("--pairs", required=True)
    t.add_argument("--n", type=int, default=50)
    t.add_argument("--batch", type=int, default=8)
    t.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("score")
    s.add_argument("files", type=Path, nargs="+")
    s.add_argument("--base", type=Path)
    s.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    return run_translate(args) if args.cmd == "translate" else run_score(args)


if __name__ == "__main__":
    sys.exit(main())
