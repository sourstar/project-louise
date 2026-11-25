# LocalAI: every action item for the LocalAI team

*One place for everything this project owes or asks `mudler/LocalAI`: the
defects found (B1–B23), the patches we carry (P1–P8, more staged), and the requests (R1–R6).
The operator files them upstream. Started 2026-10-02 while mapping LocalAI
against the model pool (`model-pool-prior-art.md`@c572c3b); the patches and
requests come from `model-pool-over-localai.md`@c572c3b, accepted
2026-10-03.*

*LIVE ON THE NODE since 2026-11-21 01:23:44Z: v5.0.1 with P1
(v5.0.1-1-g105104c32; operator: "Re-patch, then update"), image tagged
quay.io/go-skynet/local-ai:v5.0.0-gpu-intel because the TrueNAS catalog
offers only the v5.0.0 template (app 1.1.47).*
- *The way back: app 1.1.43 with louise-localai:v4.12.1-gpu-intel-p1
  (992f4c6b).*
- *`LOCALAI_AUTO_UPGRADE_BACKENDS=false` is set explicitly.*
- *The ADD-ON OWNER is off since 01:48:33Z (operator: "Switch it off";
  docs/notes/localai-addons.md): `LOCALAI_ADDONS_ENABLED=false`,
  `LOCALAI_ADDONS_BACKEND_LIBRARY=false`. On by default, in local-owner mode
  with sign-in off, it let any caller on the LAN install an unconfined
  process as LocalAI's user, or a backend outside the calibration gates.
  `/api/addons/resources` now answers 503. The same 96 backends, the
  fingerprint vectors byte for byte and the rerank were held after it.*
- *Checked before against after (München):*
  - *96 backends, the same names and digests;*
  - *the sixteen fingerprint texts of NeuRIS and wikipedia, 32 of 32 byte for
    byte, the same runs;*
  - *the reranker's scores identical;*
  - *load_options for 5 of 5 loaded models, none changed;*
  - *the pin back, Ask answering.*
- *Two traps, for the next one:*
  - *the 1.1.47 template defines `LOCALAI_DOWNLOAD_STAGING_PATH` itself, and an
    `additional_envs` entry repeating it makes `app.upgrade` refuse;*
  - *`app.rollback` leaves the app STOPPED (LocalAI was down 01:17-01:20Z).*
- *A false difference to discount: v4.12.1's `/system` listed 38 `*.backup`
  entries that lived only in the old container's layer.*
- *v5.0.1's own endpoints tests fail 48 cases (Postgres testcontainers among
  them) identically with and without P1: upstream's, not ours.*

*Checked against v5.0.0 (2026-11-18, 1d08354) and v5.0.1 (2026-11-20,
dd5780a), 2026-11-21, read in the source against Louise's surface:*
- *Every endpoint Louise calls is there, its handler unchanged and behind
  the same `adminMiddleware`:*
  - *`/v1/embeddings`, `/v1/rerank`, `/v1/chat/completions`,
    `/v1/completions`, `/v1/tokenize`, `/v1/models`;*
  - *`/v1/audio/transcriptions`, `/v1/audio/speech`;*
  - *`/system`, `/backend/shutdown`, `/models/toggle-pinned`,
    `/api/models/config-json`, `/models/import`, `/backends`.*

  *`core/schema/localai.go` only adds fields.*
- *`backend.proto` only adds things: the `Forecast` RPC and its messages,
  and an `api_key` field; nothing is renumbered or removed. v5.0.1 does not
  touch it. Louise's backends (VeGa's wrapper, louise-python, the patched
  llama.cpp builds) still speak to it.*
- *Every environment variable the node sets is still read.
  `LOCALAI_AUTO_UPGRADE_BACKENDS` still defaults to false.*
