"""A node-rendered PREVIEW of a plate the browser cannot show (app/ask/
preview.py; 08 §8.7 amended 2026-11-26; the operator, of EUR-Lex's pictures:
"we must show pictures in the results as it is done for gutenberg
collection", "Option A").

  - a TIFF plate is answered inline as the PNG this node renders from it, at
    most 1,600 px wide, its own bytes a download beside it;
  - THE HEADER FIRST: what the file declares — pixels, sides, what rendering
    it would hold, tiles, the bytes its tags point at, its coding — is held
    to caps before any decoder is handed the file, and a directory two
    readers would read differently (a tag twice, tags out of order) is
    refused;
  - the web never holds the plate's bytes: a child renders under its own
    limits, the parent watches its memory and its seconds, three at a time
    on a node;
  - a preview is kept (derived data) and a refusal remembered — for good
    where it is the file's, for an hour where it may be the moment's — under
    the renderer's version;
  - a large plate's download is streamed, and its last piece is not sent
    before the whole is its hash.

Half of these tests are the findings of the independent review of
2026-11-26 (a Deflate plate decoded behind a declared LZW; a damaged plate
of the right length downloaded whole with status 200; the tests themselves
making pytest the kernel's first choice): each is named where it is held.
"""
from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import collections as collections_mod
from app.ask import api, figures as F, preview, preview_child
from app.hold import figures as fig
from app.pack import records as pack_records
from tests.ask.test_figures import _record, an_illustrated_copy, png as a_png
from tests.deposit.test_deposit_identity import node  # noqa: F401


def tiff(size=(2400, 3200), mode="1", compression="group4", **save) -> bytes:
    """A real TIFF: a page with a few black bars, as an official journal's form."""
    white = {"1": 1, "L": 255, "P": 255, "CMYK": (0, 0, 0, 0)}.get(mode, (255, 255, 255))
    black = {"1": 0, "L": 0, "P": 0, "CMYK": (0, 0, 0, 255)}.get(mode, (0, 0, 0))
    im = Image.new(mode, size, white)
    if mode == "P":
        im.putpalette([c for v in range(256) for c in (v, v, v)])
    for y in range(100, size[1], 400):
        im.paste(black, (100, y, size[0] - 100, y + 12))
    out = io.BytesIO()
    im.save(out, "TIFF", compression=compression, **save)
    return out.getvalue()


_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 7: 1, 14: 1}


def header_only(width: int = 16, height: int = 16, *, bits=1, compression=1, magic=42, order="<",
                extra=(), entries=None) -> bytes:
    """A classic TIFF that DECLARES a size and holds no pixel. `extra`: more
    (tag, type, count, value) entries, put in tag order; `entries`: the whole
    directory AS GIVEN (out of order, a tag twice). A value too large for
    its four bytes is given as the offset it lies at."""
    if entries is None:
        entries = sorted([(256, 4, 1, width), (257, 4, 1, height), (258, 3, 1, bits), (259, 3, 1, compression),
                          (277, 3, 1, 1), *extra])

    def value(kind: int, n: int, v: int) -> bytes:
        if kind == 3 and n == 1:
            return struct.pack(order + "HH", v, 0)
        return struct.pack(order + "I", v)
    ifd = struct.pack(order + "H", len(entries)) + b"".join(
        struct.pack(order + "HHI", tag, kind, n) + value(kind, n, v) for tag, kind, n, v in entries
    ) + struct.pack(order + "I", 0)
    return (b"II" if order == "<" else b"MM") + struct.pack(order + "HI", magic, 8) + ifd


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


CHART = a_png(640, 480, b"a chart, " * 40)


# ---- the header, before any pixel ---------------------------------------------------

def test_the_header_says_what_the_image_declares_and_no_decoder_is_called():
    got = preview.tiff_header(io.BytesIO(tiff((2400, 3200), "1", "group4")))
    assert (got.width, got.height, got.bits_per_pixel, got.compression, got.samples) == (2400, 3200, 1, 4, 1)
    assert got.pixels == 7_680_000 and got.decoded_bytes == 7_680_000, "a 1-bit page decodes to a byte a pixel"
    assert got.held_bytes == 2 * 7_680_000, "and the child holds its grey copy beside it"
    rgb = preview.tiff_header(io.BytesIO(tiff((300, 200), "RGB", "tiff_lzw")))
    assert (rgb.bits_per_pixel, rgb.compression, rgb.samples, rgb.photometric) == (24, 5, 3, 2)
    assert rgb.held_per_pixel == 4, "Pillow keeps colour at four bytes a pixel, whatever the file's three"
    palette = preview.tiff_header(io.BytesIO(tiff((300, 200), "P", "tiff_lzw")))
    assert (palette.photometric, palette.held_per_pixel) == (3, 5), "a palette page, and its RGB copy"
    # the other byte order (Pillow writes II on this machine: the MM branch was untested)
    assert header_only(64, 48, bits=8, order=">")[:4] == b"MM\x00*"
    big = preview.tiff_header(io.BytesIO(header_only(64, 48, bits=8, compression=5, order=">")))
    assert (big.width, big.height, big.bits_per_pixel, big.compression) == (64, 48, 8, 5)


