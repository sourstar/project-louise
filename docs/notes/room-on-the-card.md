# Room on the card — the order of work (2026-11-24)

*The operator, 2026-11-24, in order: "Extend page instead of unload to the
dense models"; "And of course the 'shared' context is mandatory
improvement". Before them the same day: the 35B's experts paged by use,
measured and ended (backend-qualification-2026-11-21.md §3, "THE EIGHT
ROWS"). This note is what is built, what the node still has to do, and in
which order. M = measured (by München unless said), E = estimate.*

## 1. The order

1. **The shared context on every model** — built (P8), on for three
   embedders; the rest is per-model work on the node (§2). First, because
   it is memory given back at no cost to a request that is not running.
2. **Page instead of unload** for a load that still does not fit — built
   here (§3), OFF until the node has the engine switches, the gates and a
   floor measured in its busy hours.
3. **The 35B** — not by paging its experts (ended). Resident with the
   driver's swap (S5: its own speed, a toll at each change of model) or
   behind the engine's expert cache (84 tok/s with 8 GiB, 19.3 GiB of host
   memory): the POC's report, §4.6 of the qualification note; the chat
   model is the operator's decision.

## 2. The shared context, per model

Context on demand (shared-context.md, idle-context-brief.md): a model keeps
its weights; its context is made when a request arrives and freed when the
work is done. The node at 13:05Z (the live files and the keeper's file):

| model | on demand | the keeper counts at rest, MiB | idle with its context released, MiB | what it still takes |
|---|---|---|---|---|
| vega-qwen3-embedding-4b | yes | 5,685 | 2,737 (M 11-12, a test copy) | the keeper's figure corrected |
| vega-nemotron-3-embed-1b | yes | none (largest 4,404) | 2,512 (M 11-12) | the same |
| vega-bge-m3 | yes | 1,335 | 621 (M 11-23) | the same |
| louise-translate-madlad | no | 4,850 | ≈ 2,850 (E: its context read 2,007 on 11-12) | the option, a keep, the reading; left out on purpose on 11-11 — the operator's "mandatory" reverses that |
| louise-rerank-nemotron-1b, louise-rerank-bge | no | 2,465 (nemotron) | unknown, 0.2–0.6 GiB less each (E) | the option, a keep (the test copy ran `keep_context_ms:500`), the reading |
| the chat model | no | 4,894 (the 9B) | ≈ 0.6 GiB less (E, 11-12) | the option and a keep of minutes: a freed context drops the conversation's cached prompt |
| vega-medcpt-query | no | 258 | little (a 513-token context) | the option, the reading, and its gate (an encoder: invariant 12; preflight `louise hold embedder cpu-reference --check`) |
| OpenVINO, torch, omnivoice-cpp, the iGPU's models | not llama.cpp | dar-legal-it 5,187 | — | torch's own: what dar-legal-it keeps of a corpus pass (1.8 GB on 11-09) |
| the OmniVoice port | refused by the patch (a model with a second model file) | — | — | nothing: its decoding context is already made per request |

**What it gives.** Counted today and not held: 5,685 − 2,737 + 4,404 −
2,512 + 1,335 − 621 = **5,554 MiB** (M, on three dates) — the keeper's
budget (26,990 MiB, 23,457 taken at 13:05Z) believes the three embedders
hold their contexts. The correction is the keeper's rule, the architecture
session's. With the others opted in: about 3 GiB more (E). At work the set
adds what runs in the same second — 3,262 MiB at most, 2,221 at p95 (M, 24
h of the card's record, shared-context.md §2), which is the keeper's
working-memory reserve.

**Per model, on the node** (München's, on the operator's word there): the
option and its keep in the model's file; idle / in a burst / after, read on
the card as for bge-m3; for an encoder the gate under the new file before
it writes a vector.

## 3. Page instead of unload — what is built

`[models] make_room = "page"` (07 §7.8; app/model_evict.py; off by default).
A load WORK is waiting on that does not fit under the line unloads nothing
while the host can hold the difference — MemAvailable less the shortfall
at or above `page_floor_gib` — and the xe driver moves idle models' memory
to host memory and back. M, S5 part 2: a small model's next request 0.2–0.4
s later, against 7.5–8.7 s after an unload; the 35B 1.3–1.8 s; memory comes
back at 2.4–3.4 GiB/s; two rounds without an error, a 30-minute loop never
run.

What the rule is careful about, each from a measurement or a review:
- **the keeper's preloads keep today's unload** (the architecture session:
  a preload is never urgent and must not push what is in use off the card);
- **never further over the card than was measured** (the architecture
  session's blocking point): a model whose context is made on demand
  reserves it from the card at each request, and a reservation the card
  refuses KILLS its backend — `UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY` at
  ggml-sycl's pool, seen against the 35B's system-allocated pages. The
  driver fills the card before it pages, so the keeper cannot hold the
  working-memory reserve FREE there; it holds the measured envelope
  instead: the loaded models at their largest plus the load may exceed the
  card by `page_over_gib` at most — 3, where S5 held 3.5 with a 1.9 GiB
  context made on demand in it; the largest such context, 3.26 GiB, was not
  in that run — and the host keeps a context's worth (3,262 MiB) above its
  floor. Whether a reservation can be refused against ORDINARY paged-out
  buffers under more pressure is not measured either way (München);
- **the host's floor**: under it, or the host unread, the load unloads as
  before. The default, 12 GiB, is a placeholder: on 11-24 the node had 4.7
  GB available under load, where the rule never pages. To be measured
  against the busy hours — the host's OOM killer is what stands under it;
- **a model that cannot page**: an llama.cpp model without the engine's
  switch refuses an allocation the card cannot hold. Its failed load is
  remembered and it is unloaded-for during the next hour, with one line in
  the node's log;
- **LocalAI's reclaimer off** in this mode: a card holding more than its
  size reads full at every tick. The deploy's settings stage reads the
  node's own file (`localai_settings.py --platform`), so it applies and
  checks the mode instead of undoing it; the threshold stays, the keeper
  reads its line from it;
- **memory the driver cannot move** is not for this: the 35B's experts as
  system allocations killed a neighbour's load (the eight rows, row 7).

**On the node, before the setting is turned on** (München's, on the
operator's word there; nothing here does it):
1. this code deployed — inert while the file says nothing;
2. in every llama.cpp model's file `GGML_SYCL_BMG_VRAM_CHECK=0` and
   `GGML_SYCL_HOST_PINNED_MEM_2G=1` (S5's two switches; the second so that
   a model leaves the card in pieces, not whole — MADLAD without it lost
   5.4 GiB at once). **Every encoder is a new run under a new file: gated
   before it writes a vector** (invariant 12). The 35B's five answers were
   text for text the same with the second switch (M, S5 part 1);
3. the floor measured in the busy hours, and written in the file;
4. `make_room = "page"`, the settings stage run, `node resident show` read
   (it names a reclaimer left on and a host under its floor);
5. a soak the POC never had: the Ask's models and a corpus pass together
   for 30 minutes, the card over its size, no abort and no kernel line.

**What would let the bound rise:** a row on the node with the largest
on-demand context created on a card committed further over; and an engine
that REFUSES a context it cannot reserve instead of dying — the design of
context on demand says "refused, never an abort" (idle-context-brief.md
§1.2), the pool underneath aborts.

**Not built:** paging for the keeper's preloads (a preload beyond the card
would only be loaded to be paged out), a rule for which model the driver
takes first (it chooses, by idleness and whole buffers), and the 2 GiB
pieces for the backends that are not llama.cpp.
