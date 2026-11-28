"""RAGE's plates (app/rage/figures.py; operator, 2026-11-28: "Why the fuck
this is not a fucking part of RAGE API???"): Gutenberg's proven rules, made
common — markers where the text places an image, the Nth marker figures[N],
every plate carried, hashes streamed, the container pass one plate at a time."""
import hashlib
import zipfile

import pytest

from app.hold import figures as figures_mod
from app.plugins.gutenberg import pg_epub
from app.rage import figures as plates


def _unit(tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x89PNG plate a")
    (tmp_path / "b.tif").write_bytes(b"II*\x00 plate b" * 1000)
    with zipfile.ZipFile(tmp_path / "pkg.zip", "w") as z:
        z.writestr("c.jpg", b"\xff\xd8 plate c")
        z.writestr("b.tif", b"II*\x00 plate b" * 1000)      # the same bytes as the loose b.tif
    return plates.Unit({n: tmp_path / n for n in ("a.png", "b.tif", "pkg.zip")})


S = plates.SENTINEL


def test_each_image_is_a_marker_where_it_stands_and_the_nth_marker_is_figures_n(tmp_path):
    with _unit(tmp_path) as unit:
        got = plates.place([("Art. 1", f"Before {S} between {S} after."), ("Annex", f"{S}")],
                           [plates.Ref("a.png", "Map"), plates.Ref("c.jpg"), plates.Ref("a.png", "Map")], unit)
    text = got.sections[0][1] + got.sections[1][1]
    assert text.count("[Illustration") == 3 and "[Illustration: Map]" in text
    assert [f.href for f in got.figures] == ["a.png", "c.jpg", "a.png"], "one entry per appearance"
    assert [f.seq for f in got.figures] == [0, 1, 2]
    assert got.figures[0].sha256 == hashlib.sha256(b"\x89PNG plate a").hexdigest()
    assert got.figures[1].bytes == len(b"\xff\xd8 plate c") and got.figures[1].media_type == "image/jpeg"
    assert all(f.carried for f in got.figures) and got.gaps == []


def test_the_marker_is_gutenbergs_byte_for_byte():
    assert plates.marker("A plan") == pg_epub._marker("A plan") and plates.marker("") == pg_epub._marker("")


def test_a_reference_the_unit_cannot_give_leaves_neither_marker_nor_entry_and_is_absent(tmp_path):
    with _unit(tmp_path) as unit:
        got = plates.place([("", f"x {S} y {S} z")], [plates.Ref("gone.png"), plates.Ref("a.png")], unit)
    assert got.sections[0][1].count("[Illustration") == 1 and [f.href for f in got.figures] == ["a.png"]
    assert got.figures[0].seq == 0, "the alignment holds after the missing one"
    assert got.gaps == [{"part": "gone.png", "why": "absent"}]


def test_an_image_no_text_places_is_no_figure_and_is_unplaced(tmp_path):
    """Operator, 2026-11-28: "if there is no mark there is no mark, consider the article degraded"."""
    with _unit(tmp_path) as unit:
        got = plates.place([("", f"{S}")], [plates.Ref("a.png")], unit, held_images=["a.png", "c.jpg"])
    assert [f.href for f in got.figures] == ["a.png"] and got.gaps == [{"part": "c.jpg", "why": "unplaced"}]


def test_the_places_and_the_refs_must_count_the_same(tmp_path):
    with _unit(tmp_path) as unit, pytest.raises(plates.PlatesError, match="1 image place"):
        plates.place([("", f"{S}")], [plates.Ref("a.png"), plates.Ref("c.jpg")], unit)


def test_a_member_named_as_another_item_must_be_the_same_bytes(tmp_path):
    (tmp_path / "x.png").write_bytes(b"one")
    with zipfile.ZipFile(tmp_path / "p.zip", "w") as z:
        z.writestr("x.png", b"other")
    with pytest.raises(plates.PlatesError, match="other bytes"):
        plates.Unit({"x.png": tmp_path / "x.png", "p.zip": tmp_path / "p.zip"})


def test_the_container_pass_spools_one_plate_at_a_time_and_writes_the_same_bytes(tmp_path):
    """`FrameSpool` writes exactly what `write_container` writes."""
    with _unit(tmp_path) as unit:
        wanted = {unit.sha256(n): n for n in ("a.png", "b.tif", "c.jpg")}
        pieces = plates.iter_figures(unit, wanted)
        assert not isinstance(pieces, dict), "one at a time"
        spool = figures_mod.FrameSpool(tmp_path / "spool")
        for h, data in pieces:
            spool.add(h, data)
            spool.add(h, data)                        # a hash given twice is stored once
        whole = figures_mod.write_container(tmp_path / "whole.fig.zst", plates.read_figures(unit, wanted))
    streamed = spool.write(tmp_path / "streamed.fig.zst")
    assert (tmp_path / "whole.fig.zst").read_bytes() == (tmp_path / "streamed.fig.zst").read_bytes()
    assert streamed.figures == whole.figures == 3 and streamed.sha256 == whole.sha256
    assert list((tmp_path / "spool").iterdir()) == [], "nothing left beside the container"


def test_the_spool_refuses_a_plate_that_is_not_its_name(tmp_path):
    spool = figures_mod.FrameSpool(tmp_path)
    with pytest.raises(figures_mod.FigureError, match="disagree"):
        spool.add("sha256:" + "0" * 64, b"not that")
    spool.close()


def test_the_container_pass_takes_a_plugins_plates_one_at_a_time(tmp_path):
    from app import plugin_io

    class OneAtATime:
        source_id = "x"
        def iter_figures(self, book_dir, native_id, hrefs):
            for h in hrefs:
                yield h, b"bytes of " + h.encode()

    class Whole:
        source_id = "y"
        def read_figures(self, book_dir, native_id, hrefs):
            return {h: b"whole " + h.encode() for h in hrefs}

    row = {"path": str(tmp_path), "native_id": 1}
    got = plugin_io.figure_items(OneAtATime(), row, {"h1": "a", "h2": "b"})
    assert not isinstance(got, dict) and dict(got) == {"h1": b"bytes of h1", "h2": b"bytes of h2"}
    assert dict(plugin_io.figure_items(Whole(), row, {"h": "a"})) == {"h": b"whole h"}
