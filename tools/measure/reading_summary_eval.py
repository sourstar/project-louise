"""MEASUREMENT (not product code): the AI summary of a historical source,
judged against a GOLDEN one written from a MODERN version of the same work
(operator, 2026-11-18: "if a translation in modern language exists (same
language or english), use it as a golden sample. Evaluate using it instead
of the historical language for the AI summary").

    python -m tools.measure.reading_summary_eval --collection <gutenberg's> \\
        --claims <gutenberg-book-editions' claims.jsonl> --set known_items.jsonl \\
        --out summary_eval.jsonl [--permutations 10000] [--seed 1]

For each KEPT query of the known-item set whose target record has a held
MODERN version — a `same-work` or `translation-of` claim joining it to an
English record, or to an Italian TRANSLATION (a `trl` credit) — read-only:

1. the query searched within the historical record and within the modern
   one (`retrieval.search`, `book_ids`), the best passage of each;
2. the answer the chat model writes from the historical passage, the
   reader's glossary ON and OFF (the other helps, A and B, the same in both);
3. the GOLDEN answer, from the modern passage;
4. ALIGNMENT first: the judge rates 0-5 whether the modern passage covers
   the same content as the historical one; a pair below MIN_ALIGNMENT is
   dropped and counted (architecture, 2026-11-18);
5. each answer judged BLIND against the golden one — alone, in random
   order, never told its arm — agreement 0-5 and contradictions, temperature
   0, by the chat model and by each `--judge-model` (another family), whose
   agreement with the first is reported.

THE DECISION RULE, stated before the run (architecture, 2026-11-18): a 95 %
interval of ON - OFF lying wholly below 0 goes to the operator the same day.
The FIRST line is the header: the chat model and the judges, their weights
sha256 as given, the prompts' sha256, the seed, the rule.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import statistics
import sys
from pathlib import Path

JUDGE = (
    "You compare two answers to the same question about a book. The REFERENCE answer was written from a "
    "modern version of the passage and is taken as correct. Score the CANDIDATE: how far do its facts agree "
    "with the reference (0 = none or wrong, 5 = all the reference's facts, nothing contrary), and how many "
    "statements in it contradict the reference. Reply with JSON only: "
    '{"agreement": <0-5>, "contradictions": <int>}\n\nQUESTION: ')


ALIGN = (
    "Two passages, one from an old Italian book and one from a modern version of the same work (possibly "
    "in English). How far do they cover the SAME content (0 = unrelated, 5 = the same events or ideas)? "
    'Reply with JSON only: {"alignment": <0-5>}\n\nOLD:\n')
MIN_ALIGNMENT = 3
RULE = "a 95 % interval of ON - OFF wholly below 0 goes to the operator the same day"


class model:
    """The chat model `config.CHAT_MODEL` names, for one call (a judge of another family)."""

    def __init__(self, name: str | None):
        self.name = name

    def __enter__(self):
        from app import config
        self.old = config.CHAT_MODEL
        if self.name:
            config.CHAT_MODEL = self.name
        return self

    def __exit__(self, *a):
        from app import config
        config.CHAT_MODEL = self.old
        return False


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def align(hist: str, gold: str, judge_model: str | None = None) -> float | None:
    from app import llm
    with model(judge_model):
        raw = llm.strip_think(llm.chat([{"role": "user", "content": ALIGN + hist + "\n\nMODERN:\n" + gold}],
                                       temperature=0.0, max_tokens=40))
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return float(json.loads(m.group(0)).get("alignment")) if m else None
    except (TypeError, ValueError):
        return None


def modern_partners(claims: Path, records: dict[str, dict]) -> dict[str, str]:
    """{historical uid: its modern version's uid} from same-work and
    translation-of claims: the partner English, or an Italian translation."""
    from app import historical
    out: dict[str, str] = {}
    with open(claims, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            if c.get("type") not in ("same-work", "translation-of"):
                continue
            a, b = str(c.get("subject") or ""), str(c.get("object") or "")
            for hist, other in ((a, b), (b, a)):
                o = records.get(other)
                if hist in records and o is not None and hist not in out and (
                        str(o.get("lang") or "").startswith("en")
                        or (str(o.get("lang") or "").startswith("it") and historical.is_translation(o))):
                    out[hist] = other
    return out


def ro_connect(collection) -> sqlite3.Connection:
    from app import db
    con = sqlite3.connect(f"file:{collection.manifest_path}?mode=ro", uri=True, timeout=30,
                          isolation_level=None, factory=db.ManifestConnection)
    con.row_factory = sqlite3.Row
    return con


def best_passage(con, col, query: str, uid: str) -> dict | None:
    from app import retrieval
    native = int(uid.split(":", 1)[1])
    res = retrieval.search(con, query, k=1, mode="hybrid", translate=True, collection=col, book_ids={native})
    hits = res.get("hits") or []
    return hits[0] if hits else None


def answer(query: str, hit: dict, *, glossary: bool) -> tuple[str, bool]:
    """(the chat model's answer from this one passage, whether the glossary changed it)."""
    from app import historical, llm, reading, retrieval
    result = {"hits": [dict(hit)]}
    real = reading.years
    if not glossary:
        reading.years = lambda hits, collection=None: {}
    try:
        prepared, reads = historical.prepare(result, query, "it")
    finally:
        reading.years = real
    changed = any(r.glossary for r in reads)
    msgs = retrieval.build_answer_messages(query, prepared, "it")
    return llm.strip_think(llm.chat(msgs, temperature=0.0, max_tokens=600)).strip(), changed


def judge(query: str, gold: str, cand: str, judge_model: str | None = None) -> dict:
    from app import llm
    with model(judge_model):
        raw = llm.strip_think(llm.chat([{"role": "user", "content": JUDGE + query + "\n\nREFERENCE:\n" + gold
                                         + "\n\nCANDIDATE:\n" + cand}], temperature=0.0, max_tokens=80))
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        got = json.loads(m.group(0)) if m else {}
        return {"agreement": float(got.get("agreement")), "contradictions": int(got.get("contradictions", 0))}
    except (TypeError, ValueError):
        return {"agreement": None, "contradictions": None, "raw": raw[:200]}


def paired_stats(diffs: list[float], permutations: int, seed: int) -> dict:
    """Mean paired difference, two-sided sign-flip permutation p, 95 % bootstrap interval."""
    if not diffs:
        return {"n": 0}
    rng = random.Random(seed)
    obs = statistics.mean(diffs)
    extreme = sum(1 for _ in range(permutations)
                  if abs(statistics.mean(d if rng.random() < 0.5 else -d for d in diffs)) >= abs(obs) - 1e-12)
    boots = sorted(statistics.mean(rng.choice(diffs) for _ in diffs) for _ in range(2000))
    return {"n": len(diffs), "mean_diff": round(obs, 4), "p": round((extreme + 1) / (permutations + 1), 4),
            "ci95": [round(boots[49], 4), round(boots[1949], 4)]}


def main(argv: list[str] | None = None) -> int:
    from app import collections as collections_mod
    from tools.measure.reading_known_items import records_of
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--collection", required=True)
    ap.add_argument("--claims", type=Path, required=True)
    ap.add_argument("--set", type=Path, required=True, help="the known-item set (reading_known_items)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--permutations", type=int, default=10000)
    ap.add_argument("--weights-sha256", required=True, help="the chat model's weights file sha256")
    ap.add_argument("--judge-model", action="append", default=[],
                    help="a second judge, another family (e.g. louise-chat-command-r7b-q8); repeatable")
    ap.add_argument("--judge-weights", action="append", default=[], metavar="MODEL=SHA256")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    lines = [json.loads(x) for x in a.set.read_text(encoding="utf-8").splitlines() if x.strip()]
    queries = [q for q in lines if "qid" in q and not q.get("refused")]
    col = collections_mod.by_name(a.collection)
    # only the targets and the records claims join them to are kept, never the whole collection
    targets = {q["uid"] for q in queries}
    wanted = set(targets)
    with open(a.claims, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            if c.get("type") in ("same-work", "translation-of") and (c.get("subject") in targets
                                                                       or c.get("object") in targets):
                wanted.update((str(c.get("subject")), str(c.get("object"))))
    records = {}
    # every language: the modern versions are English records too (records_of
    # keeps Italian ones by default — the first node run paired nothing so)
    for r in records_of(a.collection, random.Random(0), lang=None):
        if r["uid"] in wanted:
            records[r["uid"]] = {k: v for k, v in r.items() if k != "sections"}
    partners = {h: m for h, m in modern_partners(a.claims, records).items() if h in targets}
    con = ro_connect(col)
    from app import config, historical
    rng = random.Random(a.seed)
    on, off, changed, rows, misaligned = [], [], 0, 0, 0
    second: list[tuple[float, float]] = []                   # (first judge, second judge) per judged answer
    with open(a.out, "w", encoding="utf-8") as out:
        out.write(json.dumps({"set": "reading-summary-eval", "chat_model": config.CHAT_MODEL,
                              "weights_sha256": a.weights_sha256, "judges": [config.CHAT_MODEL, *a.judge_model],
                              "judge_weights": a.judge_weights,
                              "prompt_sha256": {"judge": _sha(JUDGE), "align": _sha(ALIGN),
                                                "older_rule": _sha(historical.SYSTEM_RULE)},
                              "min_alignment": MIN_ALIGNMENT, "seed": a.seed, "rule": RULE},
                             ensure_ascii=False) + "\n")
        for q in queries:
            gold_uid = partners.get(q["uid"])
            if gold_uid is None:
                continue
            hist_hit, gold_hit = best_passage(con, col, q["query"], q["uid"]), best_passage(con, col, q["query"], gold_uid)
            if hist_hit is None or gold_hit is None:
                continue
            al = align(str(hist_hit.get("text") or ""), str(gold_hit.get("text") or ""))
            if al is None or al < MIN_ALIGNMENT:
                misaligned += 1
                out.write(json.dumps({"qid": q["qid"], "uid": q["uid"], "gold_uid": gold_uid,
                                      "alignment": al, "dropped": "misaligned"}, ensure_ascii=False) + "\n")
                continue
            gold, _ = answer(q["query"], gold_hit, glossary=False)
            with_g, did = answer(q["query"], hist_hit, glossary=True)
            without_g, _ = answer(q["query"], hist_hit, glossary=False)
            # BLIND: each answer alone, the two in random order, never labelled
            arms = [("on", with_g), ("off", without_g)]
            rng.shuffle(arms)
            judged: dict[str, dict] = {}
            for arm, text in arms:
                judged[arm] = judge(q["query"], gold, text)
                for jm in a.judge_model:
                    other = judge(q["query"], gold, text, jm)
                    judged[arm].setdefault("others", {})[jm] = other
                    if judged[arm]["agreement"] is not None and other["agreement"] is not None:
                        second.append((judged[arm]["agreement"], other["agreement"]))
            j_on, j_off = judged["on"], judged["off"]
            rows += 1
            changed += did
            if j_on["agreement"] is not None and j_off["agreement"] is not None:
                on.append(j_on["agreement"]); off.append(j_off["agreement"])
            out.write(json.dumps({"qid": q["qid"], "uid": q["uid"], "gold_uid": gold_uid, "query": q["query"],
                                  "alignment": al, "glossary_changed": did, "gold": gold, "on": with_g,
                                  "off": without_g, "judge_on": j_on, "judge_off": j_off}, ensure_ascii=False) + "\n")
            out.flush()
        diff = paired_stats([x - y for x, y in zip(on, off)], a.permutations, a.seed)
        summary = {"queries": len(queries), "with_modern_version": rows + misaligned,
                   "dropped_misaligned": misaligned, "judged": rows, "glossary_changed": changed,
                   "agreement_on": round(statistics.mean(on), 3) if on else None,
                   "agreement_off": round(statistics.mean(off), 3) if off else None,
                   "on_minus_off": diff,
                   "judges_agree_within_1": round(sum(1 for x, y in second if abs(x - y) <= 1) / len(second), 3)
                   if second else None,
                   "judges_mean_abs_diff": round(statistics.mean(abs(x - y) for x, y in second), 3) if second else None,
                   "to_the_operator": bool(diff.get("ci95") and diff["ci95"][1] < 0),
                   "rule": RULE}
        out.write(json.dumps({"summary": summary}) + "\n")
    print(json.dumps(summary), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