- *CORRECTED the same day (München): the env var is NOT enough. LocalAI's
  runtime settings (`runtime_settings.json`, written from its UI) win over
  it. The node's held `"auto_upgrade_backends": true` from 2026-11-08:
  v4.12.1 never acted on it, but v5.0.1's upgrade checker did, the same
  morning. LocalAI itself upgraded intel-sycl-f16-llama-cpp-development,
  intel-sycl-f16-ced and intel-transformers, outside every gate, and logged
  "upgrade available" for about twelve more. No Louise engine runs on them:
  every model the repo stages is on a `vega-louise-*` or `louise-sycl-*`
  backend, and a changed engine states another run, which Louise refuses
  per answer (invariant 1). Now `auto_upgrade_backends` and
  `auto_upgrade_addons` are held false by
  deploy/vega/stage/localai_settings.py, which the deploy applies and
  checks (step 6a'), failing where LocalAI does not take them.*
- *The model-YAML fields Louise's models use are all read: `context_size`
  is only realigned in `LLMConfig`. The removed fields are the agent
  runtime's.*
- *Fixed upstream: B3 (`pkg/model/watchdog.go`: a backend whose stop fails
  is tracked again, "model could not be stopped, tracking it again") and B15
  (#13186: the OCI staging falls back to the OS temp folder).*
- *Unchanged, so still standing: B4, B11 (the reclaimer), B9 (no NaN
  handling in `/v1/embeddings`) and B14 (upstream's llama.cpp `Embedding`
  still ignores the token ids; P2 is ours).*
- *v5.0's own changes are not on Louise's path:*
  - *the agent runtime and chat page replaced (`/api/agents*` answer 404);*
  - *Studio and Talk as add-ons;*
  - *`backend: liquid-audio` gone;*
  - *`LOCALAI_AGENT_POOL_*` renamed;*
  - *its upstream llama.cpp bumped, which is not the engine our backends
    build.*
- *v5.0.1 adds native Windows, process liveness and downloader fixes. On
  Linux its system-backends default resolves as before
  (`/var/lib/local-ai/backends`).*

*Checked against v4.12.1 (2026-10-27; 308 commits from v4.11.0, read in
the source):*
- *The watchdog and reclaimer are unchanged (`pkg/model/watchdog*.go`), and
  so are `/v1/embeddings` and the llama.cpp `Embedding`, so B3, B4, B9, B11
  and B14 stand.*
- *`backend.proto` only adds fields (+90 lines, nothing renumbered), so a
  v4.11.0 backend still speaks to a v4.12.1 app.*
- *P1 applies cleanly. P2 needs its `prepare.sh` hunk re-based: a context
  line moved and the code hunks are untouched.*
- *v4.12.1 itself fixes one thing, SSRF on user-supplied URLs (#12853).
  It matters only to a LocalAI open to untrusted users, which ours is not.*
- *`LOCALAI_AUTO_UPGRADE_BACKENDS` still defaults to false. Upgrading the
  app alone leaves every installed backend, and so every stated engine
  (invariant 12), as it is.*

| id | kind | what | for us | upstream |
|---|---|---|---|---|
| B1 | defect | transformers backend, OpenVINO: the device test is always true, so the GPU-only settings never apply | — | not filed |
| B2 | defect | transformers backend, OpenVINO: embedding models are re-exported at every load, so OpenVINO's compile cache never applies | — | not filed |
| B3 | defect | watchdog: a backend whose eviction fails keeps running, no longer counted or evicted | availability | **fixed in v5.0.0** (a backend whose stop fails is tracked again); live on the node since 2026-11-21 (v5.0.1) |
| B4 | defect | memory reclaimer: memory held by another process makes it evict every model, one per 500 ms | availability: the B70 is shared | not filed |
| B5 | defect | startup: `LOAD_TO_MEMORY` is silently ignored with `max_active_backends = 1` | — | not filed |
| B6 | defect | distributed scheduler: evicts one model and places the new one there whether or not it now fits; an estimate of 0 skips the VRAM check | — (one node) | not filed |
| B7 | defect, may be intended | loader: when busy models cannot be evicted, the load goes ahead over `max_active_backends` | availability | not filed |
| B8 | defect | `POST /models/reload` never forgets a model whose YAML was removed: it lists and serves it until LocalAI restarts | the model pool's `DELETE` answers 502 and says so | not filed; seen on a real LocalAI, 2026-10-03 |
| B9 | defect | `/v1/embeddings`: a vector holding a NaN fails the whole request with 500 ("json: unsupported value: NaN"), losing every other vector and the reason | Louise retries it as the null vector llama-server sends (`embeddings._numeric_failure`) | not filed; seen on the node, 2026-10-04 (15c) |
| B11 | defect | memory reclaimer: a model being loaded has a zero `lastUsed` and is evicted first, as the "least recently used" — under memory pressure the model a request is waiting for is evicted every time, the request fails with 500 | availability: every Ask answer failed with 500 for minutes (2026-10-14) | not filed; seen on the node with v4.11.0, log and source in §B11 |
| B12 | defect | `intel-transformers` (v4.11.0's, as the gallery installed it 2026-10-05): optimum-intel 1.15.0 imports `openvino.runtime`, which the openvino 2026.4.1 beside it no longer has, so EVERY `OVModel*` type fails to load (`ModuleNotFoundError`, checked on the node 2026-10-17). `intel-rerankers` has optimum-intel 2.2.0 and is not affected | none since louise-python (docs/notes/backend-consolidation.md, U4) | not filed |
| B13 | defect | transformers backend, `Embedding`: prints every input in full to stderr ("Calculated embeddings for: …"), a whole document per line in LocalAI's log | — (U3) | not filed |
| B14 | defect | llama.cpp backend, `Embedding`: ignores `EmbeddingTokens`, which `/v1/embeddings` fills when given token ids; it embeds the empty text beside them and refuses it ("Input content cannot be empty"), so no client can embed tokens | **blocking** for a space that cuts by tokens: without token ids an input cannot always be cut to exactly its first N tokens (3 of 512 at 1,024 on the AMD peer); P2 | not filed; found 2026-10-18, source in §B14 |
| B15 | defect | `v4.12.1`: backend downloads are staged at `${basepath}/downloading` = `//downloading`, unwritable by LocalAI's user, so every backend install and auto-upgrade fails | backend updates (the operator wants them); set `LOCALAI_DOWNLOAD_STAGING_PATH` | **fixed in v5.0.0** (#13186: staging falls back to the OS temp folder); live on the node since 2026-11-21 (v5.0.1) |
| B16 | defect | llama.cpp backend (llama-server's code): never runs the encoder of an encoder-decoder model; a T5-family GGUF is decoded as if decoder-only and answers garbage (`<2cs><2cs>…`) | MADLAD cannot run on llama.cpp, so it runs on torch (17–38 s cold); P3 | upstream llama.cpp #5763 / #26565, no fix; §P3 |
| B17 | defect | `v5.0.1`, `/v1/audio/speech`: whether a model may be given a voice profile is decided from its `backend:` NAME against LocalAI's own table, before `tts.voice_cloning` is read — a backend installed under any other name (ours: the engine's digest in it) is refused "selected model does not support reference-audio voice cloning", and no key of the model file can say otherwise | OmniVoice on our engine cannot be asked for a recorded voice the way Louise asks (`localai://voice-profiles/<id>`): blocks the cut-over of speech, not the measurement | not filed; found on the card 2026-11-24 |
| B18 | defect | llama.cpp (ggml-sycl): a device-memory refusal while a graph runs aborts the process — the pool's commit is under `SYCL_CHECK` (`ggml_sycl_pool_vmm::alloc`, `UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY`) | on a card several models share, another model's load can kill a model with its request; it is what stands between the node and paging idle models instead of unloading them | not filed; seen on the card 2026-11-24; fixed by P14 (staged) |
| B19 | defect | llama.cpp backend (LocalAI's `grpc-server.cpp`, the handler of llama.cpp's own server): a SECOND SIGTERM/SIGINT, arriving while the backend cleans up after the first, calls `exit(1)` INSIDE the signal handler — the exit handlers and static destructors then run against the clean-up in progress and can block, the process alive and its card memory held | 3 of 14 unloads of a model behind our wrapper took LocalAI's whole 15 s and ended in its SIGKILL (München, 2026-11-24): LocalAI signals the backend's process GROUP, and anything in that group that forwards the signal delivers it twice | not filed; avoided on our side since 2026-11-24 (the wrapper leaves a backend already told 2 s to go, #856); fixed by P15 (staged) |
| B20 | defect | `v5.0.1`, voice profiles (`core/services/voiceprofile`): a request's lease on a profile's clip is a hard link under `voice-profiles/.leases/<store>/`, removed when the request ends — and by nothing else. A process that is killed leaves its lease directory and any lease in it for ever: no start, no delete looks at another process's directory | a deleted profile's RECORDING stays on disk behind a link that no listing of profiles shows — a person's voice outliving its delete. Seen on the node 2026-11-25 (München): nine lease directories dated 10-10 to 11-25, eight empty, one holding a 1,331,940-byte link made in October to a clip of a profile that still exists (same inode); had that profile been deleted, the bytes would have stayed | **to file upstream** (mudler/LocalAI); P19 (staged) |
| B21 | defect | `v5.0.1`, llama.cpp backend (`grpc-server.cpp`, `PredictStream`): only the stream's FIRST result is tested for an error. A later one — a graph that fails mid-generation, any error of llama.cpp's server after the first chunk — is turned into one more reply (an empty one: its JSON has no content) and the stream then ends with status OK | a generation cut short by a failure is delivered as a COMPLETE answer: the client sees a normal end of stream — on every chat model, today. Found by the independent reading of the park engine part (the architecture session, 2026-11-25), confirmed in the source, and MEASURED on a CPU build with a graph refused after the first chunk (`tools/measure/stream_fails_test.py`): before, OK after 4 of 50 chunks; after the fix, INTERNAL after 3 of 50 and the next answer whole. NOT measured on a card: München's row | **to file upstream** (mudler/LocalAI); **fixed: staged 0025, in the NEXT engine's candidate `engine-stream.series` (`792f050e318e`)**, not behind parking — with staged 0026, the test switch that lets a card show it (`GGML_SYCL_GRAPH_REFUSE`) |
| B22 | defect | `v5.0.1`, llama.cpp backend (`grpc-server.cpp`, `LoadModel`): a LoadModel for the model a process already holds is answered success, `Model already loaded`, by comparing the model's NAME alone. The same name under other options — another context size, other backend options, another file — gets the same success while the options of the first load go on computing | harmless while nothing sends a second LoadModel; not once that call is how a parked model comes back: a caller, or a proxy that states a run at each load, would believe its new options are the ones in force (invariant 12). Found by the same reading, confirmed in the source | **to file upstream**; fixed in the park engine part: the options of the load are kept, a repeat that differs is `FAILED_PRECONDITION` naming the fields and never their values (staged 0023, `engine-park.series`, parking-step-2.md §6, finding 7) |
| B23 | defect | llama.cpp's server with OUR context on demand (P11, the context canary): a canary the device REFUSES FOR MEMORY is answered `context canary failed`, counted in `context_canary_failures_total` and logged as a context that "does not reproduce the canary of the load" — `canary_check` says MISMATCH for every canary it could not compute. At the LOAD the same refusal says NONE: the model is served with "the contexts created on demand are not checked", unchecked for the life of the process. Reachable since staged 0018 made a refused pool commit fail its graph instead of ending the process | Louise takes the engine at its word (operator, 2026-11-11: "Wire the canary failure to the gate"): the run's statements are set aside and the collection gated again from scratch — forty minutes of gutenberg's embedding on the node, 2026-11-25 09:40:55Z, for one context of `vega-qwen3-embedding-4b` discarded in the second the card filled at a worker's start (1 of 493 creations; the engine's lines of that second were not kept, so that event is NOT proven to be this). And an encoder loaded on a full card may serve unchecked. MEASURED on a CPU build of `792f050e318e`'s source with the stand-in device that refuses graphs: 503 `context canary failed`, the counter at 1; at the load, served unchecked | ours (P11), not upstream's. Fixed inside the park engine part since its second cut (staged 0023) and now CUT OUT for the next engine: staged 0028, `engine-canary.series` (`619655e5833b`); parking-step-2.md §6b |
| **P1** | **patch we carry** | `GET /system` lists the options each loaded model was given, with their hash | none since VeGa: the wrapper receives `LoadModel` itself (vega.md §1). It was blocking for the model pool; kept for upstream. IN the node's LocalAI again since 2026-11-21 (v5.0.1 + P1, cherry-picked as 105104c32; operator: "Re-patch, then update"): its `/system` rows carry `load_options` (5 of 5 loaded models, as the runs state them). Before it, v4.12.1 upstream core carried none, and Louise read a model's options from the answering run's statement (`model_load.note_run_options`), as it still can | not filed; [`deploy/localai/patches/0001-system-list-load-options.patch`](../../deploy/localai/patches/0001-system-list-load-options.patch) |
| **P2** | **patch we carry** | llama.cpp backend: `Embedding` embeds `EmbeddingTokens`, with the special tokens a text gets (B14) | **blocking** for the token cut: VeGa's wrapper sends a cut as its first tokens to a backend that embeds them (`Gate._probe`) | not filed; [`deploy/localai/patches/0002-llama-cpp-embed-token-ids.patch`](../../deploy/localai/patches/0002-llama-cpp-embed-token-ids.patch) |
| **P3** | **patch we carry** | llama.cpp backend: the encoder of an encoder-decoder model runs (B16), as a llama.cpp patch in the backend's own `patches/` | MADLAD on llama.cpp (operator, 2026-10-27: "we need anyway a patched llama.cpp") | not filed; [`deploy/localai/patches/0003-llama-cpp-encoder-decoder.patch`](../../deploy/localai/patches/0003-llama-cpp-encoder-decoder.patch) |
| **P4** | **patch we carry** | llama.cpp backend: encoder-decoder slots are encoded together, in WAVES, and decode in one batch (on P3, which ran one slot) | MADLAD on llama.cpp at torch's speed: a question's 7 languages in one wave instead of 7 × 3 s | not filed; [`deploy/localai/patches/0004-llama-cpp-encoder-decoder-waves.patch`](../../deploy/localai/patches/0004-llama-cpp-encoder-decoder-waves.patch) |
| **P5** | **patch we carry** | llama.cpp backend (ggml-sycl): the batched F16 matmul converts a non-contiguous src1 for the GEMM that RUNS — the oneDNN flat copy only when oneDNN's GEMM runs, the gather for oneMKL (`GGML_SYCL_ENABLE_DNN=0`); before, the KQ product of two or more decoder tokens over an F16 K cache read the wrong queries (the P4 card fault, upstream-bugs.md S1) | the root cause of the wrong text in waves, found by code reading 2026-10-28 and matched by the 32-case pattern of upstream's own suite; the F16 KV cache back for MADLAD (−44 % per token against the f32 workaround) once the rebuilt tar passes its gates | **to file at ggml-org/llama.cpp as a PR** ([upstream-reports/ggml-llama-cpp-sycl.md](upstream-reports/ggml-llama-cpp-sycl.md), second section); [`deploy/localai/patches/0005-llama-cpp-sycl-batched-mul-mat-src1.patch`](../../deploy/localai/patches/0005-llama-cpp-sycl-batched-mul-mat-src1.patch) |
| **P9 (staged)** | **patch staged, not in the series yet** | llama.cpp backend: the encoder output is kept PER SEQUENCE (`llama_memory_enc_dec`, stage 1 of llama-cpp-encoder-decoder.md), so a slot is encoded while others decode; on P3 and P4 | a late request joins the next decode step instead of waiting for the wave; the upstream shape (no public API) | not filed; [`deploy/localai/patches/staged/0006-llama-cpp-encoder-decoder-memory.patch`](../../deploy/localai/patches/staged/0006-llama-cpp-encoder-decoder-memory.patch); moves into the series after its card gates |
| **P10 (staged)** | **patch staged, not in the series yet** | llama.cpp backend: the encoder outputs live ON THE DEVICE (stage 2: a persistent store written by the encoder graph, read by the decoder's `ggml_get_rows`; no host copies), on P5 | the shape upstream asked for (a persistent buffer, no host copies); the per-step upload gone | not filed; [`deploy/localai/patches/staged/0007-llama-cpp-encoder-decoder-store.patch`](../../deploy/localai/patches/staged/0007-llama-cpp-encoder-decoder-store.patch); moves into the series with P5 after the card gates |
| **P11 (staged)** | **patch staged, not in the series yet** | llama.cpp: OmniVoice (k2-fsa/OmniVoice) speaks on llama.cpp's own audio generation — the architecture `omnivoice` in libllama, the projector type in libmtmd, the pipeline in mtmd-helper, omnivoice.cpp's host rules vendored (MIT); a text model and an mmproj repacked from omnivoice.cpp's two files | one backend for speech too: the omnivoice-cpp backend (another ggml, another build, unpinned) is no longer needed; the model as its authors mask it | not filed (upstream llama.cpp has no OmniVoice; an upstream pull request needs a converter from the Hugging Face checkpoint and the operator's own review); [`deploy/localai/patches/staged/0009-llama-cpp-omnivoice.patch`](../../deploy/localai/patches/staged/0009-llama-cpp-omnivoice.patch); speech from text, from a clip and from a described voice gated on the CPU 2026-11-24; the card before the series |
| **P12 (staged)** | **patch staged, not in the series yet** | llama.cpp backend: the TTS call takes a voice profile's transcript (`params["ref_text"]`), a described voice (`instructions`, or a `voice` that names no file) or no voice at all, for a model that clones AND has a "tts" chat template; `LOCALAI_HAS_TTS_TEMPLATE` probes for the template, which is older than per-session voices | LocalAI resolves a voice profile to a clip and its transcript, and its own llama.cpp backend dropped the transcript and refused a request without a voice; needed for P11's model to be served as Louise asks. Also passes `params["seed"]`, `["flow_steps"]`, `["cfg_scale"]`: without a seed no speech request on this backend is reproducible | not filed; [`deploy/localai/patches/staged/0010-llama-cpp-tts-voice-modes.patch`](../../deploy/localai/patches/staged/0010-llama-cpp-tts-voice-modes.patch); its `grpc-server.cpp` half first compiles with the test engine on the node |
| **P13 (staged)** | **patch staged, not in the series yet** | LocalAI's server (`core/config`): `VoiceCloningForModel` honours a model's explicit `tts.voice_cloning: true` for a backend the capability table does not know, with the reference contract the cloning backends share | the fix of B17: a model on an engine installed under its own name can be asked for a saved voice profile, as Louise asks | **to file upstream** (the operator, 2026-11-24: "add the improvement to the list of the fixes I have at some point send"); [`deploy/localai/patches/staged/0011-config-voice-cloning-opt-in-unknown-backend.patch`](../../deploy/localai/patches/staged/0011-config-voice-cloning-opt-in-unknown-backend.patch); LocalAI's `core/config` tests pass with it; the node's server not rebuilt yet |
| **P18 (staged)** | **LIVE in engine d531d5d804f1 since 2026-11-25; ON THE CARD: IT HOLDS** (two refusals answered 500 "Compute error." by one process, the third served with the control's scores) | llama.cpp (ggml-sycl): the pool's refusal is a plain struct, not a `std::exception` — `CHECK_TRY_ERROR` around every op catches those and `SYCL_CHECK` aborts — so that it reaches the graph's catch (two `static_assert`s hold the shape) | what P14 was meant to be: **P14 DID NOT HOLD ON THE CARD** (2026-11-25: each process gone at its first refusal, `ggml_sycl_op_mul_mat` line 4221) | to file upstream WITH P14, as one change; [`deploy/localai/patches/staged/0018-llama-cpp-sycl-pool-refusal-reaches-the-graph.patch`](../../deploy/localai/patches/staged/0018-llama-cpp-sycl-pool-refusal-reaches-the-graph.patch); the real macros under Intel's compiler and a CPU stand-in above the backend pass here; its card row is 0016's switch |
| **P14 (staged)** | **live since 2026-11-24, and it does not do what it says: see P18** | llama.cpp (ggml-sycl): the pool's refused commit throws, `ggml_backend_sycl_graph_compute` answers `GGML_STATUS_ALLOC_FAILED`, the server's existing "Compute error." answers the request and the process lives | the fix of B18 | **to file upstream** (ggml-org/llama.cpp); [`deploy/localai/patches/staged/0013-llama-cpp-sycl-pool-refusal.patch`](../../deploy/localai/patches/staged/0013-llama-cpp-sycl-pool-refusal.patch); compiled here 2026-11-24, never run; its test is on the card |
| **P19 (staged)** | **LIVE in the node's SERVER since 2026-11-25 (v5.0.1-3-g3ee38c799 = v5.0.1 + P1 + P13 + P19)**; patch staged, for the SERVER (v5.0.1 + P1, the node's server tree; not a patch of the engine and in no engine series) | LocalAI's server (`core/services/voiceprofile`): a Store keeps an owner file locked in its lease directory for as long as it lives; lease directories whose owner is gone are removed, with their leases, when a Store starts and at every profile delete; a living Store's leases are left alone; a directory with no owner file goes once it is a day old | the fix of B20 | **to file upstream** (mudler/LocalAI); [`deploy/localai/patches/staged/0019-voiceprofile-abandoned-leases.patch`](../../deploy/localai/patches/staged/0019-voiceprofile-abandoned-leases.patch); LocalAI's own tests pass with it on v5.0.1 + P1 and on the whole candidate tree (25 specs, 6 new; the first new one fails without the change), and `core/http/endpoints/openai`'s |
| **P15 (staged)** | **patch staged, not in the series yet** | llama.cpp backend (`grpc-server.cpp`): a second termination signal leaves through `second_signal.h` — `write(2)` and `std::_Exit(1)`, async-signal-safe calls only — with a standalone test LocalAI's own unit-test runner picks up | the fix of B19 | **to file upstream** (mudler/LocalAI; the same handler is in ggml-org/llama.cpp's `tools/server/server.cpp`); [`deploy/localai/patches/staged/0014-llama-cpp-second-signal-exit.patch`](../../deploy/localai/patches/staged/0014-llama-cpp-second-signal-exit.patch); its test compiled and run here, the backend itself not built |
| **P16 (staged, a prototype)** | **patch staged, in the next live engine's candidate, off unless a model's env sets it** | llama.cpp (ggml, ggml-sycl, the server): PARKING — a model's weights leave the device and come back at the same addresses (physical memory behind a reserved virtual range), switched on by a model's env only; the engine half of `models-beyond-the-card.md` | more models than the card holds without a process start per swap (operator, 2026-11-24) | not for upstream yet: a prototype for one measurement ([`parking-prototype.md`](parking-prototype.md)); [`deploy/localai/patches/staged/0015-llama-cpp-park-weights.patch`](../../deploy/localai/patches/staged/0015-llama-cpp-park-weights.patch); compiled and tested on a CPU stand-in here, never run on a card |
| **P17 (staged)** | **patch staged, in the next live engine's candidate, off unless a model's env sets it** | llama.cpp (the MoE cache): the experts used are recorded beyond a context — in memory with the model and in a file — and a cache is filled from the record when it is created, its eviction order seeded from it | the first answer after every load of a MoE model with its experts in host memory is the slow one: the cache starts empty and uploads miss by miss (operator, 2026-11-24) | **to file upstream** (ggml-org/llama.cpp, whose cache it extends) once the card has measured it; [`deploy/localai/patches/staged/0017-llama-cpp-moe-record.patch`](../../deploy/localai/patches/staged/0017-llama-cpp-moe-record.patch); [`moe-expert-record.md`](moe-expert-record.md); the record tested here, the fill not run |
| **P20 (staged)** | **LIVE in engine `619655e5833b` since 2026-11-25 15:18:30Z** (its own candidate `engine-lang.series`, `aa88ab5c356a`, was never built); the card row (`en-GB` the bytes of `en`) is being taken | llama.cpp (the speech port, ours): a language TAG — `it-IT`, `en_GB`, `IT` — is the language of its first subtag; it was read as no language, in silence, and the text spoken in the reference clip's accent. LocalAI sends tags: a voice profile's language is its fallback when a request names none | the operator's "thick italian accent"; Louise's half is #881 | `staged/0022-llama-cpp-omnivoice-language-tag.patch`; omnivoice-on-our-backend.md §14 |
| **P21 (staged)** | **patch staged, in the candidate `engine-park.series` (`ed93e3c1b2e2`), not built** — its second cut accepted by the architecture session 2026-11-25 | llama.cpp + its backend: PARKING WHEN TOLD (step 2, the engine part) — `Free` parks a model that can park, a `LoadModel` with the options of the load brings it back, a request brings it back by itself and never parks, `Status` says where the weights are; one answer for a full device (`RESOURCE_EXHAUSTED` `no_room_on_device: …`), a failure never answered as a refusal; B22 fixed in it | parking-step-2.md §6 | `staged/0023-llama-cpp-park-when-told.patch` |
| **P22 (staged)** | **patch staged, for LocalAI's SERVER, in no engine series, not built** — accepted as written; it goes on the node with the server part | `pkg/model`: a backend LocalAI just started is asked whether it is ready every 50 ms instead of every 2 s, inside the same wait (R6) | up to two seconds on every cold load; parking-step-2.md §7 | `staged/0024-model-readiness-poll.patch` |
| **P23 (staged)** | **LIVE in engine `619655e5833b` since 2026-11-25 15:18:30Z** (`engine-stream.series`, `792f050e318e`, was built and never gated: `engine-canary.series` carries its lines); the card row, through LocalAI's SSE, is being taken | llama.cpp backend (`grpc-server.cpp`): a stream that meets an error after its first chunk ends with that error (B21) | a truncated chat answer was delivered as complete; measured before and after on a CPU build | `staged/0025-llama-cpp-stream-fails-after-its-first-chunk.patch` |
| **P24 (staged)** | **LIVE in engine `619655e5833b` since 2026-11-25 15:18:30Z, off unless a model's env sets both variables** (no model file does: tests hold it) | llama.cpp (ggml-sycl): a TEST switch, `GGML_SYCL_GRAPH_REFUSE=N` with `GGML_SYCL_GRAPH_REFUSE_ARM=<file>` — once the file exists the next N graphs are abandoned as after a refused pool commit | the only way a card can show a failure in the MIDDLE of an answer: a pool has done its growing before the first token, so `GGML_SYCL_POOL_REFUSE` cannot be timed there | `staged/0026-llama-cpp-sycl-graph-refuse-switch.patch` |
| **P25 (staged)** | **LIVE in engine `619655e5833b` since 2026-11-25 15:18:30Z** (`engine-canary.series`); the card row on a copy is being taken | llama.cpp's server and library (ours, on P11): a context canary the device refuses for memory is NOT a canary that failed. `llama_process` returns `LLAMA_PROCESS_NO_MEMORY` (-4) where the backend could not allocate memory for the graph (it was -2, like every other failure before a graph is computed); the canary says `COMMON_CANARY_NO_MEMORY`; at a request it is a context that cannot be created (503 `the context cannot be created now … try again later`, counted nowhere as a canary failure), at the load it fails the load. A canary that was computed and differs is what it was. No new error type (that stays P21's) | B23. Through LocalAI the answer is a 5xx WITHOUT the words `context canary failed`, which `app/embeddings.py` already takes for a busy endpoint: the batch deferred, nothing set aside, nothing gated again. MEASURED on a CPU build with the stand-in (`tools/measure/canary_refused_test.py`): DEFECT twice before, 12 of 12 after; llama.cpp's own context-on-demand tests, 21; `refuse_cpu_test.py`, 13 of 13. NOT measured: a card — München's row is in the series' head (`GGML_SYCL_GRAPH_REFUSE=2`, staged 0026) | staged 0028; `engine-park.series` does not list it — P21 carries the same fix in its own form, on the same lines, held by a test |
| **P7** | **in the series since 2026-11-22** (was P10: staged, built into a separate backend, LIVE 2026-11-11; the qualification's F4) | llama.cpp: nvidia/llama-nemotron-rerank-1b-v2 ranks (`LlamaBidirectionalForSequenceClassification`: bidirectional Llama 3.2 1B, mean-pooled, a `score` head). Ported from ggml-org/llama.cpp#24083 (open) onto ddf48f8: converter class, the optional `cls.output` in llama-embed, BOS for a rerank template's prompt; this base pools before the head when the GGUF says classifier pooling mean, so no graph change | the reranker benchmark's arm A3 (operator, 2026-11-10: "Patch llama.cpp to support nemo reranker, tomorrow I want to see its benchmarks too") | upstream #24083 open; [`deploy/localai/patches/0007-llama-cpp-nemotron-rerank.patch`](../../deploy/localai/patches/0007-llama-cpp-nemotron-rerank.patch), built only from [`staged/nemotron-rerank.series`](../../deploy/localai/patches/staged/nemotron-rerank.series) into its own backend; joins the series only if A3 earns it. A3 earned it (operator, 11-11: B): the `[reranker]` of five collections on `louise-sycl-f16-llama-cpp-9d17b8a39845` (STATUS 11-11); joining the main series waits for the next engine change |
| **P8** | **in the series since 2026-11-22** (was P11: staged, yet LIVE on the node's engine since the 2026-11-12 trial; F4) | llama.cpp backend: CONTEXT ON DEMAND — a model loaded with the option `context_on_demand` keeps its weights, and a request that carries `metadata.keep_context_ms` has its context (KV cache and compute buffers) freed that long after its answer; the next request creates it again. A request that carries none leaves the context loaded, as ever (the option `keep_context_ms:N` gives such requests a keep). No RPC reads the `llama_context` any more (it is null while the model is idle) | the card holds ~34 GB of models on 32.6 GB, ~10 GB of it context that works seconds a day (operator, 2026-11-11: "when llama.cpp gets a request it allocates the context memory; when the processing is done it frees it"; "Implement it") — [idle-context-brief.md](idle-context-brief.md) | not filed — PREPARED for the operator to file himself (2026-11-11: "prepare everything for upstream, I will upload it eventually"; [`deploy/localai/upstream/llama-cpp-context-on-demand/`](../../deploy/localai/upstream/llama-cpp-context-on-demand/READING-GUIDE.md), [`localai-context-on-demand/`](../../deploy/localai/upstream/localai-context-on-demand/READING-GUIDE.md)); [`deploy/localai/patches/0008-llama-cpp-context-on-demand.patch`](../../deploy/localai/patches/0008-llama-cpp-context-on-demand.patch), built only from [`staged/context-on-demand.series`](../../deploy/localai/patches/staged/context-on-demand.series); CPU checks passed 2026-11-11, card gates not run |
| **P6** | **patch we carry** | llama.cpp (common/chat): Command R7B / Command A ("Command 3") tool calls are parsed by llama.cpp's own Cohere2 MoE parser; before, the autoparser preserved `<\|START_THINKING\|><\|END_THINKING\|><\|START_ACTION\|>` as ONE token string, the server dropped the three markers, and every R7B tool call was an HTTP 500 | Command R7B as a chat candidate (operator, 2026-11-03: "B, check vllm parser before reinventing") | **to file at ggml-org/llama.cpp as a PR** once proven on the node (#20786, closed unreproduced, was this); [`deploy/localai/patches/0006-llama-cpp-chat-cohere-command3.patch`](../../deploy/localai/patches/0006-llama-cpp-chat-cohere-command3.patch); in the series since 2026-11-06: engine ea2417d91dcc, every live model byte-identical, every gate passed |
| **P7** (retired 2026-11-21) | **upstream, in the base** | llama.cpp (ggml-sycl, dmmv.cpp): upstream's "sycl: dmmv code cleanup" (ggml-org/llama.cpp#31325, commit 5e18eb02, merged 2026-11-02, after our pin ddf48f8): the q8_0 reorder dmmv folded into the generic template, the non-K launchers templated, +41/−411, "a no-op for performance" | the operator, 2026-11-07: "Add the patch 1 as well", in the same engine as the B70 AOT build and the pinned Intel stack, so every space is gated once | **upstream**; dropped from the series 2026-11-21: LocalAI v5.0.1's llama.cpp `78e0529` carries 5e18eb02; was [`deploy/localai/patches/0007-llama-cpp-sycl-dmmv-cleanup.patch`](../../deploy/localai/patches/0007-llama-cpp-sycl-dmmv-cleanup.patch); gated with the a+b engine (quantized dmmv bytes can move: the chat models' decode) |
| R1 | request | a response header naming the backend instance (pid and start time) that answered | optional: the model pool checks before and after instead | not filed |
| R2 | request | a batched `Embedding` call: one backend call for N texts, where `/v1/embeddings` now makes N in sequence | only if the node measures a loss (model-pool-over-localai.md@c572c3b §8.2) | not filed |
| R3 | request | read API keys from a file, not only from `LOCALAI_API_KEY` | only if LocalAI's own UI is published (invariant 8) | not filed |
| R4 | request | admission by declared memory, priority eviction, refusal instead of overcommit (opt-in), eviction aware of other processes' memory, restore after quiet | availability; what the model pool gives up at the cut-over | not filed; #5352 is the nearest open issue |
| R5 | request | a strict mode: LocalAI fills in no load option the model's YAML leaves unset — no GGUF guess, no hardware tuning, no app-wide setting — or says, per option, where each value came from | settings changed in LocalAI's UI would otherwise give runs nobody asked for; we pin instead (model-pool-over-localai.md@c572c3b §10) | not filed |
| R6 | request, a patch to come | the readiness poll after a backend is spawned: LocalAI checks `Health` at once, before the process has bound its port, then sleeps `grpc.attempts_sleep_time` (default 2, whole seconds) before it looks again — about 2 s of every cold load of every spawned backend, with no work in them | 2.01–2.07 s between the backend's "Server listening" and LocalAI's load call, the same for qwen3-embedding-4b and MADLAD (München, 2026-11-24); every load after an eviction pays it | not filed; the patch is built with the parking work (operator, 2026-11-24, relayed by München: "Patch localAI when clockwork is implemented") |

A global compile-cache directory was considered and is **not to be filed**
(operator, 2026-10-02): the per-model caches already exist (§ at the end).

**Before filing a patch.** LocalAI's policy for AI-assisted contributions
(`CONTRIBUTING.md`, `.agents/ai-coding-assistants.md`) forbids an AI agent to
add `Signed-off-by` or a `Co-Authored-By` naming itself. Our patches carry
neither. Whoever submits one applies it with `git am`, sets themselves as
author, adds their own `Signed-off-by` (the DCO is a human's), and completes
the `Assisted-by:` trailer in the form that policy gives.

---

## Defects

All line references are to master at
[`53cd6c5`](https://github.com/mudler/LocalAI/tree/53cd6c5716ab5fa608a242dee57562456860094a)
(2026-10-01). Each entry says how sure it is: **certain** (the code cannot
do otherwise), **code reading** (a path the code allows; not reproduced), or
**reported live** (seen on a running system by someone). Nothing here was
run against a LocalAI install.

**Already reported?** LocalAI's issues and pull requests were searched for
each one on 2026-10-02: none is. The nearest: #12331 / PR #12333 (the idle
watchdog reaping a stale address, fixed) is near B3 but not it; #5352
(open) asks for LRU/ARC eviction and manual reclaim, not B4's defect.

---

### B1. OpenVINO: the device test is always true

[`backend/python/transformers/backend.py#L163-L167`](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/backend/python/transformers/backend.py#L163-L167),
and the same block at
[`#L186-L190`](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/backend/python/transformers/backend.py#L186-L190):

```python
if "CPU" or "NPU" in device_map:          # ("CPU") or ("NPU" in device_map): always true
    if "-CPU" or "-NPU" not in device_map:  # likewise always true
        ovconfig={"PERFORMANCE_HINT": "CUMULATIVE_THROUGHPUT"}
else:
    ovconfig={"PERFORMANCE_HINT": "CUMULATIVE_THROUGHPUT","GPU_DISABLE_WINOGRAD_CONVOLUTION": "YES"}
```

A non-empty string is truthy, so `"CPU" or …` is `"CPU"`. The `else` branch
is dead: `GPU_DISABLE_WINOGRAD_CONVOLUTION` is never set, for `AUTO:GPU` or
for a `main_gpu` naming a GPU, in both `OVModelForCausalLM` and
`OVModelForFeatureExtraction`.

**Careful with the obvious fix.** Written as intended
(`"CPU" in device_map or "NPU" in device_map`, then `"-CPU" not in … and
"-NPU" not in …`), a device such as `AUTO:-CPU` passes the outer test and
fails the inner one, and `ovconfig` is then never assigned: an
`UnboundLocalError` at `from_pretrained`. The inner `if` needs an `else`.
Which configuration each case should get is the maintainers' call.

### B2. OpenVINO: embedding models are converted again at every load

[`backend.py#L195`](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/backend/python/transformers/backend.py#L195):
`OVModelForFeatureExtraction.from_pretrained(..., export=True, ...)`.

- Every load converts the model from its PyTorch weights to OpenVINO IR
  again, in a temporary directory: a slower start each time.
- optimum-intel sets a default OpenVINO `CACHE_DIR` only when the model is
  NOT in a temporary directory and the device is a GPU (optimum-intel 2.2.0,
  `optimum/intel/openvino/modeling_base.py` L443–450). An exported model is
  always in one, so OpenVINO's model cache never applies either: every load
  compiles the model for the GPU again (the GPU driver's own kernel cache,
  where it works, softens only part of that). `requirements-intel.txt` pins
  no version of `optimum[openvino]`.

Suggested: export once into the models directory and load the IR from
there, or accept an IR as it is.

### B3. Watchdog: a backend whose eviction fails keeps running, untracked

[`pkg/model/watchdog.go#L580`](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/pkg/model/watchdog.go#L565-L583)
(LRU and group eviction, `collectEvictionsLocked`),
[`#L742`](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/pkg/model/watchdog.go#L722-L745)
(idle, `checkIdle`), and L767 (busy, `checkBusy`): the model is `untrack`ed
under the watchdog's lock BEFORE `shutdownEvicted` stops it, and nothing
re-tracks it when the stop fails.

The stop can fail. By default it is graceful
([`process.go#L20`](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/pkg/model/process.go#L20):
`LOCALAI_FORCE_BACKEND_SHUTDOWN` unset), waits up to 30 s for calls in
flight (`gracefulShutdownTimeout`, L32), and returns `ErrModelBusy` after
that
([`loader.go#L619-L641`](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/pkg/model/loader.go#L619-L641)).
A model chosen as idle can receive a request between its selection and its
stop: a chat generation longer than 30 s is enough.

Then the backend process is still loaded and still answering, but:
- it is no longer in `addressModelMap`, so `max_active_backends` does not
  count it (`EnforceLRULimit`, L493, counts `len(wd.addressModelMap)`), and
  the next load may push the node over its limit;
- idle, LRU and memory eviction never select it again;
- its next requests set `busyTime` for an address `checkBusy` cannot
  resolve, which logs "Address unresolvable" and untracks it again.

The memory reclaimer does it the safe way:
[`evictLRUModel`, L896–L900](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/pkg/model/watchdog.go#L890-L902)
untracks only after a successful stop. Suggested: the same everywhere, or
re-track on failure.

### B4. Memory reclaimer: another process's memory makes it evict every model

[`watchdog.go` `checkMemory`, L776–L810](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/pkg/model/watchdog.go#L776-L810),
and `evictLRUModel` (L814):

```go
aggregate := xsysinfo.GetResourceAggregateInfo()
if aggregate.UsagePercent > thresholdPercent {
    wd.evictLRUModel()      // one model, every watchdog tick (500 ms by default)
}
```

`UsagePercent` is the whole device's, every process's. When something
outside LocalAI holds enough of the GPU to stay above the threshold alone —
another container, a desktop session, a second inference server — evicting
LocalAI's models never brings usage under it, and the reclaimer gives up
one more non-pinned, non-busy model every tick until none is left. They
reload on their next request, and the cycle repeats.

A second, smaller effect: nothing waits for the driver to report the memory
just freed (Ollama's scheduler waits up to 5 s for it, `waitForVRAMRecovery`
in `server/sched.go`). Where the reading lags the process's exit, the next
tick can evict a second model for the same excess.

LocalAI already computes what its own backends hold
(`xsysinfo.ProcessVRAM`, `pkg/xsysinfo/process_vram_linux.go`). Suggested:
evict only when LocalAI's own share can bring usage under the threshold,
and wait for the reading to settle after an eviction.

### B5. `LOAD_TO_MEMORY` is silently ignored with `max_active_backends = 1`

[`core/application/startup.go#L558`](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/core/application/startup.go#L558):
`if options.LoadToMemory != nil && !options.SingleBackend {`, and
[`application_config.go#L514-L522`](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/core/config/application_config.go#L514-L522):
`SetMaxActiveBackends(1)` also sets `SingleBackend`.

So on a single-GPU node configured as the documentation recommends
(`--max-active-backends=1`, which replaced the deprecated
`--single-active-backend`), `LOAD_TO_MEMORY=mymodel` preloads nothing and
logs nothing: the first request pays the load. Suggested: load the first
model of the list (or the last), or log that the list was skipped and why.

### B6. Distributed: eviction does not check that it frees enough

[`core/services/nodes/router.go` `scheduleNewModel`, L1067](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/core/services/nodes/router.go#L1067):
- **L1177–L1184:** when no node has room, `evictLRUAndFreeNodeFrom` (L2121)
  evicts ONE idle model — the least recently used anywhere in the candidate
  set (L2164) — and the new model is then placed on THAT node, whether or
  not its VRAM now covers the estimate. The model given up may be a small
  one on a node that still cannot hold the new model, while another node
  would have.
- **L1227–L1236:** if the VRAM reservation then fails, the load "proceeds
  without reservation".
- **L1134:** with an estimate of 0 — any model the estimator cannot parse,
  diffusers checkpoints for instance — the VRAM filter is skipped and the
  node is chosen by idleness or load alone.

**Seen on a live cluster** by the author of the fork `matt-edmondson/LocalAI`
(branch `custom`, `docs/superpowers/specs/2026-06-03-per-gpu-vram-scheduling-design.md`):
two 12 GB GPUs, one full, one empty; the estimate 0 for SDXL kept new
diffusers backends on the full GPU, each failed its readiness probe, was
killed and retried. That fork fixes it (per-GPU placement, a heuristic
estimate when parsing fails, bounded retries); its commits are in none of
upstream's pull requests.

Suggested: evict until the estimate fits on the node being freed (or
refuse), and never treat an unknown size as zero.

### B7. Loader: the load goes ahead over `max_active_backends`

[`pkg/model/initializers.go` `retryEnforce`, L366–L388](https://github.com/mudler/LocalAI/blob/53cd6c5716ab5fa608a242dee57562456860094a/pkg/model/initializers.go#L366-L388):
when the models that would have to be evicted are busy, the loader retries
`LRU_EVICTION_MAX_RETRIES` times (30 × 1 s by default), logs "enforcement
incomplete after max retries", and loads anyway. The limit is then not a
limit: on a GPU sized for N models, the N+1st loads beside the N busy ones,
and the result is an out-of-memory in the backend rather than a refusal the
client can retry. For concurrency groups the code says this is intended
(`watchdog.go`, `EnforceGroupExclusivity`); for the LRU limit nothing says
so. Worth raising as a question: a 503 with `Retry-After` — which LocalAI
already returns for its load cooldown (`ModelLoadCooldownError`) — would
keep the limit.

---

### B8. `/models/reload` never forgets a removed model

*Seen on a real LocalAI at 58830f7 with P1 (the model pool's smoke test,
model-pool-over-localai.md@c572c3b §11), and certain from the code.*

[`ReloadModelsEndpoint`](https://github.com/mudler/LocalAI/blob/58830f7ac508845a6f4efa32cfca06af422d4d82/core/http/endpoints/localai/edit_model.go#L123-L140)
calls `LoadModelConfigsFromPath`, whose non-strict path only sets
`bcl.configs[c.Name]` for each YAML it finds
([`model_config_loader.go` L849–L930](https://github.com/mudler/LocalAI/blob/58830f7ac508845a6f4efa32cfca06af422d4d82/core/config/model_config_loader.go#L849-L930)).
A model whose file was deleted stays in `GET /v1/models` and stays loadable
until LocalAI restarts. Nothing says it is a model without a file.

Suggested: on reload, drop a config whose source file lies in the models
path and no longer exists. `LoadModelConfigsFromPathStrict` already builds
an authoritative snapshot; the reload endpoint could use those semantics.
`/models/delete/:name` is not the answer: it is the gallery's, and it
deletes the model's files too.

---

### B9. A NaN in one vector fails the whole `/v1/embeddings` request

*Seen on the node at 58830f7 with P1, the SYCL f16 llama.cpp backend of the
same commit, bge-m3 on an Arc Pro B70 (migration step 15c, 2026-10-04).*

Two of three passes of a 512-text calibration set, sent in batches, ended
in HTTP 500 `json: error calling MarshalJSON for type schema.Item: json:
unsupported value: NaN`. The same texts asked again, alone or in the same
batches, came back finite: the NaN is intermittent and comes from the
backend. Go's `encoding/json` cannot encode a NaN or an Inf in a `float32`
slice, so the response is lost whole: every finite vector in the request,
and which input was bad.

llama-server, on the same card and weights, sends such a vector as nulls,
which a client can recognise and retry. Suggested: check each embedding
for non-finite values before encoding, and either answer an error naming
the input's index, or retry that input once in the backend. Either way the
client learns which text failed rather than losing the batch.

### B10. A wedged backend is "deleted to recreate it" and keeps running

*Seen on the node with stock v4.11.0-gpu-intel, 2026-10-10/11: a Python
backend (louise-medcpt-article-ov) whose four gRPC worker threads were
deadlocked inside OpenVINO.*

The health check failed every 5 minutes for 7 hours, and each time the log
said `GRPC Model not responding … Deleting the process in order to recreate
it`. The process was never ended: the same pid ran on, `/system` kept
listing the model as loaded, `/backend/shutdown` hung past 30 s, and every
`/v1/embeddings` waited until the client's timeout. Ending it by hand
(SIGTERM, then SIGKILL) and loading again worked.

Why, from the source (`pkg/model/loader.go`, `checkIsLoaded`, master
2026-10-11): after the warning, a LOCAL process is deleted only
`if !process.IsAlive()`. A live, wedged backend is logged as "deleting" and
left alone, every time. Suggested: when the health check fails and the
process is alive, force-delete it after N consecutive failures (the busy
watchdog's `ShutdownModelForce` already does the killing), or at least do not
log "Deleting" when nothing is deleted.

**What already covers it:** the busy watchdog (`watchdog_busy_enabled`,
`watchdog_busy_timeout`, default 5m; ON on the node since 2026-10-12 at 10m — its first second killed a pubmed encoder hung for 8 h, and the next call loaded it again). It times each
backend from its oldest in-flight request, ignores pinning, and kills with
`ShutdownModelForce` (go-processmanager: SIGTERM, then SIGKILL after the
graceful timeout). STATUS.md "Open after the migration" weighs turning it
on.

---

### B11. Memory reclaimer: a model being loaded is evicted first, as the "least recently used"

*Reported live, and certain from the code. Seen on the node with stock
`quay.io/go-skynet/local-ai:v4.11.0-gpu-intel`, 2026-10-14 (Intel Arc Pro
B70, 34 GB).*

**What happened.** Every chat request failed with HTTP 500. The GPU stood
at 79.2 % against the reclaimer's 68 % threshold. Each time the chat model
was loaded for a waiting request, the reclaimer chose it as the
least-recently-used model and stopped it about 5 seconds into its load;
the request got 500 and the next one started the cycle again. Six cycles
in 40 seconds, while idle models stayed loaded. The log, ANSI colours
stripped:

```
09:06:28.788 WARN [WatchDog] Memory usage exceeds threshold, evicting LRU backend type="GPU" usage_percent=79.2167126944692 threshold_percent=68
09:06:28.789 INFO [WatchDog] Memory reclaimer evicting LRU model model="louise-chat-qwen3-8b" lastUsed=0001-01-01T00:00:00Z
09:06:37.239 INFO Backend process stopped id="louise-chat-qwen3-8b" address="127.0.0.1:45373" process="run.sh" exitCode="-1"
09:06:37.340 INFO BackendLoader starting modelID="louise-chat-qwen3-8b" backend="intel-sycl-f16-llama-cpp" model="weights/chat/qwen3-8b.gguf"
09:06:37.820 INFO [WatchDog] Memory reclaimer evicting LRU model model="louise-chat-qwen3-8b" lastUsed=0001-01-01T00:00:00Z
09:06:42.574 INFO Backend process stopped id="louise-chat-qwen3-8b" address="127.0.0.1:39129" process="run.sh" exitCode="-1"
09:06:42.649 INFO HTTP request method="POST" path="/v1/chat/completions" status=500
09:06:44.677 INFO BackendLoader starting modelID="louise-chat-qwen3-8b" …
09:06:44.689 INFO [WatchDog] Memory reclaimer evicting LRU model model="louise-chat-qwen3-8b" lastUsed=0001-01-01T00:00:00Z
09:06:49.827 INFO Backend process stopped id="louise-chat-qwen3-8b" address="127.0.0.1:42149" … exitCode="-1"
… the same at 09:06:50, 09:06:56, 09:07:01; six "evicting LRU model" lines
for the chat model, every one with lastUsed=0001-01-01T00:00:00Z
09:07:06.887 ERROR Stream ended with error error=rpc error: code = Unavailable desc = connection error: … connect: connection refused
09:07:06.887 INFO HTTP request method="POST" path="/v1/chat/completions" status=500
```

What `GET /system` listed as loaded meanwhile (`size_vram`):

| model | VRAM | state |
|---|---|---|
| vega-dar-legal-it | 13.6 GB | busy (an embedding stream) |
| vega-qwen3-embedding-4b | 9.9 GB | idle: loaded at 00:11, no request since |
| vega-bge-m3 | 1.1 GB | idle |
| vega-medcpt-query, vega-medcpt-article-ov, vega-granite | 1.1 GB together | idle |

The idle 4B embedder, last used at 00:11, was never chosen; the chat model,
loading for a request that was waiting, was chosen six times. Stopping the
idle 4B model by hand (`POST /backend/shutdown`) let the chat model load,
and the next request answered 200.

**Why, from the source** (`pkg/model/watchdog.go` at `v4.11.0`):
`evictLRUModel` sorts the candidates oldest-first by `lastUsed`
(`a.lastUsed.Compare(b.lastUsed)`). `lastUsed` is written only when a
request begins or ends (`beginRequestLocked`, `finishRequestLocked`,
`UpdateLastUsed`); nothing initialises it when a model is registered, so a
model whose load has started but whose first request has not yet reached
the backend has the zero time (`0001-01-01`) and sorts as the oldest model
on the card. The busy check (`busyTime`) does not cover it either: the
request that caused the load is not yet in flight at the backend. So under
memory pressure the reclaimer evicts exactly the model a request is
waiting for, every time, and the request can never succeed while the
pressure lasts.

**Suggested fix**, any of:
- set `lastUsed[address] = now` when the address is registered
  (`AddAddressModelMap`), so a model being loaded counts as just used;
- or skip, as busy, an address whose load has a request waiting on it;
- or, at least, never choose a model whose `lastUsed` is zero while another
  candidate has a real time.

**What covers it for us:** Louise unloads an embedder whose collection does
not embed everything (`worker._release_embedder`, 2026-10-14), so idle
encoders do not hold the card; the chat model can still lose this race
whenever the card is over the threshold for other reasons.

### B14. llama.cpp backend: `Embedding` ignores the token ids it is given

*Found 2026-10-18 from the source at `v4.11.0` (58830f7), after the AMD peer's
LocalAI (rocm-llama-cpp) refused 3 of the 512 calibration texts at a window
of 1,024 tokens (`tools/vega_cut_scan.py`).*

`/v1/embeddings` takes token ids as well as text. `core/backend/embeddings.go`
(`ModelEmbedding`, L131–L144) puts them in `PredictOptions.EmbeddingTokens`
and leaves `Embeddings`, the text, empty. The llama.cpp backend's `Embedding`
(`backend/cpp/llama-cpp/grpc-server.cpp` L2880 on) reads only the text
(`parse_options`, L371: `data["embeddings"] = predict->embeddings()`);
`EmbeddingTokens` appears nowhere in the file. So the empty text is
tokenized and refused: "Input content cannot be empty". llama.cpp itself
embeds token ids: the `tokenize_input_prompts` the backend calls takes an
array of numbers as the tokens themselves.

**Why it matters to us.** A space that cuts by tokens (09 §9.3, `ctx`;
char-cut-truncation.md) embeds an input's first `ctx` tokens — exactly what
its calibration reference embeds. With text only, VeGa's wrapper has to find
a text with exactly those tokens: a prefix of the input, or the pieces read
back (`Detokenize`). Some inputs have none — a word segmented otherwise once
cut, a cut inside a character spelled as byte tokens, a read-back that
tokenizes differently: 3 of 512 at 1,024 on llama.cpp's tokenizer (i = 339,
Greek decoded as Latin-1; 352, Danish; 372, Latin), all 622 with HF's.

**Fix: P2.** Embed the ids, with the special tokens a text gets, so that
embedding a text's tokens is embedding the text.

### B15. `v4.12.1`: backend downloads are staged at `//downloading`

*Seen on the node 2026-10-27, the first hour on v4.12.1.*

v4.12.1 stages every backend download in `DownloadStagingPath`
(`core/cli/run.go` L54, default `${basepath}/downloading`). In the
container `basepath` is `/`, and LocalAI runs as `apps`, which cannot
write there. So every backend install and auto-upgrade failed:
"failed to create staging directory: mkdir //downloading: permission
denied" (`pkg/oci/image.go` L340). The node's LocalAI keeps
`auto_upgrade_backends: true`, because the operator wants backend updates.

**Set on the node:** `LOCALAI_DOWNLOAD_STAGING_PATH=/backends/.downloading`
in the app's `additional_envs`. It sits on the backends' own filesystem,
so the final rename stays on one disk, and LocalAI does not list a
dot-named entry as a backend. **Caution:** `app.update` replaces the whole
`localai` values block it is given. Sending only `additional_envs`
silently reset `image_selector` to the CPU image for 38 s (12:42:42–43:20).
Nothing was written meanwhile: gutenberg deferred, because its answers did
not come from a validated run. Always send `image_selector: intel_image`
with the envs.

---


### B17. `v5.0.1`: a voice profile is refused by the backend's name, whatever the model says

*Seen on the node 2026-11-24, the OmniVoice port's first rows on the B70
(München): every request with `voice: localai://voice-profiles/<id>` to the
test model answered 400 in 1–5 ms, before the backend; with
`tts.voice_cloning: true` in the model file the same.*

`core/http/endpoints/localai/tts.go` asks `config.VoiceCloningForModel(cfg)`
before it leases a profile's clip. That function
(`core/config/backend_capabilities.go`) first resolves `cfg.Backend` in
`BackendCapabilities` — the exact name, else with a gallery hardware prefix
(`intel-sycl-f16-`, `vulkan-`, …) and a release-channel suffix
(`-development`, `-quantization`) stripped — and returns nil when nothing
is found. Only then does it read the model's own `tts.voice_cloning`, whose
comment says "operators with custom filenames can opt in or out
explicitly". A backend an operator builds and installs under a name of its
own (`louise-sycl-f16-llama-cpp-<series>-stack-<stack>`: the digest IS the
name, invariant 12) resolves to nothing, so the explicit opt-in is never
reached. The live speech model passes only because
`intel-sycl-f16-omnivoice-cpp` strips to `omnivoice-cpp`.

**Around it, for a measurement:** `voice` as the clip's path with
`params.ref_text` — a `voice` that is no `localai://` reference goes to the
backend untouched (refused to a non-admin viewer only), and the llama.cpp
backend with P12 takes an existing file as the reference.

**The fix:** P13 (staged) below — an explicit `tts.voice_cloning: true`
honoured for a backend the table does not know, the name lookup kept for
everything else. The node's server is built from our series (P1 is live
there: `/local-ai --version` reads `v5.0.1-1-g…`), so it can carry it.

## Patches we carry

Our llama.cpp backend images build LocalAI's backend from the base commit in
[`deploy/localai/patches/series`](../../deploy/localai/patches/series) with
these patches applied in order (the model pool image built LocalAI itself that way
until 2026-10-09). A patch leaves the series when upstream merges it, or
when a merged change makes it unnecessary. `tests/test_localai_patches.py`
holds the series, the files and this section to each other.

**Base since 2026-11-02: LocalAI master
[`16acc89`](https://github.com/mudler/LocalAI/tree/16acc8926bb8a27b67dc4d419dbceaeb4110f7af)**
(`LLAMA_VERSION` `ddf48f8`, which carries the Tekken pre-tokenizer fixes
Nemotron-3-Embed needs and the SYCL host-wait removal). Operator, 2026-11-02,
choice A: it becomes the ONE llama.cpp engine of every model on the node
(docs/notes/nemotron-embed.md, "The candidate engine"). P1 and P5 apply
unchanged (offsets only). P2 was rebased by München for the candidate build
(two hunks re-placed, its added and removed lines identical). P3 and P4 were
rebased onto `ddf48f8`'s `server-context.cpp` (P3 re-anchored; two hunks of P4
re-placed by hand around upstream's new audio-stream and realtime conditions),
their code unchanged, and pass the P3/P4 CPU checks again at `ddf48f8` (each
entry below). The series as committed applies with no fuzz and no rejected
hunk at every level. P6 and P9 (staged) are NOT rebased yet.

**Base since 2026-11-21: LocalAI v5.0.1
[`dd5780a`](https://github.com/mudler/LocalAI/tree/dd5780aa904d132c784867d37fc713df8d984c8a)**
(`LLAMA_VERSION` `78e0529`, llama.cpp of 2026-11-18). Operator, 2026-11-21,
choice A: the series moves to the latest backend
(docs/notes/llama-cpp-update-2026-11-21.md says what it gains). P1 and P2
apply unchanged. P3, P4, P5 and P6 were regenerated on llama.cpp `78e0529`
with LocalAI's own `0001-add-server-task-type-score.patch` applied first, as
its `prepare.sh` does (`ls patches`, `patch -p1`): their added and removed
lines are identical, only context lines and hunk offsets moved (P4: two
hunks had taken fuzz around upstream's per-task LoRA handling; P6: two in
`tests/test-chat.cpp`). Applied that way on a fresh `78e0529` every hunk is
exact, and `git am` of the six on a fresh v5.0.1 succeeds. The former P7 is
in the base and retired (below). **2026-11-22: P10 and P11 enter the series as P7 and
P8** — the backend qualification found both LIVE on the node (281e1ee481b6 carried the
context on demand, 10aa8b1851d7 the nemotron reranker) while this file called them staged,
so the first v5.0.1 engine lacked them and the gate refused it (finding F4,
backend-qualification-2026-11-21.md). The series' own checks — P2's
`embedding_tokens_test`, P3's 379/379 MADLAD decode and P4's in-wave
equality, P5's `test-backend-ops` case, P6's chat tests — are run on the
node before the engine serves anything (every space re-gated, invariant
12). Every encoder, reranker and MADLAD YAML now carries `fit:off`: LocalAI's
backend defaults `fit_params` to true at both bases, and a model's placement
must not depend on what else was on the card when it loaded.

**The series digest — what an engine is named by** (`build.sh`: the series
file's bytes, then each patch's, in order; first twelve hex of the sha256).
`NAME_ONLY=1 sh deploy/localai/llama-cpp/build.sh <variant> <out>` prints it
for a tree and builds nothing (`SERIES_FILE=` for a staged series) — the way
to answer "which patches was the engine on the node built from".
- *A hole, closed 2026-11-12* (#586, `tests/test_localai_build_name.py`).
  With `SERIES_FILE` given as a RELATIVE path (as the staged series' build
  steps write it) the file was not found after the script's `cd` into the
  patches, the pipe hid `cat`'s error, and the name covered the patches
  ALONE: the `base` line — LocalAI's commit — was not in it. The live series
  was never affected (its default path is absolute).
- *Two engines were named over their patches alone, and KEEP their names
  until they are rebuilt*: `c4e6133a6e85` — `staged/context-on-demand.series`
  at `6f1748a` (the live on-demand engine of the three VeGa encoders, stack
  `7c301a7ba437`); a build of the same series is now named `281e1ee481b6`.
  `9d17b8a39845` — `staged/nemotron-rerank.series` (the Nemotron reranker's
  backend); now `10aa8b1851d7`. To check such a name: `cat` the series'
  patches, in order, into `sha256sum`.
- *The rename, with no rebuild* (operator, 2026-11-12, on leaving the two
  names until a rebuild: "Then let's fix it"). The bytes are the ones
  already built, so nothing is compiled again (RULE 1); what is by the book
  is the NAME. On the node, by München, on the operator's word there:
  1. per engine, the image it was built as gets the by-the-book tag
     (`docker tag louise-llama-cpp:sycl_f16-<old>… louise-llama-cpp:sycl_f16-<new>…`),
     is saved under the by-the-book tar name (`docker save`), and its
     `.stack.lock` is copied beside it; `<new>` is `NAME_ONLY=1`'s word for
     the series — `281e1ee481b6`, `10aa8b1851d7` — and each name keeps the
     suffix it has today;
  2. it is installed as LocalAI installs any (`install-external`,
     `ocifile://`), under the new backend name, beside the old one;
  3. THE PROOF, before anything points at it: the engine's tree digest of
     the new directory (09 §9.3; the install record is outside it) is the old
     directory's — for the on-demand engine `sha256:b801b4b0…`. Only the
     engine's `name` moves. A different digest stops the rename;
  4. `vega-louise-llama-cpp`'s `vega.json`, MADLAD's YAML and the reranker's
     (`deploy/vega/stage/models/louise-rerank-nemotron-1b.yaml`) name the new
     backends; LocalAI is restarted once. THE OLD DIRECTORIES STAY until the
     new names answer and their gates have passed — they are the way back
     (the coordinator, 2026-11-12); then `deploy/vega/retired_backends.py
     --delete` on the operator's own yes, with its size, in München's
     session (his practice of 2026-11-07);
  5. `name` is inside the engine object, so `engine_id` and every VeGa run on
     the on-demand engine are NEW: each is gated once, on its twin, by the
     code as it is. No statement is carried — a carried one would not be the
     one a gate writes, and the gate no longer holds the model from the
     others. Writes of those spaces defer until their gate passes. One gate
     per space covers its collections, which borrow its statement (legi is
     in normattiva-nemo's space and needs none of her own); gates on one
     model wait for each other; the worker gates each new run by itself,
     the busiest space first where an order is wanted. The reranker and
     MADLAD answer under no VeGa run: nothing to gate.
  Cost: one LocalAI restart and one gate per (space, role) of three encoders.
  WHEN (München, 2026-11-12): after legi's case F has swapped — her F recipe
  is pinned to the live engine's id, which the rename changes, and a rename
  before it ends would have F refused again (safely: the chain stops and
  forgets nothing) and lose the night. The operator, in München's session:
  after legi F's swap. Steps 1–3 are prepared ahead, nothing repointed.
  The reranker's staged YAML is held as a draft (PR #589) and merged with
  the node step: merged before, the next unrelated deploy would point the
  reranker at a backend that does not exist yet.
- *What the digest settled the same day.* The live engine `c4e6133a6e85` was
  read as predating P11's `init_stop` fix. It does not: `6f1748a` is the
  merge after `8f404c9`, its staged patches are byte-identical to the fix's,
  and the patches-only digest is `618e5a2b7e24` at `5fd224d` (the defective
  build, never used) and `c4e6133a6e85` at `6f1748a`. No rebuild was needed.

### P1. `GET /system` lists the options each loaded model was given

*Base: master `16acc89` since 2026-11-02 (unchanged bytes, offsets only); `v4.12.1` [`ec92740`](https://github.com/mudler/LocalAI/tree/ec9274027eadd552d56f2c6c1fe86cd9216a991a) from 2026-10-27; written on master
[`58830f7`](https://github.com/mudler/LocalAI/tree/58830f7ac508845a6f4efa32cfca06af422d4d82)
(2026-10-02). File: `0001-system-list-load-options.patch`. 9 files, 201
lines added, none removed.*

**Why.** LocalAI completes a model's config before loading it. It guesses
`ContextSize` and `NGPULayers` from the GGUF (`core/config/gguf.go`
L57–L95), applies the hardware auto-tuning, and fills in the application's
runtime settings (`LoadOptionContextSize(appConfig.ContextSize)`,
`model_config_loader.go` L252). What the backend received therefore
appears in no file and on no endpoint, only in a DEBUG log line
(`initializers.go` L194). The model pool must state, with every answer, the options
the answering run was loaded with (09 §9.3 `options`, §9.12). Without them
it states no run, and Louise writes nothing. The distributed registry
already hashes these options per replica (`EffectiveModelOptionsHash`,
`core/services/nodes/router.go` L410); on a single node, neither the
options nor the hash are kept.

**What it changes.**
- `pkg/model/model.go`: `Model.SetLoadOptions` and `Model.LoadOptions`,
  each with its own copy, under the model's lock.
- `pkg/model/initializers.go`: one line. After a successful `LoadModel`,
  the options sent are recorded on the model.
- `core/schema/localai.go`: `SysInfoModel` gains `load_options` (protojson
  with proto field names; unset fields omitted) and `load_options_hash`.
  Both are omitted when this host did not issue the load.
- `core/http/endpoints/localai/system.go`: fills both fields.
- `swagger/`: the two properties, added by hand. Regenerating with
  `swag` v1.16.6, the version `go.mod` pins, also rewrites unrelated
  definitions on master, which is worth telling upstream separately.

**Tests**, Ginkgo, beside the existing ones:
- `pkg/model/load_options_test.go`: a model loaded through an in-process
  gRPC backend records exactly what `LoadModel` received, including the
  per-load fields set just before the call. This spec fails with the
  one-line hook removed. Neither the caller's copy nor the returned copy
  can change what was recorded.
- `core/http/endpoints/localai/system_info_test.go`: `/system` lists the
  options under their proto names, and a hash equal to
  `EffectiveModelOptionsHash`.

**Run** with Go 1.26.0 after `make protogen-go`:
- `go test ./pkg/model/ ./core/schema/ ./core/config/` passes.
- In `./core/http/endpoints/localai/`, the new spec passes. 42 other specs
  fail identically with and without the patch: they need NATS, a database
  or the network.

**What it exposes.** `/system` is an admin route. The options carry no
key: the proxy options name a key's variable or file, not the key. A
model's `env:` is copied into them (`core/backend/options.go` L580),
which the DEBUG log already shows.

### P2. llama.cpp backend: `Embedding` embeds the token ids it is given

*Base: master `16acc89` since 2026-11-02 (rebased by München: two hunks
re-placed, the same added and removed lines); `v4.12.1`
[`ec92740`](https://github.com/mudler/LocalAI/tree/ec9274027eadd552d56f2c6c1fe86cd9216a991a) from 2026-10-27 (first `v4.11.0` = `58830f7`),
after P1 (they touch different files). File:
`0002-llama-cpp-embed-token-ids.patch`. 5 files, 99 lines added, none
removed.*

**Why.** B14: the backend drops `EmbeddingTokens`, so a client cannot
embed tokens, and cannot cut an input to exactly its first N tokens.

**What it changes** (`backend/cpp/llama-cpp/`):
- `grpc-server.cpp`, `Embedding`: when `EmbeddingTokens` is set, the prompt
  is those ids with the special tokens a text gets, as an array of numbers,
  which `tokenize_input_prompts` takes as the tokens themselves. A request
  with text only takes the same path as before.
- `embedding_tokens.h`: where the special tokens go. The ids are an input's
  tokens as `TokenizeString` returns them, without special tokens; a probe
  text ("a") is tokenized with and without them, and the ids take the place
  of its own tokens. That covers BOS/EOS, CLS/SEP, BOS only, EOS only and
  none, without naming any vocabulary type.
- `embedding_tokens_test.cpp`: those five cases, and the refusal when the
  probe's tokens are not found; `prepare.sh` copies both files, as it does
  the other helpers; `CMakeLists.txt` registers the test under
  `LLAMA_GRPC_BUILD_TESTS`. `backend/cpp/run-unit-tests.sh` finds it by its
  name.

**Run:** the unit test, `g++ -std=c++17 -Wall -Wextra -Werror` (gcc 14),
passes. **Measured** (2026-10-18, the dev PC): the CPU variant built by
`deploy/localai/llama-cpp/build.sh`'s recipe, bge-m3 (the node's GGUF,
sha256 `daec91ff…`), over gRPC, on the 512 calibration texts:
- a text's token ids give the text's vector bit for bit, from as many
  tokens: the probe, and 64 of 64 whole texts;
- at a window of 1,024: 189 of 189 over-long texts embedded from exactly
  1,024 tokens (1,022 + the 2 specials), 339, 352 and 372 among them;
- the same ids twice, the same bytes: 8 of 8.

**On the AMD peer's RX 9060 XT** (2026-10-18, its session): `build.sh hipblas`
with `AMDGPU_TARGETS=gfx1200` at 5193953 (series digest `724329e027f2`, as
the SYCL build; 3.0 GB), installed as `louise-rocm-llama-cpp`, vega-llama-cpp
rebuilt in front of it. `tools/vega_cut_scan` vega-bge-m3 at 1,024: 0 of 512
refused (3 before P2). `tools/vega_repro_check`, 16 texts x 2 fresh loads x
2 shuffled passes, bit-identical: vega-bge-m3 (`--window 1024`) 16/16,
its CPU twin 16/16, vega-qwen3-embedding-4b 16/16.

**LIVE on the node, 2026-10-18 21:19** (`deploy/vega/switch/rollout-p2.sh`,
the SYCL build of series digest `724329e027f2`, installed as
`louise-sycl-f16-llama-cpp`): the five SYCL vega- models 16/16 bit-identical
over 2 fresh loads x 2 shuffled passes at a window of 511 (bge-m3 at 1,024
too); the cut scan 0 of 512 refused at 1,024; the gate passed on every new
run — gutenberg worst 0.999999, normattiva 1.000000, pubmed document 0.999999
and query 1.000000, wikipedia 0.9997 (whole set 0.9990) — and nothing was
re-embedded. The first run stopped at step 7: a 512-token model sent a
2,465-token text whole; every model is now measured with a window.

**Rebased on `v4.12.1`** ([`ec92740`](https://github.com/mudler/LocalAI/tree/ec9274027eadd552d56f2c6c1fe86cd9216a991a),
2026-10-27, after the app moved to v4.12.1; operator: "a backend update is
advisable, just re-apply the known patches if still needed"). B14 is still
there: v4.12.1's `grpc-server.cpp` never reads `EmbeddingTokens`. P2's only
conflict was its `prepare.sh` hunk (upstream added `slot_cache.h` above it);
the code hunks applied unchanged. llama.cpp moves from `a868c3e` to
`c0fd786`, so each SYCL model on it is a new run: measured (invariant 12)
and gated, as on 2026-10-18. P1 still applies and stays in the series; it
patches LocalAI's server, not the backend image, and the node's server is
stock. louise-python is not rebuilt: v4.12.1's `backend.proto` only adds
(SlotCache, TTSLive, MotionStream, voice fields), its `common/` files are
byte-identical, and the VeGa wrapper forwards any method it does not
intercept as bytes.

**LIVE on the node, 2026-10-27 13:00**
(`deploy/vega/switch/rollout-p2-20261027.sh@c572c3b`, series digest
`e79a808b4808`, replacing `louise-sycl-f16-llama-cpp` in place, with the old
tree in `backends-bak-20261027`):
- bge-m3, granite, medcpt-query and qwen3-embedding-4b: each 16/16
  bit-identical over 2 fresh loads × 2 shuffled passes at a window of 511.
- bge-m3 also 16/16 at 1,024, and the cut scan refused 0 of 512 there.
- Gates passed:
  - gutenberg: 16/16 at 1.000000;
  - pubmed: document whole set worst 0.999998, query worst 1.000000;
  - wikipedia: whole set worst 0.9990.
- wikipedia-v8 was not gated, because its paused records build held the
  collection's lock.
- Nothing was re-embedded.

**Tokens against HF's** (bge-m3's `tokenizer.json`): 475 of 512 identical.
The other 37 are one thing: the text ends with a space, and HF's tokenizer
adds a trailing `▁` that llama.cpp, as SentencePiece, does not. The first
tokens agree, so no cut is affected; a whole text ending with a space is
one token apart, as it is today without the patch.

**How VeGa uses it.** At load the wrapper sends a probe's tokens as
`EmbeddingTokens` WITH NO TEXT and keeps the token path only if the vector
comes back bit-identical to the text's, from as many tokens
(`Gate._probe`). A stock backend refuses the empty text, so it can never
pass for one that embeds the ids; its inputs are cut as text, exactly or
refused, as before.

### P3. llama.cpp backend: the encoder of encoder-decoder models runs

*Base since 2026-11-21: LocalAI v5.0.1 (`dd5780a`, llama.cpp `78e0529`), context refreshed, code unchanged (above). Before:* *master `16acc89` (llama.cpp `ddf48f8`) since 2026-11-02, re-anchored,
the code unchanged; `v4.12.1` (`ec92740`) before. After P1 and P2. File:
`0003-llama-cpp-encoder-decoder.patch`. It adds
`backend/cpp/llama-cpp/patches/0003-server-encoder-decoder.patch`, a
llama.cpp patch that the backend's `prepare.sh` applies to llama.cpp
`c0fd786` beside upstream's own two. 80 lines added to
`tools/server/server-context.cpp`.*

**Why.** B16. MADLAD-400-3B-MT is a T5 model. The library runs its encoder
(`llama_model_has_encoder`, `LLAMA_PROCESS_TYPE_ENCODE`), and
`tools/completion` calls it. llama-server never does, so a GGUF of it
answered `<2cs><2cs>…` (2026-09-18), and MADLAD went on PyTorch:
17–38 s cold and 7.2 GB (translator-choice.md).

**Upstream:**
- fairydreaming's `t5-server` branch (Dec 2024) was never merged; 0x4139's
  fork (Mar 2025) is the same 31 lines.
- #26565 (Aug 2026) closed stale with no reply.
- Both forks rewrite the prompt inside the prompt loop. Today's slot holds
  its task as `const`, and the prompt cache would reuse decoder positions
  that belong to another encoder input.

**What it changes:**
- **Which models:** an encoder-decoder target (architecture `t5` with a
  decoder), for generation tasks only. `llama_model_has_encoder()` alone is
  also true of T5ENCODER (encoder-only embedding models) and of EAGLE3 and
  DFLASH (speculative draft heads): none of P3's code runs for them, or for
  any other model (found 2026-10-27, before deploying: the first version
  keyed on `llama_model_has_encoder()` alone).
- `launch_slot_with_task`: for such a model, the prompt's tokens
  become the slot's encoder input, and the task's prompt becomes the decoder
  start token (BOS where none is named). `cache_prompt` is off for that
  task. A multimodal prompt is refused.
- `update_slots`, when the slot starts its prompt:
  - its sequence is cleared first (the encoder batch's positions are
    checked against what the memory holds);
  - the encoder reads the input in one ubatch on the slot's sequence id,
    which the decoder's cross-attention mask pairs with its own;
  - an input longer than `n_ubatch` is refused, rather than asserted on.
- `load_model`: one EOS token is encoded before `common_context_can_seq_rm`,
  whose probe decodes; a T5 decoder asserts "llama_encode must be called
  first" without it.
- More than one slot is warned about, because `llama_cross` holds one
  encoder output per context. Our models run one slot.

**Checked on CPU** (llama.cpp `c0fd786` + the patch, MADLAD bf16 GGUF
converted from our own weights by llama.cpp's converter, 2026-10-27):
- Tokens: all 3,078 query prompts tokenize identically to transformers'.
- Translations: 120 of 130 are identical to the torch backend's
  (`tools/measure/out/xlate_choice/q_madlad.jsonl`), once transformers'
  `clean_up_tokenization_spaces` (true in MADLAD's config) is applied. The
  other 10 diverge greedily at one token, 8 of them in zh/ja/ko.
- Speed: 2.26 s median per translation on 8 CPU threads.
**On the card**, 2026-10-27, measured with gutenberg embedding beside it.
Test backend `louise-sycl-f16-llama-cpp-p3test` (series `5c7ec99992cf`);
test model `madlad-p3-test-f16`, an f16 GGUF converted from our weights,
one slot. The live backend was not touched.
- **Format:** bf16 and f16 give the same translations; f16 is 2–3× faster
  warm.
- **Memory and load:** 6.29 GB on the card (torch: 6.71 GB). The first
  request, load included, takes 9.5–11 s (torch: 17–38 s).
- **The 490 queries (3,078 translations):** 2,815 identical to torch's
  after the cleanup (91.5 %). The differences by target: zh 48/488,
  ja 69/490, ko 69/490, fi 26/466, de 22/421, fr 21/412, en 8/311.
- **Speed:** median 3.03 s per translation (max 10.3 s), one language at a
  time. A question's 6–7 languages take ~18 s, against torch's 3.2 s for
  the batch.
- **Stage 0 (T10, llama-cpp-encoder-decoder.md §9):** 48 output tokens
  take ~170 ms each, whatever the input length (7 → 192 tokens: ×1.10). The
  cross-attention recompute does not dominate; a fixed cost per decode step
  does, and that is what decoding several translations together amortizes.
  Hence translator-choice.md "Waves".

**Only `/completion` with a raw prompt** is right for these models: a chat
template is text they were not trained on. The client sends one request
per segment (`<2xx> segment`), where today's backend takes the whole batch
as one JSON prompt (translator-choice.md, "Code changes").

### P4. llama.cpp backend: encoder-decoder slots are encoded in waves

*Base since 2026-11-21: LocalAI v5.0.1 (`dd5780a`, llama.cpp `78e0529`), context refreshed, code unchanged (above). Before:* *master `16acc89` (llama.cpp `ddf48f8`) since 2026-11-02: two hunks
re-placed by hand in `release()` and in the loop that picks the generating
slots, around upstream's new audio-stream and realtime conditions, the code
unchanged; `v4.12.1` (`ec92740`) before. After P1–P3. File:
`0004-llama-cpp-encoder-decoder-waves.patch`. It adds
`backend/cpp/llama-cpp/patches/0004-server-encoder-decoder-waves.patch`,
applied by the backend's `prepare.sh` after P3's. 173 lines added and 40
removed in `tools/server/server-context.cpp`, every one under P3's
`enc_dec` guard (architecture `t5` with a decoder, generation tasks).*

**Why.** P3 ran one slot: llama.cpp keeps ONE encoder output per context
(`llama_context::cross`, tagged per sequence), and every encode replaces
it whole, so a second slot's encode would overwrite the first's. One slot
is 3.03 s per translation on the card and ~18 s per question (its 6–7
languages), against torch's 3.2 s for the batch. Measured first (T10,
above): a decode step costs the same whatever the input length, so the
lever is decoding several translations in one batch — what torch does.
The per-sequence encoder memory (docs/notes/llama-cpp-encoder-decoder.md
§3, ~900 lines in the library) stays the upstream shape; this is the
server-only step (translator-choice.md "Waves"), built 2026-10-27.

**What it changes:**
- `enc_dec_waves()`, run by `pre_decode` before the batch is built: the
  slots in STARTED whose prompt the encoder has not read are gathered for
  a window (`LLAMA_ENC_DEC_WAVE_MS`, default 50 ms — a client's concurrent
  requests arrive within a few ms), or until every slot holds a request or
  the waiting tokens fill one ubatch. Then the longest prefix of them, in
  CONTENT order (their tokens; slot id as the tie-break), that fits one
  ubatch is encoded in ONE `llama_process(ENCODE)` call, each on its own
  sequence id (the library tags the rows per sequence and the cross mask
  pairs them). An input longer than `n_ubatch` is refused, not asserted
  on. While a wave decodes, new slots wait in STARTED.
- The batch follows the wave's content order (`slot_order`), so the KV
  cells a wave takes depend on what is translated, never on which slot
  took which request or on arrival order: the same group gives the same
  bytes.
- `release()` clears an encoder-decoder slot's prompt (its KV cells), so
  the next wave starts from the same empty pool.
- `n > 1` (`n_cmpl`) is refused: a child copies its parent's decoder state
  and never encodes.
- Other models: no code path of theirs changes.

**Checked on CPU** (llama.cpp `c0fd786` + P3 + P4, a random-weight T5 with
the TEST tokenizer, 7 slots, ubatch 1024, 2026-10-27): a lone request is
byte-identical to P3's; the same 7 prompts in 4 arrival orders give the
same 7 outputs (T3); a request arriving while a wave decodes is answered
after it; an input over `n_ubatch` and `n > 1` are refused with 400 and
the server goes on; 60 requests after a burst leave every slot empty.
**Checked again on CPU at `ddf48f8`** (P3 + P4 rebased, 2026-11-02;
llama-server built from the tree the committed series produces): a
random-weight T5 written with gguf-py (`test-llama-archs -o` no longer
saves T5), weights N(0, 1) so the output follows the input, the TEST
tokenizer plus an EOS id (P3's load probe encodes EOS; MADLAD has one), 7
slots, ubatch 1024, `-kvu` (without it upstream's graph reserve asserts for
T5 with several sequences; LocalAI's server sets `kv_unified` by default).
A lone request equals llama-completion's greedy output, the library's own
encoder-decoder path, on 7 of 7 prompts; 4 arrival orders give the same 7
outputs; in-wave equals alone on 7 of 7; 1,200 tokens are refused with
"input (1200 tokens) is too large for the encoder" and `n_cmpl: 2` with "an
encoder-decoder model does not support n > 1", both 400, and the server
goes on; after 60 requests all 7 slots are idle. The card gates follow on
the node (below), with the MADLAD byte compare against today's engine.
**On the card:** the gates in tools/measure/t5waves.py (T4 against P3's
one-slot bytes, T3, in-wave vs alone, seconds per question), then the
retrieval comparison on the 451 + 39 queries (translator-choice.md).

**Measured on the card, 2026-10-28 (München, the node paused):** T4 PASSES —
379 of 379 one-at-a-time translations byte-identical to P3's, 0.43 s each;
a 7-slot wave answers a question in 1.2–1.6 s against torch's 3.2 s; T3
PASSES (626 of 626 the same twice: deterministic). **But a wave decodes
WRONG**: in-wave against alone 0 of 626 equal, in-wave against torch 0 of
626. Two identical prompts in one wave come back as two different wrong
texts (`' Le gardien de la lampe.'`, `' Le gardien allum .'` for `' Le
gardien du phare alluma la lampe.'`), the first two or three tokens right,
then divergence; which text a request gets follows its SLOT, not the
request. Ruled out, each with the same bytes: E1, the strict SYCL env
(`GGML_SYCL_ENABLE_FUSION/GRAPH/OPT=0`); **E2, the same backend, GGUF and
weights on the CPU (`gpu_layers: 0`): CORRECT** — so the server logic of
P4 is right and the fault is a SYCL kernel at two or more decoder tokens
(or two or more encoder sequences); E3, `GGML_SYCL_DYNAMIC_PRECISION=F32`
(every weight mul_mat an fp32 gemm): still wrong, so not the fp16-operand
gemm the F16 build uses at N ≥ 2. What differs between one and several
decoder tokens in `ggml-sycl` (read, not yet measured): the attention over
the F16 cache leaves the N = 1 kernels (`mul_mat_vec_p021`, `_nc`) for
oneMKL `gemm_batch` in half at tiny M and N (M = the key count, N = the
slots), `set_rows` writes several cells, the cross-attention's F32,
permuted K goes through the generic driver's 2-D copies with several
columns, and the position bias is a 3-D permute-and-cont. Nobody runs an
encoder-decoder with several slots on SYCL; upstream has no matching
report (searched 2026-10-28: ggml-org/llama.cpp #21893 reorder/OPT
corruption, #30892 fused XMX dequant-GEMM NaNs — quantized types only,
F16 weights never enter it — #19276 and #30624 flash-attention
corruption, which T5 cannot use; intel/compute-runtime #966, #972).
**The instrument:** `deploy/localai/patches/diag/0005-llama-cpp-sycl-cpu-ops-diag.patch`
(`GGML_SYCL_CPU_OPS=OP[:cond]...` makes the device decline an op class, so
the scheduler runs it on the CPU): one build, then one model load per op
class on the real failing probe; and R0 before it, `cache_type_k/v: f32`
in the YAML (no build), which takes the cache attention off the F16
kernels. **R0 (09:05): CORRECT with the f32 cache**, so the fault is in the
F16-cache path (the half `gemm_batch` of the attention, its conversions, or
the cache write), and the f32 cache is the workaround (+9 % time, 2× cache
memory). **NAMED (09:40, the diag build 03c771b88d03, München):** declining
the KQ product over the F16 K cache alone (`MUL_MAT:f16:nc:nN:p`) restores
the bytes; declining the KQV product alone does not; a K cache in f32 alone is
right, a V cache in f32 alone is not. So: `ggml_sycl_mul_mat_batched_sycl`
for the permuted F16 K view and the permuted queries at N ≥ 2 (the half
conversion of q, then oneMKL `gemm_batch` at M = the key count), on this
card. Upstream report drafted: upstream-bugs.md S1; the shape reproducer
`diag/test-backend-ops-kq-shapes.patch`. **Shipped with the f32 cache (both
K and V: the V-side call is the same library function at the mirror shape,
proven only at N = 2); the gates re-run on it. Until they pass the switch
does not happen;
nothing live has changed** (the torch translator serves; the test
backend and model are unloaded). In-wave vs alone (bar 90 %) is now a gate
in tools/measure/t5waves.py, not a report (fb014fe).

**The f32-cache gates, 2026-10-28 08:00–10:30 (München; from 09:51 the card
otherwise idle at the operator's order): PASS.** T3, the same group twice:
625 of 626 identical — the one row (WQ9339507 pl→en, "from his trip" against
"from a trip") came from the contended hour, when a question's group split
into waves of 2–5 slots where the paused run made waves of 6–7, and the batch
shape flips a last bit: a property of waves, recorded, not a defect. In-wave
vs alone 614 of 626 (PASS, bar 90 %; the 12 are one-word or punctuation
variants). Torch agreement: wave 555, alone 556 of 626. T4 as information:
372 of 379 identical to P3, 7 one-word variants, torch 342 against 343.
**Timing, node paused:** waves median **1.12 s per question**, p90 1.56 s,
against torch's 3.2 s (2.9× faster); one at a time 0.72 s per translation;
per token, one slot, paired on the same idle card: **29.2 ms on f32 against
16.3 ms on F16 — the f32 cache's own price, +79 % per token** (the generic
f32 path copies the K view per head; the F16 kernels come back when
upstream fixes the KQ product, upstream-bugs.md S1); 36.1 ms per step per
slot in waves. **Retrieval gate**, paired against torch MADLAD on the same
index (490 questions): descriptive n = 451 MRR 0.254 / R@10 0.446 / R@50
0.627 on both sides (+3/−1, p = 0.625); titles n = 39 identical. **Switched
2026-10-28:** this branch merged to main, the f8b4acdcdcb8 tar installed as
`vega-llama-cpp-t5`, `louise-translate-madlad` is the GGUF with the f32
cache, the torch model kept as `louise-translate-madlad-torch` for backout.
**Backed out the same morning (10:47), torch live again — a CLIENT defect,
not the backend's:** `app/translate.py shape()` told MADLAD-on-llama.cpp from
the torch backend by `/v1/tokenize("<2en>")` returning ONE token; LocalAI's
tokenize route does not parse special tokens and answers `[805, 38]` (two
pieces; the completion path does parse them, which is why every gate passed
with raw `<2xx> seg` prompts), so the probe said "batch" and cached it 600 s,
the warm sent a JSON prompt with no `max_tokens`, and the model generated to
its limit, 92 s, under the operator's paired test (A1, 60 questions through
/api/search beside gutenberg sharing). The gates had never exercised
`shape()`: t5waves.py speaks to the server directly. Fixed: the shape is told
by the VOCABULARY (`_t5_languages`' `/v1/detokenize` scan finds ≥ 16 `<2xx>`
codes; the scan is kept as the language list, so it costs nothing extra),
every batch-shape request carries a `max_tokens` bound (torch ignores it), and
the test fake answers the recorded two-piece tokenize. A1 is rerun on the
fixed client before the switch is tried again.
**Second attempt, 2026-10-28 11:19–11:42 (München, client 0ef4e4a): quality
equal, speed NOT better, backed out to torch at 11:42.** The same 60
questions through /api/search under gutenberg sharing: translation median
1.95 s against torch's 1.68 s (+16 %), p90 2.74 against 2.78, search total
10.81 against 10.65 s; 341/381 translations identical to torch's, 59/60 the
same top hit, 55/60 the same twelve; warm 7.8 s, shape t5, 498 languages, no
split waves. Through Louise torch's one batched call costs 1.7 s for six
languages, and the GGUF wave — 1.12 s on an idle card — lands at 1.95 s with
the f32 cache (+79 % per token) beside the embedder. The switch condition is a
gain; the F16 cache, back with P5, is the way to one (16.3 against 29.2 ms per
token). The GGUF YAML is kept on the node as
`models/louise-translate-madlad.yaml.gguf-20261028b`; results
`/root/madlad-gguf/a1b-{torch,gguf}.jsonl`.
**Root cause of the card fault found the same day: P5 below** — not the
card, not the runtime, not oneMKL: ggml-sycl's own src1 conversion on the
oneMKL path. The f32 cache stays until the P5 tar passes its gates.


### P5. llama.cpp backend (ggml-sycl): the batched F16 matmul converts src1 for the GEMM that runs

*Base since 2026-11-21: LocalAI v5.0.1 (`dd5780a`, llama.cpp `78e0529`), context refreshed, code unchanged (above). Before:* *master `16acc89` since 2026-11-02 (unchanged bytes; at `ddf48f8` its
hunks apply with offsets, no fuzz); `v4.12.1` (`ec92740`) before. After P1–P4 (independent of them: a ggml-sycl
file). File: [`deploy/localai/patches/0005-llama-cpp-sycl-batched-mul-mat-src1.patch`](../../deploy/localai/patches/0005-llama-cpp-sycl-batched-mul-mat-src1.patch),
which adds `backend/cpp/llama-cpp/patches/0005-sycl-batched-mul-mat-src1-strides.patch`
(applies to plain `c0fd786`, checked with `patch --dry-run`). Written
2026-10-28 by code reading; München's log of upstream's suite matched the
prediction exactly; the card run of the fixed build is the gate.*

**What.** `ggml_sycl_mul_mat_batched_sycl` (F16 src0 × F32 src1: the KQ
product over an F16 K cache, the KQV product over an F16 V cache) converts
src1 to F16 before its GEMM. A build with oneDNN compiled in
(`GGML_SYCL_DNNL`, ours: the backend needs libdnnl.so.3) made a FLAT copy of
src1's whole extent — right for oneDNN's GEMM, which reads src1's own strides
— and then set `s11 = ne10, s12, s13` (the CONTIGUOUS strides) and
`is_src1_cont_2 = true` whichever GEMM ran. When oneMKL runs instead of
oneDNN — `GGML_SYCL_ENABLE_DNN=0`, which invariant 12 requires (oneDNN's
heuristic drifts), or a device without an optimized oneDNN GEMM — its
`gemm_batch` calls read the flat copy as contiguous: wrong data for every
non-contiguous src1. The fix decides the GEMM once, before the conversion:
flat copy for oneDNN (strides untouched), the gather `to_fp16_nc_sycl` with
the contiguous strides for oneMKL. Upstream: since bdca38376 (`sycl: Hotfix
for non dnnl codepath`, #14677, 2025-07-14) for a oneDNN build with the DNN
switch off; the `has_optimized_gemm` runtime gate (#28985, 2026-10-02) sends
devices without one down the same path with DNN=1. No upstream issue or PR
(searched 2026-10-28, c0fd786 and master e486a4b; none of the 20 ggml-sycl
commits since c0fd786 touch it).

**Why it explains everything measured** (localai-bugs.md P4 "Measured on the
card", upstream-bugs.md S1): the decoder's queries are PERMUTED (`[0,2,1,3]`)
— for ONE token the permuted layout is the contiguous one (right: N = 1 was
right, and takes `mul_mat_vec_p021` anyway), for two or more it is not
(wrong: every N ≥ 2); the V side's src1 is the softmax output, contiguous
(right: V f32 alone changed nothing); an F32 K cache takes the generic
path (right: K f32 alone fixed the text); the GPU runtime is not involved
(26.27 and 26.35 identical); upstream's suite fails EXACTLY its 32 strided
F16×F32 views with `ne13 > 1` — `bs2 = 1` takes `mul_mat_vec_nc`, not the
batched path — and its permuted cases with n = 1 pass (contiguous layout),
while the suite's permuted F16 cases with n ≥ 2 and `ne3 > 1` are "not
supported" by `supports_op`, which is why nobody saw it. The patch adds the
decoder KQ shape (16 heads, n = 2, 4, 7) to `test-backend-ops`.

**Confirmed on the card before any rebuild (München, 2026-10-28 11:43): the
same binary with `GGML_SYCL_ENABLE_DNN=1` — only that env changed — passes
everything: upstream's F16×F32 filter 223 OK / 0 FAIL / 60 n.s. (DNN=0: 191 /
32 / 60), the KQ shapes 272 / 0 (DNN=0: 34 / 238); logs
`/root/kq/{std-f16,kq-run}-dnn1.txt`.** The oneDNN path reads the strides;
the oneMKL path read the flat copy as contiguous.

**Gate (München; the card).** (1) DONE 2026-10-28 11:55 — `test-backend-ops`
from plain c0fd786 + P5's embedded patch + the diag KQ-shapes patch, SYCL F16,
oneAPI 2025.3.0, DNN=0 (sha256 `01ec9540…`): `-o MUL_MAT -p
"type_a=f16,type_b=f32"` **226 OK / 0 FAIL / 60 n.s.** (191 / 32 before; the
three more OK are P5's decoder KQ cases), `GGML_TEST_KQ_SHAPES=1` **272 / 0**
(34 / 238), the whole `-o MUL_MAT` suite **2276 OK / 0 FAIL / 592 n.s.**; logs
`/root/kq/{std-f16,kq-run,mm-all}-p5.txt`. (2) and (3) DONE 2026-10-28 12:02–12:18 (München;
node paused, card otherwise idle): the tar `louise-llama-cpp-sycl_f16-c68167b65cb0.tar`
(series digest **c68167b65cb0**, main ed4e9e7 = P1–P5; sha256 `0fed8958…07dba2`;
a new engine) installed as `vega-llama-cpp-t5`; MADLAD on the **F16 KV cache**
(the YAML of the f32 run minus its two `cache_type_*` lines; `parallel:7`,
`ubatch:1024`, DNN=0), t5waves fb014fe: **T4 379/379 = P3's bytes** (343/379 =
torch; 0.43 s per translation); **T3 same group twice 626/626, in-wave == alone
616/626** (bar 90 %; the 10 are word variants, "raconte l'amour" / "sur
l'amour"; torch agreement 556 in waves, 560 alone); **0.68 s per question, p90
0.96 s** (f32 cache: 1.12 / 1.56; torch 3.2); **per token 16.3 ms** one slot
(= the F16 figure of the morning), 21.6 ms per slot step in waves (f32: 36.1);
waves 1 slot ×802, 6 ×187, 7 ×62, none still waiting; retrieval paired to torch
on the same index: descriptive n = 451 MRR 0.254 / R@10 0.446 / R@50 0.627 on
both sides (+3/−1, p = 0.625), titles n = 39 identical. Files
`/root/madlad-gguf/waves-p5/`. **The F16 cache is cleared for MADLAD on
engine c68167b65cb0.** Step 4, A1 through Louise under sharing, decides the
switch (a median gain with p90 not worse) — **MET 12:21–12:43: translation
median 1.44 s against torch's 1.85 (−22 %), p90 2.21 against 2.57, 60/60,
342/381 identical, under more load than the torch pass; SWITCHED, and it
stays** (translator-choice.md). The live shared engine
`louise-sycl-f16-llama-cpp` carries the same wrong path (no live model takes
it: embedders and chat run FA on, the rerankers have no cache); it is NOT
rebuilt for this alone — every space on it would be re-gated — the next
rebuild carries P5 (approved-encoders.md "Known limits").


### P9 (staged). llama.cpp backend: the encoder output is kept per sequence

*Base: `v4.12.1` (`ec92740`), after P1–P4. NOT yet rebased onto `16acc89`:
at `ddf48f8` its llama.cpp patch has 4 rejected hunks; it is rebased before
its card gates. File:
`deploy/localai/patches/staged/0006-llama-cpp-encoder-decoder-memory.patch`
(staged: built and tested on the CPU 2026-10-28, NOT in `series` until its
card gates pass — staged/README.md). It adds
`backend/cpp/llama-cpp/patches/0005-encoder-decoder-memory.patch`, applied
by the backend's `prepare.sh` after P4's: 323 lines added and 23 removed
across `src/` (new `llama-memory-enc-dec.h/.cpp`, 10 files), `tests/` and
`tools/server/server-context.cpp`.*

**Why.** P4 encodes the slots about to start together because the library
keeps ONE encoder output per context (`llama_context::cross`), and must
therefore hold a new request until the running wave has decoded. Stage 1
of docs/notes/llama-cpp-encoder-decoder.md gives T5 its own memory: the
decoder's `llama_kv_cache` plus a host-side store of encoder rows, each
owned by the sequences it was encoded for. An encode replaces the rows of
the batch's sequences and keeps every other sequence's; a decoder ubatch
uploads the rows its sequences own (by contiguous runs: a lone sequence is
one run, the same bytes as before) and masks each token to its own
sequence's rows; a sequence with no rows is refused (a new internal status
`LLAMA_MEMORY_STATUS_FAILED_INPUT`, `-1` from `llama_decode`). A
whole-sequence removal, `clear` and `seq_keep` release the rows, `seq_cp`
shares them, the sequence state carries them as a suffix (magic `T5XE`,
version 1; `PARTIAL_ONLY` writes none). For `n_seq_max > 1` the context
forces `kv_unified`, so the self-attention mask and the KV views agree.
No public API changes; only `LLM_ARCH_T5` constructs the memory;
`llama_cross` and the graph parameter stay, filled as before, for the
carried patch (deletions wait for the upstream PR).

**Deviations from the note's §3**, each for a reason found in the code:
- `init_update` is delegated to the decoder cache (its update context is
  only used by the shift graphs, which never build the T5 decoder);
- the graph reserve sizes the cross tensors to `n_seq_max × min(n_ubatch,
  n_ctx_train)` rows (the note said `n_seq_max × n_ubatch`): a T5 input
  longer than its window is refused by the server, and the scheduler
  reallocates if a live graph is ever larger;
- the cross inputs decide by `arch == LLM_ARCH_T5 && mctx`, not by a
  dynamic cast; the encoder graph has no memory context and never reaches
  them.

**Server (on P4):** a wave is encoded while earlier ones decode (the
`enc_live` gate is gone; the 1 ms rest only when nothing decodes); the
prompt start skips its whole `seq_rm` for a slot the wave just encoded
(it would release the rows just stored); the load-time probe encode and
`common_context_can_seq_rm` (which decodes after a clear) are skipped for
these models, speculative decoding stays unsupported; the forced unified
cache is mirrored in `params_base.kv_unified`. `n > 1` stays refused.

**Checked on the CPU** (random-weight T5, `tests/test-llama-archs -a
'^t5$'`; `tests/test-enc-dec-store`; the tiny-model server checks of P4):
a sequence alone gives the bytes of a one-sequence context; two sequences
decoding in one batch are each within nmse 1e-4 of their solo runs, NOT
bitwise (as §6 T2 predicted: the batched kernels differ); a whole removal,
a clear and a truncated state restore leave `-1` with no abort and the
other sequence untouched; a partial removal, `seq_cp` and a restored
state keep the output; the store's replace/compact/share/keep semantics
and its bound (`n_seq_max × n_ubatch`) hold; a lone server request is
byte-identical to P3's, the same 7 prompts in 3 orders give the same
bytes, 60 requests after a burst leave every slot empty, and a request
arriving while a wave decodes is answered in 0.06 s (P4: after the wave).

**Card gates before it moves into the series** (staged/README.md): the
f32-cache MADLAD backend rebuilt with it AND P6 — T3, in-wave vs alone (bar
90 %), a lone request against P4's bytes, seconds per question — then
the retrieval comparison. Stage 2 (the device store) is P6 below.

### P10 (staged). llama.cpp backend: the encoder outputs live on the device

*Base: `v4.12.1` (`ec92740`), after P1–P5. NOT yet rebased onto `16acc89`
(with P6). File:
`deploy/localai/patches/staged/0007-llama-cpp-encoder-decoder-store.patch`
(staged with P5: built and tested on the CPU 2026-10-28, NOT in `series`
until the card gates pass — staged/README.md). It adds
`backend/cpp/llama-cpp/patches/0006-encoder-decoder-store.patch`, applied
by the backend's `prepare.sh` after P5's.*

**Why.** Stage 2 of docs/notes/llama-cpp-encoder-decoder.md, the shape
upstream asked for (a persistent buffer, no host copies: PR #11213
r1969892524, #22728). P5 kept the encoder rows on the host and uploaded a
ubatch's rows at every decode step; here the store is one F32 tensor in
its own backend buffer, `(n_seq_max + 1)` REGIONS of `n_ubatch` rows
(MADLAD at `-np 7 -ub 1024`: 8 × 1024 × 1024 floats, 32 MiB on the card).
The encoder graph writes its output rows into the lowest free region with
`ggml_set_rows` (its context, `init_encode`, assigns the region; a new
default-nullptr virtual on `llama_memory_i`), and `llama_context::encode`
COMMITS after the compute succeeded — the batch's sequences leave the rows
they owned and own the new ones — so a failed encode keeps the old output
(the §8 question, settled as write-beside plus a post-compute commit). A
sequence owns rows in one region at a time, so a free region always
exists. The decoder gathers the rows its ubatch's sequences own with
`ggml_get_rows` on an I32 index input; the mask is unchanged. The
synchronize and memcpy of the encoder output and the per-step upload are
gone for T5. Sequence operations move no data: they are owner bookkeeping
on the rows. State writes carry the rows through the io's tensor path
(suffix version 2: the owned rows of each used region at their in-region
index), so `llama_memory_get_buffers` sees the store's buffer and a
single-sequence restore lands in a free region. Each state write of the
whole context and each single-sequence restore is region-shaped; a
file from a store of another shape (`n_embd`, region size) is refused.

**Checked on the CPU**: the same tests as P5 — `tests/test-llama-archs -a
'^t5$'` (a sequence alone bitwise equal to a one-sequence context; two
sequences in one batch within nmse 1e-4 of their solo runs; whole removal,
clear and a truncated restore leave `-1`; partial removal, `seq_cp` and a
restored state keep the output), `tests/test-enc-dec-store` rewritten for
the device-backed store (regions chosen lowest-free and released by the
commit, shared rows, `seq_cp`/`seq_keep`, the `n_seq_max + 1` bound, state
whole and per sequence, truncated and foreign files refused), and the P4
server checks (a lone request byte-identical to P3, the same 7 prompts in 3
orders, bursts, a late request answered in 0.06 s).

**Card gates** with P5 (one rebuild): plus `test-backend-ops -o
SET_ROWS,GET_ROWS` on the node for F32, which the device store relies on.

### P11 (staged). llama.cpp: OmniVoice speaks on llama.cpp's own audio generation

*Base: LocalAI v5.0.1 (`dd5780a`, llama.cpp `78e0529`), after P1–P8. File:
`deploy/localai/patches/staged/0009-llama-cpp-omnivoice.patch` (staged: built
and gated on the CPU 2026-11-24, voice cloning and described voices
included, NOT in `series` until the card gates pass — staged/README.md). The operator, 2026-11-24:
"Evaluate to patch our backend to support omnivoice, I would like to have
only one backend", then "Go with option D" (docs/notes/omnivoice-on-our-backend.md).*

**What LocalAI does today.** OmniVoice is served by a backend of its own,
`omnivoice-cpp`: a Go wrapper around omnivoice.cpp, which links its own ggml
fork. On a node that builds its llama.cpp backend from a pinned series with
its own SYCL fixes, speech runs on an engine nobody there builds, pins or
qualifies.

**What the patch does.** One llama.cpp patch (`0012-mtmd-omnivoice.patch`),
nothing in LocalAI's own sources: the llama.cpp backend already answers the
TTS call through libmtmd's audio generation, so an OmniVoice model is a
`model:` (the backbone, a new libllama architecture: Qwen3 layers read in
both directions, no KV cache, no vocabulary head on embedding rows) and an
`mmproj:` (the audio heads, the residual codebooks, the DAC decoder, and an
audio encoder that turns a voice clip into codec frames). A request's
`voice` is a clip to clone or a description of a voice, through llama.cpp's
own speaker-reference and "tts" template paths. The
32-step decoding is host code in mtmd-helper on a context of its own;
omnivoice.cpp's host-side rules (language names, duration estimate, chunking,
post-processing, Philox noise) are vendored unchanged under its MIT notice.

**One deliberate difference from omnivoice.cpp.** It adds its attention mask
to the scores (+1 / 0), which is what transformers' "eager" attention does
with the reference's boolean mask and what omnivoice.cpp was validated
against; the reference's default path (SDPA), its training and its authors'
fast path block with it. The port blocks. On one sentence the two give 1,298
different codes of 1,344.

**Tested (CPU).** Against omnivoice.cpp a650381 on the same ggml, with the
mask blocked, step by step — the codes themselves cannot be compared, the
decoding being chaotic under rounding: prompt ids identical; logits on the
same codes within omnivoice.cpp's own rounding noise at steps 0, 1, 8, 16
and 31; omnivoice.cpp's scoring on the port's logits gives the port's codes
at all 32 steps; the two decoders agree to 1.25e-6 but for the last 83 ms of
a chunk (two transposed convolutions omnivoice.cpp pads with a zero); two
runs byte-identical; a clip's codes equal on 158 of 159 frames, the other on
a near-tie (`tools/measure/omnivoice_port_gate.py`,
`tests/test_measure_omnivoice_port_gate.py`, `tests/test_omnivoice_repack.py`).
**Speed (CPU, equal threads, one job on the machine):** 1.2–1.5 times
omnivoice.cpp's on a sentence, 2.1 times with a voice clip; a slot keeps the
last clip's frames, so an answer read sentence by sentence encodes its
clip once; one log line per request says where its time went
(omnivoice-on-our-backend.md §11; `tests/test_localai_staged_omnivoice.py`).

**For LocalAI's team, when it is filed:** nothing to change in LocalAI; the
gallery would list the two repacked files under the llama.cpp backend. For
llama.cpp's: the patch needs a converter from the Hugging Face checkpoint
(`conversion/omnivoice.py`) in place of our repack of omnivoice.cpp's files,
and the vendored headers restyled or rewritten.

### P12 (staged). llama.cpp backend: TTS takes a transcript, a described voice, or no voice

*Base: LocalAI v5.0.1 (`dd5780a`), after P1–P8 and P11. File:
`deploy/localai/patches/staged/0010-llama-cpp-tts-voice-modes.patch` (staged
2026-11-24 with P11; docs/notes/omnivoice-on-our-backend.md §10).*

**What happens.** `core/http/endpoints/localai/tts.go` resolves a saved voice
profile into `voice` = the clip's path and `params["ref_text"]` = its
transcript. The llama.cpp backend's `prepareTTSTask` knows two kinds of
model — a required speaker reference (`voice` is a path) or, where llama.cpp
has per-session voices, a named voice for the "tts" template — and never
reads `ref_text` or the request's `instructions`. A model that clones and
describes (OmniVoice) is then refused without a voice, has a description
opened as a file, and clones without the transcript the profile holds. At a
llama.cpp pin without `set_voice` (v5.0.1's own, 78e0529) the template branch
is compiled out altogether, though `common_chat_get_tts_prompt` is there.

**The patch.** `tts_request_options.h` gets a third mode, a reference OR a
name: `voice` is the reference when the caller found a file of that name, a
name or description otherwise, and may be empty; `instructions` is the
template's voice when the request names none; `ref_text` is kept beside a
reference, in the required-reference mode too. `grpc-server.cpp` picks the
mode from the model (its speaker-reference capability and whether it has a
"tts" template), hands the transcript to the helper's `speaker_ref_text` and
renders the template whenever the mode describes. `prepare.sh` gains the
probe `LOCALAI_HAS_TTS_TEMPLATE`; `set_voice` stays behind
`LOCALAI_HAS_TTS_VOICE`. Qwen3-TTS Base and LFM3-Audio behave as before,
but for the transcript now reaching the first.
**And the decoding's seed, steps and guidance (added 2026-11-24, found when
the card timing was prepared):** `params["seed"]`, `["flow_steps"]` and
`["cfg_scale"]` — the three fields llama.cpp's own `/v1/audio/speech` reads
from its body and LocalAI's call could not set. Without a seed the helper
draws one for every request: the same text never gave the same audio twice,
for any model on this backend.

**Tested.** `tts_request_options_test.cpp`: the thirteen cases there before
and four new ones (a clip with its transcript, a description, neither,
`instructions`, both together; the required reference keeping its rule; the
three decoding fields parsed, and refused when malformed).
NOT compiled here: the `grpc-server.cpp` half builds with the backend.

**For LocalAI's team:** the patch is independent of OmniVoice and could be
filed on its own — the transcript of a voice profile is lost on the llama.cpp
backend today for Qwen3-TTS Base as well.

### P13 (staged). LocalAI's server: `tts.voice_cloning` is honoured for a backend the table does not know

*Base: LocalAI v5.0.1 (`dd5780a`), on the series or on v5.0.1 + P1 alone
(the tree the node's server is built from); independent of P11 and P12.
File: `deploy/localai/patches/staged/0011-config-voice-cloning-opt-in-unknown-backend.patch`
(staged 2026-11-24). The operator, the same day, to München: "Patch LocalAI
and add the improvement to the list of the fixes I have at some point
send".*

**What happens.** B17: `VoiceCloningForModel`
(`core/config/backend_capabilities.go`) answers "no cloning" as soon as the
model's backend name resolves to no entry of `BackendCapabilities`, before
it reads the model's `tts.voice_cloning` — the field its own comment gives
operators to opt in with. LocalAI's test file tells the same story for
`audio-cpp`, which was fixed by registering it; a backend an operator
builds and names cannot be registered.

**The patch.** 17 lines in that function: for a backend the table does not
know, `tts.voice_cloning: true` returns `referenceVoiceCloning()`, the
contract the cloning backends share (a WAV, a transcript required).
Nothing else moves — no field: no; `false`: no; a known backend that cannot
clone (piper) is not opened by the field; a model that names no backend is
not either.

**Tested.** LocalAI's `core/config` package, `go test -tags
nosqlite,purego_utp`, on v5.0.1 with the generated protocol code: all specs
pass with the six new ones; without the change two of the six fail (the
opt-in, and the contract being each caller's own copy).

**For LocalAI's team:** file as it is. It is independent of OmniVoice and of
our engine: any external backend installed under a name of its own meets
B17 with the first saved voice profile.

### B18 and P14 (staged). ggml-sycl: a refused pool commit ends the process

*Seen on the node 2026-11-24 (München): nemotron-embed's backend, loading
beside the 35B, died with `level_zero backend failed with error: 39
(UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY)` … `SYCL error:
CHECK_TRY_ERROR(phys.emplace(dev, ctx, reserve_size))` … `in function alloc
at ggml-sycl.cpp:2147`. File:
`deploy/localai/patches/staged/0013-llama-cpp-sycl-pool-refusal.patch`.*

**What happens.** ggml-sycl's virtual-memory pool commits device memory for
the temporaries of the ops while a graph runs; the commit is wrapped in
`SYCL_CHECK`, which aborts. The model's buffers themselves fail softly (a
device allocation that returns null fails the load or the context), so the
abort is the one path where contention on a shared card costs a process:
the weights, every slot, the request, and for LocalAI a cooldown of 500s
and 503s.

**The patch.** The pool throws `ggml_sycl_out_of_device_memory` before it
has changed anything; `ggml_backend_sycl_graph_compute` catches it, drains
the queue and returns `GGML_STATUS_ALLOC_FAILED`. llama.cpp already turns
that into `-2` from decode/encode and its server into "Compute error." for
the request, without stopping. 36 lines. Left as it was: a refusal inside a
SYCL graph RECORDING (`GGML_SYCL_ENABLE_GRAPH=1`, off by default), and the
two older pools.

**Tested.** Compiled here 2026-11-24 with Intel's compiler (oneAPI 2025.3.3: the
engine's SYCL backend builds and links with it), never run. On the card (docs/notes/room-on-the-card.md
§4): the device filled, a model asked — the request refused, the process
alive, the same model answering afterwards. **Run 2026-11-24 (München):
not provoked** — at 31,701 of 32,002 MiB every request was served, the
driver paging a neighbour out instead of refusing. The path is exercised
with a test switch instead, `GGML_SYCL_POOL_REFUSE=N` (staged
`0016-llama-cpp-sycl-pool-refuse-switch.patch`, in the parking
prototype's engine; parking-prototype.md §4).

**PROVOKED, AND 0013 DID NOT HOLD (München, 2026-11-25, engine b190673dbdbf).** A reranker copy with `GGML_SYCL_POOL_REFUSE=2` against a control: the control served the short pair and the twelve long documents three times (1.54–1.59 s, the same scores, its process alive). With the switch, the first request that grew the pool ended the PROCESS — HTTP 500 "error reading from server: EOF" after 3.0 s — and so did LocalAI's two reloads, each at its first refusal. In each process, in one millisecond: "the device refused 6 MiB for the pool (2 MiB committed)", then "the SYCL pool could not commit device memory / Exception caught at … ggml-sycl.cpp, line:4221, func:operator()", then "SYCL error: CHECK_TRY_ERROR(op(ctx, src0, src1, dst, …)): Exception caught in this line of code … in function ggml_sycl_op_mul_mat". Never "the graph is abandoned", never "Compute error.". **Why:** 0013 threw the refusal as a `std::runtime_error`; the backend calls its ops under its own macros, `SYCL_CHECK(CHECK_TRY_ERROR(op(…)))`, and `CHECK_TRY_ERROR` catches ANY `std::exception` and aborts (72 call sites). The op that asked for the memory took the refusal; the graph's catch was never reached. The live engines since 2026-11-24 therefore die on a refused pool commit exactly as every engine before them: no worse, and not fixed. It was not seen before the card because nothing here runs the SYCL path, and filling the card did not provoke it.

**The fix, staged 0018** (`0018-llama-cpp-sycl-pool-refusal-reaches-the-graph.patch`, candidate `engine-refusal.series`: the live engine and this one patch): the refusal is a plain struct, neither a `sycl::exception` nor a `std::exception`, with a `static_assert` for each, so only the graph's own catch can take it. Checked without a card, in the three layers a card run crosses: (1) the backend's REAL macros under Intel's compiler (`tools/measure/sycl_refusal_macro_check.cpp`): the old type aborts with the card's own lines, exit 134; the new one reaches a catch outside the op, exit 0; (2) what an abandoned op leaves, read: its temporaries are the pool's and are released in reverse order as the stack unwinds; the one thing ops change for good, the layout of quantized weights, takes its scratch memory from the device allocator, not the pool; no handler between an op and the graph catches `(...)`; (3) everything ABOVE the backend, run: a CPU stand-in that fails two graphs with `GGML_STATUS_ALLOC_FAILED` (`tools/measure/refuse_cpu_standin.patch`, `tools/measure/refuse_cpu_test.py`, 13 of 13) — the request is answered 500 "Compute error." (503 from the canary when the context is made for the request), the process is the same one, and the next request returns the control's vectors bit for bit. What is left for the card is the one thing none of these is: a real device between (1) and (3) — the same row again. (room-on-the-card.md §4 has the same, with the steps.)

### B20 and P19 (staged). A voice lease outlives the process that took it

*Seen on the node 2026-11-25 (München, after deleting a TEST profile in the
port's rows): `voice-profiles/.leases` held nine directories, eight empty,
one with a single file — 1,331,940 bytes, link count 2, the same inode as
the original 27.75 s clip in a living profile, made in October by a process
long gone. Nothing of the deleted test profile remained. Base: LocalAI
v5.0.1 (`dd5780a`) + P1, the node's server tree.*

**What happens.** `Store.LeaseAudios` hard-links a profile's clip into the
Store's own lease directory (`.leases/<uuid>`, one per process) so that a
delete does not break a synthesis in flight; the handler of `/tts` and
`/v1/audio/speech` gives it back with `defer release()` when the request
ends (the realtime endpoint, when its session ends). `Store.Close` removes
the directory. A process that is killed does neither, and no later process
looks: `Delete` removes the profile's directory and leaves every lease
directory alone. So a recording can stay on disk after its profile is
deleted, for as long as the data directory lives.

**The fix, P19** (`0019-voiceprofile-abandoned-leases.patch`, a patch of
the SERVER): the Store keeps `.leases/<uuid>/.owner` locked while it lives
— `flock` on Unix, `LockFileEx` on Windows, as LocalAI's add-on stores do;
the kernel drops the lock when the process ends, however it ends. A lease
directory whose owner file can be locked belongs to nobody: it is removed,
with every lease in it, when a Store starts and whenever a profile is
deleted. A living Store's leases are never touched. A directory without an
owner file (every one made before the patch) is removed once the last lease
made or released in it is a day old — by the directory's own time, since a
lease is a link and carries its clip's. Only real directories are
considered: a link under `.leases` is not followed, a stray file not
removed.

**Tested.** LocalAI's own suite for the package, 25 specs with 6 new: a
killed process's lease is gone with the profile's delete and no WAV is left
below the store (this one FAILS without the change); a living store's lease
survives the delete and is readable until released; a new Store removes what
the ones before it left; a directory without an owner file goes only once
it is old; a symlink and a stray file are left alone; the owner file is
0600 and gone at Close. `core/http/endpoints/openai`'s suite passes with it.
Compiles for Windows (`GOOS=windows go vet`).

**On the node.** It takes effect when the SERVER is rebuilt with it — the
engine's tar does not carry it. At its first start it removes the nine
lockless directories, the October link among them (all older than a day):
that is a deletion on the node and is the operator's word, put to him with
the pass by München. Until it is live, the rule the architecture session
set stands: no recorded profile is deleted without `find … -samefile` on
its clip under `.leases`, before and after, said in the report.

**On the node, 2026-11-25 (München).** The server with P19 started at
~04:51Z: seven lines "Removed the voice profile leases of a process that is
gone", the October link gone, Philippe's original clip back to link count
1, one lease directory with its `.owner` file; the seven run ids of the
engine unchanged by the server's swap. Then the three layers after a TEST
profile's delete (HTTP 204): its directory gone with its encoding; no file
of its clip's inode anywhere under `voice-profiles`; and the port, in the
very process that had spoken the deleted voice last, answering Philippe's
voice from his own kept encoding. A request naming the deleted profile by
its reference: HTTP 404 from LocalAI, the backend not called.

**The same condition, one layer down, in our own port:** the engine kept
the last clip's samples and rows in memory between requests (staged 0020,
live in d531d5d804f1).

### B19 and P15 (staged). The backend calls `exit()` inside its signal handler

*Seen on the node 2026-11-24 (München, the load timings retaken on an
unloaded node): 3 of 14 `POST /backend/shutdown` of a model behind our vega
wrapper took 15.06 s instead of 0.11–0.21 s; 0 of 22 for the same engine
started directly. File:
`deploy/localai/patches/staged/0014-llama-cpp-second-signal-exit.patch`.*

**What happens.** LocalAI's unload ends in `process.Stop()`
(`pkg/model/process.go` L190) of mudler/go-processmanager v0.2.0:
- SIGTERM to the backend's PROCESS GROUP (`process_unix.go` L39–L46,
  `syscall.Kill(-pid, …)`; `KillProcessGroup` is true by default and the
  child is started with `Setpgid`);
- a poll every 100 ms for the pid it started, for `GracefulTimeout` — 15 s
  by the library's default (`config.go` L40); LocalAI passes no
  `WithGracefulTimeout` (`process.go` L332–L341) and has no setting for it;
- then SIGKILL to the group. The HTTP answer is written after that, and
  `exitCode=-1` in "Backend process stopped" is Go's code for a child ended
  by a signal.

Behind the wrapper the engine was in that group, so it took LocalAI's
SIGTERM and, a millisecond later, the one the wrapper sent its child. The
backend's handler (`grpc-server.cpp` L218–L227 at v5.0.1; llama.cpp's
`tools/server/server.cpp` L32–L41 at our pin) serves the first by ending
the server loop — main() then prints "stopping", "cleaning up before
exit...", shuts the gRPC server down and frees the backend — and answers
the second with `fprintf` and `exit(1)`. `exit()` is not safe inside a
handler: it runs the exit handlers and the static destructors against the
clean-up it interrupted. The log is the same in the fast and the slow case
("Received second interrupt, terminating immediately." within 1 ms); in the
slow one the process stays, the card's counter returns to empty only at
LocalAI's kill. Where exactly it blocks was not read: the threads' stacks
during a slow case would say.

**Our side.** The wrapper no longer tells a backend twice (architecture,
#856: a backend already told is left `OWN_EXIT_S` = 2 s to go by itself,
and the wrapper's whole stop is over in 12 s, under LocalAI's 15). One case
stays open there: an engine slower than 2 s to leave — a forced stop with a
call in progress, a busy node — gets the second signal after all.

**The fix (P15, staged).** The second signal leaves through a small header,
`second_signal.h`: `write(2)` of the same message and `std::_Exit(1)`,
nothing else. `second_signal_test.cpp` forks a child whose exit handler
never returns: it is gone at once with status 1, and the same child leaving
through `exit(1)` is still there a second later. The test is standalone,
so `backend/cpp/run-unit-tests.sh` picks it up by its name; compiled and run
here with that script's flags. The backend itself was not built here: the
change in `grpc-server.cpp` is one include and one call. **On the card,
2026-11-24 (München, engine 9ea95e93a83c): PASS** — ten double SIGTERMs on
a direct bge-m3 copy, the process gone after 52–155 ms each, the
second-interrupt line once each. It applies on the
series alone and beside each of 0009–0013. An engine build with it is the
operator's word to München; it can ride the next one.

### P6. llama.cpp (common/chat): Command R7B / Command A tool calls parse

*Base since 2026-11-21: LocalAI v5.0.1 (`dd5780a`, llama.cpp `78e0529`), context refreshed, code unchanged (above). Before:* *master `16acc89` (llama.cpp `ddf48f8`), after P1–P5 (independent of
them: common/chat). File:
`deploy/localai/patches/0006-llama-cpp-chat-cohere-command3.patch`,
which adds `backend/cpp/llama-cpp/patches/0008-chat-cohere-command3-parser.patch`
(applies to plain `ddf48f8` with no fuzz). Written and tested 2026-11-03;
nothing at llama.cpp master after `ddf48f8` touches it (`git log -S
START_RESPONSE`, one chat commit since: gpt-oss).*

**What.** Command R7B's tool calls came back as HTTP 500 on our engine
("Unexpected error in RPC handling"; the backend log: `common_chat_peg_parse:
unparsed peg-native output: I will search for "…" and its translations.[`).
Measured with upstream's test harness at `ddf48f8`: the autoparser preserves
`<|START_THINKING|><|END_THINKING|><|START_ACTION|>` as ONE string, which no
single token matches, so the server drops those three control tokens from the
text; and Cohere's original tool templates end every prompt with
`<|START_OF_TURN_TOKEN|><|CHATBOT_TOKEN|>`, so the derived generation prompt is
empty (a streamed answer fails on its first token). Upstream: #20786 fixed the
generation prompt and was closed as unreproduced (2026-03-20); #20729 (catch
the throw in the server) is open. Ask answers a failed tool round without
tools meanwhile (#303).

**Not reinvented.** vLLM parses Cohere models through Cohere's own library
(cohere-ai/melody, MIT), which defines Command 3 (R7B, Command A) as Command 4
(Cohere2 MoE / North) with `<|START_RESPONSE|>`/`<|END_RESPONSE|>` for
`<|START_TEXT|>`/`<|END_TEXT|>` and a thinking block the model opens itself.
llama.cpp already has a dedicated Command 4 parser (`common/parsers/cohere2moe.cpp`,
written by the autoparser's author because the autoparser "didn't really work"
for it), so the patch routes Command 3 templates to it: the content markers
follow the template, every Cohere marker is preserved, the generation prompt
falls back to the prompt's tail, and the opening `<|START_THINKING|>` is atomic
(a marker still arriving is not streamed as content). The standardized
alternative, written down: link melody itself through its C FFI (a Rust
toolchain in our SYCL build, for one model family) — it would also parse
R7B's `<co>` citation spans, which Ask does not ask for.

**Tests** (tests/test-chat.cpp): Cohere's ORIGINAL R7B templates as the GGUFs
carry them (default and tool_use; upstream's test copy is edited), melody's
cmd3 vectors (src/tests.rs: a plan then a call, a call with no plan, a
non-ASCII argument, two calls in one action), an answer with tools offered,
reasoning_format none, and the preserved tokens and generation prompt (which
a text-fed parser test cannot see). Upstream's R7B and Cohere2 MoE cases pass
unchanged; test-chat, -chat-auto-parser, -chat-peg-parser, -chat-template,
-peg-parser, -reasoning-budget, -server-component: 7/7. Without the parser
change the new checks fail (generation prompt `''`).

**Gates — PASSED 2026-11-06, in the series since** (as `0006-…`; built and gated as `staged/0008-…`, the same bytes: the tag `ea2417d91dcc` is the staged series' digest, the renamed series' digest differs, the engine is the one installed). Engine `ea2417d91dcc` (SYCL tar sha256 `17e72b37…`, CPU `e447157b…`): the same tagged fixed inputs (the calibration set's sixteen fingerprint texts) through the live packages before and after the swap, byte for byte: vega-qwen3-embedding-4b, vega-bge-m3, vega-medcpt-query, vega-nemotron-3-embed-1b, their four CPU twins 16/16 each, louise-rerank-bge 16/16, MADLAD 8/8; then `hold embedder check` on the swapped engine: gutenberg document + query, wikipedia document, pubmed document + query PASS, exact, every fingerprint IDENTICAL to the stated run's. The planned gates were: (1) The SYCL and CPU engines rebuilt with it;
the current gate's fixed inputs through every live encoder and reranker on
the new build, byte for byte against the stated runs (the architecture
session, 2026-11-03: identical bytes = the same arithmetic, RULE 1; any
difference = the full re-gate). (2) Command R7B walked (no `tools_failed`)
and piloted (15 answers), read before any whole run.


### P7. llama.cpp: nvidia/llama-nemotron-rerank-1b-v2 ranks (P10, staged, until 2026-11-22)

*In the series since 2026-11-22 as `deploy/localai/patches/0007-llama-cpp-nemotron-rerank.patch`,
which adds `backend/cpp/llama-cpp/patches/0010-llama-nemotron-rerank.patch`: the
`LlamaBidirectionalForSequenceClassification` architecture (bidirectional Llama 3.2 1B,
mean-pooled, a classification head), its conversion and its server task — the port of
ggml-org/llama.cpp #24083, not merged upstream. Applied as it was: on llama.cpp `78e0529`
every hunk lands with offsets only. Built into its own backend `10aa8b1851d7` and LIVE for
`louise-rerank-nemotron-1b` since 2026-11-11 (the reranker survey's arm A3) while this file
still called it staged — the qualification's finding F4: without it the reranker's GGUF does not
load ("done_getting_tensors: wrong number of tensors"). Parity with NVIDIA's own scoring:
`tools/measure/nemotron_rerank_parity.py`; the index row above has its measurements.*

### P8. llama.cpp backend: context on demand (P11, staged, until 2026-11-22)

*In the series since 2026-11-22 as `deploy/localai/patches/0008-llama-cpp-context-on-demand.patch`
on LocalAI v5.0.1 (llama.cpp `78e0529`): the LocalAI half rebased (one `grpc-server.cpp` hunk,
the pooling-type member, re-placed by hand), the llama.cpp half taken in its upstream form
(`deploy/localai/upstream/llama-cpp-context-on-demand/`, three diffs applied in order; they
differ from the staged form in comments only), applied by LocalAI's `prepare.sh` after `0010`.
Verified by the qualification's phase 2 on the engine it replaces: the gate requires it
(idle-context-brief.md §10: an engine that does not say what it did with its context is not
admitted), and the live engine 281e1ee481b6 carried it from the 11-12 trial while this entry
still said "staged" — finding F4. The entry as it stood follows.*

*2026-11-11. The design and its reasons: [idle-context-brief.md](idle-context-brief.md).
Staged — built and tested on the CPU, NOT in `series`, never on a live model
until its card gates pass and the operator says so (staged/README.md).*

`deploy/localai/patches/0008-llama-cpp-context-on-demand.patch`, built, until 2026-11-22,
only from `staged/context-on-demand.series` (gone with the move). Two halves, one design:

**llama.cpp** (`backend/cpp/llama-cpp/patches/0011-server-context-on-demand.patch`;
the same bytes in `staged/llama.cpp/`, written in upstream's form:
`--help`, `tools/server/README.md`, `tools/server/tests/unit/test_context_on_demand.py`;
the form to FILE — on upstream's master, in three parts — is
[`deploy/localai/upstream/llama-cpp-context-on-demand/`](../../deploy/localai/upstream/llama-cpp-context-on-demand/READING-GUIDE.md).
- `--context-on-demand`, and `keep_context_ms` in a request's body
  (completions, embeddings, rerank): the standard is Ollama's `keep_alive`,
  applied to the context and in milliseconds. A REQUEST THAT CARRIES NONE
  USES THE CONTEXT THE CANONICAL WAY (operator, 2026-11-11: "no free after
  use"): it is not freed after it, until a request that carries one is
  answered; `--keep-context-ms N` gives such requests a keep, for a server
  whose clients cannot be changed (default: none). The context of the load
  itself goes once the server is idle.
- `common`: `common_init_result::free_context()` / `init_context()` and
  `common_init_context_from_params()` — the context is created again from the
  context params the FIRST load resolved (no fitting again: the brief's H2),
  then the steps a load runs after its creation, in the load's own order
  (control vectors, the check of a reranking model, lora adapters, warmup —
  `common_init_context_apply`, shared by both), so the first decode of every
  new context is the warmup's, never a request's. The load returns a context
  one of these steps stopped on, exactly as before, and records WHERE
  (`common_init_result::init_stop`); a later creation is accepted when its
  steps end in the same place — the state of the load's context, or none.
  (2026-11-11, a defect of the first staging: every creation a step stopped
  on was refused, so a model whose load stops there — a reranker whose
  vocabulary fails the reranking check — answered 503 for ever after its
  first free. Found by the reranker's CPU check, fixed before any engine was
  built on it; the brief §10 "The reranker".)
- `server_queue::on_idle()`: a callback of the loop's thread each time it finds
  no task, run WITHOUT the queue's lock (it may free the context; tasks are
  posted meanwhile), which says when it wants to be called again — the wait
  ends AT a keep's deadline, not at the next one-second check (H9).
- `server_context`: only the loop's thread creates and frees the context
  (H10: the worker that takes tasks during a decode declines everything but
  metrics and the slot listing). With the option on, a task that needs a
  context creates it; a
  creation that fails is answered `503 unavailable_error` and the server
  stays up (H1: upstream's sleep aborts on a failed reload). A slot's release
  reads its task's keep: none, and the context is held; one, and the deadline
  is extended to it — the latest asked wins, a request never shortens
  another's — and the idle callback frees the context once it has passed and
  nothing holds it. `get_meta()`, `n_ctx_slot()` and `use_mixed_batch()` no longer
  read the context: LocalAI calls `get_meta()` on its RPC threads.
- `/metrics`: `context_loaded`, `context_creations_total`,
  `context_creation_seconds_total`; the log says each creation and free with
  its milliseconds.
- A TASK'S RESULTS SAY WHEN THE CONTEXT WAS CREATED FOR IT, and how long that
  took (`server_task_result::t_ctx_create_us`, negative when the context was
  there; `/embedding`'s results carry it as `context_create_ms`): the
  server's own word per request, for whoever counts creations. A client's
  clock cannot tell one — the context may have been kept for another
  client's request (the integration review's finding 1).
- THE CONTEXT CANARY (operator, 2026-11-11: "Add the canary; it must be
  possible to disable it once we have the confidence that the architecture is
  sound"). On by default with the option, `--no-context-canary` to switch it
  off. The load records the output its context gives for a fixed short input
  (the pooled embedding with `--embedding`, the last token's logits
  otherwise; at most 64 tokens), computed twice — the load is refused if the
  two differ. Every context created later computes it again, after its
  warmup and before any task runs on it: not the same values bit for bit,
  and the context is discarded, its request answered `503 unavailable_error`
  ("context canary failed: …", retryable). `common_init_result::
  canary_record` / `canary_check`; `/metrics`: `context_canary`,
  `context_canary_failures_total`; the creation's time includes its pass.
  After the lora adapters are set the next context records it again.
- Refused at load with speculative decoding or a multimodal projector (H8).
  Upstream's sleeping state is untouched and can be used with it.

**LocalAI** (`grpc-server.cpp`, `context_on_demand.h` and its test):
- model options `context_on_demand` and `keep_context_ms:N`; `Predict`,
  `PredictStream` and `Embedding` give their tasks the keep of the request's
  `metadata` (`PredictOptions.Metadata`, which LocalAI's HTTP layer already
  fills from the request's `metadata` map). The fields are DETECTED, so the
  file still builds against pins and forks without the feature.
- the SEVEN reads of `ctx_server.get_llama_context()` are gone (H4):
  `parse_options`, `TokenizeString` and `Detokenize` take the vocab,
  `Embedding`'s layout the pooling type read ONCE at the load (one
  `get_meta()` a load, none a text). The VeGa wrapper calls
  `TokenizeString` for every input, while the model is idle.
- `Embedding` SAYS BESIDE ITS ANSWER whether the context was created for that
  call: trailing metadata `llama-context-created-us` — the creation's
  microseconds, `-1` when the context was there, nothing for a model loaded
  without the option. No change to backend.proto. The VeGa wrapper counts
  on it (vega.md §2, `kind: keeps`) and the calibration gate admits an
  on-demand run only on creations counted that way.
- the model option `context_canary` (on with `context_on_demand`;
  `context_canary:false` switches the canary off — in the run's stated
  options), and a canary failure SAID BESIDE THE ERROR: trailing metadata
  `llama-context-canary: failed` on `Embedding`'s `INTERNAL`, so a caller
  tells a discarded context from memory that could not be had.
- a refused creation reaches LocalAI's client as gRPC `INTERNAL`, HTTP 500
  with the message "the context cannot be created now…": for Louise a
  deferral (07 §7.8: a busy encoder — 408, 429, a 5xx — defers the batch),
  never an error on a document.

**Checked on the CPU, 2026-11-11** (llama.cpp `ddf48f8` + the series + P11;
random-weight `llama` and `qwen3` models written by `tests/test-llama-archs -o`
— huggingface.co is not reachable from the session that wrote it, so the
presets the tests name were pointed at them):
- `test_context_on_demand.py`, 21 of 21 (the 21st with the `init_stop` fix:
  the same rerank scores across contexts for a model whose load stops at the
  reranking check — it fails before the fix; two added with the review's
  fixes: a result says when the context was created for it — one result of
  a batch, none without the option; five with the canary: each created
  context held to it, for a completion and an embedding model; a context
  made to fail it discarded, the request refused with 503 and no context
  left, the next one served; the canary switched off; none without the
  option): the load's context freed when idle
  and none created by `/health`, `/props`, `/models`, `/slots`, `/tokenize`;
  one creation per request with a keep of 0; a request WITHOUT a keep leaves
  the context loaded, cached prompt included, until one that carries a keep
  is answered; a keep from the request (a completion and a chat completion)
  and from the server; a keep of 0 that does not shorten another's; the same completion
  and the same embedding, value for value, across creations; the cached
  prompt reused within a keep and gone after it; a creation refused with 503,
  then served; eight concurrent requests; together with `--sleep-idle-seconds`.
- With the option OFF the existing suites give the same results before and
  after the patch (nine suites, 131 tests: the same 83 passes and the same 46
  failures — the failures are the stand-in models' content, on both).
- LocalAI's `grpc-server` built from the tree the candidate series produces
  and driven over gRPC as LocalAI drives it, 32 of 32 (18 at first; 5 on the
  trailer, first recorded here as six and the total as 25 — miscounted; 7 on
  the canary; 2 on a generation request's keep of 0 — a model WITHOUT the
  option answers the same with the key and frees nothing, one with it gives
  its context back at once: what chat sends, app/llm.py). The trailer: the microseconds of a creation, `-1` inside
  another's keep and after a request without one, twenty requests with a
  keep of 0 saying nineteen creations (the first rode the held context) as
  the backend's log counts them, nothing without the option. The canary:
  recorded at the load and reproduced by every context created; a context
  made to fail it discarded, the call refused `INTERNAL` with the trailer,
  the backend up, the next call served with the same vector; a creation
  refused for memory carrying no such trailer; `context_canary:false`
  recording and checking nothing. Among the first: the same
  vector, byte for byte, WITH AND WITHOUT the option; a request without a
  keep, and one whose keep is no number, leaving the context loaded; token ids (P2) embedded
  on demand; `TokenizeString` and `Detokenize` answered while idle with no
  creation; a creation made to fail, then the next request served.
  Rebuilt with the `init_stop` fix: 32 of 32 again.
- ONE backend with P10 and P11 (`staged/nemotron-rerank-context-on-demand.series`,
  for the reranker on demand — operator, 2026-11-11: "Reranker option A"):
  `git am` of the two staged patches on the series gives the tree built;
  the same 32 of 32, and 8 of 8 on `Rerank` with the model's own keep
  (`context_on_demand`, `context_canary:true`, `keep_context_ms:500`): the
  canary of a rank-pooling model recorded; a rerank creates the context, the
  next ones within the keep find it, the same scores as without the option;
  the context back half a second after the last; a canary failure refused
  and the next rerank served.
- A creation on the CPU, tiny model: 1.4–1.8 ms; a free: 0.03–0.09 ms. These
  say nothing about the B70: the brief's §2 is still to be measured.

**NOT checked, and why:** nothing on a card (the node's, on the operator's
word); `bert` (no random-weight writer for it — the embedding checks ran on
`qwen3` with `--pooling last`, the shape of our Qwen3 encoder); the REAL
reranker (the `Rerank` checks ran on a stand-in whose scores mean nothing:
the card's to say, brief "For München" step 6); encoder-decoder (P3/P4: to check on the card with
MADLAD's test copy).

**Card gates before it moves into the series** (staged/README.md; the brief
§2 and §7).

## Patches retired into the base

*A patch leaves the series when upstream merges it (above). Its entry stays
here, with its verification, for the record.*

### P7. llama.cpp (ggml-sycl, dmmv.cpp): upstream's dmmv cleanup, carried ahead of the pin — RETIRED 2026-11-21

*Retired with the move to LocalAI v5.0.1 (llama.cpp `78e0529`, which carries
upstream's 5e18eb02); `0007-llama-cpp-sycl-dmmv-cleanup.patch` deleted from
the series. What follows is the entry as it stood.*

*Base: master `16acc89` (llama.cpp `ddf48f8`), after P1–P6 (independent of
them: dmmv.cpp only; P5 is in ggml-sycl.cpp). File:
`deploy/localai/patches/0007-llama-cpp-sycl-dmmv-cleanup.patch`, which adds
`backend/cpp/llama-cpp/patches/0009-sycl-dmmv-cleanup.patch`: upstream's
commit `5e18eb021b021a133cfa4d9ac84dab644bc24318` (ggml-org/llama.cpp#31325,
merged 2026-11-02, after our pin) byte for byte in its changed lines.*

**What.** "sycl: dmmv code cleanup": the q8_0 reorder dmmv folded into the
generic template and the non-K launchers templated, +41/−411, "a no-op for
performance" upstream.

**Why it is carried.** The operator, 2026-11-07: "Add the patch 1 as well",
built into the same engine as the B70 AOT build and the pinned Intel stack,
so that every space is gated once rather than twice.

**Risk.** Quantized dmmv bytes can move: the chat models' decode. A gate
that is not exact on that engine has three candidate causes (AOT, the stack,
P7), and attributing it costs a bisect build.

**Upstream.** Already upstream. It drops out of the series when LocalAI's
llama.cpp pin passes `5e18eb02`.
---

## Nice to have for the next round of backend patches

Not planned, and no work until a round of backend patches is opened. Each
entry says what it is, what it would cost, and what it replaces.

### N1. Qwen3-TTS VoiceDesign: `/v1/audio/speech` honours `instructions`

*Operator, 2026-11-17, through München: "Voice llama.cpp patch tell
architecture to write it down in the nice to have for the next round of
backend patches".*

- **What.** Upstream's `server-chat.cpp:810` ignores the `instructions`
  field of `/v1/audio/speech`. The patch would pass it into Qwen3-TTS's
  VoiceDesign prompt template, so a voice described in words is spoken by
  our own llama.cpp backend, the one engine every model runs on (the
  operator's A, 2026-11-02).
- **Cost.** About 1–3 days, by München's estimate, plus the card gates
  every patch passes before it moves into the series (staged/README.md).
- **The alternative, rejected.** Importing qwentts.cpp: about 30k lines with
  a ggml of its own (München's review).
- **What runs today.** LocalAI's gallery backend
  `qwen3-tts-cpp-1.7b-voicedesign` (MIT, 2.2 GB, on the B70) honours
  `instructions`, at about 2–6 s a sentence (München's tests, 2026-11-17).
  The operator makes the voices himself; the test voice samples were
  deleted.

## Requests

Searched on 2026-10-02 and 2026-10-03: nobody has asked for R1, R2, R3 or R5.
The nearest hit for R3 is #4576 (CORS and API key, closed), a different
problem. For R5, #11022 (closed) made the Ollama-compatible routes honour
the hardware-aware context clamp: the opposite wish.

### R1. Name the instance that answered

On one node a model has one backend process, so the model pool can learn which
process answered by checking its (pid, start time) from `/system` before
and after the request. A response header naming the instance would make
that a single read, and it would stay exact in distributed mode, where a
model has replicas.

### R2. A batched `Embedding` call

`/v1/embeddings` calls the backend once per input, in sequence
(`core/http/endpoints/openai/embeddings.go` L112). llama.cpp's own server
embeds a batch in one pass. File this only if the node's benchmark (03
§3.3, tokens per second) measures a loss against llama-server, which is
model-pool-over-localai.md@c572c3b §8.2.

### R3. API keys from a file

Keys come only from `LOCALAI_API_KEY` or `--api-keys`
(`core/cli/run.go` L78). Our invariant 8 keeps tokens out of environment
values. That matters only if LocalAI's own UI is published: inside the
the model pool image LocalAI listens on loopback with no key, and the model pool's front
guards the admin routes.

### R4. The scheduling the model pool gives up

What the model pool did that LocalAI does not, proposed as opt-in changes to the
model YAML and its defaults in `model-pool-prior-art.md`@c572c3b
§7:
- admission by declared memory per device, with a reserve;
- priority eviction that checks the freed memory is enough;
- a refusal (503 + `Retry-After`) instead of a load over the limit (B7);
- eviction aware of other processes' memory (B4);
- restoring preloaded models after a quiet period.

To be filed as one issue, next to #5352.

### R5. A strict mode for load options

LocalAI fills in any option a model's YAML leaves unset. The values come
from:
- the GGUF (`context_fit.go` L106);
- the detected VRAM, read again at every load, with a failed reading
  taken as 0 (`hardware_defaults.go` L287, `xsysinfo/gpu.go` L170);
- the app-wide runtime settings `threads`, `context_size` and
  `vram_budget` (`runtime_settings_registry.go` L222–L246);
- a RANDOM seed at every load when `parameters.seed` is unset or -1
  (`core/backend/options.go`, `getSeed`). The model pool's smoke test saw each
  reload become another run: "Seed 551695963 → 1045691491".

A setting changed in the UI therefore changes how models already
configured run, at their next load. `LOCALAI_DISABLE_HARDWARE_DEFAULTS`
and `LOCALAI_DISABLE_GUESSING` switch off two of these sources but not
the app-wide settings. Ask for either:
- a strict mode, in which an unset field stays at the backend's own
  default; or
- a per-option record of where each value in `load_options` came from
  (P1).

Until then the model pool's models pin every such field
(`model-pool-over-localai.md`@c572c3b §10).

### R6. The readiness poll costs every cold load two seconds

Found 2026-11-24 from a load timed line by line on the node
(`models-beyond-the-card.md` §5.2): the llama.cpp backend prints "Server
listening" and LocalAI's `LoadModel` reaches it 2.01–2.07 s later, the same
for a 2.5 GiB encoder and a 6 GiB translator. Read at `v5.0.1`, and the same
on master that day:
- `spawnGRPCModel` (`pkg/model/initializers.go` L190–L203) starts the
  process and loops: `HealthCheck`, and if it is not alive,
  `time.Sleep(grpcAttemptsDelay × 1 s)`, at most `grpcAttempts` times;
- the defaults are 20 attempts and a delay of 2 (`pkg/model/loader_options.go`
  L161–L162); a model's YAML may set them, `grpc: attempts` and
  `attempts_sleep_time` (`core/config/model_config.go` L1783–L1784), in whole
  seconds, and 0 reads as unset, so 1 s is the least a file can ask;
- the first check runs the instant the process is spawned. The client is
  `grpc.NewClient` (`pkg/grpc/client.go` L83) and the call does not wait for
  the connection, so a refused loopback connection fails at once: the first
  check never succeeds, and the second comes a full delay later.

So a backend that listens after 50 ms is found after 2 s. LocalAI's own
documentation presents the two keys as the window for slow backends
(attempts × sleep ≈ 40 s); the delay is also the resolution with which a
fast one is noticed. That the whole 2.0 s is this sleep is inferred from the
source; a copy with `attempts_sleep_time: 1` would make it a reading (the
gap falls to about 1 s, or it is not the poll).

The patch to come, in `spawnGRPCModel` alone: the wait between two checks
starts at a few tens of milliseconds and doubles up to the configured delay,
inside the same window (attempts × delay) — exponential backoff, no new
setting, no YAML key. gRPC's own wait-for-ready was looked at and does not do
it alone: its reconnect backoff starts at 1 s. Not searched upstream yet.
Decided (operator, 2026-11-24, relayed by München): "Patch localAI when
clockwork is implemented" — built and swapped with the parking work's
LocalAI changes (`models-beyond-the-card.md` §6, step 2), one server
rebuild, and no YAML key meanwhile.

---

## Not filed: one compile-cache directory for every backend

*Operator, 2026-10-02: not needed — each model is already cached where it
is.* Nobody has requested it either (searched 2026-10-02; the nearest,
#2034, shows optimum-intel's per-model default at work:
`Setting OpenVINO CACHE_DIR to /models/models--…/snapshots/…/model_cache`).

The per-model caches cover it, with two gaps a reader of this file should
know, neither of which a global setting is needed to close:
- **OpenVINO embedding models are never cached** — B2, which is to be filed:
  fixed, they get the same per-model cache as the rest.
- **A read-only models directory** (as the model pool mounts `/models`) cannot hold
  optimum-intel's `model_cache`; there the cache must be pointed elsewhere,
  as the model pool does per model (`OV_CACHE_DIR`, model-pool.md@c572c3b §20).

Intel's driver cache (`NEO_CACHE_DIR`, default `$HOME/.cache`) lasts as long
as the container, which is not a gap worth naming (operator, 2026-10-02):
the kernels compile once per container, in seconds, and a container lives
through many loads.
