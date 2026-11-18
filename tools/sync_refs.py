"""Sync reference sources (HGNC, ClinVar-mini, ...) into refs.db.

With --fetch, the reference dumps download themselves first (HGNC complete
set, ClinVar variant_summary — HTTPS, budgeted and Range-resumable, see
app/refs/acquire.py); without it, --src must already contain the TSVs.
Idempotent and safe to re-run: downloads are freshness-gated by file age,
and syncing short-circuits unless a source's ref_version changed (bump it
to force reprocessing).

Usage:
  python -m app.louise dev refs-sync --src /path/to/refs-dir [--db /data/refs.db]
                             [--fetch] [--force] [--ack eurovoc] [--only SOURCE]
"""
import argparse
from pathlib import Path

from app.refs import connect
from app.refs import acquire as refs_acquire
from app.refs import store as refs_store


def _sync_one(db: str | None, src: str, corpora, source_id: str):
    """One source's sync in a child of the pass's pool: its own connection
    to refs.db (WAL, a busy timeout: each source writes its own tables in
    short batch transactions, refs/store.py `sync_source`)."""
    con = connect(Path(db) if db else None)
    try:
        return source_id, refs_store.sync_all(con, Path(src), corpora=corpora, only=(source_id,))[source_id]
    finally:
        con.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--src", required=True, type=Path,
                    help="directory containing (or receiving) hgnc.tsv, clinvar_mini.tsv, ...; "
                         "a plugin's corpus is read from the collections held here, and from "
                         "<src>/<plugin> only for a plugin none of them carries")
    p.add_argument("--db", type=Path, default=None,
                    help="refs.db path (default: config.refs_db_path())")
    p.add_argument("--fetch", action="store_true",
                    help="download/refresh the reference dumps into --src first")
    p.add_argument("--force", action="store_true",
                    help="with --fetch: re-download even if the local copy is fresh")
    p.add_argument("--ack", action="append", default=[], metavar="SOURCE",
                    help="record the acknowledgement of a yellow or red source's terms (invariant 9: "
                         "a timestamp and the sha256 of <src>/<source>/TERMS.txt), then sync "
                         "that source alone; today: eurovoc, tlio")
    p.add_argument("--only", action="append", default=[], metavar="SOURCE",
                    help="sync only this source (repeatable); every source otherwise")
    args = p.parse_args()

    for source_id in args.ack:
        for line in ack(args.src, source_id):
            print(line)

    if args.fetch:
        for source_id, result in refs_acquire.fetch_all(
                args.src, force=args.force).items():
            print(f"fetch {source_id}: {result}")

    con = connect(args.db)
    # the corpora of the sources that read a plugin's corpus, by the
    # collections held here, never by a folder name (2026-11-13)
    corpora = refs_store.held_corpora()
    for plugin, roots in sorted(corpora.items()):
        print(f"corpus {plugin}: {', '.join(str(r) for r in roots)}")
    # --ack syncs what it acknowledged, never every source again (München,
    # 2026-11-16: `--ack eurovoc` re-read clinvar's 442 MB for half an hour)
    only = tuple(dict.fromkeys([*args.only, *args.ack])) or None
    from app import parallel
    from app.refs import REFS_REGISTRY
    sids = only or tuple(REFS_REGISTRY)
    unknown = sorted(set(sids) - set(REFS_REGISTRY))
    if unknown:
        raise SystemExit(f"no reference source named {', '.join(unknown)}")
    con.close()
    # MULTI-PROCESS (CLAUDE.md): one source a child, the sources independent
    # (their own tables), so a pass takes the cores the node allows; the same
    # one process where it allows one (parallel.each_part)
    tasks = [(str(args.db) if args.db else None, str(args.src), corpora, sid) for sid in sids]
    for source_id, result in parallel.each_part(_sync_one, tasks):
        print(f"{source_id}: {result}", flush=True)
    from app.refs import eurovoc
    why = eurovoc.held_back(args.src / eurovoc.DIR) if (args.src / eurovoc.DIR).is_dir() else None
    if why:
        print(f"eurovoc: held back: {why}")
    from app import licensing
    from app.plugins.base import LicenseClass
    for sid in sids:
        source = REFS_REGISTRY[sid]
        if sid == eurovoc.SOURCE_ID or getattr(source, "license_class", None) not in (
                LicenseClass.YELLOW, LicenseClass.RED):
            continue
        base = args.src / getattr(source, "ref_dir", sid)
        why = licensing.ref_held_back(base, sid) if base.is_dir() else None
        if why:
            print(f"{sid}: held back: {why}")


def ack(src: Path, source_id: str) -> list[str]:
    """Record the acknowledgement of a yellow or red reference source's terms;
    the lines to print. EVERY registered source of either class (architecture,
    2026-11-18: "the next source needs no CLI change"), its terms at
    `<src>/<its ref_dir, else its id>/TERMS.txt`, through the reference-source
    resolution of app/licensing.py; eurovoc keeps its own record
    (app/refs/eurovoc.py)."""
    import time
    from app import licensing
    from app.plugins.base import LicenseClass
    from app.refs import REFS_REGISTRY, eurovoc
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    source = REFS_REGISTRY.get(source_id)
    if source is None or getattr(source, "license_class", LicenseClass.GREEN) not in (
            LicenseClass.YELLOW, LicenseClass.RED):
        raise SystemExit(f"--ack {source_id}: no yellow or red reference source of that name")
    if source_id == eurovoc.SOURCE_ID:
        base = Path(src) / eurovoc.DIR
        return [f"terms {base / eurovoc.TERMS}: sha256:{eurovoc.terms_sha256(base)}",
                f"ack {source_id}: {eurovoc.make_ack(base, now)}"]
    base = Path(src) / getattr(source, "ref_dir", source_id)
    return [f"terms {base / licensing.REF_TERMS}: sha256:{licensing.terms_sha256(base / licensing.REF_TERMS)}",
            f"ack {source_id}: {licensing.make_ref_ack(base, now)}"]


if __name__ == "__main__":
    main()