@pytest.mark.parametrize("data, why", [
    (b"\x89PNG\r\n\x1a\n" + b"0" * 64, "not a TIFF"),
    (header_only(10, 10, magic=43), "BigTIFF"),
    (b"II" + struct.pack("<HI", 42, 9_999_999) + b"0" * 32, "outside the file"),
    (b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", 60_000) + b"0" * 64, "declares 60000 entries"),
    (header_only(entries=[(259, 3, 1, 1)]), "declares no dimensions"),
    (header_only(entries=[(256, 4, 1, 16), (257, 4, 1, 16), (322, 4, 1, 256)]), "one side of a tile"),
    (header_only(entries=[(256, 4, 1, 16), (257, 4, 1, 16), (258, 3, 3, 8), (277, 3, 1, 4)]), "3 bit depth"),
])
def test_what_is_not_a_readable_classic_tiff_is_refused_with_the_reason(data, why):
    with pytest.raises(preview.NotPreviewable, match=why):
        preview.tiff_header(io.BytesIO(data))


@pytest.mark.parametrize("entries, why", [
    # THE REVIEW'S FIRST FINDING: two Compression entries. libtiff decodes by the
    # FIRST (Deflate), this reader and Pillow's read the LAST (LZW): the closed
    # list of codings was no list
    ([(256, 4, 1, 16), (257, 4, 1, 16), (259, 3, 1, 8), (259, 3, 1, 5)], "tag 259 after 259"),
    ([(256, 4, 1, 16), (257, 4, 1, 16), (259, 3, 1, 7), (258, 3, 1, 8)], "tag 258 after 259"),
    ([(256, 4, 1, 16), (256, 4, 1, 30_000), (257, 4, 1, 16)], "tag 256 after 256"),
    # a count or a type the standard does not give the tag: libtiff ignores it and takes its default
    ([(256, 4, 1, 16), (257, 4, 1, 16), (259, 3, 2, 5)], r"tag 259 is of a type or count \(3, 2\)"),
    ([(256, 3, 2, 16), (257, 4, 1, 16)], r"tag 256 is of a type or count \(3, 2\)"),
    ([(256, 1, 1, 16), (257, 4, 1, 16)], r"tag 256 is of a type or count \(1, 1\)"),
    ([(256, 4, 1, 16), (257, 4, 1, 16), (700, 14, 4, 0)], "field type \\(14\\) TIFF 6.0 does not define"),
])
def test_a_directory_two_readers_would_read_differently_is_refused(entries, why):
    with pytest.raises(preview.NotPreviewable, match=why):
        preview.tiff_header(io.BytesIO(header_only(entries=entries)))


def test_what_a_decoder_would_read_before_any_pixel_is_counted():
    """150 private tags each pointing at the same 2 MiB held 303 MiB at
    `Image.open` (the review): the values the entries DECLARE are summed."""
    many = [(256, 4, 1, 16), (257, 4, 1, 16)] + [(40_000 + i, 7, 2 << 20, 8) for i in range(150)]
    with pytest.raises(preview.NotPreviewable, match="point at over 16 MiB of values"):
        preview.tiff_header(io.BytesIO(header_only(entries=many)))
    some = [(256, 4, 1, 16), (257, 4, 1, 16)] + [(40_000 + i, 7, 2 << 20, 8) for i in range(7)]
    assert preview.tiff_header(io.BytesIO(header_only(entries=some))).tag_bytes == 7 * (2 << 20)
    with pytest.raises(preview.NotPreviewable, match="declares 513 entries"):
        preview.tiff_header(io.BytesIO(header_only(
            entries=[(256, 4, 1, 16), (257, 4, 1, 16)] + [(1000 + i, 3, 1, 0) for i in range(511)]) + b"\0" * 8192))


def test_a_bomb_is_refused_on_what_it_declares():
    """A few dozen bytes that declare 30,000 × 30,000: refused by the caps,
    which are read from the header — the decoder never sees it."""
    H = preview.TiffHeader
    bomb = header_only(30_000, 30_000)
    head = preview.tiff_header(io.BytesIO(bomb))
    assert "over the 400-megapixel cap" in preview.refused(head, len(bomb))
    deep = preview.tiff_header(io.BytesIO(header_only(19_000, 19_000, bits=32)))
    assert deep.pixels < preview.MAX_SOURCE_PIXELS and "would hold" in preview.refused(deep, 100)
    assert "over the 1,024 MiB cap" in preview.refused(H(10, 10, 1, 1), (1 << 30) + 1)
    for coding in (2, 6, 7, 8, 34712):          # modified Huffman, old JPEG, JPEG, Deflate, JPEG 2000
        why = preview.refused(H(10, 10, 8, coding), 100)
        assert f"compression {coding}" in why and "not a coding" in why
    # WHAT THE HEADER'S PIXELS DO NOT SAY (the review, each measured in a child):
    # a tile of 46,000 × 46,000 for a 16 × 16 image held 1.8 GiB;
    assert "a tile no image of that size is cut into" in preview.refused(H(16, 16, 8, 32773, 1, 1, (46_000, 46_000)), 3000)
    assert "a tile no image" in preview.refused(H(16, 16, 8, 5, 1, 1, (0, 256)), 3000)
    assert preview.refused(H(100, 80, 8, 5, 1, 1, (256, 256)), 3000) is None, "a small picture in one tile"
    # a 1 × 40,000,000 strip held 414 MiB for 38 MiB of pixels (the decoder's table of rows);
    assert "a side over the 65,535 pixels" in preview.refused(H(1, 40_000_000, 8, 1), 100)
    # a palette page is held five bytes a pixel, 16-bit grey ten
    assert "would hold 1,073 MiB" in preview.refused(H(15_000, 15_000, 8, 5, 1, 3), 100)
    assert "would hold" in preview.refused(H(12_000, 12_000, 16, 1), 100)
    # THE CORPUS'S OWN LARGEST ARE RENDERED: three certificate forms of 15,794 ×
    # 20,561 at 8 bits, the 565.7 MB file of 9,954 × 18,942 RGB, two fax pages of
    # 15,794 × 20,540 (München's header count, 2026-11-26)
    assert preview.refused(H(15_794, 20_561, 8, 1, 1, 1), 324_800_000) is None
    assert preview.refused(H(9_954, 18_942, 24, 1, 3, 2), 565_666_968) is None
    assert preview.refused(H(15_794, 20_540, 1, 3, 1, 0), 1_015_582) is None


# ---- the child ------------------------------------------------------------------------

def _container(tmp_path, plates: dict[str, bytes]):
    path = tmp_path / "figures-00001.zst"
    fig.write_container(path, plates)
    return path


def _child(path, plate: bytes, dest) -> int:
    return preview_child.main([str(path), fig.bare(sha(plate)), str(dest), str(preview.WIDTH),
                               str(preview.MAX_HEIGHT)])


@pytest.mark.parametrize("mode, compression", [("1", "group4"), ("1", "group3"), ("1", "packbits"),
                                               ("L", "tiff_lzw"), ("P", "tiff_lzw"), ("RGB", None),
                                               ("CMYK", "tiff_lzw")])
def test_the_child_renders_a_png_of_at_most_1600_px_from_each_coding_the_corpus_holds(
        tmp_path, mode, compression):
    plate = tiff((2400, 3200), mode, compression)
    path, dest = _container(tmp_path, {sha(plate): plate}), tmp_path / "out.png"
    assert _child(path, plate, dest) == 0 and not (tmp_path / "out.png.src").exists(), "the temporary copy is gone"
    data = dest.read_bytes()
    assert F.not_displayable(data, "image/png") is None, "the preview passes 08 §8.7's own display rule"
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (1600, 2133) and im.mode == ("L" if mode in ("1", "L") else "RGB")
        assert im.getpixel((800, 2)) != im.getpixel((800, 70)), "the page's bars are in it, antialiased"


def test_a_small_image_is_not_enlarged_and_a_very_tall_one_is_held_to_the_height(tmp_path):
    small, tall = tiff((300, 200), "L", "tiff_lzw"), tiff((400, 40_000), "1", "group4")
    path = _container(tmp_path, {sha(small): small, sha(tall): tall})
    for plate, size in ((small, (300, 200)), (tall, (64, 6400))):
        dest = tmp_path / f"{len(plate)}.png"
        assert _child(path, plate, dest) == 0
        with Image.open(dest) as im:
            assert im.size == size


def test_sixteen_bit_grey_is_reduced_by_its_high_byte_and_not_clipped_to_white(tmp_path):
    """Pillow's own I;16 → L clips at 255: a ramp came out at a mean of 254.5."""
    import numpy as np
    from PIL import ImageStat
    out = io.BytesIO()
    Image.fromarray(np.linspace(0, 65535, 400 * 300).reshape(300, 400).astype("<u2")).save(out, "TIFF")
    plate = out.getvalue()
    assert preview.tiff_header(io.BytesIO(plate)).held_per_pixel == 10
    path, dest = _container(tmp_path, {sha(plate): plate}), tmp_path / "out.png"
    assert _child(path, plate, dest) == 0
    with Image.open(dest) as im:
        assert im.mode == "L" and 120 < ImageStat.Stat(im).mean[0] < 135


def test_the_preview_is_the_pixels_and_carries_nothing_of_the_plates_metadata(tmp_path):
    """THE REVIEW: Pillow's PNG writer copies the image's colour profile in
    whole — a 16 × 16 TIFF with an 8 MiB profile gave an 8 MiB preview that
    passed the display rule, the publisher's bytes inside it and served
    inline. The preview is the pixels."""
    marker = b"PUBLISHER-BYTES-" * 20_000                       # 320 KB of profile
    plate = tiff((600, 800), "RGB", "tiff_lzw", icc_profile=marker, description="a description " * 500)
    assert marker in plate
    path, dest = _container(tmp_path, {sha(plate): plate}), tmp_path / "out.png"
    assert _child(path, plate, dest) == 0
    data = dest.read_bytes()
    assert b"iCCP" not in data and b"tEXt" not in data and b"zTXt" not in data and b"iTXt" not in data
    assert b"PUBLISHER" not in data and len(data) < 60_000
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (600, 800) and not im.info.get("icc_profile")


def test_the_child_refuses_before_decoding_and_says_why(tmp_path, capsys, monkeypatch):
    bomb, deflate = header_only(30_000, 30_000), tiff((64, 64), "L", "tiff_adobe_deflate")
    # the review's plate, as a header: Deflate first, LZW second — libtiff would decode by the first
    twice = header_only(entries=[(256, 4, 1, 64), (257, 4, 1, 64), (258, 3, 1, 8), (259, 3, 1, 8), (259, 3, 1, 5)])
    tiled = header_only(16, 16, bits=8, compression=32773, extra=[(322, 4, 1, 46_000), (323, 4, 1, 46_000)])
    path = _container(tmp_path, {sha(x): x for x in (bomb, deflate, twice, tiled)})
    decoded = []
    monkeypatch.setattr(preview_child, "render", lambda *a, **k: decoded.append(a))
    for plate, why in ((bomb, "400-megapixel cap"), (deflate, "compression 8"),
                       (twice, "not in ascending tag order"), (tiled, "a tile no image of that size")):
        assert _child(path, plate, tmp_path / "x.png") == preview.REFUSED
        assert why in json.loads(capsys.readouterr().out)["refused"]
    assert decoded == [], "no decoder was handed any of them"


def test_what_is_the_nodes_and_the_moments_is_said_as_that_and_not_as_the_files(tmp_path, capsys, monkeypatch):
    """THE REVIEW: a container that was missing, a copy that could not be
    written and a store that was full each ended "the renderer failed on it
    (exit 1)", remembered FOR GOOD — and so did a damaged copy, after it was
    fetched again. Each is the moment's; only what the decoder says of the
    file is the file's."""
    plate = tiff((600, 800), "1", "group4")
    path = _container(tmp_path, {sha(plate): plate})

    def said(*argv) -> tuple[int, dict]:
        code = preview_child.main([str(a) for a in argv])
        return code, json.loads(capsys.readouterr().out)
    h = fig.bare(sha(plate))
    code, out = said(tmp_path / "gone.zst", h, tmp_path / "x.png", 1600, 6400)
    assert code == preview.NOT_NOW and "does not read" in out["now"]
    code, out = said(path, "ee" * 32, tmp_path / "x.png", 1600, 6400)
    assert code == preview.NOT_NOW and "not in the container" in out["now"]
    code, out = said(path, h, tmp_path / "no-such-dir" / "x.png", 1600, 6400)
    assert code == preview.NOT_NOW and "could not be written beside the previews" in out["now"]
    # the store full while the PNG is written
    monkeypatch.setattr(preview_child, "write", lambda im, dest: (_ for _ in ()).throw(OSError(28, "No space left")))
    code, out = said(path, h, tmp_path / "x.png", 1600, 6400)
    assert code == preview.NOT_NOW and "could not be written (No space left)" in out["now"]
    # a held copy that is damaged: fetched again, it will be previewed
    raw = bytearray(path.read_bytes())
    entry = fig.read_index(path)[h]
    for i in range(entry.offset + 6, entry.offset + entry.frame_bytes):
        raw[i] ^= 0xFF
    path.write_bytes(bytes(raw))
    code, out = said(path, h, tmp_path / "x.png", 1600, 6400)
    assert code == preview.NOT_NOW and "not the one its record names" in out["now"]
    assert not list(tmp_path.glob("*.src")), "no copy left behind on any of them"


def test_what_the_decoder_says_of_the_file_is_the_files(tmp_path, capsys):
    """A header that passes and pixels that do not decode: exit 1 and the
    decoder's own words — the reason was "exit 1" and nothing more."""
    plate = bytearray(tiff((600, 800), "L", "tiff_lzw"))
    head = preview.tiff_header(io.BytesIO(bytes(plate)))
    assert head.compression == 5
    for i in range(8, 8 + 2000):                               # the LZW strips, not the directory
        plate[i] = 0xFF
    plate = bytes(plate)
    assert preview.refused(preview.tiff_header(io.BytesIO(plate)), len(plate)) is None
    path = _container(tmp_path, {sha(plate): plate})
    code = _child(path, plate, tmp_path / "x.png")
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["failed"], out


def test_calling_the_child_in_this_process_changes_nothing_of_this_process(tmp_path):
    """THE REVIEW: these tests called `main`, and `main` wrote 1000 into
    /proc/self/oom_score_adj — the whole pytest run became the kernel's first
    choice. The child's limits are set where it is RUN (`_bound_myself`,
    under `__main__`), which a real child shows below."""
    import resource
    before = (Path("/proc/self/oom_score_adj").read_text(), resource.getrlimit(resource.RLIMIT_DATA))
    plate = tiff((600, 800), "1", "group4")
    assert _child(_container(tmp_path, {sha(plate): plate}), plate, tmp_path / "out.png") == 0
    assert (Path("/proc/self/oom_score_adj").read_text(), resource.getrlimit(resource.RLIMIT_DATA)) == before


def test_a_real_child_sets_its_own_limits_and_one_past_its_memory_fails_alone(tmp_path, monkeypatch):
    """The child as the parent runs it: first for the kernel, its data limit
    set, and an image it has no room for is its own failure — said, with
    this process untouched. (The header's caps are a model; this is the
    bound.)"""
    probe = ("import sys, resource; from app.ask import preview_child as c; c._bound_myself(30); "
             "c._limit_data(64 << 20); print(open('/proc/self/oom_score_adj').read().strip(), "
             "resource.getrlimit(resource.RLIMIT_DATA)[0] - c._vm_data(), __import__('signal').alarm(0))")
    got = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         cwd=str(Path(preview.__file__).resolve().parents[2]))
    score, room, alarm = got.stdout.split()
    assert score == "1000" and 0 < int(room) <= 64 << 20 and 0 < int(alarm) <= 30, got
    plate = tiff((6000, 5000), "RGB", "tiff_lzw")              # 30 MP: 120 MiB held as Pillow keeps colour
    path = _container(tmp_path, {sha(plate): plate})
    monkeypatch.setattr(preview, "CHILD_DATA_BYTES", 48 << 20)
    code, out = preview._render(path, fig.bare(sha(plate)), len(plate), tmp_path / "out.png")
    why, for_s = preview._judge(code, out)
    assert code == 1 and for_s is None and "the decoder failed on it" in why, (code, out)
    assert "memory" in why or "Error" in why, why      # Python's MemoryError, or libtiff's own refusal
    monkeypatch.setattr(preview, "CHILD_DATA_BYTES", preview.MAX_HELD_BYTES + (768 << 20))
    assert preview._render(path, fig.bare(sha(plate)), len(plate), tmp_path / "out.png")[0] == 0


