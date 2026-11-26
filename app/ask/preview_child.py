"""The CHILD that renders one figure's preview (app/ask/preview.py, which says
why a child, and what bounds it).

    python -m app.ask.preview_child <container> <hash> <dest.png> <width> <max height>
                                    <data bytes> <seconds>

One figure streamed from its container to a temporary file beside `dest`,
checked against its hash and its length as it goes (`figures.stream_figure`:
its bytes are never held here); its TIFF header read and held to the caps
BEFORE any pixel (`preview.tiff_header`, `preview.refused`); then, and only
then, Pillow's TIFF decoder — that one plugin, nothing sniffed — its first
image reduced to at most `width` × `max height` (`render`) and written as
PNG to `dest` (`write`),
with NOTHING of the plate's metadata (a colour profile, a description: the
publisher's bytes, which Pillow's PNG writer would copy in whole).

ITS OWN LIMITS, set when it is run as a program (`_bound_myself`,
`_limit_data`), never when `main` is called by a test: the kernel's first
choice should its cgroup's memory run out; ended when its parent is gone
(`parallel.watch_parent`); an alarm after `<seconds>`; and, from the moment
the decoder is handed the file, a data limit of `<data bytes>` past what the
process holds then (RLIMIT_DATA, POSIX setrlimit, as the parse pool's large
lane sets it).

What it says, one line of JSON on stdout, and its exit code:
  0                       `dest` is the preview;
  3  {"refused": why}     refused before decoding — the file's (`preview.REFUSED`);
  4  {"now": why}         not possible just now — the container or the store
                          not readable or writable, a module missing: the
                          node's, tried again later (`preview.NOT_NOW`);
  1  {"failed": why}      the decoder failed on the file — the file's.
The parent kills this process past its seconds or its memory.

A 1-bit page (the fax codings of an official journal's forms) is turned to
grey before it is reduced, so its lines are antialiased instead of dropped;
palette and CMYK become RGB; 16-bit grey becomes 8 by its high byte (the
corpus holds two). Nothing of the original changes: it is held as it came,
and offered as a download beside its preview.
"""
from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path


def render(source: Path, width: int, max_height: int, declared: tuple[int, int]):
    """`source`'s first image, decoded and reduced to at most `width` ×
    `max_height`: a Pillow image in L, LA, RGB or RGBA."""
    from PIL import Image
    from app.ask import preview
    # Pillow's own guard, set to the same cap: it warns past it and refuses at twice
    Image.MAX_IMAGE_PIXELS = preview.MAX_SOURCE_PIXELS
    with Image.open(source, formats=["TIFF"]) as im:
        im.seek(0)
        # the same pixels the header was held to — as declared, or turned a
        # quarter (Pillow gives a page with Orientation 5–8 its sides swapped)
        if im.size not in (declared, declared[::-1]):
            raise ValueError(f"the decoder reads {im.size[0]}×{im.size[1]} where the header declares "
                             f"{declared[0]}×{declared[1]}")
        im.load()
        if im.mode == "1":
            im = im.convert("L")
        elif im.mode in ("P", "CMYK", "YCbCr", "LAB", "HSV"):
            im = im.convert("RGB")
        elif im.mode.startswith("I;16"):
            # by 256, the convention: Pillow's own I;16 → L CLIPS at 255, and a
            # 16-bit page comes out white (measured 2026-11-26: mean 254.5 of a ramp)
            im = im.convert("I").point(lambda v: v * (1 / 256)).convert("L")
        elif im.mode in ("I", "F"):
            lo, hi = im.getextrema()
            scale = 255.0 / (hi - lo) if hi > lo else 1.0
            im = im.point(lambda v: v * scale + (-lo * scale)).convert("L")
        elif im.mode not in ("L", "LA", "RGB", "RGBA"):
            im = im.convert("RGB")
        if im.width > width or im.height > max_height:
            # `thumbnail` keeps the aspect and never enlarges; `reducing_gap`
            # lets it shrink by whole factors first — a 325 MP page in a second
            im.thumbnail((width, max_height), Image.LANCZOS, reducing_gap=2.0)
        return im.copy() if im.readonly else im


def write(im, dest: Path) -> None:
    """THE PIXELS AND NOTHING ELSE, as PNG: Pillow's PNG writer copies
    `info` — the plate's colour profile, its text — into the file verbatim
    (an 8 MiB profile gave an 8 MiB preview that passed the display rule)."""
    im.info = {}
    im.save(dest, "PNG")


#: the errors of the SYSTEM under the decoder — the drive, the store, the
#: process's own limits on files — and no other: an errno the file itself can
#: cause (EINVAL, EOVERFLOW) is the file's
_THE_SYSTEMS = frozenset({errno.EIO, errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EMFILE, errno.ENFILE,
                          errno.ESTALE, errno.ENOENT, errno.EACCES})


def _say(key: str, why: object) -> None:
    print(json.dumps({key: str(why)[:400]}), flush=True)


