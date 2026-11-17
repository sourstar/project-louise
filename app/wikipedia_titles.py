"""A WIKIPEDIA PAGE BY ITS TITLE, FROM THE NODE'S OWN WIKIPEDIA COLLECTION
(2026-11-17; operator: "if there is a local wikipedia collection it is
pointless to download a wikipedia snapshot; peers can always check against
the snapshot"). What the MediaWiki API's `action=query&redirects=1&prop=
pageprops&ppprop=wikibase_item&titles=…` answered for
tools/measure/one_language_works.py, answered from the manifest, the local
dump and its page_props: the title normalised as the API normalises it, a
redirect followed to its target (a short bounded chain), the target's
Wikidata item as its record holds it. No network, ever.

ONE PASS OVER THE MANIFEST. The manifest has no index on `title`, and a
query per title would be a full scan each (52.9 M rows on the node). So one
windowed pass, rowid windows from a cursor in ranges spread over the parse
pool (`parallel.each_part`: the multi-core rule), keeps per titled page ONE
64-bit word — the CRC-32 of its language and title above, its rowid below —
sorted once: 8 bytes a page, 423 MB at 52.9 M. Every title, the wanted ones
and the redirect targets found afterwards, is then a binary search and a
read of its few candidate rows by rowid (the primary key), so a redirect
costs no second pass. A CRC collision only adds a candidate, dropped when
its row's title is read.

WHAT IT CANNOT ANSWER THAT THE API COULD, said per title (`UNKNOWN`, never
taken for "no such page"): a wiki the collection does not hold (its eleven
languages, app/plugins/wikipedia/ids.py), and a title not found in a
language some of whose pages have no title yet (the rdf stage has not read
them). A page with no item and no redirect is `NONE`, as the API's answer
would be.

Standardised alternative, not taken: a full-text index over the titles
(the catalog's tantivy index, app/catalog.py) answers by token, not by the
exact title, and lives in the worker's process; an SQLite index on
`documents(title)` would be a schema change on a 52.9 M-row manifest for one
monthly pass. It would pay if titles were looked up at search time.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
import zlib
from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: rows read per window of the pass
WINDOW = 50_000
#: rowids per task of the pass, at least (fewer tasks than the pool's twice
#: its size are wasted on start-up)
TASK_ROWS = 250_000
#: redirects followed from one title at most (MediaWiki resolves chains)
MAX_HOPS = 3
#: rowids looked up per query
LOOKUP = 500
ITEM, NONE, UNKNOWN = "item", "none", "unknown"

#: the pass: one window of a rowid range, by the primary key
SCAN_SQL = ("SELECT rowid, native_id, title FROM documents WHERE rowid > ? AND rowid <= ? "
            "ORDER BY rowid LIMIT ?")
#: what a candidate row is read for
ROW_SQL = "SELECT rowid, native_id, title, path, fts_done, chunks FROM documents WHERE rowid IN ({})"

#: MediaWiki's title whitespace (Title::newFromText: `MediaWikiTitleCodec`),
#: collapsed to one space, and the directional marks it strips
_SPACES = re.compile("[ _\u00a0\u1680\u180e\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")
_MARKS = re.compile("[\u200e\u200f\u202a-\u202e]")
_LOW32 = np.uint64(0xFFFFFFFF)


def normalise(title: str) -> str:
    """The title as MediaWiki stores it, the API's `normalized` step: NFC,
    underscores and its spaces as one space, directional marks dropped,
    trimmed, the first letter upper-cased (`$wgCapitalLinks`, which every
    Wikipedia of the collection keeps; a letter whose capital is two letters,
    `ß`, is kept)."""
    t = _MARKS.sub("", unicodedata.normalize("NFC", str(title)))
    t = _SPACES.sub(" ", t).strip()
    if t:
        up = t[0].upper()
        if len(up) == 1:
            t = up + t[1:]
    return t


def _ordinal(native_id: int) -> int:
    from .plugins.wikipedia import ids
    rest = native_id - ids.DUMP_BASE if native_id >= ids.DUMP_BASE else native_id
    return rest // ids.LANG_BASE


def title_hash(ordinal: int, title: str) -> int:
    """The 32 bits a title is found by: its language and its title."""
    return zlib.crc32(f"{ordinal}\x1f{title}".encode())


def _ro(manifest: str | Path) -> sqlite3.Connection:
    # as_uri(): a `#` or `?` in a path cut a raw URI short (db.read_only_connect)
    return sqlite3.connect(Path(manifest).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)


def scan_range(manifest: str, lo: int, hi: int) -> tuple[bytes, dict[int, int], dict[int, int]]:
    """A task of the pass (module level: it runs in the parse pool): the
    rows of rowid in (lo, hi], each titled page as one word — `title_hash`
    above, the rowid below — and per language the pages with a title and
    the pages without one yet."""
    from .plugins.wikipedia import ids
    con = _ro(manifest)
    words = array("Q")
    titled: dict[int, int] = {}
    untitled: dict[int, int] = {}
    crc, dump_base, lang_base = zlib.crc32, ids.DUMP_BASE, ids.LANG_BASE    # the per-row loop's own names
    try:
        cursor = lo
        while cursor < hi:
            rows = con.execute(SCAN_SQL, (cursor, hi, WINDOW)).fetchall()
            if not rows:
                break
            for rowid, nid, title in rows:
                try:
                    n = int(nid)
                except (TypeError, ValueError):
                    continue                     # a container's row: no page
                o = (n - dump_base if n >= dump_base else n) // lang_base      # `_ordinal`, inlined
                if not title:
                    untitled[o] = untitled.get(o, 0) + 1
                    continue
                if rowid > 0xFFFFFFFF:
                    raise ValueError(f"{manifest}: rowid {rowid} does not fit the pass's 32 bits")
                words.append((crc(f"{o}\x1f{title}".encode()) << 32) | rowid)
                titled[o] = titled.get(o, 0) + 1
            cursor = int(rows[-1][0])
    finally:
        con.close()
    return words.tobytes(), titled, untitled


@dataclass
class TitleIndex:
    """The pass's result: every titled page's word, sorted, and the pages
    with and without a title per language ordinal."""
    manifest: Path
    words: np.ndarray
    titled: dict[int, int]
    untitled: dict[int, int]

    def rowids(self, ordinal: int, title: str) -> list[int]:
        h = np.uint64(title_hash(ordinal, title)) << np.uint64(32)
        lo = int(np.searchsorted(self.words, h, side="left"))
        hi = int(np.searchsorted(self.words, h | _LOW32, side="right"))
        return [int(w) for w in (self.words[lo:hi] & _LOW32)]


def index(manifest: str | Path, *, log=print, name: str | None = None) -> TitleIndex:
    """The ONE pass over the manifest (rowid ranges on the parse pool);
    `name`, the collection's, for the log line."""
    from . import parallel
    manifest = Path(manifest)
    con = _ro(manifest)
    try:
        top = int(con.execute("SELECT COALESCE(MAX(rowid), 0) FROM documents").fetchone()[0])
    finally:
        con.close()
    n = max(1, min(-(-top // TASK_ROWS), 4 * parallel.processes()))
    step = -(-top // n) if top else 1
    tasks = [(str(manifest), lo, min(lo + step, top)) for lo in range(0, top, step)]
    words = np.empty(top, dtype=np.uint64)
    filled = 0
    titled: dict[int, int] = {}
    untitled: dict[int, int] = {}
    for got, t, u in parallel.each_part(scan_range, tasks):
        part = np.frombuffer(got, dtype=np.uint64)
        words[filled:filled + len(part)] = part
        filled += len(part)
        for src, dst in ((t, titled), (u, untitled)):
            for o, k in src.items():
                dst[o] = dst.get(o, 0) + k
    words = words[:filled]
    words.sort()
    log(f"[wikipedia titles] {name or manifest}: {filled:,} titled pages read in {len(tasks)} range(s)"
        + (f"; {sum(untitled.values()):,} without a title yet" if untitled else ""))
    return TitleIndex(manifest, words, titled, untitled)


@dataclass(frozen=True)
class Page:
    rowid: int
    native_id: int
    title: str
    locator: str
    has_text: bool        # indexed with chunks: an article, not a redirect


#: why a title answers no item, in a word or two (`Resolved.code`)
NOT_HELD, NOT_READ, UNREADABLE = "wiki not held", "not read yet", "unreadable"
NO_TITLE, NO_PAGE, NO_ITEM, LOOP = "no title", "no page", "no item", "redirect loop"


@dataclass(frozen=True)
class Resolved:
    """What a title answers: `ITEM` and the item, `NONE` (no such page, or
    a page with no item — what the API answers too), or `UNKNOWN` (this node
    cannot tell); `code` and `why` say why in a word and in a sentence; the
    page it reached and the redirects followed."""
    kind: str
    item: str | None
    code: str
    why: str
    page: str | None = None
    redirects: int = 0


def pages(con: sqlite3.Connection, idx: TitleIndex, wanted: Iterable[tuple[int, str]]) -> dict[tuple[int, str], Page]:
    """The page of each (language ordinal, normalised title) the manifest
    holds: its candidates by `rowids`, read by rowid, the one whose own
    language and title are those. Two rows of one title (a page renamed and
    not read again yet): the later row."""
    wanted = set(wanted)
    cand: dict[int, list[tuple[int, str]]] = {}
    for key in wanted:
        for r in idx.rowids(*key):
            cand.setdefault(r, []).append(key)
    out: dict[tuple[int, str], Page] = {}
    ids = sorted(cand)
    for i in range(0, len(ids), LOOKUP):
        batch = ids[i:i + LOOKUP]
        for rowid, nid, title, path, fts_done, chunks in con.execute(
                ROW_SQL.format(",".join("?" * len(batch))), batch):
            try:
                n = int(nid)
            except (TypeError, ValueError):
                continue
            key = (_ordinal(n), title)
            if key not in wanted:
                continue                         # a CRC collision
            p = Page(int(rowid), n, title, str(path), bool(fts_done) and bool(chunks))
            if key not in out or p.rowid > out[key].rowid:
                out[key] = p
    return out


def resolve(manifest: str | Path, wanted: Iterable[tuple[str, str]], *, idx: TitleIndex | None = None,
            log=print, name: str | None = None) -> dict[tuple[str, str], Resolved]:
    """Each (language code, title as a URL gave it) → `Resolved`: the title
    normalised, its page found, a redirect followed to its target (at most
    `MAX_HOPS`, a loop is `NONE`), the item read from the page_props unit
    the page's locator names (app/plugins/wikipedia/source.py `item_of`) —
    the page read from the local dump only when the manifest does not say
    it holds text (`redirect_of`)."""
    from .plugins.wikipedia import ids, source
    wanted = sorted(set(wanted))
    if idx is None and any(lang in ids.LANG_ORDINAL for lang, _ in wanted):
        idx = index(manifest, log=log, name=name)     # no pass for wikis the collection cannot hold
    held = set(idx.titled) | set(idx.untitled) if idx is not None else set()
    out: dict[tuple[str, str], Resolved] = {}
    todo: dict[tuple[str, str], tuple[int, str, int, frozenset]] = {}
    for lang, title in wanted:
        o = ids.LANG_ORDINAL.get(lang)
        if o is None or o not in held:
            out[(lang, title)] = Resolved(UNKNOWN, None, NOT_HELD,
                                          f"the node's wikipedia holds no {lang or '?'} wiki")
            continue
        t = normalise(title)
        if not t:
            out[(lang, title)] = Resolved(NONE, None, NO_TITLE, "no title")
            continue
        todo[(lang, title)] = (o, t, 0, frozenset())
    if not todo:
        return out
    con = _ro(manifest)
    try:
        while todo:
            found = pages(con, idx, {(o, t) for o, t, _, _ in todo.values()})
            nxt: dict[tuple[str, str], tuple[int, str, int, frozenset]] = {}
            for key, (o, t, hops, seen) in todo.items():
                p = found.get((o, t))
                if p is None:
                    if idx.untitled.get(o):
                        out[key] = Resolved(UNKNOWN, None, NOT_READ,
                                            f"no {key[0]} page titled {t!r} among those read; "
                                            f"{idx.untitled[o]:,} have no title yet", redirects=hops)
                    else:
                        out[key] = Resolved(NONE, None, NO_PAGE, f"no {key[0]} page titled {t!r}",
                                            redirects=hops)
                    continue
                uid = f"wikipedia:{p.native_id}"
                try:
                    to = None if p.has_text else source.redirect_of(p.locator, p.native_id)
                    if to is None:
                        q = source.item_of(p.locator, p.native_id)
                        out[key] = (Resolved(ITEM, q, "", f"{uid}'s page_props", uid, hops) if q else
                                    Resolved(NONE, None, NO_ITEM, f"{uid} names no Wikidata item", uid, hops))
                        continue
                except (OSError, RuntimeError, ValueError, SyntaxError) as e:   # ParseError is a SyntaxError
                    out[key] = Resolved(UNKNOWN, None, UNREADABLE,
                                        f"{uid} could not be read here: {type(e).__name__}", uid, hops)
                    continue
                t2 = normalise(to)
                if hops + 1 > MAX_HOPS or (o, t2) in seen or t2 == t:
                    out[key] = Resolved(NONE, None, LOOP,
                                        f"{uid}: a redirect chain longer than {MAX_HOPS} or a loop", uid, hops)
                    continue
                nxt[key] = (o, t2, hops + 1, seen | {(o, t)})
            todo = nxt
    finally:
        con.close()
    return out
