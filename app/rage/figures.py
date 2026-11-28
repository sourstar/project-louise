"""RAGE'S PLATES: what every collection does with the images of its origin
(operator, 2026-11-28, on EUR-Lex's figures being written in its plugin:
"Why the fuck this is not a fucking part of RAGE API???").

The rules are Gutenberg's, proven on ~700,000 plates
(app/plugins/gutenberg/pg_epub.py; 08 §8.7 "Every plate", 2026-10-25;
09 §9.5), made common:

  * an image stands in the text as a MARKER where the text places it —
    `[Illustration: <caption>]`, the convention Gutenberg's own plain-text
    edition uses, the caption staying in `sections` and indexed;
  * one `figures` entry per APPEARANCE, in reading order, so THE Nth MARKER
    IS `figures[N]` — by construction: the marker and the entry are made in
    one step from one list, never matched up afterwards;
  * every placed image is CARRIED (`carried = True`), whatever its type or
    size: the closed list and the caps bind display, not storage;
  * a reference the unit cannot give (no such file) leaves NEITHER a marker
    nor an entry, and is said: `origin_gaps` "absent" (09 §9.5, record line 3);
  * an image the unit holds that no text places is no figure — "if there is
    no mark there is no mark, consider the article degraded" (operator,
    2026-11-28): `origin_gaps` "unplaced".

What a PLUGIN does: walk its origin's text in reading order, write
`SENTINEL` where an image stands, and hand one `Ref` per sentinel — the
image's file name in the unit and its caption. Which files are images, and
which are not plates (a PDF's page scans), is the plugin's knowledge of its
origin. Everything else is here: the markers, the alignment, the hashes
(SHA-256 over the full bytes, streamed — never a plate held whole to hash
it), the sizes (a zip member's from its central directory), and reading the
plates back for the container pass, one at a time (`iter_figures`;
`hold.figures.FrameSpool` spools them in hash order).

A unit is a directory's files and the members of its zip packages
(PKWARE APPNOTE, Python's own `zipfile`, each member's CRC-32 checked on
read). A member name that another item of the unit also holds is refused:
which bytes a name means must be the origin's, never the order we read in.
"""
from __future__ import annotations

import hashlib
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterable, Iterator, Mapping

from app.plugins.base import Figure

#: what a plugin writes in a section's text where an image stands
SENTINEL = "\x1fLOUISE-PLATE\x1f"
#: a plate is hashed and read in pieces of this size
CHUNK = 1 << 20
#: by extension, as Gutenberg's walk names them (pg_epub `_IMAGE_TYPES`)
MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    ".tif": "image/tiff", ".tiff": "image/tiff", ".bmp": "image/bmp",
}


class PlatesError(ValueError):
    """A unit or a call RAGE's plates cannot honour; the message says why."""


@dataclass(frozen=True)
class Ref:
    """One appearance of an image, as the plugin found it in reading order."""
    name: str           # the image's file name in the unit (a loose file or a package's member)
    caption: str = ""   # the alt text, the title — "" for none


@dataclass(frozen=True)
class Placed:
    sections: list[tuple[str, str]]
    figures: list[Figure]
    gaps: list[dict]


def marker(caption: str) -> str:
    """What an image leaves in the text — Gutenberg's, byte for byte."""
    return f"\n\n[Illustration: {caption}]\n\n" if caption else "\n\n[Illustration]\n\n"


def media_type(name: str) -> str:
    return MEDIA_TYPES.get(posixpath.splitext(name)[1].lower(), "application/octet-stream")


