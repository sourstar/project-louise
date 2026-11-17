"""AN ORIGIN THAT NAMES ITS UNIT BY THE ORIGIN'S OWN STABLE KEY, resolved in
a run (09 §9.5, amended 2026-11-17, operator: "Choice B"; docs/notes/
collection-updates.md §6) — one mechanism for every dated-dump origin:
wikipedia's page id through Wikimedia's multistream index, wikidata's
entity id through the index the node builds of its dump.

A record's `origin` used to name the dated file and the unit's place in it
(a byte range, an entity's ordinal), so every new run of the origin moved
every line and re-issued every part to every peer. Named by a key the
origin keeps across runs, and read in the run the snapshot names (09 §9.4
`origin_run`, else the Codex's `files` run), a line moves only when its
unit does.

THE DECLARATION. A plugin whose origin is so named carries `origin_key`, a
`KeyedOrigin`: which key, what the run's index is, whether the origin
publishes it or the node builds it, and from which parser version its
records name the key. The hooks the platform calls (app/plugin_io.py),
read like LOCATOR_SEP so no protocol version moves:

  origin_of(locator, native_id, base)  the record's `origin` for a manifest row
  local_of(origin, base, run)          the manifest locator `origin` names in `run`
  origin_files(origin, base, run)      the files a unit is read from in `run` (no index read)
  run_of_locator(locator)              the run a manifest locator reads
  carried_origin(origin, native_id, v) the origin this parser writes for a record parser
                                       `v` wrote — a pure function of the line where the
                                       two lines differ in `origin` alone (the re-creation's
                                       carry, app/hold/line_carry.py); None otherwise

`tests/test_keyed_origin.py` refuses a declaration whose hooks are missing.

THE TABLE (`KeyTable`). Whatever the run's index is, a lookup must not scan
it: each plugin derives from it a table of the key and the unit's place —
sorted int64 keys, one Parquet file, its row groups' first keys bisected and
one row group read, kept in the process's one read cache (app/backend.py).
Where the table lives and how it is kept fresh is the plugin's template's
(CONTAINER_PACK for wikipedia's index, DUMP_PACK's side table for
wikidata's): this is the format and the lookup, written once.

THE STANDARD, NOT ADOPTED: an SQLite table keyed on the key answers the same
with a B-tree probe; the Parquet file is the form every derived pack of the
templates already is (fresh by its own metadata, evicted by the cache).
"""
from __future__ import annotations

import threading
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.backend import read_cache

#: the hooks a plugin declaring `origin_key` implements (module docstring)
HOOKS = ("origin_of", "local_of", "origin_files", "run_of_locator", "carried_origin")


@dataclass(frozen=True)
class KeyedOrigin:
    """A plugin's declaration that its records' `origin` names each unit by
    the origin's own stable `key`, resolved in the snapshot's run through
    `index` — the origin's own (`built` False) or one the node builds of
    the run (`built` True) — from parser `since` on."""
    key: str
    index: str
    built: bool
    since: int


def declared(plugin) -> KeyedOrigin | None:
    """The plugin's `origin_key` where it names keys at the parser it runs
    at (a `plugin_at` instance), else None."""
    k = getattr(plugin, "origin_key", None)
    if k is None or int(getattr(plugin, "PARSER_VERSION", -1)) < int(k.since):
        return None
    return k


def missing_hooks(plugin) -> list[str]:
    return [h for h in HOOKS if not callable(getattr(plugin, h, None))]


class KeyTableError(ValueError):
    """The index holds a key twice: one key would locate two units."""


class KeyTable:
    """The table format: `key` and the `values` columns, int64, sorted by
    key, unique; `tables` cuts it in row groups for a writer, `lookup`
    answers one key from one row group."""

    def __init__(self, name: str, values: Sequence[str]):
        self.name = name
        self.values = tuple(values)
        self._lock = threading.Lock()
        self._bounds: dict[str, list[int]] = {}
        self._groups = read_cache(name)

    def schema(self):
        import pyarrow as pa
        return pa.schema([pa.field(c, pa.int64()) for c in ("key", *self.values)])

    def tables(self, keys, columns: dict, *, where: str, row_group: int) -> Iterator:
        """`keys` and `columns` (name -> array, the index's order) sorted by
        key as Arrow tables of `row_group` rows; a key twice is refused,
        naming `where`."""
        import numpy as np
        import pyarrow as pa
        keys = np.asarray(keys, dtype=np.int64)
        order = np.argsort(keys, kind="stable")
        keys = keys[order]
        cols = {c: np.asarray(columns[c], dtype=np.int64)[order] for c in self.values}
        if len(keys) > 1:
            twice = np.flatnonzero(keys[1:] == keys[:-1])
            if len(twice):
                raise KeyTableError(f"{where}: key {int(keys[twice[0]])} is listed twice; a key names one unit")
        schema = self.schema()
        for i in range(0, max(len(keys), 1), row_group):
            yield pa.table({"key": keys[i:i + row_group], **{c: v[i:i + row_group] for c, v in cols.items()}},
                           schema=schema)

    def forget(self, pack: Path) -> None:
        """A table newly built or confirmed: what was cached of an older one goes."""
        with self._lock:
            self._bounds.pop(str(pack), None)
        self._groups.forget(lambda k: k[0] == str(pack))

    def lookup(self, pack: Path, key: int) -> tuple[int, ...] | None:
        """The values of `key` in the table at `pack`, or None."""
        import numpy as np
        import pyarrow.parquet as pq
        pk = str(pack)
        with self._lock:
            bounds = self._bounds.get(pk)
        if bounds is None:
            md = pq.read_metadata(pack)
            bounds = [int(md.row_group(g).column(0).statistics.min) for g in range(md.num_row_groups)
                      if md.row_group(g).num_rows]
            with self._lock:
                self._bounds[pk] = bounds
        g = bisect_right(bounds, int(key)) - 1
        if g < 0:
            return None
        group = self._groups.get((pk, g))
        if group is None:
            with pq.ParquetFile(pack) as pf:
                t = pf.read_row_group(g)
            cols = tuple(t.column(c).to_numpy() for c in ("key", *self.values))
            group = self._groups.put((pk, g), cols, int(sum(c.nbytes for c in cols)))
        keys = group[0]
        i = int(np.searchsorted(keys, int(key)))
        if i >= len(keys) or int(keys[i]) != int(key):
            return None
        return tuple(int(c[i]) for c in group[1:])
