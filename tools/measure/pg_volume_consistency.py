"""Volumes of one Project Gutenberg work that its catalogue classes differently.

    python -m tools.measure.pg_volume_consistency pg_catalog.csv --out DIR

A work = the same first author, language and title once the volume or part
designation is removed ("Memoirs of Louis XIV — Volume 03" -> "memoirs of louis
xiv"). Collections (complete works, writings, papers, anthologies …) are left
out: their volumes rightly hold different kinds of text.

Volumes may rightly differ in TOPIC (Greville's volumes cover different reigns),
not in what the work IS. Three flags are compared, each read from the subjects
exactly as wiki-gutenberg's selection reads them (kg_scope_count):
  - memoir: a Biography / Memoirs / Diaries / Correspondence / Personal
    narratives heading, or LoCC CT;
  - fiction, poetry or drama;
  - for children: a "Juvenile …" heading;
and the LoCC class. A work is reported when its volumes disagree on any of them,
with the volumes on each side and the heading that sets the flag, so that a
cataloguer can carry it over, adjusted to each volume's topic. Nothing is
proposed blindly. Writes <DIR>/volume-inconsistencies.csv; Bookshelves (PG's
"Category:") are only counted.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import kg_scope_count as scope  # noqa: E402

VOL = re.compile(r"\s*(—|-|,|:|\(|\[)?\s*(volume|vol\.?|tome|band|tomo|part|parte|partie|teil|book|livre|libro)\s*"
                 r"([0-9ivxlc]+|one|two|three|four|five|six|first|second|third)\b.*$", re.I)
COLLECTION = re.compile(r"\b(complete|works|writings|papers|classics|collected|collection|selected|selections|"
                        r"library|anthology|miscellan\w*|beacon lights|junior|werke|oeuvres|œuvres|opere|obras)\b", re.I)


def split(v: str) -> list[str]:
    return [x.strip() for x in v.split(";") if x.strip()]


def works(catalog: Path) -> dict[tuple[str, str, str], list[dict[str, str]]]:
    g: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    with catalog.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["Type"] != "Text":
                continue
            t = " ".join(r["Title"].split())
            stem = VOL.sub("", t).strip(" .,;:—-").lower()
            if stem == t.lower().strip(" .,;:—-") or COLLECTION.search(stem):
                continue
            g[(r["Authors"].split(";")[0].strip(), stem, r["Language"])].append(r)
    return {k: sorted(v, key=lambda r: int(r["Text#"])) for k, v in g.items() if len(v) > 1}


def flags(r: dict[str, str]) -> dict[str, bool]:
    subs, locc = scope.subjects(r), r["LoCC"]
    return {"memoir": scope.is_memoir(subs, locc), "fiction, poetry or drama": scope.is_out(subs, locc),
            "for children": scope.is_juvenile(subs)}


def evidence(r: dict[str, str], flag: str) -> str:
    """The headings that set the flag on this volume."""
    subs, locc = scope.subjects(r), r["LoCC"]
    hits = [s for s in subs if {"memoir": scope.is_memoir([s], ""), "fiction, poetry or drama": scope.is_out([s], ""),
                                "for children": scope.is_juvenile([s])}[flag]]
    if flag == "memoir" and not hits and "CT" in locc:
        hits = ["LoCC CT"]
    return "; ".join(hits)


def inconsistencies(rs: list[dict[str, str]]) -> list[tuple[str, str, str, str]]:
    """(field, volumes with it, volumes without it, the evidence) for each disagreement."""
    out: list[tuple[str, str, str, str]] = []
    fl = {r["Text#"]: flags(r) for r in rs}
    for name in ("memoir", "fiction, poetry or drama", "for children"):
        yes = [r for r in rs if fl[r["Text#"]][name]]
        no = [r for r in rs if not fl[r["Text#"]][name]]
        if yes and no:
            ev = sorted({evidence(r, name) for r in yes})
            out.append((name, " ".join(r["Text#"] for r in yes), " ".join(r["Text#"] for r in no), " | ".join(ev)))
    loccs = {r["Text#"]: r["LoCC"] for r in rs}
    if len({frozenset(split(v)) for v in loccs.values()}) > 1:
        out.append(("LoCC", "", "", " | ".join(f"{k}: {v or '(none)'}" for k, v in loccs.items())))
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    ws = works(a.catalog)
    n = shelves = 0
    count: dict[str, int] = defaultdict(int)
    with (a.out / "volume-inconsistencies.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["work", "author", "language", "volumes", "field", "volumes_with", "volumes_without", "evidence"])
        for (author, stem, lang), rs in sorted(ws.items(), key=lambda kv: int(kv[1][0]["Text#"])):
            if len({frozenset(split(r["Bookshelves"])) for r in rs}) > 1:
                shelves += 1
            inc = inconsistencies(rs)
            if inc:
                n += 1
            for field, yes, no, ev in inc:
                count[field] += 1
                w.writerow([stem, author, lang, " ".join(r["Text#"] for r in rs), field, yes, no, ev])
    print(f"multi-volume works (collections left out): {len(ws)}; inconsistent: {n}; "
          f"by field: {dict(count)}; bookshelves differ: {shelves}")


if __name__ == "__main__":
    main()
