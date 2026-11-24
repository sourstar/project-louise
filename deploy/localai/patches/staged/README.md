# Staged patches — built and tested, not yet in `series`

A patch here has passed its CPU tests and waits for its CARD gates before it
joins `series` (CLAUDE.md: a stage ships only after its gate; a rebuilt
backend is a new engine, gated once). `tests/test_localai_patches.py` ignores
this folder: nothing here is applied by `build.sh`, and no release digest
names it. To build a test backend with one: append its path, relative to
`deploy/localai/patches` (`staged/0006-llama-cpp-encoder-decoder-memory.patch`, then `staged/0006-…`),
to a LOCAL, uncommitted copy of `series`, then `build.sh` as usual. When its
gates pass, `git mv` it up one level, add its line to `series`, and rename its
entry in docs/notes/localai-bugs.md from "P8 (staged)" to the next P number (and P9 alike; renumbered 2026-11-06 when the Command 3 parser joined the series as P6, and 2026-11-07 when upstream's dmmv cleanup joined as P7).

**Left for the series, 2026-11-22:** `0008-llama-cpp-nemotron-rerank.patch` (P10) and
`0009-llama-cpp-context-on-demand.patch` (P11) were LIVE on the node's engines since 11-10/11-12
while still staged here — the backend qualification's finding F4 — and are now series 0007 and
0008 on LocalAI v5.0.1; their candidate `.series` files are gone with them.

**Not rebased yet (2026-11-02).** Both were written on `v4.12.1` (llama.cpp
`c0fd786`). The series moved to LocalAI `16acc89` (llama.cpp `ddf48f8`), where
0006's llama.cpp patch has 4 rejected hunks: each is rebased onto the series
and passes its CPU tests again before its card gates.

**A candidate engine, 2026-11-24: `engine-fixes.series`** (operator, relayed by München from the
architecture session: "Do everything, also 721, everything, run unattended" — an engine build with
the pool's refusal and the second signal's exit). The live series, then 0012, 0013 and 0014 below:

    SERIES_FILE=deploy/localai/patches/staged/engine-fixes.series \
      sh deploy/localai/llama-cpp/build.sh sycl_f16 <out dir>      # with the live engine's INTEL_STACK

0012 rides although only 0013 and 0014 were named: its switch defaults to today's behaviour, and the
expert-layout comparison on the 35B then needs no second build and no second gating of every run.
Should 0012 not compile, its line is dropped and the other two build alone. The file goes when the
three have passed their card checks and moved up into `series`.

| patch | what | gates before it moves up |
|---|---|---|
| `0009-llama-cpp-omnivoice.patch` | OmniVoice on llama.cpp's own audio generation (docs/notes/omnivoice-on-our-backend.md §7–§8), on LocalAI v5.0.1 after series 0001–0008: the architecture `omnivoice` in libllama, the projector type in libmtmd, the pipeline in mtmd-helper, omnivoice.cpp's host rules vendored. Speech from text, in a voice cloned from a clip, or in a described voice | CPU gate against omnivoice.cpp **passed 2026-11-24**, cloning and described voices included (`tools/measure/omnivoice_port_gate.py`; note §8–§9). Speed at equal threads and the request's own timing line: note §11. Before the series, the card: the same gate on the B70, phase 0's three clips against today's backend by ear and by time, memory at rest and at work |
| `0010-llama-cpp-tts-voice-modes.patch` | LocalAI's llama.cpp backend, after 0009: its TTS call takes a voice profile's transcript (`ref_text`), a described voice (`voice` that is no file, or `instructions`) or no voice, for a model that clones and has a "tts" template; a probe for the template in `prepare.sh`; `params["seed"]`, `["flow_steps"]` and `["cfg_scale"]` reach the decoding (docs/notes/omnivoice-on-our-backend.md §10) | its pure half: `tts_request_options_test.cpp`, 17 cases pass. Its `grpc-server.cpp` half is compiled with the backend only: the test engine's build on the node, then Louise's own speech request against it (§10, steps 1 and 4) |
| `0011-config-voice-cloning-opt-in-unknown-backend.patch` | LocalAI's SERVER (`core/config`), independent of 0009/0010: a model on a backend LocalAI's capability table does not know — ours, named by its digest — may be given a saved voice profile when its file says `tts.voice_cloning: true`; before, the request was refused 400 by the backend's name, the field never read (localai-bugs.md B17; docs/notes/omnivoice-on-our-backend.md §12). The operator, 2026-11-24: "Patch LocalAI" | LocalAI's own `core/config` tests pass with it on v5.0.1 (the six new cases; two of them fail without the change). It applies on v5.0.1 + P1, the node's server tree, and on the whole series. On the node: the server rebuilt with it, then the test model answers a `localai://voice-profiles/<id>` request with audio, the live speech model still does, a llama.cpp chat model still refuses |
| `0012-llama-cpp-sycl-reorder-experts-switch.patch` | A SWITCH FOR ONE COMPARISON: `GGML_SYCL_REORDER_EXPERTS` (default 1 = today) — with 0 the expert tensors of a mixture-of-experts model keep the layout they were loaded in. It takes the place of `0012-llama-cpp-sycl-usm-by-use.patch` (the 35B paged to the card by use: measured 2026-11-24, the whole model on the card after one answer, a neighbour's load killed — deleted, edc48d6 has it), whose run started long prompts a quarter sooner with the experts not reordered (docs/notes/backend-qualification-2026-11-21.md §3, "THE EIGHT ROWS"). Independent of 0009–0011 | NOT compiled (no SYCL compiler in the cloud session). On the node: the 35B's test copy with the switch 1 then 0, both fixed prompts five times each. It joins the series with a default only if the comparison says so |
| `0013-llama-cpp-sycl-pool-refusal.patch` | A DEFECT OF THE LIVE ENGINE, fixed: when the card refuses memory for an op's temporaries while a request runs, ggml-sycl's pool aborted the whole backend (`UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY` at `ggml_sycl_pool_vmm::alloc`; München, 2026-11-24, a model loading beside the 35B) — against context on demand's own contract, "refused, never an abort". The refusal now fails the one graph (`GGML_STATUS_ALLOC_FAILED`), the server answers that request "Compute error." and lives; Louise reads a 5xx as a deferral (docs/notes/room-on-the-card.md §4; localai-bugs.md B18/P14). Independent of 0009–0012 | NOT compiled (no SYCL compiler in the cloud session). ITS TEST IS ON THE CARD (the note's §4): the device filled, an on-demand model asked — the request refused, the backend's pid unchanged, and the same model answering once the card has room. Page mode is not switched on before it is in the live engine (the architecture session) |
| `0014-llama-cpp-second-signal-exit.patch` | A DEFECT OF THE BACKEND'S MAIN, fixed: a second SIGTERM/SIGINT while the backend cleans up called `exit(1)` inside the signal handler, which can block with the card still held — 3 of 14 unloads behind our wrapper took LocalAI's whole 15 s (München, 2026-11-24; LocalAI signals the process GROUP, the wrapper forwarded). The second signal now leaves through `second_signal.h` (`write(2)`, `std::_Exit(1)`). Our wrapper no longer sends the second signal (#856), so this closes what that leaves open: an engine slower than 2 s to go (localai-bugs.md B19/P15). Independent of 0009–0013 | `second_signal_test.cpp`, in the patch, compiled and run here with `backend/cpp/run-unit-tests.sh`'s flags: a child whose exit handler never returns is gone at once, status 1; through `exit(1)` it is still there a second later. tests/test_localai_staged_second_signal.py builds and runs it from the patch. The backend itself NOT built here. On the node, after an engine build with it: unloads behind the wrapper with `OWN_EXIT_S` set to 0 on a copy — every one fast, the log's second-interrupt line followed by the process's end |
| `0007-llama-cpp-encoder-decoder-store.patch` | stage 2 (on 0005): the store is one F32 tensor on the device, `(n_seq_max + 1)` regions of `n_ubatch` rows; the encoder graph writes its region (`ggml_set_rows`, `init_encode`), committed after the compute; the decoder gathers (`ggml_get_rows`); no host copies | the same run as 0005's; `test-backend-ops -o SET_ROWS,GET_ROWS` on the node: **727/727 OK (2026-10-28)** |
| `0006-llama-cpp-encoder-decoder-memory.patch` | stage 1 of docs/notes/llama-cpp-encoder-decoder.md: the encoder output kept per sequence (`llama_memory_enc_dec`), so a slot is encoded while others decode; on top of P3 and P4 | on the card, the f32-cache MADLAD backend rebuilt with it: T3 (same group twice), in-wave vs alone (bar 90 %), a lone request against P4's bytes, seconds per question; then the retrieval comparison |
