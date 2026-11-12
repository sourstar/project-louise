"""wikipedia's ARTICLE claims as a claims file for `louise hold collection
create annotations` / `hold annotations update` (07 §7.3; operator,
2026-11-10: "the right approach would be to create an annotation";
docs/notes/wikipedia-one-version.md, app/wiki_versions.py).

    python -m tools.annotations_from_wikipedia <wikipedia collection> --out claims.jsonl [--count]

Reads, read-only, the collection's manifest (`native_id`, `tokens`) and its
page_props items (as the smart embedding reads them), and writes one
`same-work` claim per non-shown version of every article of two versions or
more, against the version shown (the largest; English on a tie or an
unknown length). `--count` writes nothing and says how many claims, versions
and articles there would be — what the annotations collection's claim index
would hold.

The node re-runs the same claims by itself once the collection's file
records `method = "wikipedia-versions"` (app/hold/annotation_methods.py,
2026-11-12): this tool is for a first `create`, or to look.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("collection")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--count", action="store_true", help="say the sizes, write nothing")
    a = ap.parse_args(argv)
    from app import collections as collections_mod, db, wiki_versions
    c = next((x for x in collections_mod.load_all() if x.name == a.collection), None)
    if c is None or getattr(c, "plugin", None) != "wikipedia":
        print(f"{a.collection}: not a wikipedia collection held here", file=sys.stderr)
        return 2
    con = db.connect(c)                    # reads only: SELECTs in rowid windows
    m = wiki_versions.from_manifest(c, con)
    sizes = m["item_start"][1:] - m["item_start"][:-1]
    multi = sizes >= 2
    counts = {"articles": int(multi.sum()), "versions": int(sizes[multi].sum()),
              "claims": int((sizes[multi] - 1).sum())}
    if a.count or a.out is None:
        print(json.dumps(counts))
        return 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(a.out.suffix + ".tmp")
    n = 0
    with open(tmp, "wb") as f:
        # `json.dumps(claim, ensure_ascii=False, sort_keys=True)` per line, byte
        # for byte, from one template (wiki_versions.claim_lines, 2026-11-12)
        for chunk in wiki_versions.claim_lines(m, plugin=c.plugin):
            f.write(chunk)
            n += chunk.count(b"\n")
    tmp.replace(a.out)
    print(json.dumps({**counts, "written": n, "out": str(a.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
