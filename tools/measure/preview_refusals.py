"""How many of a corpus's TIFF plates the preview renderer would refuse
before decoding, and why — counted with the renderer's OWN reader
(`app.ask.preview.tiff_header` and `refused`), no pixel decoded, nothing
written.

    python -m tools.measure.preview_refusals ROOT [ROOT …] [--workers N] [--out FILE]

    ROOT: a folder (walked: every file whose first bytes are a TIFF's, and
    every zip, whose TIFF members are read where they lie), or one file.

WHY (the second independent review of the renderer, 2026-11-26): the caps
were set from München's header count of EUR-Lex's 335,166 images — and the
rules added AFTER it (ascending tags, no tag twice, a tag's count and type,
16 MiB of tag values, 65,535 a side, no more strips than rows, a per-kind
model of what rendering holds) were never counted against that corpus.
measuring.md §6: counted, not inferred. A rule that refuses an honest plate
of an official journal is a defect of the rule.

WHAT IT GIVES: files seen, TIFFs read, how many would be rendered, and the
refusals by rule (the reason's first words) with up to twenty examples
each (where, and the whole reason); the largest model of what rendering
would hold among those admitted. One JSON object.

Read-only, and every core the node profile allows (`--workers`, default the
CPUs this process may use): one process a file.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Iterator

#: a refusal's rule: its words before the first figure or file-specific value
_RULES = (
    ("not in ascending tag order", "a tag twice or out of order"),
    ("is of a type or count", "a tag's count or type"),
    ("field type", "a field type TIFF 6.0 does not define"),
    ("point at over", "over 16 MiB of tag values"),
    ("points outside the file", "a tag's values outside the file"),
    ("more strips than", "more strips than rows"),
    ("a side over", "a side over 65,535"),
    ("a tile no image", "a tile no image is cut into"),
    ("would hold", "what rendering it would hold, over the cap"),
    ("megapixel cap", "over 400 MP declared"),
    ("MiB cap a preview", "over 1 GiB of file"),
    ("not a coding", "a coding outside the list"),
    ("BigTIFF", "a BigTIFF"),
    ("declares no dimensions", "no dimensions"),
    ("sample(s) a pixel", "samples and bit depths that disagree"),
    ("one side of a tile", "one side of a tile"),
    ("not a TIFF", "not a TIFF"),
    ("image directory", "its image directory"),
)
EXAMPLES = 20


def rule_of(why: str) -> str:
    for words, rule in _RULES:
        if words in why:
            return rule
    return re.sub(r"\d[\d,.×]*", "N", why)[:80]


def _is_tiff(head: bytes) -> bool:
    return head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def _judge(f, size: int) -> tuple[str | None, int | None]:
    """(the refusal, None) or (None, the bytes the model says it would hold)."""
    from app.ask import preview
    try:
        head = preview.tiff_header(f)
    except preview.NotPreviewable as e:
        return f"image/tiff: {e}", None
    why = preview.refused(head, size)
    return (why, None) if why else (None, head.held_bytes)


def _one(path: str) -> list[tuple[str, str | None, int | None]]:
    """[(where, refusal or None, held bytes or None)] for every TIFF in the file."""
    out: list[tuple[str, str | None, int | None]] = []
    p = Path(path)
    try:
        with open(p, "rb") as f:
            head = f.read(4)
        if _is_tiff(head):
            with open(p, "rb") as f:
                out.append((str(p), *_judge(f, p.stat().st_size)))
            return out
        if head[:2] != b"PK":
            return out
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                with z.open(info) as m:
                    if not _is_tiff(m.read(4)):
                        continue
                # a stored member is read where it lies; a deflated one through
                # zipfile's own seek, which decompresses up to the offset asked
                with z.open(info) as m:
                    out.append((f"{p}!{info.filename}", *_judge(m, info.file_size)))
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as e:
        out.append((str(p), f"unreadable here: {type(e).__name__}: {e}", None))
    return out


def files(roots: list[str]) -> Iterator[str]:
    for root in roots:
        r = Path(root)
        if r.is_file():
            yield str(r)
            continue
        for dirpath, dirnames, names in os.walk(r):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for n in sorted(names):
                yield os.path.join(dirpath, n)


def measure(roots: list[str], *, workers: int | None = None) -> dict[str, Any]:
    from app import config
    workers = workers or max(1, int(getattr(config, "available_cpus", lambda: os.cpu_count() or 1)()))
    seen, tiffs, admitted, largest = 0, 0, 0, (0, "")
    rules: Counter = Counter()
    examples: dict[str, list[dict[str, str]]] = {}
    unreadable = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for got in pool.map(_one, files(roots), chunksize=64):
            seen += 1
            for where, why, held in got:
                if why is not None and why.startswith("unreadable here"):
                    unreadable += 1
                    examples.setdefault("unreadable here", [])
                    if len(examples["unreadable here"]) < EXAMPLES:
                        examples["unreadable here"].append({"where": where, "why": why})
                    continue
                tiffs += 1
                if why is None:
                    admitted += 1
                    if held is not None and held > largest[0]:
                        largest = (held, where)
                    continue
                rule = rule_of(why)
                rules[rule] += 1
                ex = examples.setdefault(rule, [])
                if len(ex) < EXAMPLES:
                    ex.append({"where": where, "why": why})
    return {"roots": roots, "workers": workers, "files_seen": seen, "tiffs": tiffs, "admitted": admitted,
            "refused": sum(rules.values()), "unreadable": unreadable,
            "by_rule": dict(rules.most_common()), "examples": examples,
            "largest_admitted": {"held_mib": round(largest[0] / 2 ** 20, 1), "where": largest[1]}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    got = measure(a.roots, workers=a.workers or None)
    text = json.dumps(got, indent=1, ensure_ascii=False)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
