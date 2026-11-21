"""How many Works a held amendment would end (2026-11-21, the EUR-Lex5
review's V3/V4; read-only, nothing written).

validity.CHAIN_VERSION 2 ends a Work's last text version at the first day
after its start from which a held statement says the text changed
(`validity.first_change`): the next version starts then, held or not. Before
the manifest is re-chained (`validity.rechain`, the index maintainer), this
counts what it will move, from the manifest alone, per collection:
  * the Works any statement targets, and of them those whose last text
    version is open today and would be ended — every such version's text and
    vector stages are reset by the re-chain (its index columns rewritten,
    nothing re-embedded);
  * of those, the ones whose new end is already past (the text shown today
    is not the text in force) and the ones ended in the future;
  * the new ends by year, and examples (the Work, its last start, the day,
    the statement that says it).

The manifest is opened read-only (`mode=ro`); every query is indexed
(idx_validity_works_work, the amendments' target index, the validity
primary key); the statements are read once, in their table's order.

Usage:
  python -m tools.measure.validity_changed_since <manifest.sqlite> [--examples 10] [--today YYYYMMDD]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def measure(con: sqlite3.Connection, *, today: int, examples: int = 10) -> dict:
    from app import validity
    con.row_factory = sqlite3.Row
    targets = {r[0] for r in con.execute("SELECT target FROM validity_amendments")}
    works: set[str] = set()
    for key in targets:
        works |= {r[0] for r in con.execute("SELECT work FROM validity_works WHERE key=?", (key,))}
    out = {"statements_targets": len(targets), "works_targeted": len(works), "would_end": 0,
           "ended_already": 0, "ends_later": 0, "already_ended": 0, "by_year": Counter(), "examples": []}
    for work in sorted(works):
        last = con.execute("SELECT uid, valid_from, valid_to FROM validity WHERE work=? AND has_text=1 "
                           "ORDER BY valid_from DESC, native_id DESC LIMIT 1", (work,)).fetchone()
        if last is None:
            continue
        later = con.execute("SELECT 1 FROM validity WHERE work=? AND valid_from > ? LIMIT 1",
                            (work, last["valid_from"])).fetchone()
        if later is not None:
            continue                       # a version without text after it ends it already
        day = validity.first_change(con, work, int(last["valid_from"]))
        if day is None:
            continue
        if last["valid_to"] is not None and int(last["valid_to"]) <= day:
            out["already_ended"] += 1      # a repeal on or before it: nothing moves
            continue
        out["would_end"] += 1
        out["ended_already" if day <= today else "ends_later"] += 1
        out["by_year"][str(day)[:4]] += 1
        if len(out["examples"]) < examples:
            out["examples"].append({"work": work, "last": last["uid"], "from": int(last["valid_from"]),
                                    "until": day})
    out["by_year"] = dict(sorted(out["by_year"].items()))
    return out


def main(argv: list[str] | None = None) -> int:
    from app import validity
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--examples", type=int, default=10)
    ap.add_argument("--today", type=int, default=None)
    a = ap.parse_args(argv)
    con = sqlite3.connect(f"file:{a.manifest}?mode=ro", uri=True)
    got = measure(con, today=a.today or validity.today(), examples=a.examples)
    print(json.dumps(got, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
