# Image generation with FLUX.1 Krea [dev] — what it costs on the B70

*2026-11-11. The operator asked how much VRAM a Krea image model would take
in the context-sharing environment, then chose **A** ("measure a test copy
on the node") and added: **"write it down for a future separated
session"**. This note is that brief. Nothing has been measured, loaded or
installed. Everything below is a file size, a published figure, or a line
of source code, and is labelled as such.*

## 1. What LocalAI already offers

LocalAI's gallery (`gallery/models/index.yaml`, read 2026-11-11) has two
entries. Both go through `gallery/flux-ggml.yaml`, which sets
`backend: stablediffusion-ggml`, `step: 25`, `cfg_scale: 1`,
`sampler:euler`, and the options `diffusion_model`,
`clip_l_path:clip_l.safetensors`, `t5xxl_path:t5xxl_fp16.safetensors` and
`vae_path:ae.safetensors`.

| file | in which entry | bytes (published, HF) | sha256 (the gallery's) |
|---|---|---|---|
| `flux1-krea-dev-Q4_K_M.gguf` (QuantStack) | `flux.1-krea-dev-ggml` | 6,931,817,760 | `cf199b88…dbdcaf` |
| `flux1-krea-dev-Q8_0.gguf` (markury) | `flux.1-krea-dev-ggml-q8_0` | 12,714,452,256 (QuantStack's Q8_0; markury's to check) | `0d085b1e…761fbd90` |
| `t5xxl_fp16.safetensors` (T5-XXL text encoder) | both | 9,787,841,024 | `6e480b09…9167635` |
| `clip_l.safetensors` (CLIP-L text encoder) | both | 246,144,152 | `660c6f5b…a6cdd` |
| `ae.safetensors` (the VAE, the image decoder) | both | 335,304,388 | `afc8e282…529e38` |

**On the node:** vega.md ("Still open (§6.2)") records
`flux.1-krea-dev-ggml` in the node's LocalAI at **12.7 GB, never loaded**.
12.7 GB is the size of the Q8_0 transformer file, not of the Q4_K_M entry's
files (17.3 GB). So either the node holds the Q8_0 transformer, or only one
file was counted. **First step of the session: list the files on disk** and
their sizes and sha256.

The licence is `flux-1-dev-non-commercial-license` for the weights.

## 2. What it would hold on the card (modelled from the file sizes)

| placement | Q4_K_M | Q8_0 |
|---|---|---|
| everything on the card | 17.3 GB | 23.1 GB |
| T5-XXL and CLIP-L on the CPU (`keep_clip_on_cpu`) | 7.3 GB | 13.1 GB |
| weights in RAM, moved to the card per stage (`offload_params_to_cpu`) | ≈ 0 idle; while running, the stage being computed | the same |

Working memory while an image is generated comes on top of the weights.
It is **published, not measured on Intel cards**: stable-diffusion.cpp's
`docs/performance.md` gives `flux compute buffer size: 650.00 MB(VRAM)`
with flash attention, on CUDA. The VAE decode at 1024×1024 needs more
unless it is tiled.

**Between images, only the weights stay.** In stable-diffusion.cpp
(master, 2026-11-11), `GGMLRunner::compute` ends every run with
`runner_end()`, which calls `free_compute_ctx()` and frees its compute
buffer (`src/core/ggml_runner.cpp`). That is the behaviour of our
`context_on_demand` with a keep of 0 (idle-context-brief.md), built in.
It must be confirmed at the pin the node's backend carries (§4, step 1).

**The card today:** 32.6 GB, ≈ 34 GB resident, ≈ 10 GB of it idle context
(shared-context.md §1). At most ~3.3 GB of that context works in the same
second. So:
- **Q4_K_M with the text encoders on the CPU** fits only after context on
  request is deployed and has given the idle context back.
- **Everything on the card** does not fit beside the current set.
- The preloader keeps every model resident and never unloads (#399, #403).
  An image model would have to be **on-demand only**: loaded when asked,
  which costs its load time per cold start.

## 3. The backend's options (LocalAI master, 2026-11-11)

From `backend/go/stablediffusion-ggml/cpp/gosd.cpp`, the options relevant
here:
- `offload_params_to_cpu`, `keep_clip_on_cpu`, `keep_vae_on_cpu`;
- `diffusion_flash_attn`, `diffusion_conv_direct`, `vae_conv_direct`;
- `params_backend`, `max_vram`, `stream_layers`, `backend`, `n_threads`.

VAE tiling is a per-image setting (`vae_tiling_params`). The
stable-diffusion.cpp pin in that Makefile is
`32d516c0197ed5ca31305b8575e14eb1451fad33`. **The node runs LocalAI
v4.12.1**, whose backend may carry an older pin and fewer options: read
them from the installed backend, never from this list.

## 4. The measurement (choice A) — for München, on the operator's word in München's session

Rules that bind it:
- CLAUDE.md, and docs/notes/measuring.md §6: deployed flags and env, one
  job on the machine (the node paused), warm answers, free space checked
  before and after, numbers labelled.
- A **test copy under its own name** (`cod-test-flux-krea-q4`), never
  wired to Louise.
- Nothing installed, loaded or paused without the operator's word given in
  München's own session.
- GPU measurements go outside the quiet periods (STATUS, the node's
  schedule).

Steps:
1. **Inventory.**
   - The files of `flux.1-krea-dev-ggml` on disk: size, sha256, which
     transformer (§1).
   - Whether the stablediffusion-ggml backend is installed for Intel
     (SYCL), and its stable-diffusion.cpp commit.
   - The options it accepts.
   - At that commit, whether the compute buffer is freed at the end of each
     run (§2).
   - Free space on ssd3.
2. **Install, if needed.** The Intel (SYCL) build of the backend from
   LocalAI's backend gallery, on the operator's word. Record its image
   digest.
3. **The test copy.**
   - Q4_K_M, `keep_clip_on_cpu:true`, `diffusion_flash_attn:true`, 25
     steps, euler, cfg 1.
   - The vendor's mode in its env (`GGML_SYCL_ENABLE_DNN=0`), as for every
     ggml-SYCL model. Invariant 12 covers encoders, but a run whose bytes
     move under it is worth knowing.
4. **Read, per configuration:**
   - `size_vram` after the load, idle;
   - the peak during one 1024×1024 image, sampled every 100 ms;
   - `size_vram` after the image (back to the weights?);
   - the time per image, cold and warm;
   - the T5 time on the CPU and the CPU cores it took;
   - the debug log's `compute buffer size` lines.
5. **Configurations, in this order:**
   1. as in step 3;
   2. with VAE tiling;
   3. 768×768;
   4. `offload_params_to_cpu:true`;
   5. flash attention off (to know what it saves);
   6. Q8_0, only if 1–5 leave room.
6. **Determinism:** the same prompt and seed twice. Are the PNGs the same
   bytes? Record it, don't fix it.
7. **Clean up.** Unload the test copy, delete what step 2 installed only if
   the operator says so, and check free space after.

## 5. Where the results go

- A table in this note (§6, empty until measured), one row per
  configuration.
- One line in STATUS.
- If it fits: the operator decides whether Louise offers image generation
  at all, and where (Ask, an API). That is a product decision, not this
  measurement's. RULE 2 applies to any page that offers it: the reader
  types a prompt and nothing else.

## 6. Results

*Not measured yet.*