@pytest.mark.parametrize("code, out, why, lasts", [
    (3, '{"refused": "image/tiff, 30000×30000: over the 400-megapixel cap"}', "400-megapixel", None),
    (4, '{"now": "the container held for it does not read"}', "does not read", 3600),
    ("seconds", "it was not rendered within 180 s", "not rendered within", 3600),
    ("memory", "rendering it held over 2,048 MiB", "held over", None),
    (1, '{"failed": "OSError: decoder error -9"}', r"the decoder failed on it \(OSError: decoder error -9\)", None),
    (1, "", r"the renderer did not run \(exit 1\)", 3600),          # it never reached the figure: an import, a crash at start
    (-9, "", r"killed on it \(signal 9\)", 3600),                   # the kernel for memory, a stop of the container
    (-14, "", r"killed on it \(signal 14\)", 3600),                 # its own alarm
    (-11, "", r"the decoder crashed on it \(signal 11\)", None),    # the decoder's own, on this file: not every hour for ever
    (-6, "", r"the decoder crashed on it \(signal 6\)", None),
])
def test_each_way_a_child_ends_is_the_files_for_good_or_the_moments_for_an_hour(code, out, why, lasts):
    import re
    got, for_s = preview._judge(code, out)
    assert re.search(why, got) and for_s == lasts


