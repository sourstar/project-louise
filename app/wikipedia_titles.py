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

"NO PAGE" ONLY ON PROOF (2026-11-17, the node's dry run: 83 claims withdrawn,
among them eight books through the enwiki redirect "Journey_to_the_Centre_of_
the_Earth", which the manifest holds no row for, and "Bouvard and Pécuchet",
page 1078856, which layer 1 holds under the archive's 2024 title "Bouvard et
Pécuchet"). A title the manifest misses is looked up in the RUN's own
multistream index (`offset:page_id:title`, every page of the run, redirects
included), one pass per language over the titles missed, set membership,
the languages in parallel on the parse pool (`look_up`):

- absent from the index: `NONE`, "no page" — the run has no page so titled;
- present, and the manifest holds that page by its id (either layer): its
  item, as its row's page_props unit holds it (`BY_PAGE_ID`; the title the
  manifest holds instead is said);
- present and a redirect: the page read from the run's dump at the index's
  offset (`redirect_of`, the plugin's stream reader, read-only) and its
  `<redirect title>` followed, within `MAX_HOPS` and the loop rule;
- present, not a redirect, not held: `UNKNOWN`, "held by the run, not read
  yet";
- the run's dump or index not on disk: `UNKNOWN`, "no dump to tell".

WHAT IT CANNOT ANSWER THAT THE API COULD, said per title (`UNKNOWN`, never
taken for "no such page"): a wiki the collection does not hold (its eleven
languages, app/plugins/wikipedia/ids.py), and the cases above. A page with
no item and no redirect is `NONE`, as the API's answer would be. Every
answer names the run it is the run's answer for (`Resolved.run`): a `NONE`
from a run older than the claim it would withdraw is the caller's to weigh
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


def _ends(open_: list[tuple[str, int, bytes]], rows: list[bytes], start: int,
          found: dict[str, tuple[int, int, int]]) -> list[tuple[str, int, bytes]]:
    """Each (title, page id, its stream's offset) of `open_` whose stream's
    end — the next offset — `rows` from `start` give: into `found`; the
    others returned, for the next block."""
    left = []
    for title, pid, off in open_:
        end = None
        for j in range(start, len(rows)):
            o = rows[j].partition(b":")[0]
            if o != off and o.isdigit():
                end = int(o)
                break
        if end is None:
            left.append((title, pid, off))
        else:
            found[title] = (pid, int(off), end)
    return left


def look_up(index: str, titles: list[str]) -> tuple[dict[str, tuple[int, int, int]], int, str | None]:
    """A task of the index pass (module level: it runs in the parse pool):
    each of `titles` that the run's index lists → (its page id, its stream's
    start, its end: the next stream's offset, 0 for the last, as discovery
    writes a locator), the lines read, and why the index could not be read
    (None when it was). ONE pass: the title of every line (after its second
    colon: a title may hold one) against the set of `index_forms`. Two lines
    of one title: the later."""
    want: dict[bytes, str] = {}
    for t in titles:
        for form in sorted(index_forms(t)):
            want.setdefault(form.encode("utf-8"), t)
    wanted = set(want)
    found: dict[str, tuple[int, int, int]] = {}
    pending: list[tuple[str, int, bytes]] = []
    lines = 0
    try:
        for block in blocks(index):
            rows = block.split(b"\n")
            if rows and not rows[-1]:
                rows.pop()
            lines += len(rows)
            if pending:
                pending = _ends(pending, rows, 0, found)
            ts = [r.split(b":", 2)[-1] for r in rows]
            for h in sorted(wanted.intersection(ts)):
                i = -1
                while True:
                    try:
                        i = ts.index(h, i + 1)
                    except ValueError:
                        break
                    off, _, rest = rows[i].partition(b":")
                    pid = rest.partition(b":")[0]
                    if off.isdigit() and pid.isdigit():
                        pending.extend(_ends([(want[h], int(pid), off)], rows, i + 1, found))
    except (OSError, EOFError, ValueError, RuntimeError) as e:
        return {}, lines, f"{Path(index).name} could not be read: {type(e).__name__}: {e}"[:300]
    for title, pid, off in pending:
        found[title] = (pid, int(off), 0)         # the last stream: to the end of the dump
    return found, lines, None


def read_pages(dump: str, lang: str, wanted: list[tuple[int, int, int]]) -> list[tuple[int, str | None, str | None]]:
    """A task (module level: the parse pool): each (page id, its stream's
    start, end) read from the run's dump by the plugin's own reader
    (`source.redirect_of`, read-only) — (page id, the title its redirect
    points at or None for an article, None), or (page id, None, why it could
    not be read)."""
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
    layer — by uid, the primary key: a row with text before one without,
    then the later row."""
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


def _index_hits(dumps: Mapping[str, Path], missed: dict[str, set[str]], *, log, name: str | None):
    """The titles each language's manifest missed, looked up in its run's
    index — one pass per language, the languages in parallel on the parse
    pool: ({(lang, title): (page id, start, end)}, {lang: (code, why)} for a
    language whose index cannot tell)."""
    from . import parallel
    hits: dict[tuple[str, str], tuple[int, int, int]] = {}
    cannot: dict[str, tuple[str, str]] = {}
    tasks, langs = [], []
    for lang in sorted(missed):
        d = dumps.get(lang)
        ix = index_path(d) if d is not None else None
        if d is None:
            cannot[lang] = (NO_DUMP, f"no {lang} dump of the run is on disk")
        elif not (d.is_file() and ix.is_file()):
            cannot[lang] = (NO_DUMP, f"{(ix if d.is_file() else d).name} is not on disk")
        else:
            tasks.append((str(ix), sorted(missed[lang])))
            langs.append(lang)
    if not tasks:
        return hits, cannot
    t0 = time.monotonic()
    lines = 0
    for lang, (got, n, err) in zip(langs, parallel.each_part(look_up, tasks)):
        lines += n
        if err is not None:
            cannot[lang] = (UNREADABLE, err)
            continue
        hits.update(((lang, t), h) for t, h in got.items())
    log(f"[wikipedia titles] {name or 'wikipedia'}: {sum(len(t) for _, t in tasks):,} title(s) the manifest "
        f"misses looked up in the run's index ({', '.join(langs)}): {len(hits):,} listed, {lines:,} lines "
        f"read in {time.monotonic() - t0:.1f}s")
    return hits, cannot


def _read_all(dumps: Mapping[str, Path], need: Iterable[tuple[str, int, int, int]]) -> dict[tuple[str, int], tuple[str | None, str | None]]:
    """(lang, page id) → (its redirect's target or None, why it could not be
    read or None): the pages read from the run's dumps, `READS` a task."""
    from . import parallel
    by: dict[str, list[tuple[int, int, int]]] = {}
    for lang, pid, start, end in need:
        by.setdefault(lang, []).append((pid, start, end))
    tasks, langs = [], []
    for lang in sorted(by):
        ws = sorted(by[lang], key=lambda w: (w[1], w[0]))
        for i in range(0, len(ws), READS):
            tasks.append((str(dumps[lang]), lang, ws[i:i + READS]))
            langs.append(lang)
    out: dict[tuple[str, int], tuple[str | None, str | None]] = {}
    for lang, got in zip(langs, parallel.each_part(read_pages, tasks)):
        for pid, to, err in got:
            out[(lang, pid)] = (to, err)
    return out


def resolve(manifest: str | Path, wanted: Iterable[tuple[str, str]], *, idx: TitleIndex | None = None,
            dumps: Mapping[str, Path] | None = None, run: str | None = None,
            log=print, name: str | None = None) -> dict[tuple[str, str], Resolved]:
    """Each (language code, title as a URL gave it) → `Resolved`: the title
    normalised, its page found, a redirect followed to its target (at most
    `MAX_HOPS`, a loop is `NONE`), the item read from the page_props unit
    the page's locator names (app/plugins/wikipedia/source.py `item_of`) —
    the page read from the local dump only when the manifest does not say
    it holds text (`redirect_of`). A title the manifest misses is answered
    by the run's own index and dump (`dumps`: language → the run's dump,
    `run_dumps`; the module's docstring) and is `UNKNOWN` without them —
    never "no page" unproven. `run`: the run the collection holds."""
    from .plugins.wikipedia import ids, source
    wanted = sorted(set(wanted))
    dumps = dict(dumps or {})
    if idx is None and any(lang in ids.LANG_ORDINAL for lang, _ in wanted):
        idx = index(manifest, log=log, name=name)     # no pass for wikis the collection cannot hold
    held = set(idx.titled) | set(idx.untitled) if idx is not None else set()
    runs = {lang: _run_of(dumps.get(lang), run) for lang in ids.LANG_ORDINAL}
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
            out[(lang, title)] = Resolved(NONE, None, NO_TITLE, "no title", run=runs[lang])
            continue
        todo[(lang, title)] = (o, t, 0, frozenset())
    if not todo:
        return out

    def item(key, p: Page, hops: int, stored: str | None = None) -> Resolved:
        uid = f"wikipedia:{p.native_id}"
        r = runs[key[0]]
        q = source.item_of(p.locator, p.native_id)
        if stored is not None and q:
            return Resolved(ITEM, q, BY_PAGE_ID, f"{uid}'s page_props, the run's index naming it {key[1]!r} "
                                                 f"and the manifest {stored!r}", uid, hops, r, stored)
        return (Resolved(ITEM, q, "", f"{uid}'s page_props", uid, hops, r) if q else
                Resolved(NONE, None, NO_ITEM, f"{uid} names no Wikidata item", uid, hops, r, stored))
    con = _ro(manifest)
    try:
        while todo:
            found = pages(con, idx, {(o, t) for o, t, _, _ in todo.values()})
            missed: dict[str, set[str]] = {}
            for (lang, _), (o, t, _, _) in todo.items():
                if (o, t) not in found:
                    missed.setdefault(lang, set()).add(t)
            hits, cannot = _index_hits(dumps, missed, log=log, name=name) if missed else ({}, {})
            rows = by_page(con, {(lang, h[0]) for (lang, _), h in hits.items()})
            reads = _read_all(dumps, sorted({(lang, h[0], h[1], h[2]) for (lang, _), h in hits.items()
                                             if not (rows.get((lang, h[0])) and rows[(lang, h[0])].has_text)}))
            nxt: dict[tuple[str, str], tuple[int, str, int, frozenset]] = {}
            for key, (o, t, hops, seen) in todo.items():
                lang, r = key[0], runs[key[0]]
                p = found.get((o, t))
                uid = ""
                try:
                    if p is None:
                        if lang in cannot:
                            code, why = cannot[lang]
                            out[key] = Resolved(UNKNOWN, None, code, f"no {lang} page titled {t!r} in the manifest, "
                                                                     f"and {why}", redirects=hops, run=r)
                            continue
                        h = hits.get((lang, t))
                        if h is None:
                            out[key] = Resolved(NONE, None, NO_PAGE, f"no {lang} page titled {t!r} in the run's "
                                                                     f"index ({r or 'its run'})", redirects=hops, run=r)
                            continue
                        row = rows.get((lang, h[0]))
                        uid = f"wikipedia:{ids.native_id_of(lang, h[0])}"
                        if row is not None and row.has_text:
                            out[key] = item(key, row, hops, row.title)
                            continue
                        to, err = reads.get((lang, h[0]), (None, "not read"))
                        if err is not None:
                            out[key] = Resolved(UNKNOWN, None, UNREADABLE, f"{uid}, the run's {t!r}, could not be "
                                                                           f"read from its dump: {err}", uid, hops, r)
                            continue
                        if to is None:
                            out[key] = (item(key, row, hops, row.title) if row is not None else
                                        Resolved(UNKNOWN, None, NOT_READ, f"{uid}, the run's {t!r}, is held by the "
                                                                          f"run and not read yet", uid, hops, r))
                            continue
                    else:
                        uid = f"wikipedia:{p.native_id}"
                        to = None if p.has_text else source.redirect_of(p.locator, p.native_id)
                        if to is None:
                            out[key] = item(key, p, hops)
                            continue
                except (OSError, EOFError, RuntimeError, ValueError, SyntaxError) as e:   # ParseError: SyntaxError
                    out[key] = Resolved(UNKNOWN, None, UNREADABLE,
                                        f"{uid} could not be read here: {type(e).__name__}", uid, hops, r)
                    continue
                t2 = normalise(to)
                if hops + 1 > MAX_HOPS or (o, t2) in seen or t2 == t:
                    out[key] = Resolved(NONE, None, LOOP,
                                        f"{uid}: a redirect chain longer than {MAX_HOPS} or a loop", uid, hops, r)
                    continue
                nxt[key] = (o, t2, hops + 1, seen | {(o, t)})
            todo = nxt
    finally:
        con.close()
    return out
