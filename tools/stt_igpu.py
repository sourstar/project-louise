"""Speech to text on the processor's integrated Radeon: the model YAMLs and
the measurement (docs/notes/stt-on-igpu.md, operator 2026-11-11: "I want to
run on it the STT model currently running in CPU, and see how fast it is").

THE SPEECH PROCESSING IS NOT CHANGED (operator, 2026-11-11: "without
changing the speech processing logic: do it at the end of the speech"). The
Ask page still records the whole question, and app/transcribe.py still runs
the VAD and then the STT on the whole clip once the reader stops. Only the
ENGINE under the STT model moves: the same weights on `vulkan-parakeet-cpp`
(or `rocm-parakeet-cpp`) in the node's own LocalAI. The note's §7 designs
(A, B and C, transcribing while the reader speaks) are not built.

    python3 -m tools.stt_igpu yaml  MODEL.yaml --out DIR --env GGML_VK_VISIBLE_DEVICES=1
    python3 -m tools.stt_igpu bench CLIPS --server URL --model NAME --out RESULT.json [--first] \
            [--pid HOSTPID] [--igpu 0000:13:00.0]
    python3 -m tools.stt_igpu diff  CPU.json IGPU.json

`yaml` (the note's §4 step 3.1 and 3.4). It reads the live STT model's YAML
and writes two files into DIR, both as JSON, which is YAML (the convention
of deploy/vega/stage/models/):
  - `<name>.yaml`: the same model with its `backend` pinned to the CONCRETE
    CPU build, `cpu-parakeet-cpp`. Every parakeet build carries the alias
    `parakeet-cpp`, and installing a second build can move what the alias
    names, which would move the live model to the iGPU unasked (§5).
  - `<name>-igpu.yaml`: the same weights on the GPU build, with the
    device pinned in its `env`. The tool refuses a GPU model with no pin:
    the Vulkan bundle carries ANV (it sees the B70) and lavapipe (the CPU),
    and either can be picked silently. `--env` names the pin, for example
    `GGML_VK_VISIBLE_DEVICES=<n>`, `VK_DRIVER_FILES=<RADV's icd json>` or
    `PARAKEET_DEVICE=Vulkan<n>`. The ROCm build also gets
    `HSA_OVERRIDE_GFX_VERSION=10.3.0` (gfx1036 is not on ROCm's list, §3).
The operator copies them into LocalAI's models directory (node-runbook.md
§3). Nothing here writes to the node.

`bench` (§4 steps 2 and 4). It sends each 16 kHz mono WAV under CLIPS to
LocalAI's `POST /v1/audio/transcriptions` (OpenAI's API, the call the Ask
page's relay makes, without the VAD so that only the model is timed). A
subdirectory's name is its clips' language (CLIPS/en/q1.wav), and a clip at
the top level is sent with none. Per clip it records:
  - the warm time, as the median of `--runs`, and the RTF (time ÷ audio);
  - the transcript, which is the reference for `diff`;
  - with `--pid` (the backend's pid as the host numbers it), its CPU in
    cores, from /proc/<pid>/stat;
  - with an iGPU present, `gpu_busy_percent` at its peak (sampled every
    50 ms) and its VRAM and GTT use before and after the run;
  - the package temperature: k10temp's Tctl, at its peak.
`--first` unloads the model first (LocalAI's `POST /backend/shutdown`) and
times the call that pays the load. That figure is labelled and kept out of
the warm figures (measuring.md §10). Free space where the result is written
is read before and after. Every figure is labelled "measured".

`diff`: the transcripts of two `bench` results compared word by word
(difflib's SequenceMatcher), with the median times side by side.

THE STANDARD ALTERNATIVE (CLAUDE.md, "Do not reinvent the wheel"):
`parakeet-cli`'s own benchmark (parakeet.cpp benchmarks/) times the model
outside LocalAI, and `pidstat`/`radeontop` give CPU and GPU use. This tool
instead measures through the engine that will serve (measuring.md §7, "An
offline count is not the live engine") and keeps every figure in one file.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.voice_reference import multipart

#: the concrete CPU build: what the live model is pinned to (§4 step 3.1)
CPU_BACKEND = "cpu-parakeet-cpp"
#: the GPU builds the backend gallery offers for parakeet (§3)
GPU_BACKENDS = ("vulkan-parakeet-cpp", "rocm-parakeet-cpp")
#: the alias every parakeet build carries
ALIAS = "parakeet-cpp"
#: the env names that pin the device (§5): one of them must be set
DEVICE_PINS = ("GGML_VK_VISIBLE_DEVICES", "VK_DRIVER_FILES", "VK_ICD_FILENAMES", "PARAKEET_DEVICE",
               "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES")
#: ROCm on gfx1036 runs only as gfx1030 (§3; a debugging aid, as ROCm's maintainers say)
ROCM_OVERRIDE = ("HSA_OVERRIDE_GFX_VERSION", "10.3.0")
SUFFIX = "-igpu"
#: the iGPU (node-runbook.md §1)
IGPU_PCI = "0000:13:00.0"
RATE = 16000
SAMPLE_S = 0.05
CALL_TIMEOUT_S = 180.0
LABEL = "measured"


# ---------------------------------------------------------------- the YAMLs

def load_model(text: str) -> dict[str, Any]:
    """A LocalAI model YAML (JSON is YAML too)."""
    try:
        import yaml                                    # PyYAML: in the app image (constraints.txt)
    except ImportError:
        yaml = None
    if yaml is not None:
        got = yaml.safe_load(text)
    else:
        body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        try:
            got = json.loads(body)
        except ValueError:
            raise SystemExit("this Python has no PyYAML and the model file is not JSON: run the tool "
                             "in Louise's image (docker exec -i ix-louise-rag-web-1 python -m tools.stt_igpu …)")
    if not isinstance(got, dict) or not got.get("name"):
        raise SystemExit("not a LocalAI model file: no `name`")
    return got


def pinned_cpu(model: dict[str, Any]) -> dict[str, Any]:
    """The live model with its backend named by the concrete CPU build."""
    backend = str(model.get("backend") or "")
    if backend not in (ALIAS, CPU_BACKEND):
        raise SystemExit(f"{model['name']}: backend {backend!r} is not parakeet's CPU build "
                         f"({ALIAS} or {CPU_BACKEND}); nothing written")
    return {**model, "backend": CPU_BACKEND}


def igpu_twin(model: dict[str, Any], backend: str, env: dict[str, str]) -> dict[str, Any]:
    """The same weights on a GPU build, under `<name>-igpu`, its device
    pinned in `env`. Raises SystemExit for an unpinned device."""
    if backend not in GPU_BACKENDS:
        raise SystemExit(f"--backend must be one of {', '.join(GPU_BACKENDS)}")
    merged = {str(k): str(v) for k, v in (model.get("env") or {}).items()} | env
    if backend.startswith("rocm-"):
        merged.setdefault(*ROCM_OVERRIDE)
    if not any(merged.get(k) for k in DEVICE_PINS):
        raise SystemExit("the device is not pinned: the Vulkan bundle also carries ANV (the B70) and "
                         "lavapipe (the CPU), and either can be picked silently. Name the Radeon with "
                         "--env, one of: " + ", ".join(DEVICE_PINS))
    name = str(model["name"])
    return {**model, "name": name if name.endswith(SUFFIX) else name + SUFFIX, "backend": backend,
            "env": dict(sorted(merged.items()))}


def _write_model(path: Path, model: dict[str, Any], comment: str) -> None:
    head = "".join(f"# {line}\n" for line in comment.splitlines())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(head + json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_yamls(source: dict[str, Any], out: Path, backend: str, env: dict[str, str]) -> list[Path]:
    cpu, gpu = pinned_cpu(source), igpu_twin(source, backend, env)
    out.mkdir(parents=True, exist_ok=True)
    a, b = out / f"{cpu['name']}.yaml", out / f"{gpu['name']}.yaml"
    _write_model(a, cpu, f"{cpu['name']}: the live STT, its backend pinned to the concrete CPU build\n"
                         f"({CPU_BACKEND}, not the alias {ALIAS}): docs/notes/stt-on-igpu.md §4 step 3.1.\n"
                         "JSON, which is YAML. Written by tools/stt_igpu.py yaml.")
    _write_model(b, gpu, f"{gpu['name']}: {cpu['name']}'s weights on the processor's Radeon ({backend}),\n"
                         "the device pinned in env: docs/notes/stt-on-igpu.md §4 step 3.4.\n"
                         f"Back to the CPU: louise node speech set --stt {cpu['name']}\n"
                         "JSON, which is YAML. Written by tools/stt_igpu.py yaml.")
    return [a, b]


# ---------------------------------------------------------------- the readings

def wav_seconds(data: bytes) -> float:
    """The clip's length; ValueError for a WAV not in the page's form."""
    import io
    with wave.open(io.BytesIO(data), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
            raise ValueError("not 16 kHz mono 16-bit")
        return w.getnframes() / RATE


def clips(root: Path) -> list[tuple[Path, str | None]]:
    """(wav, language) under `root`: a subdirectory's name is its language."""
    out = [(p, None) for p in sorted(root.glob("*.wav"))]
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        out += [(p, d.name) for p in sorted(d.glob("*.wav"))]
    return out


