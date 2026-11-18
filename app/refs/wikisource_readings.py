"""`wikisource-readings`: historical Italian spellings and their modern
forms, both attested in Italian Wikisource's proofread scan pages
(architecture's ruling, 2026-11-18; docs/notes/historical-languages.md,
"Proposal: a reading annotation for historical Italian").

THE ORIGIN FORM, and why. The XML dump of it.wikisource, ONE form every
time: `itwikisource-<run>-pages-articles.xml.bz2` of a DATED run
(dumps.wikimedia.org/itwikisource/<run>/; CC BY-SA 3.0 and GFDL) — the
pages-articles dump holds the scan pages (ns 108) and their indexes (ns
110) this source reads; the Hugging Face Parquet (main namespace only) has
neither. `resolve_run` names the run (Wikimedia's own RSS for the newest
complete one) and its published sha1, `refs/acquire.py` fetches it on the
node's budgeted, Range-resumable transport (invariant 5) and checks the
sha1 before anything is derived; the run is recorded beside the pairs
(`RUN`) and in each pair's `method`, so the table's digest — its version —
names the run it came from (architecture, 2026-11-18: two copies of one
size and two hashes are two dumps). Its scan pages (namespace 108, "Pagina:") are
transcribed from the scanned EDITION, ſ written as s by Wikisource's
convention, and dated here by that edition's PRINT year (namespace 110,
"Indice:", field Anno); only pages proofread (pagequality level >= 3) are
read. The main namespace dates a text by its work and often holds a modern,
regularised edition (louise-ocr measured *vtilissimo* as unseen there), so it
is not used.

THE PAIRS. A form printed 1400-1899 (at least `MIN_OLD` times) whose
regularisation — the form with some of the old printing conventions
undone, each optional, `variants` — is a word printed in the 1900s at least
`MODERN_PER_MILLION` times a million words, the most printed one if several
are, gives `orig` → `reg` for the centuries it was printed in. A form that
is itself a 1900s word gives none (the ruling's homograph exclusion: *ha*
stays *ha*) — judged against its modern form, not absolutely: printed in
the 1900s at least `HOMOGRAPH_SHARE` as often as `reg`. Measured on the
20261101 dump: *perche* is printed 15 a million words in the 1900s (reprints
of old texts) against *perché*'s 751 — a historical spelling, kept; *gioja*
928 against *gioia*'s 7,288 — still current then, excluded. The conventions, the
ones louise-ocr's `it_old._key` normalises: v for u at a word's start
(*vtile*), u for v inside one (*hauere*), an initial h before a vowel
(*huomo*), ph for f, ti before a vowel for zi (*oratione*), ct and pt for tt,
j for i, an unaccented final vowel (*perche*). Nothing is
inferred that the two attestations do not both carry.

The 1900s counts are written beside the pairs (`MODERN`), so `tlio`'s sync
applies the same exclusion to its forms.

The derivation runs in the transform after the download: the XML parse is
one process (bz2 is one stream); the counting on `parallel.processes()`
spawned workers, a bounded window of chunks in flight (the pattern of
`parallel.each_part`, which needs its tasks indexable; a stream is not).
"""
from __future__ import annotations

import bz2
import collections
import csv
import itertools
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Iterator

from ..plugins.base import LicenseClass, RefRecordBatch, RefSchema, RefSyncState
from . import readings

SOURCE_ID = "wikisource-readings"
TABLE = readings.table("wikisource_reading")
FINAL = "wikisource_readings.tsv"
MODERN = "wikisource_modern.tsv"
LANG = "it"

