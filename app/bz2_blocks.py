"""THE BLOCK INDEX OF ONE BZIP2 STREAM — where each compressed block of a
single-stream `.bz2` file begins, in bits, and where its bytes fall in the
decompressed file; with, per block, how many of the file's UNIT LINES begin
before it. What lets `records-vs-origin` ask an origin for the few blocks
around a sampled record instead of its whole file (app/acquire/
block_origin.py; 03 §3.2, operator 2026-11-18: "A").

Wikidata's dump is one bzip2 stream of 103 GB (lbzip2's output, probed
2026-11-17: `BZh9` at byte 0, the first block's magic at bit 32), one entity
a line, and its origin publishes no index of it. A bzip2 block is decoded
from its own bits alone (its 48-bit magic, its CRC, its tables and data; the
stream header gives only the block-size level), so once the copy says where
the block that holds a line begins and ends, those bits are the whole of
what a verifier must read.

THE STANDARD, USED: `indexed_bzip2` (MIT; github.com/mxmlnkn/indexed_bzip2,
pinned in requirements.txt), the proven library for random access into one
bzip2 stream. Its parallel reader decodes the stream on `threads` cores and
its `block_offsets()` maps each block's compressed BIT offset to its
decompressed BYTE offset. No bit scanner is written here.

WHAT THIS ADDS beside its offsets, in the same one pass, without parsing a
line: where the unit lines begin. A line begins at byte 0 and after every
newline; a UNIT line is one that opens with `Lines.opens` (`{` for a JSON
dump of one object a line); every other line is counted and its first bytes
kept (wikidata's `[` and `]`), so the reader can hold the file to its
plugin's framing before it trusts an ordinal (`Lines`). Per block: `first`,
the unit lines begun before the block's first decompressed byte, and
`exact`, a unit line begins on that very byte. So the blocks a unit line
spans are two searches of `first` (`BlockIndex.line_blocks`) — no record,
no line and no table is read for it.

ON EVERY CORE THE PROFILE ALLOWS (CLAUDE.md, multi-threading is a must):
indexed_bzip2's reader runs `parallel.processes()` decoding threads; this
thread scans what they deliver for newlines with numpy (the GIL let go).
Built once per file, never on the worker's round path: by `records-vs-
origin` when it first needs it (app/louise.py `_check_origin`), or ahead of
it by the same command in its own container.

RESUMABLE, AND STOPPED CLEANLY (invariant 2). Every `CHECKPOINT_S` the pass
writes its state beside the index (`<index>.partial.npz`, atomically): the
blocks resolved so far, cut at the last one that begins on a BYTE boundary,
and the counts at that block. A pass stopped (`stop`, a SIGTERM the caller
turned into it) or out of time (`deadline`) writes that state and raises
`Incomplete`; the next one reads on from that block through a VIEW of the
file — the stream's 4-byte header, then the file's bytes from the block's
first byte — which indexed_bzip2 reads as a stream whose first block is at
bit 32 (its combined CRC is not the view's, and the reader does not hold
the stream to it; every block is still held to its own CRC). Measured
(2026-11-18): the offsets read through a view are the whole file's, shifted.

DERIVED DATA (invariant 6): the index is placed by app/derived.py under the
fast cache (`LOUISE_CACHE_DIR`, kind `bz2-blocks`, keyed on the file's
resolved path), else in `.pack/` beside the file, and is fresh only while
the file's size, mtime and inode are what it was built from — a file
replaced or rewritten is indexed again. Re-creatable from the copy alone,
never exchanged.
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

VERSION = 1
#: the derived kind (app/derived.py): read at random, so the fast cache
KIND = "bz2-blocks"
SUFFIX = ".blocks.npz"
PARTIAL = ".partial.npz"
#: decompressed bytes read per step of the pass (a stop is honoured within one)
STEP = 16 << 20
#: blocks are resolved, and the pass's state written, at least this often
CHECKPOINT_S = 60.0
#: decompressed bytes one reader reads before the pass begins it again at its
#: checkpoint (a byte-aligned block): what bounds the reader's map of blocks
#: and the pass's memory, whatever the file's size. ~18 k blocks of 900 kB;
#: each new reader decodes again the few blocks past the checkpoint (~0.2 %)
SEGMENT = 16 << 30
#: other-than-unit lines whose first bytes are kept (wikidata has two)
KEEP_OTHERS = 16
#: bytes kept of such a line
OTHER_PREFIX = 64
_HEADER = b"BZh"


class BlockIndexError(ValueError):
    """The file is not one bzip2 stream this index can describe."""


class Incomplete(Exception):
    """The pass stopped (a stop, or its time ran out) with its state written;
    the next pass resumes there. `done` and `total` are compressed bytes."""

    def __init__(self, path: Path, done: int, total: int, seconds: float, why: str):
        self.path, self.done, self.total, self.seconds, self.why = Path(path), done, total, seconds, why
        share = f"{done / total:.0%}" if total else "0%"
        super().__init__(f"{Path(path).name}'s block index is built to {share} ({done:,} of {total:,} "
                         f"compressed bytes, {seconds:,.0f} s so far): {why}; the next pass resumes there")


@dataclass(frozen=True)
class Lines:
    """How a line-per-unit file is framed: every UNIT line opens with
    `opens`; `head` and `tail`, where given, are the file's first and last
    lines, which are no unit (wikidata: `[` and `]`, app/plugins/wikidata/
    dump.py). A file with any other line that is no unit is not framed so,
    and an ordinal is not placed in it (`BlockIndex.framed`)."""
    opens: bytes
    head: bytes | None = None
    tail: bytes | None = None


def identity(path: Path) -> dict:
    """What the index is fresh against: the file's size, mtime and inode.
    Its resolved path is the index's place (`derived.tag`)."""
    st = os.stat(path)
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns), "ino": int(st.st_ino)}