def _is_zip(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"PK\x03\x04"
    except OSError:
        return False


class Unit:
    """A unit's items, `{name: Path}`, and the members of the zip packages
    among them, read without unpacking: one open per package, kept until
    `close` (a context manager)."""

    def __init__(self, files: Mapping[str, Path | str]) -> None:
        self._loose: dict[str, Path] = {}
        self._members: dict[str, tuple[Path, zipfile.ZipInfo]] = {}
        self._zips: dict[Path, zipfile.ZipFile] = {}
        self._sha: dict[str, str] = {}
        for name, path in files.items():
            self._loose[name] = Path(path)
        for name, path in self._loose.items():
            if not _is_zip(path):
                continue
            for info in self._zip(path).infolist():
                if info.is_dir():
                    continue
                if not self._same(info.filename, info):
                    raise PlatesError(f"{info.filename!r} is a member of {name} and another item of the "
                                      f"unit with other bytes: which it names is not the origin's to say")
                self._members.setdefault(info.filename, (path, info))

    def _same(self, name: str, info: zipfile.ZipInfo) -> bool:
        """Is a member named as an item already held the same bytes (its size
        and CRC-32)? Two names for one file are the origin's own way of
        serving it twice (as `formex.unpack` takes them); other bytes are not."""
        held = self._members.get(name)
        if held is not None:
            return (held[1].file_size, held[1].CRC) == (info.file_size, info.CRC)
        if name not in self._loose:
            return True
        import zlib
        crc = 0
        with open(self._loose[name], "rb") as f:
            while piece := f.read(CHUNK):
                crc = zlib.crc32(piece, crc)
        return (self._loose[name].stat().st_size, crc) == (info.file_size, info.CRC)

    def _zip(self, path: Path) -> zipfile.ZipFile:
        z = self._zips.get(path)
        if z is None:
            z = self._zips[path] = zipfile.ZipFile(path)
        return z

    def __enter__(self) -> "Unit":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for z in self._zips.values():
            z.close()
        self._zips.clear()

    def names(self) -> set[str]:
        return set(self._loose) | set(self._members)

    def __contains__(self, name: str) -> bool:
        return name in self._loose or name in self._members

    def size(self, name: str) -> int:
        if name in self._loose:
            return self._loose[name].stat().st_size
        if name in self._members:
            return self._members[name][1].file_size
        raise PlatesError(f"the unit holds no {name!r}")

    def open(self, name: str) -> IO[bytes]:
        """The item's bytes as a stream: a loose file as itself, a member read
        through its zip (its CRC-32 checked at the end)."""
        if name in self._loose:
            return open(self._loose[name], "rb")
        if name in self._members:
            path, info = self._members[name]
            return self._zip(path).open(info)
        raise PlatesError(f"the unit holds no {name!r}")

    def sha256(self, name: str) -> str:
        """SHA-256 over the item's full bytes, streamed; once per name."""
        got = self._sha.get(name)
        if got is None:
            h = hashlib.sha256()
            with self.open(name) as f:
                while piece := f.read(CHUNK):
                    h.update(piece)
            got = self._sha[name] = h.hexdigest()
        return got

    def read(self, name: str) -> bytes:
        with self.open(name) as f:
            return f.read()


def place(sections: Iterable[tuple[str, str]], refs: list[Ref], unit: Unit, *,
          held_images: Iterable[str] | None = None) -> Placed:
    """The text with its markers, its figures and the gaps, from the plugin's
    SENTINELs and `refs` (one per sentinel, the same order). `held_images`:
    the unit's images (by name) the plugin knows to be plates or orphans —
    each one no ref names is an "unplaced" gap. Raises PlatesError where the
    sentinels and the refs do not count the same: the alignment is never
    guessed."""
    sections = [(h, t) for h, t in sections]
    found = sum(t.count(SENTINEL) for _h, t in sections)
    if found != len(refs):
        raise PlatesError(f"{found} image place(s) in the text and {len(refs)} reference(s): each "
                          f"SENTINEL is one Ref, in reading order")
    figures: list[Figure] = []
    gaps: dict[str, str] = {}
    out: list[tuple[str, str]] = []
    it = iter(refs)
    for heading, text in sections:
        pieces = text.split(SENTINEL)
        built = [pieces[0]]
        for tail in pieces[1:]:
            ref = next(it)
            if ref.name in unit:
                figures.append(Figure(href=ref.name, media_type=media_type(ref.name),
                                      sha256=unit.sha256(ref.name), bytes=unit.size(ref.name),
                                      caption=ref.caption, seq=len(figures), carried=True))
                built.append(marker(ref.caption))
            else:
                gaps.setdefault(ref.name, "absent")    # neither marker nor entry: the alignment holds
            built.append(tail)
        out.append((heading, "".join(built)))
    named = {r.name for r in refs}
    for name in held_images or ():
        if name not in named:
            gaps.setdefault(name, "unplaced")
    return Placed(out, figures, [{"part": p, "why": gaps[p]}
                                 for p in sorted(gaps, key=lambda s: s.encode("utf-8"))])


def iter_figures(unit: Unit, hrefs: Mapping[str, str]) -> Iterator[tuple[str, bytes]]:
    """`(hash, bytes)` for each `{hash: name}`, one plate at a time — the
    container pass (`plugin_io.figure_items`). Each is checked against its
    hash by the spool before it is stored."""
    for h, name in hrefs.items():
        yield h, unit.read(name)


def read_figures(unit: Unit, hrefs: Mapping[str, str]) -> dict[str, bytes]:
    """`iter_figures`, all at once — for a record whose plates are small."""
    return dict(iter_figures(unit, hrefs))
