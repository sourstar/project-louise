"""THE STREAMING CREATE of an annotations collection (operator, 2026-11-10;
docs/notes/wikipedia-one-version.md "Measured: the size": wikipedia's
12,326,519 same-work claims about 16.8 M of its 52.9 M records).

`create` held every claim three times — its raw line, its parsed dict, its
pinned copy — and routed each claimed uid through every part whose id range
holds it: on wikipedia, ~170 of 26,551 ranges a uid. Here the claims file is
READ, never held, and `create` writes byte for byte what it wrote
(tests/hold/test_annotations_stream.py):

1. `survey` streams the file: every line parsed and checked (`check_claim`),
   a claim made twice found by the BLAKE2b-128 of its identity
   (`identity_key`), each claim kept as three integers — its line, where its
   bytes start and how many — and each end as the BLAKE2b-128 of its uid,
   the hash 09 §9.5 already trusts with an identity (`origin_hash`);
2. `read_target` reads each target ONCE, part by part in its own order: only
   the parts whose id range can hold a wanted uid, only the frames that do;
3. `check_pins` pins every claim as `pin` does and gives the refusal `pin`
   gives — the first claim in the file's order that `pin` refuses — and
   hands each claim's numbering key (`_number_order`) to an `ExternalSort`;
4. `ClaimParts` reads the claims back in that order by their offsets and
   writes each part as `records_of` and `write_parts` made it.

Each pass is cut into tasks for the parse pool (`parallel.each_part`: in
order, a child the kernel kills costing only a retry) — the file by byte
ranges at line ends, the claims by ranges of the file's order, the parts by
number — and the targets' tables go to the workspace as files the children
map. A file given as bytes is read here, in this process. One code path for
one claim and for twelve million: the pool's serial path is the same
functions, and a sort run spills to disk only once it is full. What stays in
memory is ~60 bytes a claim (its integers and the digests of its ends), one
sort run and, per process, one part.
"""
from __future__ import annotations

import hashlib
import heapq
import io
import itertools
import json
import os
import pickle
import re
import secrets
import shutil
from array import array
from collections import Counter
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence as _Sequence
from typing import Any, Iterable, Iterator, Sequence

import numpy as np

#: claims a sort run holds before it spills to disk (~250 bytes each)
RUN_ITEMS = 1 << 19
#: items one pickled batch of a spilled run carries
SPILL_BATCH = 4096
#: claims read, looked up and pinned together (one vectorised search a batch)
BATCH = 8192
#: bytes of the claims file one survey task reads
SURVEY_BYTES = 32 << 20
#: claims one pinning task checks, in the file's order
PIN_CLAIMS = 50_000
#: what the parts written at once may hold: a JSONL part of 50,000 claims
#: peaked at ~230 MB in its process (tracemalloc, 2026-11-10), ~5 KB a claim,
#: so a Parquet part of 1,000,000 is written one at a time
PARTS_BUDGET = 2 << 30
PART_BYTES_PER_CLAIM = 5 << 10
#: the private copy of the claims file, in the create's workspace
COPY = "claims.jsonl"

_I64_MIN, _I64_MAX = -(1 << 63), (1 << 63) - 1
_HEX32 = re.compile(r"[0-9a-f]{32}")


def uid_digest(uid: str) -> bytes:
    """A uid's key in the tables here: its BLAKE2b-128 (surrogatepass: a
    uid JSON gave with a lone surrogate still has one, and no other)."""
    return hashlib.blake2b(uid.encode("utf-8", "surrogatepass"), digest_size=16).digest()


def identity_digest(claim: dict[str, Any]) -> bytes:
    """A claim's identity (`identity_key`) as 16 bytes: the BLAKE2b-128 of
    the key's repr, which is injective for its str, bytes and int parts."""
    from .annotations import identity_key
    return hashlib.blake2b(repr(identity_key(claim)).encode("utf-8", "surrogatepass"),
                           digest_size=16).digest()


# --------------------------------------------------------------------------
# the claims file

