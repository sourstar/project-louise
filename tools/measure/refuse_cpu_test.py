#!/usr/bin/env python3
"""What sits ABOVE a backend that fails a graph for want of device memory (GGML_STATUS_ALLOC_FAILED), tested where
there is no card: llama_decode's -2, the server's answer, and the next request.

The device is a stand-in (tools/measure/refuse_cpu_standin.patch: DEV ONLY, applied to a scratch copy of the engine's
llama.cpp source and never in a series): with GGML_CPU_REFUSE_GRAPHS=N and GGML_CPU_REFUSE_ARM=<file> the CPU backend
refuses the next N graphs once the file exists. What the SYCL backend does BELOW this - its pool's refusal reaching
the graph - is tools/measure/sycl_refusal_macro_check.cpp's and the card's.

    python3 -I tools/measure/refuse_cpu_test.py tools/measure/park_cpu_test.py <llama-server> <model.gguf> [--told]

--told: on the park engine's source, where the refusal has a type of its own (503 no_room_on_device).

Run 2026-11-25 on the candidate source of engine-refusal.series (CPU build, bge-small-en-v1.5 F16): 13 of 13.
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("park", sys.argv[1])
park = importlib.util.module_from_spec(spec)
spec.loader.exec_module(park)
binary, model = sys.argv[2], sys.argv[3]

results = []
def check(name, ok, detail=""):
    results.append(ok)
    print(("ok:   " if ok else "FAIL: ") + name + (f"  [{detail}]" if detail else ""), flush=True)

s = park.Server(binary, model, park.BASE, {})
status, base = s.embed()
check("the control answers", status == 200 and len(base) == len(park.TEXTS))
s.stop()

for label, args in (("the context kept", park.BASE), ("context on demand, a keep of 0", park.BASE + park.ON_DEMAND)):
    arm = Path(tempfile.mkdtemp(prefix="refuse-")) / "arm"
    s = park.Server(binary, model, args, {"GGML_CPU_REFUSE_GRAPHS": "2", "GGML_CPU_REFUSE_ARM": str(arm)})
    pid = s.proc.pid
    status, got = s.embed()
    check(f"{label}: before any refusal, the control's vectors", status == 200 and got == base)
    arm.write_text("armed")
    # a request is several graphs (and, on demand, its context's canary first): the two refusals may fall in one
    # request or in two. Every request they touch is answered as an error; the first one they do not is served.
    refused = []
    for _ in range(4):
        status, got = s.embed()
        if status == 200:
            break
        refused.append((status, str(got)[:90], park._type(got)))
    check(f"{label}: a refused graph is answered as an error, never with vectors",
          len(refused) >= 1 and all(st >= 500 for st, _, _ in refused), str(refused))
    # the park engine: a graph the device had no memory for is 503 no_room_on_device, where it was 500 server_error -
    # and where it is the CANARY of a context being created that is refused, it is a context that cannot be created,
    # not a canary that failed (it was 503 unavailable_error "context canary failed", and counted as one)
    if "--told" in sys.argv:
        check(f"{label}: every refusal is answered 503 no_room_on_device",
              all((st, ty) == (503, "no_room_on_device") for st, _, ty in refused), str(refused))
        check(f"{label}: no refusal is counted as a canary that failed",
              s.metric("context_canary_failures_total") == 0 and "does not reproduce the canary" not in s.text())
    check(f"{label}: the process is the same one", s.proc.poll() is None and s.proc.pid == pid)
    check(f"{label}: the first request after the refusals is served, with the control's vectors bit for bit",
          status == 200 and got == base, f"HTTP {status} after {len(refused)} refused")
    text = s.text()
    check(f"{label}: both refusals happened and the server said so",
          text.count("DEV: the CPU stand-in refuses") == 2 and ("Compute error" in text or "canary" in text),
          f"{text.count('DEV: the CPU stand-in refuses')} refusals, {text.count('Compute error')} compute-error lines")
    if "--told" in sys.argv:
        check(f"{label}: each refused request was counted", s.metric("no_room_refusals_total") >= len(refused) >= 1,
              f"{s.metric('no_room_refusals_total'):.0f} counted, {len(refused)} requests refused")
    status, got = s.embed()
    check(f"{label}: and it goes on reproducing itself", status == 200 and got == base and s.proc.poll() is None)
    s.stop()

print(f"{sum(results)} of {len(results)} passed")
sys.exit(0 if all(results) else 1)