def index_path(dump: Path) -> Path:
    """`<fast cache>/bz2-blocks/<tag of the file>/<name>.blocks.npz`, or
    `.pack/` beside the file where the node names no fast cache."""
    from app import derived
    dump = Path(dump)
    where = derived.place(KIND, dump) or dump.parent / derived.BESIDE
    return where / (dump.name + SUFFIX)


def partial_path(dump: Path) -> Path:
    p = index_path(dump)
    return p.with_name(p.name + PARTIAL)


class BlockIndex:
    """A file's blocks: `bits[k]` where block k begins (bits; `bits[n]` the
    stream's end-of-stream magic, where the last block ends), `dec[k]` its
    first decompressed byte (`dec[n]` the decompressed size), `first[k]`
    the unit lines begun before `dec[k]` (`first[n]` all of them) and
    `exact[k]` whether one begins on `dec[k]`."""

    def __init__(self, path: Path, meta: dict, bits, dec, first, exact) -> None:
        self.path, self.meta = Path(path), meta
        self.bits, self.dec, self.first, self.exact = bits, dec, first, exact

    @property
    def blocks(self) -> int:
        return len(self.bits) - 1

    @property
    def units(self) -> int:
        return int(self.first[-1])

    @property
    def size(self) -> int:
        return int(self.meta["identity"]["size"])

    @property
    def header(self) -> bytes:
        return bytes.fromhex(self.meta["header"])

    def framed(self, lines: Lines) -> str | None:
        """None where the file's lines are as `lines` frames them — every
        line a unit but the head and the tail it names — else why not, in
        plain words."""
        others = [(int(p), bytes.fromhex(b)) for p, b in self.meta["others"]]
        n = int(self.meta["others_n"])
        want = int(lines.head is not None) + int(lines.tail is not None)
        if self.meta.get("opens") != lines.opens.hex():
            return f"its unit lines were counted as opening with {self.meta.get('opens')!r}"
        if n != want:
            seen = ", ".join(f"at byte {p:,} ({b[:24]!r})" for p, b in others[:3])
            return (f"{n:,} of its lines are no unit (they do not open with {lines.opens!r}) where its framing "
                    f"has {want}: {seen}")
        if lines.head is not None and (not others or others[0][0] != 0 or others[0][1] != lines.head):
            return f"its first line is not {lines.head!r}"
        if lines.tail is not None and (not others or others[-1][1] != lines.tail
                                       or int(self.meta.get("last_line_at", -1)) != others[-1][0]):
            return f"its last line is not {lines.tail!r}"
        return None

    def line_blocks(self, ordinal: int) -> tuple[int, int]:
        """(the first, the last block) holding unit line `ordinal` (0-based,
        in file order), from its start to its newline. The last unit line is
        given the blocks to the end of the stream (its tail follows it)."""
        import numpy as np
        e = int(ordinal)
        if not 0 <= e < self.units:
            raise IndexError(f"unit line {e} of {self.units:,}")
        n = self.blocks
        first = self.first[:n]
        a = int(np.searchsorted(first, e, side="right")) - 1
        if e + 1 >= self.units:
            return a, n - 1
        b1 = int(np.searchsorted(first, e + 1, side="right")) - 1
        # the next unit line begins exactly where block b1 begins: this one
        # ended in the block before it
        b = b1 - 1 if (bool(self.exact[b1]) and int(first[b1]) == e + 1) else b1
        return a, max(a, b)

    def byte_range(self, a: int, b: int) -> tuple[int, int]:
        """[start, end) of the compressed bytes holding blocks a..b's bits:
        ⌊start bit / 8⌋ to ⌈end bit / 8⌉. The first and last byte may hold a
        few bits of the blocks beside them."""
        return int(self.bits[a]) // 8, -(-int(self.bits[b + 1]) // 8)


