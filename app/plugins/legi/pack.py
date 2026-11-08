"""ONE CONTAINER PACK PER TARBALL (app/backend.py's CONTAINER_PACK template;
app/container_pack.py is its one implementation, app/derived.py places it).

A LEGI tarball is a gzip stream: a member cannot be read where it lies,
only by inflating everything before it (the baseline is 1.17 GB compressed,
~5.25 M members). So each tarball is read ONCE, streaming, into a Parquet
pack beside it (or on the fast drive, `LOUISE_CACHE_DIR`): a row per member,
in the tarball's order, holding the member's exact bytes and what discovery
needs of it (`xml.index_fields`) — then, after them, an INDEX of the
members' ids sorted, so a record is found from its locator's member name
without a name table in memory: the name's file stem is its id, the id's
index rows (row-group statistics, min/max) name the row, the row's group is
`row // ROW_GROUP`. An id met twice in one tarball (two folders) has two
index rows; the member name decides between them.

The deletion lists (`liste_suppression_legi.dat`,
`liste_suppression_legi_dossier.dat`) are members like the others, kind
`deletion` / `deletion_dossier`, their bytes kept whole; discovery reads
their lines (app/plugins/legi/source.py).

Fresh by the pack's own Parquet metadata (version, the tarball's size and
mtime); one builder across processes; written whole (container_pack.py).
The tarballs stay the archive: fetched, kept, seeded. The pack is a cache
and is never exchanged.
"""
from __future__ import annotations

import re
import tarfile
import threading
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ...backend import read_cache
from ...container_pack import ContainerPack
from . import xml as lx

PACK_VERSION = 1
ROW_GROUP = 2000
INDEX_GROUP = 8192
SUFFIX = ".pack.parquet"
_META_VERSION = b"louise.legi_pack_version"
INDEX_KIND = "~id"

SCHEMA = pa.schema([
    pa.field("index", pa.int32()),         # the member's ordinal (main rows); the row an index row names
    pa.field("kind", pa.string()),         # article | texte_version | section_ta | textelr | deletion |
                                           # deletion_dossier | other | ~id (an index row)
    pa.field("member", pa.string()),       # the tar member's name, as the tarball has it
    pa.field("path", pa.string()),         # the name without its increment's dated folder nor `.xml`
    pa.field("id", pa.string()),           # the file's own id (its name's stem)
    pa.field("mtime", pa.int64()),
    pa.field("size", pa.int64()),
    pa.field("date_debut", pa.string()),
    pa.field("date_texte", pa.string()),
    pa.field("date_publi", pa.string()),
    pa.field("chain", pa.string()),        # an article's VERSIONS ids, space-separated
    pa.field("xml", pa.binary()),          # the member's bytes, exactly
])

DELETIONS = {"liste_suppression_legi.dat": "deletion",
             "liste_suppression_legi_dossier.dat": "deletion_dossier"}
#: an increment's members sit under a folder named for its time: `20261028-212713/legi/…`
_DATED_FOLDER = re.compile(r"^\d{8}-\d{6}/")

_PACK = ContainerPack("legi pack", "members", _META_VERSION)
_groups = read_cache("legi pack")
_meta_lock = threading.Lock()
_meta: dict[tuple, tuple] = {}            # (pack, mtime_ns) -> (main groups, [(id_min, id_max, group)])


def normal_path(member: str) -> str:
    """`20261028-212713/legi/global/…/LEGIARTI000006398122.xml` →
    `legi/global/…/LEGIARTI000006398122`: the member's place in the
    origin's tree, the same in the baseline and in every increment."""
    p = _DATED_FOLDER.sub("", member.lstrip("./").lstrip("/"))
    return p[:-4] if p.endswith(".xml") else p


def pack_path(tarball: Path) -> Path:
    """The fast drive's `legi-pack/<tag of the tarballs' folder>/` when the
    node names one (app/derived.py; a pack built beside the tarballs is
    carried there once), else `.pack/` beside the tarballs."""
    from app import derived
    name = tarball.name + SUFFIX
    beside = tarball.parent / derived.BESIDE
    cached = derived.place_kept("legi-pack", tarball.parent)
    if cached is None:
        return beside / name
    derived.adopt(beside / name, cached / name)
    return cached / name


def _row(index: int, kind: str, info: tarfile.TarInfo, data: bytes | None, fields: dict) -> dict:
    name = info.name
    stem = name.rsplit("/", 1)[-1]
    return {"index": index, "kind": kind, "member": name, "path": normal_path(name),
            "id": stem[:-4] if stem.endswith(".xml") else stem,
            "mtime": int(info.mtime), "size": int(info.size),
            "date_debut": fields.get("date_debut"), "date_texte": fields.get("date_texte"),
            "date_publi": fields.get("date_publi"),
            "chain": " ".join(fields.get("chain") or ()) or None, "xml": data}


