"""A FETCHED DUMP MENDED, NEVER A DUMB FULL RE-DOWNLOAD (operator, 2026-11-17,
verbatim: "If available, a resume is always preferable over a dumb full
re-download").

The update by cadence holds every file it fetched to the origin's own md5
(wikidata's `md5sums.txt` line, wikipedia's `dumpstatus.json`; app/hold/
update.py step `fetch`). When the md5 does not match, `ensure`:

  (a) a file SHORTER than the origin's length takes the rest from its own
      length by Range (RFC 9110 §14), appended, and the md5 is asked again;
      a longer one is cut at the origin's length (what is past it is not the
      origin's) and checked as (b);
  (b) a file of the origin's length with wrong bytes, when it is bzip2: its
      independently checked UNITS are located and checked, only the bad
      ones' bytes are fetched again by Range and written in place, and the
      md5 is asked again. The units are bzip2's own (the format bzip2 1.0
      writes; Seward's `bzip2recover` finds blocks the same way): every
      BLOCK starts with a 48-bit magic, 0x314159265359, at any bit offset,
      followed by the CRC of its data; every STREAM starts `BZh1`..`BZh9` on
      a byte and ends with the 48-bit magic 0x177245385090 and the combined
      CRC of its blocks. A block is checked as bzip2recover checks it — as a
      stream of one block, decompressed — and a stream's combined CRC is
      folded from its blocks'. Every bit of the file is accounted for by a
      block, a stream header or a stream end that checks out; whatever is
      not is bad. A multistream file (wikipedia's `pages-articles-
      multistream`: a stream per 100 pages) is mended stream by stream; a
      single stream (wikidata's `all.json.bz2`, lbzip2's output — probed
      2026-11-17: `BZh9` at byte 0, the first block's magic at bit 32, no
      other stream header in its first 8 KB, 103,755,108,222 bytes) block by
      block. The regions are checked on the parse pool (`parallel.
      each_part`: a child per core the node allows, a killed child's regions
      taken again);
  (c) ONLY where the units cannot be told apart — not a bzip2 file (a gzip
      of one member keeps one CRC for the whole member), no unit failing its
      check, more than half the file bad, or the md5 still wrong after the
      mend — is the file fetched again in full, and the message says why.

Every byte moved draws from the transfer budget (invariant 5: each chunk is
charged after its fsync, `transport_https._stream_range`), from the origin's
own base with its User-Agent, one request at a time. A STOP (invariant 2)
ends at the region being checked or the range being fetched: the mend's
sidecar (`<file>.mend.json`, beside it, written atomically after the bytes
it describes are durable) records how far it came, and the next pass
resumes there.

Standardised alternatives, not taken: zsync's (and rsync's) rolling
checksums re-fetch only the blocks that differ from any file, but need a
`.zsync` map the origin does not publish (Wikimedia publishes an md5 and a
sha1 per file); `bzip2recover` itself writes every block as a file of its
own (~1.1 M files, the whole dump again, for wikidata), so its method is
used and not the tool.
"""
from __future__ import annotations

import bz2
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

BLOCK_MAGIC = 0x314159265359
END_MAGIC = 0x177245385090
_MASK48 = (1 << 48) - 1
#: a region of the file checked by one task of the parse pool
REGION = 64 << 20
#: read past a region's end to reach the magic after its last block: a
#: level-9 block's compressed bytes are under 1 MB (900 kB of input)
TAIL = 4 << 20
#: more than this share of the file bad: it is fetched again in full
MAX_SHARE = 0.5
#: one Range request of the mend: seconds at the origin's rate, so a stop
#: is honoured within one
STEP = 8 << 20
SIDECAR = ".mend.json"
_HEADER = b"BZh"
_LEVELS = b"123456789"


@dataclass(frozen=True)
class Expected:
    """A file the origin's own record names: its path under the corpus, its
    md5, and its length where the record gives one."""
    rel_path: str
    md5: str
    size: int | None = None


