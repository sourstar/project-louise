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

**Not rebased yet (2026-11-02).** Both were written on `v4.12.1` (llama.cpp
`c0fd786`). The series moved to LocalAI `16acc89` (llama.cpp `ddf48f8`), where
0006's llama.cpp patch has 4 rejected hunks: each is rebased onto the series
and passes its CPU tests again before its card gates.

| patch | what | gates before it moves up |
|---|---|---|
| `0007-llama-cpp-encoder-decoder-store.patch` | stage 2 (on 0005): the store is one F32 tensor on the device, `(n_seq_max + 1)` regions of `n_ubatch` rows; the encoder graph writes its region (`ggml_set_rows`, `init_encode`), committed after the compute; the decoder gathers (`ggml_get_rows`); no host copies | the same run as 0005's; `test-backend-ops -o SET_ROWS,GET_ROWS` on the node: **727/727 OK (2026-10-28)** |
| `0006-llama-cpp-encoder-decoder-memory.patch` | stage 1 of docs/notes/llama-cpp-encoder-decoder.md: the encoder output kept per sequence (`llama_memory_enc_dec`), so a slot is encoded while others decode; on top of P3 and P4 | on the card, the f32-cache MADLAD backend rebuilt with it: T3 (same group twice), in-wave vs alone (bar 90 %), a lone request against P4's bytes, seconds per question; then the retrieval comparison |
| `0009-llama-cpp-context-on-demand.patch` | P11, the context on demand (docs/notes/idle-context-brief.md): a model loaded with `context_on_demand` keeps its weights, and a request that carries `metadata.keep_context_ms` has its context freed that long after the answer, the next request creating it again; a request that carries none leaves the context loaded, as ever. `Embedding` says beside each answer whether the context was created for that call (trailing metadata `llama-context-created-us`), which the VeGa wrapper counts and the calibration gate admits an on-demand run on. The llama.cpp side is `llama.cpp/0011-server-context-on-demand.patch` (written for upstream, with its server tests and README); built only from `context-on-demand.series` | on a TEST model name, by the node (the brief §2, §7): a creation + free's time per encoder at its live settings and the first decode after it; the memory back to the driver (`GGML_SYCL_MEMTRACE=1` beside the xe fdinfo count); bytes identical across ≥ 300 creations (the gate with keep 0); a 10,000-creation soak; chunks/s within 5 % of the BEFORE row at RAGE's keep. Then the series, on the operator's word, with every space re-gated once |
