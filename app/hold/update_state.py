"""The update marker, `<root>/<name>/state/update.json`: where a
collection's update by cadence stands (docs/notes/collection-updates.md §1;
app/hold/update.py is the pass).

One JSON object, rewritten whole and atomically (a temporary beside it,
fsync, rename, fsync of the directory) and ONLY AFTER the durable commit of
what it records (invariant 2: marking first is the one ordering that loses
data). A crash leaves the marker naming the step that was running, and the
next pass resumes there. Its shape:

    {"kind": "louise-update", "version": 1,
     "collection_id": "sha256:…",
     "period": "2026-11",            the snapshot this update makes
     "run": "20261201",              the origin's run it is built from
     "from": {"snapshot": "2026-10", "run": "20261101"},
     "step": "fetch",                the step to run next; "done" at the end
     "moved": false,                 the collection reads `run` (step `run`)
     "scan": null | {"run", "complete", "at", "counts"},
     "steps": {"<step>": {"start", "end", "counts"}, …},
     "failure": null | {"step", "error", "tries", "at", "night"},
     "paused": false}                the worker paused the collection for it

Node-local state (07 §7.8's `state/`), never shared and never hashed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

KIND = "louise-update"
VERSION = 1
FILE = "update.json"


def path(root, name: str) -> Path:
    return Path(root) / name / "state" / FILE


def read(root, name: str) -> dict[str, Any] | None:
    """The marker, or None when there is none or it does not parse (a
    marker is written atomically, so an unparseable one was written by
    hand — and is not read as a step reached)."""
    return read_at(path(root, name))


def read_at(p) -> dict[str, Any] | None:
    try:
        got = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and got.get("kind") == KIND else None


def write(root, name: str, doc: dict[str, Any]) -> None:
    write_at(path(root, name), doc)


def write_at(p, doc: dict[str, Any]) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({**doc, "kind": KIND, "version": VERSION}, sort_keys=True, indent=1) + "\n"
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    try:
        fd = os.open(p.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
