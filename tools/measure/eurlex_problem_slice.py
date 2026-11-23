"""The restart's PROBLEM SLICE for EUR-Lex5 (read-only, nothing written but the
two lists; 2026-11-22). The operator: "Any further run of eurlex has to start
from the problematic parts". This names them from the manifest and the units
check, so `louise dev rederive EUR-Lex5 --ids problem.txt --retry-errors --yes`
derives them first and `--ids rest.txt` the rest afterwards. Nothing is
derived twice (RULE 1).

A live document (no `superseded …` tombstone) is in the problem slice for any of:
  * `duplicate-kept`: the row a unit held twice keeps (app/hold/identity.py
    `plan`); the row it retires is in neither list, `retire-superseded`'s;
  * `chunkless`: indexed with no chunk (`fts_done=1`, `chunks` 0 or none;
    1,909 on 2026-11-21);
  * `corrigendum`: a CELEX `…R(nn)`, whose text the reader once read empty (C2);
  * `errored`: an error other than a tombstone; `--retry-errors` clears it so the
    corrected reader reads the unit again (C9: units refused for another act's
    wrapper were the act's own);
  * `issue:<code>`: an issue of the text (`TEXT_ISSUES`);
  * `unit:<reason>`: a unit the units check flags (`--units FILE`, its
    `--list-flagged` output): a package, a misnamed file, a file in another's
    place, a wrapper under another name, an included document, a package
    lacked, an unreadable wrapper or tree;
  * `largest`: the `--largest` N largest units, by `documents.size`.

Writes `problem.txt`, `rest.txt` and `reform.txt` (one uid a line) and prints
the counts. `reform.txt` holds the units to RE-FORM (a Formex package their tree
lists and they lack): they are in neither of the other two, and are derived when
the re-form lands their files (discovery resets them). Deriving them first would
derive them twice. Then the counts:
per reason, the slice, the rest, the overlaps. The manifest is opened
read-only; a stopped collection is named by its TOML (`--toml`, as
legal_chunker_drift).

Usage:
  python -m tools.measure.eurlex_problem_slice EUR-Lex5 --toml FILE [--units flagged.tsv]
      [--largest 200] --out-dir DIR
"""
from __future__ import annotations

import argparse
import heapq
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: the issues whose document's TEXT may read otherwise with the review's reader
TEXT_ISSUES = ("formex-not-this-act", "item-not-held", "formex-wrapper-names-another-act", "no-text-layer",
               "form-disagrees-with-celex", "consolidation-start-disagrees")
_CORRIGENDUM = re.compile(r"R\(\d{2}\)")
SUPERSEDED = "superseded"
#: the units check's reason for a unit whose tree lists a Formex package it
#: lacks: re-formed first (reform.txt), derived when its files land
REFORM = "unit:package lacked"


def _celex_of(path: str | None) -> str:
    return Path(path).name if path else ""


def slice_of(con, plugin, *, flagged: dict[str, list[str]] | None = None, largest: int = 200
             ) -> tuple[dict[str, set[str]], set[str], set[str]]:
    """(reason → uids, live uids, uids a retire would tombstone); read only."""
    from app.hold import carry, identity
    reasons: dict[str, set[str]] = {}

    def add(reason: str, uid: str) -> None:
        reasons.setdefault(reason, set()).add(uid)
    _counts, retire = identity.plan(con, plugin, log=lambda m: None)
    retired = {u for u, _keep in retire}
    for _u, keep in retire:
        add("duplicate-kept", keep)
    live: set[str] = set()
    by_key: dict[str, str] = {}
    sizes: list[tuple[int, str]] = []
    # ONE walk of the table (no index answers these, and no sort is asked of it)
    for uid, path, error, fts_done, chunks, size in con.execute(
            "SELECT uid, path, error, fts_done, chunks, size FROM documents"):
        if str(error or "").startswith(SUPERSEDED) or uid in retired:
            continue
        live.add(uid)
        if size is not None:
            sizes.append((int(size), uid))
        if path:
            by_key[carry._unit_key(path)] = uid
        if error:
            add("errored", uid)
        elif fts_done == 1 and not chunks:
            add("chunkless", uid)
        if _CORRIGENDUM.search(_celex_of(path)):
            add("corrigendum", uid)
    marks = ",".join("?" for _ in TEXT_ISSUES)
    for uid, code in con.execute(f"SELECT uid, code FROM issues WHERE code IN ({marks})", TEXT_ISSUES):
        if uid in live:
            add(f"issue:{code}", uid)
    for unit, why in (flagged or {}).items():
        uid = by_key.get(carry._unit_key(unit))
        if uid is not None:
            for r in why:
                add(f"unit:{r}", uid)
    for _size, uid in heapq.nlargest(max(0, largest), sizes):
        add("largest", uid)
    return reasons, live, retired


def read_flagged(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        unit, _tab, why = line.partition("\t")
        if unit.strip():
            out[unit.strip()] = [r.strip() for r in why.split(",") if r.strip()]
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eurlex_problem_slice", description=__doc__.split("\n")[0])
    p.add_argument("collection")
    p.add_argument("--toml", type=Path, default=None, help="a stopped collection's TOML, read where it lies")
    p.add_argument("--units", type=Path, default=None, help="the units check's --list-flagged output")
    p.add_argument("--largest", type=int, default=200)
    p.add_argument("--out-dir", type=Path, required=True)
    a = p.parse_args(argv)
    from app import collections as collections_mod, db
    if a.toml is not None:
        coll = collections_mod._load_one(a.toml)
        if coll.name != a.collection:
            print(f"eurlex_problem_slice: {a.toml} is {coll.name!r}, not {a.collection!r}", file=sys.stderr)
            return 2
    else:
        coll = collections_mod.by_name(a.collection)
    if coll is None:
        print(f"eurlex_problem_slice: no collection {a.collection!r} (a stopped one: --toml FILE)",
              file=sys.stderr)
        return 2
    con = db.read_only_connect(coll)
    if con is None:
        print(f"eurlex_problem_slice: {coll.name} holds no manifest", file=sys.stderr)
        return 2
    try:
        reasons, live, retired = slice_of(con, coll.plugin_instance(),
                                          flagged=read_flagged(a.units) if a.units else None,
                                          largest=a.largest)
    finally:
        con.close()
    # A UNIT TO RE-FORM is derived when its re-formed files land (discovery
    # resets it), never before: derived now from its old form it would be
    # derived twice (RULE 1) and its first text replaced (2026-11-22, München:
    # 126,652 units whose tree lists a Formex package they lack)
    reform = set(reasons.get(REFORM, set()))
    problem = (set().union(*reasons.values()) if reasons else set()) - reform
    rest = live - problem - reform
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / "problem.txt").write_text("".join(f"{u}\n" for u in sorted(problem)), encoding="utf-8")
    (a.out_dir / "rest.txt").write_text("".join(f"{u}\n" for u in sorted(rest)), encoding="utf-8")
    (a.out_dir / "reform.txt").write_text("".join(f"{u}\n" for u in sorted(reform)), encoding="utf-8")
    many = Counter(sum(1 for r in reasons.values() if u in r) for u in problem)
    print(json.dumps({"collection": coll.name, "live": len(live), "retired_by_retire_superseded": len(retired),
                      "problem": len(problem), "rest": len(rest), "reform": len(reform),
                      "by_reason": {k: len(v) for k, v in sorted(reasons.items(), key=lambda kv: -len(kv[1]))},
                      "reasons_per_document": dict(sorted(many.items())),
                      "files": [str(a.out_dir / n) for n in ("problem.txt", "rest.txt", "reform.txt")]},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