def read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def tctl_path(hwmon: Path = Path("/sys/class/hwmon")) -> Path | None:
    """k10temp's Tctl: the package temperature the note watches (§4 step 4)."""
    try:
        dirs = sorted(hwmon.iterdir())
    except OSError:
        return None
    for d in dirs:
        if (d / "name").is_file() and (d / "name").read_text().strip() == "k10temp":
            for label in sorted(d.glob("temp*_label")):
                if label.read_text().strip() == "Tctl":
                    return label.with_name(label.name.replace("_label", "_input"))
            if (d / "temp1_input").is_file():
                return d / "temp1_input"
    return None


def cpu_seconds(pid: int, proc: Path = Path("/proc")) -> float | None:
    """utime + stime of a process, in seconds (proc(5), fields 14 and 15)."""
    try:
        text = (proc / str(pid) / "stat").read_text()
    except OSError:
        return None
    rest = text[text.rindex(")") + 2:].split()
    return (int(rest[11]) + int(rest[12])) / os.sysconf("SC_CLK_TCK")


@dataclass
class Peaks:
    """The iGPU's busy share and the package temperature, at their peak,
    sampled every SAMPLE_S on a thread of their own while calls run."""
    busy: Path | None
    tctl: Path | None
    busy_max: int | None = None
    tctl_max_c: float | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop.is_set():
            b = read_int(self.busy) if self.busy else None
            t = read_int(self.tctl) if self.tctl else None
            if b is not None:
                self.busy_max = max(b, self.busy_max or 0)
            if t is not None:
                self.tctl_max_c = max(t / 1000.0, self.tctl_max_c or -273.0)
            self._stop.wait(SAMPLE_S)

    def __enter__(self) -> "Peaks":
        self._thread = threading.Thread(target=self._run, name="stt-igpu-peaks", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)


