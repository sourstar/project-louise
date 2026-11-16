"""How big EUR-Lex's HISTORY slice is, per language: the units Cellar lists
that the Data Dump did not give (consolidated versions, acts not stated in
force), so the operator can choose which languages hold it (München,
2026-11-16).

It runs parser 4's own listing (cellar.TreeInventory: the SPARQL windows of
one CELEX year, each work's in-force with its page) for the collection's
corpus, and counts each language's units by where they are or would be:
the base held (the dump built it), the base not held (an act in force the
dump lacked, or modified since: the API's within the day), the history held
(carried from an earlier corpus) and the history not held (what holding
`history/lang=<l>` would fetch). Nothing is fetched but the listing — no
unit, so the bulk-first rule is kept — and its windows land in the listing
cache the per-unit pass reads after (app/acquire/cellar_listing.py).

At most two languages at once: Cellar's two connections (specs.EURLEX_SPEC).
With `--unit-bytes` and `--units-per-hour` (the per-unit pass's measured
average and rate, its `cellar: N units in M min (R/h)` events) it says the
bytes and the hours the history not held would cost.

Usage:
  python -m tools.measure.eurlex_history <collection> [--languages it,fr,de,en]
                                         [--unit-bytes B] [--units-per-hour R]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def count_language(corpus: Path, lang: str, *, spec=None, endpoint=None, years=None) -> dict:
    """The language's listed units, counted by place and by held."""
    from app.acquire import cellar, specs
    from app.acquire.spec import Filter
    from app.plugins.eurlex.source import _CONSOLIDATED
    spec = spec or specs.ACQUIRE_REGISTRY["eurlex"]
    endpoint = endpoint or spec.endpoints[0]
    inv = cellar.TreeInventory(Path(corpus))
    flt = Filter(include=(f"lang={lang}/*", f"history/lang={lang}/*"))
    c: Counter = Counter()
    for f in cellar.listing(endpoint, spec, flt, years=years, place=inv._place):
        place = "history" if f.rel_path.startswith("history/") else "base"
        held = (Path(corpus) / f.rel_path).is_file()
        c[f"{place}_{'held' if held else 'not_held'}"] += 1
        if place == "history" and _CONSOLIDATED.match(cellar.unit_of(f)["celex"]):
            c["history_consolidated"] += 1
    return {"language": lang, **{k: c[k] for k in ("base_held", "base_not_held", "history_held",
                                                    "history_not_held", "history_consolidated")}}


def main(argv: list[str] | None = None) -> int:
    from app import collections as collections_mod
    from app.plugins.eurlex.source import LANGUAGES
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("collection")
    p.add_argument("--languages", default=None, help="comma-separated codes (default: the 24)")
    p.add_argument("--unit-bytes", type=float, default=None, help="the average bytes of a fetched unit")
    p.add_argument("--units-per-hour", type=float, default=None, help="the per-unit pass's measured rate")
    a = p.parse_args(argv)
    c = collections_mod.by_name(a.collection)
    if c is None or c.plugin != "eurlex":
        raise SystemExit(f"{a.collection}: not a held eurlex collection")
    langs = [x.strip() for x in a.languages.split(",")] if a.languages else sorted(LANGUAGES)
    bad = [l for l in langs if l not in LANGUAGES]
    if bad:
        raise SystemExit(f"not an official language: {', '.join(bad)}")
    rows = []
    with ThreadPoolExecutor(max_workers=2) as ex:            # Cellar's two connections
        for row in ex.map(lambda l: count_language(Path(c.corpus_dir), l), langs):
            if a.unit_bytes:
                row["history_bytes_est"] = int(row["history_not_held"] * a.unit_bytes)
            if a.units_per_hour:
                row["history_hours_est"] = round(row["history_not_held"] / a.units_per_hour, 1)
            rows.append(row)
            print(json.dumps(row), flush=True)
    total = {k: sum(r.get(k, 0) for r in rows) for k in rows[0] if k != "language"} if rows else {}
    print(json.dumps({"language": "total", **total}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