def _tables(tarball: Path) -> Iterator[pa.Table]:
    """The tarball's members, ROW_GROUP a table, then the sorted id index."""
    rows: list[dict] = []
    ids: list[pa.Array] = []
    n = 0
    with tarfile.open(tarball, "r|*") as tf:              # streaming: one pass over the gzip
        for info in tf:
            if not info.isfile():
                continue
            base = info.name.rsplit("/", 1)[-1]
            f = tf.extractfile(info)
            data = f.read() if f is not None else b""
            if base in DELETIONS:
                rows.append(_row(n, DELETIONS[base], info, data, {}))
            elif base.endswith(".xml"):
                fields = lx.index_fields(data)
                rows.append(_row(n, fields["kind"] or "other", info, data, fields))
            else:
                rows.append(_row(n, "other", info, None, {}))
            n += 1
            if len(rows) >= ROW_GROUP:
                t = pa.Table.from_pylist(rows, schema=SCHEMA)
                ids.append(t.column("id").combine_chunks())
                yield t
                rows = []
    if rows:
        t = pa.Table.from_pylist(rows, schema=SCHEMA)
        ids.append(t.column("id").combine_chunks())
        yield t
    if not ids:
        return
    all_ids = pa.concat_arrays(ids)
    order = pc.sort_indices(all_ids)
    sorted_ids = pc.take(all_ids, order)
    for start in range(0, len(sorted_ids), INDEX_GROUP):
        part_ids = sorted_ids.slice(start, INDEX_GROUP)
        part_rows = order.slice(start, INDEX_GROUP).cast(pa.int32())
        k = len(part_ids)
        yield pa.table({
            "index": part_rows, "kind": pa.array([INDEX_KIND] * k, pa.string()),
            "member": pa.nulls(k, pa.string()), "path": pa.nulls(k, pa.string()),
            "id": part_ids, "mtime": pa.nulls(k, pa.int64()), "size": pa.nulls(k, pa.int64()),
            "date_debut": pa.nulls(k, pa.string()), "date_texte": pa.nulls(k, pa.string()),
            "date_publi": pa.nulls(k, pa.string()), "chain": pa.nulls(k, pa.string()),
            "xml": pa.nulls(k, pa.binary())}, schema=SCHEMA)


def build(tarball: Path, dest: Path) -> int:
    """One streaming pass over the tarball into `dest`, written whole
    (container_pack.py). What tarfile/gzip raise on a corrupt tarball is
    raised after the temporary file is gone: the caller reports ONE
    container error."""
    return _PACK.write(tarball, dest, SCHEMA, _tables(tarball), PACK_VERSION)


def ensure(tarball: Path) -> Path:
    """The pack for `tarball`, built if missing or stale — one builder
    across threads and processes, the others wait and reuse it."""
    from app import derived
    return _PACK.ensure(tarball, lambda: [pack_path(tarball)], version=PACK_VERSION,
                        build=lambda t, d: build(t, d), ready=_forget,
                        where=derived.place_kept("legi-pack", tarball.parent))


def _forget(path: Path) -> None:
    _groups.forget(lambda k: k[0] == str(path))
    with _meta_lock:
        for k in [k for k in _meta if k[0] == str(path)]:
            _meta.pop(k)


def _layout(path: Path) -> tuple[int, list[tuple[str, str, int]]]:
    """(the number of main row groups, the index groups' id ranges), from
    the footer's statistics — read once per pack file."""
    key = (str(path), path.stat().st_mtime_ns)
    with _meta_lock:
        hit = _meta.get(key)
    if hit is not None:
        return hit
    md = pq.read_metadata(path)
    names = md.schema.names
    kind_col, id_col = names.index("kind"), names.index("id")
    main, index = 0, []
    for g in range(md.num_row_groups):
        rg = md.row_group(g)
        ks = rg.column(kind_col).statistics
        if ks is not None and ks.has_min_max and ks.min == ks.max == INDEX_KIND:
            st = rg.column(id_col).statistics
            index.append((st.min, st.max, g))
        else:
            main += 1
    out = (main, index)
    with _meta_lock:
        _meta[key] = out
    return out


def main_groups(tarball: Path) -> Iterator[pa.Table]:
    """Every member's row, its bytes left out, one row group at a time —
    what discovery reads."""
    path = ensure(tarball)
    n, _ = _layout(path)
    cols = [c for c in SCHEMA.names if c != "xml"]
    with pq.ParquetFile(path) as pf:
        for g in range(n):
            yield pf.read_row_group(g, columns=cols)


def deletion_lines(tarball: Path) -> list[tuple[str, list[str]]]:
    """`[(kind, lines)]` of each deletion list the tarball holds."""
    path = ensure(tarball)
    n, _ = _layout(path)
    out = []
    with pq.ParquetFile(path) as pf:
        for g in range(n):
            t = pf.read_row_group(g, columns=["kind", "xml"])
            for kind, data in zip(t.column("kind").to_pylist(), t.column("xml").to_pylist()):
                if kind in ("deletion", "deletion_dossier"):
                    text = (data or b"").decode("utf-8", "replace")
                    out.append((kind, [ln.strip() for ln in text.splitlines() if ln.strip()]))
    return out


