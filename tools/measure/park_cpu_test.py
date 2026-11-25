#!/usr/bin/env python3
"""What sits ABOVE the park hook, tested where there is no card: llama_model_park /
unpark and the server's lifecycle (the weights leave when the context is freed, come
back for the next request, a refusal leaves the model parked, the canary catches
weights that did not come back) - and, from F on, parking when TOLD (step 2,
docs/notes/parking-step-2.md §3.1): POST /park and /unpark of llama.cpp's own
server, which post the task LocalAI's backend posts for Free and a repeated
LoadModel.

The device is a STAND-IN, tools/measure/park_cpu_standin.patch (never part of an
engine): with GGML_CPU_PARK_TEST=1 a CPU weight buffer "parks" by copying its bytes
aside and POISONING itself with 0xA5, so anything that reads it while parked, or after
a bad unpark, computes garbage. GGML_CPU_PARK_TEST_REFUSE=N refuses the next N
unparks (a device with no room); GGML_CPU_PARK_TEST_LOSE=N makes the next N succeed
without putting the bytes back.

The server under test: llama.cpp at the series' pin with the series' llama.cpp
patches, staged 0012-0015's (from F on: the park engine's), then the stand-in; built
for CPU (no web UI). Any small embedding GGUF serves (run 2026-11-24 on
bge-small-en-v1.5 f16: 16 of 16; 2026-11-25 with the told sections: see the note).
docs/notes/parking-prototype.md §3.

    python3 -I tools/measure/park_cpu_test.py <llama-server> <model.gguf>
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

TEXTS = [f"sentence number {i}: the quick brown fox jumps over the lazy dog, {i} times" for i in range(16)]
BASE = ["--embedding", "--load-mode", "none", "--no-repack", "-c", "512", "-b", "512", "-ub", "512", "-np", "1", "-t", "4",
        "--no-webui", "--metrics", "--log-verbosity", "4"]   # 4: the library's own lines too
ON_DEMAND = ["--context-on-demand", "--keep-context-ms", "0"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, binary: str, model: str, args: list[str], env: dict[str, str]):
        self.port = free_port()
        self.log = Path(tempfile.mkstemp(prefix="park-", suffix=".log")[1])
        self.proc = subprocess.Popen(
            [binary, "-m", model, "--host", "127.0.0.1", "--port", str(self.port), *args],
            stdout=self.log.open("w"), stderr=subprocess.STDOUT, env=dict(os.environ, **env))
        deadline = time.monotonic() + 120
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"the server did not start:\n{self.text()[-3000:]}")
            time.sleep(0.2)

    def text(self) -> str:
        return self.log.read_text(errors="replace")

    def count(self, what: str) -> int:
        return self.text().count(what)

    def wait_for(self, what: str, n: int, seconds: float = 30.0) -> None:
        deadline = time.monotonic() + seconds
        while self.count(what) < n:
            if time.monotonic() > deadline:
                raise AssertionError(f"{what!r} seen {self.count(what)} times, expected {n}:\n{self.text()[-2500:]}")
            time.sleep(0.05)

    def embed(self, texts: list[str] | None = None) -> tuple[int, object]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/embeddings",
                                     data=json.dumps({"input": texts or TEXTS}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = json.loads(r.read())
                return r.status, [d["embedding"] for d in body["data"]]
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def post(self, path: str) -> tuple[int, dict]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def metric(self, name: str) -> float:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/metrics", timeout=30) as r:
            for line in r.read().decode().splitlines():
                if line.startswith(f"llamacpp:{name} "):
                    return float(line.split()[1])
        raise AssertionError(f"no metric {name}")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def _type(body: object) -> str:
    return str((body.get("error") or {}).get("type")) if isinstance(body, dict) else ""


def main() -> int:
    binary, model = sys.argv[1], sys.argv[2]
    park = {"GGML_CPU_PARK_TEST": "1", "LLAMA_SERVER_PARK_WEIGHTS": "1"}
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(("ok:   " if ok else "FAIL: ") + name + (f"  [{detail}]" if detail else ""), flush=True)

    # A. the vectors without parking: context on demand as the series has it
    s = Server(binary, model, BASE + ON_DEMAND, {})
    status, base = s.embed()
    check("baseline answers", status == 200 and len(base) == len(TEXTS))
    status, again = s.embed()
    check("baseline reproduces itself", status == 200 and again == base)
    check("no parking without the switch", s.count("weights parked") == 0)
    s.stop()

    # B. N cycles: each request finds the model parked, brings it back, and answers the same vectors
    n = 12
    s = Server(binary, model, BASE + ON_DEMAND, park)
    check("the server says it parks", s.count("parking: when the context is freed the weights leave the device") == 1)
    same = True
    for i in range(1, n + 1):
        status, got = s.embed()
        same = same and status == 200 and got == base
        s.wait_for("weights parked in", i + 1 if s.count("weights parked in") > i else i)
    s.wait_for("weights parked in", n)
    text = s.text()
    parked, unparked = text.count("weights parked in"), text.count("weights unparked in")
    check(f"{n} requests, each on weights that had been parked, give the baseline's vectors bit for bit", same)
    check("every request found the weights parked and brought them back", unparked == n and parked >= n,
          f"parked {parked}, unparked {unparked}")
    check("the stand-in poisoned the buffer at every park", text.count("the buffer poisoned") == parked,
          f"{text.count('the buffer poisoned')} poisonings")
    check("every context created after an unpark reproduced the canary",
          text.count("its canary reproduced") >= unparked and "does not reproduce the canary" not in text)
    s.stop()

    # C. a device with no room: the request is refused, the model stays parked, the next one is served
    # (with a keep of 0 the load's own context is freed, and the weights parked, before the first request)
    s = Server(binary, model, BASE + ON_DEMAND, dict(park, GGML_CPU_PARK_TEST_REFUSE="1"))
    s.wait_for("weights parked in", 1)
    status, body = s.embed()
    check("with no room the request is refused, not answered wrong", status == 503, f"HTTP {status}: {str(body)[:120]}")
    check("and the refusal says what it is: no_room_on_device", _type(body) == "no_room_on_device", str(body)[:160])
    check("and the model is still parked", s.count("the weights cannot come back to the device, is the memory") == 1)
    status, got = s.embed()
    check("the next request brings the weights back and answers the baseline", status == 200 and got == base)
    s.stop()

    # D. weights that did NOT come back (the stand-in loses them once): the canary refuses the context
    s = Server(binary, model, BASE + ON_DEMAND, dict(park, GGML_CPU_PARK_TEST_LOSE="1"))
    s.wait_for("weights parked in", 1)
    status, body = s.embed()
    check("weights that came back wrong are caught by the canary and the request is refused",
          status == 503 and "canary" in json.dumps(body), f"HTTP {status}: {str(body)[:160]}")
    s.stop()

    # E. the switch without what it needs is ignored, and says so
    s = Server(binary, model, BASE, park)        # no context on demand
    check("without context on demand the switch is ignored", s.count("needs context on demand") == 1)
    status, got = s.embed()
    check("and the model answers as ever", status == 200 and got == base and s.count("weights parked") == 0)
    s.stop()
    mapped = ["mmap" if a == "none" else a for a in BASE]
    s = Server(binary, model, mapped + ON_DEMAND, {"LLAMA_SERVER_PARK_WEIGHTS": "1"})   # weights that cannot park
    check("weights that cannot park: the switch is ignored", s.count("cannot be parked on this backend") == 1)
    status, got = s.embed()
    check("and the model answers as ever", status == 200 and got == base and s.count("weights parked") == 0)
    s.stop()

    # ---- PARKING WHEN TOLD (step 2). No measuring switch from here on.
    told = {"GGML_CPU_PARK_TEST": "1"}

    # F. between two requests the weights STAY; they leave when told, and a request brings them back by itself
    s = Server(binary, model, BASE + ON_DEMAND, told)
    check("the server says the model can park, and how much", s.count("parking: this model can park,") == 1)
    size = s.metric("weights_park_bytes")
    check("the bytes that park are counted", size > 1_000_000, f"{size:.0f} bytes")
    status, got = s.embed()
    s.wait_for("context freed in", 2)        # the load's own context, then this request's (a keep of 0)
    check("a request, its context freed: the weights are NOT parked by that", status == 200 and got == base
          and s.count("weights parked in") == 0 and s.metric("weights_parked") == 0)
    status, body = s.post("/park")
    check("told to park: parked, and it says the bytes", status == 200 and body.get("parked") is True
          and body.get("park_size") == size and s.count("weights parked in") == 1 and s.metric("weights_parked") == 1,
          f"HTTP {status}: {body}")
    status, body = s.post("/park")
    check("told again: still parked, nothing done twice", status == 200 and body.get("parked") is True
          and s.count("weights parked in") == 1)
    status, got = s.embed()
    check("a request to a parked model brings the weights back by itself and answers the baseline",
          status == 200 and got == base and s.count("weights unparked in") == 1)
    s.wait_for("context freed in", 3)
    check("and after it the model is on the device, not parked again", s.metric("weights_parked") == 0
          and s.count("weights parked in") == 1)
    s.post("/park")
    status, body = s.post("/unpark")
    check("told to come back: on the device", status == 200 and body.get("parked") is False
          and s.count("weights unparked in") == 2 and s.metric("weights_parked") == 0, f"HTTP {status}: {body}")
    status, body = s.post("/unpark")
    check("told again: nothing to do", status == 200 and body.get("parked") is False and s.count("weights unparked in") == 2)
    same = True
    for _ in range(8):
        same = same and s.post("/park")[0] == 200
        status, got = s.embed()
        same = same and status == 200 and got == base
    text = s.text()
    check("8 more cycles told: the baseline's vectors bit for bit, each on weights that had left",
          same and text.count("weights parked in") == 10 and text.count("the buffer poisoned") == 10,
          f"parked {text.count('weights parked in')}, poisoned {text.count('the buffer poisoned')}")
    check("counted", s.metric("weights_parks_total") == 10 and s.metric("weights_unparks_total") == 10)
    s.stop()

    # G. a context that is being KEPT is freed by the order: who tells is who manages the device's memory
    s = Server(binary, model, BASE + ["--context-on-demand"], told)       # no keep: the context stays loaded
    s.wait_for("context freed in", 1)        # the load's own context: nothing had asked for it to be kept yet
    status, got = s.embed()
    time.sleep(1.0)
    check("a model that keeps its context between requests", status == 200 and got == base and s.count("context freed in") == 1
          and s.metric("context_loaded") == 1, f"freed {s.count('context freed in')} times")
    status, body = s.post("/park")
    check("told to park, its context goes first", status == 200 and body.get("parked") is True
          and s.count("context freed in") == 2 and s.count("weights parked in") == 1 and s.metric("context_loaded") == 0,
          f"HTTP {status}: {body}")
    status, got = s.embed()
    check("and the next request answers the baseline on a context created for it",
          status == 200 and got == base and s.count("weights unparked in") == 1)
    s.stop()

    # H. no room: told to come back, or asked by a request - refused as what it is, the model still parked
    s = Server(binary, model, BASE + ON_DEMAND, dict(told, GGML_CPU_PARK_TEST_REFUSE="2"))
    s.post("/park")
    status, body = s.post("/unpark")
    check("told to come back with no room: 503 no_room_on_device", status == 503 and _type(body) == "no_room_on_device",
          f"HTTP {status}: {str(body)[:160]}")
    check("the model is still parked", s.metric("weights_parked") == 1 and s.count("weights unparked in") == 0)
    # ONE input: each input of a request is a task of its own and asks for the weights again, and the stand-in counts
    # refusals, where a device with no room refuses them all
    status, body = s.embed(TEXTS[:1])
    check("a request with no room: 503 no_room_on_device, never vectors", status == 503 and _type(body) == "no_room_on_device",
          f"HTTP {status}: {str(body)[:160]}")
    check("still parked, the same process", s.metric("weights_parked") == 1 and s.proc.poll() is None)
    status, got = s.embed()
    check("the next request is served, the baseline's vectors", status == 200 and got == base and s.metric("weights_parked") == 0)
    check("both refusals were counted, and nothing else", s.metric("no_room_refusals_total") == 2
          and s.metric("weights_parks_total") == 1 and s.metric("weights_unparks_total") == 1)
    s.stop()

    # I. a model that cannot park says so, and is left alone
    for label, args, env in (("without context on demand", BASE, told),
                             ("with weights that cannot leave the device", mapped + ON_DEMAND, {})):
        s = Server(binary, model, args, env)
        status, body = s.post("/park")
        check(f"{label}: told to park, 501 not supported", status == 501 and _type(body) == "not_supported_error",
              f"HTTP {status}: {str(body)[:120]}")
        status, got = s.embed()
        check(f"{label}: and the model answers as ever", status == 200 and got == base and s.count("weights parked") == 0
              and s.metric("weights_park_bytes") == 0)
        s.stop()

    # J. told to park WHILE requests come: each request is answered whole, with the baseline's vectors, whatever the
    # order the two met in - the park waits its turn on the server's own thread, or is refused "busy"
    s = Server(binary, model, BASE + ON_DEMAND, told)
    answers: list[tuple[int, object]] = []
    stop = threading.Event()

    def ask() -> None:
        k = 0
        while not stop.is_set():
            answers.append(s.embed())
            k += 1
            time.sleep(0.004 * (k % 7))      # gaps of 0 to 24 ms: an order to park lands in one, or on a request

    worker = threading.Thread(target=ask)
    worker.start()
    seen: dict[str, int] = {}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status, body = s.post("/park")
        key = "parked" if status == 200 and body.get("parked") is True else f"{status} {_type(body)}"
        seen[key] = seen.get(key, 0) + 1
        time.sleep(0.007)
    stop.set()
    worker.join()
    bad = [(st, str(b)[:80]) for st, b in answers if st != 200 or b != base]
    check(f"{len(answers)} requests beside {sum(seen.values())} orders to park: every one the baseline's vectors",
          not bad and len(answers) >= 5, f"{len(bad)} wrong: {bad[:2]}")
    check("each order was either done or refused as busy, nothing else",
          set(seen) <= {"parked", "503 unavailable_error"} and seen.get("parked", 0) >= 3, str(seen))
    parks = s.count("weights parked in")
    check("requests ran on weights that had left in between, and the process is alive and consistent",
          s.proc.poll() is None and parks >= 3 and s.metric("weights_parks_total") == parks
          and s.count("weights unparked in") >= parks - 1, f"parked {parks}, unparked {s.count('weights unparked in')}")
    s.stop()

    # K. a park that FAILS (no host memory for the copy): said as a fault, and nothing is half done - the weights
    # are on the device, and the model answers
    s = Server(binary, model, BASE + ON_DEMAND, dict(told, GGML_CPU_PARK_TEST_NO_HOST="1"))
    status, body = s.post("/park")
    check("a park that cannot copy the weights out: 500 server_error", status == 500 and _type(body) == "server_error",
          f"HTTP {status}: {str(body)[:140]}")
    check("the weights stayed on the device", s.metric("weights_parked") == 0 and s.count("the buffer poisoned") == 0
          and s.count("the weights could not be parked") >= 1)     # the server's line, and the error's own
    status, got = s.embed()
    check("and the model answers the baseline", status == 200 and got == base)
    status, body = s.post("/park")
    check("told again, it parks", status == 200 and body.get("parked") is True and s.metric("weights_parked") == 1)
    status, got = s.embed()
    check("and comes back as ever", status == 200 and got == base)
    s.stop()

    # L. a return that FAILS is not a refusal (the device failed, or the bytes read back are not the ones parked):
    # a fault, 500 - never 503, never counted as no room, never a request to send again - and the model stays parked
    s = Server(binary, model, BASE + ON_DEMAND, dict(told, GGML_CPU_PARK_TEST_BROKEN="2"))
    s.post("/park")
    status, body = s.post("/unpark")
    check("told to come back and the return FAILS: 500 server_error, not a refusal",
          status == 500 and _type(body) == "server_error" and "a failure, not a refusal" in str(body), f"HTTP {status}: {str(body)[:150]}")
    status, body = s.embed(TEXTS[:1])
    check("a request whose return FAILS: 500 server_error, never 503",
          status == 500 and _type(body) == "server_error" and "a failure, not a refusal" in str(body), f"HTTP {status}: {str(body)[:150]}")
    check("the model is still parked, its weights intact aside; nothing was counted as a refusal or a return",
          s.metric("weights_parked") == 1 and s.metric("no_room_refusals_total") == 0 and s.metric("weights_unparks_total") == 0
          and s.proc.poll() is None)
    status, got = s.embed()
    check("when the return works again the model answers the baseline", status == 200 and got == base)
    s.stop()

    # M. A REQUEST NEVER PARKS: the weights came back for a request whose context is then refused - they STAY on the
    # device, are not copied out again, and the next request copies nothing
    s = Server(binary, model, BASE + ON_DEMAND, dict(told, LLAMA_SERVER_CTX_CREATE_FAIL="1"))
    s.wait_for("context freed in", 1)
    s.post("/park")
    status, body = s.embed(TEXTS[:1])
    check("the weights come back, the context is refused: 503 no_room_on_device", status == 503 and _type(body) == "no_room_on_device"
          and s.count("weights unparked in") == 1, f"HTTP {status}: {str(body)[:120]}")
    check("the weights STAY on the device: no second park, no second copy", s.metric("weights_parked") == 0
          and s.metric("weights_parks_total") == 1 and s.count("the buffer poisoned") == 1
          and s.count("they stay on the device") >= 1, f"poisoned {s.count('the buffer poisoned')} times")
    status, got = s.embed()
    check("the next request answers the baseline and brings nothing back again", status == 200 and got == base
          and s.metric("weights_unparks_total") == 1 and s.metric("no_room_refusals_total") == 1)
    s.stop()

    failed = [name for name, ok, _ in results if not ok]
    print(f"{len(results) - len(failed)} of {len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
