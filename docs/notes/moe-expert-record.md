# The MoE cache starts from a record of the experts used (2026-11-24)

*A design note for the architecture session's review, with what is built.
Operator, relayed by München: "I want … the MoE preloading in VRAM based on
the recent experts used"; and that morning, after the 35B's rows: "is it
possible to keep an history of the experts used recently to know what
already load in VRAM from the beginning and what swap to cache first?".
BUILT, its own logic tested, compiled for the card; the fill itself has
NOT run: it needs a card beside the host. §5 is the node's measurement.*

## 1. What is there already, and what is missing

The engine has the cache (llama.cpp's own `llama-moe-cache`, at our pin):
a mixture-of-experts model keeps its experts in host memory (`n_cpu_moe`)
and the ones it uses most in a card buffer of `moe_cache_mib`. München's
rows of the morning are that arm: 8,192 MiB → 11.3 GB on the card, 57–70
tok/s; 4,096 → 7.2 GB, 55–62 (backend-qualification-2026-11-21.md §3).
Per layer and expert it keeps a score of use that decays over 128 tokens;
an expert not in the cache is uploaded at its first use, and the score
orders what is evicted.

What is missing is the operator's sentence. The score lives and dies with
the CONTEXT. Every new context — every load, under LocalAI — starts with an
empty cache and no history, and fills it one miss at a time, each an upload
in the middle of a token. The first answer after a load is the slow one.

## 2. What is built

Patch: `deploy/localai/patches/staged/0017-llama-cpp-moe-record.patch` (one
llama.cpp patch, 615 lines in 6 files; in `staged/engine-next.series`).

| part | what |
|---|---|
| the record (`src/llama-moe-record.h`) | per layer and expert, the lookups that selected the expert, decayed over 4,096 tokens of its layer — answers, where the cache's own score decays over a hundred tokens |
| the fill | when a cache is created, the best-ranked experts of each group of layers, as many as it has slots, are uploaded in runs of consecutive experts — inside the context's creation, before any token |
| the eviction order | seeded from the same record: each expert put in gets the score its recorded use would have given it (the record's rate of use in the cache's own units), so what leaves first is what the record ranks last; real uses take over as they come |
| keeping it | when the cache goes, what it saw is folded in: in memory with the model (the next context of the process), and in a file (the next process) |

The architecture session's conditions, each as built:

| asked | built |
|---|---|
| the record is the ENGINE's state, per weights file and engine build, beside the engine's own cache, never Louise's | a file `moe-record-<hash>.bin` in the directory the model's env names (`LLAMA_MOE_RECORD_DIR`); the hash and the file's header carry the model's name, sizes and shape AND a stamp compiled into the library, so another model's or another build's file is not even opened by name, and is refused if it is |
| switched by the model's env; off = today's engine | unset: nothing is read, kept or filled |
| placement must not change an answer | by construction with no share of the misses on the CPU (`moe_cache_cpu_share` 0, the default and the node's): an expert in the cache and one uploaded at a miss are the same bytes through the same kernels. THE PROOF IS THE NODE'S (§5: the same token ids at temperature 0). With a CPU share above 0 the cache's state already decides today which kernels compute a miss, and a cache that starts full applies the share from its first token: documented, not claimed equal |
| a record that cannot be read is ignored | cut, changed, noise, a NaN, other weights, another build: one log line, the model loads and starts cold (tested: every cut and every changed byte) |
| THE FILL BELONGS TO THE LOAD | it is in the cache's constructor, which the context's constructor calls: under LocalAI, inside the load and before the warm-up run. Bounded by the cache's size: at the card's 6.2–6.4 GiB/s (München), 8,192 MiB are about 1.3 s; the fill's own log line says its MiB and its ms |
| the file written by temporary name and rename, off the token path; a kill leaves the previous file whole | written in the cache's destructor (the unload's graceful path), never while it computes; `rename` over the old file |
| the card bytes at rest | UNCHANGED: the cache's buffer is allocated whole and cleared when the cache is created, as before; the record decides what is in it. The keeper's size for the model does not move |
| counts, no text; on the node only | per layer and expert, a float; never in a SHARD or a statement |

**Do not reinvent the wheel.** The cache, its classes of use and its
eviction are llama.cpp's own; the record adds a second, slower decay of the
same counts and a file. The named alternative is the cache's own simulator
(`scripts/moe-cache-tune.py` at the pin, which replays a routing trace
through a copy of the cache's logic): it tunes the SIZE from traces and does
not persist anything; a trace-driven preload would need the trace of a
"typical" session kept beside each model, which is what this record is,
maintained by use instead of by hand.

## 3. What was verified here (no card)

| check | result |
|---|---|
| the record's own test (`tests/test-moe-record.cpp`, standard library only) | 29 checks: the choice of experts and its ties, the decay by layer, the file read back, other weights and another build refused, a file cut at ANY byte or with ANY byte changed refused, noise, NaN and negative scores refused, written whole or not at all |
| it compiles | g++ (the CPU build of the whole candidate source) and Intel's compiler 2025.3.3 (`libllama` with the patch) |
| it applies in the build's order | after the port, the three fixes, parking and its switch: the candidate's engine source |
| the fill and the seeded eviction | NOT RUN. The cache needs a card beside the host (it refuses to be created without one), and none of the stand-ins tried here could give one: the engine's SYCL device discovery refuses the CPU device, and a software Vulkan device needed a downloaded compiler binary that this session is not permitted to run |

## 4. What it should bring, and what would make it not worth having

A cold cache uploads every expert it meets: under LocalAI that is the first
answer after each load of the chat model. With the record, those uploads
are made once, in runs, inside the load — which Louise now starts ahead,
while the search runs. If the first answer's misses are few (a small cache
that turns over anyway, or a first answer that touches experts no record
predicted) the gain is small: §5's hit and miss lines say it directly, and
a record that does not help is switched off by removing one env line.

## 5. The measurement on the node (München; a copy of the 35B as in its cache arm)

`n_cpu_moe` and `moe_cache_mib: 8192` as in the morning's arm, one fixed
question at temperature 0 with a fixed seed, `verbosity: 4`.

| run | env | read |
|---|---|---|
| A, today | none | the load's seconds; the time to the first token; tok/s over the first 64 tokens; the cache's lines at unload — "hits = …, misses = …, uploaded = … MiB" |
| B, a second context of the same process | `LLAMA_MOE_RECORD_DIR=<dir>`, the copy on demand (`context_on_demand:true`, a keep of 0) asked twice | at the second context: "N experts put in the cache from the record of T tokens, X MiB uploaded in Y ms"; then the same figures as A |
| C, a new process reading the file | the same env, the copy unloaded and loaded again | "the record of T tokens of this model is read from moe-record-….bin", the fill's line, the same figures as A, and THE LOAD'S SECONDS against A's |
| the proof | A, B, C | the token ids of the three answers equal |

What decides: the first token and the first 64 tokens of C against A, C's
load against A's (the fill moved there), and the misses of C's first
answer.
