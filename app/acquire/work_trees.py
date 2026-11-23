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