def igpu_memory(pci: str, sysfs: Path = Path("/sys/bus/pci/devices")) -> dict[str, int | None] | None:
    d = sysfs / pci
    if not d.is_dir():
        return None
    return {k: read_int(d / k) for k in ("mem_info_vram_total", "mem_info_vram_used",
                                         "mem_info_gtt_total", "mem_info_gtt_used")}


# ---------------------------------------------------------------- the calls

def transcribe(server: str, model: str, data: bytes, language: str | None,
               timeout: float = CALL_TIMEOUT_S) -> tuple[str, float]:
    """(text, wall seconds) of one `POST /v1/audio/transcriptions`."""
    fields = {"model": model, "response_format": "json"}
    if language:
        fields["language"] = language
    body, ctype = multipart(fields, data)
    req = urllib.request.Request(f"{server.rstrip('/')}/v1/audio/transcriptions", data=body,
                                 headers={"Content-Type": ctype}, method="POST")
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    took = time.perf_counter() - t
    return str((json.loads(raw or b"{}") or {}).get("text") or "").strip(), took


def unload(server: str, model: str) -> None:
    """LocalAI's `POST /backend/shutdown`: the next call pays the load."""
    req = urllib.request.Request(f"{server.rstrip('/')}/backend/shutdown",
                                 data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60.0) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code != 404:                 # not loaded: nothing to unload
            raise


def bench(root: Path, server: str, model: str, *, runs: int, first: bool, pid: int | None,
          pci: str, out: Path, call=transcribe) -> dict[str, Any]:
    found = clips(root)
    if not found:
        raise SystemExit(f"no .wav under {root}")
    result: dict[str, Any] = {"label": LABEL, "model": model, "server": server, "runs": runs,
                              "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                              "free_bytes_before": shutil.disk_usage(out.parent).free,
                              "igpu_before": igpu_memory(pci), "first_call": None, "clips": []}
    if first:
        unload(server, model)
        data = found[0][0].read_bytes()
        _text, took = call(server, model, data, found[0][1])
        result["first_call"] = {"clip": found[0][0].name, "seconds": round(took, 3),
                                "note": "paid the model's load: kept out of the warm figures"}
    busy = Path(f"/sys/bus/pci/devices/{pci}/gpu_busy_percent")
    for path, lang in found:
        data = path.read_bytes()
        try:
            seconds = wav_seconds(data)
        except (ValueError, wave.Error, EOFError) as e:
            raise SystemExit(f"{path}: {e} — the Ask page sends 16 kHz mono 16-bit WAV")
        call(server, model, data, lang)                      # warm: never timed
        cpu0 = cpu_seconds(pid) if pid else None
        times, text = [], ""
        with Peaks(busy if busy.is_file() else None, tctl_path()) as peaks:
            for _ in range(runs):
                text, took = call(server, model, data, lang)
                times.append(took)
        cpu1 = cpu_seconds(pid) if pid else None
        med = statistics.median(times)
        row = {"clip": str(path.relative_to(root)), "language": lang, "audio_s": round(seconds, 3),
               "warm_s": [round(t, 4) for t in times], "warm_median_s": round(med, 4),
               "rtf": round(med / seconds, 4) if seconds else None,
               "rtfx": round(seconds / med, 2) if med else None, "text": text,
               "cpu_cores": (round((cpu1 - cpu0) / sum(times), 2)
                             if cpu0 is not None and cpu1 is not None and sum(times) else None),
               "igpu_busy_max_pct": peaks.busy_max, "tctl_max_c": peaks.tctl_max_c}
        result["clips"].append(row)
        print(f"{row['clip']}: {row['audio_s']} s audio, warm {row['warm_median_s']} s, RTFx {row['rtfx']}",
              file=sys.stderr)
    result["igpu_after"] = igpu_memory(pci)
    result["free_bytes_after"] = shutil.disk_usage(out.parent).free
    return result


