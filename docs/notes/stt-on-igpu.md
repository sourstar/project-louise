# Speech to text on the processor's integrated Radeon — the brief

*2026-11-11. The operator: "the CPU has a small integrated GPU Radeon 610M,
since keeping it unused is a waste of silicon, I want to run on it the STT
model currently running in CPU, and see how fast it is. Write down the
instructions for another session." This note is those instructions.
Nothing has been installed, changed or measured. The facts below are read
from the repository and from LocalAI's own files on 2026-11-11, and are
labelled as such.*

## 1. What runs today

- **STT: parakeet, on LocalAI's `parakeet-cpp` backend, on the CPU.**
  - The backend is the CPU build (approved-encoders.md: "parakeet-cpp ships
    the same [runtime] but runs on the CPU (its libparakeet.so links no
    SYCL)").
  - VAD is silero, also on the CPU. Both hold 0 MiB of VRAM and never enter
    the B70's eviction (ask-card-workflow.md).
  - The operator kept parakeet on the CPU when whisper-large-v3-turbo on the
    B70 (+5.9 GB, 1.5–1.9 s per clip) was dropped (STATUS, task "Speech").
- **The model name** lives in the node's `platform.toml` `[speech]
  stt_model`. Read it with `louise node speech show`, never from this note.
- **The path:** the Ask page records a 16 kHz mono 16-bit WAV.
  `POST /v1/audio/transcriptions` (app/transcribe.py) sends it to the VAD,
  then to the STT model on CHAT_URL's LocalAI (app/voice.py `server`). STT
  has no server of its own today.
- **No CPU speed figure for parakeet is recorded in the repository.** The
  session measures its own baseline first (§4, step 2).

## 2. The hardware

- **Processor:** AMD Ryzen 5 7600 (vega.md; Zen 4, 6 cores / 12 threads).
  Its integrated GPU, the operator's "Radeon 610M", is RDNA 2 with 2 compute
  units (gfx1036).
- **The iGPU is at PCI `0000:13:00.0`** (node-runbook.md §1). It shows up
  as one of the `/dev/dri/cardN`. Those numbers swap across boots: always go
  by the PCI path, `/dev/dri/by-path/pci-0000:13:00.0-render`
  (node-runbook.md §12; the 2026-09-22 case in STATUS).
- **Its memory is system RAM:**
  - a fixed carve-out set in the BIOS (`mem_info_vram_total`);
  - plus GTT, shared on demand (`mem_info_gtt_total`).
  - Both are in `/sys/bus/pci/devices/0000:13:00.0/`.
  - A carve-out too small for the model is the first thing that can fail.
    Raising it is a BIOS change, so the operator does it at the machine.
- **It shares the package with the CPU:** the same power and thermal
  budget, and the same DDR5 bandwidth. The CPU reached 90–91 °C under the
  12-core records build (STATUS, 2026-10-26). An iGPU running beside a
  12-core pass may slow that pass, or be slowed by it. §4 measures both.

## 3. The engines available

From LocalAI's backend gallery (`backend/index.yaml`, read 2026-11-11), the
`parakeet-cpp` backend (github.com/mudler/parakeet.cpp, a ggml port of
NeMo Parakeet) has these builds:

| build | image (gallery) | for this iGPU |
|---|---|---|
| `cpu-parakeet-cpp` | default | what runs now |
| `vulkan-parakeet-cpp` | `quay.io/go-skynet/local-ai-backends:latest-gpu-vulkan-parakeet-cpp` | **the first choice**: Mesa's RADV drives RDNA 2 iGPUs, no vendor stack needed |
| `rocm-parakeet-cpp` | the gallery's | gfx1036 is not on ROCm's supported list. `HSA_OVERRIDE_GFX_VERSION=10.3.0` (runs gfx1030 kernels) is a community workaround. Second choice, measured, never assumed |

- `latest-…` is a moving tag: pin the image by digest the moment it is
  pulled, and record it.
- **Reproducibility** (approved-encoders.md, "Bitwise reproducibility, by
  vendor"): Vulkan offers no deterministic mode, so measure, do not assume.
  STT returns text, not vectors, so invariant 12 does not bind it, but
  identical text against the CPU's is the quality check (§4).

## 3.1 What to expect — from published figures (2026-11-11, not measured here)

| | the iGPU (2 CU RDNA 2) | the CPU (Ryzen 5 7600, 6 × Zen 4) |
|---|---|---|
| peak FP32 | 0.49–0.56 TFLOPS (128 shaders × 2 × 1.9–2.2 GHz; notebookcheck, siliconcat) | ≈ 0.86–0.98 TFLOPS (modelled: 32 FLOP/cycle/core with AVX-512 double-pumped, at 4.5–5.1 GHz) |
| peak FP16 | 0.97–1.13 TFLOPS (packed, 2:1) | no FP16 FMA (Zen 4: bf16 dot products only) |
| matrix units | none: RDNA 2 has no cooperative matrix, so ggml-vulkan runs scalar shaders | AVX-512 / VNNI |
| memory | the same DDR5 as the CPU | the same DDR5 |
| power | shares the package's budget | the same budget |

- **parakeet.cpp's own figures** (its benchmarks/BENCHMARK.md):
  - tdt-0.6b-v3 runs at RTFx 29.7 (f32) and 34.4 (q8_0) on 8 threads of a
    Ryzen 9 9950X3D CPU;
  - on an Apple M4 GPU it is 2.7× the M4's own CPU;
  - its authors list ROCm as "not tested by us", and GPU backends are not
    in its CI.
- **The closest iGPU figure for this kind of model:** whisper.cpp 1.8.3
  with Vulkan on a Radeon 680M gave 3–4× its CPU (Phoronix). The 680M is
  RDNA 2 with 12 compute units, six times this iGPU.
- **What that predicts (modelled):**
  - The iGPU will most likely be **slower than the six Zen 4 cores**,
    somewhere around 0.3–0.7× their speed.
  - A 10-second question at the CPU's likely RTFx of 15–30 takes 0.3–0.7 s;
    on the iGPU, roughly 0.5–2 s.
  - Its gain is the CPU it leaves free, and having no competition with a
    12-core pass. That gain is small: the Ask page's microphone is used a
    few times a day.
- **The first call after a load** pays Vulkan's pipeline compilation, which
  can take seconds. A persistent Mesa shader cache (`MESA_SHADER_CACHE_DIR`
  on a volume) makes it a one-time cost. Measure the first call with and
  without the cache.
- **ROCm on gfx1036** runs only through `HSA_OVERRIDE_GFX_VERSION=10.3.0`,
  which ROCm's maintainers call a debugging aid. Reports of MES/kernel
  errors exist. On 2 CUs with no matrix units it has no reason to beat
  Vulkan. Expect it to fail or to match Vulkan, and try it only if Vulkan
  fails.

Sources: notebookcheck.net (Radeon 610M; Radeon Graphics Ryzen 7000),
siliconcat.net (610M), github.com/mudler/parakeet.cpp (README,
benchmarks/BENCHMARK.md), phoronix.com/news/whisper-cpp-1.8.3-12x-perf,
github.com/ggml-org/llama.cpp issue 22992 (RDNA 2: no coopmat) and
discussion 10879 (Vulkan figures), localaimaster.com (the HSA override).

## 4. The measurement

**Read first:** CLAUDE.md, docs/notes/measuring.md with its §6 checklist,
node-runbook.md §1 and §12, and approved-encoders.md "Bitwise
reproducibility, by vendor".

**The rules:**
- **Same instance** (operator, 2026-11-11: "Same instance … no problem
  replacing the CPU instance for testing, this is not a production system
  yet"):
  - the test runs in the node's live LocalAI, with no throwaway container;
  - the live STT may be moved to the iGPU while testing;
  - moving back is one line: the model's `backend:` back to
    `cpu-parakeet-cpp`, or `louise node speech set --stt` back to the CPU
    model.
- The session that runs it starts on the operator's word given in
  München's own session, as every node change does.
- **Never delete a TrueNAS app.**
- The node is paused for the clean numbers.
- Free space is checked before and after.
- Every number is labelled measured, modelled or published.

**Steps:**

1. **Inventory (read-only).**
   - `louise node speech show`: the STT and VAD model names.
   - The STT model's YAML in LocalAI's models directory (`ssd5`,
     node-runbook.md §1): its backend, its file, the file's size and
     sha256.
   - The installed CPU backend's version.
   - The iGPU:
     - `lspci -nnk -s 13:00.0`: is `amdgpu` bound?
     - `dmesg | grep -i amdgpu`: firmware loaded?
     - its render node by path;
     - `mem_info_vram_total` and `mem_info_gtt_total`, against the model's
       size plus its working memory.
2. **The CPU baseline, on the deployed config.**
   - **The clip set:** real Ask-page shapes, 16 kHz mono WAV of 2–15 s in
     it, en, fr and de, about 5 per language, plus one of about 30 s.
   - **Where the clips come from:** the operator's recorded questions where
     they exist and the operator agrees. The TTS's own voice is only a
     stand-in, and the write-up must say so (measuring.md §6, "real
     production shapes").
   - **Per clip:**
     - the warm time and the RTF (real-time factor);
     - the first call after a load;
     - the backend process's CPU use (`docker top`, `pidstat`);
     - the transcript, saved as the reference.
3. **The iGPU in the live LocalAI** (the operator's choice; §5 has the
   reasons). In this order:
   1. **Pin the CPU model first.** Its YAML names `cpu-parakeet-cpp`, not
      the alias `parakeet-cpp`. Every build carries that alias, and
      installing a second build can move what it names (§5). Step 2's
      baseline then stays reproducible.
   2. **The iGPU's render node in the LocalAI app**
      (`/dev/dri/by-path/pci-0000:13:00.0-render`), if the app does not
      pass it already. This restarts LocalAI: the node is paused for it,
      and every model reloads.
   3. **Install `vulkan-parakeet-cpp`** from the backend gallery, and record
      its image digest (the tag is `latest-…`).
   4. **A second model, `<stt model>-igpu`:** the same weights file,
      `backend: vulkan-parakeet-cpp`, and in its `env` the device pinned to
      the Radeon (§5: only RADV's manifest via `VK_DRIVER_FILES`, or
      `PARAKEET_DEVICE` / `GGML_VK_VISIBLE_DEVICES`).
   5. **Louise beside it,** checked before any timing: with the model loaded
      on the iGPU, read `/system` and `louise node resident show`. If the
      resident keeper counts the iGPU model against the B70's budget, or
      LocalAI's own usage figure (the reclaimer, the watchdog; localai-bugs
      B4, B6) moves because of it, that is a defect, fixed before going on
      (CLAUDE.md, "Fix it after is never an option").
   - **From the log, before any timing, confirm:**
     - the device ggml chose IS the Radeon. Mesa's `llvmpipe` is a CPU
       Vulkan device and can be picked silently;
     - the memory allocated.
   - **"Computing on the iGPU" is shown, not assumed** (measuring.md §7):
     - `gpu_busy_percent` of the iGPU rises during a clip;
     - `mem_info_vram_used` / `mem_info_gtt_used` grow by about the model's
       size;
     - the backend's CPU use drops.
   - **If the carve-out is too small:**
     - the operator raises it in the BIOS; or
     - try ggml-vulkan's host-memory preference (`GGML_VK_PREFER_HOST_MEMORY=1`),
       after checking that the backend's ggml carries that variable.
4. **The same clips on the iGPU:**
   - warm time, RTF and first call;
   - the transcripts diffed against step 2's, word by word;
   - CPU use during the run;
   - iGPU busy and memory used;
   - the package temperature (`k10temp` Tctl), and power if readable.
5. **If Vulkan fails or is slow:** the ROCm build with
   `HSA_OVERRIDE_GFX_VERSION=10.3.0`, the same steps, recorded as
   unsupported by AMD.
6. **One labelled contention row** (not the clean number): the iGPU
   transcribing while the worker runs its usual CPU and B70 work, unpaused.
   - Did the clip get slower?
   - Did the worker's rates (its log, its share lines) drop?
   - How hot did the package get?
7. **Afterwards:**
   - Ask the operator: the Ask page's STT stays on the iGPU, or goes back
     to the CPU (§5).
     - To try it for real meanwhile: `louise node speech set --stt
       <stt model>-igpu`.
     - Back to the CPU: the CPU model's name.
   - Record the backend image's digest.
   - Check free space after.

## 5. After the numbers — for the operator, not this measurement

Questions to put in the one shape (CLAUDE.md, Style), each with its pros
and cons:

- **Switch or not:** the iGPU against the CPU, on time per clip, CPU
  cores freed, identical transcripts, and heat.
- **How to wire it — DECIDED: (a), the same instance** (operator,
  2026-11-11).
  - (a) the live LocalAI app gets the iGPU's render node, the Vulkan
    backend and the model on it.
  - (b), not chosen: a second, small LocalAI just for speech on the iGPU.
    It would have kept the B70's LocalAI untouched, but Louise sends STT to
    CHAT_URL's server (app/voice.py), so it would need a speech server of
    its own: a code change.
- **One instance is enough** (LocalAI master, read 2026-11-11). A backend
  runs as its own process with its own `lib/` (`run.sh`).
  - The Vulkan build bundles the Vulkan loader AND Mesa's drivers (RADV,
    ANV, lavapipe), with their dependencies
    (`scripts/build/package-gpu-libs.sh` `package_vulkan_libs`).
  - So the node's Intel (SYCL) LocalAI can run `vulkan-parakeet-cpp` beside
    its SYCL backends, without Mesa in its own image.
  - What (a) takes:
    1. **the iGPU's render node in the LocalAI app**
       (`/dev/dri/by-path/pci-0000:13:00.0-render`), if it is not passed
       already;
    2. **the device pinned to the Radeon.** The bundle carries ANV, which
       sees the B70, and lavapipe, which is the CPU. Load only RADV's
       manifest (`VK_DRIVER_FILES`) in the model's `env`, or name the
       device (`PARAKEET_DEVICE=Vulkan<N>`, `GGML_VK_VISIBLE_DEVICES`), and
       read the chosen device in the log;
    3. **the alias pitfall, first.** Every parakeet build carries the alias
       `parakeet-cpp`. On this node the meta backend resolves to the
       `intel` build, and installing a second build can move what the alias
       names. Before installing `vulkan-parakeet-cpp`, point the live STT
       model's YAML at its concrete backend (`cpu-parakeet-cpp`), so the
       live model cannot move to the iGPU unasked.
- **Checks before either:**
  - **The resident keeper:** app/resident.py budgets the B70 from
    `/system`'s `size_vram`. A model on the iGPU listed by the same LocalAI
    must not be counted against the B70's budget.
  - **LocalAI's own VRAM figures:** the reclaimer and the watchdog
    (localai-bugs.md B4, B6) see one usage figure. An iGPU in the same
    LocalAI must not confuse it.
  - **The System page's GPU panel** (app/gpu_probe.py) reads the xe driver,
    and says nothing of an amdgpu iGPU.

## 7. Transcribing while the reader speaks

*The operator, 2026-11-11: "If the question can be processed in chunks we
don't have to wait the end to start processing and the result is in
realtime".*

**Today** (app/static/voice.js, app/transcribe.py):
- the page records the whole question and stops after `AUTO_STOP_MS` =
  1,500 ms of quiet;
- then it uploads the WAV, and the VAD and the STT run on the whole clip.

So the reader waits 1.5 s, then the VAD and the transcription of
everything they said.

**What already exists** (read 2026-11-11):
- **LocalAI `/v1/audio/transcriptions` with `stream=true`** streams the
  OUTPUT (SSE `transcript.text.delta`). The input is still one whole file,
  so it does not help here.
- **LocalAI's Realtime API** (OpenAI's WebSocket protocol, audio sent with
  `input_audio_buffer.append`; `docs/content/features/openai-realtime.md`):
  - With `streaming.transcription: true` and
    `turn_detection: semantic_vad`, it feeds the microphone audio to a
    cache-aware streaming parakeet model while the reader speaks. It sends
    `conversation.item.input_audio_transcription.delta` live.
  - `semantic_vad` needs an end-of-utterance model. Today that is only
    `parakeet-cpp-realtime_eou_120m-v1`, which is **English only**.
  - Under `server_vad`, transcription happens at the commit, as one batch.
- **parakeet.cpp's streaming models** (its README):
  - `realtime_eou_120m-v1`: English, RNNT, with end-of-utterance events;
  - `nemotron-3.5-asr-streaming-0.6b`: 40+ locales, offline and cache-aware
    streaming, OpenMDW-1.1;
  - `nemotron-labs-unified-asr-0.6b`: 40+ locales, buffered streaming.
  - Streaming costs more per second of audio: **RTFx 3.80** on a 20-core
    Ryzen 9 9950X3D CPU, against about 30 offline (BENCHMARK.md). It runs
    many small passes instead of one.

**Three designs**

| | how | pros | cons |
|---|---|---|---|
| status quo | the whole clip, after 1.5 s of quiet | nothing to build | the reader waits 1.5 s plus the whole clip's transcription |
| **A. Start at the first pause** | At ~300 ms of quiet the page uploads what it has and keeps listening. Louise transcribes while the page waits out the rest of its 1.5 s. If speech resumes, that request is aborted, and the whole clip so far goes again at the next pause. | Same model (tdt-0.6b-v3, multilingual), same endpoint, CPU or iGPU. Most questions are transcribed within that 1.5 s wait (on the CPU, a 10–30 s question at the RTFx §3.1 expects), so the result is there when the recording stops. A change to voice.js only. | Work redone when the reader pauses mid-sentence (a short clip: cheap). A long question is still transcribed whole. On the iGPU the 1.2 s left after the pause is tight beyond ~10 s of speech (§3.1). |
| **B. Segments at pauses** | The page streams the audio (a WebSocket, or ~1 s chunked uploads). Each segment closed by a pause is transcribed at once; at the end only the last segment is left. | Long questions too. It cuts only in silence, so no word is split. Same model and endpoint. | A session per question, segment order and joining, a request per segment. A sentence cut by a pause is transcribed in two halves (casing and punctuation may differ at the join). |
| **C. True streaming** (the Realtime API) | The page streams to Louise, which relays LocalAI's Realtime WebSocket. A cache-aware streaming model decodes as the audio arrives. | Real time by construction; live words in the box while the reader speaks. | Another model (`nemotron-3.5-asr-streaming-0.6b`), to judge against tdt-0.6b-v3 on it, fr and de. The semantic end of turn is English only. Streaming RTFx is 3.8 even on a fast CPU: on the 7600, and above all on the iGPU (0.3–0.7× the CPU, §3.1), it may not keep up with speech. A WebSocket path through Louise's web. |

**Recommendation: A first.**
- It is the smallest change, and it gives the real-time result for
  ordinary questions with the current model, on either engine.
- **B** if long questions still show a wait.
- **C** only if live words on the page are wanted, and only after a
  streaming model passes on it, fr and de, measured on the engine that will
  run it.
- Nothing here is a reader's setting (RULE 2).

**Measurements to add to §4** (the same session):
1. The time from the reader's last word to the transcript in the box,
   today and with A, on the §4 clips, on the CPU and on the iGPU.
2. A streaming model's RTFx on the CPU and on the iGPU
   (`nemotron-3.5-asr-streaming-0.6b`, `parakeet-cli transcribe --stream`
   or the Realtime API). Does it stay faster than speech, with a margin?
3. Its transcripts of the it, en, fr and de clips against tdt-0.6b-v3's.

## 6. Results

*Not measured yet.*

| clip set | engine | warm s/clip (median) | RTF | first call | transcripts = CPU's | CPU use | iGPU busy | memory | Tctl |
|---|---|---|---|---|---|---|---|---|---|
| — | CPU (live) | | | | reference | | — | — | |
| — | Vulkan, iGPU | | | | | | | | |
