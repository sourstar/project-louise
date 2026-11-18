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
from pathlib import Path
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
    ap.add_argument("--claims", type=Path, help="the book-editions claims: count the cross-language same-work "
                                               "pairs with no translation mark on either side")
    ap.add_argument("--author", help="also list every record whose creators name this (e.g. 'porta')")
    a = ap.parse_args(argv)
    from app import collections as collections_mod
    col = collections_mod.by_name(a.collection)
    fields = reading.year_fields(getattr(col, "plugin", None))
    rules: collections.Counter = collections.Counter()
    forms: collections.Counter = collections.Counter()
    examples: dict[str, list] = collections.defaultdict(list)
    n = 0
    unparsed, unparsed_examples = 0, []
    by_author: list[dict] = []
    entries: list = []
    auto: collections.Counter = collections.Counter()
    from_summary: collections.Counter = collections.Counter()
    claimed: frozenset = frozenset()      # translation-of claims: none on the node; marks are the corpus's own
    by_claim_only, unmarked, unmarked_examples = 0, 0, []
    _TR = re.compile(r"(?i)\btranslat(?:ed|ion|or)\b|\btradott[oa]\b|\btraduzione\b")
    for r in records_of(a.collection, random.Random(0)):
        if not str(r.get("lang") or "").lower().startswith(a.lang):
            continue
        n += 1
        f = works.edition_facts(r)
        if a.author and any(a.author.lower() in str(c.get("name") or "").lower()
                            for c in ((r.get("extra") or {}).get("creators_detail") or []) if isinstance(c, dict)):
            yy = reading.year_of(fields, f)
            by_author.append({"uid": r["uid"], "title": str(r.get("title") or "")[:60],
                              "year": int(yy) if yy else None, "by": getattr(yy, "by", "") if yy else "",
                              "creators": [(c.get("name"), c.get("role"), c.get("birth"), c.get("death"))
                                           for c in ((r.get("extra") or {}).get("creators_detail") or [])
                                           if isinstance(c, dict)]})
        summary = " ".join(str(x) for x in (((r.get("extra") or {}).get("marc") or {}).get("520") or []))
        if re.search(r"(?i)centur", summary) and not f.century_text and not f.first_published:
            # a record its first publication places needs no century (architecture)
            unparsed += 1
            if len(unparsed_examples) < a.examples:
                unparsed_examples.append({"uid": r["uid"], "summary": summary[:200]})
        if f.century_text:
            forms[re.sub(r"\d+(st|nd|rd|th)", "Nth", f.century_text.lower())] += 1
        y = reading.year_of(fields, f)
        entries.append((r["uid"], f, y))
        if y is not None and f.summary_auto and ("its first publication" in y.by or "century" in y.by):
            auto[("first publication" if "its first publication" in y.by else "century")] += 1
        if y is not None and ("its first publication" in y.by or "century" in y.by) and "is outside them" not in y.by:
            from_summary[("first publication" if "its first publication" in y.by else "century")] += 1
        if r["uid"] in claimed and not f.translated:
            by_claim_only += 1
        if not f.translated and r["uid"] not in claimed and _TR.search(summary):
            unmarked += 1
            if len(unmarked_examples) < a.examples:
                unmarked_examples.append({"uid": r["uid"], "title": str(r.get("title") or "")[:60],
                                          "summary": summary[:160]})
        rule = rule_of(getattr(y, "by", "") if y else "")
        rules[rule] += 1
        if len(examples[rule]) < a.examples:
            examples[rule].append({"uid": r["uid"], "title": str(r.get("title") or "")[:60],
                                   "year": int(y) if y else None, "by": getattr(y, "by", "") if y else "",
                                   # the creators' own dates, as the record states them
                                   "creators": [(c.get("name"), c.get("role"), c.get("birth"), c.get("death"))
                                                for c in ((r.get("extra") or {}).get("creators_detail") or [])
                                                if isinstance(c, dict)][:4]})
    unmarked_pairs = None
    if a.claims:
        from app import historical
        pairs = []
        with open(a.claims, encoding="utf-8") as fh:
            for line in fh:
                c = json.loads(line)
                if c.get("type") == "same-work":
                    pairs.append((str(c.get("subject")), str(c.get("object"))))
        need = {u for p in pairs for u in p}
        marked, lang_of = {}, {}
        for r in records_of(a.collection, random.Random(0), lang=None):
            if r["uid"] in need:
                marked[r["uid"]] = historical.is_translation(r)
                lang_of[r["uid"]] = str(r.get("lang") or "")[:2]
        cross = [(x, y) for x, y in pairs if x in lang_of and y in lang_of and lang_of[x] != lang_of[y]]
        unmarked_pairs = {"cross_language_same_work": len(cross),
                          "neither_side_marked": sum(1 for x, y in cross if not marked[x] and not marked[y]),
                          "with_an_italian_side_unmarked": sum(1 for x, y in cross if not marked[x] and not marked[y]
                                                               and "it" in (lang_of[x], lang_of[y]))}
    shared, volume_stats = reading.share_volumes(entries)
    volume_examples = [{"uid": u, "year": int(yy), "by": yy.by} for u, yy in list(shared.items())[: a.examples]]
    print(json.dumps({"collection": a.collection, "records": n, "rules": dict(rules.most_common()),
                      "from_the_summary": dict(from_summary), "from_pgs_automatic_summary": dict(auto),
                      "volumes": volume_stats, "volume_examples": volume_examples,
                      "unmarked_translation_pairs": unmarked_pairs,
                      "by_author": by_author,
                      "century_forms": dict(forms.most_common(20)),
                      "century_unparsed": unparsed, "century_unparsed_examples": unparsed_examples,
                      "translations_by_claim_only": by_claim_only,
                      "translations_unmarked_candidates": unmarked,
                      "translations_unmarked_examples": unmarked_examples,
                      "examples": examples},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