# ---------------------------------------------------------------- the comparison

def words(text: str) -> list[str]:
    return text.split()


def compare(ref: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """Per clip in both: equal transcripts or the word-level differences,
    and the two median times."""
    theirs = {c["clip"]: c for c in other.get("clips", ())}
    rows, same = [], 0
    for c in ref.get("clips", ()):
        o = theirs.get(c["clip"])
        if o is None:
            continue
        a, b = words(c["text"]), words(o["text"])
        diff = [{"op": op, "ref": " ".join(a[i1:i2]), "other": " ".join(b[j1:j2])}
                for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()
                if op != "equal"]
        same += not diff
        rows.append({"clip": c["clip"], "equal": not diff, "differences": diff,
                     "ref_s": c["warm_median_s"], "other_s": o["warm_median_s"],
                     "speed": round(c["warm_median_s"] / o["warm_median_s"], 2) if o["warm_median_s"] else None})
    return {"label": LABEL, "ref": ref.get("model"), "other": other.get("model"), "clips": rows,
            "equal": same, "compared": len(rows),
            "median_speed": (round(statistics.median(r["speed"] for r in rows if r["speed"]), 2)
                             if any(r["speed"] for r in rows) else None)}


# ---------------------------------------------------------------- the command line

def _env(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs:
        k, sep, v = p.partition("=")
        if not sep or not k:
            raise SystemExit(f"--env takes NAME=VALUE, not {p!r}")
        out[k] = v
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    y = sub.add_parser("yaml", help="the live STT pinned to the CPU build, and its -igpu twin")
    y.add_argument("model", help="the live STT model's YAML ('-': standard input)")
    y.add_argument("--out", required=True)
    y.add_argument("--backend", default=GPU_BACKENDS[0], choices=GPU_BACKENDS)
    y.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                   help="the twin's env; one must pin the Radeon (" + ", ".join(DEVICE_PINS) + ")")
    b = sub.add_parser("bench", help="time the clips through one model")
    b.add_argument("clips")
    b.add_argument("--server", required=True, help="the node's LocalAI, e.g. http://127.0.0.1:30286")
    b.add_argument("--model", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--runs", type=int, default=5)
    b.add_argument("--first", action="store_true", help="unload first, and time the call that loads")
    b.add_argument("--pid", type=int, help="the backend's pid on the host, for its CPU")
    b.add_argument("--igpu", default=IGPU_PCI, help=f"the iGPU's PCI address (default {IGPU_PCI})")
    d = sub.add_parser("diff", help="two bench results: transcripts word by word, times")
    d.add_argument("ref")
    d.add_argument("other")
    a = ap.parse_args(argv)

    if a.cmd == "yaml":
        text = sys.stdin.read() if a.model == "-" else Path(a.model).read_text(encoding="utf-8")
        for p in write_yamls(load_model(text), Path(a.out), a.backend, _env(a.env)):
            print(p)
        return 0
    if a.cmd == "bench":
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        got = bench(Path(a.clips), a.server, a.model, runs=max(1, a.runs), first=a.first, pid=a.pid,
                    pci=a.igpu, out=out)
        out.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(out)
        return 0
    got = compare(json.loads(Path(a.ref).read_text(encoding="utf-8")),
                  json.loads(Path(a.other).read_text(encoding="utf-8")))
    print(json.dumps(got, indent=1, ensure_ascii=False))
    return 0 if got["equal"] == got["compared"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
