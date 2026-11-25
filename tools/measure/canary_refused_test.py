#!/usr/bin/env python3
"""A context canary the device REFUSES FOR MEMORY is not a canary that failed
(docs/notes/parking-step-2.md §6b; staged 0028).

With context on demand and the canary on - every encoder - the first graph of a
context created on a full card IS its canary. Refused for memory, it was
answered "context canary failed" and counted as one: a determinism failure that
never happened. Louise then sets the run's statements aside and gates the
collection again (invariant 12) - forty minutes of a collection's embedding for
a card that was full for a second (the node, 2026-11-25 09:40:55Z). At the LOAD
the same refusal left the model served UNCHECKED for the life of the process.

Where there is no card: llama.cpp's own server, built for CPU with the stand-in
that refuses a graph once a file exists (tools/measure/refuse_cpu_standin.patch,
DEV ONLY, in no engine):

    python3 -I tools/measure/canary_refused_test.py tools/measure/park_cpu_test.py <llama-server> <an embedding model>.gguf

Exit 0: a refused canary at a request is a context that cannot be created (a
deferral, counted nowhere as a canary that failed) and the next request is
served with the control's vectors; a refused canary at the load fails the load.
Exit 1 says what it saw - "DEFECT" where the refusal was answered as a canary
that failed, or the model was served unchecked.

Run 2026-11-25, CPU build, bge-small-en-v1.5 F16: on the source of
engine-stream.series (792f050e318e) DEFECT, twice; with staged 0028, 12 of 12.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("park", sys.argv[1])
park = importlib.util.module_from_spec(spec)
spec.loader.exec_module(park)
binary, model = sys.argv[2], sys.argv[3]

#: what Louise's embedding client reads the engine's answer for (app/embeddings.py CANARY_FAILED)
CANARY_FAILED = "context canary failed"
REFUSED_AT_REQUEST = "the device had no memory for the canary of the context created, it is discarded"
REFUSED_AT_LOAD = "the device had no memory for the context canary: the load fails rather than serve"
DISCARDED = "does not reproduce the canary of the load"
UNCHECKED = "the contexts created on demand are not checked"
STANDIN = "DEV: the CPU stand-in refuses"

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("ok:   " if ok else "FAIL: ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def defect(what):
    results.append(False)
    print("DEFECT: " + what, flush=True)


# ---- at a request: the context created for it has its canary refused
# A context created on demand computes two things before any request: the warmup's empty run, whose failure
# llama.cpp ignores, and the canary. A device with no room refuses both; the stand-in refuses the next TWO graphs.
arm = Path(tempfile.mkdtemp(prefix="canary-")) / "arm"
s = park.Server(binary, model, park.BASE + park.ON_DEMAND, {"GGML_CPU_REFUSE_GRAPHS": "2", "GGML_CPU_REFUSE_ARM": str(arm)})
status, base = s.embed()
check("the control answers, on a context whose canary reproduced",
      status == 200 and len(base) == len(park.TEXTS) and "its canary reproduced" in s.text())
created = s.count("context created in")
arm.write_text("armed")
status, got = s.embed([park.TEXTS[0]])
body = str(got)
check("the stand-in refused the warmup and the canary of the context being created, and no context came of it",
      s.count(STANDIN) == 2 and s.count("context created in") == created, f"{s.count(STANDIN)} refused")
if CANARY_FAILED in body or DISCARDED in s.text():
    defect(f"a canary refused for memory was answered as a canary that FAILED: HTTP {status} {body[:150]!r}; "
           f"context_canary_failures_total = {s.metric('context_canary_failures_total'):.0f}")
else:
    check("it is answered as a context that cannot be created now: 503, to be asked again",
          status == 503 and "the context cannot be created now" in body, f"{status} {body[:120]}")
    check("the answer does not say \"context canary failed\": Louise defers the batch and gates nothing again",
          CANARY_FAILED not in body)
    check("it is counted nowhere as a canary that failed",
          s.metric("context_canary_failures_total") == 0 and DISCARDED not in s.text())
    check("the engine says what it was", s.count(REFUSED_AT_REQUEST) == 1)
arm.unlink()
status, again = s.embed()
check("the next request is served, with the control's vectors bit for bit", status == 200 and again == base)
check("on a context whose canary reproduced, in the same process", s.count("its canary reproduced") >= 2 and s.proc.poll() is None)
s.stop()

# ---- a canary that really does not reproduce is still one: the fix takes nothing from the check
s = park.Server(binary, model, park.BASE + park.ON_DEMAND, {"LLAMA_SERVER_CTX_CANARY_FAIL": "1"})
status, got = s.embed([park.TEXTS[0]])
check("a canary that does NOT reproduce is still answered \"context canary failed\", and counted",
      status >= 500 and CANARY_FAILED in str(got) and s.metric("context_canary_failures_total") == 1, f"{status} {str(got)[:100]}")
s.stop()

# ---- at the load: the canary cannot be recorded
arm.write_text("armed")
started = None
try:
    started = park.Server(binary, model, park.BASE + park.ON_DEMAND + ["--no-warmup"],
                          {"GGML_CPU_REFUSE_GRAPHS": "1", "GGML_CPU_REFUSE_ARM": str(arm)})
    text = started.text()
except RuntimeError as e:
    text = str(e)
if started is not None:
    if UNCHECKED in text:
        defect("a canary refused for memory AT THE LOAD left the model served UNCHECKED for the life of the process")
    else:
        check("the load whose canary was refused does not serve", False, text[-300:])
    started.stop()
else:
    check("a load whose canary was refused for memory FAILS: it is tried again, never served unchecked",
          REFUSED_AT_LOAD in text and UNCHECKED not in text, text[-300:])
    check("and the refusal was the stand-in's, of the canary's graph", text.count(STANDIN) == 1)
arm.unlink()
s = park.Server(binary, model, park.BASE + park.ON_DEMAND + ["--no-warmup"], {})
status, got = s.embed()
check("the same load, with room, serves the control's vectors", status == 200 and got == base)
s.stop()

print(f"{sum(results)} of {len(results)}")
sys.exit(0 if all(results) else 1)
