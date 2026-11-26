"""A node-rendered PREVIEW of a plate the browser cannot show (app/ask/
preview.py; 08 §8.7 amended 2026-11-26; the operator, of EUR-Lex's pictures:
"we must show pictures in the results as it is done for gutenberg
collection", "Option A").

  - a TIFF plate is answered inline as the PNG this node renders from it, at
    most 1,600 px wide, its own bytes a download beside it;
  - THE HEADER FIRST: the declared pixels, the bytes they decode to and the
    compression are held to caps before any decoder is handed the file — a
    bomb is refused on what it declares;
  - the web never holds the plate's bytes: a child renders, the parent
    watches its memory and its seconds, one at a time;
  - a preview is kept (derived data) and a refusal remembered: neither costs
    a second attempt;
  - a large plate's download is streamed, hashed as it goes.
"""
from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import struct
import subprocess
import sys
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


def tiff(size=(2400, 3200), mode="1", compression="group4") -> bytes:
    """A real TIFF: a page with a few black bars, as an official journal's form."""
    im = Image.new(mode, size, 1 if mode == "1" else (255 if mode == "L" else (255, 255, 255)))
    for y in range(100, size[1], 400):
        im.paste(0 if mode in ("1", "L") else (0, 0, 0), (100, y, size[0] - 100, y + 12))
    out = io.BytesIO()
    im.save(out, "TIFF", compression=compression)
    return out.getvalue()


def header_only(width: int, height: int, *, bits=1, compression=1, magic=42) -> bytes:
    """A classic little-endian TIFF that DECLARES a size and holds no pixel."""
    entries = [(256, 4, 1, width), (257, 4, 1, height), (258, 3, 1, bits), (259, 3, 1, compression),
               (277, 3, 1, 1)]
    ifd = struct.pack("<H", len(entries)) + b"".join(
        struct.pack("<HHI", tag, kind, n) + (struct.pack("<I", v) if kind == 4 else struct.pack("<HH", v, 0))
        for tag, kind, n, v in entries) + struct.pack("<I", 0)
    return b"II" + struct.pack("<HI", magic, 8) + ifd


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


CHART = a_png(640, 480, b"a chart, " * 40)


# ---- the header, before any pixel ---------------------------------------------------

def test_the_header_says_what_the_image_declares_and_no_decoder_is_called():
    got = preview.tiff_header(io.BytesIO(tiff((2400, 3200), "1", "group4")))
    assert (got.width, got.height, got.bits_per_pixel, got.compression) == (2400, 3200, 1, 4)
    assert got.pixels == 7_680_000 and got.decoded_bytes == 7_680_000, "a 1-bit page decodes to a byte a pixel"
    rgb = preview.tiff_header(io.BytesIO(tiff((300, 200), "RGB", "tiff_lzw")))
    assert (rgb.bits_per_pixel, rgb.compression, rgb.decoded_bytes) == (24, 5, 300 * 200 * 3)
    big_endian = io.BytesIO()
    Image.new("L", (64, 48)).save(big_endian, "TIFF", compression=None)
    assert preview.tiff_header(io.BytesIO(big_endian.getvalue())).width == 64


