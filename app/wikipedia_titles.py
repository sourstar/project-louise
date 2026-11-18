"""A WIKIPEDIA PAGE BY ITS TITLE, FROM THE NODE'S OWN WIKIPEDIA COLLECTION
(2026-11-17; operator: "if there is a local wikipedia collection it is
pointless to download a wikipedia snapshot; peers can always check against
the snapshot"). What the MediaWiki API's `action=query&redirects=1&prop=
pageprops&ppprop=wikibase_item&titles=…` answered for
tools/measure/one_language_works.py, answered from the run's own index and
dump, the manifest and its page_props: the title normalised as the API
normalises it, a redirect followed to its target (a short bounded chain),
the target's Wikidata item as its record holds it. No network, ever.

THE RUN IS AUTHORITATIVE FOR TITLES (2026-11-17, the node's dry run: 83
claims withdrawn, among them eight books through the enwiki redirect
"Journey_to_the_Centre_of_the_Earth", which the manifest holds no row for,
and "Bouvard and Pécuchet", page 1078856, which layer 1 holds under the
archive's 2024 title "Bouvard et Pécuchet"). Where a wiki's run has its
multistream index on disk (`offset:page_id:title`, every page of the run,
redirects included), EVERY wanted title of that wiki is looked up there —
one pass per language, set membership, the languages in parallel on the
parse pool (`look_up`) — and the page is read from the manifest by its id,
in either layer (`by_page`, the primary key):

- absent from the index: `NONE`, "no page" — the run has no page so titled;
- present, and held with its text: its item, as its row's page_props unit
  holds it (`BY_PAGE_ID` where the manifest holds it under another title —
  layer 1's are the archive's, 2024-06 — that title said);
- present and a redirect: the page read from the run's dump at the index's
  offset (`redirect_of`, the plugin's stream reader, read-only) and its
  `<redirect title>` followed, within `MAX_HOPS` and the loop rule;
- present, not a redirect, held without text: its item as above;
- present, not a redirect, not held: `UNKNOWN`, "held by the run, not read
  yet".

A 2024 title the run now gives to another page answers with THAT page, the
run's — never with the page layer 1 held under it.

THE MANIFEST'S TITLES, ONLY WHERE THE RUN'S INDEX IS NOT ON DISK (the
fallback). The manifest has no index on `title`, and a query per title
would be a full scan each (52.9 M rows on the node). So one windowed pass,
rowid windows from a cursor in ranges spread over the parse pool
(`parallel.each_part`: the multi-core rule), keeps per titled page ONE
64-bit word — the CRC-32 of its language and title above, its rowid below —
sorted once: 8 bytes a page, 423 MB at 52.9 M; every title is then a binary
search and a read of its few candidate rows by rowid. A CRC collision only
adds a candidate, dropped when its row's title is read. A title found there
answers as it always did; one missing is `UNKNOWN`, "no dump to tell" (an
index cut short: "unreadable"). Where every wanted wiki has its index, the
pass is not made.

WHAT IT CANNOT ANSWER THAT THE API COULD, said per title (`UNKNOWN`, never
taken for "no such page"): a wiki the collection does not hold (its eleven
languages, app/plugins/wikipedia/ids.py), and the cases above. A page with
no item and no redirect is `NONE`, as the API's answer would be. Every
answer names the run it is the run's answer for (`Resolved.run`): an answer
from a run older than the claim it would change is the caller's to weigh
(app/gutenberg_editions.py).

Standardised alternative, not taken: a full-text index over the titles
(the catalog's tantivy index, app/catalog.py) answers by token, not by the
exact title, and lives in the worker's process; an SQLite index on
`documents(title)` would be a schema change on a 52.9 M-row manifest for one
monthly pass. It would pay if titles were looked up at search time.
"""
from __future__ import annotations