@dataclass
class Mended:
    rel_path: str
    state: str                 # ok | cut | resumed | mended | again
    says: str
    refetched: int = 0         # bytes fetched again by the mend
    size: int = 0
    seconds: float = 0.0
    blake2: str | None = None  # the file's digest once it matches (the engine's row)
    ranges: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"file": self.rel_path.rsplit("/", 1)[-1], "state": self.state, "refetched": self.refetched,
                "size": self.size, "seconds": round(self.seconds, 1), "ranges": len(self.ranges),
                "says": self.says}


class Stopped(Exception):
    """A stop, honoured at a region or a range; the sidecar says where."""


# --------------------------------------------------------------------------
# bzip2's units

def _patterns(magic: int) -> list[tuple[int, bytes, int]]:
    """For each bit shift s of a 48-bit magic (it starts at bit 8i + s), the
    bytes it covers whole and their offset from byte i — found by
    `bytes.find` at C speed and then held to all 48 bits."""
    out = [(0, magic.to_bytes(6, "big"), 0)]
    for s in range(1, 8):
        out.append((s, (magic << (8 - s)).to_bytes(7, "big")[1:6], 1))
    return out


_PATTERNS = {BLOCK_MAGIC: _patterns(BLOCK_MAGIC), END_MAGIC: _patterns(END_MAGIC)}


def find_magic(buf: bytes, magic: int) -> list[int]:
    """Every bit offset in `buf` where the 48 bits are `magic`, sorted."""
    out = []
    n = len(buf)
    for s, pat, off in _PATTERNS[magic]:
        j = buf.find(pat)
        while j >= 0:
            i = j - off
            whole = i >= 0 and i + 6 + (1 if s else 0) <= n
            if whole and (s == 0 or (int.from_bytes(buf[i:i + 7], "big") >> (8 - s)) & _MASK48 == magic):
                out.append(8 * i + s)
            j = buf.find(pat, j + 1)
    return sorted(out)


def _bits(buf: bytes, p: int, q: int) -> int:
    """Bits [p, q) of `buf` as an integer."""
    i, k = p // 8, -(-q // 8)
    return (int.from_bytes(buf[i:k], "big") >> (8 * k - q)) & ((1 << (q - p)) - 1)


