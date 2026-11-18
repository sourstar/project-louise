"""The pre-embedded archive as a SOURCE of Wikipedia's text: Upstash's
`wikipedia-2024-06-bge-m3` parquet shards, whose rows carry (id, url,
title, text, embedding) with `id = "{article}_{seq}"` and the rows of one
article consecutive (architecture-v2.md §17 amendment 11, format v1).

Layer 1 of the `wikipedia` collection reads its articles from here, in the
archive's own cut — one row, one section, one chunk (`verbatim`) — so the
archive's vectors attach one to one (docs/notes/wikipedia-two-layers.md).
The legacy `embedded` plugin reads the same shards through these same
functions until wikivec is migrated.

Layout (the Upstash convention):  data/{lang}/{NNN}.parquet

Locator: one or more segments "<shard path>::<start_row>::<end_row>" joined
by "|" — end exclusive; a second segment covers an article whose rows
straddle a shard boundary.
"""
import hashlib
import os
import shutil
import threading
from collections import OrderedDict
from pathlib import Path

from app.backend import read_cache

from . import ids

SEG_SEP = "|"
_LOCATOR_SEP = "::"

# ROW-GROUP reads for fetch(). The first cut cached one whole shard (the
# four text columns of a ~437 MB parquet, ~10 s to read) because the worker
# walks documents in shard order; with the PARSE POOL (amendment 47) a
# batch's documents are fetched by several processes, and a whole-shard
# cache per process would multiply that read and hold N x hundreds of MB.
# A locator names the article's rows, so only the row groups holding them
# are read — a few MB — and kept in a small LRU per process. Lock because
# the parallel embed stage fetches from threads.
_cache_lock = threading.Lock()
_layouts: OrderedDict = OrderedDict()     # path -> (mtime, [row group start offsets], total)
#: (path, group) -> (mtime, table), in the process's one read budget
#: (app/backend.py `READ_CACHE`, config.READ_CACHE_BYTES); it held 8 groups,
#: then 256 MiB of its own
_groups = read_cache("wikipedia archive")
_MAX_LAYOUTS = 64
#: the columns a fetch reads of a row group — never the vectors' column; what a
#: verifier reads by Range is these columns' chunks (weigh.py)
READ_COLUMNS = ("id", "url", "title", "text")


def _layout(path: str) -> tuple[list[int], int]:
    import pyarrow.parquet as pq
    mtime = Path(path).stat().st_mtime_ns
    with _cache_lock:
        hit = _layouts.get(path)
        if hit is not None and hit[0] == mtime:
            _layouts.move_to_end(path)
            return hit[1], hit[2]
    meta = pq.ParquetFile(path).metadata
    starts, off = [], 0
    for g in range(meta.num_row_groups):
        starts.append(off)
        off += meta.row_group(g).num_rows
    with _cache_lock:
        _layouts[path] = (mtime, starts, off)
        _layouts.move_to_end(path)
        while len(_layouts) > _MAX_LAYOUTS:
            _layouts.popitem(last=False)
    return starts, off


def _group_table(path: str, g: int):
    import pyarrow.parquet as pq
    mtime = Path(path).stat().st_mtime_ns
    key = (path, g)
    hit = _groups.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    table = pq.ParquetFile(path).read_row_group(g, columns=list(READ_COLUMNS))
    _groups.put(key, (mtime, table), table.nbytes)
    return table


def read_rows(path: str, start: int, end: int) -> list[dict]:
    """Rows [start, end) of a shard as dicts, from their row groups only."""
    starts, total = _layout(path)
    end = min(end, total)
    out: list[dict] = []
    for g, g_start in enumerate(starts):
        g_end = starts[g + 1] if g + 1 < len(starts) else total
        if g_end <= start or g_start >= end:
            continue
        table = _group_table(path, g)
        lo, hi = max(start, g_start) - g_start, min(end, g_end) - g_start
        for i in range(lo, hi):
            out.append({"id": table.column("id")[i].as_py(),
                        "url": table.column("url")[i].as_py(),
                        "title": table.column("title")[i].as_py(),
                        "text": table.column("text")[i].as_py()})
    return out


def split_row_id(row_id: str) -> tuple[str, int]:
    """'12345_3' -> ('12345', 3). Raises on malformed ids — the caller
    decides whether that costs a row or a shard."""
    article, _, seq = row_id.rpartition("_")
    return article, int(seq)


def embed_input(title: str, text: str) -> str:
    """The producer's embedding input, reproduced as OUR chunk text
    (amendment 11): Upstash prefixed each paragraph with its article title.
    The exact separator is confirmed empirically by the genesis sample
    re-encode — a wrong guess here refuses the archive whole and imports
    nothing, so this constant is safe to correct until first import."""
    return f"{title}\n{text}" if title else text