import queue
import re
import sqlite3
import sys
import threading
import time
import unicodedata
import zlib
from array import array
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
#: a page the run's index names, read by its uid (the primary key)
UID_SQL = "SELECT rowid, native_id, title, path, fts_done, chunks FROM documents WHERE uid IN ({})"
#: the compressed bytes an index pass decompresses a call (`blocks`): about
#: 5 MB of lines from a Wikimedia index
RAW = 1 << 20
#: the GIL's switch interval while a pass reads (`blocks`)
SWITCH_S = 1e-4
#: a run index's lines matched at a time (`RunIndex.find`)
FIND_LINES = 1 << 22
#: pages of the dump read per task (a stream is decompressed for each)
READS = 64

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
NOT_HELD, NOT_READ, UNREADABLE = "wiki not held", "held by the run, not read yet", "unreadable"
NO_TITLE, NO_PAGE, NO_ITEM, LOOP = "no title", "no page", "no item", "redirect loop"
NO_DUMP = "no dump to tell"
#: an item found through the run's index by the page's id: the manifest holds
#: the page under another title (layer 1's titles are the archive's, 2024-06)
BY_PAGE_ID = "by page id"


@dataclass(frozen=True)
class Resolved:
    """What a title answers: `ITEM` and the item, `NONE` (no such page, or
    a page with no item — what the API answers too), or `UNKNOWN` (this node
    cannot tell); `code` and `why` say why in a word and in a sentence; the
    page it reached and the redirects followed; `run`, the run of the wiki
    whose answer it is (its index, its page_props), None where none is known;
    `stored`, the title the manifest holds the page under where the run's
    index found it by its id instead."""
    kind: str
    item: str | None
    code: str
    why: str
    page: str | None = None
    redirects: int = 0
    run: str | None = None
    stored: str | None = None


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


# --------------------------------------------------------------------------
# the run's own index and dump (the module's docstring: "no page" only on proof)

def run_dumps(corpus: str | Path | None, run: str | None) -> dict[str, Path]:
    """Language → the run's pages-articles multistream dump in the corpus, as
    discovery picks it (app/plugins/wikipedia/source.py `discover`): under
    `<wiki>/<run>/` where the collection holds a run, the one dump of each
    wiki otherwise. A wiki with two dumps is left out: discovery refuses the
    second, and which is the run's is not guessed."""
    if corpus is None or not Path(corpus).is_dir():
        return {}
    from .plugins import walk
    from .plugins.wikipedia import ids, source
    found: dict[str, list[Path]] = {}
    for d in walk.files(corpus, f"*{source._DUMP_SUFFIX}"):
        if run and d.parent.name != run:
            continue
        found.setdefault(source._lang_of(d.name), []).append(d)
    return {lang: ds[0] for lang, ds in sorted(found.items()) if len(ds) == 1 and lang in ids.LANG_ORDINAL}


def index_path(dump: Path) -> Path:
    """The multistream index beside a dump (`<wiki>-<run>-pages-articles-multistream-index.txt.bz2`)."""
    from .plugins.wikipedia import source
    return dump.with_name(dump.name.replace(source._DUMP_SUFFIX, source._INDEX_SUFFIX))


def _run_of(dump: Path | None, run: str | None) -> str | None:
    """The run an answer is the run's: the collection's, else its dump's directory (`YYYYMMDD`)."""
    from .hold.origin_run import RUN_RE
    if run:
        return run
    return dump.parent.name if dump is not None and RUN_RE.match(dump.parent.name) else None


