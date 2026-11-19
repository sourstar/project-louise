"""What the held EUR-Lex units are, counted against what their names and
trees say (2026-11-19; read-only, nothing written).

Two findings from the node prompted it:
  * 32015D1735 (19 languages): the Data Dump's member
    `L_2015252xx.01004901.xml` is a TIFF and the body's XML is filed as
    `…01005202.tif` — the dump pairs contents and names wrongly, and only the
    units whose `.xml` turned out not to be XML errored. A swap that
    leaves an XML under an XML name, or under a `.tif` name, errors nowhere.
  * a Formex manifestation Cellar serves as ONE zip (32024R1689) was read
    from XHTML (`cellar.form_of`), so the unit holds no Formex.

Per unit, on the parse pool's processes (`parallel.each_part`):
  1. every file's first bytes against its extension (`.xml` / `.html` must
     open with markup, `.tif` with a TIFF header, `.pdf` with `%PDF`, …):
     each (extension → what it holds) pair that disagrees, counted; and each
     pair of OJ Formex files of one issue whose own BIB.INSTANCE pages run
     against the order of their names (`formex.swapped`: 32015R0207 de, the
     annex's content under the body's name; a Special Edition's numbering
     keeps the order);
  2. in a Formex unit, the root of each file the act's own wrapper names
     (`DOC.MAIN.PUB` the body: ACT, CONS.ACT, AGR, CORR, GENERAL …;
     `DOC.SUB.PUB` an annex: ANNEX) — a body filed under an annex's name,
     or the reverse, is the XML-to-XML swap; plus a named file that is not
     XML;
  3. whether the held tree lists a Formex package the unit does not hold
     (`cellar.lacks_package`): the units the dump build re-forms.

Usage:
  python -m tools.measure.eurlex_units_check <corpus> [--lang it,fr] [--every N] [--examples 5]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: a file's first bytes: what it is
_KINDS = ((b"II*\x00", "tiff"), (b"MM\x00*", "tiff"), (b"%PDF", "pdf"), (b"\xff\xd8\xff", "jpeg"),
          (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF8", "gif"), (b"PK\x03\x04", "zip"))
#: what a file of that extension must hold
EXPECT = {".xml": "markup", ".html": "markup", ".htm": "markup", ".xhtml": "markup", ".tif": "tiff",
          ".tiff": "tiff", ".pdf": "pdf", ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif",
          ".zip": "zip"}
_ROOT_TAG = re.compile(rb"<([A-Za-z][A-Za-z0-9._-]*)[\s>/]")
_HEAD = 4096
ACT_ROOTS = {"ACT", "CONS.ACT", "AGR", "CORR", "GENERAL", "CONS.ANNEX"}


def kind(head: bytes) -> str:
    if not head:
        return "empty"
    for magic, name in _KINDS:
        if head.startswith(magic):
            return name
    if head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return "markup"
    return "other"


def root_tag(head: bytes) -> str | None:
    """The first element's name after the declaration, PIs and comments."""
    text = re.sub(rb"<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>", b"", head, flags=re.S)
    m = _ROOT_TAG.search(text)
    return m.group(1).decode() if m else None


def units(corpus: Path, langs: set[str] | None) -> list[tuple[str, str, str]]:
    """(lang, year folder, unit path) of every unit, base and history slice."""
    out = []
    for base in (corpus, corpus / "history"):
        if not base.is_dir():
            continue
        for ld in sorted(base.iterdir()):
            m = re.match(r"^lang=([a-z]{2})$", ld.name)
            if not m or (langs and m[1] not in langs) or not ld.is_dir():
                continue
            for yd in sorted(p for p in ld.iterdir() if p.is_dir()):
                for ud in sorted(p for p in yd.iterdir() if p.is_dir() and not p.name.startswith(".")):
                    out.append((m[1], yd.name, str(ud)))
    return out