# ---- kept, remembered, bounded ----------------------------------------------------------

@pytest.fixture
def copy(node, monkeypatch):  # noqa: F811
    """A held collection whose record names three plates: a TIFF page, a TIFF
    bomb, and a PNG chart."""
    codex, name, d, snap = an_illustrated_copy(node)
    monkeypatch.setattr(collections_mod, "louise_root", lambda: node.parent)
    page, bomb = tiff((2400, 3200), "1", "group4"), header_only(30_000, 30_000)
    text = ("Words.\n\n[Illustration: ANNEX II, the form]\n\nMore.\n\n[Illustration: A bomb]\n\nEnd."
            "\n\n[Illustration: A chart]\n\nLast.")
    entries = [{"hash": sha(page), "media_type": "image/tiff", "bytes": len(page), "seq": 0, "carried": True},
               {"hash": sha(bomb), "media_type": "image/tiff", "bytes": len(bomb), "seq": 1, "carried": True},
               {"hash": sha(CHART), "media_type": "image/png", "bytes": len(CHART), "seq": 2, "carried": True}]
    fig.write_container(fig.path_for(d, "year=2023", 2), {sha(page): page, sha(bomb): bomb, sha(CHART): CHART})
    pack_records.write_shard(d / "records/year=2023/00002.jsonl.zst", [_record("10", entries, text)],
                             record_line=2)
    pack_records.reset_caches()
    return name, d, page, bomb, entries


def _hidden(where: Path) -> list[str]:
    return sorted(p.name for p in where.iterdir() if p.name.startswith("."))


def test_a_preview_is_rendered_once_in_a_child_and_kept(copy, monkeypatch):
    name, d, page, bomb, entries = copy
    runs = []
    real = preview._render
    monkeypatch.setattr(preview, "_render", lambda *a: runs.append(a[1]) or real(*a))
    first = preview.png(name, "pubmed:10", sha(page))
    assert F.sniff(first) == "image/png" and len(runs) == 1
    assert preview.png(name, "pubmed:10", sha(page)) == first and len(runs) == 1, "kept: not rendered again"
    kept, _no = preview._files(d, fig.bare(sha(page)))
    assert kept.read_bytes() == first and kept.parent.parent == preview.store(d)
    assert _hidden(kept.parent) == [], "no temporary file left beside it"
    assert oct(kept.stat().st_mode & 0o777) == "0o644"