class ClaimsSource:
    """The claims file — bytes in memory, or a file on disk — read line by
    line as `read_claims` splits it, and a line again by its offset."""

    def __init__(self, claims: bytes | str | os.PathLike, *, owned: bool = False) -> None:
        if isinstance(claims, (bytes, bytearray, memoryview)):
            self.data: bytes | None = bytes(claims)
            self.path: Path | None = None
            self._fd: int | None = None
        else:
            self.data, self.path = None, Path(claims)
            self._fd = os.open(self.path, os.O_RDONLY)
        #: a private copy this create made, which `install` moves into place
        self.owned = owned

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "ClaimsSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def ref(self) -> "Path | ClaimsSource":
        """What a task names the source by: the file's path, which a pool
        child opens; bytes are read in this process (`_limit`)."""
        return self.path if self.path is not None else self

    @property
    def size(self) -> int:
        return len(self.data) if self.data is not None else os.fstat(self._fd).st_size

    def physical(self, start: int = 0, end: int | None = None) -> Iterator[tuple[int, int, bytes]]:
        """(line number from 1 within the range, offset, the line with its
        newline) for every line from byte `start` — a line start — to `end`."""
        if self.data is not None:
            f = io.BytesIO(self.data if start == 0 and end is None else self.data[start:end])
        else:
            f = open(self.path, "rb", buffering=1 << 20)
            f.seek(start)
        with f:
            offset = start
            for i, line in enumerate(f, 1):
                if end is not None and offset >= end:
                    return
                yield i, offset, line
                offset += len(line)

    def lines(self, start: int = 0, end: int | None = None) -> Iterator[tuple[int, int, bytes]]:
        """(line number, offset of its first byte, the line without its
        newline and one trailing CR) for every line that is not blank —
        `read_claims`'s `split(b"\\n")`, read as it streams."""
        for i, offset, line in self.physical(start, end):
            raw = _raw(line)
            if raw.strip():
                yield i, offset, raw

    def read(self, offset: int, length: int) -> bytes:
        if self.data is not None:
            return self.data[offset:offset + length]
        got = os.pread(self._fd, length, offset)
        if len(got) != length:
            from .annotations import AnnotationsError
            raise AnnotationsError(f"{self.path}: the claims file changed while it was read")
        return got

    def install(self, dst: Path) -> None:
        """The claims as submitted, as the collection's origin (02 §2.10):
        the bytes written, or the private copy moved into place whole."""
        if self.data is not None:
            dst.write_bytes(self.data)
            return
        if not self.owned:
            raise ValueError("install: only a private copy is moved")
        from . import shard as shard_mod
        self.close()
        shard_mod._move(self.path, dst)


def _raw(line: bytes) -> bytes:
    raw = line[:-1] if line.endswith(b"\n") else line
    return raw[:-1] if raw.endswith(b"\r") else raw


def _opened(ref: "Path | ClaimsSource"):
    """A task's source: its own handle on the file, or the source itself."""
    return nullcontext(ref) if isinstance(ref, ClaimsSource) else ClaimsSource(ref)


def _limit(src: ClaimsSource, cap: int | None = None, tables: "Held | Path | None" = None) -> int | None:
    """The processes a pass over `src` may use: one for bytes, which only
    this process holds, and for tables not saved for the children to map
    (`Held.save`); else the pool's, at most `cap`."""
    return 1 if src.data is not None or isinstance(tables, Held) else cap


@contextmanager
def workspace(root: Path, tag: str) -> Iterator[Path]:
    """A scratch directory beside the collections — the private copy of the
    claims file, the targets' tables, the sort's runs — named by `tag` (a
    create's is its collection's name, so that one a killed create left is
    cleared by the next) and removed when the pass ends however it ends."""
    d = Path(root) / f".annotations-{tag}"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def private_source(claims: bytes | str | os.PathLike, work: Path) -> ClaimsSource:
    """The source a create reads: a COPY of the claims, in the workspace —
    of a file, so that every pass reads the very bytes the first checked and
    the origin installed is those bytes whatever happens to the file given
    meanwhile; of bytes, so that the pool's children can read them too."""
    copy = Path(work) / COPY
    if isinstance(claims, (bytes, bytearray, memoryview)):
        copy.write_bytes(claims)
    else:
        shutil.copyfile(claims, copy)
    return ClaimsSource(copy, owned=True)


# --------------------------------------------------------------------------
# pass 1: the survey

def _key_summary(key: Any, order: str) -> int:
    """A target id-order key as 64 bits, never decreasing as the key grows:
    the integer clamped (numeric), the first eight bytes (bytes). Equal
    summaries say nothing; a summary outside a part's range says the key is
    outside it too."""
    if order == "numeric":
        return min(max(key, _I64_MIN), _I64_MAX)
    return int.from_bytes(key[:8].ljust(8, b"\0"), "big")


def _uid_summary(uid: str, order: str) -> int | None:
    from .records import RecordsError, order_key
    try:
        return _key_summary(order_key(uid.split(":", 1)[1], order), order)
    except (RecordsError, UnicodeError):
        return None                     # no part's range can hold it (`shards_of`)


