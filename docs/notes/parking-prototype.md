# Parking, step 1: the engine parks one encoder (2026-11-24)

*The prototype of `models-beyond-the-card.md` §6 step 1. Operator, relayed by
München from the architecture session: "Do everything, also 721, everything,
run unattended" — line 2, "Parking step 1, Tensor's prototype (bge-m3
parked; N park cycles the same bytes; the park, the un-park and the first
request timed apart; the parked process's floor read)"; and in the cloud
session: "Run unattended". BUILT, AND RUN ON THE CARD 2026-11-25 (§4a): the
same bytes at every cycle, 15.5 ms to come back, 575 MiB given back. The
measurement was §4, München's; §4a has its rows and what they decide.*

## 1. What it is

A parked model keeps its process — the kernel programs, the tensors, the
server — and gives back only the device memory of its weights. The next
request copies the weights onto the card again. By the load split on the
unloaded node (`models-beyond-the-card.md` §5.2: cold 6.22–6.32 s for
bge-m3, the copy 0.19–0.21 s, a process with no weights 319 MiB) that
should cost a copy and a context, not a load. The prototype exists to
measure exactly that.

Patch: `deploy/localai/patches/staged/0015-llama-cpp-park-weights.patch`
(one llama.cpp patch, 632 lines in 11 files). Engine: the next live one,
`staged/engine-next.series` (agreed with the architecture session,
2026-11-24: one build for everything due rather than a prototype engine of
its own — a build costs every run its gate). Parking is in it and OFF;
copies switch it on by their env.

## 2. How it works

