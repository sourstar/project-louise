"""ONE NON-INFERRED TREE PER WORK, not per unit (München's probe, 2026-11-14:
Cellar answers a tree in 6.7 s p50, 23x a notice and 44x an item; parser 3
asked it once per (CELEX, language), so 24 times per work, and the Italian
pass alone spent ~57 h on trees at two connections).

The tree is the WORK's — every language's expression is in it, `rdf.scope`
cuts the unit's slice — so one fetch serves every language. A unit takes its
work's tree from, in order:
  1. this store: the tree a unit of another language fetched before;
  2. the Data Dump's MTD (`<work uuid>/tree_non_inferred.rdf`, one per work,
     datadump.py), bulk first (operator, 2026-11-14);
  3. Cellar, which the store then keeps.
A tree from 1 or 2 is taken only when it is the tree Cellar serves NOW for
everything the unit's own notice (fetched fresh) dates: the work's, the
expression's and each manifestation's `cmr:lastModificationDate` equal the
notice's LASTMODIFICATIONDATE, and the expression has the same manifestations
(operator, 2026-11-14: "Assume that cellar always updates the date when a
file is changed"). Anything else is asked of Cellar. `records-vs-origin`
never reads this store: it re-fetches from Cellar.

WHERE: derived data, re-creatable from Cellar, never exchanged (invariant 6):
`derived.place_kept(KIND, corpus root)`, beside the corpus under `.pack/`
when no cache drive is named. One gzip'd canonical JSON per work, fanned out
by the uuid's first two characters. A lock per work in the process, so two
languages' units of one work, fetched at once by the two connections, ask
Cellar once.

THE STANDARD, NOT ADOPTED: an HTTP cache (RFC 9111) keyed on the URL would
not serve another language's request (the URL is the work's, the answer is
negotiated) and cannot revalidate: Cellar's ETag is a modification time with
no conditional request documented. The notice's dates are the validator.
"""
from __future__ import annotations

import gzip
import re
import json
import os
import threading
from collections import Counter
from pathlib import Path
from typing import Callable

from app import derived

from . import datadump

KIND = "eurlex-work-trees"


def matches(tree: dict, dated: dict[str, str]) -> bool:
    """Is `tree` (rdf.read's form, the whole work) current for what a branch
    notice dates? Every dated node in it with the same instant, at least the
    work dated, and each dated expression with exactly the notice's
    manifestations."""
    if not dated:
        return False
    stamps = {u: datadump._stamp(w) for u, w in dated.items()}
    if any(s is None for s in stamps.values()):
        return False
    if not any(_depth(u) == 0 for u in stamps):
        return False                    # no work node dated: nothing to hold the tree to
    for uri, when in stamps.items():
        if datadump.modified(tree, uri) != when:
            return False
    nodes = tree.get("nodes", {})
    for expr in (u for u in stamps if _depth(u) == 1):
        held = {u for u in nodes if _depth(u) == 2 and u.startswith(expr + ".")}
        said = {u for u in stamps if _depth(u) == 2 and u.startswith(expr + ".")}
        if held != said:
            return False
    return True


def _depth(uri: str) -> int:
    """0 for a cellar work URI, 1 for an expression (`<uuid>.0006`), 2 for a
    manifestation (`<uuid>.0006.02`); -1 for anything else (an item, an alias)."""
    head, sep, rest = uri.partition("/resource/cellar/")
    if not sep or "/" in rest:
        return -1
    return rest.count(".")


