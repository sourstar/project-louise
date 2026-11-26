"""A PREVIEW of a figure the browser cannot show (08 §8.7, amended 2026-11-26).

The operator, 2026-11-26, of EUR-Lex's pictures: "we must show pictures in
the results as it is done for gutenberg collection" — and its origin gives
them as TIFF, which no page shows (08 §8.7's closed list is JPEG and PNG, and
browsers do not render TIFF anyway). His choice, "Option A": each node
renders a PNG of about 1,600 px FROM THE BYTES IT HOLDS, in a child process,
with caps read from the header before any pixel is decoded, kept as derived
data on the fast drive. Nothing published or hashed changes: the collection
carries the origin's own bytes (invariant 11), the preview never travels and
is made again on a miss; the browser still receives only PNG.

WHAT THIS ADDS TO THE ATTACK SURFACE, and what bounds it. A figure is
publisher-supplied bytes (08 §8.7), and a TIFF decoder now runs on the node
over them — where, until now, only the reader's browser decoded anything.
THREE LINES, and the order matters: an estimate is not a bound (app/
parallel.py learnt it on 2026-11-20), so the header is the first and not the
last.

  1. THE HEADER FIRST, by a reader of our own (`tiff_header`: the first
     image file directory of a classic TIFF, eight tags and every entry's
     size). What the file DECLARES is held to caps before the decoder is
     handed it: its pixels, its sides, what the child would hold for them
     (`TiffHeader.held_bytes`: a model, by kind of image), its tiles, the
     bytes its tags point at, and its coding — a CLOSED LIST, the ones the
     corpus holds (`COMPRESSIONS`). The directory must be in ascending tag
     order with no tag twice, as TIFF 6.0 requires: our reader, Pillow's and
     libtiff's do not agree on WHICH of two entries counts (the review of
     2026-11-26 decoded a Deflate plate that declared LZW that way), and a
     file that gives them nothing to disagree about is the only one read.
  2. THE CHILD'S OWN LIMITS (app/ask/preview_child.py): a data limit from
     the moment the decoder is handed the file (RLIMIT_DATA, as the parse
     pool's large lane), an alarm, the parent watched so an orphan ends,
     and first in line should the cgroup's memory run out. The web never
     holds the figure's bytes (EUR-Lex's largest is 565 MB) nor runs the
     decoder.
  3. THE PARENT'S WATCH: the child's resident memory and its seconds, read
     every 50 ms; killed past either.

And around them: at most `CONCURRENT` previews are rendered at once across
processes (the web, `louise ask pdf`) — on a NODE where a fast drive is
named, for each collection's own store where none is; the PNG a child makes
is held to the display rule (`figures.not_displayable`) and to its size
before it is read, and carries nothing of the plate's metadata; a refusal is
REMEMBERED beside the cache with its reason — for good where the reason is
the FILE's, for an hour where it may be the MOMENT's (`NotPreviewableNow`:
busy, not done in time, the store or the container not readable just now);
a plate not done in time `TIMEOUT_TRIES` times, an hour apart, is the file's.
What is kept and what is refused carry the renderer's version in their name
(`RENDERER`, and the pinned Pillow): a raised cap, an added coding or
another decoder is asked afresh.

THE CAPS ARE SET FROM THE CORPUS, as 08 §8.7's were (München's header count
of EUR-Lex's 335,166 images, 2026-11-26, no pixel decoded — 300,775 read:
250,220 TIFF and 50,555 JPEG, which the page shows itself; 34,362 packaged
TIFFs whose directory lies past their first MiB were not): declared pixels
p50 0.137 MP, p99 27.7 MP, max 324.7 MP (three certificate forms of 15,794 ×
20,561, 8-bit, uncompressed); decoded size p99 40 MiB, max 539 MiB; the
largest file 565.7 MB (9,954 × 18,942 RGB). So 400 MP declared, 1 GiB held
and 1 GiB of file: a little over the largest honest one each. Over a cap the
figure is "not displayable — download", as before.

MEASURED (2026-11-26, synthetic pages of the corpus's shapes, this session's
machine, the child's start and its limits included — seconds, the child's
resident peak, what the header's model said it would hold):
    2,400 × 3,200 fax page (G4)                0.25 s    71 MiB    15 MiB
    9,933 × 14,043 at 1 bit (G4)               0.46 s   355 MiB   266 MiB
    15,794 × 20,540 at 1 bit (G3)              0.76 s   714 MiB   619 MiB
    15,794 × 20,561 at 8 bits, uncompressed    0.71 s   404 MiB   619 MiB
    9,954 × 18,942 RGB, uncompressed (565 MB)  1.41 s   917 MiB   719 MiB
    8,000 × 8,000 CMYK (LZW)                   0.86 s   642 MiB   488 MiB
    12,000 × 12,000 palette (LZW)              0.96 s   824 MiB   687 MiB
    14,000 × 14,000 RGB (LZW)                  0.86 s   882 MiB   748 MiB
The child allocates 90–140 MiB past the model (its reduced copy, the
decoder's strip), so `CHILD_DATA_BYTES` is the model's cap and 768 MiB.

STANDARD, WRITTEN DOWN (CLAUDE.md): the decoder is Pillow's (libtiff), the
library every Python image path uses, declared in requirements.txt from this
day — it was importable in the image only because another package brought
it. The isolation is a subprocess with POSIX limits, watched by its parent;
the standard alternative is a sandbox proper (seccomp/landlock via
bubblewrap, or a decoder compiled to WebAssembly): worth adopting the day
previews are rendered for collections whose publisher is not also an
institution's official journal, at the cost of a system dependency in the
image.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

KIND = "figure-preview"
#: the media types a preview is rendered for — what the record's entry names
PREVIEW_OF = ("image/tiff",)
#: the preview: at most this wide and this tall, in pixels (its aspect kept)
WIDTH, MAX_HEIGHT = 1600, 6400
#: THE RENDERER'S VERSION, in the name of every preview kept and every
#: refusal remembered: raise it with any change of the caps, the codings or
#: what the child does to the pixels, and the next view asks afresh. The
#: pinned Pillow is in the name too (a test holds it to constraints.txt).
RENDERER = 1
PILLOW = "12.3.0"
#: the caps on the SOURCE, from the corpus (the module's docstring)
MAX_SOURCE_BYTES = 1 << 30
MAX_SOURCE_PIXELS = 400_000_000
#: neither side over this: the decoder keeps a table of rows (a 1 × 40,000,000
#: image held 414 MiB for 38 MiB of pixels); the corpus's longest side is 20,561
MAX_SIDE = 65_535
#: what the child may HOLD for an image at its peak, by `TiffHeader.held_bytes`
MAX_HELD_BYTES = 1 << 30
#: a tile may be larger than a small image (a 100 × 80 picture in one 256 × 256
#: tile), never by more than this many pixels: the decoder allocates the tile
MAX_TILE_SLACK_PIXELS = 1 << 20
#: all the bytes the directory's entries point at together (strip tables, a
#: colour profile, a description): Pillow reads them at open, before any pixel
MAX_TAG_BYTES = 16 << 20
#: TIFF `Compression` values a preview is rendered from: those EUR-Lex's
#: 250,220 read TIFF headers hold, and no other (uncompressed 117,194; LZW
#: 76,325; CCITT Group 3 28,062; PackBits 21,851; CCITT Group 4 6,788)
COMPRESSIONS = {1: "uncompressed", 3: "CCITT Group 3", 4: "CCITT Group 4", 5: "LZW", 32773: "PackBits"}
#: THE CHILD: killed by its parent past this many seconds; its own data limit
#: (what it may allocate once its modules are loaded: the image by the model
#: above, the decoder's strip or tile, the reduced copy); the parent's watch on
#: its resident memory adds the source's size (the decoder maps the file)
RENDER_TIMEOUT_S = 180.0
CHILD_DATA_BYTES = MAX_HELD_BYTES + (768 << 20)
#: what the child's loaded modules are resident for before any plate (59 MiB
#: measured; its data limit is counted from them, so the watch adds them)
CHILD_BASE_BYTES = 256 << 20
#: the PNG a child made is not read past this: the display rule's own cap
MAX_PREVIEW_BYTES = 16 << 20
#: the child's exit codes with a reason as JSON on stdout: refused before
#: decoding ({"refused": why}), not possible just now ({"now": why}); 1 with
#: {"failed": why} is the decoder's own failure on the file
REFUSED, NOT_NOW = 3, 4
#: `_render`'s codes for a child THIS process killed
TIMED_OUT, OVER_MEMORY = "seconds", "memory"
#: previews rendered at once on a node, across processes; how long a request
#: waits for one of them before it is told "busy" (not remembered)
CONCURRENT = 3
WAIT_S = 60.0
#: how long a refusal that may be the MOMENT's is remembered before the next
#: view tries again; what a killed renderer left is swept after this long too
RETRY_S = 3600
#: a plate not rendered within its seconds this many times, an hour apart, is
#: not tried again: it is the file's after all (a strip table that names the
#: same bytes a hundred thousand times passes every cap and never finishes)
TIMEOUT_TRIES = 3
#: how long "every renderer is busy" asks a reader to wait before asking again
BUSY_RETRY_S = 30
#: a signal that is the DECODER's own on this file — remembered for good; any
#: other (a kill, a stop of the container, SIGBUS: an I/O error under the
#: mapped copy, which is the node's disk) is the moment's
_CRASHES = frozenset(int(s) for s in (signal.SIGSEGV, signal.SIGABRT, signal.SIGFPE, signal.SIGILL))


class NotPreviewable(Exception):
    """No preview can be shown for this figure: the reason, in words."""


class NotPreviewableNow(NotPreviewable):
    """No preview can be shown for it JUST NOW — every renderer busy, not
    done within its seconds, the store not writable: asked later, it may be.
    `after_s`: how many seconds later is worth asking."""

    def __init__(self, why: str, after_s: float = BUSY_RETRY_S) -> None:
        super().__init__(why)
        self.after_s = max(1, int(after_s))


@dataclass(frozen=True)
class TiffHeader:
    """What a TIFF's first image file directory DECLARES."""
    width: int
    height: int
    bits_per_pixel: int
    compression: int
    samples: int = 1
    photometric: int | None = None
    tile: tuple[int, int] = (0, 0)
    tag_bytes: int = 0
    strips: int = 0

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def decoded_bytes(self) -> int:
        """The pixels as the file states them: a byte a pixel at least."""
        return self.pixels * max(1, (self.bits_per_pixel + 7) // 8)

    @property
    def held_per_pixel(self) -> int:
        """Bytes the CHILD holds per pixel at its peak, by kind of image —
        Pillow's own storage (a byte for bilevel, grey and palette; four for
        colour, whatever the file's three) and the copy its conversion makes
        (app/ask/preview_child.py `render`). A model, measured on each kind
        2026-11-26, and the first line only: the child's data limit is the
        bound."""
        bits = self.bits_per_pixel // max(1, self.samples)
        if self.samples == 1:
            if self.photometric == 3:
                return 5                     # palette: itself, and its RGB copy
            if bits <= 8:
                return 2                     # bilevel or grey: itself, and the grey copy of a bilevel
            return 10 if bits <= 16 else 12  # 16-bit and deeper: itself, the 32-bit copy, its scaled one
        if self.photometric == 2 and bits <= 8 and self.samples in (3, 4):
            return 4                         # RGB, RGBA
        return 8                             # CMYK, YCbCr, Lab, deep colour: itself and its RGB copy

    @property
    def held_bytes(self) -> int:
        """What the child holds for it: the image by `held_per_pixel`, and
        one tile as the file states it where it is tiled."""
        return (self.pixels * self.held_per_pixel
                + self.tile[0] * self.tile[1] * max(1, (self.bits_per_pixel + 7) // 8))


#: the tags read (TIFF 6.0 §8), and the bytes of one value of each field type
_WIDTH, _HEIGHT, _BITS, _COMPRESSION, _PHOTOMETRIC, _SAMPLES = 256, 257, 258, 259, 262, 277
_TILE_WIDTH, _TILE_LENGTH = 322, 323
_READ = (_WIDTH, _HEIGHT, _BITS, _COMPRESSION, _PHOTOMETRIC, _SAMPLES, _TILE_WIDTH, _TILE_LENGTH)
#: StripOffsets: only its COUNT is read — how many strips the decoder will walk
_STRIP_OFFSETS = 273
#: BYTE, ASCII, SHORT, LONG, RATIONAL, SBYTE, UNDEFINED, SSHORT, SLONG, SRATIONAL, FLOAT, DOUBLE, IFD
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
#: a directory with more entries than this is not an image's (TIFF 6.0 has ~70 baseline tags)
_MAX_ENTRIES = 512


def tiff_header(f: BinaryIO) -> TiffHeader:
    """The first image file directory of a CLASSIC TIFF read from `f`, or
    `NotPreviewable` with what is wrong. No pixel is read and no decoder is
    called: byte order, the magic 42, the directory's offset, then its
    entries (TIFF 6.0 §2). BigTIFF (magic 43) is refused: the corpus holds
    none.

    REFUSED, because another reader would read it otherwise than this one:
    tags not in ascending order or one tag twice (libtiff takes the first of
    two, Pillow and a naive reader the last — a second `Compression` was a
    way around the closed list); a tag this reader acts on with a count or a
    field type the standard does not give it (libtiff then ignores the tag
    and takes its default)."""
    head = f.read(8)
    if len(head) < 8 or head[:2] not in (b"II", b"MM"):
        raise NotPreviewable("the bytes are not a TIFF (no II/MM byte-order mark)")
    order = "<" if head[:2] == b"II" else ">"
    magic, offset = struct.unpack(order + "HI", head[2:8])
    if magic == 43:
        raise NotPreviewable("a BigTIFF: not a form a preview is rendered from")
    if magic != 42:
        raise NotPreviewable(f"the bytes are not a TIFF (magic {magic}, want 42)")
    f.seek(0, os.SEEK_END)
    size = f.tell()
    if not 8 <= offset <= size - 2:
        raise NotPreviewable("its image directory lies outside the file")
    f.seek(offset)
    (count,) = struct.unpack(order + "H", f.read(2))
    if not 1 <= count <= _MAX_ENTRIES or offset + 2 + count * 12 > size:
        raise NotPreviewable(f"its image directory declares {count} entries")
    entries = f.read(count * 12)
    got: dict[int, list[int]] = {}
    last, tag_bytes, strips = -1, 0, 0
    for i in range(count):
        tag, kind, n, raw = struct.unpack(order + "HHI4s", entries[i * 12:(i + 1) * 12])
        if tag <= last:
            raise NotPreviewable(f"its image directory is not in ascending tag order (tag {tag} after {last}): "
                                 f"two readers would not read the same image from it")
        last = tag
        width = _TYPE_SIZE.get(kind)
        if width is None:
            raise NotPreviewable(f"its tag {tag} is of a field type ({kind}) TIFF 6.0 does not define")
        at = None
        if n * width > 4:
            # EVERY entry, read by this reader or not: its values lie in the file,
            # and all of them together within what a decoder may read at open
            tag_bytes += n * width
            if tag_bytes > MAX_TAG_BYTES:
                raise NotPreviewable(f"its directory's entries point at over {MAX_TAG_BYTES >> 20} MiB of "
                                     f"values, which a decoder reads before any pixel")
            (at,) = struct.unpack(order + "I", raw)
            if not 8 <= at <= size - n * width:
                raise NotPreviewable(f"its tag {tag} points outside the file")
        if tag == _STRIP_OFFSETS:
            strips = n
        if tag not in _READ:
            continue
        if kind not in (3, 4) or not (n == 1 or (tag == _BITS and 1 <= n <= 8)):
            raise NotPreviewable(f"its tag {tag} is of a type or count ({kind}, {n}) no image declares")
        if at is None:
            data = raw[:n * width]
        else:
            f.seek(at)
            data = f.read(n * width)
        got[tag] = list(struct.unpack(order + ("H" if width == 2 else "I") * n, data))
    if _WIDTH not in got or _HEIGHT not in got:
        raise NotPreviewable("its header declares no dimensions")
    w, h = got[_WIDTH][0], got[_HEIGHT][0]
    if w <= 0 or h <= 0:
        raise NotPreviewable(f"its header declares {w}×{h}")
    samples = got.get(_SAMPLES, [1])[0]
    bits = got.get(_BITS, [1])
    if not 1 <= samples <= 8 or len(bits) not in (1, samples):
        # a BitsPerSample that is not one value, or one a sample, is ignored by libtiff
        raise NotPreviewable(f"its header declares {samples} sample(s) a pixel and {len(bits)} bit depth(s)")
    bpp = sum(bits) if len(bits) == samples else bits[0] * samples
    if not 1 <= bpp <= 64:
        raise NotPreviewable(f"its header declares {bpp} bits a pixel")
    if (_TILE_WIDTH in got) != (_TILE_LENGTH in got):
        raise NotPreviewable("its header declares one side of a tile and not the other")
    tile = (got[_TILE_WIDTH][0], got[_TILE_LENGTH][0]) if _TILE_WIDTH in got else (0, 0)
    return TiffHeader(w, h, bpp, got.get(_COMPRESSION, [1])[0], samples,
                      got[_PHOTOMETRIC][0] if _PHOTOMETRIC in got else None, tile, tag_bytes, strips)


def refused(header: TiffHeader, size: int) -> str | None:
    """Why no preview is rendered from a TIFF of this header and size — a cap
    or a coding outside the list — or None. Asked BEFORE the decoder."""
    mib = lambda n: f"{n / (1 << 20):,.0f} MiB"   # noqa: E731
    dims = f"{header.width}×{header.height}"
    if size > MAX_SOURCE_BYTES:
        return f"image/tiff, {mib(size)}: over the {mib(MAX_SOURCE_BYTES)} cap a preview is rendered from"
    if header.pixels > MAX_SOURCE_PIXELS:
        return (f"image/tiff, {dims}: over the {MAX_SOURCE_PIXELS // 1_000_000}-megapixel cap a preview is "
                f"rendered from")
    if max(header.width, header.height) > MAX_SIDE:
        return f"image/tiff, {dims}: a side over the {MAX_SIDE:,} pixels a preview is rendered from"
    tile = header.tile[0] * header.tile[1]
    if header.tile != (0, 0) and (min(header.tile) <= 0 or tile > header.pixels + MAX_TILE_SLACK_PIXELS):
        return (f"image/tiff, {dims} in tiles of {header.tile[0]}×{header.tile[1]}: a tile no image of that "
                f"size is cut into")
    if header.strips > header.height * max(1, header.samples):
        # a strip holds a row at least (one a sample where the planes are apart):
        # more of them than that is a table that names the same bytes again
        return (f"image/tiff, {dims} in {header.strips:,} strips: more strips than an image of that "
                f"height has rows")
    if header.held_bytes > MAX_HELD_BYTES:
        return (f"image/tiff, {dims} at {header.bits_per_pixel} bits a pixel: rendering it would hold "
                f"{mib(header.held_bytes)}, over the {mib(MAX_HELD_BYTES)} cap")
    if header.compression not in COMPRESSIONS:
        return (f"image/tiff with compression {header.compression}: not a coding a preview is rendered "
                f"from ({', '.join(COMPRESSIONS.values())})")
    return None


def entry_previewable(entry: dict) -> bool:
    """Whether a record's figure entry is one a preview is ATTEMPTED for, from
    what the entry itself says: a type on `PREVIEW_OF` and a size within the
    cap. The header's caps are the renderer's, at the route."""
    size = entry.get("bytes")
    return entry.get("media_type") in PREVIEW_OF and not (isinstance(size, int) and size > MAX_SOURCE_BYTES)


def store(shard_tree: Path) -> Path:
    """Where a collection's previews are kept: the fast drive when the node
    names one, else `<name>/state/figure-preview` beside its SHARD tree —
    derived, re-creatable, never exchanged (invariant 6)."""
    from app import derived
    return derived.place(KIND, Path(shard_tree)) or (Path(shard_tree).parent / "state" / KIND)


def _slots_dir(shard_tree: Path) -> Path:
    """Where the node's renderers take their turn: one place for every
    collection when a fast drive is named, the collection's own store else."""
    from app import derived
    base = derived.root(KIND)
    return base / KIND if base is not None else store(shard_tree)


def _files(shard_tree: Path, want: str) -> tuple[Path, Path]:
    """(the preview kept, the refusal remembered) for one figure, by this
    renderer's version."""
    base = store(shard_tree) / want[:2]
    stem = f"{want}.w{WIDTH}.r{RENDERER}-pillow{PILLOW}"
    return base / f"{stem}.png", base / f"{stem}.no"


def _remember_refusal(no: Path, why: str, for_s: int | None = None, tries: int = 0) -> None:
    """Keep why no preview is shown, for good or — `for_s` — until then;
    `tries`: how many times in a row it was not done within its seconds."""
    said: dict = {"why": why, "at": int(time.time())}
    if for_s is not None:
        said["until"] = said["at"] + for_s
    if tries:
        said["tries"] = tries
    try:
        no.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=no.parent, prefix=".", suffix=".no.tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(said))
        os.replace(name, no)
    except OSError:
        pass                                # not remembered: the next view tries again


def _record(no: Path) -> dict:
    """What is kept for this figure, whether it still holds or not; {} for none."""
    try:
        said = json.loads(no.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return said if isinstance(said, dict) and isinstance(said.get("why"), str) else {}


def _remembered(no: Path) -> tuple[str, float | None] | None:
    """(the refusal kept for this figure, the seconds it still holds — None:
    for good) while it holds, else None. One that names an end holds until
    then — and not past `RETRY_S` from now, whatever a clock set back wrote."""
    said = _record(no)
    if not said:
        return None
    until = said.get("until")
    if until is None:
        return said["why"], None
    now = time.time()
    if not isinstance(until, (int, float)) or now >= until or until - now > RETRY_S:
        return None
    return said["why"], until - now


def _raise_remembered(no: Path) -> None:
    got = _remembered(no)
    if got is not None:
        raise (NotPreviewable(got[0]) if got[1] is None else NotPreviewableNow(got[0], got[1]))


def _tmp_dir(shard_tree: Path) -> Path:
    """Where a collection's renderers put what they are making: ONE folder
    on the store's own drive (a preview is moved into place from it), so
    what a killed renderer left — a plate's copy can be 1 GiB — is found by
    the next render of any plate, not only one of the same hash prefix."""
    return store(shard_tree) / ".tmp"


def _sweep(where: Path) -> None:
    """What a renderer that was killed left in `where`, once older than any
    renderer still running could be."""
    old = time.time() - RETRY_S
    try:
        with os.scandir(where) as entries:
            for e in entries:
                try:
                    if e.is_file(follow_symlinks=False) and e.stat().st_mtime < old:
                        os.unlink(e.path)
                except OSError:
                    pass
    except OSError:
        pass


def _drop_other_versions(kept: Path, no: Path, want: str) -> None:
    """What an OLDER renderer kept or refused for this figure: gone once
    this one has an answer of its own for it (the names carry the version)."""
    try:
        with os.scandir(kept.parent) as entries:
            for e in entries:
                if e.name.startswith(want + ".") and e.name not in (kept.name, no.name):
                    try:
                        os.unlink(e.path)
                    except OSError:
                        pass
    except OSError:
        pass


@contextmanager
def _slot(where: Path) -> Iterator[None]:
    """One of the node's `CONCURRENT` renderers, ACROSS PROCESSES: an
    exclusive `flock` on one of that many lock files (the call
    `derived.building` makes for one builder; here it is one of N, waited
    for with a bound). `NotPreviewableNow` when every one stays taken for
    `WAIT_S`. A lock is its holder's: the kernel drops it with the process."""
    where.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + WAIT_S
    while True:
        for i in range(CONCURRENT):
            f = open(where / f".render.{i}.lock", "a+b")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close()
                continue
            try:
                yield
            finally:
                try:
                    fcntl.flock(f, fcntl.LOCK_UN)
                finally:
                    f.close()
            return
        if time.monotonic() >= deadline:
            raise NotPreviewableNow(f"{CONCURRENT} previews are being rendered; asked again, this one is made",
                                    BUSY_RETRY_S)
        time.sleep(0.05)


def _rss_bytes(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def _render(container: Path, want: str, source_bytes: int, dest: Path) -> tuple[int | str, str]:
    """Run the child over one figure: (its exit code, what it said) — or
    (`TIMED_OUT` | `OVER_MEMORY`, the reason) when THIS process killed it,
    past RENDER_TIMEOUT_S or past its memory. The child is never left
    running: whatever ends this call ends it."""
    limit = min(source_bytes, MAX_SOURCE_BYTES) + CHILD_DATA_BYTES + CHILD_BASE_BYTES
    child = subprocess.Popen(
        [sys.executable, "-m", "app.ask.preview_child", str(container), want, str(dest),
         str(WIDTH), str(MAX_HEIGHT), str(CHILD_DATA_BYTES), str(int(RENDER_TIMEOUT_S) + 30)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=str(Path(__file__).resolve().parents[2]))
    try:
        deadline = time.monotonic() + RENDER_TIMEOUT_S
        while child.poll() is None:
            rss = _rss_bytes(child.pid)
            killed: tuple[str, str] | None = None
            if time.monotonic() > deadline:
                killed = (TIMED_OUT, f"it was not rendered within {RENDER_TIMEOUT_S:.0f} s (asked again "
                                     f"within the hour, it is tried again)")
            elif rss is not None and rss > limit:
                killed = (OVER_MEMORY, f"rendering it held over {limit / (1 << 20):,.0f} MiB")
            if killed:
                child.kill()
                child.wait()
                return killed
            time.sleep(0.05)
        # the child writes one short line of JSON; no more of it than that is read
        out = (child.stdout.read(65536) if child.stdout else b"").decode("utf-8", "replace")
        return int(child.returncode), out.strip()
    finally:
        if child.poll() is None:            # an interrupt, an error of ours: the child goes with us
            child.kill()
            child.wait()
        if child.stdout:
            child.stdout.close()


def _said(out: str, key: str) -> str | None:
    """The child's reason under `key`, when its one line of JSON gives one."""
    try:
        got = json.loads(out)[key]
    except (ValueError, KeyError, TypeError):
        return None
    return str(got)[:400] if got else None


def _judge(code: int | str, out: str) -> tuple[str, int | None]:
    """A child that made no preview: (why, how long that holds — None is for
    good). The FILE's reasons hold for this renderer's version; the
    MOMENT's for an hour."""
    if code == REFUSED:
        return _said(out, "refused") or "its header was refused", None
    if code == NOT_NOW:
        return _said(out, "now") or "the renderer could not run just now", RETRY_S
    if code == TIMED_OUT:
        return out, RETRY_S                 # the node's load, perhaps: not the file's for good
    if code == OVER_MEMORY:
        return out, None
    if isinstance(code, int) and code < 0:
        if -code in _CRASHES:
            return f"the decoder crashed on it (signal {-code})", None
        # killed, and not by this process: the kernel for memory (the child
        # puts itself first), a stop of the container, its own alarm
        return (f"the renderer was killed on it (signal {-code}); asked again within the hour, it is "
                f"tried again"), RETRY_S
    failed = _said(out, "failed")
    if failed:
        return f"the decoder failed on it ({failed})", None
    # it ended without a word: it did not get as far as the figure
    return f"the renderer did not run (exit {code}); asked again within the hour, it is tried again", RETRY_S


def _make(collection: str, container: Path, want: str, size: int, kept: Path, no: Path,
          tmp_dir: Path) -> bytes:
    """Render one figure's preview, keep it and return it — or remember why
    not and raise. The caller holds a renderer's slot."""
    from app.ask import figures as figures_mod
    kept.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    _sweep(tmp_dir)
    fd, name = tempfile.mkstemp(dir=tmp_dir, prefix=f"{want[:12]}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    tries = 0
    try:
        code, out = _render(container, want, size, tmp)
        if code == 0:
            made = tmp.stat().st_size
            if made > MAX_PREVIEW_BYTES:
                why, for_s = (f"the preview made of it is {made / (1 << 20):,.0f} MiB, over the "
                              f"{MAX_PREVIEW_BYTES >> 20} MiB a page shows"), None
            else:
                data = tmp.read_bytes()
                bad = figures_mod.not_displayable(data, "image/png")
                if bad is None:
                    os.chmod(tmp, 0o644)
                    os.replace(tmp, kept)
                    _drop_other_versions(kept, no, want)
                    return data
                why, for_s = f"the preview made of it is itself not displayable ({bad})", None
        else:
            why, for_s = _judge(code, out)
            if code == TIMED_OUT:
                # the node's load once, perhaps twice; the third time it is the file
                tries = int(_record(no).get("tries") or 0) + 1
                if tries >= TIMEOUT_TRIES:
                    why, for_s = (f"it was not rendered within {RENDER_TIMEOUT_S:.0f} s, {tries} times "
                                  f"an hour apart: not tried again"), None
    finally:
        # the child's own temporary copy too: a child that was killed left it
        for left in (tmp, tmp.with_name(tmp.name + ".src")):
            try:
                left.unlink()
            except OSError:
                pass
    print(f"[{collection}] no preview of figure {want[:12]}…: {why}", flush=True)
    _remember_refusal(no, why, for_s, tries)
    _drop_other_versions(kept, no, want)
    raise (NotPreviewable(why) if for_s is None else NotPreviewableNow(why, for_s))


def png(collection: str, uid: str, hash_: str, *, root: Path | None = None, render: bool = True) -> bytes:
    """The PNG preview of one figure, rendered now or read from where it was
    kept. `NotPreviewable` with the reason where none can be shown —
    `NotPreviewableNow` where it is the moment's; `figures.FigureNotServed`
    where the figure itself is not served: a red-class collection, terms not
    acknowledged, a plate this node does not hold — asked FIRST, at every
    call, so nothing kept answers for a figure that would be refused (04
    §4.4). `render=False` reads what is kept and makes nothing. The figure's
    bytes are never read here."""
    from app.ask import figures as figures_mod
    container, want, entry, shard_tree = figures_mod.figure_place(collection, uid, hash_, root=root)
    media = str(entry.get("media_type") or "")
    if media not in PREVIEW_OF:
        raise NotPreviewable(f"{media or 'an unnamed type'}: not a type a preview is rendered from")
    size = figures_mod.held_size(container, want)          # 404 where it is not held, kept preview or not
    kept, no = _files(shard_tree, want)
    try:
        return kept.read_bytes()
    except OSError:
        pass
    _raise_remembered(no)
    if size > MAX_SOURCE_BYTES:
        why = (f"image/tiff, {size / (1 << 20):,.0f} MiB: over the {MAX_SOURCE_BYTES >> 20:,} MiB cap a "
               f"preview is rendered from")
        _remember_refusal(no, why)
        raise NotPreviewable(why)
    if not render:
        raise NotPreviewableNow("its preview is not made yet", 1)
    try:
        with _slot(_slots_dir(shard_tree)):
            try:
                return kept.read_bytes()        # rendered while this request waited
            except OSError:
                pass
            _raise_remembered(no)               # or refused
            return _make(collection, container, want, size, kept, no, _tmp_dir(shard_tree))
    except OSError as e:
        # the store cannot be written, the child cannot be started: the node's, and the moment's
        raise NotPreviewableNow(f"a preview cannot be made here just now ({type(e).__name__}: "
                                f"{e.strerror or e})", BUSY_RETRY_S) from None