# --------------------------------------------------------------------------
# reading it back

def _load_arrays(path: Path):
    import numpy as np
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        return meta, z["bits"], z["dec"], z["first"], z["exact"]


def load(dump: Path, lines: Lines | None = None) -> BlockIndex | None:
    """The file's index where it is complete and fresh (its version, the
    file's identity, the unit lines' opening byte), else None."""
    dump = Path(dump)
    p = index_path(dump)
    if not p.is_file():
        return None
    try:
        meta, bits, dec, first, exact = _load_arrays(p)
        ident = identity(dump)
    except (OSError, ValueError, KeyError):
        return None
    if meta.get("version") != VERSION or meta.get("identity") != ident:
        return None
    if lines is not None and meta.get("opens") != lines.opens.hex():
        return None
    return BlockIndex(p, meta, bits, dec, first, exact)


def ensure(dump: Path, lines: Lines, *, threads: int | None = None, stop: Callable[[], bool] | None = None,
           deadline: float | None = None, log: Callable[[str], None] | None = None) -> BlockIndex:
    """`load`, else `build`."""
    got = load(dump, lines)
    return got if got is not None else build(dump, lines, threads=threads, stop=stop, deadline=deadline, log=log)


def progress(dump: Path) -> tuple[int, int] | None:
    """(compressed bytes indexed, the file's size) of a pass under way or
    stopped, or None where none is."""
    try:
        meta = _load_arrays(partial_path(dump))[0]
    except (OSError, ValueError, KeyError):
        return None
    return int(meta["state"]["resume_bit"]) // 8, int(meta["identity"]["size"])


# --------------------------------------------------------------------------
# the pass

class _View(io.RawIOBase):
    """The stream read from block `start_byte` on: its 4-byte header, then
    the file's bytes from `start_byte` (a block that begins on a byte
    boundary, so its magic falls at bit 32 of the view)."""

    def __init__(self, path: Path, header: bytes, start_byte: int) -> None:
        super().__init__()
        self._fd = os.open(path, os.O_RDONLY)
        self._head, self._start = bytes(header), int(start_byte)
        self._size = len(self._head) + os.fstat(self._fd).st_size - self._start
        self._pos = 0
        self._lock = threading.Lock()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        with self._lock:
            base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
            self._pos = max(0, base + int(offset))
            return self._pos

    def readinto(self, b) -> int:
        with self._lock:
            mv = memoryview(b).cast("B")
            pos, got = self._pos, 0
            if pos < len(self._head):
                head = self._head[pos:pos + len(mv)]
                mv[:len(head)] = head
                got = len(head)
            if got < len(mv):
                # straight into the caller's buffer: no copy of the file's bytes
                at = self._start + max(0, pos + got - len(self._head))
                got += os.preadv(self._fd, [mv[got:]], at)
            self._pos += got
            return got

    def close(self) -> None:
        if not self.closed:
            os.close(self._fd)
        super().close()


