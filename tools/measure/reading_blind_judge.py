"""MEASUREMENT (not product code): TLIO's glosses in the AI summary, judged
BLIND by the operator (operator, 2026-11-18, choice C: "Score answers
yourself": the summary eval against modern versions could not reach 30
judged answers — 20 records in span, 4 with a passage TLIO reads).

No modern version is needed: the operator reads the historical passage
and judges which answer reads it better. So every Italian record whose
work's year falls in TLIO's period (1100-1400, not a translation) can give
passages, not only the 20 with a modern partner.

    # 1. on the node: the items, a page to judge and the key, kept apart
    python -m tools.measure.reading_blind_judge make --collection <gutenberg's> \\
        --out-dir <dir> [--n 36] [--per-record 3] [--seed 1] \\
        --readings-tsv <v3 TSV> --tlio-root <ref_root> --weights-sha256 <chat weights>
    # 2. the operator opens <dir>/judge.html, judges, saves choices.json
    # 3. unblinded:
    python -m tools.measure.reading_blind_judge score --out-dir <dir> --choices choices.json

`make` writes judge.html (each item: the query, the historical passage,
two answers as A and B in a seeded random order, never named) and key.json
(which of A and B is ON). The page asks per item which answer reads the
passage more faithfully (A, B, the same) and whether each states something
the passage does not say. `score` unblinds: ON's wins, OFF's, the ties, a
two-sided sign test, the errors per arm, and the RULE below. Nothing in
judge.html says which arm is which; the key is never shown on the page.

THE ARMS (architecture, 2026-11-18): OFF is the SHIPPED chat copy — the
record's year kept, its Wikisource replacements read, `reading.CHAT_TLIO`
False; ON the same with `CHAT_TLIO` True. The difference is TLIO's glosses
alone, the decision asked. An item is kept only where ON reads a TLIO gloss
OFF does not. Both under Quality, the Ask path's own steps
(historical.prepare, retrieval.build_answer_messages).

THE TELLS. An answer may quote the glossary's notation (`form [lemma: …]`,
`[lemma]`), which names its arm. key.json records, per item, whether either
answer carries a bracketed lemma of that item's TLIO readings; `make`
prints how many; `score` reports over all items and over those without.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import random
import sys
from pathlib import Path

RULE = ("TLIO's glosses return to the chat copy only if at least 30 items are judged and ON wins at least "
        "as often as OFF, with no more errors (architecture, 2026-11-18); the result to the operator")
SPAN = (1100, 1400)


def prepared(query: str, searched: dict, tlio: bool) -> dict:
    """The search result as the answer's prompt reads it: the shipped chat
    copy, TLIO's glosses on or off (`reading.CHAT_TLIO`), under Quality."""
    from app import historical, reading
    result = {**searched, "hits": [dict(h) for h in searched.get("hits") or []]}
    keep, reading.CHAT_TLIO = reading.CHAT_TLIO, tlio
    try:
        with reading.quality(True):
            out, _reads = historical.prepare(result, query, "it")
    finally:
        reading.CHAT_TLIO = keep
    return out


def answer(query: str, searched: dict, tlio: bool) -> str:
    from app import llm, retrieval
    msgs = retrieval.build_answer_messages(query, prepared(query, searched, tlio), "it")
    return llm.strip_think(llm.chat(msgs, temperature=0.0, max_tokens=600)).strip()


def arm_readings(text: str, year, tlio: bool) -> list:
    """The readings the chat copy of `text` applies at `year`, TLIO on or off."""
    from app import reading
    found = reading.lookup(reading.words_of([text]))
    keep, reading.CHAT_TLIO = reading.CHAT_TLIO, tlio
    try:
        return reading.apply(text, year, found, reading.CHAT)[1]
    finally:
        reading.CHAT_TLIO = keep


def tlio_only(on: list, off: list) -> list:
    """ON's TLIO readings that OFF does not apply."""
    have = {(r.orig, r.source, r.lemma) for r in off}
    return [r for r in on if r.source == "tlio" and (r.orig, r.source, r.lemma) not in have]


def tells(answers: list[str], lemmas: set[str]) -> bool:
    """Whether an answer quotes the glossary's notation for one of these lemmas."""
    low = [a.lower() for a in answers]
    return any(f"[{lm.lower()}" in a for lm in lemmas if lm for a in low)