**No address changes.** The architecture note's worry was "every tensor's
address changes at an un-park": whatever remembers one must be found and
fixed, and one that is missed is wrong vectors, silently. The prototype
removes the question instead. On SYCL a parkable weight buffer is physical
memory mapped into a RESERVED virtual range (`sycl_ext_oneapi_virtual_mem`,
the extension the engine's pool already grows with). Park: copy the buffer
to host memory, release the physical memory. Un-park: commit physical
memory into the same range, copy back, free the host copy. Every tensor's
data pointer, every extra, and the in-place reorder of quantized weights
stay exactly as they were; the bytes that come back are the card's own, so
a reordered tensor is still reordered and its flag still true.

| layer | what was added |
|---|---|
| ggml | a buffer may implement `park` / `unpark` (optional, address-stable by contract); `ggml_backend_buffer_can_park/park/unpark`, all parts of a multi-buffer or none; `ggml_backend_set_alloc_usage_hint`, so a backend knows at allocation that a buffer is weights |
| ggml-sycl | `GGML_SYCL_PARKABLE=1`: weight buffers behind a reserved range, committed in 1 GiB pieces; park and un-park, each logging its times (the copy apart from the commit or release); `GGML_SYCL_PARK_VERIFY=1`: every un-parked buffer read back and compared, a checksum at every park; a parked buffer that is read or written aborts with a message |
| llama | `llama_model_can_park / park / unpark / is_parked`: every weight buffer that can park does, a model with a device buffer that cannot is refused whole; no context may exist, and none can be created on a parked model |
| server | `LLAMA_SERVER_PARK_WEIGHTS=1` with context on demand: the weights are parked when the context is freed and brought back before the next request's context; a card with no room refuses that request as it refuses a context (503), the model stays parked, the next request tries again; the context canary holds every context made on un-parked weights to the load's output |

Off unless the model's `env` sets the switches: without them the engine
allocates and behaves as the fixes' engine does. No load option and no RPC
were added, so the wrapper needs no rebuild for this step.

**Do not reinvent the wheel.** The mechanism is the virtual-memory
extension's own (reserve, commit, map) — Level Zero's `zeVirtualMemReserve`
/ `zePhysicalMemCreate` / `zeVirtualMemMap` underneath — and Clockwork's
shape (weights in host RAM, a copy to load). What is ours: the hook's
contract and the lifecycle. The standardized alternative for other backends
(CUDA's `cuMemMap`, Vulkan sparse binding) would implement the same two
hooks; a backend that cannot keep addresses needs every pointer rebased,
which is the larger design this avoids.

**What it holds while parked, and what that costs.** The host copy is
anonymous memory the size of the weights (bge-m3: 577 MiB), taken at each
park and freed at each un-park, so the copy is always the card's current
bytes and nothing has to track whether a reorder happened since. Reading
the file again instead (page cache, reclaimable) is the open choice of
`models-beyond-the-card.md` §8; for an F16 model the two give the same
bytes.

## 3. What was verified here (no card)

| check | result |
|---|---|
| the SYCL code compiles | `ggml-sycl.cpp` with the patch, Intel's compiler 2025.3.3 (oneAPI installed in the cloud session): no error, no new warning |
| the patch applies as the build applies it | after the series' inner patches and staged 0012–0014's, `patch -p1`: the 12 files equal the development tree |
| the engine's own context-on-demand tests | 21 of 21 on the patched CPU server (one timing guard tripped once while a compile ran beside it; alone, 3 of 3) |
| the two new tests of the patch (`test_park_weights.py`) | 2 passed: the switch changes nothing where the weights cannot park, or without context on demand |
| THE CYCLES, on a stand-in for the device (`tools/measure/park_cpu_standin.patch`, `park_cpu_test.py`): a CPU buffer that POISONS itself while parked | 16 of 16: 12 requests, each on weights that had been parked, gave the un-parked server's embeddings bit for bit; a refused un-park was answered 503, the model stayed parked, the next request was served; weights that did not come back failed the canary and the request was refused |

Run again the same evening on the WHOLE candidate source (`engine-next.series`: the port, the fixes, parking, its switch and the experts' record together, built for CPU): the cycles 16 of 16, the engine's context-on-demand tests and the patch's two, 23 of 23.

What that does NOT show: anything about the card. The reserved-range memory
has never held weights there, and the copies in and out of it have never
run. The stand-in proves the layers above the hook; the hook's SYCL half is
compiled, read twice, and unrun.

## 4. The measurement on the node (München; the card free, the node as in §5.3 of the design note)

The engine: the live one, once `staged/engine-next.series` has been swapped
in and has passed its gates with every switch off. The rows are copies of
`vega-bge-m3` DIRECT to it (no wrapper). Every copy: the live model's file, options and env, plus
`context_on_demand:true`, `keep_context_ms:0` (so each request frees its
context at once) and `verbosity: 4` (the engine's own park lines are info
lines of the library: below 4 only the server's two are printed).

| run | env added | asked | read |
|---|---|---|---|
| R0, the control | none | the sixteen fingerprint texts, 20 times | the vectors V; a request's time (a context is created for each) |
| R1, parkable and not parking | `GGML_SYCL_PARKABLE=1` | the same | the vectors equal V byte for byte; the time against R0's — weights behind mapped memory must compute the same and as fast |
| R2, parking, verified | + `LLAMA_SERVER_PARK_WEIGHTS=1`, `GGML_SYCL_PARK_VERIFY=1` | the same, each request after the log shows the park | V every time; per cycle "read back: the same bytes", the park's checksum "(the first park's)", "its canary reproduced" |
| R3, parking, timed | as R2 without `GGML_SYCL_PARK_VERIFY` | the same | per cycle: `parked … in X ms (the copy, the release)`, `unparked … in Y ms (the commit, the copy)`, `context created in Z ms`, the request's own time against R0's |
| the floor | R3's process, parked, idle 10 s | — | the card's counter and the process's own, as the other floors were read (the stand-in read 319 MiB) |
| a refusal (with 0013's filled card) | R3's process, parked | one request on the full card, one after room is made | 503 "the weights cannot come back to the device", the pid unchanged; then 200 and V |
| THE POOL'S REFUSAL (staged 0013), provoked | a RERANKER copy, direct, `GGML_SYCL_POOL_REFUSE=2`; and a control copy without it | one pair, then twelve long documents three times | the first two long requests refused: 5xx, "the device refused … for the pool" in the log, the pid unchanged; the third served, its scores the control's |

**The first thing the run says** is whether a load works at all with the
weights behind mapped memory: the upload at load goes through the same
copies. A load that fails, or R1's vectors differing from V, stops the
prototype there — report it, nothing else is meaningful. The fallback is
the design this avoided (rebasing every pointer), not built.

**What decides step 2:** R3's un-park plus context against R0's context
alone — the design note derived 0.3 to 0.9 s for a parked F16 model — the
bytes equal in every cycle, and the floor.

**Why the last row is here.** 0013 is in the engine swapped on 2026-11-24
and its refusal was NOT provoked on the card: filled to 31,701 of 32,002
MiB with three other models loaded, every request was served, the driver
paging an idle neighbour out (566 MiB) instead of refusing (München).
Filling further means starving the host, which the protocol forbids. Staged
0016 (`GGML_SYCL_POOL_REFUSE=N`, off unless a copy's env sets it) raises the device's
exception where the pool grows, so everything after it — the unwinding in
the middle of a graph, the queue drained, the request's error, the process
alive — runs on the real card. A pool's first commit is never refused: the
model loads and warms up.

**The driver, read (intel/compute-runtime, release 26.05,
`level_zero/core/source/context/context_imp.cpp`).** `mapVirtualMem`
registers the mapped range as a device allocation of its own kind
(`reservedDeviceMemory`, `insertSVMAlloc`) and makes it resident;
`unMapVirtualMem` removes it, after which the same address can be mapped
again; a size must be a whole number of the pages chosen for it (the
engine's 2 MiB granularity, as the pool's commits already are). That is
what the prototype relies on: copies and kernels find the weights as they
find any device memory. Read, not run.

## 4a. On the card (München, 2026-11-25, engine b190673dbdbf, the node paused)

Copies of `vega-bge-m3` direct to the engine, the sixteen fingerprint texts,
context on demand with a keep of 0, the card emptied to 25 MiB before each.

| run | requests | errors | vectors differing from R0 | a request, median (p90) | park / un-park, median (max) | context created |
|---|---|---|---|---|---|---|
| R0, the control | 320 | 0 | — | 36.8 ms (38.1) | — | 26.74 ms |
| R1, parkable and not parking | 320 | 0 | 0 of 16 | 36.9 ms (38.4) | — | 26.88 ms |
| R2, parking, verified | 80 | 0 | 0 of 16 | 148.8 ms (157.6) | 84.28 ms (128.06) / 59.33 ms (105.92) | 137.44 ms |
| R3, parking, timed | 80 | 0 | 0 of 16 | 58.6 ms (63.4) | 39.52 ms (70.53) / 15.54 ms (16.34) | 47.5 ms |

The first request of a process (the load) took 6.28 s in R0 and R1 alike.
R2's log: "read back: the same bytes", the park's checksum "(the first
park's)" and "its canary reproduced" at every cycle the ring kept (22 of
each).

**The floor: R3 parked and idle for 10 s holds 321 MiB of the card** (346 by
the card's counter, 25 empty) — against 896 MiB for bge-m3 with only its
context freed, and 319 MiB for a process with no weights at all. The
per-client split was not read (the pid lookup returned nothing).

- **Weights behind mapped memory compute the same and as fast** (R1 against
  R0: the same bytes, 36.9 against 36.8 ms).
- **Parked and back, the same bytes** in every cycle, verified on the card
  itself in R2.
- **Coming back costs 15.5 ms, not the 0.3–0.9 s the design derived**: a
  request that finds its model parked takes 58.6 ms against 36.8 ms, 22 ms
  more, where a model that was unloaded takes 6.28 s. The copy runs at about
  37 MiB per millisecond.
- **A parked bge-m3 gives back 575 MiB** and keeps 321, 2 MiB above a process
  that never had weights.

The pool's refusal, provoked in the same pause, did NOT hold: staged 0013
is wrong and staged 0018 completes it (room-on-the-card.md §4). The un-park
refusal on a full card was not run: a card cannot be made to refuse by
filling it, and the prototype has no switch for that path — it stands on the
CPU stand-in's 16 of 16 and gets its own switch before step 2 relies on it.

**What this decides.** Step 1 asked whether a parked model can come back
for the price of a copy. It can, for less. Step 2 is LocalAI knowing a
parked state (two calls, a RAM budget) so that the keeper parks where it
unloads today:

| | pros | cons |
|---|---|---|
| stop at the prototype (today: unload and reload) | nothing more to build or to gate | every model that leaves the card for room pays its whole load again — 6.3 s for bge-m3, 9.5–11 s for MADLAD |
| step 2: LocalAI parks instead of unloading (the engine half is this prototype; LocalAI gets a parked state, park and un-park calls, and a budget for the host RAM the parked weights use) | a model's return costs a copy: 15.5 ms measured for 575 MiB, about 0.17 s for 6 GiB at the same rate; the keeper can keep many more models "one copy away" than the card holds; the readiness poll's 2 s goes with the load it belongs to | a new state in LocalAI's loader and two calls on the wire, to carry as patches and to file upstream; host RAM held for every parked model (their weights' size); quantized models, buffers above 1 GiB and the second file of a speech model are not tested yet; a switch for the un-park refusal first |

Recommended: step 2. The number that was the whole question came out forty
times better than the design's low estimate, and the cost is engineering,
not risk to an answer (the bytes are verified on the card at every cycle
when asked). It is a change of LocalAI's wire, so it is designed with the
architecture session first; nothing changes on the node until that design
is agreed, and nothing if the operator says nothing.

## 5. Open

- Quantized models are handled by construction (the card's bytes come
  back, flags untouched) and NOT tested: the stand-in has no reorder, and
  step 1 is an F16 model. qwen3-embedding-4b is the first to try after
  bge-m3.
- The host copy as anonymous memory against the file's page cache
  (`models-beyond-the-card.md` §8).
- A park takes a download every time. Keeping the host copy while the
  model is on the card would make a park free and cost the weights' size
  in RAM for every loaded model: step 2's RAM budget decides.
- Buffers larger than 1 GiB are committed in pieces; none has been tried
  (bge-m3 is 577 MiB). MADLAD and the chat model are the test of that.
- The second model file of OmniVoice, adapters and projectors are not
  parked: a model with one cannot use context on demand today.
