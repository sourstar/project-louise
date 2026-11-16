"""The `language-stage` claims of gutenberg's older-language works, merged
into gutenberg-book-editions' claims file (operator, 2026-11-16: "B" — the
stage of a work is an annotation — then "A" — in gutenberg-book-editions,
re-created at Codex version 9; 02 §2.10, 09 §9.5,
docs/notes/historical-languages.md).

    python -m tools.annotations_language_stage --target gutenberg \\
        --claims <book-editions' claims.jsonl> --out claims.jsonl [--work DIR]

then `louise hold annotations update gutenberg-book-editions --claims
claims.jsonl` (its monthly update), or the re-creation's `create
annotations` with it. Run under deploy/supervise-pass.sh.

ONE PASS OVER THE TARGET'S HELD SNAPSHOT, its parts on the parse pool
(`parallel.each_part`: the multi-threading rule), each part's claims kept in
`--work` as it ends, so a pass stopped or killed resumes at the first part
not done (invariant 2; SIGTERM ends the current part and exits 130). A
claim is the matcher's (`app/historical.py` `stage_claim`, method
historical-markers 1): the medieval markers over the WHOLE record, its
first publication where stated, never a translation.

THE MERGE. Every line of the existing file is kept as it was, except the
matcher's own earlier `language-stage` claims — of ANY version of
historical-markers, so a new version replaces the old one's claims and two
matcher claims about one record never share an identity — which this
pass replaces. A person's claim (`human`) and Wikidata's stay beside the
matcher's and win where read (09 §9.5: a claim is identified by type,
subject and method kind; a person over Wikidata over the matcher).
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import Any

#: set by SIGTERM/SIGINT: the current part ends, then exit 130 (07 §7.7)
_STOP = {"asked": False}


def historical_version() -> int:
    """The matcher's version: a part scanned by another is scanned again."""
    from app import historical
    return int(historical.MATCHER["version"])


def scan_part(path: str, contributor: str) -> list[dict[str, Any]]:
    """The matcher's claims about one target part's records (a pool task)."""
    from app import historical
    from app.pack import records as pack_records
    out = []
    for r in pack_records.iter_records(Path(path)):
        c = historical.stage_claim(r, contributor)
        if c is not None:
            out.append(c)
    return out


def _parts(root: Path, target: str) -> list[tuple[str, str]]:
    """(part path, its records_root — what its records ARE, so a part whose
    records changed is scanned again) of the target's current snapshot."""
    from app.hold import collection as col
    from app.share import seeder
    tree = col.shard_dir_of(root, target)
    snap = seeder.cached_snapshot(tree)
    if snap is None or not snap.shards:
        raise SystemExit(f"{target} holds no snapshot to read")
    return [(str(tree / s.path), s.records_root) for s in snap.shards
            if (tree / s.path).is_file()]


def claims_of_target(root: Path, target: str, work: Path, contributor: str, log=print) -> list[dict[str, Any]]:
    """Every part scanned, resumably: a part's claims kept in `work` once
    its scan ends, read back on the next run."""
    from app import parallel
    work.mkdir(parents=True, exist_ok=True)
    parts = _parts(root, target)
    done = {p.stem for p in work.glob("*.json")}

    def key(h: str) -> str:
        return f"v{historical_version()}_" + "".join(ch if ch.isalnum() else "_" for ch in h)[-80:]
    todo = [(p, h) for p, h in parts if key(h) not in done]
    log(f"[language-stage] {target}: {len(parts)} part(s), {len(parts) - len(todo)} already scanned")
    for (path, h), got in zip(todo, parallel.each_part(scan_part, [(p, contributor) for p, _ in todo])):
        tmp = work / (key(h) + ".json.tmp")
        tmp.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True))
        tmp.replace(work / (key(h) + ".json"))
        if got:
            log(f"[language-stage] {Path(path).name}: {len(got)} claim(s)")
        if _STOP["asked"]:
            log("[language-stage] stopped: the next run resumes at the next part")
            raise SystemExit(130)
    out: list[dict[str, Any]] = []
    for _, h in parts:
        out.extend(json.loads((work / (key(h) + ".json")).read_text()))
    return out


def merge(existing: list[bytes], stage_claims: list[dict[str, Any]]) -> tuple[list[bytes], dict[str, Any]]:
    """(the merged claims file's lines, a summary): the existing lines as
    they were, less the matcher's earlier stage claims, then the new ones."""
    from app import historical
    from app.hold import codex as cx
    def ours(c: dict[str, Any]) -> bool:
        m = c.get("method") or {}
        return (c.get("type") == "language-stage" and m.get("kind") == "matcher"
                and m.get("id") == historical.MATCHER["id"])          # whatever its version
    kept = [line for line in existing if not ours(json.loads(line))]
    new = [cx.canonical_json(c) for c in sorted(stage_claims, key=lambda c: c["subject"])]
    lines = kept + new
    return lines, {"kept": len(kept), "replaced": len(existing) - len(kept), "language_stage": len(new),
                   "claims": len(lines)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--target", default="gutenberg", help="the held collection the claims are about")
    ap.add_argument("--claims", type=Path, required=True, help="the existing claims file, kept and merged")
    ap.add_argument("--out", type=Path, required=True, help="the merged claims file (JSON Lines)")
    ap.add_argument("--work", type=Path, help="where each part's claims are kept (resumable)")
    ap.add_argument("--root", type=Path, help="the collections root (default: the node's)")
    ap.add_argument("--contributor", default="louise historical-markers")
    a = ap.parse_args(argv)
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: _STOP.__setitem__("asked", True))
    from app import collections as collections_mod
    root = a.root or collections_mod.louise_root() / "collections"
    work = a.work or a.out.with_name(a.out.name + ".parts")
    stage = claims_of_target(root, a.target, work, a.contributor)
    existing = [line for line in a.claims.read_bytes().split(b"\n") if line.strip()]
    lines, summary = merge(existing, stage)
    tmp = a.out.with_name(a.out.name + ".tmp")
    tmp.write_bytes(b"".join(line + b"\n" for line in lines))
    tmp.replace(a.out)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