def test_what_is_kept_and_what_is_refused_carry_the_renderers_version(copy, monkeypatch):
    """THE REVIEW: nothing ever invalidated a kept preview or a refusal — a
    raised cap, an added coding, the 16-bit fix or another Pillow never
    reached a plate already seen. The version is in the name."""
    name, d, page, bomb, entries = copy
    kept, no = preview._files(d, fig.bare(sha(page)))
    assert kept.name.endswith(f".w1600.r{preview.RENDERER}-pillow{preview.PILLOW}.png")
    assert no.name.endswith(f".w1600.r{preview.RENDERER}-pillow{preview.PILLOW}.no")
    with pytest.raises(preview.NotPreviewable, match="400-megapixel"):
        preview.png(name, "pubmed:10", sha(bomb))
    runs = []
    real = preview._render
    monkeypatch.setattr(preview, "_render", lambda *a: runs.append(a[1]) or real(*a))
    monkeypatch.setattr(preview, "RENDERER", preview.RENDERER + 1)
    with pytest.raises(preview.NotPreviewable, match="400-megapixel"):
        preview.png(name, "pubmed:10", sha(bomb))
    assert len(runs) == 1, "another renderer asks afresh"
    root = Path(preview.__file__).resolve().parents[2]
    assert f"pillow=={preview.PILLOW}" in (root / "constraints.txt").read_text().lower().split(), \
        "a Pillow moved in constraints.txt moves preview.PILLOW: what it rendered is rendered again"


def test_a_refusal_is_remembered_with_its_reason(copy, monkeypatch, capsys):
    name, d, page, bomb, entries = copy
    runs = []
    real = preview._render
    monkeypatch.setattr(preview, "_render", lambda *a: runs.append(a[1]) or real(*a))
    for _ in range(3):
        with pytest.raises(preview.NotPreviewable, match="400-megapixel cap") as e:
            preview.png(name, "pubmed:10", sha(bomb))
        assert not isinstance(e.value, preview.NotPreviewableNow), "the file's: asking later changes nothing"
    assert len(runs) == 1, "one attempt, not one a view"
    _kept, no = preview._files(d, fig.bare(sha(bomb)))
    assert "400-megapixel" in json.loads(no.read_text())["why"]
    said = capsys.readouterr().out
    assert said.count(f"[{name}] no preview of figure {fig.bare(sha(bomb))[:12]}…") == 1, "said once, with its collection"


def test_a_child_past_its_seconds_or_its_memory_is_killed_and_the_reason_said(copy, monkeypatch):
    name, d, page, bomb, entries = copy
    real, started = subprocess.Popen, []

    def a_child_that_never_ends(cmd, **kw):
        # it leaves the temporary copy a real child killed mid-way would
        Path(cmd[5] + ".src").write_bytes(b"half a plate")
        started.append(real([sys.executable, "-c", "import time; time.sleep(60)"], **kw))
        return started[-1]

    monkeypatch.setattr(preview, "RENDER_TIMEOUT_S", 0.3)
    monkeypatch.setattr(preview.subprocess, "Popen", a_child_that_never_ends)
    kept, no = preview._files(d, fig.bare(sha(page)))
    with pytest.raises(preview.NotPreviewableNow, match="not rendered within 0 s"):
        preview.png(name, "pubmed:10", sha(page))
    assert started[0].returncode == -9, "killed, and waited for"
    assert _hidden(kept.parent) == [], "the child's half copy is gone too"
    said = json.loads(no.read_text())
    assert said["until"] == said["at"] + preview.RETRY_S, "the node's load, perhaps: remembered for an hour"
    with pytest.raises(preview.NotPreviewableNow, match="not rendered within"):
        preview.png(name, "pubmed:10", sha(page))
    assert len(started) == 1, "not tried again within the hour"
    # a clock that was set back must not make an hour a year
    no.write_text(json.dumps({**said, "until": said["at"] + 400 * 86400}))
    assert preview._remembered(no) is None
    # the hour over: tried again — and this time it is its memory
    no.write_text(json.dumps({**said, "until": said["at"] - 1}))
    monkeypatch.setattr(preview, "RENDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(preview, "_rss_bytes", lambda pid: 10 << 30)
    with pytest.raises(preview.NotPreviewable, match="held over 2,048 MiB") as e:
        preview.png(name, "pubmed:10", sha(page))
    assert not isinstance(e.value, preview.NotPreviewableNow)
    assert len(started) == 2 and started[1].returncode == -9
    assert "until" not in json.loads(no.read_text()), "the file's own: remembered for good"


def test_a_child_is_never_left_running_whatever_ends_its_watch(copy, monkeypatch):
    """THE REVIEW: an interrupt in the watch loop (`louise ask pdf` renders
    from the command's main thread) left the child with no clock and no
    bound. Whatever ends `_render` ends the child."""
    name, d, page, bomb, entries = copy
    real, started = subprocess.Popen, []
    monkeypatch.setattr(preview.subprocess, "Popen", lambda cmd, **kw: started.append(
        real([sys.executable, "-c", "import time; time.sleep(60)"], **kw)) or started[-1])

    def interrupted(pid):
        raise KeyboardInterrupt
    monkeypatch.setattr(preview, "_rss_bytes", interrupted)
    kept, no = preview._files(d, fig.bare(sha(page)))
    with pytest.raises(KeyboardInterrupt):
        preview.png(name, "pubmed:10", sha(page))
    assert started[0].poll() == -9, "the child went with it"
    assert _hidden(kept.parent) == [] and not no.exists(), "nothing left, and nothing remembered of an interrupt"
    # and the slot it held is free again
    monkeypatch.setattr(preview, "WAIT_S", 0.2)
    with preview._slot(preview._slots_dir(d)):
        pass


def test_a_child_killed_by_another_is_tried_again_within_the_hour_and_a_decoder_that_fails_is_not(
        copy, monkeypatch):
    name, d, page, bomb, entries = copy
    real = subprocess.Popen
    kept, no = preview._files(d, fig.bare(sha(page)))
    for script, words, lasting in (
            ("import os, signal; os.kill(os.getpid(), signal.SIGKILL)", r"killed on it \(signal 9\)", False),
            ("raise SystemExit(1)", r"the renderer did not run \(exit 1\)", False),
            ("print('{\"now\": \"the store is full\"}'); raise SystemExit(4)", "the store is full", False),
            ("print('{\"failed\": \"ValueError: boom\"}'); raise SystemExit(1)",
             r"the decoder failed on it \(ValueError: boom\)", True),
            ("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)", r"the decoder crashed on it \(signal 11\)",
             True)):
        monkeypatch.setattr(preview.subprocess, "Popen",
                            lambda cmd, script=script, **kw: real([sys.executable, "-c", script], **kw))
        with pytest.raises(preview.NotPreviewable, match=words) as e:
            preview.png(name, "pubmed:10", sha(page))
        assert isinstance(e.value, preview.NotPreviewableNow) is not lasting
        assert ("until" not in json.loads(no.read_text())) is lasting
        no.unlink()


def test_three_are_rendered_at_once_on_a_node_and_the_fourth_waits_its_bound(tmp_path, monkeypatch):
    """One at a time was a thread's lock: the web and `louise ask pdf` each
    had their own. The slots are files' locks, the node's."""
    monkeypatch.setattr(preview, "WAIT_S", 0.3)
    held = [preview._slot(tmp_path) for _ in range(preview.CONCURRENT)]
    for h in held:
        h.__enter__()
    began = time.monotonic()
    with pytest.raises(preview.NotPreviewableNow, match="3 previews are being rendered"):
        with preview._slot(tmp_path):
            pass
    assert 0.25 < time.monotonic() - began < 5
    held[0].__exit__(None, None, None)
    with preview._slot(tmp_path):                                  # one freed: taken at once
        pass
    for h in held[1:]:
        h.__exit__(None, None, None)
    # ANOTHER PROCESS holding all three: the same answer, and theirs once it is gone
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, sys, time\n"
         f"fs = [open(r'{tmp_path}/.render.%d.lock' % i, 'a+b') for i in range({preview.CONCURRENT})]\n"
         "[fcntl.flock(f, fcntl.LOCK_EX) for f in fs]\nprint('held', flush=True)\ntime.sleep(60)"],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(preview.NotPreviewableNow):
            with preview._slot(tmp_path):
                pass
    finally:
        holder.kill()
        holder.wait()
        holder.stdout.close()
    with preview._slot(tmp_path):                                  # the kernel dropped its locks with it
        pass