class WorkTrees:
    """The store under one corpus root, and the dump's MTD beside it."""

    def __init__(self, base: Path, dumps: "datadump.Dumps | None" = None) -> None:
        self.base = base
        self.dumps = dumps
        self.counts: Counter = Counter()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @classmethod
    def under(cls, corpus_root: Path, dumps: "datadump.Dumps | None" = None) -> "WorkTrees":
        place = derived.place_kept(KIND, corpus_root) or (corpus_root / derived.BESIDE / KIND)
        return cls(place, dumps)

    def _path(self, uuid: str) -> Path:
        return self.base / uuid[:2] / f"{uuid}.json.gz"

    def _lock(self, uuid: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(uuid, threading.Lock())

    def _held(self, uuid: str) -> dict | None:
        try:
            return json.loads(gzip.decompress(self._path(uuid).read_bytes()))
        except (OSError, ValueError, EOFError):
            return None

    def keep(self, uuid: str, tree: dict) -> None:
        path = self._path(uuid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
        tmp.write_bytes(gzip.compress(json.dumps(tree, sort_keys=True, ensure_ascii=False,
                                                 separators=(",", ":")).encode("utf-8"), 6))
        os.replace(tmp, path)

    def tree_for(self, uuid: str | None, dated: dict[str, str],
                 fetch: Callable[[], dict]) -> tuple[dict, str]:
        """The work's whole tree and where it came from (`kept`, `dump`,
        `cellar`). `fetch()` asks Cellar and returns rdf.read's form."""
        if not uuid:
            self.counts["cellar"] += 1
            return fetch(), "cellar"
        with self._lock(uuid):
            held = self._held(uuid)
            if held is not None and matches(held, dated):
                self.counts["kept"] += 1
                return held, "kept"
            if self.dumps is not None:
                bulk = self.dumps.mtd_tree(uuid)
                if bulk is not None and matches(bulk, dated):
                    self.keep(uuid, bulk)
                    self.counts["dump"] += 1
                    return bulk, "dump"
            got = fetch()
            self.keep(uuid, got)
            self.counts["cellar"] += 1
            return got, "cellar"


_STORES: dict[str, WorkTrees] = {}
_STORES_LOCK = threading.Lock()


def store_for(corpus_root: Path) -> WorkTrees:
    """The process's store for a corpus root (keyed on its resolved path)."""
    key = str(Path(corpus_root).resolve())
    with _STORES_LOCK:
        if key not in _STORES:
            _STORES[key] = WorkTrees.under(Path(corpus_root), datadump.dumps())
        return _STORES[key]


# ---- parser 4: one tree per work within a pass -------------------------------------------

#: parser 4's kind: the tree Cellar served for a work, by CELEX, its bytes as
#: served (gzip), so the work's other languages are not asked again
CELEX_KIND = "eurlex-celex-trees"
#: how long one serves them (2026-11-18, München: EUR-Lex4's pass on the
#: dump's incomplete list spent 1,144 s asking trees against 33 s for items,
#: 10,528 units of 1,537 works). A tree Cellar served within this window is
#: as current as fetching those units that much earlier in the same pass;
#: past it, Cellar is asked again. A pass's units come in path order —
#: language first — so a work's languages lie a language block apart: the
#: window spans the pass, not minutes.
RECENT_S = 6 * 3600.0


class RecentTrees:
    """Parser 4's store (cellar.fetch_unit_from_tree `trees`): parser 3's
    `WorkTrees` validates a kept tree by the unit's fresh notice, which
    parser 4 does not ask; a pass's window stands in for it. A lock per
    work, so the connections fetching one work's languages at once ask
    Cellar once. `records-vs-origin` never reads it."""

    def __init__(self, base: Path, *, window_s: float = RECENT_S,
                 clock: Callable[[], float] | None = None) -> None:
        import time
        self.base = base
        self.window_s = window_s
        self.clock = clock or time.time
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @classmethod
    def under(cls, corpus_root: Path) -> "RecentTrees":
        place = derived.place_kept(CELEX_KIND, corpus_root) or (corpus_root / derived.BESIDE / CELEX_KIND)
        return cls(place)

    def _path(self, celex: str) -> Path:
        from urllib.parse import quote
        return self.base / celex[:5] / f"{quote(celex, safe='')}.rdf.gz"

    def _lock(self, celex: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(celex, threading.Lock())

    def get(self, celex: str, ask: Callable[[], bytes],
            current: Callable[[bytes], bool] | None = None) -> tuple[bytes, str]:
        """The work's tree as Cellar served it, and where it came from:
        `kept` (served within the window, and `current(tree)` where given)
        or `cellar` (`ask()`, then kept). `current`: whether a kept tree is
        as recent as what the caller knows of the unit (review 2026-11-21,
        Q1: a tree kept 13:00 landed a unit the 16:00 listing dated 15:38,
        and the engine recorded it current for good) — a kept tree that is
        not is asked of Cellar again, under the work's lock."""
        import zlib
        path = self._path(celex)
        with self._lock(celex):
            try:
                if self.clock() - path.stat().st_mtime <= self.window_s:
                    data = gzip.decompress(path.read_bytes())
                    if current is None or current(data):
                        return data, "kept"
            except (OSError, EOFError, zlib.error):
                pass                        # none, or not whole: asked of Cellar
            data = ask()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
            tmp.write_bytes(gzip.compress(data, 6))
            os.replace(tmp, path)
            return data, "cellar"


_RECENT: dict[str, RecentTrees] = {}


def recent_for(corpus_root: Path) -> RecentTrees:
    """The process's parser-4 store for a corpus root (keyed on its resolved path)."""
    key = str(Path(corpus_root).resolve())
    with _STORES_LOCK:
        got = _RECENT.get(key)
        if got is None:
            got = _RECENT[key] = RecentTrees.under(Path(corpus_root))
        return got



# ---- parser 4: the tree from the bulk, by CELEX ------------------------------------------
#
# 2026-11-26, EUR-Lex5.next step 8: parser 4 asked Cellar for the tree of
# every one of the 124,511 units the Data Dump lacks whole (~1,180 s of every
# 300 s of the pass, items ~20 s) while the dump's MTD held each of those
# works' trees, byte for byte the one Cellar served (München's proof on 20
# units). Bulk first: the tree is taken from the MTD of the dump BUILT INTO
# the corpus for the unit's language (eurlex_dump.stamp), found by CELEX
# through an index of that MTD, and checked as the work's own.
# Architecture's review of 1be767f6 (2026-11-26) shaped the rules below.

#: the MTD's CELEX → work uuid index: derived from one MTD zip, kept by its
#: physical identity under `place_kept(INDEX_KIND, <the zip>)`, re-creatable
INDEX_KIND = "eurlex-mtd-celex"
#: every CELEX literal a tree states (`cdm:resource_legal_id_celex`): a
#: candidate only — a wrong one can only send a unit to Cellar, since the
#: tree taken is checked to state the CELEX as its work's own
_CELEX = re.compile(rb"resource_legal_id_celex[^>]*>\s*([^<\s]+)\s*<")
_MTD_TREE = re.compile(r"^([0-9a-f-]{36})/" + re.escape(datadump.MTD_MEMBER) + r"$")
INDEX_CHUNK = 2000


def _index_chunk(path: str, members: list[str]) -> tuple[list[tuple[str, str]], int]:
    """(CELEX, uuid) of every CELEX each member's tree states, and how many
    members did not read (counted, never the whole index lost for one)."""
    import zipfile
    import zlib
    out: list[tuple[str, str]] = []
    bad = 0
    with zipfile.ZipFile(path) as z:
        for m in members:
            try:
                data = z.read(m)
            except (zipfile.BadZipFile, zlib.error, OSError, KeyError, EOFError):
                bad += 1
                continue
            uuid = m.split("/", 1)[0]
            out.extend((c.decode("ascii", "replace"), uuid) for c in set(_CELEX.findall(data)))
    return out, bad


def _identity(path: Path) -> str:
    import hashlib
    st = path.stat()
    return hashlib.sha256(f"{path.resolve()}\0{st.st_size}\0{st.st_mtime_ns}".encode()).hexdigest()[:24]


class Stopped(Exception):
    """The index build ended at a chunk boundary by the pass's stop."""


def mtd_index(path: Path, place: Path, *, threads: int, stop: Callable[[], bool] = lambda: False,
              log: Callable[[str], None] = print) -> dict[str, str]:
    """`{CELEX: work uuid}` of one MTD zip, built once and kept at
    `place/<its identity>.json.gz` (an older identity's file removed). A
    CELEX two works' trees state is left out: Cellar answers it. Threads,
    not processes: the cost is the zip's inflate, which releases the GIL.
    `stop` is asked between chunks (Stopped). Raises zipfile.BadZipFile /
    OSError where the zip itself does not open."""
    from concurrent.futures import ThreadPoolExecutor
    import time
    import zipfile
    import zlib
    dest = place / f"{_identity(path)}.json.gz"
    try:
        return json.loads(gzip.decompress(dest.read_bytes()))
    except (OSError, EOFError, ValueError, zlib.error):
        pass
    t0 = time.monotonic()
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if _MTD_TREE.match(n))
    tasks = [(str(path), names[i:i + INDEX_CHUNK]) for i in range(0, len(names), INDEX_CHUNK)]
    log(f"[work-trees] indexing the CELEX of {path} ({len(names)} trees, {threads} threads)")
    seen: dict[str, str] = {}
    twice: set[str] = set()
    bad = 0
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        for k in range(0, len(tasks), max(1, threads)):
            if stop():
                raise Stopped(f"the MTD index of {path}: stopped at chunk {k}/{len(tasks)}, not kept")
            for got, b in pool.map(lambda t: _index_chunk(*t), tasks[k:k + max(1, threads)]):
                bad += b
                for celex, uuid in got:
                    if seen.setdefault(celex, uuid) != uuid:
                        twice.add(celex)
    for c in twice:
        seen.pop(c, None)
    place.mkdir(parents=True, exist_ok=True)
    for old in place.glob("*.json.gz"):
        old.unlink(missing_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.write_bytes(gzip.compress(json.dumps(seen, sort_keys=True).encode(), 6))
    os.replace(tmp, dest)
    log(f"[work-trees] MTD index of {path}: {len(seen)} CELEX in {time.monotonic() - t0:.1f} s, "
        f"{dest.stat().st_size >> 10} KiB; {len(twice)} stated by two works and {bad} member(s) that "
        f"do not read left to Cellar")
    return seen


class BulkTrees:
    """A unit's work tree from ONE MTD zip by its CELEX
    (cellar.fetch_unit_from_tree `bulk`): None where the MTD holds no work of
    that CELEX, its member does not read, or the tree does not state the
    CELEX as its work's own."""

    def __init__(self, path: Path, index: dict[str, str], *, log: Callable[[str], None] = print) -> None:
        import zipfile
        self.path = path
        self.index = index
        self._zip = zipfile.ZipFile(datadump._staged(path, log))
        self._lock = threading.Lock()

    def __call__(self, celex: str) -> dict | None:
        import zipfile
        import zlib
        from ..plugins.eurlex import rdf
        from .eurlex_dump import _work_celex
        uuid = self.index.get(celex)
        if uuid is None:
            return None
        try:
            with self._lock:
                data = self._zip.read(f"{uuid}/{datadump.MTD_MEMBER}")
            tree = rdf.read(data)
        except (zipfile.BadZipFile, zlib.error, OSError, KeyError, EOFError, rdf.RdfError):
            return None
        return tree if _work_celex(tree, uuid) == celex else None


def stamped_mtd(dump_base: Path | None, corpus_root: Path, lang: str, *,
                log: Callable[[str], None] = print) -> Path | None:
    """The MTD zip of the dump stamped into the corpus for `lang`
    (eurlex_dump.stamp: the dump whose build recorded what this language
    lacks), resolved through its links; None, said, where that dump or its
    MTD is not found. Dated layout (`dump-…/datadump-<lang>/LEG_MTD.zip` →
    `../datadump-mtd/`) and flat layout alike: the language folder's MTD."""
    from . import eurlex_dump
    when = eurlex_dump.stamp(corpus_root).get(lang)
    if dump_base is None or when is None:
        return None
    want = datadump._stamp(when)
    for folder in datadump.dump_folders(dump_base):
        got = datadump.statement(folder)
        if isinstance(got, str) or got[0] != lang or got[1] != want:
            continue
        zips = eurlex_dump.mtd_zips([folder])
        if zips:
            return zips[0].resolve()
        log(f"[work-trees] {folder}: the dump stamped for {lang} holds no MTD zip; its trees come from Cellar")
        return None
    log(f"[work-trees] the dump stamped for {lang} ({when}) is not under {dump_base}; its trees come from Cellar")
    return None


_BULK: dict[str, BulkTrees] = {}
_BULK_LOCK = threading.Lock()


def prepare_bulk(dump_base: Path | None, corpus_root: Path, langs: list[str], *, threads: int,
                 stop: Callable[[], bool] = lambda: False,
                 log: Callable[[str], None] = print) -> dict[str, BulkTrees]:
    """`{lang: BulkTrees}` for the languages a pass fetches, each on the MTD
    stamped for it, its index built here — BEFORE the pass (TreeUnits.prepare),
    the stop asked between chunks — and kept per MTD identity for the
    process. Raises what an MTD that does not open raises (the caller holds
    the pass back: bulk first)."""
    import zipfile
    out: dict[str, BulkTrees] = {}
    for lang in langs:
        path = stamped_mtd(dump_base, corpus_root, lang, log=log)
        if path is None:
            continue
        key = f"{path}|{_identity(path)}"
        with _BULK_LOCK:
            got = _BULK.get(key)
        if got is None:
            place = derived.place_kept(INDEX_KIND, path) or (path.parent / derived.BESIDE / INDEX_KIND)
            try:
                index = mtd_index(path, place, threads=threads, stop=stop, log=log)
            except zipfile.BadZipFile as e:
                raise OSError(f"{path}: the MTD does not open ({e})") from e
            got = BulkTrees(path, index, log=log)
            with _BULK_LOCK:
                got = _BULK.setdefault(key, got)
        out[lang] = got
    return out
