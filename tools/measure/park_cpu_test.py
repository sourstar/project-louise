#!/usr/bin/env python3
"""What sits ABOVE the park hook, tested where there is no card: llama_model_park /
unpark and the server's lifecycle (the weights leave when the context is freed, come
back for the next request, a refusal leaves the model parked, the canary catches
weights that did not come back).

The device is a STAND-IN, tools/measure/park_cpu_standin.patch (never part of an
engine): with GGML_CPU_PARK_TEST=1 a CPU weight buffer "parks" by copying its bytes
aside and POISONING itself with 0xA5, so anything that reads it while parked, or after
a bad unpark, computes garbage. GGML_CPU_PARK_TEST_REFUSE=N refuses the next N
unparks (a device with no room); GGML_CPU_PARK_TEST_LOSE=N makes the next N succeed
without putting the bytes back.

The server under test: llama.cpp at the series' pin with the series' llama.cpp
patches, staged 0012-0015's, then the stand-in; built for CPU (no web UI). Any small
embedding GGUF serves (run 2026-11-24 on bge-small-en-v1.5 f16: 16 of 16).
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
import time
import urllib.error
import urllib.request
from pathlib import Path

TEXTS = [f"sentence number {i}: the quick brown fox jumps over the lazy dog, {i} times" for i in range(16)]
BASE = ["--embedding", "--load-mode", "none", "--no-repack", "-c", "512", "-b", "512", "-ub", "512", "-np", "1", "-t", "4",
        "--no-webui", "--log-verbosity", "4"]   # 4: the library's own lines too
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

    def embed(self) -> tuple[int, object]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/embeddings",
                                     data=json.dumps({"input": TEXTS}).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = json.loads(r.read())
                return r.status, [d["embedding"] for d in body["data"]]
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


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
    check("and the model is still parked", s.count("the weights cannot come back to the device") == 1)
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

    failed = [name for name, ok, _ in results if not ok]
    print(f"{len(results) - len(failed)} of {len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
