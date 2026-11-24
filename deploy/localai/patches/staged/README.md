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

| patch | what | gates before it moves up |
|---|---|---|
| `0009-llama-cpp-omnivoice.patch` | OmniVoice on llama.cpp's own audio generation (docs/notes/omnivoice-on-our-backend.md §7–§8), on LocalAI v5.0.1 after series 0001–0008: the architecture `omnivoice` in libllama, the projector type in libmtmd, the pipeline in mtmd-helper, omnivoice.cpp's host rules vendored. Speech from text, in a voice cloned from a clip, or in a described voice | CPU gate against omnivoice.cpp **passed 2026-11-24**, cloning and described voices included (`tools/measure/omnivoice_port_gate.py`; note §8–§9). Before the series, the card: the same gate on the B70, phase 0's three clips against today's backend by ear and by time, memory at rest and at work |
| `0010-llama-cpp-tts-voice-modes.patch` | LocalAI's llama.cpp backend, after 0009: its TTS call takes a voice profile's transcript (`ref_text`), a described voice (`voice` that is no file, or `instructions`) or no voice, for a model that clones and has a "tts" template; a probe for the template in `prepare.sh` (docs/notes/omnivoice-on-our-backend.md §10) | its pure half: `tts_request_options_test.cpp`, 15 cases pass. Its `grpc-server.cpp` half is compiled with the backend only: the test engine's build on the node, then Louise's own speech request against it (§10, steps 1 and 4) |
| `0007-llama-cpp-encoder-decoder-store.patch` | stage 2 (on 0005): the store is one F32 tensor on the device, `(n_seq_max + 1)` regions of `n_ubatch` rows; the encoder graph writes its region (`ggml_set_rows`, `init_encode`), committed after the compute; the decoder gathers (`ggml_get_rows`); no host copies | the same run as 0005's; `test-backend-ops -o SET_ROWS,GET_ROWS` on the node: **727/727 OK (2026-10-28)** |
| `0006-llama-cpp-encoder-decoder-memory.patch` | stage 1 of docs/notes/llama-cpp-encoder-decoder.md: the encoder output kept per sequence (`llama_memory_enc_dec`), so a slot is encoded while others decode; on top of P3 and P4 | on the card, the f32-cache MADLAD backend rebuilt with it: T3 (same group twice), in-wave vs alone (bar 90 %), a lone request against P4's bytes, seconds per question; then the retrieval comparison |
