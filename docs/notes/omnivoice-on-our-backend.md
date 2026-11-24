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
| **D. A port into llama.cpp** | OmniVoice written into llama.cpp's own audio generation | the cleanest end state, upstreamable | 13,800 lines to port, and MaskGIT's parallel decoding does not fit the server's token-by-token speech slots without new plumbing; nobody has proposed it upstream (§5) |
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

## 5. What D would bring, and where upstream stands (2026-11-24)

Checked on llama.cpp's tracker: **no issue or pull request proposes
OmniVoice.** What exists: the audio-output plan in libmtmd (#21956, by
ngxson, completed 2026-11-06 with Chatterbox's support), and the primitives
OmniVoice needs, upstreamed by omnivoice.cpp's own author
(ServeurpersoCom): COL2IM_1D on the CPU (#24206), CUDA and SYCL (#25264),
the fused snake activation on CUDA and Vulkan (#22667, #22855). The pieces
are in; the model is not, and its author serves it from his own
`tts-server`.

What D brings that A, B and C do not:
- one backend with NO LocalAI patch: LocalAI's llama.cpp backend already
  sends a speech call to llama.cpp's native audio generation, so an
  OmniVoice model would load there like Qwen3-TTS does;
- the same voice: OmniVoice itself, its 646 languages, its cloning and voice
  design (C changes the model);
- if upstream takes it, nothing to carry at all — it follows every bump;
- speech under the same server as everything else: streaming, the
  OpenAI-compatible speech route, the engine's load check and switches in
  one place.

What it does not bring: memory (one process per model either way), speed
(the same kernels), or anything the listener hears.

What it costs: omnivoice.cpp's 13,800 lines re-expressed in libmtmd — the
bidirectional backbone with MaskGIT's 32-step parallel decoding, which the
server's speech slots (built for token-by-token models) have no loop for;
the DAC decoder; HuBERT, the semantic encoder and the DAC encoder for
cloning; the duration estimator and long-form chunking; a GGUF layout of
llama.cpp's own, so new model files; a parity suite against omnivoice.cpp
(the comparison of §2 finding 2, on the card). Until upstream merges it, it
is a carried llama.cpp patch far larger than any in the series (the
nemotron reranker's port is 190 lines). And upstream's rules put the review
on the submitter: every line explained by a person, no AI-written
description — as for the context-on-demand material.

So D is worth it only as an upstream contribution, and the person best
placed to make it is omnivoice.cpp's author, who already lands its
primitives there. The cheap move is to ask him (an issue on omnivoice.cpp
or llama.cpp: "OmniVoice in libmtmd?") and take A meanwhile; D done here
alone would be the most expensive way to remove one line from LocalAI's
backend list.

## 6. The operator chose D (2026-11-24: "Go with option D")

OmniVoice is ported into llama.cpp's own audio generation, carried as a
patch of the series until upstream has it. The work, in stages, each with
its gate:

1. **Design**: how the model maps onto libmtmd — the backbone, MaskGIT's
   parallel decoding, the DAC decoder, the cloning encoders, the duration
   estimate, the model files. Written here before code.
2. **Synthesis from text** in the engine's tree (llama.cpp 78e0529 with the
   series' patches), built on the CPU: `llama-tts` writes a WAV.
3. **The parity gate**: the same model files, text, seed and steps through
   the port and through omnivoice.cpp built on the same ggml — the audio
   compared sample by sample. Bar: identical, or one-unit differences
   explained; byte-identical across runs. Nothing goes further until it
   holds. *(Redrawn in §7.4: the codes are chaotic under rounding, the
   comparison is step by step. Passed, §8.)*
4. **Cloning, voice design, long texts; the server's speech route.**
5. **The series**: the patch, its tests, its entry in localai-bugs.md, the
   model file for LocalAI's llama.cpp backend; then the card, by München:
   phase 0's three clips on the B70 against today's backend, speech cut
   over, the omnivoice backend retired.

Until stage 5's card checks pass, speech stays on today's backend: nothing
on the node changes.

## 7. The design (2026-11-24)

*Read: k2-fsa/OmniVoice 4d5ed0f (the Python), transformers 5.24.0,
omnivoice.cpp a650381, llama.cpp 78e0529 with the series. MEASURED means
run in this session's container on the CPU: omnivoice.cpp built on the
engine's ggml, the node's two Q8_0 files, the Italian sentence of §2, seed
0, 32 steps, 168 frames.*

### 7.1 A finding first: today's backend does not mask the way the model does

OmniVoice's code builds a BOOLEAN attention mask for its two rows
(`omnivoice/models/omnivoice.py` 1329–1347): the conditional row all true;
the unconditional row true over its target frames, plus the diagonal of its
padding. transformers hands a four-dimensional mask to the attention
function untouched (`masking_utils.py` 817). The default function (SDPA)
reads a boolean as may-attend / may-not. The "eager" function ADDS it to the
scores (`models/qwen3/modeling_qwen3.py` 190–192): true is +1, false is 0,
nothing is blocked. omnivoice.cpp was validated against eager
(`tests/debug-tts-cossim.py` 284; `docs/ARCHITECTURE.md`, "eager
attention") and reproduces the addition (`src/pipeline-tts.cpp` 134–150,
whose comment says the model was trained that way). It was not: training
masks by document (flex attention, `omnivoice.py` 503–518), the published
config names no attention function (so SDPA), and the authors' own fast
path packs the two rows with "no pad compute"
(`omnivoice_flashinfer.py`). A diagonal on the padding only has a purpose
under a mask that blocks.

So in today's backend every target frame of the unconditional half also
attends the padding — the text embedding of id 1024, repeated — at e⁻¹ of a
real position's weight. The conditional half is not affected.

MEASURED, with a local switch in omnivoice.cpp that blocks instead of
adding: first-step unconditional logits move by up to 2.07, 432 of the
1,344 first-step predictions change, 1,298 of the 1,344 final codes change,
the audio is 6.33 s against 6.63 s. A different reading of the sentence,
not a rounding matter.

| | pros | cons |
|---|---|---|
| **the model's own masking** (chosen) | the model as trained and as its authors run it; the 28 layers become an ordinary bidirectional Qwen3 on libllama — the code our engine already qualifies (P5, flash attention), nothing re-implemented; the unconditional half is the target frames alone, so less to compute per step | the port's audio is not today's backend's: on the card the comparison is by ear and by measure, not sample by sample (§7.4 says why that was never available anyway) |
| today's addition, copied | the same decoding rule as today's backend | a known deviation from the model, kept on purpose; libllama cannot add a bias to its attention, so the 28 layers would have to be rebuilt as a graph inside libmtmd — what upstream's rules forbid, and a second Qwen3 to maintain |
| the status quo | nothing to do | the deviation stays, with all of §1 |

If the operator says nothing, the port uses the model's own masking.
Reporting the finding to omnivoice.cpp's author is the operator's call (an
issue on a public tracker).

### 7.2 The shape

| piece | where | how |
|---|---|---|
| the language model: tokenizer, text embeddings, 28 Qwen3 layers | libllama, a new architecture `omnivoice` (`src/models/omnivoice.cpp`, 100 lines): Qwen3's tensors read in both directions, no KV cache — as upstream did for `dream`, `kokoro`, `qwen3tts` | the `-m` file |
| audio code embeddings, audio heads, RVQ, DAC decoder; for cloning HuBERT, the semantic encoder, the DAC encoder | libmtmd, the mmproj under `a.gen.`; graphs in `tools/mtmd/models/omnivoice.cpp`, transplanted from omnivoice.cpp (MIT) | the mmproj file |
| the 32 steps: guidance, scoring, Gumbel noise (Philox), the schedule; text rules, duration estimate, long texts, post-processing | host code: one pipeline class in `mtmd-helper-gen.cpp`, shaped like Kokoro's (all the work in `step_prompt`, `step_gen` a no-op) | — |
| LocalAI | nothing to patch: `model:` and `mmproj:` in a model file, the TTS call is already there | — |

A step is one `llama_process` of two sequences — the conditional one (text,
reference codes, target) and the unconditional one (the target alone,
positions from 0) — on a context the pipeline owns, so the server's batch
size never bounds a sentence; the hidden states of the target rows go
through the audio heads as one mmproj graph; the rest is host arithmetic.

No vocabulary head in a step: 151,676 × 1,024 per row would add a third to
each step for logits nobody reads. The architecture computes logits only
for a batch of tokens (one token, once, so the caller's sampler has
something to read — Kokoro's arrangement).

Where the layers run — what was weighed:

| | pros | cons |
|---|---|---|
| **an architecture of its own in libllama** (chosen) | upstream's pattern for speech backbones; our qualified Qwen3 code path; the head skipped | six registration lines in core files, which a rebase can touch |
| the stock `qwen3` architecture as `-m` | no core change | per-token states need pooling "none", and with it libllama always computes the vocabulary head (`build_logits`): +32 % per step |
| the layers inside the mmproj, on a libllama context libmtmd owns (YuE2's way) | stock `qwen3` | the `-m` file would hold a tokenizer and embeddings but no layer, which libllama refuses (`n_layer_all > 0`): a new architecture all the same |
| the layers as a graph in libmtmd | either masking; comparable op for op with omnivoice.cpp | a transformer re-implemented in libmtmd (its README-dev forbids it); a second Qwen3 to keep |

### 7.3 The model files

`omnivoice_repack.py` turns the node's two files into the two llama.cpp
loads, changing names and metadata only: every tensor keeps its type and
its bytes (RULE 1 — the same weights, nothing re-quantised). DONE: 310
tensors in the text model (639,153,568 B), 488 in the mmproj
(306,734,944 B). The text model loads on the engine with the new
architecture and tokenizes the test sentence and the prompt's control
tokens to the same 34 ids as omnivoice.cpp (llama.cpp's own BPE with the
`qwen2` pre-tokenizer against omnivoice.cpp's hand-written one). The
standard alternative — a `conversion/omnivoice.py` from the Hugging Face
checkpoint, what an upstream pull request would need — is not written: it
pays when the port goes upstream.

### 7.4 The gate, corrected: the codes cannot be compared, the steps can

MEASURED: adding +1 to every ALLOWED position of the blocked mask — the
same attention in exact arithmetic — changes 1,147 of the reference's own
1,344 final codes. The decoding is chaotic: early steps are near-ties, one
flipped code changes every later step. So no two implementations give the
same codes, and "identical audio" between the port and omnivoice.cpp is not
a test that can pass or fail honestly. (§2's finding 2 held because that
was the same graph on two builds of the same kernels.) Stage 3's bar
becomes, against omnivoice.cpp a650381 on the engine's ggml with the mask
blocked:

1. **The prompt**: token ids identical.
2. **The layers and the heads, per step, on the same input**: the port
   traces each step (codes before, logits, codes after); omnivoice.cpp is
   fed the port's codes at steps 0, 1, 8, 16 and 31 and its logits compared
   with the port's — cosine and largest difference against the reference's
   own rounding noise (cosine 0.99999987, largest 0.53, measured on the
   conditional row with the +1 above).
3. **The scoring**: one Python scorer (guidance, log-softmax, layer penalty,
   Philox Gumbel noise, the schedule) must turn the REFERENCE's logits into
   the reference's codes at all 32 steps, exactly; then the same scorer
   must turn the PORT's logits into the port's codes at all 32 steps,
   exactly.
4. **The codec**: the reference's final codes through the port's decoder
   and post-processing — the audio identical, or one-unit differences
   explained.
5. **Itself**: two runs of the port byte-identical.

The trace is a debug dump behind an environment variable, as libmtmd's
`MTMD_DEBUG_EMBEDDINGS` is.

## 8. Stages 2 and 3: synthesis from text, built and gated on the CPU (2026-11-24)

**Built**, on the engine's tree (llama.cpp 78e0529 with the series), as
`deploy/localai/patches/staged/0009-llama-cpp-omnivoice.patch` (the
llama.cpp patch inside is `0012-mtmd-omnivoice.patch`): 1,115 lines of our
own in 17 files, and 3,706 lines of omnivoice.cpp's host-side rules carried
UNCHANGED under its MIT notice (`vendor/omnivoice/`: language names, the
duration estimate, chunking, post-processing, the Philox generator) —
"do not reinvent the wheel", and the only way those rules stay bit for bit.
The model files come from `tools/omnivoice_repack.py`; the gate is
`tools/measure/omnivoice_port_gate.py` with omnivoice.cpp carrying
`tools/measure/omnivoice_reference_hooks.patch` (three switches for the
comparison, 163 lines, never shipped).

**The gate (§7.4), MEASURED** — CPU, 4 threads, the Italian sentence of §2,
seed 0, 32 steps, 168 frames, the node's Q8_0 weights:

| | result |
|---|---|
| prompt | the 203 ids identical |
| the layers and the heads, on the same codes | step 0: cosine 0.999999887, largest difference 0.54 — the reference against itself (+1 on the allowed positions): 0.999999869, 0.53. Step 16: 0.99999943 against the reference's own 0.99999930. Step 31: 0.9999951 against 0.9999946. Steps 1 and 8 in between (0.9999998, 0.9999997). At every step measured the port is as far from omnivoice.cpp as omnivoice.cpp is from itself |
| the scoring | omnivoice.cpp's own scoring code, fed the port's logits, gives the port's codes after each of the 32 steps: 0 of 1,344 differ |
| the codec | the port's codes through both decoders: the samples agree to 1.25e-6 (0.04 of a 16-bit unit) except in the last 2,000 of the chunk (83 ms), where they differ by up to 8.5e-4 — see below. The two 16-bit files: same length, 51.5 % of the samples identical, 48.5 % one unit apart, 4 samples two |
| itself | two runs byte-identical |

**PASS.** The rounding noise of this model is large — a relative 5e-4 on the
first step, 3e-3 on the last — in omnivoice.cpp as in the port; that is what
makes the codes incomparable (§7.4), and it will make the card's audio
differ from the CPU's on either program.

**Three differences from omnivoice.cpp, each on purpose:**
1. the masking (§7.1);
2. the end of a chunk: `ConvTranspose1d`'s `output_padding` keeps one more
   COMPUTED sample (PyTorch: "does not actually add zero-padding to
   output"); omnivoice.cpp crops both sides and appends a zero, at the two
   odd strides (5 and 3). The port computes it. It touches the last 83 ms of
   a chunk, which silence removal and the fade then cut or cover;
3. the 16-bit file is rounded (llama.cpp's WAV writer for every model),
   where omnivoice.cpp truncates: the "one unit apart" above.
A control token typed in the text to speak is not read as one (the text is
the user's).

**Speed, CPU only — WITHDRAWN as a comparison, re-measured in §11.** The
first figures here (the sentence in 60 s against omnivoice.cpp's 158 s, "on
the same 4 threads") were not like for like: omnivoice.cpp takes half the
logical cores (2 threads here), the port ran on 4, and some of the
reference's runs shared the machine with my other work (the operator's
question, 2026-11-24; measuring.md §6, "one job on the machine"). Both
programs were built with the same flags (AVX2, no native). The measurement
at equal threads on an idle machine is in §11.

**The server and long texts, MEASURED the same day:** `llama-server`'s
`/v1/audio/speech` returns the tool's file byte for byte for the same text
and seed, and honours `flow_steps` (the number of decoding steps) and
`cfg_scale`; the long text's plan is the reference's line for line — 1,318
frames, 5 chunks of 202 / 134 / 195 / 182 / 126 characters and 316 / 208 /
305 / 284 / 199 frames, the first chunk's 316 frames the voice of the other
four.

## 9. Stage 4: a voice from a clip, a voice from a description (2026-11-24)

Both are in the staged patch (1,582 lines of our own in 21 files with §11's changes; the
vendored rules gain the resampler and the vocabulary of voice descriptions).

**From a clip.** The mmproj gets an audio encoder beside the decoder, as
llama.cpp's other cloning models have (`omnivoice_spkenc`, modelled on Fish
S2's): the codec's encoder as one graph — HuBERT, the semantic conv stack,
the DAC encoder, the 8 residual codebooks — returning one backbone row per
frame. The pipeline prepares the clip as the reference does (raised to 0.1
RMS when quieter, silences trimmed), puts its rows before the target frames
and its transcript before the text, and gives the audio the clip's level.
LocalAI needs nothing new: a request whose `voice` is a file is already
handed over as the clip.

**From a description.** `voice` as words ("female, young adult, british
accent") goes through the text model's "tts" chat template, llama.cpp's
mechanism for named voices, and is held to the vocabulary the model was
trained on (omnivoice.cpp's table, vendored): anything else is refused.

**MEASURED** (CPU, the same weights; the clip is the port's own 6.5 s
Italian sentence with its transcript, the text a second sentence, seed 0):

| | result |
|---|---|
| the clip's codes | 158 of 159 frames give the reference's 8 codes. The other one sits on a near-tie in the first codebook: best against second entry 0.049 apart, where the median over the clip is 9.9 |
| prompt | text part identical with a clip (55 ids, `<\|denoise\|>` included); with a description all 133 ids identical, the description normalised as the reference normalises it |
| first step, logits | with the clip: cosine 0.99999973 (conditional), 0.99999994 (unconditional); with a description: 0.99999976, 0.99999986 |
| the scoring, with the clip | the reference's scoring on the port's logits: 0 of 752 codes differ over 32 steps |
| the audio, with the clip | same length (89,760 samples); outside the chunk's last 5,000 samples, 15 samples two units apart and none more; inside, up to 5 units — §8's second difference, which a clip's ending does not hide under silence |
| the server | `/v1/audio/speech` with `speaker_ref_b64` and `speaker_ref_text`, and with `voice`, gives the tool's files byte for byte; an unknown description is answered 400 |
| speed, CPU | 80.4 s against omnivoice.cpp's 167.1 s, 4 threads each, one job on the machine (§11; the first figures, 77 s on 4 threads against 262 s on 2, were not like for like) |

**Memory between requests.** The context the decoding runs on is created
for a request and freed with its audio (4.1 ms to create on the CPU, the
audio unchanged): at rest the model holds its weights and nothing sized by
the last sentence. What that is in MiB on the B70 is step 4 of §10.

**Not ported, and not needed by the node:** a chunk of a long text delivered
as soon as it is decoded (the audio comes in one piece at the end, as
Kokoro's does). omnivoice.cpp has a streaming path and LocalAI's omnivoice
backend offers it (`TTSStream`), but Louise does not call it: the page sends
an answer one sentence at a time (`app/static/voice.js`) and
`app/speech.py` posts each and waits for the whole clip. It would matter to
a client that sends long texts and streams.

## 10. Stage 5: what LocalAI must pass, and the card (2026-11-24)

**A second staged patch, in LocalAI's own backend code**
(`staged/0010-llama-cpp-tts-voice-modes.patch`, 165 lines with its tests).
Louise's speech request names a LocalAI voice profile
(`localai://voice-profiles/<id>`); LocalAI's HTTP layer turns it into the
clip's path as `voice` and its transcript as `params["ref_text"]`. The
llama.cpp backend's TTS call dropped the transcript, read any `voice` as a
required file, and — at our llama.cpp, which predates per-session voices —
knew no other kind of model. With the patch a model that clones and has a
"tts" template takes a file (with its transcript), a description (`voice`
that is no file, or OpenAI's `instructions`), or nothing: the node's
warm-up call (`app/resident.py`, text alone) is answered in the model's own
voice instead of refused. Qwen3-TTS Base keeps its rule and gains the
transcript. Its pure half is unit-tested here (15 cases pass); the
`grpc-server.cpp` half is compiled only with the backend, on the node.

| | pros | cons |
|---|---|---|
| **this patch** (chosen) | Louise's requests work unchanged; generic — any cloning model gets the transcript; small, with tests, fileable to LocalAI | a second staged patch in `grpc-server.cpp`, the file every rebase re-anchors |
| no LocalAI patch | nothing to carry | cloning without the clip's transcript (a weaker clone and a wrong length estimate), no described voice, the warm-up refused |
| change Louise instead | no LocalAI patch | Louise cannot pass a transcript the backend ignores; it would only hide the refusal |

**On the card — München's, in a window the operator gives there.** Nothing
of this touches the live speech until the last step.

1. **Build** a test engine from the series plus the two staged patches
   (staged/README.md says how: a local `series` with the two lines
   appended), installed BESIDE the live one. The build is also the compile
   check of 0010's `grpc-server.cpp` half.
2. **Model files**: `python3 -I tools/omnivoice_repack.py --gguf-py
   <llama.cpp>/gguf-py omnivoice-base-Q8_0.gguf
   omnivoice-tokenizer-Q8_0.gguf <out>/omnivoice-Q8_0` (seconds; expected
   sizes 639,153,568 and about 306.9 MB). A test model on the test engine:
   `parameters.model` the `-lm` file, `mmproj` the `-mmproj` file,
   `known_usecases: ["tts"]`, `fit:off`, one slot, the env of the node's
   llama.cpp models. A small `context_size` is enough: that context reads
   one token; the decoding runs on a context the pipeline sizes itself.
3. **The gate on the B70** — the same five checks as §8, the tools as they
   ran here: `MTMD_DEBUG_OMNIVOICE=<dir>` in the test model's env for the
   port's trace; omnivoice.cpp a650381 built on the engine's ggml with
   `tools/measure/omnivoice_reference_hooks.patch` for the reference
   (`OV_HARD_MASK=1`); `tools/measure/omnivoice_port_gate.py` for the
   verdict. The rounding noise on the card is its own: the bar is the
   reference against itself THERE (`OV_HARD_MASK=shift`), as here.
4. **Against today's backend**, phase 0's three clips and one sentence in the
   admin's recorded voice through LocalAI's `/v1/audio/speech` exactly as
   Louise sends it (`voice: localai://voice-profiles/<id>`): time to the
   clip, VRAM at rest and at work for both, and that the transcript arrives
   (the first-step prompt in the trace has it). The port's own line per
   request (§11; `verbosity:4` in the model's options, as for the engine's
   other INFO lines) splits that time into the clip's encoding, the
   backbone, the heads, the scoring and the decoder: on the second
   sentence in the same voice the clip's part must read 0.00 s — the model
   on ONE slot — and what the host-side scoring weighs on the card is the
   figure the next optimization would start from.
5. **The operator's ear**: the same sentences from both backends, in the
   recorded voice and in the model's own. This is the one comparison no
   number makes (§7.1): the two do not decode alike.
6. Only then, on the operator's word: the two patches into the series (a new
   engine, gated like any other), the node's speech model repointed, the
   omnivoice backend retired.

## 11. Speed at equal threads, and what was optimized (2026-11-24)

*The operator: "have both the timings been calculated with an unloaded
CPU?" — no (§8). Then: "Can you implement and measure the omnivoice
improvements?"*

**How** (measuring.md §6). This session's machine: CPU only, 4 cores, no
card. One job at a time; each row written with the load before it and the
count of other processes above 5 % of a core (none, every row). Both
programs built with the same flags on the same ggml, the same Q8_0 weights;
omnivoice.cpp with its mask blocked (`OV_HARD_MASK=1`, so both decode the
model's way) and its thread count set (`OV_THREADS`, new in
`tools/measure/omnivoice_reference_hooks.patch`: it takes half the cores
otherwise), the port with `-t N -tb N`; the two alternated. Wall time of the
whole program, the load of the model included.

| | threads | omnivoice.cpp, s | the port, s | ratio |
|---|---|---|---|---|
| a sentence in the model's voice (168 frames, 6.72 s of audio) | 2 | 199.2, 208.1 | 133.8, 146.8 | 1.42–1.49 |
| the same | 4 | 101.0, 109.5 | 82.3, 80.7 | 1.23–1.36 |
| a sentence in a clip's voice (94 frames, 3.76 s; the clip 6.5 s) | 4 | 167.1 | 80.4 | 2.08 |

The ratio is the result, not the seconds: that hour the machine ran about
1.3 times slower than when the withdrawn figures were taken (omnivoice.cpp
on 2 threads then 155–159 s, the port on 4 59.6 s), and a row repeats within
5–10 %. Part of the ratio is counted rather than timed: a step of the port
is the conditional sequence plus the target frames alone — 203 + 168 = 371
rows for the sentence, 308 + 94 = 402 with the clip — where omnivoice.cpp
pads the second sequence to the first: 406 and 616 rows, 1.09 and 1.53
times as many. Every port row gave the gated audio byte for byte, on 2 and
on 4 threads.

**Where the port's time goes** — its own line in the log, new with this
work (`omnivoice: 1 chunk(s), 32 steps: voice clip … s, backbone … s,
audio heads … s, scoring … s, decoder … s`), 4 threads, before any change:

| | the sentence | with the clip |
|---|---|---|
| backbone, 32 steps | 77.7 s (95 %) | 75.1 s (93 %) |
| the clip's encoding | — | 2.5 s |
| audio heads | 1.25 s | 0.62 s |
| scoring, host code | 0.87 s | 0.48 s |
| decoder | 1.55 s | 0.75 s |

On a CPU the backbone is the whole cost, at the rate the engine gives any
0.6B model (about 156 rows a second on 4 threads here), and nothing that
keeps the audio shortens it. **The card will not look like this**: there
the backbone, the heads and the decoder are the card's, and what stays on
the host — the scoring, and the clip's encoding on every sentence — is what
a sentence of two or three seconds would notice. That is where the changes
went, and why the line ships: München's run gives this table on the B70.

**The changes** — each with the audio unchanged byte for byte (the sentence
against the gated file on 2 and 4 threads; the clip's sentence against the
earlier file, through the tool and through `llama-server`):

| change | measured, 4 threads | cost | kept |
|---|---|---|---|
| a slot keeps the last clip, sample for sample, with the rows its encoding gave; a request with the same clip reuses them (Louise sends an answer one sentence per request, each with the same voice profile) | the clip's encoding 2.55 s → 0.00 s on the next request (another clip: 1.35 → 0.00), and by the server's own clock 2.59 s → 2 ms from taking the request to starting its decoding; the clip changed and changed back is encoded again each time, its audio equal to a fresh process's | the clip and its rows held between requests: 1.3 MB for 6.5 s; per slot — with several slots a request may land on one that has not seen the clip (measured: four slots, four encodings). The node's speech model has one | yes |
| the scoring of a step shared by the caller's batch threads (`std::thread`; the helper has no pool and ggml's serves graphs only) | 0.87–0.92 s → 0.44 s a sentence | three threads started per step | yes |
| a slot already decoded is not scored again | 0.44 s → 0.37–0.40 s (few slots are decoded before the last steps) | none | yes |
| states asked for the target rows only, so that the last layer leaves the text and voice rows out | nothing: alternate steps of one run with and without, 3 runs, 48 pairs — 112.37 s against 112.33 s, where a step varies by ±10 % | — | **no, taken out** |

On this CPU the kept ones are worth 0.5 s of a sentence's 80 and 2–2.6 s
more when the clip repeats. Not built: dropping the second sequence when a
request asks for no guidance (`cfg_scale` 1) — it would cut 23–45 % of the
rows of a mode nobody uses.

**What would shorten the backbone changes the audio — the operator's
choice, never a default here:**

| | pros | cons |
|---|---|---|
| 32 steps, guided (today; what the gate passed) | the model's own setting; the audio judged so far | the full time |
| 16 steps (`flow_steps` in the request; no code needed) | MEASURED 39.7 s against 81.8 s, the same sentence, one after the other | different audio (the file differs); its quality is unjudged — an ear's verdict, on the card |
| no guidance (`cfg_scale` 1) | 23–45 % of the rows gone (counted), once built | the model is trained to be guided; unjudged, likely worse |

Recommendation: nothing changes — 32 guided steps — and the 16-step file
joins the sentences the operator listens to at §10 step 5, where the card's
own seconds say whether halving them is worth a hearing. If the operator
says nothing: 32 steps.
