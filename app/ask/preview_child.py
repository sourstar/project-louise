"""The CHILD that renders one figure's preview (app/ask/preview.py, which says
why a child, and what bounds it).

    python -m app.ask.preview_child <container> <hash> <dest.png> <width> <max height>

One figure streamed from its container to a temporary file beside `dest`,
checked against its hash and its length as it goes (`figures.stream_figure`:
its bytes are never held here); its TIFF header read and held to the caps
BEFORE any pixel (`preview.tiff_header`, `preview.refused`); then, and only
then, Pillow's TIFF decoder — that one plugin, nothing sniffed — its first
image reduced to at most `width` × `max height` and written as PNG to `dest`.

Exit 0: `dest` is the preview. Exit 3 (`preview.REFUSED`): refused before
decoding, `{"refused": why}` on stdout. Anything else: the decoder failed.
The parent kills this process past its seconds or its memory.

A 1-bit page (the fax codings of an official journal's forms) is turned to
grey before it is reduced, so its lines are antialiased instead of dropped;
palette and CMYK become RGB; 16-bit grey becomes 8 by its high byte (the
corpus holds two). Nothing of the original
changes: it is held as it came, and offered as a download beside its preview.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def render(source: Path, dest: Path, width: int, max_height: int, declared: tuple[int, int]) -> None:
    """`source`'s first image as a PNG of at most `width` × `max_height`."""
    from PIL import Image
    from app.ask import preview
    # Pillow's own guard, set to the same cap: it warns past it and refuses at twice
    Image.MAX_IMAGE_PIXELS = preview.MAX_SOURCE_PIXELS
    with Image.open(source, formats=["TIFF"]) as im:
        im.seek(0)
        if im.size != declared:
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
        im.save(dest, "PNG")


def _first_for_the_oom_killer() -> None:
    """Should the web's memory run out while this child decodes, the kernel
    takes THIS process, not the web (as a parse child before the worker,
    app/parallel.py; raising one's own score needs no privilege)."""
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("1000")
    except OSError:
        pass


def main(argv: list[str]) -> int:
    container, want, dest, width, max_height = Path(argv[0]), argv[1], Path(argv[2]), int(argv[3]), int(argv[4])
    _first_for_the_oom_killer()
    from app.ask import preview
    from app.hold import figures as fig
    source = dest.with_name(dest.name + ".src")
    try:
        try:
            size = fig.read_index(container)[want].bytes
        except KeyError:
            print(json.dumps({"refused": "the figure is not in the container its record names"}))
            return preview.REFUSED
        if size > preview.MAX_SOURCE_BYTES:
            print(json.dumps({"refused": preview.refused(preview.TiffHeader(1, 1, 1, 1), size)}))
            return preview.REFUSED
        # IN PIECES, to the file the decoder will read: this process never
        # holds the figure's bytes (565 MB for the corpus's largest), and
        # `stream_figure` fails a figure that is not its hash or its length
        try:
            with open(source, "wb") as out:
                for piece in fig.stream_figure(container, want):
                    out.write(piece)
        except fig.FigureError as e:
            print(json.dumps({"refused": f"the figure held is not the one its record names ({e})"}))
            return preview.REFUSED
        with open(source, "rb") as f:
            try:
                header = preview.tiff_header(f)
            except preview.NotPreviewable as e:
                print(json.dumps({"refused": f"image/tiff: {e}"}))
                return preview.REFUSED
        why = preview.refused(header, size)
        if why is not None:
            print(json.dumps({"refused": why}))
            return preview.REFUSED
        render(source, dest, width, max_height, (header.width, header.height))
        return 0
    finally:
        try:
            os.unlink(source)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