def check_chunk(chunk: list[tuple[str, str, str]], examples: int) -> dict:
    """The counts for one chunk of units (a child's task)."""
    from app.acquire.cellar import lacks_package
    from app.plugins.eurlex import formex, from_tree, rdf
    from app.plugins.eurlex import source as src
    c: Counter = Counter()
    mism: Counter = Counter()
    roots: Counter = Counter()
    lacked: Counter = Counter()
    shown: dict[str, list[str]] = defaultdict(list)
    included: list[str] = []

    def show(key: str, what: str) -> None:
        if len(shown[key]) < examples:
            shown[key].append(what)
    for lang, year, path in chunk:
        unit = Path(path)
        c["units"] += 1
        try:
            entries = src.unit_files(unit)
        except OSError:
            c["unreadable"] += 1
            continue
        heads: dict[str, bytes] = {}
        wrong = False
        for e in entries:
            if e.name in (src.NOTICE, rdf.TREE_FILE):
                continue
            with open(e.path, "rb") as f:
                heads[e.name] = f.read(_HEAD)
            wrong = wrong or formex.misnamed(e.name, heads[e.name]) is not None
            ext = os.path.splitext(e.name)[1].lower()
            want, got = EXPECT.get(ext), kind(heads[e.name])
            if want is not None and got != want:
                mism[f"{ext} holds {got}"] += 1
                show(f"{ext} holds {got}", f"{unit.parent.parent.name}/{year}/{unit.name}/{e.name}")
        if any(EXPECT.get(os.path.splitext(n)[1].lower()) not in (None, kind(h)) for n, h in heads.items()):
            c["units-with-a-misnamed-file"] += 1
        places = {n: formex.stated_place(n, h) for n, h in heads.items()}
        off = formex.swapped(places)
        if off:
            mism["formex file in another's place"] += len(off)
            wrong = True
            show("in another's place", f"{unit.parent.parent.name}/{year}/{unit.name}: "
                 + ", ".join(f"{n} states p{places[n][3]} s{places[n][4]}" for n in off[:4]))
        if wrong:
            c["units-with-a-file-not-its-name"] += 1
            c[f"not-its-name {lang}"] += 1
        # the act's own wrapper: what its named files are
        try:
            files = {n: (unit / n).read_bytes() for n in heads if formex.is_wrapper(n)}
            if files:
                _wname, wrapper = formex.wrapper_of(files, unit.name)
                c["formex-units"] += 1
                # an XML item the wrapper never names (but its toc): an
                # INCLUDED document (formex._include; 32021R1971's 39 MB annex)
                named = {_wname, *formex.main_names(wrapper), *formex.annex_names(wrapper)}
                unnamed = [n for n in heads if n.lower().endswith(".xml") and n not in named
                           and not n.lower().endswith(".toc.xml") and not formex.is_wrapper(n)]
                if unnamed:
                    c["units-with-an-included-document"] += 1
                    included.append(path)
                    show("included document", f"{unit.parent.parent.name}/{year}/{unit.name}: {unnamed[0]}")
                for n in formex.main_names(wrapper):
                    tag = root_tag(heads[n]) if n in heads else "not-held"
                    roots[f"main {tag if tag in ACT_ROOTS or tag in ('not-held', 'ANNEX') else 'other'}"] += 1
                    if tag == "ANNEX" or (n in heads and kind(heads[n]) != "markup"):
                        show(f"main is {tag if tag else kind(heads[n])}", f"{unit.name} {lang} {n}")
                for n in formex.annex_names(wrapper):
                    tag = root_tag(heads[n]) if n in heads else "not-held"
                    roots[f"annex {tag if tag in ('ANNEX', 'not-held') or tag in ACT_ROOTS else 'other'}"] += 1
                    if tag in ACT_ROOTS or (n in heads and kind(heads[n]) != "markup"):
                        show(f"annex is {tag if tag else kind(heads[n])}", f"{unit.name} {lang} {n}")
        except (OSError, formex.FormexError):
            c["formex-unreadable-wrapper"] += 1
        # a Formex package the held tree lists and the unit lacks
        try:
            note = from_tree.notice(rdf.loads((unit / rdf.TREE_FILE).read_bytes()), src.LANGUAGES[lang], unit.name)
        except (OSError, ValueError, KeyError, from_tree.TreeError):
            c["tree-unreadable"] += 1
            continue
        if lacks_package(unit, note):
            lacked[f"{lang} {year}"] += 1
            show("package lacked", f"{unit.parent.parent.name}/{year}/{unit.name}")
    return {"counts": c, "misnamed": mism, "roots": roots, "package_lacked": lacked, "examples": dict(shown),
            "included": included}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("corpus")
    p.add_argument("--lang", default=None, help="comma-separated languages (default: every one held)")
    p.add_argument("--every", type=int, default=1, help="one unit in N (a sample)")
    p.add_argument("--examples", type=int, default=5)
    p.add_argument("--chunk", type=int, default=2000)
    p.add_argument("--list-included", default=None, metavar="FILE",
                   help="write the unit paths holding an included Formex document, one a line")
    a = p.parse_args(argv)
    from app import parallel
    langs = set(a.lang.split(",")) if a.lang else None
    todo = units(Path(a.corpus), langs)[::max(1, a.every)]
    tasks = [(todo[i:i + a.chunk], a.examples) for i in range(0, len(todo), a.chunk)]
    total = {"counts": Counter(), "misnamed": Counter(), "roots": Counter(), "package_lacked": Counter()}
    shown: dict[str, list[str]] = defaultdict(list)
    included: list[str] = []
    for got in parallel.each_part(check_chunk, tasks):
        for k in total:
            total[k].update(got[k])
        included += got["included"]
        for k, v in got["examples"].items():
            shown[k].extend(v[:max(0, a.examples - len(shown[k]))])
    by_lang: Counter = Counter()
    for k, v in total["package_lacked"].items():
        by_lang[k.split()[0]] += v
    out = {"corpus": a.corpus, "every": a.every, **{k: dict(v.most_common()) for k, v in total.items()},
           "package_lacked_by_language": dict(by_lang.most_common()), "examples": dict(shown)}
    if a.list_included:
        Path(a.list_included).write_text("".join(f"{u}\n" for u in included), encoding="utf-8")
    print(json.dumps(out, indent=1, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