#: the derivation's version: its rules AND its parameters below (MIN_OLD,
#: MIN_LEN, MODERN_PER_MILLION, HOMOGRAPH_SHARE, MAX_SITES) — a change to any
#: is a new ref_version (invariant 3; architecture, 2026-11-18: "it is a
#: derivation parameter, so it goes into ref_version"). tests/
#: test_refs_readings.py pins the parameters to this number. 2: the
#: homograph bar relative to the modern form (was absolute), MIN_LEN 5.
#: 3: MIN_LEN 4 (3 for a final accent), numerals and cut words dropped by
#: rule (`_ROMAN`, `_CUT`), no y → i (a y in these prints is a foreign word's),
#: a short form printed at least MIN_OLD_SHORT times and never elided.
RULES_VERSION = 3
#: a form must be printed this often in 1400-1899 to give a pair (an OCR or
#: transcription slip is printed once or twice)
MIN_OLD = 3
#: shorter forms are abbreviations or foreign words (pj, had, ham) more often
#: than Italian ones. 5 (version 2) dropped 502 of 3,416 pairs on the 20261101
#: dump, auea → avea, cosi → così, deue → deve among them; version 3 takes 4,
#: and a 3-letter form only for a final accent restored (cio → ciò, gia → già)
MIN_LEN = 4
#: a form shorter than SHORT must be printed at least MIN_OLD_SHORT times:
#: measured on the 20261101 dump, the 4- and 5-letter forms of foreign words
#: in these prints are rare (hein 4, heis 5, huis 3, hijs 4), the Italian ones
#: are not (hora 2,941, hore 441, hebbe 1,097, hamo 20, hara 24, haro 12)
SHORT, MIN_OLD_SHORT = 6, 10
#: a final accent restored: the one change a 3-letter form may give (cio → ciò)
_ACCENTED = "àèéìòóù"
#: printed this often a million words in the 1900s: a modern word
MODERN_PER_MILLION = 1.0
#: a form printed in the 1900s at least this share of its modern form's
#: count is a 1900s word itself: no pair
HOMOGRAPH_SHARE = 0.05
#: at most this many optional changes in one form: 2**N candidates
MAX_SITES = 8

_ANNO = re.compile(r"\|\s*Anno\s*=\s*([^\n|]*)")
_QUALITY = re.compile(r'<pagequality level="(\d)"')
_NOINCLUDE = re.compile(r"<noinclude>.*?</noinclude>", re.S)
#: a word: letters (accented included), an optional elision apostrophe
WORD = re.compile(r"[^\W\d_]+['’]?")
_VOWELS = "aeiouàèéìòóù"
#: a Roman numeral, j for the final i included (ciij, xvj): never a word to read
_ROMAN = re.compile(r"^[ivxlcdm]+j?$")
#: a "modern form" ending in two consonants is an abbreviation cut short
#: (bapt → batt, dict → ditt): no Italian word ends so
_CUT = re.compile(r"[^\Waeiouàèéìòóù'’]{2}$")
_ACCENT = {"a": "à", "e": "éè", "i": "ì", "o": "ò", "u": "ù"}


RUN = "wikisource_readings.run.json"
_RSS = "latest/itwikisource-latest-pages-articles.xml.bz2-rss.xml"


