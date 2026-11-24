"""`tlio`: the forms of Old Italian (before 1400) and their meanings, from
the Tesoro della Lingua Italiana delle Origini (OVI-CNR), as GLOSSES
(architecture's ruling, 2026-11-18: "TLIO, lemma only ... Replace only
where an attested modern FORM exists; gloss otherwise";
docs/notes/historical-languages.md, "TLIO fetch").

THE ORIGIN AND ITS TERMS. tlio.ovi.cnr.it states no licence; CNR's IRIS
says "all rights reserved, unless stated otherwise". The operator,
2026-11-17: "So far assume we can use it for non commercial purposes, I
will clarify the licensing myself". So RED: nothing is synced before the
terms text laid as `<ref_root>/tlio/TERMS.txt`, byte-exact, is acknowledged
(`licensing.ref_held_back`, invariant 9). A reference source never travels
(06 §6.5): each peer syncs it after its own acknowledgement, so it is never
shared by construction.

THE PAGES. `<ref_root>/tlio/raw/voci/<id>.htm.gz`, each entry's page as
TLIO serves it, gzipped, kept current by `tools/lexicon/tlio_manifest.py`
(its manifest, light and full updates) — the operator's tool, outside
app/. Read on the parse pool (`parallel.each_part`, chunks of pages).

THE ROWS. Each form an entry lists (its "0.1"), lower-cased, one word, gives
a `gloss` row: the entry's lemma and its first definition, capped at
`GLOSS_CHARS`. A form that is also a 1900s word (`wikisource-readings`'
1900s counts, `MODERN_PER_MILLION`) gives none — the ruling's homograph
exclusion: *abbandono* needs no gloss — nor a form spelled as its lemma. The
period is TLIO's: the Italian of the origins, to 1400.
"""
from __future__ import annotations

import gzip
import html
import re
from pathlib import Path
from typing import Iterator

from .. import licensing
from ..plugins.base import LicenseClass, RefRecordBatch, RefSchema, RefSyncState
from . import readings, wikisource_readings

SOURCE_ID = "tlio"
#: the credit shown beside a reading of it, and the DISCLAIMER the operator
#: asked for (2026-11-18: "About TLIO write a disclaimer that no explicit
#: licensing is mentioned, add the usual credits")
ATTRIBUTION = ("Old Italian lemmas and definitions: Tesoro della Lingua Italiana delle Origini (TLIO), "
               "Opera del Vocabolario Italiano, CNR (tlio.ovi.cnr.it)")
DISCLAIMER = ("TLIO states no explicit licence for its content; it is used on this node, for "
              "non-commercial purposes, pending clarification of its terms")
DIR = "tlio"
TABLE = readings.table("tlio_reading")
LANG = "it"
#: the derivation's version; a change is a new ref_version (invariant 3)
#: 2 (2026-11-18): an entry of several senses gives its lemma alone, no
#: definition — its first sense is not the sense in context (the node:
#: "satisfaccia" read as paying a debt)
#: 3 (2026-11-18): the 1900s bar for a form is TLIO's own, lower
#: (`MODERN_PER_MILLION`): a form printed in the 1900s at all often is a
#: modern spelling another lemma owns — fuoro (furono, 32 in 57.0 M) listed
#: under fuoco alone, abborri (aborri, 19) under abborrare alone, where no
#: second lemma and no stem test (abborr- is abborrare's own) can tell
PARSER_VERSION = 3
PERIOD = (1100, 1400)
GLOSS_CHARS = 200
#: a form printed in the 1900s this often (a million words) gives no gloss:
#: a quarter of wikisource-readings' 1.0, written as a literal and never
#: derived from it, so a change there cannot move this floor without tlio's
#: version. There a form must be OLD-only to pair; here a gloss is a guess
#: at a sense, and a wrong one misleads. Set at tlio 3 from the node's
#: counts in 56,976,342 1900s words: fuoro 32, abborri 19 dropped (floor 14)
MODERN_PER_MILLION = 0.25     # tlio's own: a change here is a PARSER_VERSION (invariant 3)
CHUNK = 500

_SECTIONS = ["0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8"]
_WORD = re.compile(r"^[^\W\d_]+['’]?$")


