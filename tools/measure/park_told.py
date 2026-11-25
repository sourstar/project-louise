#!/usr/bin/env python3
"""Parking when TOLD, through the backend's own gRPC calls (step 2,
docs/notes/parking-step-2.md §3.1): `Free` parks a model that can park, a
`LoadModel` for the model already held brings it back, a request brings it
back by itself, `Status` says where the weights are.

Two uses.

ON A NODE, against a COPY's backend (never a live model: LocalAI does not know
its model was parked behind its back — harmless, the next request brings it
back, but its `/system` goes on saying loaded):

    python3 -I tools/measure/park_told.py status ADDR
    python3 -I tools/measure/park_told.py free   ADDR
    python3 -I tools/measure/park_told.py load   ADDR MODEL --options FILE
    python3 -I tools/measure/park_told.py cycles ADDR MODEL -n 20 --return request
    python3 -I tools/measure/park_told.py cycles ADDR MODEL -n 20 --return load --options FILE

ADDR is the backend's own address (LocalAI's log, or the `--addr` of its
process); MODEL the name LocalAI loaded it under. A LoadModel for the model a
backend already holds is answered "Model already loaded" ONLY to the options
it was loaded with: FILE is those options as JSON, the model's `load_options`
of LocalAI's `GET /system` (what LocalAI sent; behind a wrapper that rewrites
options, ask through the wrapper's address, or the backend answers
FAILED_PRECONDITION naming the fields that differ). `cycles` embeds sixteen
sentences, then N times parks, returns (by a LoadModel, or by the first
request), embeds them again and compares the vectors bit for bit with the
first; it prints what each park and each return took. With
`GGML_SYCL_PARK_VERIFY=1` in the model's env the engine also reads the card's
bytes back at every return; with `GGML_SYCL_PARK_REFUSE=N` the next N returns
are refused and `cycles` reports them (RESOURCE_EXHAUSTED, the model still
parked, the cycle after served). A return that FAILS - the device failing, or
bytes read back that are not the ones parked - is INTERNAL, and stops the run:
it is never counted as a refusal.

WHERE THERE IS NO CARD, the whole exchange against a CPU stand-in
(tools/measure/park_cpu_standin.patch, never part of an engine), built into
LocalAI's own grpc-server:

    python3 -I tools/measure/park_told.py selftest <grpc-server> <model.gguf>

The stubs: generated from tests/fixtures/vega/backend.proto with grpc_tools,
or `--stubs DIR` for a directory that holds backend_pb2.py (a vega package).
A backend that asks for a token: `--token-file PATH` (the token is never
printed).
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "tests" / "fixtures" / "vega" / "backend.proto"
TEXTS = [f"sentence number {i}: the quick brown fox jumps over the lazy dog, {i} times" for i in range(16)]
#: the backend's answer to a LoadModel for the model it already holds, parked or not: its bytes are an interface
ALREADY_LOADED = "Model already loaded"
PARKED = "parked"
#: the head of a refusal's message: RESOURCE_EXHAUSTED alone has other meanings on LocalAI's wire
NO_ROOM = "no_room_on_device: "


def stubs(directory: str | None):
    """(grpc, backend_pb2, backend_pb2_grpc), from a directory or generated."""
    import grpc
    if directory is None:
        from grpc_tools import protoc
        directory = tempfile.mkdtemp(prefix="park-stubs-")
        done = protoc.main(["protoc", f"-I{PROTO.parent}", f"--python_out={directory}",
                            f"--grpc_python_out={directory}", str(PROTO)])
        if done != 0:
            raise SystemExit(f"the stubs could not be generated from {PROTO}")
    sys.path.insert(0, directory)
    return grpc, importlib.import_module("backend_pb2"), importlib.import_module("backend_pb2_grpc")


class Backend:
    def __init__(self, addr: str, stubs_dir: str | None = None, token_file: str | None = None):
        self.grpc, self.pb, pb_grpc = stubs(stubs_dir)
        self.stub = pb_grpc.BackendStub(self.grpc.insecure_channel(addr, options=[
            ("grpc.max_receive_message_length", 256 << 20)]))
        token = Path(token_file).read_text().strip() if token_file else ""
        self.metadata = [("authorization", f"Bearer {token}")] if token else []

    def _call(self, method, request, timeout: float = 300.0):
        """(code name, the answer or None, the status' message, seconds)."""
        t0 = time.monotonic()
        try:
            answer = method(request, timeout=timeout, metadata=self.metadata)
            return "OK", answer, "", time.monotonic() - t0
        except self.grpc.RpcError as e:
            return e.code().name, None, e.details() or "", time.monotonic() - t0

    def free(self):
        return self._call(self.stub.Free, self.pb.HealthMessage())

    def load(self, model: str, **options):
        """LoadModel. For the model a backend already holds, `options` must be the ones of its load."""
        return self._call(self.stub.LoadModel, self.pb.ModelOptions(Model=model, **options))

    def options_from(self, path: str) -> dict:
        """a model's `load_options` of LocalAI's GET /system, as the keyword arguments of `load`"""
        from google.protobuf import json_format
        message = json_format.ParseDict(json.loads(Path(path).read_text()), self.pb.ModelOptions())
        return {field.name: value for field, value in message.ListFields() if field.name != "Model"}

    def stream(self, prompt: str, tokens: int, on_first=None):
        """PredictStream. (code, number of chunks, the status' message); on_first runs when the first chunk is in."""
        n = 0
        try:
            for _reply in self.stub.PredictStream(self.pb.PredictOptions(Prompt=prompt, Tokens=tokens, Temperature=0.0),
                                                  timeout=300.0, metadata=self.metadata):
                n += 1
                if n == 1 and on_first is not None:
                    on_first()
            return "OK", n, ""
        except self.grpc.RpcError as e:
            return e.code().name, n, e.details() or ""

    def status(self) -> tuple[str, dict]:
        code, answer, detail, _ = self._call(self.stub.Status, self.pb.HealthMessage(), timeout=30.0)
        if answer is None:
            return code, {"detail": detail}
        return code, {"state": self.pb.StatusResponse.State.Name(answer.state), "total": answer.memory.total,
                      **{k: int(v) for k, v in sorted(answer.memory.breakdown.items())}}

    def embed(self, text: str):
        code, answer, detail, seconds = self._call(self.stub.Embedding, self.pb.PredictOptions(Embeddings=text))
        return code, (list(answer.embeddings) if answer is not None else detail), seconds

    def embed_all(self) -> tuple[str, list | str, float]:
        """every sentence; the first code that is not OK ends it. (code, vectors or the detail, seconds of the first)"""
        out, first = [], 0.0
        for i, text in enumerate(TEXTS):
            code, got, seconds = self.embed(text)
            if i == 0:
                first = seconds
            if code != "OK":
                return code, got, first
            out.append(got)
        return "OK", out, first

    def wait(self, seconds: float = 120.0) -> None:
        deadline = time.monotonic() + seconds
        while True:
            code, _, _, _ = self._call(self.stub.Health, self.pb.HealthMessage(), timeout=2.0)
            if code == "OK":
                return
            if time.monotonic() > deadline:
                raise SystemExit(f"the backend does not answer Health: {code}")
            time.sleep(0.2)


def _meta(answer) -> dict:
    raw = getattr(answer, "metadata", b"") if answer is not None else b""
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def cycles(b: Backend, model: str, n: int, back: str, say=print, options: dict | None = None) -> dict:
    """N times: park, return, the sixteen sentences again. What happened, counted. `options`: the ones of the
    model's load, for the return by LoadModel."""
    code, base, _ = b.embed_all()
    if code != "OK":
        raise SystemExit(f"the model does not answer before any park: {code} {base}")
    out = {"cycles": 0, "same": 0, "refused": 0, "park_ms": [], "return_ms": [], "first_ms": [], "wrong": []}
    for i in range(1, n + 1):
        code, answer, detail, _ = b.free()
        if code != "OK" or answer.message != PARKED or not answer.success:
            raise SystemExit(f"cycle {i}: Free answered {code} {detail or answer.message!r}: the model does not park")
        out["park_ms"].append(float(_meta(answer).get("ms", 0.0)))
        state = b.status()[1]
        if state.get("parked") != 1 or state.get("parked_host") != state.get("park_size"):
            raise SystemExit(f"cycle {i}: parked, and Status says {state}")
        if back == "load":
            code, answer, detail, seconds = b.load(model, **(options or {}))
            if code == "RESOURCE_EXHAUSTED":
                out["refused"] += 1
                if b.status()[1].get("parked") != 1:
                    raise SystemExit(f"cycle {i}: the return was refused and the model is not parked any more")
                say(f"cycle {i}: the return was REFUSED ({detail}); still parked")
                continue
            if code != "OK" or answer.message != ALREADY_LOADED or not answer.success:
                raise SystemExit(f"cycle {i}: LoadModel answered {code} {detail or answer.message!r}")
            out["return_ms"].append(seconds * 1000.0)
            if b.status()[1].get("parked") != 0:
                raise SystemExit(f"cycle {i}: returned, and Status still says parked")
        code, got, first = b.embed_all()
        if code == "RESOURCE_EXHAUSTED":
            out["refused"] += 1
            say(f"cycle {i}: the request was REFUSED ({got}); parked: {b.status()[1].get('parked')}")
            continue
        if code != "OK":
            raise SystemExit(f"cycle {i}: the request answered {code} {got}")
        out["first_ms"].append(first * 1000.0)
        out["cycles"] += 1
        if got == base:
            out["same"] += 1
        else:
            out["wrong"].append(i)
    return out


def report(out: dict, back: str) -> str:
    def mid(values: list[float]) -> str:
        return f"{statistics.median(values):.1f} ms (median of {len(values)})" if values else "-"
    return (f"{out['cycles']} cycles answered, {out['same']} with the first vectors bit for bit"
            f"{', WRONG at ' + str(out['wrong']) if out['wrong'] else ''}; {out['refused']} refused\n"
            f"  the park, as the engine timed it: {mid(out['park_ms'])}\n"
            + (f"  the return by LoadModel:          {mid(out['return_ms'])}\n" if back == "load" else "")
            + f"  the first sentence after it:      {mid(out['first_ms'])}"
            + (" (it brings the weights back and creates its context)" if back == "request" else ""))


# ---- the selftest: the whole exchange against a CPU stand-in

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Process:
    def __init__(self, binary: str, env: dict[str, str], stubs_dir: str | None):
        self.addr = f"127.0.0.1:{_free_port()}"
        self.log = Path(tempfile.mkstemp(prefix="park-grpc-", suffix=".log")[1])    # under TMPDIR
        self.proc = subprocess.Popen([binary, "--addr", self.addr], stdout=self.log.open("w"),
                                     stderr=subprocess.STDOUT, env=dict(os.environ, **env))
        self.backend = Backend(self.addr, stubs_dir)
        self.backend.wait()

    def text(self) -> str:
        return self.log.read_text(errors="replace")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def selftest(binary: str, model: str, stubs_dir: str | None, chat_model: str | None = None) -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(("ok:   " if ok else "FAIL: ") + name + (f"  [{detail}]" if detail else ""), flush=True)

    name = "park-selftest"
    load = dict(ModelFile=model, ContextSize=512, NBatch=512, Threads=4, Embeddings=True, MMap=False)
    # every tensor in the plain CPU buffer type: a CPU's own repacking (AMX, AVX-512) puts weights in buffers of
    # another type, which the stand-in does not park - as llama-server's --no-repack in park_cpu_test.py
    on_demand = ["context_on_demand:true", "keep_context_ms:0", "verbosity:4", "tensor_buft_overrides:.*=CPU"]
    plain = dict(load, Options=["verbosity:4"])        # the options of each load, to repeat it with
    parks = dict(load, Options=on_demand)

    # A. a model that CANNOT park: the backend answers as it always did
    p = Process(binary, {}, stubs_dir)
    b = p.backend
    code, answer, detail, _ = b.load(name, **plain)
    check("a model loads", code == "OK" and answer.success, f"{code} {detail}")
    code, base, _ = b.embed_all()
    check("and answers", code == "OK" and len(base) == len(TEXTS))
    code, _, detail, _ = b.free()
    check("Free on a model that cannot park: UNIMPLEMENTED, as a backend with no Free", code == "UNIMPLEMENTED", f"{code} {detail}")
    code, state = b.status()
    check("Status on it: UNIMPLEMENTED, the caller samples the process as before", code == "UNIMPLEMENTED", f"{code} {state}")
    code, answer, detail, _ = b.load(name, **plain)
    check("LoadModel again, with the options of the load: the same message, byte for byte, and nothing done",
          code == "OK" and answer.success and answer.message.encode() == ALREADY_LOADED.encode(), f"{code} {detail}")
    # "already loaded" is said only to the options that ARE loaded
    code, _, detail, _ = b.load(name, **dict(plain, ContextSize=1024))
    check("the same name under ANOTHER context size: FAILED_PRECONDITION, the field named and not its value",
          code == "FAILED_PRECONDITION" and "ContextSize" in detail and "1024" not in detail and "512" not in detail
          and ALREADY_LOADED not in detail and "did not load again" in detail, f"{code} {detail}")
    code, _, detail, _ = b.load(name, **dict(plain, ModelFile="/elsewhere/s3cr3t-in-a-path.gguf", Options=["verbosity:1"]))
    check("under another file and other options: both fields named, nothing of their contents",
          code == "FAILED_PRECONDITION" and "ModelFile" in detail and "Options" in detail and "s3cr3t" not in detail
          and "verbosity" not in detail, f"{code} {detail}")
    code, _, detail, _ = b.load(name)
    check("with no options at all: refused the same way, as any other options", code == "FAILED_PRECONDITION" and "ModelFile" in detail,
          f"{code} {detail[:90]}")
    code, _, detail, _ = b.load("another-model")
    check("LoadModel for another model: FAILED_PRECONDITION, as before", code == "FAILED_PRECONDITION", f"{code} {detail[:60]}")
    code, got, _ = b.embed_all()
    check("and the model answers as ever", code == "OK" and got == base)
    p.stop()

    # B. a model that can park
    p = Process(binary, {"GGML_CPU_PARK_TEST": "1"}, stubs_dir)
    b = p.backend
    code, answer, detail, _ = b.load(name, **parks)
    check("a model that can park loads", code == "OK" and answer.success and "this model can park" in p.text(), f"{code} {detail}")
    code, got, _ = b.embed_all()
    check("and answers the same vectors as the one that cannot", code == "OK" and got == base)
    code, state = b.status()
    size = state.get("park_size", 0)
    check("Status: READY, on the device, the bytes that park - and the process's own memory, as the monitor reads it",
          code == "OK" and state.get("state") == "READY" and state.get("parked") == 0 and state.get("parked_host") == 0
          and size > 1_000_000 and state.get("total", 0) > state.get("rss", 0) > size, str(state))
    code, answer, detail, _ = b.free()
    meta = _meta(answer)
    check("Free: OK, \"parked\", and its metadata says the bytes", code == "OK" and answer.success and answer.message == PARKED
          and meta.get("parked") is True and meta.get("park_size") == size, f"{code} {detail} {meta}")
    code, state = b.status()
    check("Status: parked, the bytes on the host", code == "OK" and state.get("parked") == 1 and state.get("parked_host") == size
          and state.get("parks") == 1 and state.get("total", 0) > 0, str(state))
    code, answer, _, _ = b.free()
    check("Free again: still parked, nothing done twice", code == "OK" and answer.message == PARKED and b.status()[1].get("parks") == 1)
    code, _, detail, _ = b.load(name, **dict(parks, ContextSize=1024))
    check("LoadModel for the PARKED model under other options: FAILED_PRECONDITION, and the weights do NOT come back",
          code == "FAILED_PRECONDITION" and "ContextSize" in detail and b.status()[1].get("parked") == 1
          and b.status()[1].get("unparks") == 0, f"{code} {detail[:100]}")
    code, answer, detail, _ = b.load(name, **parks)
    check("LoadModel for the parked model: the weights come back, then the SAME message, byte for byte",
          code == "OK" and answer.success and answer.message.encode() == ALREADY_LOADED.encode()
          and b.status()[1].get("parked") == 0 and b.status()[1].get("unparks") == 1, f"{code} {detail}")
    code, got, _ = b.embed_all()
    check("and it answers the first vectors bit for bit", code == "OK" and got == base)
    b.free()
    code, got, _ = b.embed_all()
    check("a request to a parked model brings it back by itself", code == "OK" and got == base and b.status()[1].get("parked") == 0)
    code, _, detail, _ = b.load("another-model")
    check("LoadModel for another model: FAILED_PRECONDITION, parked or not", code == "FAILED_PRECONDITION", f"{code} {detail[:60]}")
    for back in ("load", "request"):
        out = cycles(b, name, 6, back, say=lambda _line: None, options=parks)
        check(f"6 cycles, the return by {back}: every one the first vectors", out["cycles"] == 6 and out["same"] == 6, report(out, back))
    text = p.text()
    # two parks above (the Free told twice parked once), then six and six
    check("every park poisoned the stand-in's buffer: the vectors came from weights that had left",
          text.count("the buffer poisoned") == b.status()[1].get("parks") == 14, f"{text.count('the buffer poisoned')} poisonings")
    p.stop()

    # C. no room: the return is refused as what it is, the model stays parked, and the next one is served
    p = Process(binary, {"GGML_CPU_PARK_TEST": "1", "GGML_CPU_PARK_TEST_REFUSE": "2"}, stubs_dir)
    b = p.backend
    b.load(name, **parks)
    b.free()
    code, answer, detail, _ = b.load(name, **parks)
    check("LoadModel with no room: RESOURCE_EXHAUSTED, never the message that says loaded",
          code == "RESOURCE_EXHAUSTED" and detail.startswith(NO_ROOM) and "cannot come back" in detail
          and ALREADY_LOADED not in detail, f"{code} {detail}")
    check("the model is still parked", b.status()[1].get("parked") == 1)
    # ONE sentence: each asks for the weights again, and the stand-in counts refusals
    code, detail, _ = b.embed(TEXTS[0])
    check("a request with no room: RESOURCE_EXHAUSTED, never vectors",
          code == "RESOURCE_EXHAUSTED" and str(detail).startswith(NO_ROOM) and "cannot come back" in str(detail),
          f"{code} {str(detail)[:80]}")
    check("still parked, the same process", b.status()[1].get("parked") == 1 and p.proc.poll() is None)
    code, got, _ = b.embed_all()
    check("the next request is served, the first vectors", code == "OK" and got == base and b.status()[1].get("parked") == 0)
    code, answer, detail, _ = b.load(name, **parks)
    check("and LoadModel answers the message again", code == "OK" and answer.message == ALREADY_LOADED)
    check("the two refusals were counted, and they are not parks or returns", b.status()[1].get("no_room") == 2
          and b.status()[1].get("parks") == 1 and b.status()[1].get("unparks") == 1, str(b.status()[1]))
    p.stop()

    # D. Free WHILE requests come: each request is answered whole with the first vectors, and each Free is either
    # done or refused as busy (FAILED_PRECONDITION: for now, the model stays - not Unimplemented, which is for good)
    p = Process(binary, {"GGML_CPU_PARK_TEST": "1"}, stubs_dir)
    b = p.backend
    b.load(name, **parks)
    asker = Backend(p.addr, stubs_dir)
    answers: list[tuple[str, object]] = []
    stop = threading.Event()

    def ask() -> None:
        k = 0
        while not stop.is_set():
            code, got, _ = asker.embed_all()
            answers.append((code, got))
            k += 1
            time.sleep(0.004 * (k % 7))

    worker = threading.Thread(target=ask)
    worker.start()
    seen: dict[str, int] = {}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        code, answer, detail, _ = b.free()
        key = PARKED if code == "OK" and answer.message == PARKED else f"{code} {detail.split(':')[0]}"
        seen[key] = seen.get(key, 0) + 1
        time.sleep(0.007)
    stop.set()
    worker.join()
    bad = [(c, str(g)[:60]) for c, g in answers if c != "OK" or g != base]
    check(f"{len(answers)} requests beside {sum(seen.values())} calls of Free: every one the first vectors",
          not bad and len(answers) >= 5, f"{len(bad)} wrong: {bad[:2]}")
    check("each Free was done, or refused FAILED_PRECONDITION busy - nothing else",
          set(seen) <= {PARKED, "FAILED_PRECONDITION busy"} and seen.get(PARKED, 0) >= 3, str(seen))
    check("the process is alive and its counts agree", p.proc.poll() is None
          and b.status()[1].get("parks") == p.text().count("weights parked in"))
    p.stop()

    # E. a park that FAILS (no host memory for the copy): INTERNAL - a fault, the caller stops the process as it would
    # have - and nothing is half done
    p = Process(binary, {"GGML_CPU_PARK_TEST": "1", "GGML_CPU_PARK_TEST_NO_HOST": "1"}, stubs_dir)
    b = p.backend
    b.load(name, **parks)
    code, _, detail, _ = b.free()
    check("Free that cannot copy the weights out: INTERNAL, and it says they stay", code == "INTERNAL" and "stay on the device" in detail,
          f"{code} {detail}")
    check("Status: on the device, nothing parked", b.status()[1].get("parked") == 0 and b.status()[1].get("parks") == 0)
    code, got, _ = b.embed_all()
    check("and the model answers the first vectors", code == "OK" and got == base)
    code, answer, _, _ = b.free()
    check("Free again parks", code == "OK" and answer.message == PARKED and b.status()[1].get("parked") == 1)
    p.stop()

    # F. a return that FAILS is not a refusal - the device failing, or bytes read back that are not the ones parked:
    # INTERNAL, a fault, never RESOURCE_EXHAUSTED, never counted as no room - and the model stays parked
    p = Process(binary, {"GGML_CPU_PARK_TEST": "1", "GGML_CPU_PARK_TEST_BROKEN": "2"}, stubs_dir)
    b = p.backend
    b.load(name, **parks)
    b.free()
    code, _, detail, _ = b.load(name, **parks)
    check("LoadModel whose return FAILS: INTERNAL, not a refusal", code == "INTERNAL" and "a failure, not a refusal" in detail
          and not detail.startswith(NO_ROOM), f"{code} {detail}")
    code, detail, _ = b.embed(TEXTS[0])
    check("a request whose return FAILS: INTERNAL, never RESOURCE_EXHAUSTED", code == "INTERNAL"
          and "a failure, not a refusal" in str(detail), f"{code} {str(detail)[:90]}")
    state = b.status()[1]
    check("the model is still parked, nothing counted as a refusal or a return", state.get("parked") == 1
          and state.get("no_room") == 0 and state.get("unparks") == 0 and p.proc.poll() is None, str(state))
    code, got, _ = b.embed_all()
    check("when the return works again the model answers the first vectors", code == "OK" and got == base)
    p.stop()

    # G. A REQUEST NEVER PARKS: the weights came back for a request whose context is then refused - they stay on
    # the device, are not copied out again, and Status says where they are
    p = Process(binary, {"GGML_CPU_PARK_TEST": "1", "LLAMA_SERVER_CTX_CREATE_FAIL": "1"}, stubs_dir)
    b = p.backend
    b.load(name, **parks)
    b.free()
    code, detail, _ = b.embed(TEXTS[0])
    check("the weights come back, the context is refused: RESOURCE_EXHAUSTED no_room_on_device",
          code == "RESOURCE_EXHAUSTED" and str(detail).startswith(NO_ROOM) and "context cannot be created" in str(detail),
          f"{code} {str(detail)[:90]}")
    state = b.status()[1]
    check("the weights STAY on the device, and Status says so: no second park, no second copy",
          state.get("parked") == 0 and state.get("parked_host") == 0 and state.get("parks") == 1 and state.get("unparks") == 1
          and state.get("no_room") == 1 and p.text().count("the buffer poisoned") == 1, str(state))
    code, got, _ = b.embed_all()
    check("the next request answers the first vectors and brings nothing back again",
          code == "OK" and got == base and b.status()[1].get("unparks") == 1)
    p.stop()

    # H. a STREAM that fails after its first chunk fails: it was ended OK, a generation cut short delivered as a
    # complete answer (needs a model that generates, and the stand-in that refuses a graph)
    if chat_model:
        arm = Path(tempfile.mkdtemp(prefix="park-arm-")) / "arm"
        p = Process(binary, {"GGML_CPU_REFUSE_GRAPHS": "1", "GGML_CPU_REFUSE_ARM": str(arm)}, stubs_dir)
        b = p.backend
        code, answer, detail, _ = b.load("park-selftest-chat", ModelFile=chat_model, ContextSize=256, NBatch=64, Threads=4,
                                         MMap=False, Options=["verbosity:4"])
        check("a model that generates loads", code == "OK" and answer.success, f"{code} {detail}")
        code, n, detail = b.stream("Once upon a time", 48)
        check("a stream with no failure ends OK", code == "OK" and n > 4, f"{code}, {n} chunks {detail}")
        whole = n
        code, n, detail = b.stream("Once upon a time", 48, on_first=lambda: arm.write_text("armed"))
        check("a stream refused a graph after its first chunk ENDS AS A REFUSAL, not as a complete answer",
              code == "RESOURCE_EXHAUSTED" and detail.startswith(NO_ROOM) and 1 <= n < whole, f"{code}, {n} of {whole} chunks: {detail}")
        code, n, detail = b.stream("Once upon a time", 48)
        check("the next stream is whole again, the same process", code == "OK" and n == whole and p.proc.poll() is None,
              f"{code}, {n} chunks")
        p.stop()

    print(f"{sum(results)} of {len(results)} passed")
    return 0 if all(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stubs", help="a directory that holds backend_pb2.py and backend_pb2_grpc.py")
    ap.add_argument("--token-file", help="the file that holds the backend's token")
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb in ("status", "free"):
        sub.add_parser(verb).add_argument("addr")
    for verb in ("load", "cycles"):
        sp = sub.add_parser(verb)
        sp.add_argument("addr")
        sp.add_argument("model")
        sp.add_argument("--options", help="the options of the model's load, as JSON: its `load_options` of LocalAI's GET /system")
    sub.choices["cycles"].add_argument("-n", type=int, default=20)
    sub.choices["cycles"].add_argument("--return", dest="back", choices=("load", "request"), default="request")
    sp = sub.add_parser("selftest")
    sp.add_argument("binary")
    sp.add_argument("gguf")
    sp.add_argument("--chat-gguf", help="a small model that generates, for the stream that fails after its first chunk")
    a = ap.parse_args()

    if a.verb == "selftest":
        return selftest(a.binary, a.gguf, a.stubs, a.chat_gguf)
    b = Backend(a.addr, a.stubs, a.token_file)
    if a.verb == "status":
        code, state = b.status()
        print(code, json.dumps(state))
        return 0 if code == "OK" else 1
    if a.verb == "free":
        code, answer, detail, seconds = b.free()
        print(code, detail or answer.message, json.dumps(_meta(answer)), f"{seconds * 1000:.1f} ms")
        return 0 if code == "OK" else 1
    options = b.options_from(a.options) if a.options else None
    if a.verb == "load" or a.back == "load":
        if options is None:
            raise SystemExit("a LoadModel for the model a backend already holds is answered only to the options of its "
                             "load: give them with --options FILE (the model's `load_options` of LocalAI's GET /system)")
    if a.verb == "load":
        code, answer, detail, seconds = b.load(a.model, **options)
        print(code, detail or answer.message, f"{seconds * 1000:.1f} ms")
        return 0 if code == "OK" else 1
    out = cycles(b, a.model, a.n, a.back, options=options)
    print(report(out, a.back))
    return 0 if out["cycles"] + out["refused"] == a.n and not out["wrong"] else 1


if __name__ == "__main__":
    sys.exit(main())
