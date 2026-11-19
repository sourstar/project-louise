"""Gutenberg ebooks missing from Wikidata: the matches, as a QuickStatements batch.

    python -m tools.measure.pg_wikidata_match pg_catalog.csv --out DIR [--langs it,en,fr,de,es]

Operator, 2026-11-19 ("Do 1": give back to Wikidata). Wikidata holds 4,122
items with a Project Gutenberg ebook ID (P2034) for ~78,000 texts. This finds,
for each PG text, the Wikidata item of the WORK it is, and writes the missing
P2034 statements for QuickStatements — the standard Wikidata batch tool, run by
the operator under their own account. Nothing here edits Wikidata.

Precision over recall, every rule strict:
- THE AUTHOR: a Wikidata person carrying a PG author ID (P1938, ~22,000) whose
  label contains the PG author's surname and whose birth AND death years equal
  the PG catalogue's ("Garibaldi, Giuseppe, 1807-1882"). Exactly one, or no match.
- THE WORK: an item whose author (P50) is that person and whose label IN THE
  EBOOK'S LANGUAGE is exactly the ebook's title, cut at its first subtitle
  mark (as written, or with a capital first letter). Exactly one, or no match.
  An item already carrying this ebook number is skipped.
Most existing P2034 sit on work items (3,544 of 4,128 on 2026-11-19), so the
work item is the target, as is usual on Wikidata.

Writes DIR/matches.csv (each match with its evidence), DIR/quickstatements.txt
and prints the counts.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SPARQL = "https://query.wikidata.org/sparql"
UA = {"User-Agent": "louise-research/1 (pg-wikidata matching; bot)",
      "Accept": "application/sparql-results+json"}
_YEARS = re.compile(r",\s*(\d{3,4})\??-(\d{3,4})\??\s*$")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", " ", s).strip()


def title_key(title: str) -> str:
    """The ebook's title up to its first line break or subtitle mark, normalised."""
    t = title.split("\n")[0]
    t = re.split(r"\s*[:;]\s+|\s+—\s+|\s+-\s+", t)[0]
    return norm(t)


def pg_author(field: str) -> tuple[str, int, int] | None:
    """'Garibaldi, Giuseppe, 1807-1882' -> ('garibaldi', 1807, 1882); None without both years."""
    first = re.sub(r"\s*\[[^\]]*\]\s*$", "", field.split(";")[0].strip())
    m = _YEARS.search(first)
    if not m:
        return None
    surname = norm(first.split(",")[0])
    return (surname, int(m.group(1)), int(m.group(2))) if surname else None


def query(q: str, tries: int = 5) -> list[dict[str, Any]]:
    """The bindings as {var: {"value", "xml:lang"?}}."""
    url = SPARQL + "?" + urllib.parse.urlencode({"query": q})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                d = json.load(r)
            time.sleep(1)
            return d["results"]["bindings"]
        except (OSError, ValueError):
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("the Wikidata query service did not answer")


def qid(b: dict[str, Any], var: str) -> str:
    return b[var]["value"].rsplit("/", 1)[1]


def wd_authors() -> dict[tuple[int, int], set[tuple[str, str]]]:
    """(birth, death) -> {(qid, normalised label)} for people with a PG author ID."""
    out: dict[tuple[int, int], set[tuple[str, str]]] = defaultdict(set)
    for b in query('SELECT ?a ?b ?d ?l WHERE { ?a wdt:P1938 ?pg ; wdt:P569 ?b0 ; wdt:P570 ?d0 . '
                   'BIND(YEAR(?b0) AS ?b) BIND(YEAR(?d0) AS ?d) '
                   '?a rdfs:label ?l FILTER(LANG(?l) IN ("en","mul","it","fr","de","es")) }'):
        if "b" not in b or "d" not in b:          # an unknown date ("somevalue") has no year
            continue
        out[(int(b["b"]["value"]), int(b["d"]["value"]))].add((qid(b, "a"), norm(b["l"]["value"])))
    return out


def author_qid(a: tuple[str, int, int], by_years: dict) -> str | None:
    surname, born, died = a
    hits = {q for q, label in by_years.get((born, died), ()) if surname in label}
    return hits.pop() if len(hits) == 1 else None


def title_forms(title: str) -> list[str]:
    """The labels to look up for an ebook title: cut at its first subtitle mark,
    as written and with a capital first letter (Wikidata's usual label form)."""
    t = re.split(r"\s*[:;]\s+|\s+—\s+|\s+-\s+", title.split("\n")[0])[0].strip().rstrip(".,")
    forms = [t, t[:1].upper() + t[1:]]
    return list(dict.fromkeys(f for f in forms if f))


def _lit(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def works(pairs: list[tuple[str, str, str]], log=print):
    """(author qid, title, lang) -> {work qid}, by EXACT label in the ebook's
    language — Wikidata's label index answers it directly, where listing every
    item of a prolific author times out; work qid -> its P2034 values."""
    by: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    has: dict[str, set[str]] = defaultdict(set)
    for i in range(0, len(pairs), 200):
        chunk = pairs[i:i + 200]
        vals = " ".join(f"(wd:{a} {_lit(t)}@{l})" for a, t, l in chunk)
        rows = query(f'SELECT ?a ?l ?w ?g WHERE {{ VALUES (?a ?l) {{ {vals} }} ?w rdfs:label ?l ; wdt:P50 ?a . '
                     f'OPTIONAL {{ ?w wdt:P2034 ?g }} }}')
        for b in rows:
            w = qid(b, "w")
            by[(qid(b, "a"), b["l"]["value"], b["l"].get("xml:lang", ""))].add(w)
            if "g" in b:
                has[w].add(b["g"]["value"])
        log(f"works: {min(i + 200, len(pairs)):,} of {len(pairs):,} (author, title) pairs")
    return by, has


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--langs", default="it,en,fr,de,es")
    a = ap.parse_args(argv)
    langs = tuple(a.langs.split(","))
    by_years = wd_authors()
    books = []
    stats = Counter()
    for r in csv.DictReader(a.catalog.open(newline="", encoding="utf-8")):
        lang = r["Language"].split(";")[0].strip()
        if r["Type"] != "Text" or lang not in langs:
            continue
        stats["ebooks"] += 1
        au = pg_author(r["Authors"])
        q = author_qid(au, by_years) if au else None
        if q:
            books.append((r["Text#"], lang, " ".join(r["Title"].split()), r["Authors"].split(";")[0], q))
    stats["author matched"] = len(books)
    pairs = sorted({(aq, f, lang) for _, lang, title, _, aq in books for f in title_forms(title)})
    by, has = works(pairs)
    a.out.mkdir(parents=True, exist_ok=True)
    with (a.out / "matches.csv").open("w", newline="", encoding="utf-8") as f, \
            (a.out / "quickstatements.txt").open("w", encoding="utf-8") as qs:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["ebook", "language", "title", "pg_author", "author_qid", "work_qid", "work_has_p2034"])
        for ebook, lang, title, author, aq in books:
            hits = set().union(*(by.get((aq, f, lang), set()) for f in title_forms(title)))
            if len(hits) != 1:
                stats["work ambiguous" if hits else "no work"] += 1
                continue
            wq = hits.pop()
            if ebook in has.get(wq, ()):
                stats["already on Wikidata"] += 1
                continue
            stats["new statement"] += 1
            w.writerow([ebook, lang, title, author, aq, wq, " ".join(sorted(has.get(wq, ())))])
            qs.write(f'{wq}\tP2034\t"{ebook}"\n')
    for k, v in stats.items():
        print(f"{k}: {v:,}")


if __name__ == "__main__":
    main()