def lang_of_shard(rel: Path) -> str | None:
    # data/{lang}/NNN.parquet
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "data" and parts[1] in ids.LANG_ORDINAL:
        return parts[1]
    return None


def shards(root: Path) -> list[Path]:
    # through the origin's links: rglob enters none (app/plugins/walk.py)
    from .. import walk
    return walk.files(root, "*.parquet")


def is_archive_locator(locator: str) -> bool:
    """An archive locator names parquet rows; a dump locator a bz2 stream."""
    first = str(locator).split(SEG_SEP, 1)[0]
    return first.rsplit(_LOCATOR_SEP, 2)[0].endswith(".parquet")


def join_segments(segments: list[list]) -> str:
    return SEG_SEP.join(
        f"{rel}{_LOCATOR_SEP}{start}{_LOCATOR_SEP}{end}"
        for rel, start, end in segments)


#: the page's own page_props unit (wikipedia parser 5, page_props.py), the
#: locator's last segment where it has one: never a piece of archive rows
_PROPS_SUFFIX = "-page_props.sql.gz"


def split_locator(locator: str) -> list[tuple[str, int, int]]:
    """The archive rows' segments (or a dump page's stream) — never the
    page_props segment, so an article in one shard stays ONE segment for
    genesis's straddler test (bundles/genesis.py)."""
    out = []
    for seg in locator.split(SEG_SEP):
        head, _, tail = seg.rpartition(_LOCATOR_SEP)
        path, _, start = head.rpartition(_LOCATOR_SEP)
        if path.endswith(_PROPS_SUFFIX):
            continue
        out.append((path, int(start), int(tail)))
    return out


#: the per-shard cache of the archive's article ids, under a collection's state
IDS_DIR = "wikipedia-archive-ids"
#: a cache file keyed on the shard's PHYSICAL identity (its resolved path)
PHYSICAL = "p-"


def _ids_tag(path: str, st) -> str:
    return hashlib.blake2b(f"{path}:{st.st_mtime_ns}:{st.st_size}".encode(), digest_size=16).hexdigest()


def ids_names(shard: Path, st) -> tuple[str, str]:
    """`(the cache file by the shard's physical identity, the one by the path
    as walked)` — the second is how it was keyed until 2026-11-17. The walked
    path runs through `<root>/<name>/origin/`, so it held the COLLECTION'S
    NAME: a re-created collection under `<name>.next` reading the very same
    shards missed every entry, and the hand copy München made worked only once
    the rename gave the old name back (CLAUDE.md: caches key on physical
    identity, never on a name)."""
    return (f"{PHYSICAL}{_ids_tag(os.path.realpath(shard), st)}.npy", f"{_ids_tag(str(shard), st)}.npy")


def _load_ids(path: Path):
    import numpy as np
    if not path.is_file():
        return None
    try:
        return np.load(path)
    except (OSError, ValueError):   # a file another writer left half-written: read again
        return None


def covered_articles(root: Path, state_dir: Path | None, *, write: bool = True):
    """`{lang: sorted unique page ids}` — every article the archive holds.

    What the dump pass consults so a page the archive covers is read from
    the archive ONLY, never minted twice under two ids. Read from the `id`
    column alone, and cached per shard by its physical identity (resolved
    path, mtime_ns, size) under `state_dir`, so a scan with nothing new reads
    no parquet. Independent of the scan cursor on purpose: a shard the
    cursor skips is still coverage. An unreadable shard covers nothing (its
    discovery reports it). `write=False`: the cache is read where it is and
    never written (a read-only check, app/wikipedia_coverage.py). An entry
    under the walked path's key (before 2026-11-17) is read and, writing,
    moved to its physical key: nothing is read again for the change."""
    import numpy as np
    import pyarrow.parquet as pq
    cache = state_dir / IDS_DIR if state_dir is not None else None
    parts: dict[str, list] = {}
    for shard in shards(root):
        lang = lang_of_shard(shard.relative_to(root))
        if lang is None:
            continue
        st = shard.stat()
        name, legacy = ids_names(shard, st)
        path = cache / name if cache is not None else None
        got = _load_ids(path) if path is not None else None
        if got is None and cache is not None:
            got = _load_ids(cache / legacy)
            if got is not None and write:
                try:
                    os.replace(cache / legacy, path)
                except OSError:
                    pass
        if got is None:
            try:
                col = pq.read_table(shard, columns=["id"]).column(0).to_pylist()
            except Exception:           # noqa: BLE001 — reported by discovery
                continue
            pages = []
            for rid in col:
                try:
                    pages.append(int(split_row_id(rid)[0]))
                except (ValueError, AttributeError):
                    continue
            got = np.unique(np.asarray(pages, dtype=np.int64))
            if path is not None and write:
                # whole or absent: discover may run in two processes at once
                cache.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
                with open(tmp, "wb") as fh:
                    np.save(fh, got)
                os.replace(tmp, path)
        parts.setdefault(lang, []).append(got)
    return {lang: np.unique(np.concatenate(p)) for lang, p in parts.items()}