def blocks(path: str | Path) -> Iterator[bytes]:
    """A .bz2 file's bytes, decompressed, in blocks that end at a line's end
    — decompressed in a thread of its own, so the lines are read meanwhile:
    most of a pass is the decompression (8.0 s of 10.0 on a synthetic 5 M-line
    index, 2026-11-17). `BZ2Decompressor` is fed `RAW` compressed bytes a
    call, so it releases the GIL for each whole chunk (`bz2.open` decompresses
    8 KiB a call, and its thread fought the line loop for the GIL: no
    overlap at all, measured). Concatenated streams are read as `bz2.open`
    reads them, trailing bytes that start no stream ignored, a stream cut
    short an EOFError. The thread is ours and reads a local file; a wait for
    it is bounded by its liveness, never open-ended."""
    import bz2
    q: queue.Queue = queue.Queue(maxsize=2)
    stop = threading.Event()

    def read() -> None:
        try:
            with open(path, "rb") as f:
                d, streams, fed = bz2.BZ2Decompressor(), 0, False
                while not stop.is_set():
                    raw = f.read(RAW)
                    if not raw:
                        if fed and not d.eof:
                            raise EOFError("compressed file ended before the end-of-stream marker was reached")
                        q.put(b"")
                        return
                    while raw and not stop.is_set():
                        try:
                            out = d.decompress(raw)
                        except OSError:
                            if streams and not fed:
                                q.put(b"")        # trailing bytes after the last stream, as bz2.open ignores them
                                return
                            raise
                        fed = True
                        if out:
                            q.put(out)
                        if d.eof:
                            raw, d, streams, fed = d.unused_data, bz2.BZ2Decompressor(), streams + 1, False
                        else:
                            raw = b""
        except BaseException as e:      # noqa: BLE001 — handed to the reader's caller, raised there
            q.put(e)
    t = threading.Thread(target=read, name="bz2-blocks", daemon=True)
    t.start()
    tail = b""
    # the reader takes the GIL back after each piece of a call's output; at the
    # default 5 ms switch interval it waited on the line loop for most of the
    # overlap (look_up on 5 M lines: 10.3 s, 8.0 s of it decompression alone).
    # Restored when the pass ends
    switch = sys.getswitchinterval()
    sys.setswitchinterval(SWITCH_S)
    try:
        while True:
            try:
                b = q.get(timeout=1.0)
            except queue.Empty:
                if not t.is_alive() and q.empty():
                    raise RuntimeError(f"{path}: its reader ended without an end") from None
                continue
            if isinstance(b, BaseException):
                raise b
            if not b:
                if tail:
                    yield tail
                return
            b = tail + b if tail else b
            cut = b.rfind(b"\n") + 1
            tail = b[cut:]
            if cut:
                yield b[:cut]
    finally:
        sys.setswitchinterval(switch)
        stop.set()
        while t.is_alive():
            try:
                q.get(timeout=0.2)
            except queue.Empty:
                pass


def index_forms(title: str) -> set[str]:
    """The title as an index line may hold it: as MediaWiki stores it, and
    as the dump's `<title>` escapes it (`&amp;`, `&quot;`, an apostrophe in
    any of its entity forms). Whichever the index writes, the title is found;
    a title cannot itself hold an entity (MediaWiki decodes them), so no
    form is another page's."""
    forms = {title}
    if any(c in title for c in "&\"'<>"):
        amp = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        quot = amp.replace('"', "&quot;")
        forms |= {amp, quot, *(f.replace("'", e) for f in (amp, quot) for e in ("&#039;", "&#39;", "&apos;"))}
    return forms


@dataclass
class RunIndex:
    """A run's multistream index, held for the lookups of one pass: per
    line the 64-bit `hash` of its title's bytes (this process's own: the
    table never leaves it) and its page id; per bz2 stream of the dump its
    first line and its offset. 12 bytes a line: 288 MB for enwiki's 24 M
    lines, so a redirect's target costs a lookup, not a second pass."""
    hashes: np.ndarray            # int64
    pages: np.ndarray             # uint32
    first: np.ndarray             # int64, a stream's first line
    offsets: np.ndarray           # int64, a stream's offset in the dump
    lines: int

    def find(self, titles: Iterable[str]) -> dict[str, tuple[int, int, int]]:
        """Each title of `titles` the index lists (any of its `index_forms`)
        → (its page id, its stream's start, its end: the next stream's
        offset, 0 for the last, as discovery writes a locator). Two lines of
        one title: the later."""
        form_of: dict[int, str] = {}
        for t in sorted(set(titles)):
            for f in sorted(index_forms(t)):
                form_of.setdefault(hash(f.encode("utf-8")), t)
        found: dict[str, tuple[int, int, int]] = {}
        if not form_of or not self.lines:
            return found
        want = np.array(sorted(form_of), dtype=np.int64)
        hit: list[np.ndarray] = []
        for lo in range(0, self.lines, FIND_LINES):         # bounded transient: FIND_LINES × 8 bytes
            h = self.hashes[lo:lo + FIND_LINES]
            at = np.minimum(np.searchsorted(want, h), len(want) - 1)
            hit.append(np.flatnonzero(want[at] == h) + lo)
        for line in np.concatenate(hit):
            s = int(np.searchsorted(self.first, line, side="right")) - 1
            end = int(self.offsets[s + 1]) if s + 1 < len(self.offsets) else 0
            found[form_of[int(self.hashes[line])]] = (int(self.pages[line]), int(self.offsets[s]), end)
        return found