def test_what_a_killed_renderer_left_is_swept_once_no_renderer_could_still_own_it(copy):
    name, d, page, bomb, entries = copy
    kept, no = preview._files(d, fig.bare(sha(page)))
    kept.parent.mkdir(parents=True)
    old, fresh = kept.parent / ".abc.tmp.src", kept.parent / ".def.tmp"
    old.write_bytes(b"a gigabyte, once")
    fresh.write_bytes(b"being written now")
    os.utime(old, (time.time() - 2 * preview.RETRY_S,) * 2)
    preview.png(name, "pubmed:10", sha(page))
    assert not old.exists() and fresh.exists() and kept.exists()


def test_a_store_that_cannot_be_written_is_the_moments_and_not_an_error(copy, monkeypatch):
    """THE REVIEW: an OSError from the store's mkdir or from starting the
    child left the route as a 500."""
    name, d, page, bomb, entries = copy
    monkeypatch.setattr(preview, "_slots_dir", lambda tree: Path("/proc/no-such-place/previews"))
    with pytest.raises(preview.NotPreviewableNow, match="cannot be made here just now"):
        preview.png(name, "pubmed:10", sha(page))
    app = FastAPI()
    app.include_router(api.router)
    r = TestClient(app).get("/api/ask/figure", params={"collection": name, "uid": "pubmed:10", "hash": sha(page)})
    assert r.status_code == 503 and r.headers["retry-after"] == "30" and "no preview just now" in r.json()["error"]
    _kept, no = preview._files(d, fig.bare(sha(page)))
    assert not no.exists(), "not remembered: the next view asks again"


def test_what_is_not_a_type_a_preview_is_rendered_for_is_not_attempted(copy, monkeypatch):
    name, d, page, bomb, entries = copy
    assert preview.entry_previewable(entries[0])
    assert not preview.entry_previewable({"media_type": "image/svg+xml", "bytes": 10})
    assert not preview.entry_previewable({"media_type": "image/tiff", "bytes": preview.MAX_SOURCE_BYTES + 1})
    monkeypatch.setattr(preview, "_render", lambda *a: pytest.fail("a child was started for a PNG"))
    with pytest.raises(preview.NotPreviewable, match="image/png: not a type a preview is rendered from"):
        preview.png(name, "pubmed:10", sha(CHART))
    # read what is kept, make nothing
    with pytest.raises(preview.NotPreviewableNow, match="not made yet"):
        preview.png(name, "pubmed:10", sha(page), render=False)


def test_nothing_kept_answers_for_a_figure_that_would_be_refused(copy, monkeypatch):
    """Invariant 9 and 04 §4.4: a preview KEPT while the collection could be
    read is not served once it cannot — terms no longer acknowledged — nor
    once this node no longer holds the plate (the route answered 200 from
    the cache where it answered 404 before previews existed: the review)."""
    from app import licensing
    name, d, page, bomb, entries = copy
    preview.png(name, "pubmed:10", sha(page))
    kept, _no = preview._files(d, fig.bare(sha(page)))
    assert kept.exists()
    with monkeypatch.context() as m:
        m.setattr(collections_mod, "by_name", lambda n: object())
        m.setattr(licensing, "state_for", lambda held: licensing.MISSING)
        with pytest.raises(F.FigureNotServed, match="terms are not acknowledged") as e:
            preview.png(name, "pubmed:10", sha(page))
        assert e.value.status == 403
    assert preview.png(name, "pubmed:10", sha(page)), "acknowledged again: the kept one answers"
    fig.path_for(d, "year=2023", 2).unlink()
    with pytest.raises(F.FigureNotServed, match="not held here yet") as e:
        preview.png(name, "pubmed:10", sha(page))
    assert e.value.status == 404 and kept.exists()


# ---- the route and the page -------------------------------------------------------------

def test_the_route_answers_a_tiff_plate_with_its_preview_and_the_plate_itself_as_a_download(copy):
    name, d, page, bomb, entries = copy
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    q = {"collection": name, "uid": "pubmed:10", "hash": sha(page)}
    r = client.get("/api/ask/figure", params=q)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "a preview rendered by this node" in r.headers["x-louise-preview"]
    assert r.headers["x-content-type-options"] == "nosniff"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (1600, 2133)
    r = client.get("/api/ask/figure", params={**q, "download": 1})
    assert r.status_code == 200 and r.content == page, "the origin's own bytes, byte for byte"
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"]
    r = client.get("/api/ask/figure", params={**q, "hash": sha(bomb)})
    assert r.status_code == 415 and "400-megapixel cap" in r.json()["error"]
    assert "download=1" in r.json()["error"]


def test_every_renderer_busy_is_a_503_to_ask_again_and_not_a_plate_that_cannot_be_shown(copy, monkeypatch):
    name, d, page, bomb, entries = copy
    monkeypatch.setattr(preview, "WAIT_S", 0.2)
    held = [preview._slot(preview._slots_dir(d)) for _ in range(preview.CONCURRENT)]
    for h in held:
        h.__enter__()
    app = FastAPI()
    app.include_router(api.router)
    q = {"collection": name, "uid": "pubmed:10", "hash": sha(page)}
    try:
        r = TestClient(app).get("/api/ask/figure", params=q)
    finally:
        for h in held:
            h.__exit__(None, None, None)
    assert r.status_code == 503 and r.headers["retry-after"] == "30" and r.json()["exit"] == 3
    assert "3 previews are being rendered" in r.json()["error"]
    assert not preview._files(d, fig.bare(sha(page)))[1].exists(), "busy is not remembered"
    assert TestClient(app).get("/api/ask/figure", params=q).status_code == 200