def main(argv: list[str], before_decoding=None) -> int:
    """One figure's preview (the module's docstring). `before_decoding` is
    called once the figure's copy is written and its header has passed, and
    before the decoder is handed it: where a child sets its data limit."""
    container, want, dest, width, max_height = Path(argv[0]), argv[1], Path(argv[2]), int(argv[3]), int(argv[4])
    try:
        from app.ask import preview
        from app.hold import figures as fig
    except ImportError as e:
        _say("now", f"the renderer's modules do not load here ({e})")
        return 4                            # preview.NOT_NOW, which did not load
    source = dest.with_name(dest.name + ".src")
    try:
        try:
            size = fig.read_index(container)[want].bytes
        except KeyError:
            _say("now", "the figure is not in the container held for it")
            return preview.NOT_NOW
        except (fig.FigureError, OSError) as e:
            _say("now", f"the container held for it does not read ({e})")
            return preview.NOT_NOW
        if size > preview.MAX_SOURCE_BYTES:
            _say("refused", preview.refused(preview.TiffHeader(1, 1, 1, 1), size))
            return preview.REFUSED
        # IN PIECES, to the file the decoder will read: this process never
        # holds the figure's bytes (565 MB for the corpus's largest), and
        # `stream_figure` fails a figure that is not its hash or its length.
        # A copy that is damaged, a store that is full: the node's and the
        # moment's — the copy is fetched again, the store is freed
        try:
            with open(source, "wb") as out:
                for piece in fig.stream_figure(container, want):
                    out.write(piece)
        except fig.FigureError as e:
            _say("now", f"the figure held is not the one its record names ({e})")
            return preview.NOT_NOW
        except OSError as e:
            _say("now", f"its copy could not be written beside the previews ({e.strerror or e})")
            return preview.NOT_NOW
        with open(source, "rb") as f:
            try:
                header = preview.tiff_header(f)
            except preview.NotPreviewable as e:
                _say("refused", f"image/tiff: {e}")
                return preview.REFUSED
        why = preview.refused(header, size)
        if why is not None:
            _say("refused", why)
            return preview.REFUSED
        if before_decoding is not None:
            before_decoding()
        # DECODING: what fails here is the file's, but for an error of the
        # system under it (`_THE_SYSTEMS`)
        try:
            im = render(source, width, max_height, (header.width, header.height))
        except ImportError as e:
            _say("now", f"the decoder does not load here ({e})")
            return preview.NOT_NOW
        except MemoryError:
            _say("failed", "it needs more memory than a preview is given")
            return 1
        except OSError as e:
            if e.errno in _THE_SYSTEMS:
                _say("now", f"its copy could not be read back ({e.strerror or e})")
                return preview.NOT_NOW
            # anything else is what the FILE made the decoder do — a seek to a
            # negative offset it names is EINVAL, and was taken for the node's
            # (the second review: such a plate was tried again every hour, for ever)
            _say("failed", f"{type(e).__name__}: {e}")
            return 1
        except Exception as e:              # noqa: BLE001 — whatever the decoder raises on the file, said
            _say("failed", f"{type(e).__name__}: {e}")
            return 1
        # WRITING: the store's, and the moment's
        try:
            write(im, dest)
        except MemoryError:
            _say("failed", "it needs more memory than a preview is given")
            return 1
        except OSError as e:
            _say("now", f"the preview could not be written ({e.strerror or e})")
            return preview.NOT_NOW
        return 0
    finally:
        try:
            os.unlink(source)
        except OSError:
            pass


def _vm_data() -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmData:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def _bound_myself(seconds: int) -> None:
    """This process's own settings (the module's docstring), all but its
    data limit, which `_limit_data` sets later."""
    try:
        # the kernel takes THIS process, not the web, should the cgroup's
        # memory run out (as a parse child before the worker, app/parallel.py;
        # raising one's own score needs no privilege)
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("1000")
    except OSError:
        pass
    import signal
    signal.alarm(max(1, seconds))           # SIGALRM ends it: the parent's own clock is shorter
    try:
        from app import parallel
        parallel.watch_parent()             # its parent gone, it ends: nobody is left to watch it
    except ImportError:
        pass                                # `main` says it, as "now"


def _limit_data(data_bytes: int) -> None:
    """THE DATA LIMIT, counted from what this process holds NOW — its
    modules loaded, the figure's copy written: RLIMIT_DATA is every private
    writable mapping, the allocators' reserves among them (the arrow pool the
    copy is streamed through reserves a gigabyte of address space; a limit
    counted from before it refused the corpus's own largest plate, measured
    2026-11-26). Past it an allocation fails in this process and nowhere
    else: a MemoryError, said as the file's."""
    import resource
    vm = _vm_data()
    if vm is None or data_bytes <= 0:
        return
    _soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
    want = vm + data_bytes
    if hard != resource.RLIM_INFINITY:
        want = min(want, hard)
    resource.setrlimit(resource.RLIMIT_DATA, (want, hard))


if __name__ == "__main__":
    _data = int(sys.argv[6]) if len(sys.argv) > 6 else 0
    _bound_myself(int(sys.argv[7]) if len(sys.argv) > 7 else 600)
    sys.exit(main(sys.argv[1:6], before_decoding=lambda: _limit_data(_data)))
