"""A Formex package read by its PATH (2026-11-24, step 6 of EUR-Lex5.next's
restart): discovery's `wrapper_head` dated every unit by reading its package
whole, figures included (up to 815 MB a unit); the first walk of the corpus
ran past 27 minutes on the SSD the stages share. `formex.unpack` now takes a
package's path and reads only its central directory, its text members and its
figures' first bytes. Its answers are the same as from the package's bytes —
a unit's day makes its id, so a different answer would move documents."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from app.plugins.eurlex import formex
from app.plugins.eurlex.source import wrapper_head

CELEX = "32024R1689"
WRAPPER = ('<?xml version="1.0" encoding="UTF-8"?><DOC><BIB.DOC><NO.CELEX>32024R1689</NO.CELEX></BIB.DOC>'
           '<PUBLICATION.REF FILE="L_202401689EN.toc.xml"><COLL>L</COLL><DATE ISO="20240712">20240712</DATE>'
           '</PUBLICATION.REF><FMX><DOC.MAIN.PUB><REF.PHYS FILE="L_202401689EN.000101.fmx.xml"/>'
           '</DOC.MAIN.PUB></FMX></DOC>').encode()
BODY = b'<?xml version="1.0" encoding="UTF-8"?><ACT><TITLE><TI><P>Regulation</P></TI></TITLE></ACT>'


def _package(path: Path, members: dict[str, bytes], *, stored: bool = False) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def _both(files: dict[str, Path]):
    """unpack from the files' bytes (as before) and from the packages' paths:
    (answer or exception class, answer or exception class)."""
    def run(given):
        try:
            view, came = formex.unpack(given)
            return dict(view), dict(view.sizes), came
        except Exception as e:  # noqa: BLE001 — the class is the answer compared
            return type(e)
    as_bytes = {n: p.read_bytes() for n, p in files.items()}
    as_paths = {n: (p if formex.is_package(n) else p.read_bytes()) for n, p in files.items()}
    return run(as_bytes), run(as_paths)


def test_a_package_by_path_reads_the_same_members(tmp_path):
    pkg = _package(tmp_path / "OJABA_L_202401689_ENG.fmx4.zip",
                   {"L_202401689EN.000101.doc.xml": WRAPPER, "L_202401689EN.000101.fmx.xml": BODY,
                    "L_202401689EN.000102.tif": b"II*\x00" + b"\x00" * 200_000})
    old, new = _both({pkg.name: pkg})
    assert old == new and isinstance(old, tuple)
    view = old[0]
    assert view["L_202401689EN.000102.tif"] == (b"II*\x00" + b"\x00" * 200_000)[:formex._HEAD_BYTES]


@pytest.mark.parametrize("case", ["not-plain", "over-max", "bad-crc", "not-a-zip", "truncated", "collides"])
def test_every_refusal_is_the_same_by_path(tmp_path, monkeypatch, case):
    name = "OJABA_L_202401689_ENG.fmx4.zip"
    files: dict[str, Path] = {}
    if case == "not-plain":
        pkg = _package(tmp_path / name, {"sub/L_202401689EN.000101.doc.xml": WRAPPER})
    elif case == "over-max":
        monkeypatch.setattr(formex, "PACKAGE_MAX", 100)
        pkg = _package(tmp_path / name, {"L_202401689EN.000101.doc.xml": WRAPPER})
    elif case == "bad-crc":
        pkg = _package(tmp_path / name, {"L_202401689EN.000101.doc.xml": WRAPPER}, stored=True)
        raw = bytearray(pkg.read_bytes())
        at = raw.index(b"<NO.CELEX>")
        raw[at + 1] ^= 0x20                          # one byte of the stored member flipped
        pkg.write_bytes(bytes(raw))
    elif case == "not-a-zip":
        pkg = tmp_path / name
        pkg.write_bytes(b"<html>not a zip</html>")
    elif case == "truncated":
        pkg = _package(tmp_path / name, {"L_202401689EN.000101.doc.xml": WRAPPER})
        pkg.write_bytes(pkg.read_bytes()[:-30])
    else:
        pkg = _package(tmp_path / name, {"L_202401689EN.000101.doc.xml": WRAPPER})
        loose = tmp_path / "L_202401689EN.000101.doc.xml"
        loose.write_bytes(b"<DOC>another file of that name</DOC>")
        files[loose.name] = loose
    files[pkg.name] = pkg
    old, new = _both(files)
    assert old == new, (case, old, new)
    if case == "not-a-zip":
        assert isinstance(old, tuple), "a file named .zip that is no zip is read as the file it is"
    else:
        assert old is formex.FormexError, (case, old)


def test_a_package_that_does_not_read_is_an_oserror_as_before(tmp_path):
    gone = tmp_path / "OJABA_L_202401689_ENG.fmx4.zip"
    gone.mkdir()                                     # a directory: reading it fails, as read_bytes did
    with pytest.raises(OSError):
        formex.unpack({gone.name: gone})


def test_wrapper_head_never_reads_a_package_whole(tmp_path, monkeypatch):
    unit = tmp_path / "lang=en" / "2024" / CELEX
    unit.mkdir(parents=True)
    _package(unit / "OJABA_L_202401689_ENG.fmx4.zip",
             {"L_202401689EN.000101.doc.xml": WRAPPER, "L_202401689EN.000101.fmx.xml": BODY,
              "L_202401689EN.000102.tif": os.urandom(2_000_000)}, stored=True)
    (unit / "tree.json").write_text("{}")
    entries = sorted(os.scandir(unit), key=lambda e: e.name)
    want = formex.unpack({e.name: Path(e.path).read_bytes() for e in entries if formex.is_package(e.name)})[0]
    want_head = want["L_202401689EN.000101.doc.xml"][:16 << 10]
    real = Path.read_bytes

    def guarded(self):
        if self.suffix == ".zip":
            pytest.fail(f"{self.name} read whole")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", guarded)
    assert wrapper_head(CELEX, entries) == want_head
