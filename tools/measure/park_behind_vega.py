#!/usr/bin/env python3
"""Parking BEHIND THE VEGA WRAPPER, the three parts together where there is no
card (docs/notes/parking-step-2.md §11): LocalAI's real server (staged 0027),
VeGa's wrapper as the repository has it (deploy/vega/vega_wrapper.py, started
by LocalAI as a backend, as on the node), and LocalAI's llama.cpp backend built
from the park engine's source for CPU with the stand-in device
(tools/measure/park_cpu_standin.patch, DEV ONLY, in no engine).

On the card the copies that parked were on the engine directly, so this
junction - LocalAI's `Free`, `Status` and its repeated `LoadModel` passing
through the wrapper to a real engine, and the wrapper holding the same run
across a return - had run nowhere.

    python3 tools/measure/park_behind_vega.py LOCAL_AI GRPC_SERVER MODEL.gguf \\
        --localai-tree <LocalAI's source: backend/backend.proto, backend/python/common> [--work DIR] [-n 20]

It starts a receipts service of its own (app/vega/service.py, on a socket in
the work directory), writes a `vega-park` package and a model file that says
`park: true`, starts LocalAI three times - plainly, with the stand-in refusing
two returns, with it failing a return - and runs tools/measure/park_http.py's
rows through it, each cycle COUNTED by the engine. Then what only VeGa can
say: one run across every return, the contexts the wrapper counted, the
canary failures.

Exit 0: every row passed. The work directory keeps LocalAI's logs.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.vega import client  # noqa: E402
from app.vega.service import Intake, Keeps, Passes, ReceiptStore, Runs  # noqa: E402
from app.vega.store import RunStore  # noqa: E402

spec = importlib.util.spec_from_file_location("park_http", ROOT / "tools" / "measure" / "park_http.py")
park = importlib.util.module_from_spec(spec)
spec.loader.exec_module(park)

MODEL = "copy"
PACKAGE = "vega-park"
FLOOR = 1 << 30
#: the wrapper's line for a repeated LoadModel it let through as the load in force (Gate.load_model)
STANDS = "the load in force asked again"

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append(bool(ok))
    print(("ok:   " if ok else "FAIL: ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    return bool(ok)


class Tagged(park.Server):
    """park_http's client, sending every input TAGGED as Louise's client does (app/vega/client.py): the wrapper
    refuses an input without a VeGa tag. The ids of the last request's items are kept: their receipts are asked for."""

    last_ids: tuple[str, ...] = ()

    def embed(self, model, texts=None):
        batch = client.tag_batch(list(texts or park.TEXTS))
        self.last_ids = batch.ids
        status, body, headers = self.call("POST", "/v1/embeddings", {"model": model, "input": list(batch.inputs)})
        if status == 200 and isinstance(body, dict):
            return status, [d["embedding"] for d in body["data"]], headers
        return status, body, headers


