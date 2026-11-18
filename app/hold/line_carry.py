"""`louise hold records carry <to> --from <from> [--dry-run] [--workers N]` —
a re-created collection's record parts made from its predecessor's, each
line's `origin` rewritten and every other byte taken unchanged (RULE 1; 02
§2.4 A1c; 02 §2.7, amended 2026-11-17).

WHY (wikipedia parser 6, the operator's "Choice B", 2026-11-17). The new
parser writes the same line as parser 5 but for `origin`, which names the
page instead of the dated file and byte range, and the new origin is a pure
function of the old line: its `origin` and its page id
(`carried_origin(origin, native_id, version)`, the plugin's). A build by the
book parses 52.9 M pages to write lines whose 13 keys but one are the
predecessor's byte for byte; `records build --records-from` takes the lines
but decodes, re-encodes and re-chunks every one of them. This rewrites the
one value in place, in the bytes, and writes each part with the build's own
framing (`pack.records.write_lines`, the frames and the index of
`write_shard`): the part a build by the book writes, byte for byte
(tests/hold/test_line_carry.py holds it against a parse, line for line and
part for part).

WHAT IS PROVEN, PER PART, BEFORE IT TAKES ITS PLACE: the same uids in the
same order, the same `records_root` (over the content hashes, which do not
move), the same record and chunk counts as the source snapshot lists. So
every chunk and vector of the predecessor is the new collection's: its
vector table is carried by `dev carry --rebuilt`, and `dev carry
--reconcile` reads the proof this pass leaves (`state/line-carry.json`)
instead of reading both snapshots again.

WHAT IT REFUSES (nothing written): two Codexes whose records differ in
anything but the parser and its image; a plugin that does not write the
source parser's lines with `origin` alone rewritten; a source whose parts
are not cut as a build of the target by the book cuts them (a count cut with
a short part inside a slice, a content cut ending a part elsewhere) — that
re-creation takes `records build --records-from`, which cuts afresh; a
Parquet container; relations or figures parts (none built for it yet); a
target that holds a snapshot or another build.

RESUMABLE (invariant 2). The unit is a part. Its build row and its mark are
committed BEFORE the rewritten file takes its place (`hold records
relocate`'s order): a re-run finds the mark and a file of the row's size,
or writes the part again — the same bytes. SIGTERM or SIGINT finishes the
parts in hand, commits, exits 130.

MULTI-CORE: the parts are rewritten in the parse pool's spawned processes
(`parallel.each_part`, the node profile's processes; a child the kernel
kills does not end the pass); the build's thread only commits.

THE STANDARD, NOT ADOPTED: a part rebuilt from a delta of its lines is 09
§9.9's patch (app/share/patch.py), which a PEER applies to bytes it holds;
the predecessor is not a peer's copy of this collection, and a patch per
part listing every line is the whole part again.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .codex import CodexError

MARKER = "line-carry.json"
#: what may differ between the two Codexes: the parser and the image that runs it
CARRIED = frozenset({"parser", "toolchain.platform_image"})
#: the build database's table of the parts already carried (resumable)
_MARKS = "line_carry"
#: the fixed tail of a record line: its two hashes, 32 hex each (09 §9.5)
_TAIL = re.compile(rb',"content_hash":"([0-9a-f]{32})","origin_hash":"([0-9a-f]{32})"\}$')
_TAIL_LEN = 100
_ORIGIN_KEY = b',"origin":'
#: the head of a record line: its uid and native id (09 §9.5's order)
_HEAD = re.compile(rb'^\{"uid":("(?:[^"\\]|\\.)*"),"native_id":("(?:[^"\\]|\\.)*"),')


class CarryRefused(CodexError):
    """A rule stands in the way; nothing was written (exit 2)."""
    exit_code = 2


@dataclass
class Result:
    parts: int = 0
    carried: int = 0             # parts written by this run
    present: int = 0             # parts a run before this one wrote
    records: int = 0
    rewritten: int = 0           # lines whose origin changed
    bytes_before: int = 0
    bytes_after: int = 0
    seconds: float = 0.0
    snapshot: dict[str, Any] | None = None
    interrupted: bool = False
    examples: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ---- one line ---------------------------------------------------------------

def rewrite_line(line: bytes, carry: Callable[[str, str], str]) -> tuple[bytes, str, str, bool]:
    """`(the line with its origin replaced by carry(origin, native_id), its
    uid, its content_hash, whether the origin changed)` — the value replaced
    in the BYTES, every other byte as it was. Equal, by construction and by
    test, to `canonical_line` of the decoded line with that origin: a JSON
    string's encoding is its own whatever surrounds it. A line not in the
    record line's fixed shape is decoded and re-encoded instead (the same
    answer, slower)."""
    line = bytes(line).rstrip(b"\n")
    head = _HEAD.match(line)
    tail = _TAIL.search(line, max(0, len(line) - _TAIL_LEN))
    at = line.rfind(_ORIGIN_KEY + b'"', 0, len(line) - _TAIL_LEN) if tail is not None else -1
    if head is not None and tail is not None and at > 0:
        enc = line[at + len(_ORIGIN_KEY):len(line) - _TAIL_LEN]
        try:
            origin = json.loads(enc)
        except ValueError:
            origin = None
        if isinstance(origin, str) and _enc(origin) == enc:
            uid, native_id = json.loads(head.group(1)), json.loads(head.group(2))
            new = carry(origin, native_id)
            if new == origin:
                return line, uid, tail.group(1).decode("ascii"), False
            return (line[:at] + _ORIGIN_KEY + _enc(new) + line[len(line) - _TAIL_LEN:], uid,
                    tail.group(1).decode("ascii"), True)
    from app.pack import records as pack_records
    rec = pack_records.loads_line(line)
    keys = list(rec)
    new = carry(rec["origin"], rec["native_id"])
    out = json.dumps({k: (new if k == "origin" else rec[k]) for k in keys}, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return out, rec["uid"], rec["content_hash"], new != rec["origin"]


def _enc(s: str) -> bytes:
    return json.dumps(s, ensure_ascii=False).encode("utf-8")


# ---- one part (a pool task: module-level, pickles) ----------------------------

_carriers: dict[tuple[str, int, int], Any] = {}


def _carrier(plugin_id: str, to_version: int, from_version: int):
    key = (plugin_id, int(to_version), int(from_version))
    if key not in _carriers:
        from app import collections as collections_mod
        plugin = collections_mod.plugin_at(collections_mod.PLUGIN_REGISTRY[plugin_id], int(to_version))
        fn = getattr(plugin, "carried_origin", None)
        _carriers[key] = lambda origin, native_id: _checked(fn(origin, native_id, int(from_version)), origin)
    return _carriers[key]


def _checked(got: str | None, origin: str) -> str:
    if got is None:
        raise CarryRefused(f"the plugin does not carry the origin {origin!r}: its line is parsed, not carried")
    return got


def carry_part(src: str, dst: str, plugin_id: str, to_version: int, from_version: int,
               rule: tuple[int, int, int] | None, last_in_slice: bool) -> dict[str, Any]:
    """One part of the source rewritten into `dst` (a temporary name the
    parent moves into place), with what the parent checks before it does."""
    from app.pack import records as pack_records
    from app.pack import slicing

    from . import records as hold_records
    from . import shard as shard_mod
    t0 = time.monotonic()
    carried = _carrier(plugin_id, to_version, from_version)
    # THE RUNS THE SOURCE'S LINES NAME (a dated origin's `<…>/<run>/…`; a
    # keyed one names none): the parent holds each part to the run the
    # target's Codex names, so a part read in another run never takes its place
    runs: set[str] = set()
    run_of = _run_reader(plugin_id, to_version)

    def carry(origin: str, native_id) -> str:
        if run_of is not None:
            got = run_of(origin)
            if got:
                runs.add(got)
        return carried(origin, native_id)
    index = pack_records.read_index(Path(src))
    lines: list[bytes] = []
    uids: list[str] = []
    hashes: list[str] = []
    changed, example = 0, None
    for k in range(len(index.frames)):
        for line in pack_records.read_frame(Path(src), index, k):
            new, uid, ch, moved = rewrite_line(line, carry)
            lines.append(new)
            uids.append(uid)
            hashes.append(ch)
            if moved:
                changed += 1
                if example is None:
                    example = (uid, pack_records.loads_line(line)["origin"], pack_records.loads_line(new)["origin"])
    if uids != list(index.uids):
        raise CarryRefused(f"{src}: its lines' uids are not its index's, in order")
    if len(set(uids)) != len(uids):
        raise CarryRefused(f"{src}: a uid twice in one part")
    if rule is not None:
        # a CONTENT cut is a pure function of the slice's records (02 §2.2):
        # the part ends where the rule ends it, and nowhere before
        for i, u in enumerate(uids, 1):
            ends = slicing.ends_part(u, i, rule)
            if ends and i < len(uids):
                raise CarryRefused(f"{src}: record {i} ({u}) ends a part by the content rule; the part runs on")
            if i == len(uids) and not ends and not last_in_slice:
                raise CarryRefused(f"{src}: its last record ({u}) does not end a part by the content rule")
    keys = iter(uids)
    info = pack_records.write_lines(Path(dst), lines, key_of=lambda _line: next(keys), chunks=index.chunks,
                                    frame_counts=[int(f[3]) for f in index.frames])
    return {"src": src, "dst": dst, "records": len(uids), "chunks": index.chunks,
            "records_root": hold_records.records_root(hashes), "bytes": info.bytes, "sha256": info.sha256,
            "root": shard_mod.pieces_root_of(Path(dst)), "changed": changed, "example": example,
            "bytes_before": Path(src).stat().st_size, "seconds": time.monotonic() - t0,
            "runs": sorted(runs)}


def _run_reader(plugin_id: str, to_version: int):
    """The plugin's `run_of_locator` (a dated locator or origin's run), or None."""
    from app import collections as collections_mod
    plugin = collections_mod.plugin_at(collections_mod.PLUGIN_REGISTRY[plugin_id], int(to_version))
    fn = getattr(plugin, "run_of_locator", None)
    return fn if callable(fn) else None


# ---- what may be carried --------------------------------------------------------

def _check(root: Path, from_name: str, src, to_name: str, to) -> tuple[Any, int, int]:
    """The source snapshot, the target's parser and the source's — or a refusal."""
    from app import collections as collections_mod
    from app.pack import records as pack_records
    from app.pack import slicing

    from .seed import latest_snapshot, record_differences
    if src.collection_id == to.collection_id:
        raise CarryRefused("carry: the source and the target are one collection")
    from . import origin_run
    # the origin with the run pinned that the source's records were read in
    # (09 §9.17; wikidata parser 2 -> 3): the same record bytes, each part held
    # to that run below (`runs`)
    try:
        diff = origin_run.admit_run_pin(root, from_name, src, to, record_differences(src, to), "carry")
    except CodexError as e:
        raise CarryRefused(str(e)) from None
    other = [d for d in diff if d not in CARRIED]
    if other or "parser" not in diff:
        raise CarryRefused(
            f"carry: {to_name} and {from_name} differ in {', '.join(other) if other else 'no parser'} — "
            f"this carry crosses a PARSER change whose lines differ in `origin` alone; "
            + ("anything else decides other record bytes (02 §2.7)" if other else
               "with the same parser the parts are seeded (`hold snapshot create --shards-from`)"))
    if src.parser.id != to.parser.id or src.plugin != to.plugin:
        raise CarryRefused(f"carry: {to_name} runs another parser ({to.parser.id}) than {from_name}")
    registry = collections_mod.PLUGIN_REGISTRY.get(to.plugin)
    if registry is None or int(getattr(registry, "PARSER_VERSION", -1)) != int(to.parser.version):
        raise CarryRefused(f"carry: this image runs {to.plugin} parser "
                           f"{getattr(registry, 'PARSER_VERSION', None)}, {to_name}'s Codex names "
                           f"{to.parser.version}")
    plugin = collections_mod.plugin_at(registry, int(to.parser.version))
    if not callable(getattr(plugin, "carried_origin", None)):
        raise CarryRefused(f"carry: the {to.plugin} plugin writes no line of parser {src.parser.version} with its "
                           f"origin alone rewritten (`carried_origin`); its lines are parsed")
    snap = latest_snapshot(root, from_name)
    if snap.collection_id != src.collection_id:
        raise CarryRefused(f"carry: {from_name}'s snapshot {snap.snapshot_id} names {snap.collection_id}")
    if snap.relations or snap.figures:
        raise CarryRefused(f"carry: {from_name}'s snapshot lists relations or figures parts, which this carry "
                           f"does not take yet; `records build --records-from` builds them")
    if any(pack_records.is_parquet(s.path) for s in snap.shards):
        raise CarryRefused("carry: a Parquet container's parts are rewritten by `records build --records-from`")
    rule = slicing.parts_rule_of(to.slicing)
    if rule is None:
        per = int((to.slicing or {}).get("shard_records") or 0)
        by_slice: dict[str, list] = {}
        for s in snap.shards:
            by_slice.setdefault(s.slice, []).append(s)
        bad = []
        for sl, parts in by_slice.items():
            parts.sort(key=lambda s: s.n)
            if [s.n for s in parts] != list(range(1, len(parts) + 1)):
                bad.append(f"{sl}: parts numbered {[s.n for s in parts][:5]}…")
            bad += [f"{s.slice}/{s.n:05d} holds {s.records}" for s in parts[:-1] if s.records != per]
        if bad:
            raise CarryRefused(
                f"carry: {from_name}'s parts are not the count cut a build of {to_name} by the book makes "
                f"({per} records a part, the last of each slice shorter): {'; '.join(bad[:5])}. "
                f"`hold records build {to_name} --records-from {from_name}` cuts them afresh")
    return snap, int(to.parser.version), int(src.parser.version)


def _tasks(root: Path, from_name: str, to_name: str, snap, to, versions) -> list[tuple]:
    from app.pack import slicing

    from . import collection as col
    src_dir, dst_dir = col.shard_dir_of(root, from_name), col.shard_dir_of(root, to_name)
    rule = slicing.parts_rule_of(to.slicing)
    last = {}
    for s in snap.shards:
        last[s.slice] = max(last.get(s.slice, 0), s.n)
    return [(str(src_dir / s.path), str((dst_dir / s.path).with_name(Path(s.path).name + ".carry")), to.plugin,
             versions[0], versions[1], rule, s.n == last[s.slice]) for s in snap.shards]


def _staging_db(root: Path, from_name: str, to_name: str, to, snap) -> sqlite3.Connection:
    """The target's build database: the shards table a build writes, its
    rows the source snapshot's parts (from the source's build database),
    and this carry's marks. Claimed for the target's Codex first, as a
    build's staging is; one holding another build is refused."""
    from app.pack import build as pack_build

    from . import records as rec
    from .seed import _mismatch, _rows
    staging = root / to_name / rec.STATE / rec.BUILD
    rec._claim_staging(staging, to)
    src_db = root / from_name / rec.STATE / rec.BUILD / pack_build.BUILD_DIR / pack_build.BUILD_DB
    if not src_db.is_file():
        raise CarryRefused(f"carry: {src_db}: the source has no build database, so nothing names its parts' "
                           f"entries")
    con = pack_build._build_db(staging)
    con.execute(f"CREATE TABLE IF NOT EXISTS {_MARKS}(slice TEXT NOT NULL, n INTEGER NOT NULL, root TEXT NOT NULL, "
                f"PRIMARY KEY(slice, n))")
    held = _rows(con)
    if not held:
        s_con = sqlite3.connect(f"file:{src_db.as_posix()}?mode=ro", uri=True)
        try:
            rows = _rows(s_con)
        finally:
            s_con.close()
        bad = _mismatch(rows, snap)
        if bad:
            raise CarryRefused(f"carry: {src_db} does not describe {from_name}'s snapshot {snap.snapshot_id} "
                               f"({'; '.join(bad[:3])})")
        keep = [rows[(s.slice, s.n)] for s in snap.shards]
        cols = list(keep[0]) if keep else []
        con.execute("BEGIN IMMEDIATE")
        con.executemany(f"INSERT INTO shards({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                        [[r[c] for c in cols] for r in keep])
        con.execute("COMMIT")
    elif set(held) != {(s.slice, s.n) for s in snap.shards}:
        raise CarryRefused(f"carry: {to_name}'s staging holds another build's parts; move it aside")
    return con


# ---- the pass -----------------------------------------------------------------------

def carry(root, to_ref: str, from_ref: str, *, workers: int | None = None, dry_run: bool = False,
          stop: threading.Event | None = None, snapshot: bool = True, sign: bool = True,
          log: Callable[[str], None] = print) -> Result:
    """Every part of `from_ref`'s latest snapshot into `to_ref`'s SHARD, its
    lines carried (module docstring), then — `snapshot` — `to_ref`'s first
    snapshot under the same id, held to the source's part for part, and
    signed where this peer has a key. The caller holds `to_ref`'s write lock;
    the source is only read."""
    from app import parallel

    from . import collection as col
    from . import records as rec
    root = Path(root)
    to_name, to = col.resolve(root, to_ref)
    from_name, src = col.resolve(root, from_ref)
    # PARTS NOT FREEZABLE ARE NOT WRITTEN: a target whose records name a keyed
    # unit names the run they are read in, or its snapshot could name none
    # (09 §9.4 `origin_run`; München, 2026-11-17)
    from . import origin_run
    try:
        origin_run.refuse_without_run(to, f"carry into {to_name} (nothing was written)")
    except CodexError as e:
        raise CarryRefused(str(e)) from None
    # invariant 9: both collections' records are USED here, each through THE gate
    rec.local_collection(root, from_name, src)
    rec.local_collection(root, to_name, to)
    snap, to_v, from_v = _check(root, from_name, src, to_name, to)
    want_run = origin_run.current_run(root, to_name, to)
    dst_dir = col.shard_dir_of(root, to_name)
    if (dst_dir / "snapshots").is_dir() and any(p for p in (dst_dir / "snapshots").glob("*.json")):
        raise CarryRefused(f"carry: {to_name} holds a snapshot already; its parts are never rewritten (02 §2.2)")
    tasks = _tasks(root, from_name, to_name, snap, to, (to_v, from_v))
    out = Result(parts=len(tasks), bytes_before=sum(int(s.bytes) for s in snap.shards),
                 records=sum(int(s.records) for s in snap.shards))
    if dry_run:
        import shutil
        free = shutil.disk_usage(dst_dir if dst_dir.exists() else dst_dir.parent).free
        log(f"[records carry] {to_name}: would carry {len(tasks):,} part(s), {out.records:,} record(s), "
            f"{out.bytes_before / 2**30:.1f} GiB, from {from_name}'s snapshot {snap.snapshot_id} (parser "
            f"{from_v} -> {to_v}: `origin` rewritten, every other byte taken); {free / 2**30:.1f} GiB free at "
            f"{dst_dir}; nothing written")
        out.snapshot = {"from": snap.snapshot_id, "free": free}
        return out
    con = _staging_db(root, from_name, to_name, to, snap)
    t0 = time.monotonic()
    try:
        rows = {(r["slice"], r["n"]): dict(r) for r in con.execute("SELECT * FROM shards")}
        marked = {(r[0], r[1]): r[2] for r in con.execute(f"SELECT slice, n, root FROM {_MARKS}")}
        todo = []
        for t, s in zip(tasks, snap.shards):
            key = (s.slice, s.n)
            final = Path(t[1]).with_name(Path(s.path).name)
            if key in marked and final.is_file() and final.stat().st_size == int(rows[key]["bytes"]):
                out.present += 1
                continue
            todo.append((t, s))
        results = parallel.each_part(carry_part, [t for t, _ in todo], limit=workers)
        try:
            for (_t, s), got in zip(todo, results):
                key = (s.slice, s.n)
                if (got["records"], got["chunks"], got["records_root"]) != \
                        (s.records, s.chunks, s.records_root):
                    Path(got["dst"]).unlink(missing_ok=True)
                    raise CarryRefused(f"carry: {s.path}: {got['records']} records, {got['chunks']} chunks, "
                                       f"records_root {got['records_root'][:23]}… — the snapshot lists "
                                       f"{s.records}, {s.chunks}, {s.records_root[:23]}…; nothing replaced")
                other_runs = [r for r in got.get("runs") or () if want_run is not None and r != want_run]
                if other_runs:
                    Path(got["dst"]).unlink(missing_ok=True)
                    raise CarryRefused(f"carry: {s.path}: its lines were read in run {other_runs[0]}, and "
                                       f"{to_name}'s Codex names run {want_run}; a record is read in the run "
                                       f"its snapshot names (09 §9.4), so nothing was replaced")
                # THE ROW AND THE MARK FIRST, then the file (module docstring)
                con.execute("BEGIN IMMEDIATE")
                con.execute("UPDATE shards SET bytes=?, sha256=?, root=?, written_at=? WHERE slice=? AND n=?",
                            (got["bytes"], got["sha256"], got["root"], int(time.time()), s.slice, s.n))
                con.execute(f"INSERT OR REPLACE INTO {_MARKS}(slice, n, root) VALUES(?,?,?)",
                            (s.slice, s.n, got["root"]))
                con.execute("COMMIT")
                os.replace(got["dst"], Path(got["dst"]).with_name(Path(s.path).name))
                rows[key].update(bytes=got["bytes"], sha256=got["sha256"], root=got["root"])
                out.carried += 1
                out.rewritten += got["changed"]
                if got["example"] and len(out.examples) < 3:
                    out.examples.append(got["example"])
                if out.carried % 500 == 0:
                    log(f"[records carry] {to_name}: {out.carried + out.present:,} of {len(tasks):,} part(s) "
                        f"({time.monotonic() - t0:.0f} s)")
                if stop is not None and stop.is_set():
                    out.interrupted = True
                    break
        finally:
            close = getattr(results, "close", None)
            if close:
                close()
            for left in dst_dir.rglob("*.carry"):
                left.unlink(missing_ok=True)
        out.bytes_after = sum(int(r["bytes"]) for r in rows.values())
    finally:
        con.close()
    out.seconds = time.monotonic() - t0
    for uid, old, new in out.examples:
        log(f"[records carry] {to_name}: {uid}: {old!r} -> {new!r}")
    if out.interrupted:
        log(f"[records carry] {to_name}: STOPPED after {out.carried + out.present:,} of {len(tasks):,} part(s); "
            f"run it again to continue")
        return out
    log(f"[records carry] {to_name}: {len(tasks):,} part(s), {out.records:,} record(s), {out.rewritten:,} "
        f"origin(s) rewritten, {out.carried:,} written and {out.present:,} already in place in "
        f"{out.seconds:.0f} s; {out.bytes_before / 2**30:.2f} -> {out.bytes_after / 2**30:.2f} GiB")
    rec.write_validation(root, to_name)
    if snapshot:
        out.snapshot = _freeze(root, to_name, from_name, snap, sign=sign, log=log)
        _write_marker(root, to_name, src, to, snap, out)
    return out


#: what a carried part must keep of its source part (only its bytes and roots move)
SAME = ("records_root", "chunks_root", "first_id", "last_id", "records", "chunks", "tokens")


def _freeze(root: Path, to_name: str, from_name: str, snap, *, sign: bool, log) -> dict[str, Any]:
    """The target's first snapshot, the source's id, held to the source's
    parts in everything but their bytes; withdrawn when it is not."""
    from . import collection as col
    from . import snapshot as sn
    mine = sn.create(root, to_name, snapshot_id=snap.snapshot_id, log=log)
    theirs = {(s.slice, s.n): s for s in snap.shards}
    bad = []
    if sorted(theirs) != sorted((s.slice, s.n) for s in mine.shards):
        bad.append("another set of parts")
    for s in mine.shards:
        t = theirs.get((s.slice, s.n))
        if t is not None:
            bad += [f"{s.path}: {k}" for k in SAME if getattr(s, k) != getattr(t, k)]
    if (mine.origin_run or None) != (snap.origin_run or None) and snap.origin_run is not None:
        bad.append(f"origin_run {mine.origin_run} against {snap.origin_run}")
    if bad:
        d = col.shard_dir_of(root, to_name) / "snapshots"
        for p in (d / f"{mine.snapshot_id}.json", d / f"{mine.snapshot_id}.sig.json"):
            p.unlink(missing_ok=True)
        raise CarryRefused(f"carry: {to_name}'s snapshot is not {from_name}'s parts carried ({'; '.join(bad[:5])}); "
                           f"it is withdrawn")
    signed = 0
    if sign:
        try:
            from app.bundles import keys as node_keys
            key = node_keys.load_signing_key()
        except Exception as e:                      # noqa: BLE001 — an unsigned peer still works
            log(f"[records carry] {to_name}: snapshot {mine.snapshot_id} unsigned — {e}")
        else:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            signed = len(sn.sign_snapshot(root, to_name, mine, key, now=now)["signatures"])
    return {"snapshot_id": mine.snapshot_id, "hash": mine.hash, "origin_run": mine.origin_run,
            "signatures": signed}


def _write_marker(root: Path, to_name: str, src, to, snap, out: Result) -> None:
    p = root / to_name / "state" / MARKER
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"from": src.collection_id, "to": to.collection_id,
            "from_snapshot": {"id": snap.snapshot_id, "hash": snap.hash},
            "to_snapshot": {"id": out.snapshot["snapshot_id"], "hash": out.snapshot["hash"]},
            "parser": {"from": src.parser.version, "to": to.parser.version},
            "parts": out.parts, "records": out.records, "rewritten": out.rewritten,
            "proven": "every part: the same uids in the same order, records_root, records and chunks; "
                      "every other key of every line taken byte for byte", "done": True}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def proven(root, from_name: str, to_name: str, src, to) -> dict[str, Any] | None:
    """This carry's proof, when it holds for the two collections' LATEST
    snapshots: every record of the target's is the source's, content hash
    for content hash (`dev carry --reconcile` reads it instead of both
    snapshots). None otherwise."""
    from .seed import latest_snapshot
    p = Path(root) / to_name / "state" / MARKER
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not body.get("done") or body.get("from") != src.collection_id or body.get("to") != to.collection_id:
        return None
    try:
        a, b = latest_snapshot(Path(root), from_name), latest_snapshot(Path(root), to_name)
    except CodexError:
        return None
    if (a.snapshot_id, a.hash) != (body["from_snapshot"]["id"], body["from_snapshot"]["hash"]) or \
            (b.snapshot_id, b.hash) != (body["to_snapshot"]["id"], body["to_snapshot"]["hash"]):
        return None
    return body