@dataclass
class Ends:
    """One target plugin's claim ends: each uid named as its BLAKE2b-128,
    sorted and once; their places in the target's id order (`_key_summary`),
    sorted, which tell a part that may hold one — None when the order is not
    known yet; the smallest uid (a refusal's example); the uids a chunk end
    names, whose text is re-cut."""
    digests: np.ndarray
    summaries: np.ndarray | None
    first: str
    text_for: set[str]

    def may_hold(self, lo: int, hi: int) -> bool:
        s = self.summaries
        if s is None:
            return True
        return int(np.searchsorted(s, lo, "left")) < int(np.searchsorted(s, hi, "right"))


class _EndsBuf:
    def __init__(self, order: str | None) -> None:
        self.order = order
        self.digests = bytearray()
        self.summaries = array("q" if order == "numeric" else "Q") if order else None
        self.first: str | None = None
        self.text_for: set[str] = set()

    def add(self, uid: str, chunk: bool) -> None:
        self.digests += uid_digest(uid)
        if self.first is None or uid < self.first:
            self.first = uid
        if chunk:
            self.text_for.add(uid)
        if self.summaries is not None:
            s = _uid_summary(uid, self.order)
            if s is not None:
                self.summaries.append(s)

    def merge(self, part: dict[str, Any]) -> None:
        """A survey task's ends of this plugin, after the ones before it."""
        self.digests += part["digests"]
        if self.summaries is not None:
            self.summaries.frombytes(part["summaries"])
        if self.first is None or part["first"] < self.first:
            self.first = part["first"]
        self.text_for |= part["text_for"]

    def task(self) -> dict[str, Any]:
        return {"digests": bytes(self.digests),
                "summaries": self.summaries.tobytes() if self.summaries is not None else b"",
                "first": self.first, "text_for": self.text_for}

    def done(self) -> Ends:
        digests = np.unique(np.frombuffer(self.digests, dtype="S16"))
        summaries = (np.unique(np.frombuffer(self.summaries, dtype=np.int64 if self.order == "numeric"
                                             else np.uint64)) if self.summaries is not None else None)
        self.digests = self.summaries = None
        return Ends(digests, summaries, self.first or "", self.text_for)


@dataclass
class Survey:
    """What the first pass kept of the claims file: claim k (file order) is
    `lines[k]`, `lengths[k]` bytes from `offsets[k]`; the ends by plugin, in
    the order the claims first name them; what a preview counts."""
    count: int
    lines: np.ndarray
    offsets: np.ndarray
    lengths: np.ndarray
    ends: dict[str, Ends]
    contributors: set[str]
    graph: bool
    by_type: Counter
    by_method: Counter

    @property
    def records_named(self) -> int:
        """The distinct target records the claims' ends name."""
        return sum(len(e.digests) for e in self.ends.values())


def _first_repeat(idents: bytearray, lines: Sequence[int]) -> tuple[int, int] | None:
    """(line, earlier line) of the first claim in the file that repeats an
    earlier one — the refusal `read_claims` gives at that line — or None."""
    n = len(idents) // 16
    if n < 2:
        return None
    a = np.frombuffer(bytes(idents), dtype=">u8").astype(np.uint64).reshape(n, 2)
    order = np.lexsort((a[:, 1], a[:, 0]))          # stable: a repeat sorts after its first
    s = a[order]
    same = (s[1:] == s[:-1]).all(axis=1)
    if not same.any():
        return None
    starts = np.flatnonzero(same & ~np.concatenate(([False], same[:-1])))
    second = order[starts + 1]
    g = int(np.argmin(second))
    return int(lines[int(second[g])]), int(lines[int(order[starts[g]])])


def _refuse_repeat(idents: bytearray, lines: Sequence[int]) -> None:
    got = _first_repeat(idents, lines)
    if got is not None:
        from .annotations import AnnotationsError
        raise AnnotationsError(f"claims line {got[0]}: the same claim as line {got[1]}")


def _check_line(i: int, raw: bytes, plugins: tuple[str, ...]) -> tuple[dict[str, Any], bytes]:
    """Line `i` as `read_claims` reads it — refused with its words — and its
    identity digest."""
    from .annotations import AnnotationsError, check_claim
    try:
        obj = json.loads(raw)
    except ValueError as e:
        raise AnnotationsError(f"claims line {i}: not JSON ({e})") from None
    c = check_claim(obj, f"claims line {i}", target_plugins=plugins)
    return c, identity_digest(c)