class _Pass:
    """One pass's state: the blocks resolved (final), the unit lines begun
    before the first unresolved block, the newline scan's carry."""

    def __init__(self, dump: Path, lines: Lines, ident: dict, header: bytes, state: dict | None,
                 arrays=None) -> None:
        import numpy as np
        self.dump, self.lines, self.ident, self.header = dump, lines, ident, header
        st = state or {}
        self.resume_bit = int(st.get("resume_bit", 32))
        self.resume_dec = int(st.get("resume_dec", 0))
        self.units_before = int(st.get("units_before", 0))        # unit starts < the first pending one
        self.prev_nl = bool(st.get("prev_nl", True))
        self.others = [(int(p), str(b)) for p, b in st.get("others", [])]
        self.others_n = int(st.get("others_n", 0))
        self.last_line_at = int(st.get("last_line_at", 0))
        self.seconds = float(st.get("seconds", 0.0))
        if arrays is None:
            self.bits, self.dec = np.zeros(0, np.uint64), np.zeros(0, np.uint64)
            self.first, self.exact = np.zeros(0, np.uint64), np.zeros(0, bool)
        else:
            self.bits, self.dec, self.first, self.exact = arrays
        self._began = {"resume_bit": self.resume_bit, "resume_dec": self.resume_dec,
                       "units_before": self.units_before, "prev_nl": self.prev_nl,
                       "others": [[p, b] for p, b in self.others], "others_n": self.others_n}
        self.nl_before: list[bool] = []                          # per block resolved in this pass
        self.pending: list = []                                  # unit starts ≥ the last resolved block
        self.off = self.resume_dec                               # decompressed bytes scanned

    # ---- the newline scan ----
    def scan(self, chunk: bytes) -> None:
        import numpy as np
        if not chunk:
            return
        a = np.frombuffer(chunk, dtype=np.uint8)
        starts = np.flatnonzero(a == 10) + 1
        if self.prev_nl:
            starts = np.concatenate([np.zeros(1, starts.dtype), starts])
        starts = starts[starts < len(a)]
        if len(starts):
            self.last_line_at = self.off + int(starts[-1])
            unit = a[starts] == self.lines.opens[0]
            got = starts[unit].astype(np.uint64) + np.uint64(self.off)
            if len(got):
                self.pending.append(got)
            for s in starts[~unit][:max(0, KEEP_OTHERS - len(self.others))]:
                line = chunk[int(s):int(s) + OTHER_PREFIX].split(b"\n", 1)[0]
                self.others.append((self.off + int(s), line.hex()))
            self.others_n += int((~unit).sum())
        self.prev_nl = bool(a[-1] == 10)
        self.off += len(a)

    def _pending(self):
        import numpy as np
        if len(self.pending) > 1:
            self.pending = [np.concatenate(self.pending)]
        return self.pending[0] if self.pending else np.zeros(0, np.uint64)

    # ---- the blocks ----
    def resolve(self, offsets: dict) -> None:
        """Blocks of `offsets` (the reader's, in the VIEW's bits and bytes)
        not yet resolved whose first byte has been scanned: their counts
        from the unit starts pending. An end-of-stream entry begins at the
        decompressed size, which is never below what was scanned."""
        import numpy as np
        # two arrays, never a list of pairs: the reader's map holds every
        # block it has met (~1.7 M at wikidata's end, ~13 MB as arrays)
        n = len(offsets)
        vb = np.fromiter(offsets.keys(), dtype=np.int64, count=n) + np.int64(self.resume_bit - 32)
        vd = np.fromiter(offsets.values(), dtype=np.int64, count=n) + np.int64(self.resume_dec)
        order = np.argsort(vb, kind="stable")
        vb, vd = vb[order], vd[order]
        last = int(self.bits[-1]) if len(self.bits) else -1
        keep = (vb > last) & (vd < self.off)
        if not keep.any():
            return
        nb, nd = vb[keep].astype(np.uint64), vd[keep].astype(np.uint64)
        if np.any(np.diff(vd[keep]) <= 0) or (len(self.dec) and int(nd[0]) <= int(self.dec[-1])):
            raise BlockIndexError(f"{self.dump.name}: two blocks begin at one decompressed byte — more than one "
                                  f"bzip2 stream, which this index does not describe")
        pend = self._pending()
        i = np.searchsorted(pend, nd, side="left")
        first = np.uint64(self.units_before) + i.astype(np.uint64)
        hit = i < len(pend)
        exact = np.zeros(len(nd), bool)
        exact[hit] = pend[i[hit]] == nd[hit]
        other_at = {p for p, _ in self.others}
        self.nl_before += [bool(x) or int(d) == 0 or int(d) in other_at for x, d in zip(exact, nd)]
        self.bits = np.concatenate([self.bits, nb])
        self.dec = np.concatenate([self.dec, nd])
        self.first = np.concatenate([self.first, first])
        self.exact = np.concatenate([self.exact, exact])
        # what lies before the last block resolved is counted for good
        keep = pend >= nd[-1]
        self.units_before += int((~keep).sum())
        self.pending = [pend[keep]]

    def state(self) -> tuple[dict, tuple]:
        """The state to resume from: the blocks resolved cut at the last one
        that begins on a byte boundary (or the first), and the counts
        there."""
        import numpy as np
        n_old = len(self.bits) - len(self.nl_before)
        cut = None
        for k in range(len(self.bits) - 1, n_old - 1, -1):
            if int(self.bits[k]) % 8 == 0:
                cut = k
                break
        if cut is None:                   # nothing new to resume from: the state this pass began from
            return ({**self._began, "last_line_at": self.last_line_at, "seconds": self.seconds},
                    (self.bits[:n_old], self.dec[:n_old], self.first[:n_old], self.exact[:n_old]))
        at = int(self.dec[cut])
        st = {"resume_bit": int(self.bits[cut]), "resume_dec": at, "units_before": int(self.first[cut]),
              "prev_nl": bool(self.nl_before[cut - n_old]),
              "others": [[p, b] for p, b in self.others if p < at],
              "others_n": int(sum(1 for p, _ in self.others if p < at)) if self.others_n <= len(self.others)
              else self.others_n,
              "last_line_at": self.last_line_at, "seconds": self.seconds}
        return st, (self.bits[:cut], self.dec[:cut], self.first[:cut], self.exact[:cut].astype(np.bool_))


