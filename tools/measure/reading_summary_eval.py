"""MEASUREMENT (not product code): the AI summary of a historical source,
judged against a GOLDEN one written from a MODERN version of the same work
(operator, 2026-11-18: "if a translation in modern language exists (same
language or english), use it as a golden sample. Evaluate using it instead
of the historical language for the AI summary").

    python -m tools.measure.reading_summary_eval --collection <gutenberg's> \\
        --claims <gutenberg-book-editions' claims.jsonl> --set known_items.jsonl \\
        --out summary_eval.jsonl [--permutations 10000] [--seed 1]

ITS OWN SET (architecture, 2026-11-18: the known-item set held 8 of 109
targets with a modern version): every historical Italian record of the
span, not a translation, with a held MODERN version — a `same-work` or `translation-of` claim joining it to an
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
    """The search's own result for the query within one record — its one
    best hit, the result's other keys (catalog, facts…) as the search made
    them — or None. NO translation: the query is Italian and a translation
    loaded the translation model for nothing (the node, 2026-11-18); a modern
    English version is matched by the vector leg."""
    from app import retrieval
    native = int(uid.split(":", 1)[1])
    res = retrieval.search(con, query, k=1, mode="hybrid", translate=False, collection=col, book_ids={native})
    hits = res.get("hits") or []
    return {**res, "hits": hits[:1]} if hits else None


def answer(query: str, searched: dict, *, glossary: bool) -> tuple[str, bool]:
    """(the chat model's answer from this one search result, whether the
    glossary changed it) — the Ask path's own steps: historical.prepare,
    then retrieval.build_answer_messages."""
    from app import historical, llm, reading, retrieval
    result = {**searched, "hits": [dict(h) for h in searched.get("hits") or []]}
    real = reading.years
    if not glossary:
        reading.years = lambda hits, collection=None: {}
    try:
        # both arms under Quality, as an Ask under Quality runs (explicit, not
        # the ContextVar's default); the arms differ by the record's year alone
        with reading.quality(True):
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


def readings_in(text: str, year) -> int:
    """How many words of `text` the tables read at `year` for the answer —
    the glossary's own steps (reading.lookup, reading.apply)."""
    from app import reading
    found = reading.lookup(reading.words_of([text]))
    return len(reading.apply(text, year, found, reading.CHAT)[1])


#: passages drawn from a record before it is given up for want of a word the
#: tables read (the node, 9160bf1: random passages held none, ON == OFF)
DRAWS = 24


def _light(r: dict) -> dict:
    """A record without its text: what pairing and the translation marks
    read — its front matter kept, never its sections (the node: loading
    every claimed record whole killed the run at 8 GB, 2026-11-18)."""
    from app import historical
    return {"uid": r["uid"], "lang": r.get("lang"), "title": r.get("title"), "creators": r.get("creators"),
            "extra": r.get("extra"), "sections": [["Front matter", historical.front_matter(r)]]}