def survey_part(ref, start: int, end: int, plugins: tuple[str, ...], orders: dict[str, str]) -> dict[str, Any]:
    """A survey task: the lines from byte `start` to `end`, numbered from 1
    within the range, up to the first refused (`error`: its line, offset and
    length) — every claim's three integers and identity, its ends, what a
    preview counts, and how many lines ended in the range (`newlines`)."""
    from .annotations import CHUNK, GRAPH_TYPES, _method_name, _prefix, end_uid, ends_of
    lines, offsets, lengths = array("q"), array("q"), array("q")
    idents = bytearray()
    bufs: dict[str, _EndsBuf] = {}
    contributors: set[str] = set()
    by_type: Counter = Counter()
    by_method: Counter = Counter()
    graph, newlines, error = False, 0, None
    with _opened(ref) as src:
        for i, offset, line in src.physical(start, end):
            raw = _raw(line)
            if line.endswith(b"\n"):
                newlines += 1
            if not raw.strip():
                continue
            try:
                c, ident = _check_line(i, raw, plugins)
            except Exception:           # noqa: BLE001 — the parent raises it, with its line's number
                error = (i, offset, len(raw))
                break
            idents += ident
            lines.append(i)
            offsets.append(offset)
            lengths.append(len(raw))
            t = c["type"]
            graph = graph or t in GRAPH_TYPES
            by_type[t] += 1
            by_method[_method_name(c["method"])] += 1
            contributors.add(c["contributor"])
            for kind, value in ends_of(c):
                uid = end_uid(kind, value)
                p = _prefix(uid)
                buf = bufs.get(p)
                if buf is None:
                    buf = bufs[p] = _EndsBuf(orders.get(p))
                buf.add(uid, kind == CHUNK)
    return {"lines": lines.tobytes(), "offsets": offsets.tobytes(), "lengths": lengths.tobytes(),
            "idents": bytes(idents), "ends": {p: b.task() for p, b in bufs.items()},
            "contributors": contributors, "by_type": by_type, "by_method": by_method, "graph": graph,
            "newlines": newlines, "error": error}


def _cuts(src: ClaimsSource) -> list[tuple[int, int]]:
    """The file as byte ranges of about SURVEY_BYTES, each ending just after
    a newline (the last at the end)."""
    size, out, start = src.size, [], 0
    while start < size:
        end = min(start + SURVEY_BYTES, size)
        while end < size:
            block = src.read(end, min(1 << 16, size - end))
            j = block.find(b"\n")
            if j >= 0:
                end += j + 1
                break
            end += len(block)
        out.append((start, end))
        start = end
    return out


def survey(src: ClaimsSource, *, plugins: tuple[str, ...] = (),
           orders: dict[str, str] | None = None) -> Survey:
    """Pass 1: the claims file checked as `read_claims` checks it — the
    first refusal in the file's order is the one it gives — and kept as
    `Survey`. `orders` (plugin -> the target's `slicing.order`) places the
    ends for `read_target`; without it every part is a candidate."""
    from app import parallel
    from .annotations import AnnotationsError
    orders = dict(orders or {})
    lines, offsets, lengths = array("q"), array("q"), array("q")
    idents = bytearray()
    bufs: dict[str, _EndsBuf] = {}
    contributors: set[str] = set()
    by_type: Counter = Counter()
    by_method: Counter = Counter()
    graph, base = False, 0
    tasks = [(src.ref, s, e, tuple(plugins), orders) for s, e in _cuts(src)]
    for part in parallel.each_part(survey_part, tasks, limit=_limit(src)):
        lines.frombytes((np.frombuffer(part["lines"], dtype=np.int64) + base).tobytes())
        offsets.frombytes(part["offsets"])
        lengths.frombytes(part["lengths"])
        idents += part["idents"]
        for p, e in part["ends"].items():
            if p not in bufs:
                bufs[p] = _EndsBuf(orders.get(p))
            bufs[p].merge(e)
        contributors |= part["contributors"]
        by_type.update(part["by_type"])
        by_method.update(part["by_method"])
        graph = graph or part["graph"]
        if part["error"] is not None:
            i, offset, length = part["error"]
            _refuse_repeat(idents, lines)
            _check_line(base + i, src.read(offset, length), tuple(plugins))
            raise AnnotationsError(f"claims line {base + i}: refused once and not again — the claims file "
                                   f"changed while it was read")
        base += part["newlines"]
    if not lines:
        raise AnnotationsError("the claims file holds no claim")
    _refuse_repeat(idents, lines)
    del idents
    return Survey(len(lines), np.frombuffer(lines, dtype=np.int64), np.frombuffer(offsets, dtype=np.int64),
                  np.frombuffer(lengths, dtype=np.int64), {p: b.done() for p, b in bufs.items()},
                  contributors, graph, by_type, by_method)


