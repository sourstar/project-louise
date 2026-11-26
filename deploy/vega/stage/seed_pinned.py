#!/usr/bin/env python3
"""EVERY MODEL BEHIND VEGA PINS ITS SEED — said at each deploy, from the
model files the node RUNS.

WHY (Tensor, 2026-11-26, found in a harness's model file, not on the node):
a LocalAI model file that pins no seed gets one drawn at each load, and the
seed is among the options the VeGa wrapper states for a run. So every load of
such a model is ANOTHER RUN — six loads, six run ids, differing in
`engine.options.Seed` alone. For an encoder that is a run nobody validated: a
gate asked again after every reload, and until it passes no vector of it is
written (CLAUDE.md invariant 12 — the write is refused, so nothing wrong is
stored; what is lost is the time, and the reason is nowhere said). All
fifteen vega model files staged in deploy/vega/stage/models pin `"seed": 0`
(a test holds that); the node's own files are what runs, and they are not
all staged ones. München counted them that night: 59 files behind a vega
package, 58 with seed 0, one with none (a translation model outside Louise's
set).

    python3 deploy/vega/stage/seed_pinned.py                    # the node's models path
    python3 deploy/vega/stage/seed_pinned.py --models DIR --json

WHAT IT READS: every `*.yaml` / `*.yml` of LocalAI's models path whose
`backend` names a vega package (`vega-…`). The seed is `parameters.seed`,
else a top-level `seed`; pinned means an integer of 0 or more (a negative
one asks LocalAI for a random seed). It reads, and nothing else.

WHAT IT SAYS, and its exit status:
  0  every model behind VeGa pins its seed — or the only ones that do not are
     not embedders (`embeddings: true`): those are LISTED, since each of
     their loads states another run, and refused nothing: no vector depends
     on them;
  1  an EMBEDDER behind VeGa pins none: named, with the line to add;
  2  a file could not be read as a model file: nothing is decided for it.
The deploy runs it after the swap and says its finding as a NOTE: the fault
is a file of the node's, not the tree being deployed, so it never rolls a
deploy back.

THE REFUSAL THAT BELONGS IN THE WRAPPER — "no run can be stated: the model's
file pins no seed" — is written down, not built: a changed wrapper is a new
run for every model of its package (53 minutes of gates, 2026-11-25), so it
rides the wrapper's next rebuild for a reason of its own. Standard
alternative: none found — LocalAI has no "seed must be pinned" setting.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: the node's models path (deploy/vega/retired_backends.py); an argument default only
NODE_MODELS = Path("/mnt/ssd5/apps/localai/models")
VEGA = "vega-"

_KEY = r'^\s*"?%s"?\s*:\s*"?([^"#,\s]+)"?\s*,?\s*(?:#.*)?$'


@dataclass(frozen=True)
class Found:
    file: str
    name: str
    backend: str
    embeddings: bool
    seed: int | None            # None: none pinned

    @property
    def pinned(self) -> bool:
        return self.seed is not None and self.seed >= 0


def _as_int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v.strip())
    return None


def _scan(text: str) -> dict | None:
    """A model file read line by line, where no YAML library is installed
    (the node's host): the first `backend`, `name`, `embeddings` and `seed`
    keys on lines that are not comments. Enough for this check, and for
    nothing else."""
    got: dict = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for key in ("backend", "name", "embeddings", "seed"):
            m = re.match(_KEY % key, line)
            if m and key not in got:
                got[key] = m.group(1)
    if "backend" not in got:
        return None
    out = {"backend": got["backend"], "name": got.get("name"),
           "embeddings": str(got.get("embeddings", "")).lower() == "true"}
    if "seed" in got:
        out["seed"] = got["seed"]
    return out


def read(path: Path) -> dict | None:
    """The file as a mapping: JSON (our staged files are, and JSON is YAML),
    else YAML where the library is there, else the line scan. None: not a
    model file this can read."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    try:
        got = json.loads(body)
        return got if isinstance(got, dict) else None
    except ValueError:
        pass
    try:
        import yaml   # noqa: PLC0415 — only for a file that is not JSON
    except Exception:   # noqa: BLE001 — no library on this host: the line scan
        return _scan(text)
    try:
        got = yaml.safe_load(text)
    except Exception:   # noqa: BLE001 — not YAML either
        return None
    return got if isinstance(got, dict) else None


def seed_of(cfg: dict) -> int | None:
    params = cfg.get("parameters") if isinstance(cfg.get("parameters"), dict) else {}
    for v in (params.get("seed"), cfg.get("seed")):
        n = _as_int(v)
        if n is not None:
            return n
    return None


def look(models: Path) -> tuple[list[Found], list[str]]:
    """(every model file behind a vega package, the files that could not be
    read), each by file name."""
    found, unread = [], []
    try:
        names = sorted(e.name for e in os.scandir(models) if e.is_file() and e.name.endswith((".yaml", ".yml")))
    except OSError as e:
        raise SystemExit(f"cannot list the models path {models}: {e.strerror or e}") from None
    for name in names:
        cfg = read(models / name)
        if cfg is None:
            unread.append(name)
            continue
        backend = cfg.get("backend")
        if not isinstance(backend, str) or not backend.startswith(VEGA):
            continue
        found.append(Found(name, str(cfg.get("name") or Path(name).stem), backend,
                           cfg.get("embeddings") is True, seed_of(cfg)))
    return found, unread


def says(found: list[Found], unread: list[str]) -> tuple[int, list[str]]:
    """(the exit status, the lines to say)."""
    loose = [f for f in found if not f.pinned]
    embedders = [f for f in loose if f.embeddings]
    lines = [f"seed: {len(found)} model file(s) behind VeGa, {len(found) - len(loose)} pin their seed"]
    for f in embedders:
        lines.append(f"seed: {f.file} ({f.name}, {f.backend}) is an EMBEDDER and pins NO seed: LocalAI draws one at "
                     f"each load and the wrapper states it, so every load is another run, never validated — its "
                     f"gate is asked again after each reload and no vector is written until it passes. Add "
                     f"`seed: 0` under `parameters:` in that file")
    for f in loose:
        if not f.embeddings:
            lines.append(f"seed: {f.file} ({f.name}, {f.backend}) pins no seed: each of its loads states another "
                         f"run. It is no embedder, so no vector depends on it: said, nothing refused")
    for name in unread:
        lines.append(f"seed: {name} could not be read as a model file: nothing is decided for it")
    return (1 if embedders else 2 if unread else 0), lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Every model behind VeGa pins its seed (reads only).")
    ap.add_argument("--models", type=Path, default=NODE_MODELS, help="LocalAI's models path on this host")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    found, unread = look(a.models)
    status, lines = says(found, unread)
    if a.json:
        print(json.dumps({"status": status, "behind_vega": len(found),
                          "no_seed": [{"file": f.file, "model": f.name, "backend": f.backend,
                                       "embedder": f.embeddings} for f in found if not f.pinned],
                          "unread": unread, "says": lines}))
    else:
        for line in lines:
            print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
