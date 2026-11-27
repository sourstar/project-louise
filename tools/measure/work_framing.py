"""MEASUREMENT (not product code): does the answer count a LITERARY source as
evidence for a fact? (München's judged run on the node, 2026-11-27: "When did
Napoleon die?" — "May 5, 1821 [1][2][3][4][6][9] … All these sources agree",
[9] a historical novel labelled "a literary work"; architecture's plan the
same day: measured before and after the fix, on the 9B in embed mode and the
35B in query mode, two runs each.)

  run     OUT.jsonl [--runs 2] [--label before] [--collections gutenberg]
          Every question of QUESTIONS through `app.ask.chat.ask` — the
          answer path of /ask/chat and MCP — on the node's own chat model
          (CHAT_URL), `--runs` times. One line per answer: the question, its
          kind, the answer, its sources with each one's `work` verdict.
  check   OUT.jsonl [OUT2.jsonl …]
          Per file and per question: the answers that cite a literary source
          AS EVIDENCE — in a sentence that cites two sources or more, at least
          one literary and one not, and does not say it is narration
          (NARRATED). The controls are checked unchanged: each control's
          answers cite its expected kind as before.

Ships when the "before" shows the defect, the "after" shows none, and the
controls are unchanged. The check is a heuristic for a person to confirm:
every flagged sentence is printed whole.

Changes no ranking and removes no source (invariant 4): what is measured is
the prompt's framing alone.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

#: (id, question, kind): "fact_with_fiction" — a fact a historical novel, a
#: play or a poem retells, the defect's shape; "control" — München's other
#: three questions of 2026-11-27, which came out right
QUESTIONS: list[tuple[str, str, str]] = [
    ("napoleon-death", "When did Napoleon die?", "fact_with_fiction"),
    ("waterloo", "How did the battle of Waterloo end?", "fact_with_fiction"),
    ("caesar", "Who killed Julius Caesar, and when?", "fact_with_fiction"),
    ("la-rochelle", "What did Cardinal Richelieu do at the siege of La Rochelle?", "fact_with_fiction"),
    # a question ABOUT a literary work: citing literary sources is what it asks
    # for (München's re-score, 2026-11-27: the poem's source is the book that
    # holds it, "Le tragedie, gl'inni sacri e le odi", so no title matches)
    ("cinque-maggio", "Che cosa dice Il cinque maggio di Manzoni su Napoleone?", "control_about_work"),
    ("anabasis", "What happened to the Ten Thousand after the battle of Cunaxa?", "control"),
    ("pericles", "What did Pericles build in Athens?", "control"),
]

#: a sentence that says it reports what a work narrates
NARRATED = re.compile(r"\b(novel|romance|story|tale|play|poem|ode|fiction|narrat\w*|imagin\w*|portray\w*|"
                      r"romanzo|racconto|poesia|dramma|narra\w*|immagina\w*|in the book)\b", re.IGNORECASE)
CITES = re.compile(r"\[(\d+)\]")
SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def literary_ns(sources: list[dict[str, Any]]) -> set[int]:
    return {int(s["n"]) for s in sources
            if isinstance(s.get("work"), dict) and s["work"].get("kind") == "a literary work"}


def _title(s: dict[str, Any]) -> str:
    return re.sub(r"[^\w ]", "", str(s.get("title") or "").split(":")[0]).strip().casefold()


def as_evidence(answer: str, sources: list[dict[str, Any]], question: str = "") -> list[str]:
    """The sentences that count a literary source among a fact's sources. A
    literary work the QUESTION names is what is asked about, and citing it is
    right (architecture, 2026-11-27: "Che cosa dice Il cinque maggio …")."""
    asked = re.sub(r"[^\w ]", "", question).casefold()
    lit = {n for n in literary_ns(sources)
           if not (lambda t: len(t) >= 4 and t in asked)(next((_title(s) for s in sources if int(s["n"]) == n), ""))}
    out = []
    for sent in SENTENCE.findall(answer or ""):
        cited = {int(n) for n in CITES.findall(sent)}
        if len(cited) >= 2 and cited & lit and cited - lit and not NARRATED.search(sent):
            out.append(sent.strip())
    return out


def recording(seen: dict[str, Any]):
    """The search tool as the answer path calls it, each result's `work`
    kept by uid: the verdict the deployed search gave, whatever the answer's
    own source entries carry (a "before" build's do not)."""
    from app.ask import mcp

    def call(name: str, args: dict[str, Any]) -> Any:
        got = mcp._call_tool(name, args)
        if isinstance(got, dict) and "kind_applied" in got:
            # the search's kind (docs/notes/fact-fiction-search.md), per search the answer made
            seen.setdefault("_kinds", []).append({"kind": got["kind_applied"], "by": got.get("kind_by"),
                                                  "left_out": (got.get("left_out") or {}).get("count", 0)})
        for lst in (got or {}).get("lists", []) if isinstance(got, dict) else []:
            for h in lst.get("hits", []):
                if h.get("uid"):
                    seen[h["uid"]] = h.get("work")
        return got
    return call


def run(out: Path, runs: int, label: str, collections: list[str]) -> None:
    from app.ask import chat
    with out.open("a", encoding="utf-8") as f:
        for r in range(runs):
            for qid, question, kind in QUESTIONS:
                try:
                    seen: dict[str, Any] = {}
                    got = chat.ask(question, collections=collections, call=recording(seen))
                    kinds = seen.pop("_kinds", [])
                    sources = [{**s, "work": s.get("work") or seen.get(s.get("uid"))} for s in got.get("sources") or []]
                    row = {"label": label, "run": r, "id": qid, "kind": kind, "question": question,
                           "about_the_work": kind == "control_about_work",
                           "answer": got.get("answer"), "sources": sources, "search_kinds": kinds}
                except Exception as e:   # noqa: BLE001 — said in the file, the run goes on
                    row = {"label": label, "run": r, "id": qid, "kind": kind, "question": question,
                           "error": f"{type(e).__name__}: {e}"}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                print(f"{label} run {r} {qid}: {'error' if 'error' in row else 'ok'}", file=sys.stderr)


def kind_missed(row: dict[str, Any], want: str) -> int:
    """1 when a run that records its searches' kinds (an "after" build) did
    not search `want` first: step 1's done-test (fact-fiction-search.md §4,
    "the about-the-work questions get literature"); 0 for a "before" file."""
    kinds = row.get("search_kinds")
    return int(bool(kinds) and kinds[0]["kind"] != want)


def kinds_said(row: dict[str, Any], want: str | None) -> str:
    kinds = row.get("search_kinds")
    if not kinds:
        return ""
    line = ", ".join(f"{k['kind']} by {k['by']}" + (f" ({k['left_out']} left out)" if k["left_out"] else "")
                     for k in kinds)
    miss = want is not None and kinds[0]["kind"] != want
    return f"; searched {line}" + (f" — WANTED {want}" if miss else "")


def check(files: list[Path]) -> int:
    bad = 0
    for p in files:
        rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        print(f"== {p}")
        for row in rows:
            if "error" in row:
                print(f"  {row['id']} run {row['run']}: ERROR {row['error']}")
                continue
            if row.get("about_the_work") or row["kind"] == "control_about_work":
                # architecture, 2026-11-27: a question ABOUT a literary work is
                # judged by reading; the reader's verdict is recorded in the file
                # (`verdict_by_reading`), the checker does not apply
                said = row.get("verdict_by_reading") or "NOT YET READ"
                print(f"  {row['id']} run {row['run']} (about the work): judged by reading: {said}"
                      f"{kinds_said(row, 'literature')}")
                bad += kind_missed(row, "literature")
                continue
            flagged = as_evidence(row["answer"], row["sources"], row.get("question", ""))
            lit = sorted(literary_ns(row["sources"]))
            mark = "LITERARY AS EVIDENCE" if flagged else "ok"
            print(f"  {row['id']} run {row['run']} ({row['kind']}): {mark}; literary sources {lit}"
                  f"{kinds_said(row, 'facts' if row['kind'] == 'fact_with_fiction' else None)}")
            if row["kind"] == "fact_with_fiction":
                bad += kind_missed(row, "facts")
            for s in flagged:
                print(f"      | {s}")
            bad += bool(flagged)
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("out", type=Path)
    r.add_argument("--runs", type=int, default=2)
    r.add_argument("--label", default="run")
    r.add_argument("--collections", nargs="*", default=["gutenberg"])
    c = sub.add_parser("check")
    c.add_argument("files", type=Path, nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a.out, a.runs, a.label, a.collections)
        return 0
    return 1 if check(a.files) else 0


if __name__ == "__main__":
    sys.exit(main())