# --------------------------------------------------------------------------
# the target, read once

@dataclass
class Table:
    """One target's records as the claims name them: slot j is the uid whose
    digest is `digests[j]`; `found[j]` says the target snapshot holds it,
    `hashes[16j:16j+16]` is its content_hash (one that is not 32 lowercase
    hex is in `odd`, as given); `texts` the text of a chunk end's record."""
    digests: np.ndarray
    found: np.ndarray
    hashes: np.ndarray
    odd: dict[int, Any]
    texts: dict[str, dict[str, Any]]

    @classmethod
    def empty(cls, digests: np.ndarray) -> "Table":
        m = len(digests)
        return cls(digests, np.zeros(m, dtype=np.uint8), np.zeros(16 * m, dtype=np.uint8), {}, {})

    def put(self, j: int, content_hash: Any) -> None:
        self.found[j] = 1
        if isinstance(content_hash, str) and _HEX32.fullmatch(content_hash):
            self.hashes[16 * j:16 * j + 16] = np.frombuffer(bytes.fromhex(content_hash), dtype=np.uint8)
            self.odd.pop(j, None)
        else:
            self.odd[j] = content_hash

    def content_hash(self, j: int) -> Any:
        return self.odd[j] if j in self.odd else self.hashes[16 * j:16 * j + 16].tobytes().hex()

    @property
    def held(self) -> int:
        return int(np.count_nonzero(self.found))


def _search(digests: np.ndarray, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(slot, hit) of each digest of `q` in the sorted `digests`."""
    m = len(digests)
    at = np.searchsorted(digests, q)
    hit = at < m
    hit[hit] = digests[at[hit]] == q[hit]
    return at, hit


def read_part(tree: str, shard, current_id: str | None, snapshot_id: str, digests, f, l, order: str,
              text_for: set[str]) -> list[tuple[int, Any, str, dict[str, Any] | None]]:
    """A target-reading task: of one part of the target, every record whose
    uid is wanted (`digests`, sorted; or the .npy file holding them) and
    which the part's id range holds — (its slot, its content_hash, its uid,
    its text where a chunk end names it) — each frame that holds one decompressed
    once, in the part's order."""
    from app.pack import records as pack_records
    from .annotations import _part_file
    from .records import RecordsError, order_key
    path = _part_file(Path(tree), shard, current_id, snapshot_id)
    if path is None:
        return []
    if not isinstance(digests, np.ndarray):
        digests = np.load(digests, mmap_mode="r")
    index = pack_records.read_index(path)
    uids = list(index.uids)
    if not uids:
        return []
    at, hit = _search(digests, np.frombuffer(b"".join(map(uid_digest, uids)), dtype="S16"))
    frames: dict[int, list[tuple[str, int, int]]] = {}
    for j in np.flatnonzero(hit).tolist():
        uid = uids[j]
        try:
            k = order_key(uid.split(":", 1)[1], order)
        except (RecordsError, UnicodeError):
            continue
        if not f <= k <= l:
            continue                    # this part's range does not hold it: `shards_of` never sent it here
        frame, line = index.uids[uid]
        frames.setdefault(frame, []).append((uid, line, int(at[j])))
    out: list[tuple[int, Any, str, dict[str, Any] | None]] = []
    for frame, items in sorted(frames.items()):
        lines = pack_records.read_frame(path, index, frame)
        for uid, line, slot in items:
            r = pack_records.loads_line(lines[line])
            text = ({"uid": r["uid"], "native_id": r["native_id"], "title": r.get("title"),
                     "sections": r.get("sections")} if uid in text_for else None)
            out.append((slot, r["content_hash"], uid, text))
    return out


