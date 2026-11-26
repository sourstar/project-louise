"""The CHILD that renders one figure's preview (app/ask/preview.py, which says
why a child, and what bounds it).

    python -m app.ask.preview_child <container> <hash> <dest.png> <width> <max height>

One figure read from its container, checked against its hash, written to a
temporary file beside `dest`; its TIFF header read and held to the caps
BEFORE any pixel (`preview.tiff_header`, `preview.refused`); then, and only
then, Pillow's TIFF decoder — that one plugin, nothing sniffed — its first
image reduced to at most `width` × `max height` and written as PNG to `dest`.

Exit 0: `dest` is the preview. Exit 3 (`preview.REFUSED`): refused before
decoding, `{"refused": why}` on stdout. Anything else: the decoder failed.
The parent kills this process past its seconds or its memory.

A 1-bit page (the fax codings of an official journal's forms) is turned to
grey before it is reduced, so its lines are antialiased instead of dropped;
palette and CMYK become RGB; 16-bit grey becomes 8. Nothing of the original
changes: it is held as it came, and offered as a download beside its preview.
"""
from __future__ import annotations

import hashlib
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
        elif im.mode not in ("L", "LA", "RGB", "RGBA"):
            im = im.convert("L" if im.mode.startswith(("I", "F")) else "RGB")
        if im.width > width or im.height > max_height:
            # `thumbnail` keeps the aspect and never enlarges; `reducing_gap`
            # lets it shrink by whole factors first — a 325 MP page in a second
            im.thumbnail((width, max_height), Image.LANCZOS, reducing_gap=2.0)
        im.save(dest, "PNG")


def main(argv: list[str]) -> int:
    container, want, dest, width, max_height = Path(argv[0]), argv[1], Path(argv[2]), int(argv[3]), int(argv[4])
    from app.ask import preview
    from app.hold import figures as fig
    source = dest.with_name(dest.name + ".src")
    try:
        entry = fig.read_index(container)[want]
        if entry.bytes > preview.MAX_SOURCE_BYTES:
            print(json.dumps({"refused": preview.refused(preview.TiffHeader(1, 1, 1, 1), entry.bytes)}))
            return preview.REFUSED
        with open(container, "rb") as f:
            f.seek(entry.offset)
            frame = f.read(entry.frame_bytes)
        data = fig._codec().decompress(frame, decompressed_size=entry.bytes)
        del frame
        view = memoryview(data)
        if hashlib.sha256(view).hexdigest() != want:
            print(json.dumps({"refused": "the figure held does not hash to the hash its record names"}))
            return preview.REFUSED
        with open(source, "wb") as out:
            out.write(view)
        size = len(view)
        del view, data                      # the decoder reads the file; the bytes are not held twice
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