def resolve_run(ref_root: Path, transport) -> dict:
    """{base_url, rel_path, raw_name, sha1, run} of the newest complete run:
    Wikimedia's RSS for the latest pages-articles dump names its run, the
    run's sha1sums file its checksum. Both fetched on the same transport."""
    import json as _json
    from ..acquire.spec import Endpoint, RemoteFile, Transport
    base = "https://dumps.wikimedia.org/itwikisource"
    ep = Endpoint(transport=Transport.HTTPS, url=base)
    rss = Path(ref_root) / "itwikisource-pages-articles-rss.xml"
    rss.unlink(missing_ok=True)
    transport.fetch_one(ep, RemoteFile(rel_path=_RSS), rss, 0)
    m = re.search(r"itwikisource-(\d{8})-pages-articles\.xml\.bz2", rss.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{rss}: no run named")
    run = m.group(1)
    sums = Path(ref_root) / f"itwikisource-{run}-sha1sums.txt"
    sums.unlink(missing_ok=True)
    transport.fetch_one(ep, RemoteFile(rel_path=f"{run}/itwikisource-{run}-sha1sums.txt"), sums, 0)
    name = f"itwikisource-{run}-pages-articles.xml.bz2"
    sha1 = next((ln.split()[0] for ln in sums.read_text(encoding="utf-8").splitlines()
                 if ln.split()[-1:] == [name]), None)
    if sha1 is None:
        raise ValueError(f"{sums}: no checksum for {name}")
    (Path(ref_root) / RUN).write_text(_json.dumps({"run": run, "file": name, "sha1": sha1}) + "\n",
                                      encoding="utf-8")
    return {"base_url": base, "rel_path": f"{run}/{name}", "raw_name": name, "sha1": sha1, "run": run}


def run_of(ref_root: Path) -> str:
    """The run the pairs beside `RUN` come from, or "" when unrecorded."""
    import json as _json
    try:
        return str(_json.loads((Path(ref_root) / RUN).read_text(encoding="utf-8")).get("run") or "")
    except (OSError, ValueError):
        return ""


def form_of(word: str) -> str:
    return word.lower().replace("ſ", "s")


def pages(xml_bz2: Path) -> Iterator[tuple[str | None, str, str]]:
    """(namespace, title, text) of every page of a MediaWiki XML dump."""
    import xml.etree.ElementTree as ET
    for _, el in ET.iterparse(bz2.open(xml_bz2), events=("end",)):
        if el.tag.endswith("}page"):
            ns = next((c.text for c in el if c.tag.endswith("}ns")), None)
            title = next((c.text or "" for c in el if c.tag.endswith("}title")), "")
            rev = next((c for c in el if c.tag.endswith("}revision")), None)
            text = next((c.text or "" for c in rev if c.tag.endswith("}text")), "") if rev is not None else ""
            yield ns, title, text
            el.clear()


def print_years(stream) -> dict[str, int]:
    """Indice (ns 110) name -> the edition's print year, from its Anno."""
    out = {}
    for ns, title, text in stream:
        if ns == "110":
            m = _ANNO.search(text)
            y = re.search(r"1\d{3}", m.group(1)) if m else None
            if y:
                out[title.split(":", 1)[1]] = int(y.group(0))
    return out


def scan_pages(stream, years: dict[str, int], lo: int, hi: int, level: int = 3) -> Iterator[tuple[int, str]]:
    """(century of the print, text) of each scan page proofread to `level`."""
    for ns, title, text in stream:
        if ns != "108":
            continue
        y = years.get(title.split(":", 1)[1].rsplit("/", 1)[0])
        q = _QUALITY.search(text)
        if y is None or not lo <= y <= hi or not q or int(q.group(1)) < level:
            continue
        yield (y // 100) * 100, _NOINCLUDE.sub(" ", text)


def count_texts(texts: list[tuple[int, str]]) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for century, text in texts:
        for w in WORD.findall(text):
            c[(form_of(w), century)] += 1
    return c


def count_stream(dated: Iterator[tuple[int, str]], procs: int, chunk: int = 500) -> collections.Counter:
    """`count_texts` over a stream, `procs` workers, at most 2 x procs chunks in flight."""
    total: collections.Counter = collections.Counter()
    chunks = iter(lambda: list(itertools.islice(dated, chunk)), [])
    if procs <= 1:
        for ch in chunks:
            total.update(count_texts(ch))
        return total
    import multiprocessing
    from collections import deque
    with ProcessPoolExecutor(max_workers=procs, mp_context=multiprocessing.get_context("spawn")) as ex:
        window: deque = deque()
        for ch in chunks:
            window.append(ex.submit(count_texts, ch))
            if len(window) >= 2 * procs:
                total.update(window.popleft().result())
        while window:
            total.update(window.popleft().result())
    return total


def _sites(form: str) -> list[tuple[int, int, tuple[str, ...]]]:
    """(start, end, replacements) of every optional change in `form`."""
    out: list[tuple[int, int, tuple[str, ...]]] = []
    n = len(form)
    for i, ch in enumerate(form):
        nxt = form[i + 1] if i + 1 < n else ""
        prev = form[i - 1] if i else ""
        if ch == "v" and i == 0 and nxt and nxt not in _VOWELS:
            out.append((i, i + 1, ("u",)))                          # vtile, vn
        elif ch == "u" and 0 < i < n - 1 and nxt in _VOWELS and prev not in "qg" and prev:
            out.append((i, i + 1, ("v",)))                          # hauere, seruo
        elif ch == "h" and i == 0 and nxt in _VOWELS:
            out.append((i, i + 1, ("",)))                           # huomo
        elif ch == "p" and nxt == "h":
            out.append((i, i + 2, ("f",)))                          # philosophia
        elif ch == "t" and nxt == "i" and i + 2 < n and form[i + 2] in _VOWELS and prev != "s":
            out.append((i, i + 1, ("z",)))                          # oratione (not -stione)
        elif ch in "cp" and nxt == "t":
            out.append((i, i + 1, ("t",)))                          # facto, scripto
        elif ch == "j":
            out.append((i, i + 1, ("i",)))                          # gioja, principj (not y: in these
                                                                    # prints a y is a foreign word's)
    if form and form[-1] in _ACCENT and len(form) > 2:
        out.append((n - 1, n, tuple(_ACCENT[form[-1]])))           # perche
    return out[:MAX_SITES]


def variants(form: str) -> set[str]:
    """Every form with some of its old conventions undone (not the form itself)."""
    sites = _sites(form)
    out: set[str] = set()
    for choice in itertools.product(*[(None, *r) for _a, _b, r in sites]):
        if all(c is None for c in choice):
            continue
        s, last = [], 0
        for (a, b, _r), c in zip(sites, choice):
            s.append(form[last:a]); s.append(form[a:b] if c is None else c); last = b
        s.append(form[last:])
        v = "".join(s)
        if v and v != form:
            out.add(v)
    return out


def derive(counts: collections.Counter, run: str = "") -> tuple[list[dict], dict[str, int], int]:
    """(the pairs, the 1900s counts, the 1900s word total)."""
    modern: dict[str, int] = collections.Counter()
    old: dict[str, dict[int, int]] = collections.defaultdict(dict)
    for (form, century), n in counts.items():
        if century == 1900:
            modern[form] += n
        elif 1400 <= century <= 1800:
            old[form][century] = old[form].get(century, 0) + n
    total = sum(modern.values()) or 1
    floor = MODERN_PER_MILLION * total / 1e6
    rows = []
    for form, by in old.items():
        if sum(by.values()) < MIN_OLD or len(form) < 3 or _ROMAN.match(form):
            continue                                                # a slip, an abbreviation, a numeral
        best = max(((modern.get(v, 0), v) for v in variants(form)), default=(0, ""))
        if len(form) < MIN_LEN and not (best[1][:-1] == form[:-1] and best[1][-1:] in _ACCENTED):
            continue                                                # short: only a final accent (cio → ciò)
        if len(form) < SHORT and (sum(by.values()) < MIN_OLD_SHORT or form[-1] in "'’"):
            continue                                                # short and rare, or short and elided: noise
        if best[0] < floor or modern.get(form, 0) >= HOMOGRAPH_SHARE * best[0] or _CUT.search(best[1]):
            continue                                                # no modern form, a 1900s word, a cut word
        lo, hi = min(by), max(by) + 99
        rows.append({"id": readings.row_id(form, LANG, lo, hi), "orig": form, "reg": best[1],
                     "kind": readings.REPLACE, "lemma": "", "gloss": "", "lang": LANG,
                     "period_from": lo, "period_to": hi,
                     "method": f"wikisource-variants {RULES_VERSION}" + (f" {run}" if run else "")})
    rows.sort(key=lambda r: r["id"])
    return rows, dict(modern), total


def transform(raw: Path, final: Path, procs: int | None = None) -> None:
    """The dump -> the pairs (`FINAL`) and the 1900s counts (`MODERN`)."""
    from .. import parallel
    procs = parallel.processes() if procs is None else procs
    years = print_years(pages(raw))                                 # two passes: the Indici first
    counts = count_stream(scan_pages(pages(raw), years, 1400, 1999), procs)
    rows, modern, total = derive(counts, run_of(final.parent))
    tmp = final.with_name(final.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[c.name for c in TABLE.columns], delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    mod = final.with_name(MODERN)
    mtmp = mod.with_name(MODERN + ".tmp")
    with open(mtmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"#total\t{total}\n")
        for form, n in sorted(modern.items()):
            f.write(f"{form}\t{n}\n")
    mtmp.replace(mod)
    tmp.replace(final)


def modern_counts(ref_root: Path) -> tuple[dict[str, int], int]:
    """The 1900s counts `transform` wrote, or ({}, 0) before it ran."""
    path = Path(ref_root) / MODERN
    if not path.is_file():
        return {}, 0
    out, total = {}, 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            form, _, n = line.rstrip("\n").partition("\t")
            if form == "#total":
                total = int(n)
            elif n:
                out[form] = int(n)
    return out, total


class WikisourceReadingsSource:
    source_id = SOURCE_ID
    ref_version = RULES_VERSION
    license_class = LicenseClass.GREEN          # CC BY-SA 3.0: credited, shared alike
    namespaces: list[str] = []

    def schema(self) -> RefSchema:
        return RefSchema(source_id=self.source_id, tables=(TABLE,))

    def sync(self, state: RefSyncState) -> Iterator[RefRecordBatch]:
        path = Path(state.ref_root) / FINAL
        if not path.is_file():
            return
        batch: list[dict] = []
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                r["period_from"], r["period_to"] = int(r["period_from"]), int(r["period_to"])
                batch.append(r)
                if len(batch) >= 5000:
                    yield RefRecordBatch(table=TABLE.name, rows=tuple(batch)); batch = []
        if batch:
            yield RefRecordBatch(table=TABLE.name, rows=tuple(batch))