def test_a_large_plate_is_downloaded_in_pieces_and_is_its_hash(copy, monkeypatch):
    name, d, page, bomb, entries = copy
    monkeypatch.setattr(api, "STREAMED_OVER", 1_000)
    read_whole = []
    monkeypatch.setattr(F, "placed_bytes", lambda *a, **k: read_whole.append(a) or b"")
    app = FastAPI()
    app.include_router(api.router)
    r = TestClient(app).get("/api/ask/figure", params={"collection": name, "uid": "pubmed:10",
                                                       "hash": sha(page), "download": 1})
    assert r.status_code == 200 and r.content == page and read_whole == [], "never held whole"
    assert r.headers["content-length"] == str(len(page))
    path = fig.path_for(d, "year=2023", 2)
    pieces = list(fig.stream_figure(path, sha(page), chunk=256))
    assert b"".join(pieces) == page and len(pieces) > 1
    with pytest.raises(fig.FigureError, match="holds no figure"):
        list(fig.stream_figure(path, "sha256:" + "ee" * 32))


def test_a_streamed_plate_that_is_not_its_hash_or_its_length_fails_the_transfer(tmp_path, monkeypatch):
    plate, other = tiff((600, 800), "1", "group4"), tiff((600, 900), "1", "group4")
    path = tmp_path / "figures-00001.zst"
    fig.write_container(path, {sha(plate): plate})
    held = fig.read_index(path)[fig.bare(sha(plate))]
    # the index names these bytes under another plate's hash
    monkeypatch.setattr(fig, "read_index", lambda p: {fig.bare(sha(other)): held})
    got = []
    with pytest.raises(fig.FigureError, match="streamed"):
        for piece in fig.stream_figure(path, sha(other), chunk=256):
            got.append(piece)
    sent = b"".join(got)
    # THE REVIEW: every piece was handed on and the error raised after the last
    # — a receiver held the whole wrong file. The last piece is held back.
    assert plate.startswith(sent) and 0 < len(plate) - len(sent) <= 256, "all but the last piece, never the whole"
    # a frame that decompresses PAST what its index states is stopped at that piece
    short = dataclasses.replace(held, bytes=300)
    monkeypatch.setattr(fig, "read_index", lambda p: {fig.bare(sha(plate)): short})
    got = []
    with pytest.raises(fig.FigureError, match="decompresses past the 300 bytes"):
        for piece in fig.stream_figure(path, sha(plate), chunk=256):
            got.append(piece)
    assert sum(map(len, got)) <= 300
    # a damaged frame names its plate, as `walk` does
    raw = bytearray(path.read_bytes())
    for i in range(held.offset + 4, held.offset + held.frame_bytes):
        raw[i] ^= 0xFF
    path.write_bytes(bytes(raw))
    monkeypatch.setattr(fig, "read_index", lambda p: {fig.bare(sha(plate)): held})
    with pytest.raises(fig.FigureError, match="does not decompress|streamed"):
        list(fig.stream_figure(path, sha(plate)))


def test_a_download_that_is_not_its_hash_is_cut_short_and_never_arrives_whole(copy, monkeypatch):
    """THE REVIEW, over real HTTP: a plate over the streaming threshold, of
    the right length and not its hash, arrived COMPLETE with status 200 —
    where main answered 404 — because the hash was compared after the last
    byte was sent. The last piece is now held back: the body is short of the
    length it declared, which no HTTP client takes for a whole file."""
    name, d, page, bomb, entries = copy
    monkeypatch.setattr(api, "STREAMED_OVER", 1_000)
    path = fig.path_for(d, "year=2023", 2)
    # a damaged copy: the page's bytes with one bit turned, held under the page's hash — the same length
    twin = bytes(page[:-1]) + bytes([page[-1] ^ 1])
    fig.write_container(path, {sha(twin): twin, sha(bomb): bomb, sha(CHART): CHART})
    held = fig.read_index(path)
    monkeypatch.setattr(fig, "read_index", lambda p: {**held, fig.bare(sha(page)): held[fig.bare(sha(twin))]})
    assert len(twin) == len(page) > api.STREAMED_OVER
    app = FastAPI()
    app.include_router(api.router)
    sent: list[bytes] = []
    real = fig.stream_figure

    def watched(*a, **k):
        for piece in real(*a, **k):
            sent.append(piece)
            yield piece
    monkeypatch.setattr(fig, "stream_figure", watched)
    with pytest.raises(fig.FigureError):
        TestClient(app).get("/api/ask/figure", params={"collection": name, "uid": "pubmed:10",
                                                       "hash": sha(page), "download": 1})
    assert sum(map(len, sent)) < len(page), "short of the length the answer declared"


def test_the_page_draws_the_preview_and_offers_the_original(copy):
    name, d, page, bomb, entries = copy
    held = [dict(e, state="held", caption="") for e in entries]
    big = dict(held[0], bytes=preview.MAX_SOURCE_BYTES + 1)
    text = "Words.\n\n[Illustration: ANNEX II, the form]\n\nMore.\n\n[Illustration: A bomb]\n\nEnd."
    out = F.mentions(text, [held[0], big], link=None)
    assert out[0]["kind"] == "img" and out[0]["preview"] is True and out[0]["media_type"] == "image/tiff"
    assert out[0]["caption"] == "ANNEX II, the form" and out[0]["size"].endswith(("KB", "bytes", "MB"))
    assert out[1]["kind"] == "download", "over the cap a preview is rendered from: offered as a file, as before"
    src = (Path(api.__file__).resolve().parents[1] / "templates" / "_ask_results.html").read_text(encoding="utf-8")
    assert "plate-original" in src and "preview — the original" in src