def run_index(index: str | Path) -> RunIndex:
    """ONE pass over a run's index (`blocks`: decompressed in a thread of its
    own) into a `RunIndex`. Raises what the read raises: an index cut short
    is an EOFError, a line that is not `offset:page_id:title` a ValueError."""
    hs, ps, first, offs = array("q"), array("I"), array("q"), array("q")
    last: int | None = None
    n = 0
    for block in blocks(index):
        rows = block.split(b"\n")
        if rows and not rows[-1]:
            rows.pop()
        parts = [r.split(b":", 2) for r in rows]
        parts = [p for p in parts if len(p) == 3]
        if not parts:
            continue
        o = np.fromiter(map(int, (p[0] for p in parts)), np.int64, len(parts))
        pg = np.fromiter(map(int, (p[1] for p in parts)), np.int64, len(parts))
        if int(pg.max()) > 0xFFFFFFFF or int(pg.min()) < 0:
            raise ValueError(f"{Path(index).name}: a page id outside 32 bits")
        new = np.empty(len(o), dtype=bool)
        new[0] = last is None or int(o[0]) != last
        new[1:] = o[1:] != o[:-1]
        at = np.flatnonzero(new)
        first.frombytes((at + n).astype(np.int64).tobytes())
        offs.frombytes(o[at].tobytes())
        hs.frombytes(np.fromiter(map(hash, (p[2] for p in parts)), np.int64, len(parts)).tobytes())
        ps.frombytes(pg.astype(np.uint32).tobytes())
        last = int(o[-1])
        n += len(parts)
    return RunIndex(np.frombuffer(hs, dtype=np.int64), np.frombuffer(ps, dtype=np.uint32),
                    np.frombuffer(first, dtype=np.int64), np.frombuffer(offs, dtype=np.int64), n)


def look_up(index: str, titles: list[str]) -> tuple[dict[str, tuple[int, int, int]], int, str | None]:
    """Each of `titles` the run's index lists (`RunIndex.find`), the lines
    read, and why the index could not be read (None when it was)."""
    try:
        ix = run_index(index)
    except (OSError, EOFError, ValueError, RuntimeError) as e:
        return {}, 0, f"{Path(index).name} could not be read: {type(e).__name__}: {e}"[:300]
    return ix.find(titles), ix.lines, None


def read_pages(dump: str, lang: str, wanted: list[tuple[int, int, int]]) -> list[tuple[int, str | None, str | None]]:
    """Each (page id, its stream's start, end) read from the run's dump by
    the plugin's own reader (`source.redirect_of`, read-only), in the
    dump's order — (page id, the title its redirect points at or None for
    an article, None), or (page id, None, why it could not be read)."""
    from .plugins.wikipedia import ids, source
    out: list[tuple[int, str | None, str | None]] = []
    for pid, start, end in sorted(wanted, key=lambda w: (w[1], w[0])):
        locator = f"{dump}{source._LOCATOR_SEP}{start}{source._LOCATOR_SEP}{end}"
        try:
            out.append((pid, source.redirect_of(locator, ids.native_id_of(lang, pid)), None))
        except (OSError, EOFError, RuntimeError, ValueError, SyntaxError) as e:   # ParseError is a SyntaxError
            out.append((pid, None, type(e).__name__))
    return out


