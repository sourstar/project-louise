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

## 4. The measurement

**Read first:** CLAUDE.md, docs/notes/measuring.md with its §6 checklist,
node-runbook.md §1 and §12, and approved-encoders.md "Bitwise
reproducibility, by vendor".

**The rules:**
- Nothing is installed, and the live LocalAI app, Louise and `[speech]` are
  not changed, without the operator's word given in München's own session.
- **Never delete a TrueNAS app.**
- The test runs in a throwaway container, never in the live LocalAI.
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
3. **The iGPU test container.**
   - The same LocalAI version as the live one, with `vulkan-parakeet-cpp`
     installed into it.
   - **Only the iGPU's render node passed**
     (`--device /dev/dri/by-path/pci-0000:13:00.0-render`), so the B70 is
     neither seen nor touched.
   - The same model file mounted read-only, its own port, no Louise wiring.
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
7. **Clean up:**
   - stop and remove the test container;
   - keep the pulled image's digest in the write-up;
   - check free space after.

## 5. After the numbers — for the operator, not this measurement

Questions to put in the one shape (CLAUDE.md, Style), each with its pros
and cons:

- **Switch or not:** the iGPU against the CPU, on time per clip, CPU
  cores freed, identical transcripts, and heat.
- **How to wire it:**
  - (a) the live LocalAI app gets the iGPU's render node, the Vulkan
    backend and the model on it;
  - (b) a second, small LocalAI just for speech on the iGPU.
  - (b) keeps the B70's LocalAI untouched. But Louise sends STT to
    CHAT_URL's server (app/voice.py), so it needs a speech server of its
    own: a code change.
- **Checks before either:**
  - **The resident keeper:** app/resident.py budgets the B70 from
    `/system`'s `size_vram`. A model on the iGPU listed by the same LocalAI
    must not be counted against the B70's budget.
  - **LocalAI's own VRAM figures:** the reclaimer and the watchdog
    (localai-bugs.md B4, B6) see one usage figure. An iGPU in the same
    LocalAI must not confuse it.
  - **The System page's GPU panel** (app/gpu_probe.py) reads the xe driver,
    and says nothing of an amdgpu iGPU.

## 6. Results

*Not measured yet.*

| clip set | engine | warm s/clip (median) | RTF | first call | transcripts = CPU's | CPU use | iGPU busy | memory | Tctl |
|---|---|---|---|---|---|---|---|---|---|
| — | CPU (live) | | | | reference | | — | — | |
| — | Vulkan, iGPU | | | | | | | | |
