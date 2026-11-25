#!/usr/bin/env python3
"""KEEP THE EMBEDDERS' WEIGHT FILES IN THE FILE CACHE, so a model that was
unloaded comes back from memory and not from the drive (operator, 2026-11-25:
"cache the model to reload it from disk warm").

Outside query mode a load that does not fit unloads the model used least
recently (`[models] make_room`, app/model_evict.py), and that model is loaded
again when it is next asked for. What the reload costs depends on where its
file is (docs/notes/model-profile.md, measured on the node):

    from the drive        from the file cache
    qwen3-embedding-4b   33.7 s      about 6 s
    medcpt-article-ov    21.1 s      3.5 s
    madlad               38.0 s      16.7 s
    rerank-medcpt         5.6 s      2.4 s

A file read stays cached only until something else needs the room, and in
embed mode the corpus is read through the same cache all day. This reads the
embedders' weight files again, whole and in order, each time it is run: a
file still cached costs a copy in memory and stays among the ones used most;
a file that was dropped is read back from the drive NOW, on nobody's
request, instead of at a reload someone waits for.

    python3 deploy/vega/warm_model_files.py                 # one pass (a timer runs it)
    python3 deploy/vega/warm_model_files.py --loop 300      # a pass every 5 minutes, until told to stop
    python3 deploy/vega/warm_model_files.py --all           # every model LocalAI holds a file for
    python3 deploy/vega/warm_model_files.py --json          # the pass as one JSON object

WHICH FILES — the system's to say, never a typed list (CLAUDE.md RULE 2):
LocalAI's own model files in its models path. A model is an embedder when
its file says `embeddings: true`; its weights are `parameters.model`, a file
or a directory (an OpenVINO or a transformers model), as LocalAI's container
names it. The container's path is turned into this host's through the
container's own mounts (`docker inspect`, the longest destination that holds
it), because the weights are mounted into the models path from elsewhere.

IT READS, AND NOTHING ELSE: no file is opened for writing, none is created,
LocalAI is asked nothing. It needs no RAM of its own beyond one block; the
cache it fills is the host's (on the node the ZFS ARC, capped at 16 GB, where
the embedders' ~8 GB then stand in for as many corpus blocks). SIGTERM or
SIGINT ends it after the file in hand (exit 0 from `--loop`, 130 from a pass
cut short).

EACH PASS SAYS WHAT IT FOUND — per model the bytes, the seconds and the rate.
The rate is the measurement: several GiB a second is memory, a few hundred
MiB a second is the drive. The first pass after a quiet hour tells whether
the files were still cached, which nothing else on the node records.

THE STANDARD TOOL for this is vmtouch (`vmtouch -t FILES` touches them into
the cache, `vmtouch -dl` locks them there): not on TrueNAS SCALE's base
image, and what it lacks is the list of files, which is what this derives.
ALTERNATIVES, written down: `vmtouch -dl` or mlock — the files can then never
be dropped, at the price of that much RAM taken from the parse pool for good
(4.7 GB were available at the node's worst hour, 2026-11-24); a copy on a
tmpfs — another path for the same weights, which the engines' identity is
keyed on, so no; parking (docs/notes/parking-step-2.md) — the weights kept in
host memory by the model server itself and back on the card in milliseconds,
the form this should take once that engine is live.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

#: the node's paths (deploy/vega/retired_backends.py, node-runbook.md §1);
#: argument defaults only
NODE_MODELS = Path("/mnt/ssd5/apps/localai/models")
LOCALAI = "ix-localai-localai-1"
BLOCK = 16 << 20
MIB = 1 << 20
#: a file read faster than this came from memory: an NVMe drive read whole
#: gives 1–3 GiB/s at best through ZFS, memory several times that; under it,
#: the pass says "the drive" — a statement of the rate, not of the device
FROM_MEMORY_MIB_S = 4000.0
#: a file smaller than this says nothing by its rate (it is over before the clock moves)
RATE_MIN_BYTES = 64 << 20

_stop = False


def _on_signal(signum, frame) -> None:   # noqa: ARG001
    global _stop
    _stop = True


@dataclass(frozen=True)
class Model:
    name: str
    #: the weights as LocalAI's container names them
    container_path: str
    embeddings: bool


@dataclass
class Read:
    name: str
    files: int = 0
    bytes: int = 0
    seconds: float = 0.0
    missing: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float | None:
        return self.bytes / MIB / self.seconds if self.seconds > 0 and self.bytes >= RATE_MIN_BYTES else None

    def says(self) -> str:
        if self.missing and not self.files:
            return f"{self.name}: its weights are not on this host ({self.missing[0]})"
        rate = self.rate
        where = ("" if rate is None else
                 " — from memory" if rate >= FROM_MEMORY_MIB_S else " — FROM THE DRIVE: it had been dropped")
        return (f"{self.name}: {self.bytes / MIB:,.0f} MiB in {self.files} file(s), {self.seconds:.1f} s"
                + ("" if rate is None else f" ({rate:,.0f} MiB/s)") + where
                + (f"; {len(self.missing)} path(s) not found" if self.missing else ""))


def _config(path: Path) -> dict | None:
    """One of LocalAI's model files as an object: JSON (which our staged
    files are, and which is YAML), else YAML where the library is there."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    try:
        got = json.loads(body)
    except ValueError:
        try:
            import yaml   # noqa: PLC0415 — only for a file that is not JSON
            got = yaml.safe_load(text)
        except Exception:   # noqa: BLE001 — no library, or not YAML either: not a model this can read
            return None
    return got if isinstance(got, dict) else None