def by_page(con: sqlite3.Connection, wanted: Iterable[tuple[str, int]]) -> dict[tuple[str, int], Page]:
    """The row the manifest holds for each (language, page id), in either
    layer — by uid, the primary key, `LOOKUP` uids a query: a row with text
    before one without, then the later row."""
    from .plugins.wikipedia import ids
    of: dict[str, tuple[str, int]] = {}
    for lang, pid in set(wanted):
        for archive in (True, False):
            of[f"wikipedia:{ids.native_id_of(lang, pid, archive=archive)}"] = (lang, pid)
    out: dict[tuple[str, int], Page] = {}
    uids = sorted(of)
    for i in range(0, len(uids), LOOKUP):
        batch = uids[i:i + LOOKUP]
        for rowid, nid, title, path, fts_done, chunks in con.execute(
                UID_SQL.format(",".join("?" * len(batch))), batch):
            key = of.get(f"wikipedia:{nid}")
            if key is None:
                continue
            p = Page(int(rowid), int(nid), title or "", str(path), bool(fts_done) and bool(chunks))
            q = out.get(key)
            if q is None or (p.has_text, p.rowid) > (q.has_text, q.rowid):
                out[key] = p
    return out


def _item(p: Page, t: str, hops: int, run: str | None) -> Resolved:
    """`p`'s item as its row's page_props unit holds it; the manifest's own
    title said where it holds one and it is not the run's `t`."""
    from .plugins.wikipedia import source
    uid = f"wikipedia:{p.native_id}"
    stored = p.title if p.title and p.title != t else None
    q = source.item_of(p.locator, p.native_id)
    if stored is not None and q:
        return Resolved(ITEM, q, BY_PAGE_ID, f"{uid}'s page_props, the run naming it {t!r} and the manifest "
                                             f"{stored!r}", uid, hops, run, stored)
    return (Resolved(ITEM, q, "", f"{uid}'s page_props", uid, hops, run) if q else
            Resolved(NONE, None, NO_ITEM, f"{uid} names no Wikidata item", uid, hops, run, stored))


def _follow(to: str, t: str, o: int, hops: int, seen: frozenset, uid: str, run: str | None):
    """A redirect from `t` to `to`: the next hop, or `NONE` for a chain longer
    than `MAX_HOPS` or a loop."""
    t2 = normalise(to)
    if hops + 1 > MAX_HOPS or (o, t2) in seen or t2 == t:
        return Resolved(NONE, None, LOOP, f"{uid}: a redirect chain longer than {MAX_HOPS} or a loop", uid, hops, run)
    return (o, t2, hops + 1, seen | {(o, t)})


_READ_ERRORS = (OSError, EOFError, RuntimeError, ValueError, SyntaxError)    # ParseError is a SyntaxError