def carry_ids(root: Path, src_state: Path, dst_state: Path, *, apply: bool) -> dict:
    """THE ARCHIVE'S IDS CACHE, CARRIED to a successor reading the same shards
    (`hold/carry.py`'s derived state; München, 2026-11-17, copied it by hand).
    An entry is carried when it is the cache of a shard under `root` as that
    shard is NOW — its physical identity, mtime and size, which is exactly
    what the target's own scan will look it up by — under its physical key,
    an entry kept under the walked path's key renamed to it. An entry of a
    shard that changed or went is not carried. Returns the counts."""
    src, dst = Path(src_state) / IDS_DIR, Path(dst_state) / IDS_DIR
    out = {"carried": 0, "present": 0, "stale": 0, "bytes": 0}
    if not src.is_dir():
        return out
    held = {p.name for p in src.glob("*.npy")}
    used: set[str] = set()
    for shard in shards(root):
        if lang_of_shard(shard.relative_to(root)) is None:
            continue
        st = shard.stat()
        name, legacy = ids_names(shard, st)
        have = name if name in held else legacy if legacy in held else None
        if have is None:
            continue
        used.add(have)
        if (dst / name).is_file():
            out["present"] += 1
            continue
        out["carried"] += 1
        out["bytes"] += (src / have).stat().st_size
        if apply:
            dst.mkdir(parents=True, exist_ok=True)
            tmp = dst / f".{name}.{os.getpid()}.tmp"
            shutil.copyfile(src / have, tmp)
            os.replace(tmp, dst / name)
    out["stale"] = len(held - used)
    return out


def covers(covered: dict, lang: str, page_id: int) -> bool:
    import numpy as np
    arr = covered.get(lang)
    if arr is None or len(arr) == 0:
        return False
    i = int(np.searchsorted(arr, page_id))
    return i < len(arr) and int(arr[i]) == page_id


def _tail_carry(pq, prev: Path, pf) -> dict | None:
    """The carry a scan of `prev` would have left behind IF its last article
    continues into the shard open as `pf`: that article's rows at the tail
    of `prev`, as one segment. None when the two shards share no article.
    Reads only the first row group of `pf` and the tail row groups of
    `prev` — never a whole shard."""
    if pf.num_row_groups == 0:
        return None
    head = pf.read_row_group(0, columns=["id"])
    if head.num_rows == 0:
        return None
    first_article, _ = split_row_id(head.column(0)[0].as_py())
    ppf = pq.ParquetFile(prev)
    n_groups = ppf.num_row_groups
    if n_groups == 0:
        return None
    # walk the previous shard's row groups backwards while the article holds
    group_offsets = []
    off = 0
    for g in range(n_groups):
        group_offsets.append(off)
        off += ppf.metadata.row_group(g).num_rows
    start: int | None = None
    title = ""
    for g in range(n_groups - 1, -1, -1):
        tbl = ppf.read_row_group(g, columns=["id", "title"])
        row_ids = tbl.column(0).to_pylist()
        k = len(row_ids)
        while k > 0 and split_row_id(row_ids[k - 1])[0] == first_article:
            k -= 1
        if k < len(row_ids):
            start = group_offsets[g] + k
            title = tbl.column(1)[k].as_py() or ""
        if k > 0:
            break                       # a different article precedes: done
    if start is None:
        return None                     # prev ends with another article
    st = prev.stat()
    return {"article": first_article, "title": title,
            "segments": [[str(prev), start, off]], "rows": off - start,
            "mtime": int(st.st_mtime)}


def _head_continuation(pq, nxt: Path, article: str) -> int:
    """How many leading rows of `nxt` belong to `article` — the continuation
    a scan of `nxt` would have appended to the carry. 0 when its first
    article is another. Reads row groups from the front only as far as the
    article runs."""
    pf = pq.ParquetFile(nxt)
    n = 0
    for g in range(pf.num_row_groups):
        row_ids = pf.read_row_group(g, columns=["id"]).column(0).to_pylist()
        k = 0
        while k < len(row_ids) and split_row_id(row_ids[k])[0] == article:
            k += 1
        n += k
        if k < len(row_ids):
            break
    return n


