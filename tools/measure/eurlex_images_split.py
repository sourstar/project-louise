"""EUR-Lex's images by what the figures half would do with them, READ-ONLY
(architecture, 2026-11-28, before the storage question goes to the operator).

Each image of each unit (loose, or a package member read from its zip
without unpacking) falls in ONE row, by the parser's own reading
(app/plugins/eurlex/source.py `_orphan_images`, `_FILEREF`, `_IMG_SRC`,
`_form_held`):

  placed     its name is referenced by a text of the unit: a Formex
             INCL.ELEMENT FILEREF, or an html/xhtml <img src>;
  orphan     referenced by no text, and no other form the tree lists
             offers it: what `_orphan_images` returns (Q1 a: appended);
  pdf-scan   referenced by no text, the form read is a PDF the unit holds
             (Q2: the page scans beside it);
  other-form referenced by no text, and the tree lists a manifestation
             other than the one read (a TIFF manifestation of the pages,
             say): `_orphan_images` says it is offered there.

    python -m tools.measure.eurlex_images_split /build/origin-EUR-Lex4 [--workers 8]

Prints one line per (slice, language, row): count, bytes; then the totals.
Writes nothing.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROWS = ("placed", "orphan", "pdf-scan", "other-form")


def _read_view(unit: Path):
    from app.plugins.eurlex import formex
    from app.plugins.eurlex import rdf
    from app.plugins.eurlex import source as src
    from app.plugins.eurlex.source import NOTICE, unit_files
    files, sizes = {}, {}
    for e in unit_files(unit):
        if e.name in (rdf.TREE_FILE, NOTICE):
            continue
        if formex.is_package(e.name):
            files[e.name] = Path(e.path)
        elif formex.EXTENSION_KIND.get(os.path.splitext(e.name)[1].lower()) == "markup":
            files[e.name] = Path(e.path).read_bytes()      # its references read, as the parser reads them
        else:
            with open(e.path, "rb") as f:                  # an image or a PDF: what its kind needs
                files[e.name] = f.read(max(formex._HEAD_BYTES, src.PDF_HEADER_WINDOW))
            sizes[e.name] = e.stat().st_size
    view, packed = formex.unpack(files)
    return files, view, packed, sizes


def unit_rows(unit: Path, lang: str) -> Counter:
    """(row, "n"|"bytes", "loose"|"zip") → value, for one unit."""
    from app.plugins.eurlex import formex, from_tree, rdf
    from app.plugins.eurlex import source as src
    out: Counter = Counter()
    try:
        note = from_tree.notice(rdf.loads((unit / rdf.TREE_FILE).read_bytes()), src.LANGUAGES[lang], unit.name)
        files, view, packed, sizes = _read_view(unit)
    except Exception as e:                           # noqa: BLE001 — counted, never fatal
        out[("unreadable", "n", type(e).__name__)] += 1
        return out
    images = [n for n, b in view.items() if src._image_kind(n, b)]
    if not images:
        return out
    form = "fmx4" if any(formex.is_wrapper(n) for n in view) else src._form_held(note, files)
    named: set[str] = set()
    for n, b in view.items():
        if formex.EXTENSION_KIND.get(os.path.splitext(n)[1].lower()) != "markup" or formex.is_wrapper(n):
            continue
        for m in src._FILEREF.finditer(b):
            named.add(os.path.basename(m.group(1).decode("utf-8", "replace")))
        for m in src._IMG_SRC.finditer(b):
            named.add(os.path.basename(m.group(1).decode("utf-8", "replace")))
    orphans = set(src._orphan_images(view, form, note))
    other_forms = any(items and f != form for f, items in (note.items or {}).items())
    for n in images:
        if n in named:
            row = "placed"
        elif n in orphans:
            row = "orphan"
        elif other_forms:
            row = "other-form"
        else:
            row = "pdf-scan"
        where = "zip" if n in packed else "loose"
        size = view.sizes.get(n) if n in packed else sizes.get(n, len(view[n]))
        out[(row, "n", where)] += 1
        out[(row, "bytes", where)] += int(size or 0)
    return out


def _task(args: tuple[str, str, str]) -> tuple[str, str, Counter]:
    path, slice_, lang = args
    return slice_, lang, unit_rows(Path(path), lang)


def _units(root: Path):
    from app.plugins.eurlex.source import HISTORY
    for base, slice_ in ((root, "base"), (root / HISTORY, "history")):
        if not base.is_dir():
            continue
        for ld in sorted(base.glob("lang=*")):
            lang = ld.name.partition("=")[2]
            for year in sorted(p for p in ld.iterdir() if p.is_dir()):
                for unit in sorted(p for p in year.iterdir() if p.is_dir()):
                    yield str(unit), slice_, lang


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    a = ap.parse_args(argv)
    by: dict[tuple[str, str], Counter] = {}
    total: Counter = Counter()
    with ProcessPoolExecutor(a.workers) as pool:
        for slice_, lang, got in pool.map(_task, _units(a.corpus), chunksize=64):
            by.setdefault((slice_, lang), Counter()).update(got)
            total.update(got)

    def line(label: str, c: Counter) -> None:
        cells = []
        for row in ROWS:
            n = c[(row, "n", "loose")] + c[(row, "n", "zip")]
            b = c[(row, "bytes", "loose")] + c[(row, "bytes", "zip")]
            cells.append(f"{row} {n:,} ({b / 1e9:,.1f} GB; zip {c[(row, 'bytes', 'zip')] / 1e9:,.1f})")
        bad = sum(v for k, v in c.items() if k[0] == "unreadable")
        print(f"{label:<14} " + " | ".join(cells) + (f" | unreadable units {bad:,}" if bad else ""))
    for (slice_, lang), c in sorted(by.items()):
        line(f"{slice_} {lang}", c)
    line("TOTAL", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
