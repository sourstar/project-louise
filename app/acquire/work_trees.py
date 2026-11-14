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