def models_in(models: Path, container_models: str) -> list[Model]:
    """Every model LocalAI holds a file for that names its weights, by name."""
    out: dict[str, Model] = {}
    try:
        names = sorted(e.name for e in os.scandir(models)
                       if e.is_file() and e.name.endswith((".yaml", ".yml")))
    except OSError as e:
        raise SystemExit(f"cannot list the models path {models}: {e.strerror or e}") from None
    for name in names:
        cfg = _config(models / name)
        if not cfg:
            continue
        weights = (cfg.get("parameters") or {}).get("model") if isinstance(cfg.get("parameters"), dict) else None
        if not isinstance(weights, str) or not weights.strip() or "://" in weights:
            continue                    # no local weights named (a gallery reference, a remote one)
        where = weights if weights.startswith("/") else str(PurePosixPath(container_models) / weights)
        model = str(cfg.get("name") or Path(name).stem)
        out.setdefault(model, Model(model, where, cfg.get("embeddings") is True))
    return [out[k] for k in sorted(out)]


def container_mounts(container: str, run=subprocess.run) -> list[tuple[str, str]]:
    """[(destination in the container, source on this host)] of LocalAI's
    container, longest destination first; [] where docker names none (the
    paths are then taken as this host's own)."""
    try:
        out = run(["docker", "inspect", container, "--format", "{{json .Mounts}}"],
                  capture_output=True, text=True, timeout=30)
        rows = json.loads(out.stdout or "[]") if out.returncode == 0 else []
    except (OSError, ValueError, subprocess.SubprocessError):
        rows = []
    got = [(str(r.get("Destination") or "").rstrip("/") or "/", str(r.get("Source") or ""))
           for r in rows if isinstance(r, dict) and r.get("Source") and r.get("Destination")]
    return sorted(got, key=lambda m: -len(m[0]))


def host_path(container_path: str, mounts: list[tuple[str, str]]) -> Path:
    """Where a path of LocalAI's container is on this host: under the mount
    with the longest destination that holds it; as it stands where none does."""
    p = PurePosixPath(container_path)
    for dest, source in mounts:
        d = PurePosixPath(dest)
        if p == d or d in p.parents:
            return Path(source) / p.relative_to(d)
    return Path(container_path)


def files_of(path: Path) -> list[Path]:
    """The weights' files: the file itself, or every file under a directory
    (an OpenVINO or a transformers model), in a fixed order."""
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.is_file())
    return []


def read_whole(path: Path) -> int:
    """The file read from its first byte to its last; the bytes read. A stop
    asked meanwhile is honoured after it — a file half read warms nothing."""
    n = 0
    with open(path, "rb", buffering=0) as f:
        try:
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_SEQUENTIAL)
        except (AttributeError, OSError):
            pass
        while True:
            block = f.read(BLOCK)
            if not block:
                return n
            n += len(block)


def warm(models: list[Model], mounts: list[tuple[str, str]], *, clock=time.perf_counter) -> list[Read]:
    """One pass: every model's weight files read whole, in order."""
    out = []
    for m in models:
        if _stop:
            break
        got = Read(m.name)
        where = host_path(m.container_path, mounts)
        files = files_of(where)
        if not files:
            got.missing.append(str(where))
        for f in files:
            if _stop:
                break
            t0 = clock()
            try:
                got.bytes += read_whole(f)
                got.files += 1
            except OSError as e:
                got.missing.append(f"{f}: {e.strerror or e}")
            got.seconds += clock() - t0
        out.append(got)
    return out


def summary(reads: list[Read]) -> str:
    total, secs = sum(r.bytes for r in reads), sum(r.seconds for r in reads)
    cold = [r.name for r in reads if r.rate is not None and r.rate < FROM_MEMORY_MIB_S]
    return (f"warm: {len(reads)} model(s), {total / MIB:,.0f} MiB read in {secs:.1f} s"
            + (f" — FROM THE DRIVE: {', '.join(cold)}" if cold else " — all from memory" if reads else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Keep the embedders' weight files in the file cache (reads only).")
    ap.add_argument("--models", type=Path, default=NODE_MODELS, help="LocalAI's models path on this host")
    ap.add_argument("--container", default=LOCALAI, help="LocalAI's container, asked for its mounts")
    ap.add_argument("--container-models", default=None,
                    help="the models path as the container names it (default: read from its mounts, else /models)")
    ap.add_argument("--all", action="store_true", help="every model with local weights, not the embedders alone")
    ap.add_argument("--loop", type=float, default=0.0, metavar="SECONDS", help="a pass every SECONDS until stopped")
    ap.add_argument("--json", action="store_true", help="each pass as one JSON object")
    a = ap.parse_args(argv)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    while True:
        mounts = container_mounts(a.container)
        inside = a.container_models or next(
            (d for d, s in mounts if os.path.realpath(s) == os.path.realpath(a.models)), "/models")
        chosen = [m for m in models_in(a.models, inside) if a.all or m.embeddings]
        reads = warm(chosen, mounts)
        if a.json:
            print(json.dumps({"models": [{"name": r.name, "files": r.files, "bytes": r.bytes,
                                          "seconds": round(r.seconds, 3),
                                          "mib_s": None if r.rate is None else round(r.rate, 1),
                                          "missing": r.missing} for r in reads],
                              "says": summary(reads)}), flush=True)
        else:
            for r in reads:
                print(r.says(), flush=True)
            print(summary(reads), flush=True)
        if not a.loop:
            return 130 if _stop else 0
        end = time.monotonic() + a.loop
        while not _stop and time.monotonic() < end:
            time.sleep(min(1.0, max(0.0, end - time.monotonic())))
        if _stop:
            return 0


if __name__ == "__main__":
    sys.exit(main())