def _write(path: Path, meta: dict, bits, dec, first, exact) -> None:
    """The arrays and their meta, written whole: a temporary file of this
    process's, fsynced, renamed."""
    import numpy as np
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        np.savez(f, meta=np.array(json.dumps(meta, sort_keys=True)), bits=bits, dec=dec, first=first,
                 exact=exact.astype(np.bool_))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_partial(dump: Path, lines: Lines, ident: dict):
    try:
        meta, bits, dec, first, exact = _load_arrays(partial_path(dump))
    except (OSError, ValueError, KeyError):
        return None
    if meta.get("version") != VERSION or meta.get("identity") != ident or meta.get("opens") != lines.opens.hex():
        return None                       # another file's, or another framing's: begun again
    return meta["state"], (bits, dec, first, exact.astype(bool))


def build(dump: Path, lines: Lines, *, threads: int | None = None, stop: Callable[[], bool] | None = None,
          deadline: float | None = None, log: Callable[[str], None] | None = None) -> BlockIndex:
    """One pass over `dump` — resumed where an earlier one stopped — that
    writes its index whole. Raises `Incomplete` when `stop()` says so or
    `deadline` (time.monotonic()) passes, its state written; `derived.Busy`
    when another process builds it (deferred, never waited for);
    `BlockIndexError` for a file that is not one bzip2 stream."""
    import indexed_bzip2
    import numpy as np

    from app import derived, parallel
    dump = Path(dump)
    say = log or (lambda _m: None)
    out = index_path(dump)
    n_threads = max(1, int(threads or parallel.processes()))
    with derived.writing(out.with_name(out.name + ".lock"), "the bz2 block index"):
        got = load(dump, lines)
        if got is not None:
            return got
        ident = identity(dump)
        with open(dump, "rb") as f:
            header = f.read(4)
        if len(header) < 4 or header[:3] != _HEADER or header[3:4] not in b"123456789":
            raise BlockIndexError(f"{dump.name} does not begin with a bzip2 stream header (BZh1-9)")
        resumed = _read_partial(dump, lines, ident)
        st = _Pass(dump, lines, ident, header, *(resumed or (None, None)))
        if resumed:
            say(f"{dump.name}: block index resumed at byte {st.resume_bit // 8:,} of {ident['size']:,}")
        t0 = time.monotonic()
        base_s = st.seconds

        def checkpoint() -> dict:
            st.seconds = base_s + time.monotonic() - t0
            state, arrays = st.state()
            _write(partial_path(dump), {"version": VERSION, "identity": ident, "opens": lines.opens.hex(),
                                        "header": header.hex(), "state": state}, *arrays)
            return state

        def opened():
            src = (str(dump) if st.resume_bit == 32
                   else io.BufferedReader(_View(dump, header, st.resume_bit // 8), buffer_size=4 << 20))
            return src, indexed_bzip2.open(src, parallelization=n_threads)

        def closed(src, rd) -> None:
            rd.close()
            if not isinstance(src, str):
                src.close()

        why = None
        last_cp = t0
        source, reader = opened()
        began_at = st.off
        try:
            while True:
                if stop is not None and stop():
                    why = "stopped"
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    why = "its time for this pass ran out"
                    break
                chunk = reader.read(STEP)
                if not chunk:
                    break
                st.scan(chunk)
                ended = st.off - began_at >= SEGMENT
                if ended or time.monotonic() - last_cp >= CHECKPOINT_S:
                    st.resolve(reader.available_block_offsets())
                    state = checkpoint()
                    last_cp = time.monotonic()
                    if ended and int(state["resume_bit"]) > st.resume_bit:
                        # A SEGMENT ENDED: the reader is begun again at the
                        # checkpoint's block, so its map of blocks — and the
                        # copy of it each resolve takes — holds one segment's,
                        # never the whole file's (~1.7 M for wikidata: a dict
                        # of ~200 MB, read again at every checkpoint)
                        closed(source, reader)
                        st = _Pass(dump, lines, ident, header, *_read_partial(dump, lines, ident))
                        source, reader = opened()
                        began_at = st.off
            if why is not None:
                st.resolve(reader.available_block_offsets())
                state = checkpoint()
                raise Incomplete(dump, int(state["resume_bit"]) // 8, ident["size"], st.seconds, why)
            offsets = reader.block_offsets()
        finally:
            closed(source, reader)
        total = st.off
        shift_bit = st.resume_bit - 32
        ends = sorted(int(b) + shift_bit for b, d in offsets.items() if int(d) + st.resume_dec >= total)
        if not ends:
            raise BlockIndexError(f"{dump.name}: the stream has no end-of-stream marker")
        st.resolve(offsets)
        if len(st.bits) and int(st.bits[-1]) >= ends[0]:
            raise BlockIndexError(f"{dump.name}: a block begins past the end-of-stream marker")
        units = st.units_before + len(st._pending())
        bits = np.append(st.bits, np.uint64(ends[0]))
        dec = np.append(st.dec, np.uint64(total))
        first = np.append(st.first, np.uint64(units))
        exact = np.append(st.exact, False)
        st.seconds = base_s + time.monotonic() - t0
        meta = {"version": VERSION, "identity": ident, "opens": lines.opens.hex(), "header": header.hex(),
                "decompressed": int(total), "blocks": len(st.bits), "units": int(units),
                "others": [[p, b] for p, b in st.others], "others_n": int(st.others_n),
                "last_line_at": int(st.last_line_at), "threads": n_threads, "seconds": round(st.seconds, 1)}
        _write(out, meta, bits, dec, first, exact)
        partial_path(dump).unlink(missing_ok=True)
        say(f"{dump.name}: block index of {len(st.bits):,} blocks, {units:,} unit lines, {total:,} bytes "
            f"decompressed, in {st.seconds:,.0f} s on {n_threads} thread(s)")
        return BlockIndex(out, meta, bits, dec, first, exact)