def _text_of(raw: bytes) -> tuple[str, str]:
    s = raw.decode("cp1252", "replace")
    m = re.search(r"<title>(.*?)</title>", s, re.S)
    title = html.unescape(m.group(1)).strip() if m else ""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    return title, re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse(raw: bytes) -> dict:
    """An entry page -> {lemma, pos, forms, definition} (empty where absent)."""
    title, t = _text_of(raw)
    head = t.find("Tutto/Stampa")
    pos = head + 12 if head >= 0 else 0
    found = []
    for sec in _SECTIONS:                       # each marker after the previous one, in order
        m = re.compile(r"(?:^| )" + re.escape(sec) + r" ").search(t, pos)
        if m:
            found.append((sec, m.start(), m.end())); pos = m.end()
    body = {}
    for k, (sec, _a, b) in enumerate(found):
        end = found[k + 1][1] if k + 1 < len(found) else None
        body[sec] = (t[b:end] if end else t[b:]).strip()
    m = re.match(r"(.*?[^a-z.\s/])\s+([a-z][a-z.\s/]*(?:\(FC\))?)$", title)
    lemma, pos_ = (m.group(1).strip(), m.group(2).strip()) if m else (title, "")
    lemma = re.sub(r"\s*\(\d+\)\s*$", "", lemma).lower()          # "a (1)" -> "a"
    forms = []
    for f in re.split(r"\s*[,;]\s+", body.get("0.1", "").rstrip(". ")):
        f = f.rpartition(" : ")[2].partition(" . cfr.")[0].strip().rstrip(".").lower()
        if f and _WORD.match(f):
            forms.append(f)
    definition, senses = "", 0
    d = body.get("0.7", "")
    if d:
        parts = re.split(r"(?:^| )\d+(?:\.\d+)* (?=\S)", " " + d)
        definition = next((p.strip() for p in parts if p.strip()), "")
        senses = len(set(re.findall(r"(?:^| )(\d+)(?:\.\d+)* (?=\S)", " " + d))) or 1
    return {"lemma": lemma, "pos": pos_, "forms": forms, "definition": definition[:GLOSS_CHARS],
            "senses": senses}


def parse_pages(paths: list[str]) -> list[dict]:
    """A pool task: the entries of these pages."""
    out = []
    for p in paths:
        try:
            out.append(parse(gzip.decompress(Path(p).read_bytes())))
        except (OSError, EOFError):
            continue
    return out


def rows_of(entries, modern: dict[str, int], total: int) -> Iterator[dict]:
    floor = MODERN_PER_MILLION * total / 1e6 if total else float("inf")
    lo, hi = PERIOD
    for e in entries:
        for form in dict.fromkeys(e["forms"]):
            if form == e["lemma"] or (total and modern.get(form, 0) >= floor):
                continue
            yield {"id": readings.row_id(form, LANG, lo, hi) + "|" + e["lemma"], "orig": form, "reg": "",
                   "kind": readings.GLOSS, "lemma": e["lemma"],
                   "gloss": e["definition"] if e.get("senses", 1) <= 1 else "", "lang": LANG,
                   "period_from": lo, "period_to": hi, "method": f"tlio-forms {PARSER_VERSION}"}


class TlioSource:
    source_id = SOURCE_ID
    ref_version = PARSER_VERSION
    license_class = LicenseClass.RED
    attribution = ATTRIBUTION + ". " + DISCLAIMER
    #: its directory under ref_root: the pages, TERMS.txt and ack.json
    ref_dir = DIR
    namespaces: list[str] = []

    def schema(self) -> RefSchema:
        return RefSchema(source_id=self.source_id, tables=(TABLE,))

    def input_mtime(self, state: RefSyncState) -> float:
        """The terms, the acknowledgement, the pages' manifest and the 1900s
        counts: a sync held back runs again once they change."""
        base = Path(state.ref_root) / DIR
        paths = [base / licensing.REF_TERMS, base / licensing.REF_ACK, base / "raw" / "manifest.sqlite",
                 base / "raw" / "voci", Path(state.ref_root) / wikisource_readings.MODERN]
        return max((p.stat().st_mtime for p in paths if p.exists()), default=0.0)

    def sync(self, state: RefSyncState) -> Iterator[RefRecordBatch]:
        from .. import parallel
        base = Path(state.ref_root) / DIR
        if not (base / "raw" / "voci").is_dir() or licensing.ref_held_back(base, SOURCE_ID) is not None:
            return
        modern, total = wikisource_readings.modern_counts(Path(state.ref_root))
        pages = sorted(str(p) for p in (base / "raw" / "voci").glob("*.htm.gz"))
        tasks = [(pages[i:i + CHUNK],) for i in range(0, len(pages), CHUNK)]
        for entries in parallel.each_part(parse_pages, tasks):
            got = tuple(rows_of(entries, modern, total))
            if got:
                yield RefRecordBatch(table=TABLE.name, rows=got)
