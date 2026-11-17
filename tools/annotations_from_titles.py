"""gutenberg's SAME-TITLE work claims (operator, 2026-10-22: "use the
same-title rule") as a claims file for `louise hold collection create
annotations` (07 §7.3), merged with the claims gutenberg-book-editions
already makes.

    python -m tools.annotations_from_titles records.jsonl \\
        --existing editions-claims.jsonl --out claims.jsonl

INPUT: `records.jsonl` as tools/measure/one_language_records.py writes it
(uid, lang, title, creators, and the catalogue's `agents` [role, agent id,
name], `scan` (MARC 904) and `lccn` (MARC 010)). A record without `agents`
falls back to its creator names as ids. `--existing`: the claims file the
current editions collection was created from; its claims are kept as they
are and a book they already join to its group is not claimed again.

THE TITLE. Lowercased, trimmed, PG's branding prefix dropped ("The
Gutenberg …"), and a trailing part designator dropped: "Volume 2 (of 3)",
", vol. I", "Part 1", ": Section A and B", "(1st 100 Pages)", and a bare
roman numeral after a genre word ("Kertomuksia II"; "Richard II" stays a
title). The designator's number is kept.

THE RULE, within one title — the catalogue's ids, not name strings
(München, 2026-10-22: every one of the 418 titles it leaves split read by
hand; the misses are series with no volume number, State of the Union
Addresses 5010–5049, which no title rule reaches):
1. a SHARED AGENT ID, any role (`2009/agents/N`): name variants are one
   person ("Bates, Lindon" and "Bates, Lindon, Jr." are agent 58742);
2. the same SOURCE SCAN (MARC 904) or the same LCCN (MARC 010);
3. a VOLUME RUN: both titles name a volume, whoever each volume credits
   (operator: "different volumes of the same collection may have different
   authors" — Legends of the Jews, Szold on vols 1–2, Radin on 3–4) — unless
   their numbers collide: two "Volume 1"s under different people are two
   sets (Beard's and Andrews' History of the United States);
4. NOBODY NAMED: books crediting no one (Anonymous, Various, Unknown,
   Project Gutenberg) join each other when no book of the title credits
   anyone (5 of 5 read right: The Boyhood of Jesus, 施公案…). An anonymous
   book beside a named one joins nothing: read over all 14 such pairs, 7
   were other works (The Rosary beside Barclay's novel, Genesis beside
   Piper's story, an anonymous "Winter" that bridged Sharp's and Thijsse's).
A GENRE title ("Poems", "Kertomuksia", "Essays"…) joins only the volumes of
one person: two "Poems" are not one work.

THE CLAIMS: `same-work`, a STAR per group — every book claimed against the
group's lowest ebook number, as tools/annotations_from_works.py does. The
evidence names the test that brought the book into its group and the book
it joined through. Confidence (n + 1) / (n + 2) over the read-through
(`CONFIDENCE`), not certainty.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# THE RULE lives in app/gutenberg_titles.py since 2026-11-17, unchanged: the
# node's own method (`gutenberg-editions`, app/gutenberg_editions.py)
# re-derives these claims every month; this command keeps making the file
from app.gutenberg_titles import (  # noqa: F401
    AUTHOR_ROLES,
    CONFIDENCE,
    CONTRIBUTOR,
    METHOD,
    NOBODY,
    base_title,
    claims_of,
    groups_of,
    is_genre,
    people,
    split_title,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("records", type=Path)
    ap.add_argument("--existing", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    records = [json.loads(line) for line in a.records.open(encoding="utf-8") if line.startswith("{")]
    existing = ([json.loads(line) for line in a.existing.open(encoding="utf-8") if line.startswith("{")]
                if a.existing else [])
    new = claims_of(records, existing)
    with a.out.open("w", encoding="utf-8", newline="\n") as f:
        for c in [*existing, *new]:
            f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
    books = {u for c in new for u in (c["subject"], c["object"])}
    tests: dict[str, int] = defaultdict(int)
    for c in new:
        tests[c["evidence"]["test"]] += 1
    print(json.dumps({"existing": len(existing), "new": len(new), "books_joined": len(books),
                      "groups": len({c["object"] for c in new}), "by_test": dict(tests)}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