class NotInPack(KeyError):
    """The member a locator names is not in the tarball's pack."""


def member_bytes(tarball: Path, member: str) -> bytes:
    """The bytes of `member`, exactly as the tarball holds them: its id's
    index rows, then the one row group that holds it (cached in the
    process's read budget, for the neighbours a stage asks next)."""
    path = ensure(tarball)
    _, index = _layout(path)
    stem = member.rsplit("/", 1)[-1]
    want = stem[:-4] if stem.endswith(".xml") else stem
    rows: list[int] = []
    with pq.ParquetFile(path) as pf:
        for lo, hi, g in index:
            if lo <= want <= hi:
                key = (str(path), "ids", g)
                ids = _groups.get(key)
                if ids is None:
                    t = pf.read_row_group(g, columns=["index", "id"])
                    ids = {}
                    for i, ident in zip(t.column("index").to_pylist(), t.column("id").to_pylist()):
                        ids.setdefault(ident, []).append(int(i))
                    ids = _groups.put(key, ids, t.nbytes)
                rows.extend(ids.get(want, ()))
        for row in sorted(rows):
            rg = row // ROW_GROUP
            key = (str(path), rg)
            group = _groups.get(key)
            if group is None:
                t = pf.read_row_group(rg, columns=["index", "member", "xml"])
                group = _groups.put(key, dict(zip(t.column("index").to_pylist(),
                                                  zip(t.column("member").to_pylist(),
                                                      t.column("xml").to_pylist()))),
                                    t.column("xml").nbytes)
            name, data = group.get(row, (None, None))
            if name == member and data is not None:
                return data
    raise NotInPack(f"{member}: not in {tarball.name}")


# ---- the scan's facts ---------------------------------------------------------------------
#
# What only the WHOLE origin shows about one file — the same id under two
# folders, an article's Work key that moved between two sightings or that
# its chain's files disagree on — is found by discovery, which reads every
# tarball, and reported by the parse as an Issue (06 §6.1), which reads one
# member. Discovery writes it here, beside the packs: DERIVED from the
# tarballs present (a pure function of them, re-created by the next scan),
# keyed by the file's id. It feeds Issues only, which are outside the record
# (app/plugins/base.py `Issue`): never a byte of a record line, of
# `content_hash` or of `origin_hash`.

FACTS = "legi-facts.sqlite"
_facts_lock = threading.Lock()
_facts_cons: dict[tuple, object] = {}


def facts_path(folder: Path) -> Path:
    """The fast drive's `legi-facts/<tag of the tarballs' folder>/` when the
    node names one, else `.pack/` beside the tarballs."""
    from app import derived
    cached = derived.place_kept("legi-facts", folder)
    return (cached if cached is not None else Path(folder) / derived.BESIDE) / FACTS


def write_facts(folder: Path, facts: dict[str, list[tuple[str, str]]]) -> Path:
    """The facts of one scan, written whole: into a temporary file of the
    writer's own, then an atomic replace (derived.building: one writer)."""
    import json
    import os
    import sqlite3
    from app import derived
    dest = facts_path(folder)
    with derived.building(dest):
        tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
        tmp.unlink(missing_ok=True)
        con = sqlite3.connect(tmp)
        try:
            con.execute("CREATE TABLE facts (id TEXT PRIMARY KEY, issues TEXT NOT NULL)")
            con.executemany("INSERT INTO facts VALUES (?, ?)",
                            ((i, json.dumps(v, ensure_ascii=False)) for i, v in sorted(facts.items())))
            con.commit()
        finally:
            con.close()
        os.replace(tmp, dest)
    with _facts_lock:
        _facts_cons.clear()
    return dest


def facts_of(folder: Path, ident: str) -> list[tuple[str, str]]:
    """The Issues discovery found for the file `ident`, `(code, detail)`
    each; none where no scan has written facts yet."""
    import json
    import sqlite3
    path = facts_path(folder)
    try:
        st = path.stat()
    except OSError:
        return []
    key = (str(path), st.st_mtime_ns, threading.get_ident())
    with _facts_lock:
        con = _facts_cons.get(key)
        if con is None:
            for k in [k for k in _facts_cons if k[0] == key[0] and k[2] == key[2]]:
                _facts_cons.pop(k).close()
            con = _facts_cons[key] = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                                     check_same_thread=False)
    try:
        row = con.execute("SELECT issues FROM facts WHERE id=?", (ident,)).fetchone()
    except sqlite3.Error:
        return []
    return [tuple(x) for x in json.loads(row[0])] if row else []


def reset_caches() -> None:
    _groups.clear()
    _PACK.fresh.clear()
    with _meta_lock:
        _meta.clear()
    with _facts_lock:
        for con in _facts_cons.values():
            con.close()
        _facts_cons.clear()
