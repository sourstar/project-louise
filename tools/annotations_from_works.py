"""gutenberg's WORK claims (02 §2.10; 05 §5.3 "one hit per work", amended
2026-10-09; docs/notes/one-language-per-work.md §1) as a claims file for
`louise hold collection create annotations` (07 §7.3).

    python -m tools.annotations_from_works records.jsonl wiki_map.json --out claims.jsonl

INPUTS, both written by the one-language measurement
(tools/measure/one_language_records.py and one_language_works.py wikidata):

- `records.jsonl`: per gutenberg record its uid and lang and the URLs of
  its RDF description's "Wikipedia page about this book: <url>" lines
  (`wiki`) — what the ORIGIN states (its `editions` fields, if any, are not
  read: see below);
- `wiki_map.json`: each Wikipedia page's Wikidata item, through the
  Wikipedia API's `pageprops.wikibase_item`, redirects followed.

WHY AN ANNOTATIONS COLLECTION. Two books naming Wikipedia pages that
Wikidata's sitelinks join are one work by WIKIDATA's statement, not
Project Gutenberg's; 05 §5.5 (amended 2026-10-09) puts such a link in an
annotations collection, never in gutenberg's records — and nothing of
gutenberg is rebuilt for it (CLAUDE.md RULE 1: its records, vectors and
indexes stay what they are). The search reads the claims from the held
copy's in-memory index; nothing asks Wikidata at search time.

THE CLAIMS, all `same-work` (the method does not know which side is the
source; the original language is not stated per pair), Wikidata's alone:

- `wikidata`: the books whose Wikipedia lines resolve to ONE item are one
  work. A STAR per item — every book claimed against the item's lowest
  ebook number — so a work of n books is n-1 claims, not n(n-1)/2 (Don
  Quixote: 60, not 1,830); the search walks the star. A book naming
  SEVERAL items is an anthology or an omnibus, not an edition, and claims
  nothing through them (grouping through one chained King Lear to
  Grillparzer). Evidence: the item and both books' Wikipedia URLs.
  Confidence 0.96875 = (30 + 1) / (30 + 2): 30 of 30 hand-checked
  cross-language works were one work (crosslang_sample, seeded).

NOT A CLAIM SINCE 2026-11-12 (operator, decision 1 A): Project Gutenberg's
"several editions of this eBook" note. It is the ORIGIN's statement, so it
is gutenberg's own relation `bf:otherEdition` (app/plugins/gutenberg/
source.py `relations`), which the search reads beside these claims
(app/ask/works.py `WORK_RELATIONS`); the `gutenberg-editions-note` claims
this tool made until then are withdrawn at the collection's next `hold
annotations update`, once gutenberg's relations are indexed on the node.

A pair is claimed once, either way round (`read_claims` refuses a repeat).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# THE RULE lives in app/gutenberg_editions.py since 2026-11-17, unchanged: the
# node's own method (`gutenberg-editions`) re-derives these claims every month
# from its own wikipedia collection instead of the API's answers read here
from app.gutenberg_editions import (  # noqa: F401
    CONTRIBUTOR,
    WIKIDATA,
    WIKIDATA_CONFIDENCE,
    found_by_map,
    split_wiki,
    wikidata_claims,
)


def claims_of(records: list[dict], wiki_map: dict) -> list[dict]:
    """The claims, the titles answered by the Wikipedia API (`wiki_map.json`)."""
    return wikidata_claims(records, lambda r: found_by_map(r, wiki_map))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("records", type=Path)
    ap.add_argument("wiki_map", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    records = [json.loads(line) for line in a.records.open(encoding="utf-8") if line.startswith("{")]
    claims = claims_of(records, json.loads(a.wiki_map.read_text("utf-8")))
    with a.out.open("w", encoding="utf-8", newline="\n") as f:
        for c in claims:
            f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
    by = defaultdict(int)
    for c in claims:
        by[c["method"].get("id", c["method"]["kind"])] += 1
    books = {u for c in claims for u in (c["subject"], c["object"])}
    print(json.dumps({"claims": len(claims), "by_method": dict(by), "books": len(books)}),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