def resolve_by_run(manifest: str, dump: str, lang: str, run: str | None,
                   titles: list[tuple[str, str]]) -> tuple[dict[str, Resolved] | None, dict[str, Any]]:
    """A task (module level: the parse pool), ONE wiki answered by its run:
    each (title as given, normalised) → `Resolved`, the module's docstring's
    rules — ONE pass over the run's index (`run_index`), every hop a lookup
    in it, the pages read by their id (`by_page`), the redirects among them
    read from the run's dump (`read_pages`) — and what it cost; (None,
    {"error": …}) when the index cannot be read, for the manifest's titles
    to answer instead."""
    from .plugins.wikipedia import ids
    t0 = time.monotonic()
    try:
        ix = run_index(index_path(Path(dump)))
    except (OSError, EOFError, ValueError, RuntimeError) as e:
        return None, {"error": f"{index_path(Path(dump)).name} could not be read: {type(e).__name__}: {e}"[:300]}
    stats: dict[str, Any] = {"lines": ix.lines, "pass_s": round(time.monotonic() - t0, 1), "rounds": 0,
                             "by_id": 0, "by_id_s": 0.0, "read": 0, "read_s": 0.0}
    o = ids.LANG_ORDINAL[lang]
    out: dict[str, Resolved] = {}
    todo = {title: (o, t, 0, frozenset()) for title, t in titles}
    con = _ro(manifest)
    try:
        while todo:
            stats["rounds"] += 1
            hits = ix.find(t for _, t, _, _ in todo.values())
            t1 = time.monotonic()
            rows = by_page(con, {(lang, h[0]) for h in hits.values()})
            stats["by_id"] += len(rows)
            stats["by_id_s"] += time.monotonic() - t1
            need = sorted({h for h in hits.values() if not (rows.get((lang, h[0])) and rows[(lang, h[0])].has_text)},
                          key=lambda h: (h[1], h[0]))
            t1 = time.monotonic()
            reads = {pid: (to, err) for pid, to, err in read_pages(dump, lang, need)}
            stats["read"] += len(need)
            stats["read_s"] += time.monotonic() - t1
            nxt: dict[str, tuple[int, str, int, frozenset]] = {}
            for title, (o_, t, hops, seen) in todo.items():
                h = hits.get(t)
                if h is None:
                    out[title] = Resolved(NONE, None, NO_PAGE, f"no {lang} page titled {t!r} in the run's index "
                                                               f"({run or 'its run'})", redirects=hops, run=run)
                    continue
                row = rows.get((lang, h[0]))
                uid = f"wikipedia:{ids.native_id_of(lang, h[0])}"
                try:
                    if row is not None and row.has_text:
                        out[title] = _item(row, t, hops, run)
                        continue
                    to, err = reads.get(h[0], (None, "not read"))
                    if err is not None:
                        out[title] = Resolved(UNKNOWN, None, UNREADABLE, f"{uid}, the run's {t!r}, could not be "
                                                                         f"read from its dump: {err}", uid, hops, run)
                        continue
                    if to is None:
                        out[title] = (_item(row, t, hops, run) if row is not None else
                                      Resolved(UNKNOWN, None, NOT_READ, f"{uid}, the run's {t!r}, is held by the run "
                                                                        f"and not read yet", uid, hops, run))
                        continue
                except _READ_ERRORS as e:
                    out[title] = Resolved(UNKNOWN, None, UNREADABLE, f"{uid} could not be read here: "
                                                                     f"{type(e).__name__}", uid, hops, run)
                    continue
                step = _follow(to, t, o_, hops, seen, uid, run)
                if isinstance(step, Resolved):
                    out[title] = step
                else:
                    nxt[title] = step
            todo = nxt
    finally:
        con.close()
    stats["by_id_s"] = round(stats["by_id_s"], 2)
    stats["read_s"] = round(stats["read_s"], 1)
    stats["seconds"] = round(time.monotonic() - t0, 1)
    return out, stats


def _resolve_by_manifest(con: sqlite3.Connection, idx: TitleIndex, todo: dict, runs: Mapping[str, str | None],
                         cannot: Mapping[str, tuple[str, str]]) -> dict[tuple[str, str], Resolved]:
    """THE FALLBACK, for a wiki whose run's index is not on disk (or cannot be
    read): the manifest's titles (`pages`), a redirect followed from its own
    row (`redirect_of`), a title it misses `UNKNOWN` for the reason
    `cannot` gives its language."""
    from .plugins.wikipedia import source
    out: dict[tuple[str, str], Resolved] = {}
    while todo:
        found = pages(con, idx, {(o, t) for o, t, _, _ in todo.values()})
        nxt: dict = {}
        for key, (o, t, hops, seen) in todo.items():
            lang, r = key[0], runs[key[0]]
            p = found.get((o, t))
            if p is None:
                code, why = cannot.get(lang, (NO_DUMP, f"no {lang} dump of the run is on disk"))
                out[key] = Resolved(UNKNOWN, None, code, f"no {lang} page titled {t!r} in the manifest, and {why}",
                                    redirects=hops, run=r)
                continue
            uid = f"wikipedia:{p.native_id}"
            try:
                to = None if p.has_text else source.redirect_of(p.locator, p.native_id)
                if to is None:
                    out[key] = _item(p, t, hops, r)
                    continue
            except _READ_ERRORS as e:
                out[key] = Resolved(UNKNOWN, None, UNREADABLE, f"{uid} could not be read here: {type(e).__name__}",
                                    uid, hops, r)
                continue
            step = _follow(to, t, o, hops, seen, uid, r)
            if isinstance(step, Resolved):
                out[key] = step
            else:
                nxt[key] = step
        todo = nxt
    return out


