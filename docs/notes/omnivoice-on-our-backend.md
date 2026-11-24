# Omnivoice on our backend — an evaluation (2026-11-24)

*The operator, 2026-11-24: "Evaluate to patch our backend to support
omnivoice, I would like to have only one backend." Nothing was changed on
the node. Sources read: LocalAI v5.0.1's `backend/go/omnivoice-cpp` and
`backend/cpp/llama-cpp`; omnivoice.cpp at the commit LocalAI pins
(`ServeurpersoCom/omnivoice.cpp` a650381, 2026-11-17, MIT) and its ggml fork
(`ServeurpersoCom/ggml` ef4555b); llama.cpp 78e0529, our engine's base. What
is marked TESTED was built and run in this session's container on the CPU;
nothing here ran on the B70.*

## 1. What runs today

Omnivoice is LocalAI's own backend `intel-sycl-f16-omnivoice-cpp`: a Go
wrapper (675 lines with its C glue) around omnivoice.cpp, a C++17 port of
k2-fsa/OmniVoice — a 612 M-parameter Qwen3-0.6B backbone decoded by MaskGIT
in 32 steps, a DAC codec, HuBERT and a semantic encoder for voice cloning,
646 languages, 13,800 lines. It links its OWN ggml (a fork), is built by
LocalAI's CI, and its version on the node is whatever image was installed
("latest"): outside our series, our Intel stack, our qualification, and
inside LocalAI's automatic upgrades until München switched those off
(backend-qualification-2026-11-21.md, F2).

## 2. Findings

1. **omnivoice.cpp builds against our engine's ggml, unchanged.** Its
   CMake takes `-DGGML_SOURCE_DIR=<tree>`; pointed at llama.cpp 78e0529's
   `ggml/` it configures and compiles with no error and no edit. TESTED.
2. **It gives the same audio that way.** The node's own model files
   (`omnivoice-base-Q8_0.gguf` 656,395,008 B, `omnivoice-tokenizer-Q8_0.gguf`
   288,889,600 B), one Italian sentence, seed 0, 32 steps, 6.63 s of audio:
   two runs on our ggml are byte-identical; against the build on omnivoice's
   own ggml, 159,052 of 159,120 samples are identical and the other 68
   differ by one unit of 16 bits (SNR 104 dB). TESTED, CPU.
3. **Its ggml fork is our ggml a few days older, as far as SYCL goes.** The
   operation set is identical; `ggml-sycl/` differs by 121 lines, all of
   them upstream changes the fork has not taken yet (the flash-attention
   mask fix #31536, fast division #32213). The fork's own work is in the
   CUDA, CPU, Metal and Vulkan kernels. So on the B70 it loses nothing on
   our tree and gains P5 and those two fixes.
4. **Every operation it uses has a SYCL case in our engine** (COL2IM_1D,
   CONV_2D, GROUP_NORM, NORM, PAD, SIN, ARGMAX, FLASH_ATTN_EXT, RMS_NORM,
   ROPE, the unary and gated activations…), under type conditions; what a
   condition declines runs on the CPU. Read, not run: the card decides.
5. **Our backend already serves speech — for other models.** LocalAI's
   llama.cpp backend implements the TTS and TTSStream calls on llama.cpp's
   native audio generation (libmtmd): Qwen3-TTS (it, en, de, fr, es, pt, ja,
   ko, zh, ru; cloning from a reference clip, closer with its transcript),
   Chatterbox (23 languages; the server does not run its guidance yet),
   Kokoro, LFM3-Audio, Fish Audio S2 Pro, Pocket TTS, ZONOS3 and others.
   OmniVoice is not among them.
6. **One backend does not mean fewer processes.** LocalAI starts one
   process per loaded model whatever the backend, so the ~270 MiB a process
   holds on the card stays. What one backend buys is one engine to build,
   pin, qualify and retire.

## 3. The ways to get there

| | what it is | pros | cons |
|---|---|---|---|
| **A. The same engine, a second package** | LocalAI's omnivoice backend built by OUR build, on our Intel stack, with `GGML_SOURCE_DIR` set to our patched llama.cpp's ggml and omnivoice.cpp pinned in the series file | no code to write or carry — two build arguments; our SYCL fixes and switches reach speech; pinned and qualified with the engine; nothing upgrades behind us; findings 1–3 say it works | LocalAI still lists two backends; a second image to build at each engine change |
| **B. One binary** | a LocalAI patch that links omnivoice.cpp's library into the llama.cpp backend and sends an OmniVoice load and its TTS calls to it | literally one backend package; everything of A | a carried patch of roughly 500–700 lines, with hooks in `grpc-server.cpp`'s load and speech paths — the file every rebase so far has had to re-anchor; upstream LocalAI would not take it (it has a backend for this); no memory saved (finding 6) |
| **C. A native model** | change the speech model to one llama.cpp runs itself — Qwen3-TTS is the candidate | one backend today with NO patch; follows upstream for free | a different voice: a listening comparison in Italian and English, the admin's recorded voice re-done through its cloning, 10 languages instead of 646, its size and speed on the B70 unmeasured |
| **D. A port into llama.cpp** | OmniVoice written into llama.cpp's own audio generation | the cleanest end state, upstreamable | 13,800 lines to port, and MaskGIT's parallel decoding does not fit the server's token-by-token speech slots without new plumbing; not checked whether upstream has it in progress |
| the status quo | LocalAI's image as installed | nothing to do | an engine nobody here builds, pins or qualifies; no P5 |

## 4. Recommendation

A now: it removes what is actually wrong with today's arrangement — an
unpinned, unqualified engine without our fixes — for the cost of a build.
For literally one backend, try C before writing B: a native model costs
nothing to carry, and B is a permanent patch in the most-rebased file for no
gain in memory. D only if upstream wants it.

What A takes on the node, in a window: the omnivoice variant built beside
the engine; phase 0's three clips again (0.79 / 1.12 / 1.27 s, 1,369 MiB),
the audio compared with today's backend clip by clip and with a CPU run of
the same build; the model file's `backend` changed; the old image retired.
What C takes first: Qwen3-TTS (`ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF`)
loaded as a test copy on our engine, the same three texts, the operator's
ear.