@pytest.mark.parametrize("data, why", [
    (b"\x89PNG\r\n\x1a\n" + b"0" * 64, "not a TIFF"),
    (header_only(10, 10, magic=43), "BigTIFF"),
    (b"II" + struct.pack("<HI", 42, 9_999_999) + b"0" * 32, "outside the file"),
    (b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", 60_000) + b"0" * 64, "declares 60000 entries"),
    (b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", 1) + struct.pack("<HHII", 259, 3, 1, 1) + b"\0" * 4,
     "declares no dimensions"),
])
def test_what_is_not_a_readable_classic_tiff_is_refused_with_the_reason(data, why):
    with pytest.raises(preview.NotPreviewable, match=why):
        preview.tiff_header(io.BytesIO(data))


def test_a_bomb_is_refused_on_what_it_declares():
    """A few dozen bytes that declare 30,000 × 30,000: refused by the caps,
    which are read from the header — the decoder never sees it."""
    bomb = header_only(30_000, 30_000)
    head = preview.tiff_header(io.BytesIO(bomb))
    assert "over the 400-megapixel cap" in preview.refused(head, len(bomb))
    deep = preview.tiff_header(io.BytesIO(header_only(19_000, 19_000, bits=32)))
    assert deep.pixels < preview.MAX_SOURCE_PIXELS and "decodes to" in preview.refused(deep, 100)
    assert "over the 1,024 MiB cap" in preview.refused(preview.TiffHeader(10, 10, 1, 1), (1 << 30) + 1)
    for coding in (2, 6, 7, 8, 34712):          # modified Huffman, old JPEG, JPEG, Deflate, JPEG 2000
        why = preview.refused(preview.TiffHeader(10, 10, 8, coding), 100)
        assert f"compression {coding}" in why and "not a coding" in why
    # the corpus's own largest: three certificate forms of 15,794 × 20,561, 8-bit, uncompressed
    assert preview.refused(preview.TiffHeader(15_794, 20_561, 8, 1), 324_800_000) is None
    assert preview.refused(preview.TiffHeader(9_954, 18_942, 24, 1), 565_666_968) is None


# ---- the child ------------------------------------------------------------------------

def _container(tmp_path, plates: dict[str, bytes]):
    path = tmp_path / "figures-00001.zst"
    fig.write_container(path, plates)
    return path


@pytest.mark.parametrize("mode, compression", [("1", "group4"), ("1", "group3"), ("1", "packbits"),
                                               ("L", "tiff_lzw"), ("RGB", None)])
def test_the_child_renders_a_png_of_at_most_1600_px_from_each_coding_the_corpus_holds(
        tmp_path, mode, compression):
    plate = tiff((2400, 3200), mode, compression)
    path, dest = _container(tmp_path, {sha(plate): plate}), tmp_path / "out.png"
    code = preview_child.main([str(path), fig.bare(sha(plate)), str(dest), str(preview.WIDTH),
                               str(preview.MAX_HEIGHT)])
    assert code == 0 and not (tmp_path / "out.png.src").exists(), "the temporary copy is gone"
    data = dest.read_bytes()
    assert F.not_displayable(data, "image/png") is None, "the preview passes 08 §8.7's own display rule"
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (1600, 2133) and im.mode == ("RGB" if mode == "RGB" else "L")
        assert im.getpixel((800, 2)) != im.getpixel((800, 70)), "the page's bars are in it, antialiased"


def test_a_small_image_is_not_enlarged_and_a_very_tall_one_is_held_to_the_height(tmp_path):
    small, tall = tiff((300, 200), "L", "tiff_lzw"), tiff((400, 40_000), "1", "group4")
    path = _container(tmp_path, {sha(small): small, sha(tall): tall})
    for plate, size in ((small, (300, 200)), (tall, (64, 6400))):
        dest = tmp_path / f"{len(plate)}.png"
        assert preview_child.main([str(path), fig.bare(sha(plate)), str(dest), "1600", "6400"]) == 0
        with Image.open(dest) as im:
            assert im.size == size


def test_sixteen_bit_grey_is_reduced_by_its_high_byte_and_not_clipped_to_white(tmp_path):
    """Pillow's own I;16 → L clips at 255: a ramp came out at a mean of 254.5."""
    import numpy as np
    from PIL import ImageStat
    out = io.BytesIO()
    Image.fromarray(np.linspace(0, 65535, 400 * 300).reshape(300, 400).astype("<u2")).save(out, "TIFF")
    plate = out.getvalue()
    assert preview.tiff_header(io.BytesIO(plate)).bits_per_pixel == 16
    path, dest = _container(tmp_path, {sha(plate): plate}), tmp_path / "out.png"
    assert preview_child.main([str(path), fig.bare(sha(plate)), str(dest), "1600", "6400"]) == 0
    with Image.open(dest) as im:
        assert im.mode == "L" and 120 < ImageStat.Stat(im).mean[0] < 135


def test_the_child_refuses_before_decoding_and_says_why(tmp_path, capsys, monkeypatch):
    bomb, deflate = header_only(30_000, 30_000), tiff((64, 64), "L", "tiff_adobe_deflate")
    path = _container(tmp_path, {sha(bomb): bomb, sha(deflate): deflate})
    decoded = []
    monkeypatch.setattr(preview_child, "render", lambda *a, **k: decoded.append(a))
    for plate, why in ((bomb, "400-megapixel cap"), (deflate, "compression 8")):
        code = preview_child.main([str(path), fig.bare(sha(plate)), str(tmp_path / "x.png"), "1600", "6400"])
        assert code == preview.REFUSED and why in json.loads(capsys.readouterr().out)["refused"]
    assert decoded == [], "no decoder was handed either"


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
    assert not list(kept.parent.glob(".*")), "no temporary file left beside it"


def test_a_refusal_is_remembered_with_its_reason(copy, monkeypatch):
    name, d, page, bomb, entries = copy
    runs = []
    real = preview._render
    monkeypatch.setattr(preview, "_render", lambda *a: runs.append(a[1]) or real(*a))
    for _ in range(3):
        with pytest.raises(preview.NotPreviewable, match="400-megapixel cap"):
            preview.png(name, "pubmed:10", sha(bomb))
    assert len(runs) == 1, "one attempt, not one a view"
    _kept, no = preview._files(d, fig.bare(sha(bomb)))
    assert "400-megapixel" in json.loads(no.read_text())["why"]


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
    with pytest.raises(preview.NotPreviewable, match="not rendered within 0 s"):
        preview.png(name, "pubmed:10", sha(page))
    assert started[0].returncode == -9, "killed, and waited for"
    assert not [p for p in kept.parent.iterdir() if p.name.startswith(".")], "the child's half copy is gone too"
    said = json.loads(no.read_text())
    assert said["until"] == said["at"] + preview.RETRY_S, "the node's load, perhaps: remembered for an hour"
    with pytest.raises(preview.NotPreviewable, match="not rendered within"):
        preview.png(name, "pubmed:10", sha(page))
    assert len(started) == 1, "not tried again within the hour"
    # the hour over: tried again — and this time it is its memory
    no.write_text(json.dumps({**said, "until": said["at"] - 1}))
    monkeypatch.setattr(preview, "RENDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(preview, "_rss_bytes", lambda pid: 10 << 30)
    with pytest.raises(preview.NotPreviewable, match="held over 1,792 MiB"):
        preview.png(name, "pubmed:10", sha(page))
    assert len(started) == 2 and started[1].returncode == -9
    assert "until" not in json.loads(no.read_text()), "the file's own: remembered for good"


def test_a_child_killed_by_another_is_tried_again_within_the_hour_and_a_decoder_that_fails_is_not(
        copy, monkeypatch):
    name, d, page, bomb, entries = copy
    real = subprocess.Popen
    kept, no = preview._files(d, fig.bare(sha(page)))
    for script, words, lasting in (("import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
                                    "killed on it \\(signal 9\\)", False),
                                   ("raise SystemExit(1)", "the renderer failed on it \\(exit 1\\)", True)):
        monkeypatch.setattr(preview.subprocess, "Popen",
                            lambda cmd, script=script, **kw: real([sys.executable, "-c", script], **kw))
        with pytest.raises(preview.NotPreviewable, match=words):
            preview.png(name, "pubmed:10", sha(page))
        assert ("until" not in json.loads(no.read_text())) is lasting
        no.unlink()


def test_what_is_not_a_type_a_preview_is_rendered_for_is_not_attempted(copy):
    name, d, page, bomb, entries = copy
    assert preview.entry_previewable(entries[0])
    assert not preview.entry_previewable({"media_type": "image/svg+xml", "bytes": 10})
    assert not preview.entry_previewable({"media_type": "image/tiff", "bytes": preview.MAX_SOURCE_BYTES + 1})


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
    assert b"".join(got) == plate, "the pieces were sent; the transfer fails after the last"
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
