"""Why the Data Dump's Formex units of a language are refused (München,
2026-11-16: hr refused 1,232 against 0–26 for every other language).

The unit builder (app/acquire/eurlex_dump.py) refuses a Formex unit whose
items hold no single wrapper, or whose wrapper's `<LG.DOC>` states another
language. This tallies every work of `LEG_<L>_FMX.zip` the same way — the
wrapper count and the language stated — with a few examples of each kind,
so the refusals are counted, not inferred. Read-only: nothing is built, and
only the wrappers are read.

Usage:
  python -m tools.measure.eurlex_formex_langs <dump dir> <lang> [--examples 5]
"""
from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path


def tally(dump: Path, lang: str, examples: int = 5) -> dict:
    """`{kinds: {"<n wrappers>/<stated>": count}, examples: {kind: [...]}}`
    over every work of the language's Formex zips."""
    from app.acquire.eurlex_dump import _LG_DOC
    from app.plugins.eurlex import formex
    zips = sorted((Path(dump) / f"datadump-{lang}").rglob(f"*_{lang.upper()}_FMX*.zip"))
    if not zips:
        raise SystemExit(f"{dump}/datadump-{lang}: no LEG_{lang.upper()}_FMX zip")
    works: dict[str, list[tuple[zipfile.ZipFile, zipfile.ZipInfo]]] = defaultdict(list)
    opened = [zipfile.ZipFile(p) for p in zips]
    for z in opened:
        for info in z.infolist():
            parts = info.filename.split("/")
            if len(parts) == 3 and parts[1] == "fmx4" and formex.is_wrapper(parts[2]):
                works[parts[0]].append((z, info))
            elif len(parts) == 3 and parts[1] == "fmx4":
                works.setdefault(parts[0], [])
    kinds: Counter = Counter()
    shown: dict[str, list] = defaultdict(list)
    for uuid, wrappers in works.items():
        stated = None
        if len(wrappers) == 1:
            z, info = wrappers[0]
            m = _LG_DOC.search(z.read(info))
            stated = m.group(1).decode().lower() if m else "none"
        kind = f"{min(len(wrappers), 2)}{'+' if len(wrappers) > 2 else ''}/{stated or '-'}"
        kinds[kind] += 1
        if kind != f"1/{lang}" and len(shown[kind]) < examples:
            shown[kind].append({"work": uuid, "wrappers": [i.filename.split("/")[2] for _z, i in wrappers]})
    for z in opened:
        z.close()
    return {"language": lang, "works": len(works), "kinds": dict(kinds.most_common()), "examples": shown}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dump")
    p.add_argument("lang")
    p.add_argument("--examples", type=int, default=5)
    a = p.parse_args(argv)
    print(json.dumps(tally(Path(a.dump), a.lang.lower(), a.examples), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
