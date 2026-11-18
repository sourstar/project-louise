"""THE RUN'S ARTICLES AGAINST THE COLLECTION'S ROWS — `louise dev
wikipedia-coverage` (2026-11-17, München, after the gutenberg-editions dry
run: some redirects of the pinned enwiki run have no manifest row while
others of the same article do; a redirect page is a record of the collection,
invariant 11). READ-ONLY: nothing is written, not even the archive's id
cache (`archive.covered_articles(write=False)`).

Per wiki the collection holds a run's dump of: the page ids its index lists
in the articles' namespaces (discovery's own rule: `namespace_prefixes`, the
English fallback where the dump's header names none) against the rows the
manifest holds, layer 1's (the archive's ids) and the dump layer's. It says
how many pages of the run no row holds, a bounded sample of them with their
titles, and for each whether the archive COVERS it (`archive.covers`): such
a page the dump pass skips, and with no archive row neither layer holds it.

ONE PASS over the manifest (rowid windows on the parse pool, `held_range`)
and ONE PASS over each wiki's index (`index_pages`, the wikis in parallel on
the pool): the index lookup's cost (app/wikipedia_titles.py), plus the
manifest's pass. The sample is a reservoir (Vitter's algorithm R) with a
fixed seed, so the same inputs give the same sample.

Standardised alternative, not taken: none needed — a sorted-set difference
(numpy's `searchsorted`, as `_gone_since` in app/plugins/wikipedia/source.py
already does it) is the whole computation.
"""
from __future__ import annotations

import random
import re
import time
from array import array
from pathlib import Path
from typing import Any

import numpy as np

#: rows read per window of the manifest's pass, and rowids a task at least
WINDOW = 50_000
TASK_ROWS = 250_000
#: the sample's seed: the same inputs, the same sample
SEED = 20261117
HELD_SQL = "SELECT rowid, native_id FROM documents WHERE rowid > ? AND rowid <= ? ORDER BY rowid LIMIT ?"


def held_range(manifest: str, lo: int, hi: int) -> dict[int, tuple[bytes, bytes]]:
    """A task of the manifest's pass (module level: the parse pool): per
    language ordinal, the page ids of the rows of rowid in (lo, hi] —
    (layer 1's, the dump layer's), as int64 bytes."""
    from . import wikipedia_titles as wt
    from .plugins.wikipedia import ids
    con = wt._ro(manifest)
    out: dict[int, tuple[array, array]] = {}
    dump_base, lang_base = ids.DUMP_BASE, ids.LANG_BASE
    try:
        cursor = lo
        while cursor < hi:
            rows = con.execute(HELD_SQL, (cursor, hi, WINDOW)).fetchall()
            if not rows:
                break
            for _, nid in rows:
                try:
                    n = int(nid)
                except (TypeError, ValueError):
                    continue                     # a container's row: no page
                dump = n >= dump_base
                rest = n - dump_base if dump else n
                o = rest // lang_base
                pair = out.get(o)
                if pair is None:
                    pair = out[o] = (array("q"), array("q"))
                pair[1 if dump else 0].append(rest % lang_base)
            cursor = int(rows[-1][0])
    finally:
        con.close()
    return {o: (a.tobytes(), d.tobytes()) for o, (a, d) in out.items()}


