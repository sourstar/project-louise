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
So:

  - THE HEADER FIRST, by a reader of our own (`tiff_header`: the first image
    file directory of a classic TIFF, five tags, nothing else): the declared
    pixels, the bytes they decode to and the compression are held to caps
    BEFORE the decoder is handed the file. A decompression bomb is refused
    on what it declares.
  - A CLOSED LIST of compressions, the ones the corpus holds (`COMPRESSIONS`):
    a coding admitted "in case" is a decoder admitted in case. JPEG-in-TIFF,
    Deflate and the rest are refused until a corpus shows them.
  - A CHILD PROCESS (app/ask/preview_child.py): the web never holds the
    figure's bytes (EUR-Lex's largest is 565 MB) nor runs the decoder. The
    parent watches the child's memory and its seconds and kills it past
    either; one preview is rendered at a time on a node.
  - THE PNG IT MAKES passes 08 §8.7's own display rule (`figures.
    not_displayable`) before it is kept or served, like any plate.
  - A refusal is REMEMBERED beside the cache (`<hash>.no`, with its reason),
    so a figure that cannot be previewed costs one attempt, not one a view.

THE CAPS ARE SET FROM THE CORPUS, as 08 §8.7's were (München's header count
of EUR-Lex's 335,166 images, 2026-11-26, no pixel decoded): declared pixels
p50 0.137 MP, p99 27.7 MP, max 324.7 MP (three certificate forms of 15,794 ×
20,561, 8-bit, uncompressed); decoded size p99 40 MiB, max 539 MiB; the
largest file 565.7 MB (9,954 × 18,942 RGB). So 400 MP declared, 1 GiB decoded
and 1 GiB of file: a little over the largest honest one each. Over a cap the
figure is "not displayable — download", as before.

STANDARD, WRITTEN DOWN (CLAUDE.md): the decoder is Pillow's (libtiff), the
library every Python image path uses, declared in requirements.txt from this
day — it was importable in the image only because another package brought
it. The isolation is a subprocess watched by its parent; the standard
alternative is a sandbox proper (seccomp/landlock via bubblewrap, or a
decoder compiled to WebAssembly): worth adopting the day previews are
rendered for collections whose publisher is not also an institution's
official journal, at the cost of a system dependency in the image.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

KIND = "figure-preview"
#: the media types a preview is rendered for — what the record's entry names
PREVIEW_OF = ("image/tiff",)
#: the preview: at most this wide and this tall, in pixels (its aspect kept)
WIDTH, MAX_HEIGHT = 1600, 6400
#: the caps on the SOURCE, from the corpus (the module's docstring)
MAX_SOURCE_BYTES = 1 << 30
MAX_SOURCE_PIXELS = 400_000_000
MAX_DECODED_BYTES = 1 << 30
#: TIFF `Compression` values a preview is rendered from: those EUR-Lex holds
#: (uncompressed, CCITT G3 and G4, LZW, PackBits) and CCITT's modified Huffman
COMPRESSIONS = {1: "uncompressed", 2: "CCITT modified Huffman", 3: "CCITT Group 3", 4: "CCITT Group 4",
                5: "LZW", 32773: "PackBits"}
#: the child is killed past this many seconds, or past the source's bytes plus
#: the decoded cap plus this margin of resident memory
RENDER_TIMEOUT_S = 180.0
RSS_MARGIN_BYTES = 768 << 20
#: the child's exit code for "refused before decoding", its reason as JSON on stdout
REFUSED = 3
#: how long a second request waits for the one renderer before it is told
#: "busy" (not remembered: the next view asks again)
WAIT_S = 60.0
_one_at_a_time = threading.Lock()


class NotPreviewable(Exception):
    """No preview can be shown for this figure: the reason, in words."""


@dataclass(frozen=True)
class TiffHeader:
    """What a TIFF's first image file directory DECLARES."""
    width: int
    height: int
    bits_per_pixel: int
    compression: int

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def decoded_bytes(self) -> int:
        """What the decoder holds for it: a byte a pixel at least (a 1-bit
        page is decoded to a byte a pixel), more for deeper pixels."""
        return self.pixels * max(1, (self.bits_per_pixel + 7) // 8)


#: the tags read, and the TIFF field types whose values they may carry
_WIDTH, _HEIGHT, _BITS, _COMPRESSION, _SAMPLES = 256, 257, 258, 259, 277
_TYPE_SIZE = {3: 2, 4: 4}                    # SHORT, LONG
#: a directory with more entries than this is not an image's (TIFF 6.0 has ~70 baseline tags)
_MAX_ENTRIES = 4096


def tiff_header(f: BinaryIO) -> TiffHeader:
    """The first image file directory of a CLASSIC TIFF read from `f` — its
    width, height, bits per pixel and compression — or `NotPreviewable` with
    what is wrong. No pixel is read and no decoder is called: byte order,
    the magic 42, the directory's offset, then five tags (TIFF 6.0 §2).
    BigTIFF (magic 43) is refused: the corpus holds none."""
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
    for i in range(count):
        tag, kind, n, raw = struct.unpack(order + "HHI4s", entries[i * 12:(i + 1) * 12])
        if tag not in (_WIDTH, _HEIGHT, _BITS, _COMPRESSION, _SAMPLES):
            continue
        width = _TYPE_SIZE.get(kind)
        if width is None or not 1 <= n <= 16:
            raise NotPreviewable(f"its tag {tag} is of a type or count ({kind}, {n}) no image declares")
        if n * width <= 4:
            data = raw[:n * width]
        else:
            (at,) = struct.unpack(order + "I", raw)
            if not 8 <= at <= size - n * width:
                raise NotPreviewable(f"its tag {tag} points outside the file")
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
    # one BitsPerSample for every sample, or one each (TIFF 6.0 §6: default 1)
    bpp = sum(bits) if len(bits) == samples else bits[0] * max(1, samples)
    if not 1 <= bpp <= 64:
        raise NotPreviewable(f"its header declares {bpp} bits a pixel")
    return TiffHeader(w, h, bpp, got.get(_COMPRESSION, [1])[0])


def refused(header: TiffHeader, size: int) -> str | None:
    """Why no preview is rendered from a TIFF of this header and size — a cap
    or a coding outside the list — or None. Asked BEFORE the decoder."""
    mib = lambda n: f"{n / (1 << 20):,.0f} MiB"   # noqa: E731
    if size > MAX_SOURCE_BYTES:
        return f"image/tiff, {mib(size)}: over the {mib(MAX_SOURCE_BYTES)} cap a preview is rendered from"
    if header.pixels > MAX_SOURCE_PIXELS:
        return (f"image/tiff, {header.width}×{header.height}: over the "
                f"{MAX_SOURCE_PIXELS // 1_000_000}-megapixel cap a preview is rendered from")
    if header.decoded_bytes > MAX_DECODED_BYTES:
        return (f"image/tiff, {header.width}×{header.height} at {header.bits_per_pixel} bits a pixel decodes "
                f"to {mib(header.decoded_bytes)}: over the {mib(MAX_DECODED_BYTES)} cap")
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


def _files(shard_tree: Path, want: str) -> tuple[Path, Path]:
    base = store(shard_tree) / want[:2]
    return base / f"{want}.w{WIDTH}.png", base / f"{want}.w{WIDTH}.no"


def _remember_refusal(no: Path, why: str) -> None:
    try:
        no.parent.mkdir(parents=True, exist_ok=True)
        tmp = no.with_name(f".{no.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"why": why, "at": int(time.time())}), encoding="utf-8")
        os.replace(tmp, no)
    except OSError:
        pass                                # not remembered: the next view tries again


def _rss_bytes(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def _render(container: Path, want: str, source_bytes: int, dest: Path) -> tuple[int, str]:
    """Run the child over one figure: (its exit code, what it said). Killed —
    exit -9 and the reason — past RENDER_TIMEOUT_S or past its memory bound."""
    limit = min(source_bytes, MAX_SOURCE_BYTES) + MAX_DECODED_BYTES + RSS_MARGIN_BYTES
    child = subprocess.Popen(
        [sys.executable, "-m", "app.ask.preview_child", str(container), want, str(dest),
         str(WIDTH), str(MAX_HEIGHT)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=str(Path(__file__).resolve().parents[2]))
    deadline, killed = time.monotonic() + RENDER_TIMEOUT_S, ""
    while child.poll() is None:
        rss = _rss_bytes(child.pid)
        if time.monotonic() > deadline:
            killed = f"it was not rendered within {RENDER_TIMEOUT_S:.0f} s"
        elif rss is not None and rss > limit:
            killed = f"rendering it held over {limit / (1 << 20):,.0f} MiB"
        if killed:
            child.kill()
            child.wait()
            return -9, killed
        time.sleep(0.05)
    out = (child.stdout.read() if child.stdout else b"").decode("utf-8", "replace")
    return int(child.returncode), out.strip()


def png(collection: str, uid: str, hash_: str, *, root: Path | None = None) -> bytes:
    """The PNG preview of one figure, rendered now or read from where it was
    kept. `NotPreviewable` with the reason where none can be shown;
    `figures.FigureNotServed` where the figure itself is not served (not
    held, a red-class collection). The figure's bytes are never read here."""
    from app.ask import figures as figures_mod
    from app.hold import figures as fig
    container, want, entry, shard_tree = figures_mod.figure_place(collection, uid, hash_, root=root)
    media = str(entry.get("media_type") or "")
    if media not in PREVIEW_OF:
        raise NotPreviewable(f"{media or 'an unnamed type'}: not a type a preview is rendered from")
    kept, no = _files(shard_tree, want)
    try:
        return kept.read_bytes()
    except OSError:
        pass
    try:
        raise NotPreviewable(str(json.loads(no.read_text(encoding="utf-8"))["why"]))
    except (OSError, ValueError, KeyError):
        pass
    try:
        size = fig.read_index(container)[want].bytes
    except (fig.FigureError, OSError, KeyError):
        raise figures_mod.FigureNotServed(404, f"{want[:12]}… is not held here yet") from None
    if size > MAX_SOURCE_BYTES:
        why = (f"image/tiff, {size / (1 << 20):,.0f} MiB: over the {MAX_SOURCE_BYTES >> 20:,} MiB cap a "
               f"preview is rendered from")
        _remember_refusal(no, why)
        raise NotPreviewable(why)
    if not _one_at_a_time.acquire(timeout=WAIT_S):
        raise NotPreviewable("another preview is being rendered; asked again, this one is made")
    try:
        try:
            return kept.read_bytes()            # rendered while this request waited
        except OSError:
            pass
        kept.parent.mkdir(parents=True, exist_ok=True)
        tmp = kept.with_name(f".{kept.name}.{os.getpid()}.tmp")
        try:
            code, said = _render(container, want, size, tmp)
            if code == 0:
                data = tmp.read_bytes()
                why = figures_mod.not_displayable(data, "image/png")
                if why is None:
                    os.replace(tmp, kept)
                    return data
                said = f"the preview made of it is itself not displayable ({why})"
            elif code == REFUSED:
                try:
                    said = str(json.loads(said)["refused"])
                except (ValueError, KeyError, TypeError):
                    said = "its header was refused"
            elif code != -9:
                said = f"the renderer failed on it (exit {code})"
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        _remember_refusal(no, said)
        raise NotPreviewable(said)
    finally:
        _one_at_a_time.release()