def commit_of() -> str:
    """The commit this tool runs from, "" outside a checkout: key.json names
    it, so the operator's result can be reproduced from main."""
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
                             capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=Path(__file__).resolve().parent, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    sha = out.stdout.strip() if out.returncode == 0 else ""
    return sha + ("+dirty" if sha and dirty.stdout.strip() else "")


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact binomial p for wins against losses, ties left out."""
    n = wins + losses
    if not n:
        return 1.0
    k = min(wins, losses)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def page(items: list[dict]) -> str:
    """The judging page: self-contained, no network; the choices saved as a file."""
    blocks = []
    for k, it in enumerate(items):
        blocks.append(f"""
<section data-id="{k}">
  <h2>{k + 1}. {html.escape(it["query"])}</h2>
  <p class="meta">{html.escape(it["title"])} · {html.escape(str(it["year"]))}</p>
  <details open><summary>The passage</summary><blockquote>{html.escape(it["passage"])}</blockquote></details>
  <div class="two">
    <div><h3>A</h3><p>{html.escape(it["A"])}</p>
      <label><input type="checkbox" name="err_A_{k}"> A states something the passage does not say</label></div>
    <div><h3>B</h3><p>{html.escape(it["B"])}</p>
      <label><input type="checkbox" name="err_B_{k}"> B states something the passage does not say</label></div>
  </div>
  <p>Which reads the passage more faithfully?
    <label><input type="radio" name="pick_{k}" value="A"> A</label>
    <label><input type="radio" name="pick_{k}" value="B"> B</label>
    <label><input type="radio" name="pick_{k}" value="same"> the same</label></p>
</section>""")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Blind reading judge</title>
<style>
:root{{--bg:#fff;--fg:#1b1b1b;--muted:#666;--line:#ddd}}
@media (prefers-color-scheme: dark){{:root{{--bg:#151515;--fg:#eee;--muted:#aaa;--line:#333}}}}
body{{background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;margin:0 auto;max-width:960px;padding:0 16px 80px}}
section{{border-bottom:1px solid var(--line);padding:16px 0}} .meta{{color:var(--muted);margin-top:-8px}}
blockquote{{margin:0;padding:8px 12px;border-left:3px solid var(--line);white-space:pre-wrap}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} @media (max-width:640px){{.two{{grid-template-columns:1fr}}}}
button{{font:inherit;padding:8px 16px;position:sticky;bottom:16px}}
</style></head><body>
<h1>Blind reading judge</h1>
<p>{len(items)} questions on medieval Italian passages. For each, two answers written by the chat model from
the passage; which is which is hidden. Judge only against the passage.</p>
{"".join(blocks)}
<p id="left"></p><button id="save">Save choices.json</button>
<script>
const n = {len(items)};
document.getElementById('save').onclick = () => {{
  const out = [];
  for (let k = 0; k < n; k++) {{
    const p = document.querySelector(`input[name=pick_${{k}}]:checked`);
    out.push({{id: k, pick: p ? p.value : null,
      err_A: document.querySelector(`input[name=err_A_${{k}}]`).checked,
      err_B: document.querySelector(`input[name=err_B_${{k}}]`).checked}});
  }}
  document.getElementById('left').textContent = out.filter(o => !o.pick).length + ' left unjudged';
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], {{type: 'application/json'}}));
  a.download = 'choices.json'; a.click();
}};
</script></body></html>"""


def score(key: list[dict], choices: list[dict]) -> dict:
    """Unblinded, over all items and over those without a tell."""
    out = _score(key, choices, lambda kk: True)
    out["without_a_tell"] = _score(key, choices, lambda kk: not kk.get("tell"))
    return out


def _score(key: list[dict], choices: list[dict], keep) -> dict:
    """ON's wins, OFF's, ties, errors per arm, the sign test, the rule."""
    by_id = {c["id"]: c for c in choices}
    won = lost = same = 0
    err = {"on": 0, "off": 0}
    judged = 0
    for k, kk in enumerate(key):
        c = by_id.get(k)
        if not keep(kk) or not c or not c.get("pick"):
            continue
        judged += 1
        on = kk["on"]                                  # "A" or "B"
        off = "B" if on == "A" else "A"
        err["on"] += bool(c.get(f"err_{on}"))
        err["off"] += bool(c.get(f"err_{off}"))
        if c["pick"] == "same":
            same += 1
        elif c["pick"] == on:
            won += 1
        else:
            lost += 1
    ok = judged >= 30 and won >= lost and err["on"] <= err["off"]
    return {"judged": judged, "on_wins": won, "off_wins": lost, "same": same,
            "errors_on": err["on"], "errors_off": err["off"], "sign_test_p": round(sign_test(won, lost), 4),
            "too_few": judged < 30, "tlio_glosses_may_return": ok, "rule": RULE}