def held(manifest: str | Path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Language → (layer 1's page ids, the dump layer's), each sorted unique:
    the manifest's ONE pass, rowid ranges on the parse pool."""
    from . import parallel
    from . import wikipedia_titles as wt
    from .plugins.wikipedia import ids
    con = wt._ro(manifest)
    try:
        top = int(con.execute("SELECT COALESCE(MAX(rowid), 0) FROM documents").fetchone()[0])
    finally:
        con.close()
    n = max(1, min(-(-top // TASK_ROWS), 4 * parallel.processes()))
    step = -(-top // n) if top else 1
    parts: dict[int, tuple[list, list]] = {}
    for got in parallel.each_part(held_range, [(str(manifest), lo, min(lo + step, top)) for lo in range(0, top, step)]):
        for o, (a, d) in got.items():
            pa, pd = parts.setdefault(o, ([], []))
            pa.append(np.frombuffer(a, dtype=np.int64))
            pd.append(np.frombuffer(d, dtype=np.int64))
    lang_of = {v: k for k, v in ids.LANG_ORDINAL.items()}
    return {lang_of[o]: (np.unique(np.concatenate(a)), np.unique(np.concatenate(d)))
            for o, (a, d) in parts.items() if o in lang_of}


def index_pages(index: str, dump: str, have: bytes, samples: int, seed: int) -> dict[str, Any]:
    """A task of the index pass (module level: the parse pool), one wiki: the
    articles its run's index lists (discovery's namespaces), how many the
    rows `have` (int64 page ids, sorted) hold, the page ids they do not hold
    (int64 bytes), and a reservoir sample of `samples` of those with their
    titles; `error` when the index cannot be read."""
    from . import wikipedia_titles as wt
    from .plugins.wikipedia import source
    held_ids = np.frombuffer(have, dtype=np.int64)
    skip = source.namespace_prefixes(Path(dump), Path(index))
    prefixes = tuple(s.encode("utf-8") for s in skip) if skip else None
    fallback = re.compile(source._SKIP_TITLE.pattern.encode("utf-8"))
    rng = random.Random(seed)
    pages = present = seen = 0
    missing: list[np.ndarray] = []
    sample: list[tuple[int, str]] = []
    try:
        for block in wt.blocks(index):
            pids: list[int] = []
            titles: list[bytes] = []
            for r in block.split(b"\n"):
                parts = r.split(b":", 2)
                if len(parts) < 3 or not parts[1].isdigit():
                    continue
                t = parts[2]
                if t.startswith(prefixes) if prefixes else fallback.match(t):
                    continue
                pids.append(int(parts[1]))
                titles.append(t)
            if not pids:
                continue
            arr = np.asarray(pids, dtype=np.int64)
            if len(held_ids):
                at = np.searchsorted(held_ids, arr)
                there = (at < len(held_ids)) & (held_ids[np.minimum(at, len(held_ids) - 1)] == arr)
            else:
                there = np.zeros(len(arr), dtype=bool)
            pages += len(arr)
            present += int(there.sum())
            gone = np.flatnonzero(~there)
            missing.append(arr[gone])
            for k in gone:
                seen += 1
                if len(sample) < samples:
                    sample.append((int(arr[k]), titles[k].decode("utf-8", "replace")))
                else:
                    j = rng.randrange(seen)
                    if j < samples:
                        sample[j] = (int(arr[k]), titles[k].decode("utf-8", "replace"))
    except (OSError, EOFError, ValueError, RuntimeError) as e:
        return {"error": f"{Path(index).name} could not be read: {type(e).__name__}: {e}"[:300]}
    gone_ids = np.concatenate(missing) if missing else np.zeros(0, dtype=np.int64)
    return {"pages": pages, "present": present, "missing": gone_ids.tobytes(), "sample": sorted(sample)}


def coverage(collection, *, lang: str | None = None, samples: int = 20, log=print) -> dict[str, Any]:
    """The check, for a wikipedia collection (a `collections.Collection`):
    per wiki of its run, the run's articles, the rows held, the pages no row
    holds — how many the archive covers (held by neither layer) and how many
    it does not (the dump pass's) — the rows the run does not list, and a
    sample of the missing pages, each with its title and whether the archive
    covers it."""
    from . import parallel
    from . import wikipedia_titles as wt
    from .plugins.wikipedia import archive
    t0 = time.monotonic()
    run = getattr(collection, "snapshot", None)
    corpus = Path(collection.corpus_dir)
    dumps = wt.run_dumps(corpus, run)
    if lang is not None:
        dumps = {k: v for k, v in dumps.items() if k == lang}
    rows = held(collection.manifest_path)
    log(f"[wikipedia coverage] {collection.name}: the manifest read in {time.monotonic() - t0:.1f}s")
    covered = archive.covered_articles(corpus, Path(collection.manifest_path).parent, write=False)
    out: dict[str, Any] = {"collection": collection.name, "run": run, "wikis": {}}
    for lg in sorted(set(rows) - set(dumps)):
        if lang is None or lg == lang:
            out["wikis"][f"{lg}wiki"] = {"error": f"no {lg} dump of the run is on disk: nothing to compare"}
    tasks, langs = [], []
    for lg, d in sorted(dumps.items()):
        ix = wt.index_path(d)
        if not ix.is_file():
            out["wikis"][f"{lg}wiki"] = {"error": f"{ix.name} is not on disk"}
            continue
        a, dl = rows.get(lg, (np.zeros(0, np.int64), np.zeros(0, np.int64)))
        tasks.append((str(ix), str(d), np.union1d(a, dl).tobytes(), samples, SEED))
        langs.append((lg, d, ix, a, dl))
    t1 = time.monotonic()
    for (lg, d, ix, a, dl), got in zip(langs, parallel.each_part(index_pages, tasks)):
        if "error" in got:
            out["wikis"][f"{lg}wiki"] = {"error": got["error"]}
            continue
        miss = np.frombuffer(got["missing"], dtype=np.int64)
        cov = covered.get(lg)
        in_cov = np.isin(miss, cov) if cov is not None and len(cov) else np.zeros(len(miss), dtype=bool)
        held_n = len(np.union1d(a, dl))
        out["wikis"][f"{lg}wiki"] = {
            "run": wt._run_of(d, run), "index": str(ix), "articles_in_run": got["pages"],
            "rows": {"archive": len(a), "dump": len(dl), "both": len(np.intersect1d(a, dl))},
            "missing": len(miss), "missing_covered_by_archive": int(in_cov.sum()),
            "missing_not_covered": int(len(miss) - in_cov.sum()),
            "rows_not_in_run": held_n - got["present"],
            "sample": [{"page_id": pid, "title": title, "archive_covers": archive.covers(covered, lg, pid)}
                       for pid, title in got["sample"]]}
    log(f"[wikipedia coverage] {collection.name}: {len(tasks)} index(es) read in {time.monotonic() - t1:.1f}s")
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out