def pairs_of(claims: Path, collection: str, span: tuple[int, int]) -> tuple[list[tuple[dict, str]], dict]:
    """The eval's OWN set (architecture, 2026-11-18): every historical record
    (Italian, its work's year in `span`, not a translation) with a held
    MODERN version (`modern_partners`), and the count before sampling. One
    streamed pass, each claimed record kept LIGHT (`_light`); the texts of
    the historical records sampled are read after (`texts_of`)."""
    from app import reading
    from tools.measure.reading_known_items import records_of
    wanted: set[str] = set()
    with open(claims, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            if c.get("type") in ("same-work", "translation-of"):
                wanted.update((str(c.get("subject")), str(c.get("object"))))
    records = {r["uid"]: _light(r) for r in records_of(collection, random.Random(0), lang=None) if r["uid"] in wanted}
    partners = modern_partners(claims, records)
    out = []
    for hist, modern in partners.items():
        r = records[hist]
        if not str(r.get("lang") or "").startswith("it"):
            continue
        y = reading.years([{"chunk_id": f"{hist}:0:1", "source_collection": collection}]).get(0)
        if y is None or y.translated or not span[0] <= y <= span[1]:
            continue
        out.append((r, modern))
    return out, {"claimed_records": len(records), "records_with_a_modern_version": len(partners),
                 "historical_in_span": len(out)}


def texts_of(collection: str, uids: set[str]) -> dict[str, dict]:
    """The whole records of these uids only, in one streamed pass."""
    from tools.measure.reading_known_items import records_of
    return {r["uid"]: r for r in records_of(collection, random.Random(0), lang=None) if r["uid"] in uids}


def main(argv: list[str] | None = None) -> int:
    from app import collections as collections_mod, config, historical, llm
    from tools.measure import reading_known_items as ki
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--collection", required=True)
    ap.add_argument("--claims", type=Path, required=True)
    ap.add_argument("--from", dest="lo", type=int, default=1200)
    ap.add_argument("--to", dest="hi", type=int, default=1900)
    ap.add_argument("--per-record", type=int, default=6, help="passages a historical record, at most")
    ap.add_argument("--n", type=int, default=150, help="pairs judged, at most")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--permutations", type=int, default=10000)
    ap.add_argument("--weights-sha256", required=True, help="the chat model's weights file sha256")
    ap.add_argument("--judge-model", action="append", default=[],
                    help="a second judge, another family (e.g. louise-chat-command-r7b-q8); repeatable")
    ap.add_argument("--judge-weights", action="append", default=[], metavar="MODEL=SHA256")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--readings-tsv", type=Path, help="the derived pairs file: a scratch table synced from it "
                                                      "by the product's own sync, before the node's")
    a = ap.parse_args(argv)
    from tools.measure import reading_store
    version = reading_store.use(a.readings_tsv)
    if not version:
        return reading_store.refuse("no reading table is held: the glossary ON arm would equal OFF. Sync "
                                    "wikisource-readings, or pass --readings-tsv")
    col = collections_mod.by_name(a.collection)
    if col is None:
        return reading_store.refuse(f"no collection named {a.collection}")
    con = ro_connect(col)
    rng = random.Random(a.seed)
    pairs, before = pairs_of(a.claims, a.collection, (a.lo, a.hi))
    print(f"pairs before sampling: {json.dumps(before)}", file=sys.stderr, flush=True)
    rng.shuffle(pairs)
    # the texts of the records sampled only — at most --n of them, read whole
    pairs = pairs[: a.n]
    whole = texts_of(a.collection, {r["uid"] for r, _m in pairs}) if pairs else {}
    pairs = [(whole[r["uid"]], m) for r, m in pairs if r["uid"] in whole]
    items: list[dict] = []
    misaligned = refused = no_reading = 0
    with open(a.out, "w", encoding="utf-8") as out:
        out.write(json.dumps({"set": "reading-summary-eval", "chat_model": config.CHAT_MODEL,
                              "weights_sha256": a.weights_sha256, "judges": [config.CHAT_MODEL, *a.judge_model],
                              "judge_weights": a.judge_weights,
                              "prompt_sha256": {"query": _sha(ki.PROMPT), "judge": _sha(JUDGE), "align": _sha(ALIGN),
                                                "older_rule": _sha(historical.SYSTEM_RULE)},
                              "min_alignment": MIN_ALIGNMENT, "seed": a.seed, "span": [a.lo, a.hi], "rule": RULE,
                              "pairs_before_sampling": before, "reading_tables": version}, ensure_ascii=False) + "\n")
        # PHASE 1, the chat model only: queries from the historical passages, the
        # modern passage found by the historical one, alignment, the three answers
        for r, modern in pairs:
            if len(items) >= a.n:
                break
            used: set[str] = set()
            from app import reading
            year = reading.years([{"chunk_id": f"{r['uid']}:0:1", "source_collection": a.collection}]).get(0)
            for _p in range(a.per_record):
                # a passage holding a word the tables read at the record's year:
                # one without is ON == OFF by construction and measures nothing
                passage = next((p for p in (ki.passage_of(r, rng) for _t in range(DRAWS))
                                if p is not None and p[:200] not in used and year and readings_in(p, year)), None)
                if passage is None:
                    no_reading += 1
                    break
                used.add(passage[:200])
                q = llm.strip_think(llm.chat([{"role": "user", "content": ki.PROMPT + passage}],
                                             temperature=0.0, max_tokens=120)).strip().splitlines()[0:1]
                query = (q[0] if q else "").strip().strip('"«»“”')
                if ki.refused(query, passage, str(r.get("title") or "")):
                    refused += 1
                    continue
                hist = best_passage(con, col, query, r["uid"])
                # the MODERN passage found by the historical passage itself (a longer,
                # content-full query: the short one matched 6 of 8 wrongly, the node)
                gold_hit = best_passage(con, col, passage[:500], modern)
                if hist is None or gold_hit is None:
                    continue
                al = align(str(hist["hits"][0].get("text") or ""), str(gold_hit["hits"][0].get("text") or ""))
                if al is None or al < MIN_ALIGNMENT:
                    misaligned += 1
                    out.write(json.dumps({"uid": r["uid"], "gold_uid": modern, "query": query, "alignment": al,
                                          "dropped": "misaligned"}, ensure_ascii=False) + "\n")
                    continue
                gold, _ = answer(query, gold_hit, glossary=False)
                with_g, did = answer(query, hist, glossary=True)
                without_g, _ = answer(query, hist, glossary=False)
                print(f"pair {len(items) + 1}: {r['uid']} -> {modern}", file=sys.stderr, flush=True)
                items.append({"uid": r["uid"], "gold_uid": modern, "query": query, "alignment": al,
                              "year": int(year), "year_by": getattr(year, "by", ""),
                              "readings_in_passage": readings_in(passage, year),
                              "readings_in_answered_text": readings_in(str(hist["hits"][0].get("text") or ""), year),
                              "glossary_changed": did, "gold": gold, "on": with_g, "off": without_g})
        # PHASE 2, BY MODEL: each judge over every answer before the next (one load
        # each); BLIND — each answer alone, the arms in random order, never named
        judges = [None, *a.judge_model]
        for jm in judges:
            for it in (x for x in items if x["glossary_changed"]):
                arms = [("on", it["on"]), ("off", it["off"])]
                rng.shuffle(arms)
                for arm, text in arms:
                    it.setdefault("judged", {}).setdefault(jm or "first", {})[arm] = judge(it["query"], it["gold"], text, jm)
        on, off, second = [], [], []
        for it in items:
            if not it["glossary_changed"]:
                out.write(json.dumps(it, ensure_ascii=False) + "\n")
                continue                     # ON == OFF by construction: not judged, not compared
            first = it["judged"]["first"]
            if first["on"]["agreement"] is not None and first["off"]["agreement"] is not None:
                on.append(first["on"]["agreement"]); off.append(first["off"]["agreement"])
            for jm in a.judge_model:
                for arm in ("on", "off"):
                    x, y = first[arm]["agreement"], it["judged"][jm][arm]["agreement"]
                    if x is not None and y is not None:
                        second.append((x, y))
            out.write(json.dumps(it, ensure_ascii=False) + "\n")
        diff = paired_stats([x - y for x, y in zip(on, off)], a.permutations, a.seed)
        summary = {**before, "refused_queries": refused, "dropped_misaligned": misaligned,
                   "records_without_a_passage_the_tables_read": no_reading, "answered": len(items), "judged": len(on),
                   "glossary_changed": sum(1 for it in items if it["glossary_changed"]),
                   "agreement_on": round(statistics.mean(on), 3) if on else None,
                   "agreement_off": round(statistics.mean(off), 3) if off else None,
                   "on_minus_off": diff,
                   "judges_agree_within_1": round(sum(1 for x, y in second if abs(x - y) <= 1) / len(second), 3)
                   if second else None,
                   "judges_mean_abs_diff": round(statistics.mean(abs(x - y) for x, y in second), 3) if second else None,
                   "to_the_operator": bool(diff.get("ci95") and diff["ci95"][1] < 0),
                   "too_few": len(on) < 30,
                   "rule": RULE}
        summary["reading_tables"] = version
        out.write(json.dumps({"summary": summary}) + "\n")
    print(json.dumps(summary), file=sys.stderr, flush=True)
    if not summary["judged"]:
        return reading_store.refuse(f"nothing was judged ({json.dumps(before)}; refused {refused}, misaligned "
                                    f"{misaligned}): no number")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
