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


def scratch(tsv: Path, tlio_root: Path | None = None):
    """A RefStore of the wikisource-readings table synced from `tsv`, and,
    with `tlio_root` (a ref_root holding `tlio/`: its pages, TERMS.txt and
    the operator's ack.json), the tlio table synced from it — the source's
    own licence gate decides (invariant 9: no ack, no rows). The pages are
    linked, never copied; the 1900s counts beside `tsv` are tlio's
    homograph test."""
    from app.plugins.base import RefSyncState
    from app.refs import store as rs, tlio, wikisource_readings as wr
    d = Path(tempfile.mkdtemp(prefix="reading-tables-"))
    shutil.copy(tsv, d / wr.FINAL)
    modern = Path(tsv).with_name(wr.MODERN)
    if modern.is_file():
        shutil.copy(modern, d / wr.MODERN)
    con = rs.connect(d / "refs.db")
    rs.sync_source(con, wr.WikisourceReadingsSource(), RefSyncState(ref_root=d))
    schemas = [wr.WikisourceReadingsSource().schema()]
    if tlio_root is not None:
        (d / tlio.DIR).symlink_to(Path(tlio_root).resolve() / tlio.DIR, target_is_directory=True)
        rs.sync_source(con, tlio.TlioSource(), RefSyncState(ref_root=d))
        schemas.append(tlio.TlioSource().schema())
    con.row_factory = sqlite3.Row
    return rs.RefStore(con, schemas)


def use(tsv: Path | None, tlio_root: Path | None = None) -> str:
    """Install the scratch tables when `tsv` is given; the version of the
    tables the run reads ("" when none is held), said on stderr."""
    from app import reading
    if tsv is not None:
        reading.STORE_OVERRIDE = scratch(tsv, tlio_root)
    v = reading.tables_held()
    print(f"reading tables: {v or 'NONE held'}" + (f" (scratch, synced from {tsv}"
          + (f" and {tlio_root}/tlio" if tlio_root else "") + ")" if tsv else " (the node's)"),
          file=sys.stderr, flush=True)
    if tlio_root is not None and "tlio" not in v:
        print("tlio: NOT synced — its terms are not acknowledged, or its pages are absent "
              "(python -m tools.sync_refs --src <ref_root> --ack tlio)", file=sys.stderr, flush=True)
    return v


ARG = ("--tlio-root", {"type": Path, "help": "with --readings-tsv: a ref_root holding tlio/ (pages, TERMS.txt, "
                                             "ack.json), synced into the scratch tables too"})


def refuse(why: str) -> int:
    """Exit 2 with the reason in plain words: no number is reported."""
    print(f"REFUSED: {why}", file=sys.stderr, flush=True)
    return 2