def read_target(target, ends: Ends, work: Path | None = None) -> Table:
    """`target_records` for the uids `ends` names, into a `Table`: the same
    records — a uid is taken from a part whose id range holds it (`shards_of`)
    and whose index lists it, a later part in (slice, n) order over an earlier
    — read in the target's own order, each part once and only where its range
    can hold a wanted uid, each index once, and only the frames that hold one
    decompressed. The cost is the claims' and the parts they touch, never a
    uid times the parts. A part a task (`read_part`), on the pool where a
    workspace `work` holds the wanted digests for the children to map."""
    from app import parallel
    from app.share import seeder
    from .annotations import _target_snapshot
    tree = Path(target.shard_tree)
    snap = _target_snapshot(tree, target.from_snapshot)
    table = Table.empty(ends.digests)
    current = seeder.current_snapshot(tree)
    current_id = current.snapshot_id if current is not None else None
    order = (target.codex.slicing or {}).get("order", "bytes")
    keyed = snap._keyed(order) if len(ends.digests) else None
    if keyed is None:
        return table                    # no bound this order can place: no part holds a uid
    parts: dict[tuple[str, int], tuple[Any, Any, Any]] = {}
    for f, l, _w, s in keyed:
        parts.setdefault((s.slice, s.n), (f, l, s))
    digests: Any = ends.digests
    if work is not None:
        digests = Path(work) / f"wanted-{secrets.token_hex(6)}.npy"
        np.save(digests, ends.digests)
    tasks = [(str(tree), shard, current_id, target.from_snapshot, digests, f, l, order, ends.text_for)
             for _key, (f, l, shard) in sorted(parts.items(), key=lambda kv: kv[0])
             if ends.may_hold(_key_summary(f, order), _key_summary(l, order))]
    for got in parallel.each_part(read_part, tasks, limit=None if work is not None else 1):
        for slot, content_hash, uid, text in got:
            table.put(slot, content_hash)
            if text is not None:
                table.texts[uid] = text
    return table


class Held:
    """`pin`'s `held` — uid -> {content_hash, …} — for a batch of claims at
    a time (`load`), looked up in the targets' tables by one vectorised
    search a plugin."""

    def __init__(self, tables: dict[str, Table]) -> None:
        self.tables = tables
        self._batch: dict[str, dict[str, Any]] = {}

    def load(self, uids: Iterable[str]) -> None:
        from .annotations import _prefix
        by: dict[str, list[str]] = {}
        for u in uids:
            by.setdefault(_prefix(u), []).append(u)
        batch: dict[str, dict[str, Any]] = {}
        for p, us in by.items():
            t = self.tables.get(p)
            if t is None or not len(t.digests):
                continue
            at, hit = _search(t.digests, np.frombuffer(b"".join(map(uid_digest, us)), dtype="S16"))
            for i in np.flatnonzero(hit).tolist():
                j = int(at[i])
                if t.found[j]:
                    entry = {"content_hash": t.content_hash(j)}
                    entry.update(t.texts.get(us[i], {}))
                    batch[us[i]] = entry
        self._batch = batch

    def get(self, uid: str) -> dict[str, Any] | None:
        return self._batch.get(uid)

    def save(self, directory: Path) -> Path:
        """The tables as files a pool child maps (`held_of`): numpy's .npy,
        read with `mmap_mode`, so that every child shares the page cache's
        one copy of them."""
        d = Path(directory) / f"tables-{secrets.token_hex(6)}"
        d.mkdir()
        meta: dict[str, tuple[int, dict, dict]] = {}
        for i, (p, t) in enumerate(self.tables.items()):
            for name in ("digests", "found", "hashes"):
                np.save(d / f"{i}.{name}.npy", getattr(t, name))
            meta[p] = (i, t.odd, t.texts)
        with open(d / "tables.pickle", "wb") as f:
            pickle.dump(meta, f, protocol=pickle.HIGHEST_PROTOCOL)
        return d


#: tables directory -> its tables, mapped: once a process. Keyed on the path,
#: whose name is drawn afresh for every create (`Held.save`)
_MAPPED: dict[str, Held] = {}


def held_of(tables: "Held | Path") -> Held:
    """A task's tables: the ones given, or the files `Held.save` wrote."""
    if isinstance(tables, Held):
        return tables
    key = str(tables)
    held = _MAPPED.get(key)
    if held is None:
        with open(Path(tables) / "tables.pickle", "rb") as f:
            meta = pickle.load(f)
        held = Held({p: Table(*(np.load(Path(tables) / f"{i}.{name}.npy", mmap_mode="r")
                                for name in ("digests", "found", "hashes")), odd, texts)
                     for p, (i, odd, texts) in meta.items()})
        _MAPPED.clear()                 # one create at a time a process
        _MAPPED[key] = held
    return held


def forget() -> None:
    """Unmap the tables this process mapped: a create's workspace is gone
    when it ends, and a mapped file holds its disk until it is unmapped."""
    _MAPPED.clear()


# --------------------------------------------------------------------------
# the order the claims are numbered in

