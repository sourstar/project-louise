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

**What it gives.** Counted at 13:05Z and not held: 5,685 − 2,737 + 4,404 −
2,512 + 1,335 − 621 = **5,554 MiB** (M, on three dates) — the keeper's
budget (26,990 MiB, 23,457 taken) believed the three embedders held their
contexts. **Settled the same afternoon** (the operator, to the
architecture session: "I meant separate 'models and weights' from the
context, because we use a 'shared' context and it has to be considered in
a separated budget"; cdeea12, on main as #847): the keeper has TWO budgets
— the weights, each on-demand model counted at what it holds idle (2,737 /
2,512 / 621 MiB), and beside them one shared context budget of 4,840 MiB,
the two largest contexts (2,948 + 1,892). Deployed on the operator's word
to München. **The idle sizes are the engine's before the cut-over**
(München, 2026-11-24 14:50Z, the process's own counters): the live
vega-bge-m3, idle with its context freed, holds 897 MiB, not 621 — 577 of
weights and about 320 beyond them; the keeper's remembered sizes are keyed
on the model and keep the least ever read, so an engine swap leaves the
old engine's (models-beyond-the-card.md §5.2, §8). Read again on an empty
card the same afternoon (München; the card's counter and the process's own
agree): bge-m3 896 MiB, nemotron-embed 2,513 — the remembered 2,512 holds —
and a stand-in with no weights on the card 319. qwen3-4b's 2,737 is still
the test copy's of 11-12. So the sum above is short by bge-m3's 275 MiB. With the others opted in: about 3 GiB more (E). At work the set
adds what runs in the same second — 3,262 MiB at most, 2,221 at p95 (M, 24
h of the card's record, shared-context.md §2), which is the keeper's
working-memory reserve.

**Per model, on the node** (München's, on the operator's word there): the
option and its keep in the model's file; idle / in a burst / after, read on
the card as for bge-m3; for an encoder the gate under the new file before
it writes a vector.

## 3. Page instead of unload — what is built

`[models] make_room = "page"` (07 §7.8; app/model_evict.py). **THE DEFAULT
since 2026-11-25** (operator, relayed by the architecture session: "Set the
new paging mode as the default in the engine") — in three places, held
together: the keeper's default, here; the engine's (staged 0021: every
llama.cpp model pages without a line in its file); and LocalAI's reclaimer,
off where the deploy sees a card that pages. ON A CARD WHOSE DRIVER WAS NOT
MEASURED TO PAGE the default unloads as before: paging is what the xe
driver was seen to do, and under a driver that does not, a load over the
card is a failed load or a killed backend (`PAGING_DRIVERS`). The default
replaces none of the conditions below: the refusal reaching the graph (§4),
the host's floor measured in the busy hours, the 30-minute soak.
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
0. the engine's refusal fix (§4) in the live engine — the architecture
   session's condition: until then a full card can kill a live request.
   **MET 2026-11-25**: staged 0018 is in the live engine d531d5d804f1 and
   its row on the card passed (§4);
1. this code deployed — inert while the file says nothing;
2. EVERY llama.cpp MODEL ABLE TO PAGE: `GGML_SYCL_BMG_VRAM_CHECK=0` and
   `GGML_SYCL_HOST_PINNED_MEM_2G=1` (S5's two switches; the second so that
   a model leaves the card in pieces, not whole — MADLAD without it lost
   5.4 GiB at once). Since staged 0021 these are THE ENGINE'S DEFAULTS, so
   no model's file carries them and a model added later pages too; a file
   that sets either the other way still wins for that model. A tensor
   larger than 2 GiB is not refused by the cap: the allocator gives it a
   buffer of its own, of its own size, and that one piece moves whole. **Every encoder is a new run under a new file: gated
   before it writes a vector** (invariant 12). The 35B's five answers were
   text for text the same with the second switch (M, S5 part 1);
3. the floor measured in the busy hours, and written in the file;
4. the keeper's default merged ONLY ONCE the engine with 0021 is the live
   one (the architecture session: a keeper that pages on an engine whose
   models cannot would unload-for each of them and say so) — IT IS:
   `619655e5833b` (`engine-canary.series`, which carries 0021), live since
   2026-11-25 15:18:30Z, its five gates exact with every fingerprint
   identical to before (München). Then the settings
   stage run, `node resident show` read (it names the rule the node
   follows, a reclaimer left on and a host under its floor);
5. a soak the POC never had: the Ask's models and a corpus pass together
   for 30 minutes, the card over its size, no abort and no kernel line.

**What would let the bound rise:** a row on the node with the largest
on-demand context created on a card committed further over.

**A tensor larger than a 2 GiB buffer — counted, not inferred (München,
2026-11-25; `tools/measure/gguf_largest_tensor.py`, headers only; 101 GGUF
files on the node).** With buffers of 2 GiB at most a tensor is never split
and never refused: the allocator gives one larger than that a buffer of its
own size, so it loads and that one piece moves whole instead of in pieces.
- **None of the models Louise runs has one.** The largest tensor of each:
  bge-m3 F16 488.3 MiB, nemotron-3-embed-1b F16 512.0, Qwen3-Embedding-4B
  303.7, MedCPT 44.7, MADLAD F16 500.0, the reranker 501.0, the 9B chat
  795.7, the 35B 515.3 (733 tensors, 21,099 MiB), GLM-OCR 92.4, OmniVoice
  157.4 and 24.0.
- **One installed file of 101 does**: NVIDIA-Nemotron-3.5-Bolt-E4B at Q8
  (a chat candidate, not a live model), `per_layer_token_embd.weight`
  10752 x 262144 = 2,856.0 MiB. Its Q4 sibling has the same tensor at
  1,512.0 MiB. The next largest anywhere are all under: 1,483.8, 1,102.8,
  1,062.5, 1,030.6.
- So it is where it was expected: a very large token table kept at high
  precision. A new model's file is counted with the same command.

## 4. The engine must refuse, not die — a defect of the live engine

*The architecture session, 2026-11-24, on the death München saw: "a defect
of the live engine, against idle-context-brief.md §1.2's own contract:
'refused, never an abort'. Page mode only makes it likely; any full card
can reach it … Until that fix is in, page mode should not be switched on."*

Where it dies: ggml-sycl's pool commits device memory for the ops'
temporaries WHILE A GRAPH RUNS, under `SYCL_CHECK`
(`ggml_sycl_pool_vmm::alloc`, `phys.emplace`): a refusal aborts the
process. A model's own buffers fail softly — it is this one path. The fix
is staged, `staged/0013-llama-cpp-sycl-pool-refusal.patch` (localai-bugs.md
B18 / P14): the pool throws, the graph fails with
`GGML_STATUS_ALLOC_FAILED`, llama.cpp's decode returns -2, its server
answers that request "Compute error." and goes on; Louise reads any 5xx
from an encoder as a deferral (app/embeddings.py `_endpoint_error`), never
an error on a document. A model with context on demand then frees its
context and makes a new one at the next request.

**On the card, 2026-11-24 (München, engine 9ea95e93a83c, the five steps
below): NOT PROVOKED.** The card filled to 31,701 of 32,002 MiB (the 35B,
MADLAD, a reranker and an on-demand encoder), then three requests to the
on-demand copy and two rerank requests of twelve long documents: all
served, the same vector, no pid lost, no refusal in any log — the driver
paged a neighbour out (the counter fell 566 MiB) instead. So the refusal
this fixes needs neighbours the driver cannot evict (this morning's were
the withdrawn USM-by-use experiment's), and a filled card does not give
it. Its path is exercised deterministically instead: staged 0016,
`GGML_SYCL_POOL_REFUSE=N`, in the parking prototype's engine
(parking-prototype.md §4). The fix is in the engine swapped that night.

**PROVOKED, AND 0013 DID NOT HOLD (München, 2026-11-25, engine b190673dbdbf).** A reranker copy with `GGML_SYCL_POOL_REFUSE=2` against a control: the control served the short pair and the twelve long documents three times (1.54–1.59 s, the same scores, its process alive). With the switch, the first request that grew the pool ended the PROCESS — HTTP 500 "error reading from server: EOF" after 3.0 s — and so did LocalAI's two reloads, each at its first refusal. In each process, in one millisecond: "the device refused 6 MiB for the pool (2 MiB committed)", then "the SYCL pool could not commit device memory / Exception caught at … ggml-sycl.cpp, line:4221, func:operator()", then "SYCL error: CHECK_TRY_ERROR(op(ctx, src0, src1, dst, …)): Exception caught in this line of code … in function ggml_sycl_op_mul_mat". Never "the graph is abandoned", never "Compute error.". **Why:** 0013 threw the refusal as a `std::runtime_error`; the backend calls its ops under its own macros, `SYCL_CHECK(CHECK_TRY_ERROR(op(…)))`, and `CHECK_TRY_ERROR` catches ANY `std::exception` and aborts (72 call sites). The op that asked for the memory took the refusal; the graph's catch was never reached. The live engines since 2026-11-24 therefore die on a refused pool commit exactly as every engine before them: no worse, and not fixed. It was not seen before the card because nothing here runs the SYCL path, and filling the card did not provoke it.

**The fix, staged 0018** (`0018-llama-cpp-sycl-pool-refusal-reaches-the-graph.patch`, candidate `engine-refusal.series`: the live engine and this one patch): the refusal is a plain struct, neither a `sycl::exception` nor a `std::exception`, with a `static_assert` for each, so only the graph's own catch can take it. Checked without a card, in the three layers a card run crosses: (1) the backend's REAL macros under Intel's compiler (`tools/measure/sycl_refusal_macro_check.cpp`): the old type aborts with the card's own lines, exit 134; the new one reaches a catch outside the op, exit 0; (2) what an abandoned op leaves, read: its temporaries are the pool's and are released in reverse order as the stack unwinds; the one thing ops change for good, the layout of quantized weights, takes its scratch memory from the device allocator, not the pool; no handler between an op and the graph catches `(...)`; (3) everything ABOVE the backend, run: a CPU stand-in that fails two graphs with `GGML_STATUS_ALLOC_FAILED` (`tools/measure/refuse_cpu_standin.patch`, `tools/measure/refuse_cpu_test.py`, 13 of 13) — the request is answered 500 "Compute error." (503 from the canary when the context is made for the request), the process is the same one, and the next request returns the control's vectors bit for bit. What is left for the card is the one thing none of these is: a real device between (1) and (3) — the same row again.

**ON THE CARD, 2026-11-25 06:24Z (München, engine d531d5d804f1, the card emptied, the node paused): IT HOLDS.** The control (one process): the short pair and the twelve long documents four times, 200 in 1.53–1.58 s, scores 2903cd77b39a, nothing refused. With `GGML_SYCL_POOL_REFUSE=2` (pid 3022088): the short pair 200; the first long request HTTP 500 in 0.044 s, `rpc error: code = Internal desc = Compute error.`, the pid alive; the second the same in 0.040 s, the same pid; the third 200 in 1.625 s and the fourth in 1.534 s, both with the control's scores 2903cd77b39a, the same pid. One process started in all. Its log, counted: "the device refused" 2, "ggml_sycl_graph_compute_or_fail: the SYCL pool could not commit device memory: the graph is abandoned" 2, CHECK_TRY_ERROR 0, "Exception caught" 0, "Server listening" 1. A refused pool commit now fails its request and nothing else.

**Compiled here, never run; its test is on the card** (compiled 2026-11-24
with Intel's compiler, oneAPI 2025.3.3, installed in the cloud session: the
engine's SYCL backend builds and links with it. The test is München's, on
the operator's word there), an engine built with the patch:
1. fill the device — the test copies of this morning's runs, the card at
   its last MiB (the driver's count);
2. ask a model whose context is made on demand (the nemotron-embed copy)
   for work, and load another beside it: each request is REFUSED (HTTP
   5xx, "Compute error." or a load that fails with its reason), the
   backend's log carries "the device refused … for the pool", and **the
   backend's pid is the same afterwards**;
3. the pool is every model's (the architecture session): one RERANKER
   request on the filled card — refused the same way, its backend's pid
   unchanged; the chat model runs the same code;
4. free the card: the same model answers, with no load in between (its
   weights never left), and its vectors are the bytes of before;
5. it is a new build, so a new engine and a new run: before it serves,
   each space gates on it once, as at the cut-over — preflight `louise hold
   embedder cpu-reference --check`, then the gates — and its ordinary
   outputs are byte for byte c3294e050ff2's, the sixteen fingerprints
   equal: nothing of a computation changes.

Order (the architecture session): the build and the card test on the
operator's word, then the gates, and only then may page mode be weighed.

Until it is in the live engine, `make_room = "page"` stays off (step 0 of
§3's list). It is, since 2026-11-25 05:52Z, and its row passed: step 0 is
met, and steps 1 to 5 are what stands between the node and page mode.

**Not built:** paging for the keeper's preloads (a preload beyond the card
would only be loaded to be paged out), a rule for which model the driver
takes first (it chooses, by idleness and whole buffers), and the 2 GiB
pieces for the backends that are not llama.cpp.