def discover_articles(state, source_id: str, progress, make_ref, ready=None):
    """One ref per ARTICLE over the archive's shards, via `make_ref(pending,
    lang)`; container-scope refs for what cannot be read. Moved unchanged
    from the legacy `embedded` plugin, whose discovery it still is.

    The last article of a shard is carried into the next shard's scan, so
    a boundary-straddling article becomes ONE ref with two segments.

    `ready(lang)`, when given, is False for a language whose records cannot
    be made yet (wikipedia parser 5: its dump run's page_props not on disk):
    its shards are neither walked nor staged, so a later scan walks them."""
    import pyarrow.parquet as pq
    from ..base import DocRef
    root = state.corpus_root
    pending: dict | None = None
    pending_lang: str | None = None
    # The shards whose last article is `pending` (one, or more when
    # empty shards sit between). They are staged into the cursor only
    # once that article has been YIELDED (cursor.Progress): staging a
    # shard at the end of its own loop would let an interrupted scan
    # remember it as done while its last article — yielded only when the
    # next shard starts — never reached the manifest.
    deferred: list[Path] = []
    prev: Path | None = None          # previous shard of the same language
    prev_skipped = False

    def release(ref_pending, ref_lang):
        """Yield the carried article, then stage the shards it came from."""
        yield make_ref(ref_pending, ref_lang)
        for done in deferred:
            progress.stage(done)
        deferred.clear()

    for shard in shards(root):
        rel = shard.relative_to(root)
        lang = lang_of_shard(rel)
        if lang is None:
            yield DocRef(source_id=source_id, native_id=shard.name,
                         locator=str(shard), scope="container",
                         error=f"unrecognized shard path (want data/<lang>/*.parquet): {rel}")
            continue
        if pending is not None and pending_lang != lang:
            yield from release(pending, pending_lang)
            pending, pending_lang = None, None
        if ready is not None and not ready(lang):
            prev, prev_skipped = None, False
            continue
        if progress.unchanged(shard):
            if pending is not None:
                # The mirror of the tail-carry case below: the carry may
                # continue INTO this skipped shard. Complete it from the
                # shard's head rows, or the straddler would be released
                # with only its first segment — and, its locator and
                # size now differing, re-processed as "changed" every
                # time the two shards are scanned in different passes.
                n = _head_continuation(pq, shard, pending["article"])
                if n:
                    pending["segments"].append([str(shard), 0, n])
                    pending["rows"] += n
                    pending["mtime"] = int(shard.stat().st_mtime)
                yield from release(pending, pending_lang)
                pending, pending_lang = None, None
            prev, prev_skipped = shard, True
            continue
        st = shard.stat()
        try:
            pf = pq.ParquetFile(shard)
            if pending is None and prev is not None and prev_skipped \
                    and lang_of_shard(prev.relative_to(root)) == lang:
                # The previous shard was skipped as unchanged, so its last
                # article was never carried here. If this shard's first
                # rows continue it, rebuild the carry from the previous
                # shard's tail — otherwise the straddler would be minted
                # (or "relocated") with only its second segment, silently
                # losing its first. Reachable whenever two adjacent shards
                # are scanned in different passes: an acquisition landing
                # one shard mid-scan, or a scan resumed after a restart.
                pending = _tail_carry(pq, prev, pf)
                pending_lang = lang if pending is not None else None
            row = 0
            for batch in pf.iter_batches(columns=["id", "title"], batch_size=4096):
                row_ids = batch.column(0).to_pylist()
                titles = batch.column(1).to_pylist()
                for rid, title in zip(row_ids, titles):
                    article, _seq = split_row_id(rid)
                    if pending is not None and pending["article"] == article:
                        seg = pending["segments"][-1]
                        if seg[0] == str(shard) and seg[2] == row:
                            seg[2] = row + 1
                        else:
                            pending["segments"].append([str(shard), row, row + 1])
                        pending["rows"] += 1
                        pending["mtime"] = int(st.st_mtime)
                    else:
                        if pending is not None:
                            yield from release(pending, pending_lang)
                        pending = {"article": article, "title": title or "",
                                   "segments": [[str(shard), row, row + 1]],
                                   "rows": 1, "mtime": int(st.st_mtime)}
                        pending_lang = lang
                    row += 1
        except Exception as e:  # noqa: BLE001 — a truncated/corrupt shard
            # costs one error ref, never the scan (pubmed/wikipedia rule)
            pending, pending_lang = None, None
            deferred.clear()         # their carry is gone with it: re-examine
            yield DocRef(source_id=source_id, native_id=shard.name,
                         locator=str(shard), scope="container",
                         error=f"unreadable shard {rel}: "
                                f"{type(e).__name__}: {e}"[:200])
            prev, prev_skipped = shard, False
            continue
        if pending is None:
            progress.stage(shard)    # nothing carried out of it
        else:
            deferred.append(shard)
        prev, prev_skipped = shard, False
    if pending is not None:
        yield from release(pending, pending_lang)
    for done in deferred:
        progress.stage(done)
    deferred.clear()
    progress.flush()
