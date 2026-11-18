"""MEASUREMENT (not product code): which rule dates each Italian record of a
collection for the historical reader (architecture, 2026-11-18: "Report on
the node's 1,112: how many records get each rule, and how many centuries
are rejected").

    python -m tools.measure.reading_year_rules --collection <gutenberg's> [--lang it] [--examples 5]

READ-ONLY: every record of the collection's snapshot, `works.edition_facts`
and `reading.year_of` as the reader runs them. Prints one JSON object: the
count per rule (the start of `Year.by`), the rejected centuries, the century
forms read, records with no year, and a few examples of each.
"""
from __future__ import annotations

import argparse
import collections
import json
import re


def rule_of(by: str) -> str:
    if not by:
        return "none"
    if "is outside them" in by:
        return "dates (century rejected)"
    return re.sub(r"\s*\(.*$", "", by)


def main(argv: list[str] | None = None) -> int:
    from app import reading
    from app.ask import works
    from tools.measure.reading_known_items import records_of
    import random
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--collection", required=True)
    ap.add_argument("--lang", default="it")
    ap.add_argument("--examples", type=int, default=5)
    a = ap.parse_args(argv)
    from app import collections as collections_mod
    col = collections_mod.by_name(a.collection)
    fields = reading.year_fields(getattr(col, "plugin", None))
    rules: collections.Counter = collections.Counter()
    forms: collections.Counter = collections.Counter()
    examples: dict[str, list] = collections.defaultdict(list)
    n = 0
    unparsed, unparsed_examples = 0, []
    for r in records_of(a.collection, random.Random(0)):
        if not str(r.get("lang") or "").lower().startswith(a.lang):
            continue
        n += 1
        f = works.edition_facts(r)
        summary = " ".join(str(x) for x in (((r.get("extra") or {}).get("marc") or {}).get("520") or []))
        if re.search(r"(?i)centur", summary) and not f.century_text:
            unparsed += 1
            if len(unparsed_examples) < a.examples:
                unparsed_examples.append({"uid": r["uid"], "summary": summary[:200]})
        if f.century_text:
            forms[re.sub(r"\d+(st|nd|rd|th)", "Nth", f.century_text.lower())] += 1
        y = reading.year_of(fields, f)
        rule = rule_of(getattr(y, "by", "") if y else "")
        rules[rule] += 1
        if len(examples[rule]) < a.examples:
            examples[rule].append({"uid": r["uid"], "title": str(r.get("title") or "")[:60],
                                   "year": int(y) if y else None, "by": getattr(y, "by", "") if y else ""})
    print(json.dumps({"collection": a.collection, "records": n, "rules": dict(rules.most_common()),
                      "century_forms": dict(forms.most_common(20)),
                      "century_unparsed": unparsed, "century_unparsed_examples": unparsed_examples,
                      "examples": examples},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