class Receipts:
    """VeGa's receipts service, as tests/vega/conftest.py runs it: the intake on an event loop of its own"""

    def __init__(self, work: Path) -> None:
        self.socket = work / "r.sock"
        self.receipts, self.runs = ReceiptStore(), Runs(RunStore(work / "vega-runs.sqlite"))
        self.passes, self.keeps = Passes(), Keeps()
        self.intake = Intake(self.socket, self.receipts, self.runs, log=lambda _m: None,
                             passes=self.passes, keeps=self.keeps)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        asyncio.run_coroutine_threadsafe(self.intake.start(), self.loop).result(10)

    def stop(self) -> None:
        asyncio.run_coroutine_threadsafe(self.intake.close(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)


def lay_out(work: Path, grpc_server: Path, weights: Path, tree: Path, receipts: Path) -> None:
    """LocalAI's stubs, the real backend's directory, the vega package in front of it, the model's file"""
    from grpc_tools import protoc
    import grpc_tools
    stubs = work / "stubs"
    stubs.mkdir()
    proto = tree / "backend" / "backend.proto"
    rc = protoc.main(["protoc", f"-I{proto.parent}", f"-I{Path(grpc_tools.__file__).parent / '_proto'}",
                      f"--python_out={stubs}", f"--grpc_python_out={stubs}", str(proto)])
    if rc != 0:
        raise SystemExit(f"protoc failed on {proto}")
    backends = work / "backends"
    real = backends / "park-llama-cpp"
    real.mkdir(parents=True)
    (real / "run.sh").write_text(f'#!/bin/sh\nexport LD_LIBRARY_PATH="{grpc_server.parent}"\nexec "{grpc_server}" "$@"\n')
    (real / "run.sh").chmod(0o755)
    (real / "metadata.json").write_text(json.dumps({"name": "park-llama-cpp", "version": "cpu-stand-in",
                                                    "digest": "sha256:" + "00" * 32}))
    package = backends / PACKAGE
    package.mkdir()
    (package / "vega.json").write_text(json.dumps({"backend": "park-llama-cpp", "receipts_socket": str(receipts)}))
    path = os.pathsep.join([str(ROOT), str(stubs), str(tree / "backend" / "python" / "common")])
    (package / "run.sh").write_text(
        "#!/bin/sh\n"
        f'export VEGA_CONFIG="{package / "vega.json"}"\n'
        f'export PYTHONPATH="{path}"\n'
        # what the wrapper says, and the engine it starts, in a file of the instance's own
        f'exec "{sys.executable}" "{ROOT / "deploy" / "vega" / "vega_wrapper.py"}" "$@" 2>>"$PBV_WRAPPER_LOG"\n')
    (package / "run.sh").chmod(0o755)
    models = work / "models"
    models.mkdir()
    shutil.copy2(weights, models / weights.name)     # a copy: the wrapper states no run for weights behind a link
    # what the wrapper asks of a load before it states a run: a CPU run that says so (`gpu_layers: 0`), one
    # sequence at a time (`parallel:1`), the canary stated for a context on demand. And `seed: 0`, as every vega
    # model file of the node has it: without one LocalAI draws a seed at each load, the seed is in the run's
    # options, and every load is ANOTHER RUN (seen here first: six loads, six run ids, differing in Seed alone)
    (models / f"{MODEL}.yaml").write_text(
        f"name: {MODEL}\nbackend: {PACKAGE}\nembeddings: true\npark: true\ncontext_size: 512\nthreads: 4\n"
        f"gpu_layers: 0\nbatch: 512\nmmap: false\nparameters:\n  model: {weights.name}\n  seed: 0\noptions:\n"
        "  - parallel:1\n  - context_on_demand:true\n  - context_canary:true\n"
        "  - verbosity:4\n  - tensor_buft_overrides:.*=CPU\n")       # no keep of 0: a context lives until a park frees it
    for d in ("lai-backends", "data", "config", "gen"):
        (work / d).mkdir()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalAI:
    def __init__(self, binary: Path, work: Path, name: str, env: dict[str, str]) -> None:
        self.port, self.log = free_port(), work / f"localai-{name}.log"
        self.wrapper_log = work / f"wrapper-{name}.log"
        self.wrapper_log.touch()
        self.url = f"http://127.0.0.1:{self.port}"
        self.out = self.log.open("wb")
        self.proc = subprocess.Popen(
            [str(binary), "run", "--address", f"127.0.0.1:{self.port}", "--models-path", str(work / "models"),
             "--backends-path", str(work / "lai-backends"), "--data-path", str(work / "data"),
             "--localai-config-dir", str(work / "config"), "--generated-content-path", str(work / "gen"),
             "--external-grpc-backends", f"{PACKAGE}:{work / 'backends' / PACKAGE / 'run.sh'}"],
            stdout=self.out, stderr=subprocess.STDOUT, cwd=str(work),
            env={**os.environ, "GGML_CPU_PARK_TEST": "1", "PBV_WRAPPER_LOG": str(self.wrapper_log), **env},
            start_new_session=True)
        deadline = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(self.url + "/system", timeout=2).read()
                break
            except OSError:
                if self.proc.poll() is not None or time.monotonic() > deadline:
                    raise SystemExit(f"LocalAI did not start: see {self.log}") from None
                time.sleep(0.5)
        self.server = Tagged(self.url)
        self.server.set_floor(FLOOR)

    def said(self, words: str) -> int:
        """how many lines of the wrapper, or of the engine it started, carry these words"""
        return sum(words in line for line in self.wrapper_log.read_text(errors="replace").splitlines())

    def stop(self) -> None:
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        self.out.close()
        time.sleep(1.0)        # the add-on owner's port is the next server's too


def row(name: str, passed: bool, lines: list[str], keep: int = 3) -> bool:
    print(f"--- {name}", flush=True)
    for line in (lines if len(lines) <= 2 * keep else lines[:keep] + ["..."] + lines[-keep:]):
        print("    " + line, flush=True)
    return check(name, passed, lines[-1] if not passed else "")


def kept(receipts: Receipts, want=None, patience: float = 5.0) -> dict:
    """the wrapper's count for the one run VeGa holds; waited for where `want` says what it must show (the
    wrapper tells a count before the vector of a call that created a context, otherwise from a thread)"""
    deadline = time.monotonic() + patience
    while True:
        rows = list(receipts.keeps.get())
        row = rows[0] if len(rows) == 1 else {"runs": len(rows)}
        if want is None or want(row) or time.monotonic() > deadline:
            return row
        time.sleep(0.1)


def run_ids(receipts: Receipts, ids) -> set[str]:
    """the runs the receipts of these items name; "no receipt" for an item that has none"""
    found = receipts.receipts.get(ids)
    return {r["run_id"] for r in found} | ({"no receipt"} if len(found) != len(tuple(ids)) else set())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("local_ai", type=Path)
    ap.add_argument("grpc_server", type=Path)
    ap.add_argument("weights", type=Path)
    ap.add_argument("--localai-tree", type=Path, required=True)
    ap.add_argument("--work", type=Path)
    ap.add_argument("-n", type=int, default=20)
    a = ap.parse_args()
    work = (a.work or Path(tempfile.mkdtemp(prefix="pbv-"))).resolve()
    if work.exists() and any(work.iterdir()):
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    receipts = Receipts(work)
    lay_out(work, a.grpc_server.resolve(), a.weights.resolve(), a.localai_tree.resolve(), receipts.socket)
    n = a.n
    one = [park.TEXTS[0]]
    try:
        # ---- one cycle by hand: the five things the wrapper's part for a return (#882) assumes
        lai = LocalAI(a.local_ai.resolve(), work, "plain", {})
        try:
            server = lai.server
            status, base, _ = server.embed(MODEL)
            check("the model loads behind the wrapper and answers", status == 200, f"HTTP {status} {str(base)[:200]}")
            if status != 200:
                return 1
            run = run_ids(receipts, server.last_ids)
            check("its sixteen vectors each carry a receipt, of ONE run", len(run) == 1 and "no receipt" not in run, str(run))
            status, first, _ = server.embed(MODEL, one)
            created = int(kept(receipts).get("created", -1))
            print(f"    the run {next(iter(run))[:23]}...; the wrapper counted {created} context(s) created so far", flush=True)
            status, answer, _ = server.park(MODEL)
            check("(5) a park inside the idle second is 409 model_busy, through the wrapper",
                  status == 409 and park.error_of(answer)[0] == "model_busy", f"HTTP {status} {park.error_of(answer)}")
            time.sleep(1.1)
            status, answer, _ = server.park(MODEL)
            counts, listed = server.counts(MODEL), server.row(MODEL)
            check("(1) a park passes the wrapper: 200, the ENGINE counts it, /system says parked",
                  status == 200 and counts == {"parks": 1, "unparks": 0, "no_room": 0, "parked": 1}
                  and bool(listed) and listed.get("state") == "parked", f"HTTP {status} {counts} {park.describe(listed)}")
            status, back, _ = server.embed(MODEL, one)
            counts = server.counts(MODEL)
            check("the request after it is served by the same process, the weights back: the same vector",
                  status == 200 and back == first and counts == {"parks": 1, "unparks": 1, "no_room": 0, "parked": 0}, str(counts))
            check("(2) the return's LoadModel passed the wrapper as THE LOAD IN FORCE, answered 'Model already loaded'",
                  lai.said(STANDS) == 1 and lai.said("the backend: 'Model already loaded'") == 1, f"{lai.said(STANDS)} line(s)")
            check("(2) and NO second run is stated: one load stated, the receipt after the return names the run before the park",
                  lai.said("loaded on ") == 1 and run_ids(receipts, server.last_ids) == run,
                  f"{lai.said('loaded on ')} load(s) stated; {run_ids(receipts, server.last_ids)}")
            now = kept(receipts, lambda r: int(r.get("created", -1)) == created + 1)
            check("(3) that Embedding carries its receipt, and the wrapper's `created` went up by exactly one",
                  "no receipt" not in run_ids(receipts, server.last_ids) and int(now.get("created", -1)) == created + 1,
                  f"created {created} -> {now.get('created')}")
            check("the engine says the same: one park, one return, in its own words",
                  lai.said("weights parked in") == 1 and lai.said("weights unparked in") == 1,
                  f"{lai.said('weights parked in')} / {lai.said('weights unparked in')}")

            # ---- the rows, each cycle counted by the engine
            row(f"{n} cycles, back by the request", *park.cycles(server, MODEL, n, "request"))
            row(f"{n} cycles, back by /backend/load", *park.cycles(server, MODEL, n, "load"))
            row("a park asked while a request runs", *park.busy(server, MODEL, 512))
            total = 2 * n + 1
            counts = server.counts(MODEL)
            check(f"the engine counted {total} parks and {total} returns in the one process",
                  counts is not None and (counts["parks"], counts["unparks"]) == (total, total), str(counts))
            check(f"the wrapper let {total} repeated loads through, and stated ONE load",
                  lai.said(STANDS) == total and lai.said("loaded on ") == 1,
                  f"{lai.said(STANDS)} repeated, {lai.said('loaded on ')} stated")
            now = kept(receipts, lambda r: int(r.get("created", -1)) == created + total)
            check(f"the wrapper counted {total} contexts more, one a return, and none failed its canary",
                  int(now.get("created", -1)) == created + total and int(now.get("canary_failed", -1)) == 0,
                  f"created {created} -> {now.get('created')}, canary_failed {now.get('canary_failed')}")
            print(f"    the wrapper's count: {json.dumps({k: v for k, v in now.items() if k != 'gaps'})}", flush=True)
            status, again, _ = server.embed(MODEL)
            check("the vectors after it all are the first ones, bit for bit, and VeGa holds the one run",
                  status == 200 and again == base and run_ids(receipts, server.last_ids) == run
                  and len(receipts.keeps.get()) == 1)
            row("a parked model shut down, then loaded afresh", *park.stop_parked(server, MODEL))
            check("the load afresh is STATED: a second load, the same run id (the same weights, engine and options)",
                  lai.said("loaded on ") == 2 and run_ids(receipts, server.last_ids) == run,
                  f"{lai.said('loaded on ')} stated; {run_ids(receipts, server.last_ids) == run}")
        finally:
            lai.stop()

        # ---- a return the device refuses
        lai = LocalAI(a.local_ai.resolve(), work, "refuse", {"GGML_CPU_PARK_TEST_REFUSE": "2"})
        try:
            row("a return refused twice is a deferral twice, then served", *park.refused(lai.server, MODEL))
            check("the wrapper kept the run through the refusals: one load stated, the receipts name it",
                  lai.said("loaded on ") == 1 and run_ids(receipts, lai.server.last_ids) == run,
                  f"{lai.said('loaded on ')} stated, {lai.said(STANDS)} repeated load(s) answered")
        finally:
            lai.stop()

        # ---- (4) a return that FAILS, and is paid as a load
        lai = LocalAI(a.local_ai.resolve(), work, "broken", {"GGML_CPU_PARK_TEST_BROKEN": "1"})
        try:
            passed, lines = park.cycles(lai.server, MODEL, 3, "request")
            row("a FAILED return is not counted as a cycle (the row must FAIL, saying the counts started again)",
                not passed and "the engine's counts started again" in lines[-1], lines)
            wrappers, stated = lai.said("behind VeGa: listening on port"), lai.said("loaded on ")
            after = run_ids(receipts, lai.server.last_ids)
            check("(4) the load that paid for it is a NEW wrapper process, which states its load",
                  wrappers == 2 and stated == 2, f"{wrappers} wrapper process(es), {stated} load(s) stated")
            check("(4) and the run id is THE SAME: nothing of the weights, the engine or the options changed",
                  after == run, f"before {sorted(run)}, after {sorted(after)}")
            now = kept(receipts)
            print(f"    the wrapper's count after it: {json.dumps({k: v for k, v in now.items() if k != 'gaps'})}", flush=True)
            check("(4) the wrapper's counts started again with the new process, as the engine's did",
                  0 <= int(now.get("calls", -1)) <= 16 + 16, f"calls {now.get('calls')}")
            row("a failed return costs a load, not an error", *park.failed_return(lai.server, MODEL))
        finally:
            lai.stop()
    finally:
        receipts.stop()
    print(f"{sum(results)} of {len(results)}; the logs: {work}/localai-*.log, {work}/wrapper-*.log", flush=True)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
