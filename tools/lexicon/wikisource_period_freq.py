"""Word frequencies of Italian Wikisource by century of the work, for
louise-ocr's period scoring (docs/notes/historical-languages.md, "Scoring
OCR readings by period"). The format is the one louise-ocr loads as is:

    form<TAB>count<TAB>century<TAB>source

- form: lower-cased, as printed otherwise: accents kept, ſ written as s,
  a trailing apostrophe of an elided form kept (l', de', ch');
- century: its first year (1500 = 1500-1599);
- source: the dump, e.g. wikisource-it-20231201.

    python -m tools.lexicon.wikisource_period_freq it.parquet --out DIR \\
        [--source wikisource-it-20231201] [--from 1200 --to 1899] [--min 1]

The input is the Parquet file of hf.co/datasets/wikimedia/wikisource,
config 20231201.it (CC BY-SA 3.0; the derived list is CC BY-SA 3.0,
attributed to the Wikisource contributors).

THE DATE. The dump has no date field: a work's root page opens with its
Intestazione (author, then the year or "XVI secolo"), and its subpages
(title "Work/Chapter") inherit it. A work whose header gives neither is
left out. The date is the work's, not the edition's: many texts are modern
critical editions, partly regularised (the note's caveat).

Parallel over the rows (multiprocessing, the cores allowed); no network.
"""
from __future__ import annotations

import argparse
import collections
import multiprocessing as mp
import re
from pathlib import Path

ROMAN = {"XI": 1050, "XII": 1150, "XIII": 1250, "XIV": 1350, "XV": 1450,
         "XVI": 1550, "XVII": 1650, "XVIII": 1750, "XIX": 1850, "XX": 1950, "XXI": 2050}
_YEAR = re.compile(r"\s*(?:[a-z.]+\s+)?(\d{3,4})(?:\s*[-–/]\s*\d{1,4})?\s*")
_CENT = re.compile(r"\s*(XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX|XXI)\s+secolo.*", re.I)
#: a word: letters (accented included), an optional elision apostrophe
WORD = re.compile(r"[^\W\d_]+['’]?")


def year_of(text: str) -> int | None:
    """The work's year from the first lines of its root page's header."""
    for line in text.split("\n")[:4]:
        m = _YEAR.fullmatch(line)
        if m:
            return int(m.group(1))
        m = _CENT.fullmatch(line)
        if m:
            return ROMAN[m.group(1).upper()]
    return None


def form_of(word: str) -> str:
    return word.lower().replace("ſ", "s")


def count_texts(texts: list[tuple[int, str]]) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for century, text in texts:
        for w in WORD.findall(text):
            c[(form_of(w), century)] += 1
    return c


def frequencies(titles: list[str], texts: list[str], lo: int, hi: int, procs: int | None = None) -> collections.Counter:
    roots = {t: year_of(x) for t, x in zip(titles, texts) if "/" not in t}
    dated = []
    for t, x in zip(titles, texts):
        y = roots.get(t.split("/")[0])
        if y is not None and lo <= y <= hi:
            dated.append(((y // 100) * 100, x))
    total: collections.Counter = collections.Counter()
    chunks = [dated[i:i + 500] for i in range(0, len(dated), 500)]
    if procs == 1:
        for ch in chunks:
            total.update(count_texts(ch))
        return total
    with mp.get_context("spawn").Pool(procs) as pool:
        for c in pool.imap_unordered(count_texts, chunks):
            total.update(c)
    return total


def write(counts: collections.Counter, out: Path, source: str, minimum: int = 1) -> int:
    rows = sorted(((f, n, c) for (f, c), n in counts.items() if n >= minimum), key=lambda r: (r[2], -r[1], r[0]))
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("form\tcount\tcentury\tsource\n")
        for f, n, c in rows:
            fh.write(f"{f}\t{n}\t{c}\t{source}\n")
    return len(rows)


LICENCE = Path(__file__).with_name("CC-BY-SA-3.0.legalcode.txt")


def write_terms(out: Path, source: str, parquet: Path) -> None:
    """LICENCE: the CC BY-SA 3.0 legal code, byte-exact; SOURCES: one line."""
    (out / "LICENCE").write_bytes(LICENCE.read_bytes())
    (out / "SOURCES").write_text(
        f"{source}\thttps://huggingface.co/datasets/wikimedia/wikisource (config 20231201.it, {parquet.name})"
        "\tCC BY-SA 3.0 (and GFDL), the Italian Wikisource contributors; derived word counts by century of the"
        " work, CC BY-SA 3.0; built by tools/lexicon/wikisource_period_freq.py\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("parquet", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="the directory to write into")
    ap.add_argument("--source", default="wikisource-it-20231201")
    ap.add_argument("--from", dest="lo", type=int, default=1200)
    ap.add_argument("--to", dest="hi", type=int, default=1899)
    ap.add_argument("--min", type=int, default=1)
    a = ap.parse_args(argv)
    import pyarrow.parquet as pq
    t = pq.read_table(a.parquet, columns=["title", "text"]).to_pydict()
    counts = frequencies(t["title"], t["text"], a.lo, a.hi)
    a.out.mkdir(parents=True, exist_ok=True)
    name = f"{a.source}-{a.lo}-{a.hi}.tsv"
    n = write(counts, a.out / name, a.source, a.min)
    write_terms(a.out, a.source, a.parquet)
    print(f"{name}: {n} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
