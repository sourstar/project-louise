# The MoE cache starts from a record of the experts used (2026-11-24)

*A design note for the architecture session's review, with what is built.
Operator, relayed by München: "I want … the MoE preloading in VRAM based on
the recent experts used"; and that morning, after the 35B's rows: "is it
possible to keep an history of the experts used recently to know what
already load in VRAM from the beginning and what swap to cache first?".
BUILT, AND RUN ON THE CARD 2026-11-25 (§7): it is right — the answer's text
is the same with and without it, seven ways, and 47 % fewer MiB are uploaded
under the tokens — and it buys about a tenth of a second, because an upload
on this card costs far less than anyone assumed. §7 has the rows and the
choice; it is OFF until the operator chooses.*

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
llama.cpp patch, 6 files; in `staged/engine-next.series`).

| part | what |
|---|---|
| the record (`src/llama-moe-record.h`) | per layer and expert, the lookups that selected the expert, decayed over 4,096 tokens of its layer — answers, where the cache's own score decays over a hundred tokens |
| the fill | when a cache is created, the best-ranked experts of each group of layers, as many as it has slots, are uploaded in runs of consecutive experts — inside the context's creation, before any token |
| how much of the cache (`LLAMA_MOE_RECORD_FILL`, 0–1; unset: 1) | the share of the cache's slots the fill takes, the best-ranked experts first; 0 keeps the record and fills nothing. Added after the node's traces (§6): a whole fill uploads the whole cache, and half of it is not used by the answer. A value that is not a share is said and ignored |
| the eviction order | seeded from the same record: each expert put in gets the score its recorded use would have given it (the record's rate of use in the cache's own units), so what leaves first is what the record ranks last; real uses take over as they come |
| keeping it | when the cache goes, what it saw is folded in: in memory with the model (the next context of the process), and in a file (the next process) |

The architecture session's conditions, each as built:

