"""MEASUREMENT helper (not product code): the reader's tables for a run
before the node's own sync (architecture, 2026-11-18: "build it by the SAME
sync code path from the v3 TSV, not by a second loader. Print its
version"). A scratch refs.db in a temporary directory, never the node's,
synced from the derived file by `refs.store.sync_source` — the product's
own sync — and installed as `reading.STORE_OVERRIDE` for the run.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def scratch(tsv: Path):
    """A RefStore of the wikisource-readings table synced from `tsv`."""
    from app.plugins.base import RefSyncState
    from app.refs import store as rs, wikisource_readings as wr
    d = Path(tempfile.mkdtemp(prefix="reading-tables-"))
    shutil.copy(tsv, d / wr.FINAL)
    con = rs.connect(d / "refs.db")
    rs.sync_source(con, wr.WikisourceReadingsSource(), RefSyncState(ref_root=d))
    con.row_factory = sqlite3.Row
    return rs.RefStore(con, [wr.WikisourceReadingsSource().schema()])


def use(tsv: Path | None) -> str:
    """Install the scratch tables when `tsv` is given; the version of the
    tables the run reads ("" when none is held), said on stderr."""
    from app import reading
    if tsv is not None:
        reading.STORE_OVERRIDE = scratch(tsv)
    v = reading.tables_held()
    print(f"reading tables: {v or 'NONE held'}" + (f" (scratch, synced from {tsv})" if tsv else " (the node's)"),
          file=sys.stderr, flush=True)
    return v


def refuse(why: str) -> int:
    """Exit 2 with the reason in plain words: no number is reported."""
    print(f"REFUSED: {why}", file=sys.stderr, flush=True)
    return 2