def check_block(buf: bytes, p: int, q: int) -> tuple[bool, int]:
    """(does the block at bits [p, q) of `buf` check out, its stored CRC):
    bzip2recover's test — the block alone in a stream of its own, its
    combined CRC its own CRC, decompressed by libbz2, which holds the data
    to the CRC."""
    n = q - p
    if n < 48 + 32 + 1:
        return False, 0
    v = _bits(buf, p, q)
    crc = (v >> (n - 80)) & 0xFFFFFFFF
    bits = 32 + n + 80
    pad = -bits % 8
    stream = ((int.from_bytes(b"BZh9", "big") << (n + 80)) | (v << 80) | (END_MAGIC << 32) | crc) << pad
    d = bz2.BZ2Decompressor()
    try:
        d.decompress(stream.to_bytes((bits + pad) // 8, "big"))
    except (OSError, ValueError, EOFError):
        return False, crc
    return d.eof and not d.unused_data, crc


def check_region(path: str, start: int, end: int, total: int) -> list[tuple]:
    """The units that BEGIN in bytes [start, end) of the file, each checked,
    in order, as events: ("H", bit) a stream header; ("B", bit, end bit,
    stored CRC, ok) a block, ending at the next magic; ("E", bit, stored
    combined CRC, padding zero) a stream's end. Module-level: a task of the
    parse pool."""
    with open(path, "rb") as f:
        f.seek(start)
        buf = f.read(min(total, end + TAIL) - start)
    base, limit = 8 * start, 8 * (end - start)
    magics = sorted([(p, "B") for p in find_magic(buf, BLOCK_MAGIC)]
                    + [(p, "E") for p in find_magic(buf, END_MAGIC)])
    starts = {p for p, _ in magics}
    events: list[tuple] = []
    j = buf.find(_HEADER)
    while 0 <= j < end - start:
        if j + 4 <= len(buf) and buf[j + 3] in _LEVELS and 8 * j + 32 in starts:
            events.append(("H", base + 8 * j))
        j = buf.find(_HEADER, j + 1)
    for k, (p, kind) in enumerate(magics):
        if p >= limit:
            break
        if kind == "B":
            if k + 1 < len(magics):
                q = magics[k + 1][0]
                ok, crc = check_block(buf, p, q)
            else:                         # no magic after it: the file ends, or the tail holds none
                q, ok, crc = 8 * len(buf), False, 0
            events.append(("B", base + p, base + q, crc, ok))
        else:
            if p + 80 > 8 * len(buf):
                events.append(("E", base + p, None, False))
                continue
            crc = _bits(buf, p + 48, p + 80)
            tail = -(p + 80) % 8
            pad_ok = tail == 0 or _bits(buf, p + 80, p + 80 + tail) == 0
            events.append(("E", base + p, crc, pad_ok))
    events.sort(key=lambda e: e[1])
    return events


class Walk:
    """Every bit of the file accounted for, in order, from the regions'
    events: a gap no unit covers, a block that fails, a stream end whose
    combined CRC is not its blocks' fold — each is a bad byte range. Small
    state (`to_dict`), so a stopped check resumes at its next region."""

    def __init__(self, state: dict | None = None) -> None:
        st = state or {}
        self.frontier = int(st.get("frontier", 0))      # bits accounted for
        self.fold = st.get("fold")                        # the stream's combined CRC so far; None outside one
        self.bad: list[list[int]] = [list(r) for r in st.get("bad", [])]
        self.units = int(st.get("units", 0))
        self.streams = int(st.get("streams", 0))

    def to_dict(self) -> dict:
        return {"frontier": self.frontier, "fold": self.fold, "bad": self.bad, "units": self.units,
                "streams": self.streams}

    def _mark(self, a_bit: int, b_bit: int) -> None:
        a, b = a_bit // 8, -(-b_bit // 8)
        if self.bad and a <= self.bad[-1][1]:
            self.bad[-1][1] = max(self.bad[-1][1], b)
        else:
            self.bad.append([a, b])

    def take(self, ev: tuple) -> None:
        kind, at = ev[0], ev[1]
        if at > self.frontier:
            self._mark(self.frontier, at)                 # bits no unit accounts for
        if kind == "H":
            self.fold, end, ok = 0, at + 32, True
            self.streams += 1
        elif kind == "B":
            _, at, end, crc, ok = ev
            fold = self.fold or 0
            self.fold = (((fold << 1) | (fold >> 31)) & 0xFFFFFFFF) ^ crc
            self.units += 1
        else:
            _, at, crc, pad_ok = ev
            end = 8 * (-(-(at + 80) // 8))
            ok = bool(pad_ok) and crc is not None and self.fold is not None and crc == self.fold
            self.fold = None
        if not ok:
            self._mark(at, end)
        self.frontier = max(self.frontier, end)

    def finish(self, total: int) -> list[list[int]]:
        if self.frontier < 8 * total:
            self._mark(self.frontier, 8 * total)
        return [[a, min(b, total)] for a, b in self.bad if a < total]


def is_bzip2(path: Path) -> bool:
    """A bzip2 file: its first stream's header, or — that header itself
    damaged — a `.bz2` whose first block's magic is in its first bytes
    (the walk then finds the header bad, and it is fetched again)."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return False
    if len(head) >= 10 and head[:3] == _HEADER and head[3] in _LEVELS \
            and int.from_bytes(head[4:10], "big") in (BLOCK_MAGIC, END_MAGIC):
        return True
    return path.suffix == ".bz2" and bool(find_magic(head, BLOCK_MAGIC))


# --------------------------------------------------------------------------
# the mend

def digests(path: Path) -> tuple[str, str]:
    """(md5, blake2b-16) of the file in one pass — the origin's checksum and
    the engine's row (`checksum.digest_and_verify`'s convention)."""
    md5, blake = hashlib.md5(), hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            md5.update(block)
            blake.update(block)
    return md5.hexdigest(), blake.hexdigest()


def sidecar_of(path: Path) -> Path:
    return path.with_name(path.name + SIDECAR)


def _read_sidecar(path: Path, want: Expected, total: int) -> dict | None:
    try:
        st = json.loads(sidecar_of(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(st, dict) or st.get("md5") != want.md5 or st.get("size") != total:
        return None                       # another file's, or another layout's: begun again
    return st


def _write_sidecar(path: Path, doc: dict) -> None:
    p = sidecar_of(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, sort_keys=True))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.1f} MB" if n >= 1e5 else f"{n:,} bytes"


class _Origin:
    """The origin's file by Range: its length (a one-byte Range, the origin's
    HEAD may name none) and its bytes written into a file at their offset,
    in closed steps, each charged to the budget after its fsync."""

    def __init__(self, url: str, headers: dict) -> None:
        self.url, self.headers = url, dict(headers)
        self.moved = 0

    def total(self) -> int:
        from . import budget, zipdelta
        return zipdelta.HttpRangeReader(self.url, self.headers, charge=budget.acquire_bytes).total()

    def write(self, f, start: int, end: int) -> None:
        """Bytes [start, end] of the origin at `f`'s position, which is
        `start` (a file opened for appending, or seeked there)."""
        from . import transport_https
        at = start
        while at <= end:
            got = transport_https._stream_range(self.url, self.headers, f, at, end)
            if got <= 0:
                from .spec import TransportError
                raise TransportError(f"{self.url}: nothing to Range {at}-{end}")
            at += got
            self.moved += got


def ensure(path, want: Expected, *, url: str, headers: dict, stop: threading.Event | None = None,
           log=print, origin=None, region: int | None = None) -> Mended:
    """The file at `path` made to match `want.md5`, mended where it does not
    (the module's docstring). Its state: `ok` (matched as fetched),
    `resumed` or `cut` (a), `mended` (b), `again` (c: removed, to be fetched
    whole; `says` why), or it raises `Stopped`."""
    path = Path(path)
    t0 = time.monotonic()
    region = REGION if region is None else int(region)
    origin = origin or _Origin(url, headers)
    stop = stop or threading.Event()
    name = path.name
    size = path.stat().st_size
    st = None
    total = want.size
    if total is None and sidecar_of(path).is_file():
        try:
            total = int(json.loads(sidecar_of(path).read_text(encoding="utf-8")).get("size"))
        except (OSError, ValueError, TypeError):
            total = None
    if total is not None:
        st = _read_sidecar(path, want, total)
    if st is None:
        md5, blake = digests(path)
        if md5 == want.md5:
            sidecar_of(path).unlink(missing_ok=True)
            return Mended(want.rel_path, "ok", f"{name}: the origin's md5", size=size,
                          seconds=time.monotonic() - t0, blake2=blake)
        total = total if total is not None else origin.total()
        log(f"{name}: md5 {md5}, the origin's is {want.md5} ({_gb(size)} of {_gb(total)}): mended where "
            f"its parts can be told apart")
        st = {"md5": want.md5, "size": total, "phase": "length", "refetched": 0}
        _write_sidecar(path, st)
    refetched = int(st.get("refetched") or 0)

    def done(state: str, says: str, ranges=()) -> Mended:
        md5, blake = digests(path)
        if md5 != want.md5:
            return _again(path, want, f"{says}, and its md5 is still {md5}, not the origin's {want.md5}",
                          refetched + origin.moved, t0, ranges)
        sidecar_of(path).unlink(missing_ok=True)
        return Mended(want.rel_path, state, says, refetched + origin.moved, total, time.monotonic() - t0, blake,
                      list(ranges))

    # (a) the length first: the rest appended from the file's own length
    if st["phase"] == "length":
        size = path.stat().st_size
        state = says = None
        if size > total:
            os.truncate(path, total)
            state, says = "cut", f"{name}: {size - total:,} bytes past the origin's length {total:,} cut"
            log(says)
        elif size < total:
            log(f"{name}: {_gb(size)} of {_gb(total)}: the rest resumed from byte {size:,}")
            began = size
            with open(path, "ab") as f:
                while size < total:
                    if stop.is_set():
                        _write_sidecar(path, {**st, "refetched": refetched + origin.moved})
                        raise Stopped(f"{name}: resumed to byte {size:,} of {total:,}")
                    origin.write(f, size, min(total, size + STEP) - 1)
                    size = f.tell()
            state, says = "resumed", (f"{name}: resumed from its length, {_gb(total - began)} fetched of "
                                      f"{_gb(total)}")
        if state is not None:
            md5, blake = digests(path)
            if md5 == want.md5:
                sidecar_of(path).unlink(missing_ok=True)
                tail = [[path.stat().st_size - origin.moved, total]] if origin.moved else []
                return Mended(want.rel_path, state, says, refetched + origin.moved, total,
                              time.monotonic() - t0, blake, tail)
        st = {**st, "phase": "check", "regions": 0, "region": region, "walk": None,
              "refetched": refetched + origin.moved}
        refetched, origin.moved = st["refetched"], 0
        _write_sidecar(path, st)

    # (b) the units
    if st["phase"] == "check":
        if not is_bzip2(path):
            return _again(path, want, f"{name} is not a bzip2 file: its parts carry no check of their own "
                          f"(one CRC for the whole), so they cannot be told apart", refetched, t0)
        region = int(st.get("region") or region)
        walk = Walk(st.get("walk"))
        from app import parallel
        first = int(st.get("regions") or 0)
        bounds = [(str(path), a, min(total, a + region), total) for a in range(first * region, total, region)]
        k = first
        for events in parallel.each_part(check_region, bounds):
            for ev in events:
                walk.take(ev)
            k += 1
            st = {**st, "regions": k, "walk": walk.to_dict()}
            if sum(b - a for a, b in walk.bad) > MAX_SHARE * total:
                return _again(path, want, f"more than {MAX_SHARE:.0%} of {name} is bad by its blocks' CRCs",
                              refetched, t0)
            if stop.is_set() and k < first + len(bounds):
                _write_sidecar(path, st)
                raise Stopped(f"{name}: {k * region:,} of {total:,} bytes checked")
        bad = walk.finish(total)
        if not bad:
            return _again(path, want, f"every one of {name}'s {walk.units:,} blocks checks out, so the "
                          f"bytes that differ cannot be told apart", refetched, t0)
        log(f"{name}: {len(bad)} bad range(s), {_gb(sum(b - a for a, b in bad))}, among {walk.units:,} "
            f"blocks in {walk.streams:,} stream(s): fetched again by Range")
        st = {**st, "phase": "fetch", "bad": bad, "done": 0, "walk": None}
        _write_sidecar(path, st)

    # (b) the bad ranges fetched again, in place
    bad = [list(r) for r in st["bad"]]
    i = int(st.get("done") or 0)
    with open(path, "r+b") as f:
        while i < len(bad):
            a, b = bad[i]
            at = int(st.get("at") or a)               # where a stopped range had come to
            while at < b:
                if stop.is_set():
                    _write_sidecar(path, {**st, "done": i, "at": at, "refetched": refetched + origin.moved})
                    raise Stopped(f"{name}: {i} of {len(bad)} bad range(s) fetched again")
                f.seek(at)
                origin.write(f, at, min(b, at + STEP) - 1)
                at = min(b, at + STEP)
            i += 1
            st = {**st, "done": i, "at": None}
            _write_sidecar(path, {**st, "refetched": refetched + origin.moved})
    return done("mended", f"{name}: {len(bad)} bad range(s) found by its blocks' CRCs and fetched again, "
                          f"{_gb(refetched + origin.moved)} of {_gb(total)}", bad)


def _again(path: Path, want: Expected, why: str, refetched: int, t0: float, ranges=()) -> Mended:
    """(c): the file goes again in full, and says why."""
    size = path.stat().st_size if path.is_file() else 0
    path.unlink(missing_ok=True)
    sidecar_of(path).unlink(missing_ok=True)
    return Mended(want.rel_path, "again", f"{why}: fetched again in full ({_gb(size)})", refetched, size,
                  time.monotonic() - t0, None, list(ranges))