class ExternalSort:
    """Items in sorted order beyond memory: runs of `run_items` sorted here
    and spilled to `directory` as pickled batches, merged by `heapq.merge` —
    the external merge sort (Knuth, TAOCP vol. 3 §5.4.1; what sort(1) does
    with its temporary files). An item that carries its ordinal last keeps
    equal keys in the order they were added: Python's stable sort."""

    def __init__(self, directory: Path, run_items: int | None = None) -> None:
        self.directory = Path(directory)
        self.run_items = run_items or RUN_ITEMS
        self._buf: list = []
        self._runs: list[Path] = []

    def add(self, item) -> None:
        self._buf.append(item)
        if len(self._buf) >= self.run_items:
            self._spill()

    def _spill(self) -> None:
        self._buf.sort()
        p = self.directory / f"run-{len(self._runs):05d}.pickle"
        with open(p, "wb") as f:
            for batch in itertools.batched(self._buf, SPILL_BATCH):
                pickle.dump(batch, f, protocol=pickle.HIGHEST_PROTOCOL)
        self._runs.append(p)
        self._buf = []

    @staticmethod
    def _read(p: Path) -> Iterator:
        with open(p, "rb") as f:
            while True:
                try:
                    batch = pickle.load(f)
                except EOFError:
                    return
                yield from batch

    def __iter__(self) -> Iterator:
        self._buf.sort()
        if not self._runs:
            return iter(self._buf)
        return heapq.merge(*[self._read(p) for p in self._runs], iter(self._buf))


# --------------------------------------------------------------------------
# pass 2: every claim pinned, in the file's order

def _claims_of(raws: Iterable[bytes]) -> list[dict[str, Any]]:
    from .annotations import _claim_of_line
    return [_claim_of_line(r) for r in raws]


def _uids_of(claims: Iterable[dict[str, Any]]) -> Iterator[str]:
    from .annotations import end_uid, ends_of
    for c in claims:
        for kind, value in ends_of(c):
            yield end_uid(kind, value)


class _Missing:
    """`pin`'s list of the ends not held, as its refusal reads it: how many,
    and the first eight in the file's order."""

    def __init__(self) -> None:
        self.count, self.first = 0, []

    def append(self, what: str) -> None:
        self.count += 1
        if len(self.first) < 8:
            self.first.append(what)


def _read_in_order(src: ClaimsSource, offsets: np.ndarray, lengths: np.ndarray) -> list[bytes]:
    """The claims at `offsets`, each read once, in the file's order."""
    raws: list[bytes] = [b""] * len(offsets)
    for j in np.argsort(offsets, kind="stable").tolist():
        raws[j] = src.read(int(offsets[j]), int(lengths[j]))
    return raws


def pin_part(ref, tables, k0: int, offsets: np.ndarray, lengths: np.ndarray, lines: np.ndarray,
             snapshots: dict[str, str], codices: dict[str, Any], version: int, keys: bool) -> dict[str, Any]:
    """A pinning task: claims k0, k0+1, … of the file pinned (`_pin_one`) up
    to the first `pin` would refuse at once (`error`, its ordinal); the ends
    not held counted; each pinned claim's numbering key, with `keys`."""
    from .annotations import Submitted, _number_order, _pin_one
    held = held_of(tables)
    with _opened(ref) as src:
        raws = _read_in_order(src, offsets, lengths)
    missing, out, error = _Missing(), [], None
    for b0 in range(0, len(raws), BATCH):
        claims = _claims_of(raws[b0:b0 + BATCH])
        held.load(_uids_of(claims))
        for j, c in enumerate(claims, b0):
            try:
                p = _pin_one(Submitted(int(lines[j]), raws[j], c), held, None, snapshots, codices, missing)
            except Exception:           # noqa: BLE001 — the parent raises it, as `pin` would
                error = k0 + j
                break
            if p is not None and keys:
                out.append((_number_order(p, version), k0 + j))
        if error is not None:
            break
    return {"keys": out, "count": missing.count, "first": missing.first, "error": error}


def check_pins(src: ClaimsSource, sv: Survey, tables: "Held | Path", *, snapshots: dict[str, str],
               codices: dict[str, Any], sort: ExternalSort | None, version: int) -> None:
    """Pass 2: every claim pinned as `pin` pins it — the ends not held
    counted and refused together at the end, any other refusal raised at the
    first claim in the file's order that has one — and its numbering key
    (`_number_order`) with its ordinal added to `sort`. Nothing is kept;
    pass 3 pins again what it writes."""
    from app import parallel
    from .annotations import AnnotationsError, Submitted, _not_held, _pin_one
    tasks = [(src.ref, tables, a, sv.offsets[a:a + PIN_CLAIMS], sv.lengths[a:a + PIN_CLAIMS],
              sv.lines[a:a + PIN_CLAIMS], snapshots, codices, version, sort is not None)
             for a in range(0, sv.count, PIN_CLAIMS)]
    count, first = 0, []
    for got in parallel.each_part(pin_part, tasks, limit=_limit(src, None, tables)):
        if got["error"] is not None:
            k = got["error"]
            raw = src.read(int(sv.offsets[k]), int(sv.lengths[k]))
            (c,) = _claims_of([raw])
            held = held_of(tables)
            held.load(_uids_of([c]))
            _pin_one(Submitted(int(sv.lines[k]), raw, c), held, None, snapshots, codices, _Missing())
            raise AnnotationsError(f"claims line {int(sv.lines[k])}: refused once and not again — the claims "
                                   f"file or the target changed while it was read")
        count += got["count"]
        first.extend(got["first"][:8 - len(first)])
        if sort is not None:
            for item in got["keys"]:
                sort.add(item)
    if count:
        raise _not_held(count, first)


