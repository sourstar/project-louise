"""How much a Wikisource collection would fetch, per work and per book category.

    python -m tools.measure.wikisource_fetch_breakdown scan LANG --out DIR [--no-scans]
    python -m tools.measure.wikisource_fetch_breakdown sizes LANG... --out DIR
    python -m tools.measure.wikisource_fetch_breakdown categories LANG --out DIR [--top 80]

For docs/notes/wikisource-fetch-breakdown.md (operator, 2026-11-19: "since it
is a huge project I need a per book category break down of the fetch data
amount"). Measured, not inferred, from Wikimedia's own published database
dumps (dumps.wikimedia.org, `<lang>wikisource-latest-*.sql.gz`), streamed and
never stored:

- `linktarget` names what `categorylinks` and `templatelinks` point to;
- `page` gives every page's length in bytes (`page_len`, the wikitext);
- `templatelinks` says which proofread pages (the Page: namespace, one page of
  a scan each) a work's pages TRANSCLUDE — the text of a scanned work lives
  there, not in the work's own page;
- `categorylinks` gives each page's categories as rendered, template-added
  ones included (the wikitext alone would miss them).

A WORK is a top-level page of the main namespace (its subpages are its
chapters). Its TEXT is its own pages' bytes plus the bytes of every scan file
whose pages it transcludes (a file shared by several works counts for each:
anthologies are counted once per work, an over-count noted in the report).
Its SCANS are those files; their sizes come from the Commons API (`imageinfo`
size), the local wiki's for a file not on Commons.

`scan` writes DIR/<lang>.works.jsonl (one work per line: title, categories,
text bytes, files) and DIR/<lang>.files.json (file -> bytes, pages, scan
size); `categories` prints the categories by works, to choose the buckets.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

DUMPS = "https://dumps.wikimedia.org/{w}/latest/{w}-latest-{t}.sql.gz"
UA = {"User-Agent": "louise-research/1 (wikisource fetch measurement; bot)"}
#: The Page: namespace (one proofread page of a scan) per language, read from
#: each wiki's siteinfo on 2026-11-19.
PAGE_NS = {"it": 108, "en": 104, "fr": 104, "de": 102, "es": 102}
CATEGORY_NS = 14

_S = r"'((?:[^'\\]|\\.)*)'"            # an SQL string, escapes kept
_LT = re.compile(rb"\((\d+),(-?\d+)," + _S.encode() + rb"\)")
_PAGE = re.compile(rb"\((\d+),(-?\d+)," + _S.encode() + rb",(\d),(\d),[^,]+,'[^']*',(?:'[^']*'|NULL),(\d+),(\d+),")
_TL = re.compile(rb"\((\d+),(-?\d+),(\d+)\)")
_CL = re.compile(rb"\((\d+),'(?:[^'\\]|\\.)*','[^']*','(?:[^'\\]|\\.)*','(?:page|subcat|file)',\d+,(\d+)\)")


def _unescape(b: bytes) -> str:
    return re.sub(rb"\\(.)", rb"\1", b).decode("utf-8", "replace")


def _lines(lang: str, table: str) -> Iterator[bytes]:
    """The data lines of one dump table, streamed and decompressed: an INSERT
    with its tuples on one line (older dumps) or one tuple per line after it
    (the dumps of 2026)."""
    yield from _dump_lines(DUMPS.format(w=f"{lang}wikisource", t=table))


def _dump_lines(url: str) -> Iterator[bytes]:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, \
            gzip.GzipFile(fileobj=r) as g:
        for line in g:
            if line.startswith((b"INSERT INTO", b"(")):
                yield line


#: Commons' `image` table: the latest dump directory's is empty (45 bytes, a
#: failed run, 2026-11-19), so the last complete one is named.
COMMONS_IMAGE = "https://dumps.wikimedia.org/commonswiki/20261001/commonswiki-20261001-image.sql.gz"
_IMG = re.compile(rb"^\('((?:[^'\\]|\\.)*)',(\d+),")


def sizes(langs: list[str], out: Path, *, commons: str = COMMONS_IMAGE, log=print) -> None:
    """Every scan file's size, from the `image` tables MediaWiki publishes —
    each wiki's own, then Commons' — streamed ONCE for all `langs`: the same
    numbers the imageinfo API gives (img_size), without its rate limit (429
    after a few hundred requests, a batch then lost). A file on both keeps the
    local one, the one MediaWiki serves."""
    files = {l: json.loads((out / f"{l}.files.json").read_text(encoding="utf-8")) for l in langs}
    want = {n for fs in files.values() for n in fs}
    found: dict[str, dict[str, int]] = {l: {} for l in langs}
    t0 = time.time()
    for l in langs:
        for line in _lines(l, "image"):
            m = _IMG.match(line)
            if m and (n := _unescape(m.group(1))) in files[l]:
                found[l][n] = int(m.group(2))
        log(f"[{l}] local image table: {len(found[l]):,} of {len(files[l]):,} ({time.time() - t0:.0f}s)")
    rest = want - {n for f in found.values() for n in f}
    shared: dict[str, int] = {}
    for line in _dump_lines(commons):
        m = _IMG.match(line)
        if m and (n := _unescape(m.group(1))) in rest:
            shared[n] = int(m.group(2))
    log(f"commons image table: {len(shared):,} of {len(rest):,} ({time.time() - t0:.0f}s)")
    for l in langs:
        for n, f in files[l].items():
            f["scan_bytes"] = found[l].get(n, shared.get(n))
        (out / f"{l}.files.json").write_text(json.dumps(files[l], ensure_ascii=False))
        log(f"[{l}] sizes known: {sum(f['scan_bytes'] is not None for f in files[l].values()):,} of {len(files[l]):,}")


def _file_of(page_title: str) -> str:
    """'Memorie_di_X.djvu/123' -> 'Memorie_di_X.djvu' (a page of a scan file)."""
    return page_title.rsplit("/", 1)[0] if "/" in page_title else page_title


def scan(lang: str, out: Path, *, scans: bool = True, log=print) -> None:
    pns = PAGE_NS[lang]
    files: dict[str, int] = {}                     # file name -> id
    fname: list[str] = []

    def fid(name: str) -> int:
        i = files.get(name)
        if i is None:
            i = files[name] = len(fname)
            fname.append(name)
        return i

    t0 = time.time()
    cat_lt: dict[int, str] = {}
    page_lt: dict[int, int] = {}                   # Page: link target -> file id
    for line in _lines(lang, "linktarget"):
        for m in _LT.finditer(line):
            ns = int(m.group(2))
            if ns == CATEGORY_NS:
                cat_lt[int(m.group(1))] = _unescape(m.group(3))
            elif ns == pns:
                page_lt[int(m.group(1))] = fid(_file_of(_unescape(m.group(3))))
    log(f"[{lang}] linktarget: {len(cat_lt):,} categories, {len(page_lt):,} Page targets ({time.time() - t0:.0f}s)")

    works: dict[str, int] = {}
    wname: list[str] = []
    page_work: dict[int, int] = {}
    own = Counter()                                # work id -> bytes of its own pages
    file_bytes = Counter()
    file_pages = Counter()
    for line in _lines(lang, "page"):
        for m in _PAGE.finditer(line):
            ns, redirect = int(m.group(2)), m.group(4) == b"1"
            if redirect:
                continue
            length = int(m.group(7))
            title = _unescape(m.group(3))
            if ns == 0:
                top = title.split("/", 1)[0]
                w = works.get(top)
                if w is None:
                    w = works[top] = len(wname)
                    wname.append(top)
                page_work[int(m.group(1))] = w
                own[w] += length
            elif ns == pns:
                f = fid(_file_of(title))
                file_bytes[f] += length
                file_pages[f] += 1
    log(f"[{lang}] page: {len(wname):,} works, {len(file_bytes):,} scan files with proofread pages "
        f"({time.time() - t0:.0f}s)")

    work_files: dict[int, set[int]] = defaultdict(set)
    for line in _lines(lang, "templatelinks"):
        for m in _TL.finditer(line):
            if m.group(2) != b"0":
                continue
            w = page_work.get(int(m.group(1)))
            f = page_lt.get(int(m.group(3)))
            if w is not None and f is not None:
                work_files[w].add(f)
    log(f"[{lang}] templatelinks: {len(work_files):,} works transclude scans ({time.time() - t0:.0f}s)")

    work_cats: dict[int, set[str]] = defaultdict(set)
    for line in _lines(lang, "categorylinks"):
        for m in _CL.finditer(line):
            w = page_work.get(int(m.group(1)))
            c = cat_lt.get(int(m.group(2)))
            if w is not None and c is not None:
                work_cats[w].add(c)
    log(f"[{lang}] categorylinks: {len(work_cats):,} works categorised ({time.time() - t0:.0f}s)")

    used = sorted({f for fs in work_files.values() for f in fs})
    sizes = _scan_sizes(lang, [fname[f] for f in used], log) if scans else {}
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"{lang}.works.jsonl").open("w", encoding="utf-8") as fh:
        for w, title in enumerate(wname):
            fs = sorted(work_files.get(w, ()))
            fh.write(json.dumps({"title": title, "categories": sorted(work_cats.get(w, ())),
                                 "own_bytes": own[w], "page_bytes": sum(file_bytes[f] for f in fs),
                                 "files": [fname[f] for f in fs]}, ensure_ascii=False) + "\n")
    (out / f"{lang}.files.json").write_text(json.dumps(
        {fname[f]: {"page_bytes": file_bytes[f], "pages": file_pages[f], "scan_bytes": sizes.get(fname[f])}
         for f in used}, ensure_ascii=False))
    log(f"[{lang}] written ({time.time() - t0:.0f}s)")


def _scan_sizes(lang: str, names: list[str], log) -> dict[str, int]:
    """File -> bytes: Commons first, the local wiki for what Commons lacks; 50 per request."""
    sizes: dict[str, int] = {}

    def ask(api: str, batch: list[str]) -> None:
        q = urllib.parse.urlencode({"action": "query", "format": "json", "prop": "imageinfo",
                                    "iiprop": "size", "titles": "|".join("File:" + n for n in batch)})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(f"{api}?{q}", headers=UA), timeout=60) as r:
                    d = json.load(r)
                break
            except (OSError, ValueError):
                time.sleep(3 * (attempt + 1))
        else:
            return
        norm = {n["to"]: n["from"] for n in d.get("query", {}).get("normalized", [])}
        for p in d.get("query", {}).get("pages", {}).values():
            ii = p.get("imageinfo")
            if ii:
                t = norm.get(p["title"], p["title"])
                sizes[t.split(":", 1)[1].replace(" ", "_")] = ii[0]["size"]
        time.sleep(0.2)

    for api in ("https://commons.wikimedia.org/w/api.php", f"https://{lang}.wikisource.org/w/api.php"):
        todo = [n for n in names if n not in sizes]
        for i in range(0, len(todo), 50):
            ask(api, todo[i:i + 50])
        log(f"[{lang}] scan sizes after {api.split('/')[2]}: {len(sizes):,} of {len(names):,}")
    return sizes


#: Book categories, read from Wikisource's own categories by these patterns
#: (case-insensitive, on the category's name), FIRST MATCH WINS in this order:
#: fiction, poetry and theatre before anything else, as in wiki-gutenberg's
#: rule ("Novelle storiche" is fiction, "Ricordi. Sonetti" poetry). A work no
#: pattern matches is "other": Wikisource's maintenance categories (SAL, "Testi
#: del XIX secolo", "Textos corregidos") name no subject.
BUCKETS: tuple[tuple[str, str], ...] = (
    # first: an ENTRY of an encyclopaedia is reference whatever its subject
    # ("Geschichte_(Meyers_Konversations-Lexikon)", Pauly's RE; de, 2026-11-19)
    ("reference", r"_\(meyers_konversations-lexikon\)|^re:|paulys_realencyclop|encyclopædia_britannica"
                  r"|^encyclopédie,_ou|^adb:|^ndb:"
                  r"|^chroniques_(?:de_|parues)|^chronique_de_la_quinzaine|_\((?:rp|rddm)\)$"),            # a writer's newspaper columns (fr), no history
    # first: a film's page transcludes its VIDEO, and "Drama_film" is no play (en, 2026-11-19)
    ("film", r"\bfilms?\b|_films?\b|\bfilm_|stummfilm|\bfilms?_muets?"),
    ("fiction", r"novel|romanz|\broman(?:e|s)?(?:_|\b)|raccont|cuento|nouvelle|erzählung|short.stor|fiction"
                r"|fiab|fábula|fabula|\bfable|märchen|\bcontes?\b|favol|leggend|legend"),
    ("poetry", r"poes|poet|poem|poèm|sonet|sonnet|gedicht|lyrik|\brime\b|\bcanti\b|\bcanto\b|lirich|ballad|\bverse"
               r"|cantastori|chanson|\bsongs?\b|\blieder"),
    ("theatre", r"teatr|théâtre|theater|theatre|drama|dramm|comedi|commedi|comédi|tragedi|tragédi|komödie"
                r"|trauerspiel|schauspiel|\bplays\b|opera_lirica|libretto"),
    ("memoir", r"memori|mémoire|memoir|erinnerung|denkwürdig|autobiogra|selbstbiogra|\bdiari|diary|diaries"
               r"|journal_intime|tagebuch|tagebüch|ricord|souvenir|recuerdo|\blettere\b|\blettres\b|\bletters\b"
               r"|\bcartas\b|\bbriefe\b|briefwechsel|carteggi|correspond|epistol|personal_narrative"),
    ("travel", r"viagg|voyage|viaje|\breise|travel"),
    ("biography", r"biogra|\bvite\b|\bvidas\b|lebensbeschreib|nekrolog|obituar|nécrolog"),
    ("history", r"storia|storic(?!_places)|histor(?!ic_places)|geschicht|chronic|cronac|cronic|chroniq|chronik|annali|annales|anales"),
    ("law and official", r"diritto|\bleggi\b|\blegge\b|decret|costituz|constitu|\bloi\b|\blois\b|\bdroit"
                         r"|gesetz|\brecht|\bley\b|\bleyes\b|derecho|resoluci|tratad|trattat|treat|statut|ordinanz"
                         r"|\blaws?\b|_laws?\b|\bcourts?\b|_courts?\b|court_|executive_order|proclamation|public_law|_acts?_of\b|historic_places"),
    ("religion", r"bibbia|\bbible|biblia\b|_biblia|\bbibel|biblical|_of_the_bible|sermon|theolog|religi|scriptur|hymn|predig|prediche|omeli|homil|sermón|sermons"),
    ("reference", r"encyclop|cyclopæd|cyclopaed|dictionar|dizionar|diccionar|wörterbuch|lexikon|lexicon"
                  r"|enciclop|almanac|almanach|periodical|periodic|zeitschrift|magazine|newspaper|journal_of"),
)
_BUCKET_RE = [(name, re.compile(rx, re.I)) for name, rx in BUCKETS]
#: Categories that name no subject of the work: a CITATION ("Testi in cui è
#: citato il testo Viaggio in Italia" — the work cites a travel book), the
#: maintenance namespace, an academy's proceedings and a newspaper titled
#: "Memorie"/"Correspondencia" (found on it and es, 2026-11-19).
_NOT_SUBJECT = re.compile(r"^(Testi_in_cui_è_citato|Wikisource:|Pagine_|Páginas_|Pages_|Seiten_)"
                          r"|Accademia|Académie|Academia|Akademie|^La_Correspondencia", re.I)


def bucket_of(cats: list[str]) -> str:
    cats = [c for c in cats if not _NOT_SUBJECT.search(c)]
    for name, rx in _BUCKET_RE:
        if any(rx.search(c) for c in cats):
            return name
    return "other"


def breakdown(lang: str, out: Path) -> dict[str, dict[str, float]]:
    """Per book category: works, text bytes (own pages + the proofread pages of
    its scans, each scan file counted once per category), scan files and their
    bytes. Prints the five commonest categories behind each bucket."""
    files = json.loads((out / f"{lang}.files.json").read_text(encoding="utf-8"))
    rows: dict[str, dict[str, Any]] = defaultdict(lambda: {"works": 0, "own": 0, "files": set(), "cats": Counter()})
    for line in (out / f"{lang}.works.jsonl").open(encoding="utf-8"):
        w = json.loads(line)
        b = bucket_of(w["categories"])
        r = rows[b]
        r["works"] += 1
        r["own"] += w["own_bytes"]
        r["files"].update(w["files"])
        for c in w["categories"]:
            if _NOT_SUBJECT.search(c):
                continue
            for name, rx in _BUCKET_RE:
                if name == b and rx.search(c):
                    r["cats"][c] += 1
    res = {}
    for b, r in rows.items():
        fs = [files[f] for f in r["files"] if f in files]
        res[b] = {"works": r["works"], "text_bytes": r["own"] + sum(f["page_bytes"] for f in fs),
                  "scan_files": len(fs), "scan_bytes": sum(f["scan_bytes"] or 0 for f in fs),
                  "scan_unknown": sum(1 for f in fs if f["scan_bytes"] is None),
                  "top": [c for c, _ in r["cats"].most_common(5)]}
    return res


def categories(lang: str, out: Path, top: int) -> None:
    c = Counter()
    for line in (out / f"{lang}.works.jsonl").open(encoding="utf-8"):
        for cat in json.loads(line)["categories"]:
            c[cat] += 1
    for cat, n in c.most_common(top):
        print(n, cat)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("scan", "sizes", "categories", "breakdown"))
    ap.add_argument("lang", choices=sorted(PAGE_NS), nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-scans", action="store_true")
    ap.add_argument("--top", type=int, default=80)
    a = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    if a.cmd == "sizes":
        return sizes(a.lang, a.out, log=log)
    for lang in a.lang:
        if a.cmd == "scan":
            scan(lang, a.out, scans=not a.no_scans, log=log)
        elif a.cmd == "categories":
            categories(lang, a.out, a.top)
        else:
            for b, r in sorted(breakdown(lang, a.out).items(), key=lambda kv: -kv[1]["text_bytes"]):
                print(f"{lang}\t{b}\t{r['works']}\t{r['text_bytes'] / 1e6:.1f} MB text\t{r['scan_files']} scans"
                      f"\t{r['scan_bytes'] / 1e9:.2f} GB\t{'; '.join(r['top'])[:150]}")


if __name__ == "__main__":
    main()
