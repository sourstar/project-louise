# Context on demand for llama.cpp upstream: a model keeps its weights, a request gives its context back

*Prepared 2026-11-11 for the operator ("Fill the upstream"; then "Reranker
option A, prepare everything for upstream, I will upload it eventually").
PREPARED, NOT FILED: nothing was posted, pushed or opened anywhere. The
design and its reasons: [idle-context-brief.md](../../../../docs/notes/idle-context-brief.md);
the patch as the node builds it: [localai-bugs.md](../../../../docs/notes/localai-bugs.md)
"P11 (staged)". LocalAI's half is the package beside this one,
[`../localai-context-on-demand/`](../localai-context-on-demand/READING-GUIDE.md).*

- **The diffs:** three, in order, against ggml-org/llama.cpp master
  `b8abe41a5081728f9f4c824ef810902a11ab7ffd` (2026-11-11). `git apply` each.
- **Their source:** `deploy/localai/patches/staged/llama.cpp/0011-server-context-on-demand.patch`
  (P11, on our pin), moved onto master and cut where a reviewer can stop.
  Against it they differ only in comments (shortened to upstream's rule:
  one or two lines, not wrapped), the order of the tests and of two members
  of one struct, and one row of the README's flag table.
- **No commit message and no trailers** are in the diffs. Those are the
  submitter's (§2). `tests/test_upstream_material.py` holds the files to
  that form.

## 1. What is prepared

| file | applies to | what | checked here (CPU, stand-in models) |
|---|---|---|---|
| `1-context-on-demand.diff` | llama.cpp `b8abe41` | the feature: `--context-on-demand`, `--keep-context-ms`, a request's `keep_context_ms`; 734 lines added, 334 of them tests | builds; its 14 server tests pass |
| `2-context-create-report.diff` | part 1 | a result says when the context was created for its task (`/embedding`'s `context_create_ms`); 81 lines, 55 tests | builds; 16 tests pass |
| `3-context-canary.diff` | part 2 | the canary: `--context-canary` / `--no-context-canary`; 361 lines, 98 tests | builds; 21 tests pass; the nine existing suites give master's own results (§6) |

Part 1 alone is the feature; 2 and 3 are additions that each stand on the
one before.

## 2. What only you can do, and why none of it was done for you

llama.cpp's own words (`CONTRIBUTING.md`, `AGENTS.md` at `b8abe41`):

- "Features must begin with an issue, not a PR - let interest accumulate
  before writing code".
- "It is strictly prohibited to use AI to write your posts for you (bug
  reports, feature requests, pull request descriptions, Github discussions,
  responding to humans, ...)". Prohibited, "results in immediate PR closure":
  "AI-written PR descriptions, commit messages, or reviewer responses";
  "Automated commits or PR submissions (may result in contributor ban)".
- "Explicitly disclose the manner in which AI was employed." "Undisclosed AI
  usage may result in your account being permanently banned".
- "Be prepared to explain every line of code you submit"; "A proper code
  review usually takes something like one hour per 200-400 LOC and you
  should be spending at least that much time on code review alone" — the
  three parts add about 1,170 lines: three to six hours of your reading.
- "If you are a new contributor - Limit your open PRs to 1".
- "New CLI or public API additions carry a higher bar than internal changes
  - justify why an existing mechanism doesn't suffice." The existing one is
  `--sleep-idle-seconds` (§5).

So this guide and the diffs are for YOUR reading. The issue, the PR
description, the commit message and every answer to a reviewer are yours, in
your words — not this guide's sentences pasted. The code was written by an
AI assistant from your design; say so where the PR template asks.

## 3. The order

| step | pros | cons |
|---|---|---|
| **1. Search** llama.cpp's issues and PRs for the idea first | a duplicate is closed without a question | — |
| **2. The issue**, yours, with the facts of §5 | what upstream asks; you learn whether they want it before any review is spent | may meet no interest: the patch then stays ours (staged, as now) |
| **3. One PR: part 1** | the smallest thing that is the feature; one open PR is all a new contributor may have | parts 2 and 3 wait for it |
| 3'. one PR with the three | one review, the whole design visible | 1,170 lines at once from a new contributor; the canary is the part most likely to be questioned and would hold the rest |
| **4. Parts 2, then 3**, as follow-ups | each small, each optional to them | if 3 is refused, our canary stays a patch we carry |
| **5. LocalAI's half**, once its llama.cpp pin has the feature | builds on their CI as it is | waits on upstream's merge and their pin |

Recommended: 1, 2, 3, 4, 5. If you do nothing, nothing is filed and the node
builds from the staged series as it does now.

## 4. Reading the code

Read part 1 in this order; each line is a claim to check against the diff.

**The contract** — `tools/server/README.md`, section "Context on demand".
The rules you set: a request without `keep_context_ms` leaves the context
loaded; one with it has the context freed that long after its answer; among
keeps the latest deadline wins; the load's own context goes once idle.

**`common/common.cpp`, `common.h`** — how a context is made again.
- `common_init_result::free_context()` drops the context and keeps the
  model, adapters and samplers; `init_context()` creates one from the
  context params the load resolved (`impl::cparams`) — no memory fitting
  again — and re-attaches the thread pools (`common_threadpools::attach`).
- `common_init_from_params()` was one function; its tail — control vectors,
  the reranking check, lora adapters, warmup — is now
  `common_init_context_apply()`, the SAME code moved, returning the step it
  stopped at (0: none). The load stores that in `init_stop` and behaves as
  before. `common_init_context_from_params()` = `init_context()` + the same
  steps, accepted only if they end where the load's ended. That last rule
  is the fix of the defect in the brief ("The reranker"): do not simplify it
  to "refuse if a step stops".

**`tools/server/server-queue.cpp`, `.h`** — when the free happens.
`on_idle()`: one callback, called by the loop's thread each time it finds no
task, WITHOUT the queue's mutex (a free takes milliseconds; tasks are posted
meanwhile, and `wait_for` re-checks the queue). Its return value shortens
the wait, so the context goes at the keep's deadline, not a second later.