# --------------------------------------------------------------------------
# pass 3: the parts

def write_part(ref, tables, staging: str, n: int, first_number: int, offsets: np.ndarray,
               lengths: np.ndarray, lines: np.ndarray, snapshots: dict[str, str], codices: dict[str, Any],
               snapshot_id: str, container: str):
    """A part task: part `n`, claims numbered from `first_number` — given in
    numbering order by their offsets — read back, pinned again, made records
    (`_record`) and written as `write_parts` writes a part (`_write_part`)."""
    from .annotations import AnnotationsError, Submitted, _pin_one, _record, _write_part
    held = held_of(tables)
    with _opened(ref) as src:
        raws = _read_in_order(src, offsets, lengths)
    part = []
    for b0 in range(0, len(raws), BATCH):
        claims = _claims_of(raws[b0:b0 + BATCH])
        held.load(_uids_of(claims))
        for j, c in enumerate(claims, b0):
            s = Submitted(int(lines[j]), raws[j], c)
            lost: list[str] = []
            p = _pin_one(s, held, None, snapshots, codices, lost)
            if p is None or lost:
                raise AnnotationsError(f"claims line {s.line}: pinned once and not again — the claims "
                                       f"file or the target changed while the collection was written")
            part.append(_record(first_number + j, p, s))
    del raws
    return _write_part(Path(staging), n, part, snapshot_id, container)


class _PartTasks(_Sequence):
    """Part i's task, made when the pool asks for it (`parallel.each_part`
    asks again for the parts a killed child left)."""

    def __init__(self, parts: "ClaimParts", staging: Path, snapshot_id: str, per_part: int, container: str):
        self.p, self.staging, self.snapshot_id = parts, str(staging), snapshot_id
        self.per_part, self.container = per_part, container

    def __len__(self) -> int:
        return -(-self.p.survey.count // self.per_part)

    def __getitem__(self, i: int):
        if not 0 <= i < len(self):
            raise IndexError(i)
        ks = self.p.order[i * self.per_part:(i + 1) * self.per_part]
        sv = self.p.survey
        return (self.p.src.ref, self.p.tables, self.staging, i + 1, i * self.per_part + 1, sv.offsets[ks],
                sv.lengths[ks], sv.lines[ks], self.p.snapshots, self.p.codices, self.snapshot_id, self.container)


class ClaimParts:
    """`write_snapshot`'s records for a streamed create: the claims in
    numbering order (`order[n - 1]` is claim n's ordinal in the file), written
    a part a task (`write_parts`)."""

    def __init__(self, src: ClaimsSource, survey: Survey, sort: ExternalSort, tables: "Held | Path",
                 snapshots: dict[str, str], codices: dict[str, Any]) -> None:
        self.src, self.survey, self.tables = src, survey, tables
        self.snapshots, self.codices = snapshots, codices
        self.order = np.fromiter((k for _key, k in sort), dtype=np.int64, count=survey.count)

    def write_parts(self, staging: Path, snapshot_id: str, *, shard_records: int,
                    container: str) -> tuple[tuple, tuple, list[str]]:
        """(shards, relations, uids): `write_parts`'s entries for these
        records, and every record's uid."""
        from app import parallel
        from .annotations import PLUGIN
        tasks = _PartTasks(self, staging, snapshot_id, shard_records, container)
        cap = max(1, PARTS_BUDGET // (shard_records * PART_BYTES_PER_CLAIM))
        shards, relations = [], []
        for s, r in parallel.each_part(write_part, tasks, limit=_limit(self.src, cap, self.tables)):
            shards.append(s)
            relations.append(r)
        count = self.survey.count
        self.survey = self.order = self.tables = None           # what the snapshot's delta no longer needs
        return tuple(shards), tuple(relations), [f"{PLUGIN}:{n}" for n in range(1, count + 1)]