| asked | built |
|---|---|
| the record is the ENGINE's state, per weights file and engine build, beside the engine's own cache, never Louise's | a file `moe-record-<hash>.bin` in the directory the model's env names (`LLAMA_MOE_RECORD_DIR`); the hash and the file's header carry the model's name, sizes and shape AND a stamp compiled into the library, so another model's or another build's file is not even opened by name, and is refused if it is |
| switched by the model's env; off = today's engine | unset: nothing is read, kept or filled |
| placement must not change an answer | by construction with no share of the misses on the CPU (`moe_cache_cpu_share` 0, the default and the node's): an expert in the cache and one uploaded at a miss are the same bytes through the same kernels. THE PROOF IS THE NODE'S (§5: the same token ids at temperature 0). With a CPU share above 0 the cache's state already decides today which kernels compute a miss, and a cache that starts full applies the share from its first token: documented, not claimed equal |
| a record that cannot be read is ignored | cut, changed, noise, a NaN, other weights, another build: one log line, the model loads and starts cold (tested: every cut and every changed byte) |
| THE FILL BELONGS TO THE LOAD | it is in the cache's constructor, which the context's constructor calls: under LocalAI, inside the load and before the warm-up run. ONLY THE LOAD'S OWN CONTEXT: with context on demand, a context created for a question after the keep ran out is filled inside that question, before its first token — there the person pays the fill, and it pays only if the seconds it saves under the tokens exceed its own (§6). The live 35B is not that case: its YAML has no `context_on_demand` (München, 2026-11-24), so the load's context serves every question and the fill is paid once, in the load. Bounded by the cache's size: at the card's 6.2–6.4 GiB/s (München) 8,192 MiB were expected to take about 1.3 s — MEASURED 2026-11-25: 8,186 MiB in 173 ms (§7); the fill's own log line says its MiB and its ms |
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
| the record's own test (`tests/test-moe-record.cpp`, standard library only) | 35 checks: the fill's share read and refused and the slots it takes, the choice of experts and its ties, the decay by layer, the file read back, other weights and another build refused, a file cut at ANY byte or with ANY byte changed refused, noise, NaN and negative scores refused, written whole or not at all |
| it compiles | g++ (the CPU build of the whole candidate source) and Intel's compiler 2025.3.3 (`libllama` with the patch) |
| it applies in the build's order | after the port, the three fixes, parking and its switch: the candidate's engine source |
| the fill's CHOICES, without a device (`tools/measure/moe_record_replay.py`, §6) | its cold replay equals llama.cpp's own simulator (`scripts/moe-cache-tune.py` `replay()`), and its cold and filled replays equal the ENGINE's own `moe_cache_lru` and `llama_moe_record`, extracted from the candidate's source and driven as the cache drives them: 40 random cases, one and two groups, caches from a tenth of the experts to all of them; one case kept as the test's golden figures |
| the fill and the seeded eviction | not run HERE — RUN ON THE CARD 2026-11-25, §7. Here: the cache needs a card beside the host (it refuses to be created without one), and none of the stand-ins tried here could give one: the engine's SYCL device discovery refuses the CPU device, and a software Vulkan device needed a downloaded compiler binary that this session is not permitted to run |

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
Louise-shaped prompt P (2,048 tokens: it goes as large batches, as in
production, and the first 64 tokens are generated ones) at temperature 0
with a fixed seed, `verbosity: 4`.

Two rules of the shape (2026-11-24, before the pass):
- **the record C and D read does not hold the answer they measure.** A
  process with the record's directory answers two or three OTHER long
  prompts and is unloaded; that directory is copied for B, for C and for
  D. A record that holds P itself is the best case, not the case that
  decides (§6's 40–53 % is for other questions);
- **A, C and D run on the live 35B's own context options**, P sent right
  after the load. With a keep of 0 the load's context is freed before the
  question and the question's context is filled again before its first
  token: the fill is then counted twice and the load-ahead case never
  measured. The log says which context served the answer. P goes to the
  UNLOADED copy: request to 64th chunk is the whole wait of a person whose
  35B was not loaded, and the engine's own "model loaded" line isolates
  the load.

| run | env | read |
|---|---|---|
| A, today | none | the load's seconds; the time to the first token; tok/s over the first 64 tokens; the cache's lines at unload — "hits = …, misses = …, uploaded = … MiB" |
| B1, B2, a second context of the same process | `LLAMA_MOE_RECORD_DIR=<its copy>`, the copy on demand (`context_on_demand:true`, a keep of 0), P asked twice: B1 on a record of other questions, B2 on one that now holds P (the best case) | the fill is inside the first token here, by construction: "context created in … ms"; at each context: "N experts put in the cache from the record of T tokens, X MiB uploaded in Y ms"; then the same figures as A |
| C, a new process reading the file | `LLAMA_MOE_RECORD_DIR=<its copy>`, the live context options | "the record of T tokens of this model is read from moe-record-….bin", the fill's line, the same figures as A, and THE LOAD'S SECONDS against A's |
| D, the fill at a half and at a quarter | C's env and `LLAMA_MOE_RECORD_FILL=0.5`, then `0.25` | the fill's line (half, a quarter of the experts and MiB), the load's seconds, and the same figures as A |
| E, the load-ahead shape (if the pause has room) | A and C again, Louise's own warm request first, then P | the warm request's time and P's own: a short warm prompt goes through the cache as one batch and warms a cold cache by itself — what is left for the record to remove when the load is hidden |
| the proof | A, B1, B2, C, D | the answers equal (their texts: the API gives no token ids) |

What decides: the first token and the first 64 tokens of C against A, C's
load against A's (the fill moved there), and the misses of C's first
answer; then the share — the load's seconds PLUS the first 64 tokens' of
C against D's, since where the load is not ahead of the question the
person waits for both.

## 6. Before the engine is swapped: the node's own routing, replayed

The live engine already writes what the record would have been built from:
`LLAMA_MOE_CACHE_TRACE=<file>` in the model's env gives, per lookup, the
layer and the experts selected (no text). The file is opened anew by every
context, so each answer's file is moved aside after its unload. Then, with
no engine and no card:

    python3 -I tools/measure/moe_record_replay.py --mib 8192 answer1.trace answer2.trace answer3.trace other-language.trace

Each file is one load. Per load after the first it prints the experts
uploaded under the first 16 / 64 / 256 tokens and in all, three ways:
`cold` (today), `filled` (0017 as built: each expert seeded with the
eviction rank its record gives it) and `flat` (the same fill, every expert
seeded as if used once). The last file, on another subject, is the case of
a WRONG record. `--cap N` (repeatable) adds the seedings between the two:
the record's rank, no higher than a CRF of N.

It is uploads, not seconds: what an upload costs under a token is §5's.
`--fill S` (repeatable) replays a fill of the share S of the cache
(`LLAMA_MOE_RECORD_FILL`).

### The node's four answers (München, 2026-11-24, the live engine 9ea95e93a83c)

A copy of the 35B, `moe_cache_mib: 8192` (4,492 slots), no CPU share,
temperature 0, an unload between answers. Answer 1 (Italian, a legal
definition, 157 tokens through the cache) only builds the record: cold,
2,343 / 4,057 / 5,190 uploads under the first 16 / 64 tokens and in all.

| answer | tokens | cold: under 16 / 64 / in all | filled (0017) | removed under 64 | the fill uploads |
|---|---|---|---|---|---|
| 2, a medical question | 439 | 1,077 / 4,220 / 8,780 | 328 / 1,976 / 6,523 | 2,244, 53 % | 4,427 experts, 8,060 MiB |
| 3, a novel's plot | 424 | 1,077 / 4,111 / 6,868 | 608 / 2,460 / 5,176 | 1,651, 40 % | 4,492 experts, 8,186 MiB |
| 4, German, EU law | 110 | 1,077 / 4,298 / 4,970 | 704 / 2,580 / 3,258 | 1,718, 40 % | 4,492 experts, 8,186 MiB |

- **The seeding stands.** Filled never costs more than cold, the
  other-language answer included; the record's rank against a flat seed is
  within 3 % with no winner (flat ahead on answer 2, the rank on 3 and 4).
  The synthetic case where a wrong record cost more than a cold cache does
  not happen on this routing.
- **The record is about the first tokens only.** From token 64 on, a filled
  and a cold cache upload the same (answer 2, tokens 64–256: 2,607 against
  2,606): at 8 GiB the cache turns over 10–14 experts per token whatever it
  started with.
- **The cost nobody had a number for: the fill uploads the whole cache** —
  8.2 GiB inside every load, about 1.3 s at the card's rate — to remove 3.0
  to 4.1 GiB of uploads under the tokens. Half of what it uploads the answer
  never uses. Hence `LLAMA_MOE_RECORD_FILL`, added before the candidate's
  gates were spent (engine `b190673dbdbf`, replacing `5f926f4efa04`).
- Reading the figures: on answers 2–4 the prompt (26–32 tokens after the 4
  the load's warm-up left) went through the cache as ONE batch; Louise's
  prompts are thousands of tokens and go as large batches the cache is not
  asked for. Answer 1 has the production shape: a cold cache uploads 4,057
  experts, 7.4 GiB, under its first ~57 generated tokens. The 1,077 under 16
  tokens, the same on three answers, is the load's own warm-up sequence.

| the fill | pros | cons |
|---|---|---|
| none (the live engine) | nothing uploaded in the load | 4,057–4,298 uploads, 7.4–7.8 GiB, under the first 64 tokens after every load |
| the whole cache (0017, the switch unset) | the most removed: 40–53 % of those uploads | 8.2 GiB uploaded in every load for 3.0–4.1 GiB removed: a loss wherever the person waits for the load too |
| a half or a quarter (`LLAMA_MOE_RECORD_FILL`) | less uploaded for nothing | less removed; how much is `--fill`'s curve and §5's row D |

**The curve of the share** (the same four files, `--fill`): MiB removed
under the first 64 tokens for MiB the fill uploads.

| share filled | the fill uploads | answer 2 | answer 3 | answer 4, other language | removed per MiB filled |
|---|---|---|---|---|---|
| 1 (0017 unset) | 8,060–8,186 MiB | 4,090 MiB, 53 % | 3,015, 40 % | 3,131, 40 % | 0.51 / 0.37 / 0.38 |
| 0.75 | 6,140 | 3,442, 45 % | 2,664, 35 % | 2,841, 36 % | 0.56 / 0.43 / 0.46 |
| 0.5 | 4,093 | 2,535, 33 % | 1,960, 26 % | 2,079, 27 % | 0.62 / 0.48 / 0.51 |
| 0.25 | 2,047 | 1,329, 17 % | 999, 13 % | 1,001, 13 % | 0.65 / 0.49 / 0.49 |
| 0.1 | 816 | 504, 7 % | 360, 5 % | 262, 3 % | 0.62 / 0.44 / 0.32 |

Close to a straight line: **no share removes as many MiB as it uploads**
(0.4–0.65 of a MiB per MiB), and a half keeps 62–66 % of the whole fill's
saving for half its upload. So the record does not save bytes; it MOVES
them — from one miss at a time under the first tokens to one run inside the
load. What that is worth is seconds, and only the card has them (§5):

- added: the load's seconds with the fill, minus without;
- saved: the seconds of the first 64 generated tokens without it, minus with.

Saved above added: the fill pays even where the person waits for the load,
and the whole cache is the default. Saved below added: it pays only where
the load is ahead of the question (Louise's load-ahead), and the default is
set by that. A straight line puts the best share at one end, so row D is
one middle point (0.5), to confirm the seconds follow the uploads.

Recommended: nothing is switched on until those two numbers are in.

On synthetic routing (a cache a third the size of its experts, an eighth of
a layer selected per token) the seeding DID matter: the record's rank cost
24 % more uploads than a cold cache on another subject, a flat seed none.
Kept as a warning for a smaller cache or another model; `--cap N` replays
the seedings in between.

## 7. On the card (München, 2026-11-25, engine b190673dbdbf, the node paused)

Copies of the 35B (`cpu_moe:true`, `moe_cache_mib:8192`, no CPU share,
`verbosity:4`), the card emptied before each arm. P is the 2,048-token
prompt of phase 0, temperature 0, seed 0, 160 tokens asked; **the answer is
42 tokens**, so there is no 64-token figure. The record was built by a
process answering three OTHER long prompts (19,709 tokens, a 41,051-byte
file) and copied per arm, as §5 says.

| arm | request to first token | the engine's own load | the fill | MiB uploaded under the tokens (batches of ≤ 8) | hits |
|---|---|---|---|---|---|
| A, no record, P to an unloaded copy | 27.12 s — the file's FIRST load after the gates (page cache): not to be read against C | 23.82 s | — | 7,742.65 (4,254 misses) | 71.51 % |
| B1, a record of other questions, context on demand | 16.00 s | 13.11 s | 4,492 experts, 8,186.38 MiB in 172.8 ms | 4,310.27 (2,372) | 84.13 % |
| B2, the same process's second context, the record now holding P | 3.215 s | — (context created in 437.79 ms, its canary reproduced) | 8,186.38 MiB in 173.4 ms | 3,908.78 (2,151) | 85.05 % |
| C, a new process reading the file | 15.77 s | 12.51 s | 8,186 MiB in 172.9 ms | 4,135.30 (2,277): −47 % against A | 84.75 % |
| D, C with `LLAMA_MOE_RECORD_FILL=0.5` | 15.73 s | 12.41 s | 2,246 experts, 4,093.19 MiB in 92.1 ms | 5,290.71 (2,910): −32 % | 80.51 % |
| A2, the load-ahead shape, no record | warm "OK" 13.27 s; then P: 2.882 s, 3.51 s for the answer | | | not captured | |
| C2, the same with the record | warm "OK" 13.21 s; then P: 2.817 s, 3.39 s for the answer | | | not captured | |

The answer's text is the same in all seven (sha256 f9ca4ff7b3c1fe94). Every
load of the file after its first took 12.3–13.1 s, with or without a fill.

- **It is right.** The same text with no record, in a second context of the
  process, in a new process reading the file, with half a fill, and in the
  load-ahead shape. The uploads under the tokens fall by 47 % — the replay
  of §6 said 40–53 % — and by 32 % with half the cache filled.
- **It costs almost nothing in the load:** 8,186 MiB in 173 ms, 4,093 MiB in
  92 ms, about 47 MiB per millisecond — seven times the rate the design
  assumed. The load's seconds do not show it.
- **And it buys almost nothing, for the same reason:** an upload is that
  cheap under a token too. The 3,607 MiB it removes are worth 0.08 s at that
  rate; measured with the load hidden, 0.065 s to the first token (2.882 →
  2.817 s) and 0.12 s over the answer (3.51 → 3.39 s). What the first token
  waits for is the prefill of the prompt. The record's builder bounds the
  whole prize: the first 64 chunks of a process's FIRST answer took 0.898 s,
  of its second and third 0.762 and 0.742 s — a cold cache costs 0.14–0.16 s
  over 64 tokens, and the record takes about half of that back.

By §6's rule: saved 0.07–0.12 s, added 0.17 s (the whole cache) or 0.09 s
(half). Where the person waits for the load it is a wash; where the load is
ahead of the question it is a tenth of a second on the first answer after
each load of the 35B. A half fill has nothing measured in its favour.

| the record | pros | cons |
|---|---|---|
| off (today) | nothing to keep on the node, nothing to remember | the first answer after each load of the 35B is about 0.1 s slower when the load was ahead |
| on, the whole cache: one env line on the 35B, its directory under the node's cache | about 0.1 s on that answer; the answers are the same, shown seven ways; 0.17 s inside a 12.5 s load | a file per model and engine build, and a mechanism kept alive for a tenth of a second; nothing gained where the person waits for the load |
| on, half the cache | 0.09 s in the load instead of 0.17 | removes 32 % of the uploads instead of 47 %; no second measured in its favour |

Recommended: on, the whole cache, ONCE THE LOAD AHEAD IS LIVE for the chat
model — then it is a gain with nothing against it but its size, and this
project deploys measured gains however small. Before that it is a wash and
stays off. It is the operator's word on the node (München's one line);
nothing is switched on if he says nothing.

**DECIDED (the operator, 2026-11-25: "I accept your recommendations for all
the decisions above"): on, the whole cache, once the load ahead is live for
the chat model — not before.** Then the 35B's file gets
`LLAMA_MOE_RECORD_DIR` (a directory under the node's cache) and no
`LLAMA_MOE_RECORD_FILL` line. München's, when the architecture session says
the load ahead is live.

What would change the picture: a model whose cache turns over harder, or a
link where an upload is dear. The replay tool answers the first from traces;
the fill's own log line answers the second.