def test_a_plate_that_will_not_be_shown_is_refused_before_its_bytes_are_read(copy, monkeypatch):
    """The route held a plate whole to find out it was over the display cap:
    with a corpus whose largest plate is 565 MB that is the web's memory.
    Its type and its length — the container's word — decide first."""
    name, d, page, bomb, entries = copy
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    q = {"collection": name, "uid": "pubmed:10", "hash": sha(CHART)}
    r = client.get("/api/ask/figure", params=q)
    assert r.status_code == 200 and r.content == CHART and r.headers["content-type"] == "image/png"
    assert "x-louise-preview" not in r.headers, "a PNG is shown as itself"
    read = []
    real = F.placed_bytes
    monkeypatch.setattr(F, "placed_bytes", lambda *a: read.append(a) or real(*a))
    monkeypatch.setattr(F, "MAX_DISPLAY_BYTES", 100)
    r = client.get("/api/ask/figure", params=q)
    assert r.status_code == 415 and "over the 16 MiB display cap" in r.json()["error"] and read == []
    r = client.get("/api/ask/figure", params={**q, "download": 1})
    assert r.status_code == 200 and r.content == CHART and len(read) == 1, "the file itself is still given"
    # a hash that is no hash: 404 with the reason (it was a 500 — the review, of main's own route)
    r = client.get("/api/ask/figure", params={**q, "hash": "sha256:not-a-hash"})
    assert r.status_code == 404 and "not a sha256" in r.json()["error"]
    # an unnamed type keeps main's words
    assert F.placed_not_displayable("", 12_000) == "an unnamed type, 11.7 KB: not a type this page shows"
    # not held: 404 before anything, for the picture and for the file
    fig.path_for(d, "year=2023", 2).unlink()
    for extra in ({}, {"download": 1}):
        assert client.get("/api/ask/figure", params={**q, **extra}).status_code == 404


def test_the_saved_pdf_draws_what_the_page_drew(copy, monkeypatch):
    """`ask_pdf._plate_bytes`: a TIFF plate goes into the PDF as this node's
    preview of it; one that cannot be previewed, or would not be shown, is
    listed with the reason and its bytes are never read."""
    from app import ask_pdf
    name, d, page, bomb, entries = copy
    data, media = ask_pdf._plate_bytes(name, "pubmed:10", sha(page))
    assert media == "image/png" and F.not_displayable(data, media) is None
    assert ask_pdf._plate_bytes(name, "pubmed:10", sha(CHART)) == (CHART, "image/png")
    shown = ask_pdf.plate_shown(ask_pdf.PdfPlate(kind="img", collection=name, uid="pubmed:10", hash=sha(page),
                                                 caption="ANNEX II"), [ask_pdf.MAX_PLATE_BYTES])
    assert shown["kind"] == "img" and shown["src"].startswith("data:image/png;base64,")
    shown = ask_pdf.plate_shown(ask_pdf.PdfPlate(kind="img", collection=name, uid="pubmed:10", hash=sha(bomb),
                                                 caption="A bomb"), [ask_pdf.MAX_PLATE_BYTES])
    assert shown["kind"] == "none" and "400-megapixel cap" in shown["note"]
    read = []
    monkeypatch.setattr(F, "placed_bytes", lambda *a: read.append(a) or b"")
    monkeypatch.setattr(F, "MAX_DISPLAY_BYTES", 100)
    shown = ask_pdf.plate_shown(ask_pdf.PdfPlate(kind="img", collection=name, uid="pubmed:10", hash=sha(CHART),
                                                 caption="A chart"), [ask_pdf.MAX_PLATE_BYTES])
    assert shown == {"kind": "none", "caption": "A chart",
                     "note": f"not displayable: image/png, {F._size(len(CHART))}: over the 16 MiB display cap"}
    assert read == []


def test_one_pdf_makes_previews_only_while_its_seconds_for_it_last(copy, monkeypatch):
    """THE REVIEW: a PDF may name 200 entries of 24 plates, and each TIFF
    among them became a child process with no total bound — up to four
    minutes each for a bad one — while every page view waited behind them.
    A kept preview costs a read; one not kept is made only while the PDF's
    clock lasts."""
    from app import ask_pdf
    name, d, page, bomb, entries = copy
    made = []
    real = preview._make
    monkeypatch.setattr(preview, "_make", lambda *a: made.append(a[2]) or real(*a))
    plate = ask_pdf.PdfPlate(kind="img", collection=name, uid="pubmed:10", hash=sha(page), caption="ANNEX II")
    spent = [0.0]
    shown = ask_pdf.plate_shown(plate, [ask_pdf.MAX_PLATE_BYTES], spent)
    assert shown == {"kind": "none", "caption": "ANNEX II",
                     "note": "not drawn: its preview is not made yet; it is on the page"} and made == []
    clock = [ask_pdf.RENDER_S]
    shown = ask_pdf.plate_shown(plate, [ask_pdf.MAX_PLATE_BYTES], clock)
    assert shown["kind"] == "img" and len(made) == 1 and 0 < clock[0] < ask_pdf.RENDER_S, "made, and its seconds counted"
    shown = ask_pdf.plate_shown(plate, [ask_pdf.MAX_PLATE_BYTES], [0.0])
    assert shown["kind"] == "img" and len(made) == 1, "kept: drawn with the clock spent, and nothing made"
    # the whole PDF sets one clock for all its plates
    html = ask_pdf.build_html(
        ask_pdf.AskPdf(question="q", answer="See [1].", references=[ask_pdf.PdfReference(
            n=1, title="A regulation", collection=name, passage="[Illustration: ANNEX II]", plates=[plate])]),
        base_url="https://ask.example.org", node="ask.example.org",
        when=__import__("datetime").datetime(2026, 11, 26, tzinfo=__import__("datetime").timezone.utc))
    assert "data:image/png;base64," in html


def test_the_reader_page_draws_the_preview_with_the_same_words():
    for page in ("_ask_results.html", "reader.html"):
        src = (Path(api.__file__).resolve().parents[1] / "templates" / page).read_text(encoding="utf-8")
        assert src.count("preview — the original ({{ f.size }})") == 1, page
        assert "{% if f.preview %}open the preview this node rendered{% else %}" in src, page


def test_the_decoder_is_declared_and_reads_the_codings_the_corpus_holds():
    """Pillow was importable in the image only because WeasyPrint brings it:
    a decoder this node runs over publisher-supplied bytes is DECLARED, and
    pinned as the image has it. Its TIFF plugin needs libtiff for the fax
    codings (run in the image by the deploy's lists: a build without it
    would refuse every page of an official journal)."""
    from PIL import features
    assert features.check("libtiff"), "this Pillow reads no CCITT TIFF"
    root = Path(api.__file__).resolve().parents[2]
    lines = lambda name: [ln.strip().lower() for ln in (root / name).read_text().splitlines()]  # noqa: E731
    declared = [ln for ln in lines("requirements.txt") if ln.startswith("pillow==")]
    assert len(declared) == 1 and declared[0] in lines("constraints.txt")