def resolve(manifest: str | Path, wanted: Iterable[tuple[str, str]], *, idx: TitleIndex | None = None,
            dumps: Mapping[str, Path] | None = None, run: str | None = None,
            log=print, name: str | None = None) -> dict[tuple[str, str], Resolved]:
    """Each (language code, title as a URL gave it) → `Resolved`: the title
    normalised, its page found, a redirect followed to its target (at most
    `MAX_HOPS`, a loop is `NONE`), the item read from the page_props unit
    the page's row names (app/plugins/wikipedia/source.py `item_of`).

    THE RUN FIRST (the module's docstring): a wiki whose run's index is on
    disk (`dumps`: language → the run's dump, `run_dumps`; the index beside
    it) is answered by `resolve_by_run`, the wikis in parallel on the parse
    pool; only a wiki without one — or whose index cannot be read — by the
    manifest's titles (`index`, the pass made only then, or `idx` given), a
    title missing there `UNKNOWN`. `run`: the run the collection holds."""
    from . import parallel
    from .plugins.wikipedia import ids
    wanted = sorted(set(wanted))
    dumps = dict(dumps or {})
    langs = {lang for lang, _ in wanted if lang in ids.LANG_ORDINAL}
    indexed = {lang for lang in langs if lang in dumps and dumps[lang].is_file() and index_path(dumps[lang]).is_file()}
    if idx is None and langs - indexed:
        idx = index(manifest, log=log, name=name)     # the fallback's pass: no wiki with its index needs it
    held = {ids.LANG_ORDINAL[lang] for lang in indexed} | (
        set(idx.titled) | set(idx.untitled) if idx is not None else set())
    runs = {lang: _run_of(dumps.get(lang), run) for lang in ids.LANG_ORDINAL}
    out: dict[tuple[str, str], Resolved] = {}
    by_run: dict[str, list[tuple[str, str]]] = {}
    fallback: dict[tuple[str, str], tuple[int, str, int, frozenset]] = {}
    for lang, title in wanted:
        o = ids.LANG_ORDINAL.get(lang)
        if o is None or o not in held:
            out[(lang, title)] = Resolved(UNKNOWN, None, NOT_HELD,
                                          f"the node's wikipedia holds no {lang or '?'} wiki")
            continue
        t = normalise(title)
        if not t:
            out[(lang, title)] = Resolved(NONE, None, NO_TITLE, "no title", run=runs[lang])
        elif lang in indexed:
            by_run.setdefault(lang, []).append((title, t))
        else:
            fallback[(lang, title)] = (o, t, 0, frozenset())
    cannot: dict[str, tuple[str, str]] = {}
    for lang in {k[0] for k in fallback}:
        d = dumps.get(lang)
        cannot[lang] = (NO_DUMP, f"no {lang} dump of the run is on disk" if d is None else
                        f"{(index_path(d) if d.is_file() else d).name} is not on disk")
    langs_run = sorted(by_run)
    tasks = [(str(manifest), str(dumps[lang]), lang, runs[lang], by_run[lang]) for lang in langs_run]
    for lang, (got, stats) in zip(langs_run, parallel.each_part(resolve_by_run, tasks)):
        if got is None:
            cannot[lang] = (UNREADABLE, stats["error"])
            fallback.update({(lang, title): (ids.LANG_ORDINAL[lang], t, 0, frozenset()) for title, t in by_run[lang]})
            log(f"[wikipedia titles] {name or 'wikipedia'}: {lang}: {stats['error']}; its titles from the manifest")
            continue
        out.update({(lang, title): r for title, r in got.items()})
        log(f"[wikipedia titles] {name or 'wikipedia'}: {lang}: {len(got):,} title(s) by the run's index, "
            f"{stats['lines']:,} lines in {stats['pass_s']}s, {stats['rounds']} round(s); {stats['by_id']:,} page(s) "
            f"by id in {stats['by_id_s']}s, {stats['read']:,} read from the dump in {stats['read_s']}s; "
            f"{stats['seconds']}s in all")
    if fallback:
        if idx is None:
            idx = index(manifest, log=log, name=name)
        con = _ro(manifest)
        try:
            out.update(_resolve_by_manifest(con, idx, fallback, runs, cannot))
        finally:
            con.close()
    return out