**`tools/server/server-context.cpp`** — the feature.
- State: `t_ctx_keep_until` (latest deadline asked), `ctx_hold` (the last
  task carried no keep), and three properties cached at the load because
  they are read while there is no context (`n_ctx_seq`, `mixed_batch`,
  `pooling_type`).
- `context_release()` frees; `context_acquire()` creates and re-binds the
  slots and the batch; `context_keep()` runs when a slot is released;
  `context_idle()` is the callback. ONLY the loop's thread calls them —
  that is the whole concurrency argument, check it by reading who calls.
- In the task dispatch: a task that needs a context and finds none creates
  it; a failure answers `503` and the server stays up
  (`task_needs_context()` lists the task types that do not need one).
- `get_meta()`, `n_ctx_slot()`, `use_mixed_batch()` no longer read the
  context (HTTP threads call them).
- Refused at load with speculative decoding or a multimodal projector:
  their contexts are bound to the model's.
- `LLAMA_SERVER_CTX_CREATE_FAIL` exists for the tests only, like the
  `LLAMA_SERVER_SLOTS_*` variables beside it.

**Part 2** — `server_task::t_ctx_create_us`, copied to each result of the
task; `server_task_result_embd::to_json_non_oaicompat()` prints it. Why it
exists: a client's clock cannot tell a creation (the context may have been
kept for another client's request).

**Part 3** — `common_init_result::canary_record()` / `canary_check()`
(`common.cpp`): a fixed short input (at most 64 tokens) run like the warmup,
its pooled embedding or last-token logits compared with `memcmp`; recorded
twice at the load (a model that does not reproduce itself refuses to start,
or runs with `--no-context-canary`); checked in `context_acquire()` before
any task; a mismatch discards the context and answers `503`. After `POST
/lora-adapters` the next context records it again (`ctx_canary_stale`).

## 5. The facts you have (yours: measured on your node)

From the brief §10.1 (München, an Arc B70, three embedding models on test
copies):

| | bge-m3 | Nemotron embed | Qwen3-4B |
|---|---|---|---|
| memory back to the device per free | 561 MiB | 1,887 MiB | ~1,870 MiB |
| a creation's cost in a request | ~40 ms | ~55 ms | ~23 ms |
| 300 creations with a keep of 0 | byte-equal vectors | byte-equal | byte-equal |

Why `--sleep-idle-seconds` does not do it: it unloads the weights too, so the
next request pays a full load (seconds), and its unit is seconds — the useful
keeps here are 20–500 ms. The two work together.

What a reviewer may ask, and where you stand:

| point | as built | the alternative |
|---|---|---|
| the canary on by default | a re-created context is new memory; a wrong one would answer silently | off by default, or part 3 not sent at all |
| a request's key in the body | `keep_context_ms`, like Ollama's `keep_alive` but for the context and in ms | a header; server-wide only (`--keep-context-ms` alone) |
| no speculative decoding, no mmproj | refused at load, said in the README | a later PR |
| three new flags | `--context-on-demand` is the feature; `--keep-context-ms` serves clients that cannot be changed; `--context-canary` comes with part 3 | part 1 with `--context-on-demand` alone |

## 6. What was run

- llama.cpp `b8abe41`, CPU: master alone gives 83 passes and 46 failures on
  nine server suites with the stand-in models (huggingface.co is not
  reachable from the session; the failures are the stand-ins' content); with
  the three parts, the same 83 and the same 46, and the feature's own tests
  pass at each part (14, 16, 21). The three files, applied in order to a
  clean checkout, give the tree that was tested (tree hashes equal).
- NOT run: llama.cpp's full CI (`ci/README.md`, which upstream asks you to
  run before publishing); any GPU backend on master.

## 7. When upstream has moved

The diffs are cut from three commits on `b8abe41`. To move them: apply the
three in order on the new master (`git apply --3way`), build
`llama-server`, run `tools/server/tests/unit/test_context_on_demand.py`, cut
them again and update the hash at the top. The staged patch the node builds
from is separate (`patches/staged/llama.cpp/0011-…`, on our pin) and does not
move with them.