def make(a) -> int:
    from app import collections as collections_mod, llm, reading
    from tools.measure import reading_known_items as ki, reading_store
    from tools.measure import reading_summary_eval as ev
    version = reading_store.use(a.readings_tsv, a.tlio_root)
    if "tlio" not in version:
        return reading_store.refuse("TLIO's table is not held: nothing to judge (pass --tlio-root, its ack recorded)")
    col = collections_mod.by_name(a.collection)
    if col is None:
        return reading_store.refuse(f"no collection named {a.collection}")
    con = ev.ro_connect(col)
    rng = random.Random(a.seed)
    items, key = [], []
    seen = refused = without = no_tlio = 0
    for r in ki.records_of(a.collection, rng, lang="it"):
        if len(items) >= a.n:
            break
        y = reading.years([{"chunk_id": f"{r['uid']}:0:1", "source_collection": a.collection}]).get(0)
        if y is None or y.translated or not SPAN[0] <= y <= SPAN[1]:
            continue
        seen += 1
        used: set[str] = set()
        for _p in range(a.per_record):
            if len(items) >= a.n:
                break
            passage = next((p for p in (ki.passage_of(r, rng) for _t in range(ev.DRAWS))
                            if p is not None and p[:200] not in used and ev.readings_in(p, y)), None)
            if passage is None:
                without += 1
                break
            used.add(passage[:200])
            q = llm.strip_think(llm.chat([{"role": "user", "content": ki.PROMPT + passage}],
                                         temperature=0.0, max_tokens=120)).strip().splitlines()[0:1]
            query = (q[0] if q else "").strip().strip('"«»“”')
            if ki.refused(query, passage, str(r.get("title") or "")):
                refused += 1
                continue
            hit = ev.best_passage(con, col, query, r["uid"])
            if hit is None:
                continue
            answered = str(hit["hits"][0].get("text") or "")
            off_r = arm_readings(answered, y, False)
            only = tlio_only(arm_readings(answered, y, True), off_r)
            if not only:
                no_tlio += 1
                continue                                 # ON == OFF in what is decided: nothing to judge
            on, off = answer(query, hit, True), answer(query, hit, False)
            tell = tells([on, off], {x.lemma for x in only})
            first_on = rng.random() < 0.5
            items.append({"query": query, "title": str(r.get("title") or ""), "year": int(y),
                          "passage": str(hit["hits"][0].get("text") or "")[:1800],
                          "A": on if first_on else off, "B": off if first_on else on})
            key.append({"uid": r["uid"], "on": "A" if first_on else "B", "tell": tell,
                        "tlio_readings": [{"orig": x.orig, "lemma": x.lemma} for x in only],
                        "shipped_readings": [{"orig": x.orig, "reg": x.reg or "", "source": x.source} for x in off_r]})
            print(f"item {len(items)}: {r['uid']}", file=sys.stderr, flush=True)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / "judge.html").write_text(page(items), encoding="utf-8")
    (a.out_dir / "key.json").write_text(json.dumps(
        {"commit": commit_of(), "reading_tables": version, "seed": a.seed, "weights_sha256": a.weights_sha256, "span": SPAN,
         "records_in_span": seen, "records_without_a_passage_the_tables_read": without, "refused_queries": refused,
         "answered_texts_without_a_tlio_reading": no_tlio, "items_with_a_tell": sum(k["tell"] for k in key),
         "items": key, "rule": RULE}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"items": len(items), "items_with_a_tell": sum(k["tell"] for k in key),
                      "records_in_span": seen, "without": without, "refused": refused,
                      "answered_texts_without_a_tlio_reading": no_tlio}), file=sys.stderr)
    if len(items) < 30:
        return reading_store.refuse(f"{len(items)} items, under the 30 the rule needs: the page is written, "
                                    "judging it cannot decide")
    return 0


def main(argv: list[str] | None = None) -> int:
    from tools.measure import reading_store as _rs
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--collection", required=True)
    m.add_argument("--out-dir", type=Path, required=True)
    m.add_argument("--n", type=int, default=36, help="items, at most (30 judged needed; a few spare)")
    m.add_argument("--per-record", type=int, default=3)
    m.add_argument("--seed", type=int, default=1)
    m.add_argument("--weights-sha256", required=True)
    m.add_argument("--readings-tsv", type=Path, required=True)
    m.add_argument(_rs.ARG[0], **_rs.ARG[1])
    s = sub.add_parser("score")
    s.add_argument("--out-dir", type=Path, required=True)
    s.add_argument("--choices", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "make":
        return make(a)
    key = json.loads((a.out_dir / "key.json").read_text(encoding="utf-8"))
    got = score(key["items"], json.loads(a.choices.read_text(encoding="utf-8")))
    got["reading_tables"] = key["reading_tables"]
    print(json.dumps(got))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
