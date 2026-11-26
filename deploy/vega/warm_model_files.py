#!/usr/bin/env python3
"""READ THE MODELS' WEIGHT FILES WHOLE AND SAY HOW FAST THEY CAME — the
measurement behind "cache the model to reload it from disk warm" (operator,
2026-11-25), and what it found.

THE IDEA. Outside query mode a load that does not fit stops the model used
least recently, and that model is loaded again when it is next asked for.
If its weight file were always in the host's file cache, the reload would
read memory and not the drive; a timer re-reading the embedders' files would
keep them there.

WHAT THE NODE MEASURED (München, 2026-11-25 21:08Z, right after a boot, Louise
stopped and nothing loaded; `--all --json`, twice in a row):

    first pass   105 models, 469.8 GiB in 107.2 s = 4,490 MiB/s   ARC 2,235 -> 16,332 MiB (its cap)
    second pass  the same,   469.8 GiB in 108.4 s = 4,440 MiB/s

    from the DRIVE (both passes: 470 GiB do not fit a 16 GiB cache)    4.3-4.8 GiB/s
        the 35B chat model   20.61 GiB   4.69 s
        madlad                5.48 GiB   1.20 s
        qwen3-embedding-4b    2.33 GiB   0.81 s
        nemotron-embed        2.13 GiB   0.46 s
        bge-m3                1.08 GiB   0.25 s
    from MEMORY (a file read a moment before, shared by a second model) 7.3-7.5 GiB/s

THE FINDING: THE DRIVE IS NOT WHAT A RELOAD COSTS HERE. ssd5 gives 4.5 GiB a
second cold; an embedder's whole file is a quarter to three quarters of a
second of it, and the cache would save about a tenth of a second of that.
The 35B's file is 4.7 s of a load that took 37.4 s on an empty card that
night and 99.2 s that afternoon with twelve models on the card: the time is
the card and the server, not the file. So NO TIMER IS INSTALLED, and the
file cache's cap is not raised for it. What does shorten a model's return is
keeping its weights in host memory in the model server itself — parking
(docs/notes/parking-keeper.md): 15.5 ms measured for bge-m3.

It stays as the tool that measures this, on this node after a change of
drive or on another node:

    python3 deploy/vega/warm_model_files.py                 # the embedders, one pass
    python3 deploy/vega/warm_model_files.py --all           # every model LocalAI holds a file for
    python3 deploy/vega/warm_model_files.py --json          # the pass as one JSON object
    python3 deploy/vega/warm_model_files.py --loop 300      # a pass every 5 minutes, until told to stop

WHICH FILES — the system's to say, never a typed list (CLAUDE.md RULE 2):
LocalAI's own model files in its models path. A model is an embedder when
its file says `embeddings: true`; its weights are `parameters.model`, a file
or a directory (an OpenVINO or a transformers model), as LocalAI's container
names it. The container's path is turned into this host's through the
container's own mounts (`docker inspect`, the longest destination that holds
it), because the weights are mounted into the models path from elsewhere.

IT READS, AND NOTHING ELSE: no file is opened for writing, none is created,
LocalAI is asked nothing. SIGTERM or SIGINT ends it after the file in hand
(exit 0 from `--loop`, 130 from a pass cut short). It needs no RAM of its
own beyond one block — but what it reads fills the host's cache (the ARC
went to its 16 GiB cap in the first pass), in place of what was there.

EACH PASS SAYS THE BYTES, THE SECONDS AND THE RATE of every model, AND NO
VERDICT. A first cut called anything over 4,000 MiB/s "from memory": on this
node that was the DRIVE (4.3-4.8 GiB/s), and its summary would have said "all
from memory" of a pass that read 470 GiB from it. Where a rate comes from is
told by comparing two passes, or two files of one pass — as above — never by
a constant: it is the drive's, and no interface says whether a file is in a
ZFS ARC (`mincore(2)`, what vmtouch reads, sees the page cache, which the
ARC is not).

THE STANDARD TOOL for touching files into a page cache is vmtouch (`vmtouch
-t FILES`; `vmtouch -dl` locks them there): not on TrueNAS SCALE's base
image, blind to the ARC, and what it lacks is the list of files, which is
what this derives.
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
        return (f"{self.name}: {self.bytes / MIB:,.0f} MiB in {self.files} file(s), {self.seconds:.2f} s"
                + ("" if rate is None else f" ({rate:,.0f} MiB/s)")
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
    rates = sorted(r.rate for r in reads if r.rate is not None)
    return (f"read: {len(reads)} model(s), {total / MIB:,.0f} MiB in {secs:.1f} s"
            + (f" = {total / MIB / secs:,.0f} MiB/s" if secs > 0 and total >= RATE_MIN_BYTES else "")
            + (f"; per model {rates[0]:,.0f} to {rates[-1]:,.0f} MiB/s — where a rate comes from is told by "
               f"a second pass, not by its size" if rates else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read the models' weight files whole and say the rate (reads only).")
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
