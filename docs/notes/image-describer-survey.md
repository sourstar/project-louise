# Image describer survey — VLMs for the Met Open Access run (2026-11-28)

Web research only; nothing measured on our cards. Context: ~517,000 Met
images, described once by a VLM, the descriptions embedded as text
(image-collections.md §9–10: a "describes" annotation, labelled "described
by <model>"). Card: Intel Arc Pro B70 32 GB, SYCL/Vulkan, llama.cpp libmtmd.

**Every speed and quality number below is someone else's, on other hardware
and other images. "Unconfirmed" means I looked and did not find it.** No
source gives a s/image figure on a B-series Arc for any VLM.

## 1. Findings that decide the shortlist

1. **No detailed-captioning benchmark covers 2026 models together, and none
   covers artworks.** CapArena (ACL Findings 2025) says so itself: "artwork
   and medical images are not yet covered". Its newest open model is
   Qwen2.5-VL-72B. Our own 100-image benchmark (§6) is the only evidence
   that will apply to the Met.
2. **The closest public evidence for "caption as a retrieval surrogate" is
   CaptionQA** (arXiv 2511.21025, 2025-11-26): questions answered from the
   caption alone. Qwen3-VL-30B-A3B leads the open models (Simple prompt,
   87.02 overall), then GLM-4.1V-9B (84.28), Qwen2.5-VL-32B (81.20) and
   InternVL3.5-38B (79.58). It has no art domain, and Gemma was not tested.
3. **llama.cpp support** (mainline `docs/multimodal.md`, read 2026-11-28, and
   ggml-org's HF repositories): Qwen2.5-VL, Qwen3-VL (PR #16780, merged
   2025-10-30, with imrope for SYCL and Vulkan), Gemma 3 and Gemma 4, InternVL
   2.5/3 (not 3.5), Mistral Small 3.1, Pixtral, SmolVLM/SmolVLM2, Kimi-VL,
   GLM-4.6V-Flash, LFM2-VL and Moondream2 are supported. Qwen3.5/3.6 vision
   works with community mmproj files (users run `--mmproj mmproj-F16.gguf`),
   but `multimodal.md` does not list it. **Unsloth: MTP builds do not support
   `--mmproj`**, so the describer must be the non-MTP GGUF.
4. **The SYCL vision path has a recorded gap.** llama.cpp #19543 (opened
   2026-02-12, closed stale 2026-05-11, never fixed): Qwen3-VL's vision
   encoder on an Arc A770 lacked SYCL ops (UPSCALE and others), which fell
   back to CPU. In March only UPSCALE remained, and only "mode=nearest" was
   supported. The encode took about 14 s per image, and a maintainer
   confirmed a "performance gap". Nothing was found for the B70. **Measure
   both SYCL and Vulkan for the projector, and check the log for CPU
   fallback.**
5. **Determinism**: greedy decoding is reproducible only if batching does not
   change the reduction order (arXiv 2506.09501: outputs change with batch
   size even under greedy decoding). Nothing llama.cpp-specific was found.
   Inference, unconfirmed: run with `-np 1` and a fixed `-b/-ub`, keep the
   vendor mode in the env (`GGML_SYCL_ENABLE_DNN=0`, invariant 12), and check
   it with the existing harness (`llama-intel-arc-b70`). Concurrency across
   slots is the obvious throughput lever, and it is exactly the one that may
   break byte reproducibility. It must be measured, not assumed.
6. **Art identification is the main faithfulness risk.** VLMs show a
   popularity bias: up to 34 % higher accuracy on famous buildings than on
   ordinary ones (YearGuessr, arXiv 2512.21337). GPT-4o misattributed
   canonical paintings (arXiv 2409.03521). Title and place of origin were the
   weakest categories on museum objects (Museum-65, arXiv 2412.01370, which
   does not include the Met). So a model will name some famous works
   correctly, misname obscure ones confidently, and the label "identified
   from the model's knowledge" has to be in the prompt's output format, not
   left to chance.

## 2. Candidate describers (open weights, ~1B–35B)

Sizes and licences come from the HF model cards, read 2026-11-28 unless
noted. "GGUF" means llama.cpp mainline with an mmproj.

| Model (date) | Size total / active | Licence | GGUF + mmproj | Description quality evidence | Pros | Cons |
|---|---|---|---|---|---|---|
| **Qwen3.6-35B-A3B** (2026-04) — installed | 35B / 3B (MoE, 256 experts) | Apache-2.0 | community mmproj-F16 (0.90 GB, unsloth); not in `multimodal.md`; not with MTP | MMMU 81.7, RealWorldQA 85.3 (vendor card). No captioning benchmark found | already on disk; strongest general vision scores in the 3B-active class; fast decode for its size (MoE) | biggest memory footprint; thinking model (non-thinking mode needed); the vendor recommends sampling, so greedy is untested; vision path SYCL-unverified |
| Qwen3.6-27B (2026-04-22) | 27B dense | Apache-2.0 | as above (unconfirmed for vision) | vendor tables only | dense, possibly more stable under greedy | about 9× the active parameters of the 35B-A3B, so slower per token |
| Qwen3.5-9B / 4B / 2B (2026-03-02) | 9B / 4B / 2B dense | Apache-2.0 | community mmproj; vision support status as for 3.6 | MMMU-Pro 69.2 (9B), 65.4 (4B) (Artificial Analysis); AA "flags high hallucination rates for the 4B and 9B" | small and fast; same family as the installed model | the hallucination flag is exactly our risk |
| **Qwen3-VL-8B / 4B / 2B / 30B-A3B / 32B** (2025-10) | 2–32B dense; 30B / 3B MoE | Apache-2.0 | **yes**, mainline (PR #16780); ggml-org GGUFs for 2B and 30B-A3B | **CaptionQA: 30B-A3B best open model (87.02)**; trained with dense-caption data (tech report 2511.21631) | the only family with public caption-utility evidence; Instruct models (no thinking); mainline support | SYCL op gap (#19543) recorded on this architecture; superseded by 3.5/3.6 on general scores |
| **Gemma 4 E4B / 12B / 26B-A4B / 31B** (2026; tech report 2607.02770) | 4.5B eff.; 12B; 25.2B / 3.8B; 30.7B | Apache-2.0 | **yes**, mainline (E2B, E4B, 26B-A4B, 31B; 12B "unified" encoder-free, its llama.cpp path is unconfirmed) | MMMU-Pro 73.8 (26B-A4B), 76.9 (31B). **Visual token budget selectable: 70–1120** | token budget is a direct speed/detail knob; MoE 26B-A4B is close to our 35B-A3B in cost; Apache (Gemma 3 was under Gemma terms) | no captioning or hallucination numbers found |
| InternVL3.5-8B / 14B / 38B / 30B-A3B (2025-08) | 1B–38B, MoE 30B-A3B | Apache-2.0 | **no** (llama.cpp lists InternVL 2.5/3 only) | CaptionQA 79.58 (38B); **DiffCap-Bench (2026-05): InternVL3.5 hallucination rate > 50 % vs 20–30 % for others** (difference captioning, a narrow task) | strong general benchmarks | no GGUF path; hallucination signal |
| GLM-4.6V-Flash (2025-12) | 10.3B dense | MIT | **yes** (ggml-org/GLM-4.6V-Flash-GGUF) | predecessor GLM-4.1V-9B was second in CaptionQA (84.28) | permissive; mid-size; caption evidence via its predecessor | zh/en only on the card; 4.6V itself has no caption numbers |
| Kimi-VL-A3B (2025-04/06) | 16.4B / 3B | MIT | yes (ggml-org has the Thinking-2506 GGUF; Instruct unconfirmed) | none found | MoE cheap | older; Thinking variant verbose (more output tokens) |
| MiniCPM-V 4.6 (2026-05) | 1.3B | Apache-2.0 | guides exist; mainline status unconfirmed | vendor: "Qwen3.5-2B-level" on several benchmarks | tiny, fast | edge model; vendor claims only |
| Molmo2-8B / 4B (2025-12) | 8.7B (Qwen3-8B base) | Apache-2.0 | **no GGUF found** | PixMo/Molmo2-Cap training is caption-centred; Molmo 2's report claims captioning "only outperformed by the strongest closed models" (no number seen) | caption-first training data | vLLM/transformers only; the Molmo2-Cap data is video |
| Ministral 3 14B / 8B (2025-12) | 14B dense (0.4B vision) | Apache-2.0 | unconfirmed (Mistral Small 3.1 24B is supported) | none found | multilingual; permissive | no evidence for captioning |
| SmolVLM2-2.2B, Florence-2-large (0.78B, MIT), Phi-4-multimodal (5.6B, MIT) | small | Apache / MIT / MIT | SmolVLM2 yes; Florence-2 no; Phi-4-mm unconfirmed | Florence-2 produces short captions; no detail benchmarks | very fast | too shallow for a description that should carry the image's content; Florence-2 not in llama.cpp |
| Llama 4 Scout, Qwen3.5-122B-A10B, Nemotron-3.5-Super-VL-120B | 109B–122B total | Llama 4 / Apache / other | yes / – / yes | – | – | do not fit 32 GB at a useful quantisation (not checked in detail) |

## 3. Benchmarks and metrics for detailed description

| Benchmark (date) | What it measures | Public data we can run locally | Pros | Cons |
|---|---|---|---|---|
| DOCCI (Google, ECCV 2024) | 15k photos, human descriptions averaging ~136 words, designed to separate near-duplicate images | **yes**, CC BY 4.0, JSONL + 7.1 GB of images, on HF | references are long and visual; near-duplicate groups test specificity | one photographer's everyday scenes, no art |
| IIW — ImageInWords (Google, 2024) | hyper-detailed human descriptions; IIW-400 plus DCI/DOCCI test splits | **yes**, CC BY 4.0 (github google/imageinwords, HF) | best human references available | small (400) |
| DCI — Densely Captioned Images (Meta, CVPR 2024) | 7,805 SA-1B images with mask-level captions; tests CLIP on long text | annotations CC-BY-NC; images need the SA-1B licence | built for retrieval with long captions | NC licence; images separate |
| DetailCaps-4870 / CAPTURE (ByteDance, 2024) | object/attribute/relation matching against references; CAPTURE PCC 0.5051 against experts vs CLIPScore 0.3498 | `capture-metric` on PyPI; dataset on HF unconfirmed | runs locally; no LLM judge | references written by GPT-4V/Gemini |
| CompreCap (CVPR 2025) | 560 COCO images with directed scene graphs: object coverage, attributes, relations | yes, github LuFan31/CompreCap | structured, human-aligned | COCO scenes only |
| CapArena / CapArena-Auto (ACL Findings 2025) | pairwise human battles; Auto: 600 images with a VLM judge, 94.3 % rank correlation to humans | Auto set public; judge is GPT-4o (≈$4/run) — a local judge is not validated | the only arena for detail captioning; finds CLIPScore-style metrics "fail entirely" | no art; needs a strong judge |
| CaptionQA (2025-11) | can a QA model answer 33k questions from the caption alone (657 images) | yes, github bronyayang/CaptionQA (dataset licence not stated) | **closest proxy for retrieval utility** | no art domain |
| CHAIR (2018), AMBER (2023), FAITHSCORE (EMNLP Findings 2024) | object hallucination (CHAIR); AMBER = ½(1−CHAIR+F1), LLM-free; FAITHSCORE: atomic facts checked against the image | yes (github junyangwang0410/amber; bcdnlp/FAITHSCORE) | AMBER needs no judge model; FAITHSCORE fits long descriptions | CHAIR/AMBER use a fixed object vocabulary (COCO-like) that does not fit coins or textiles; FAITHSCORE needs a VLM verifier |

## 4. Cultural heritage and museum work

| Work | What | Relevance |
|---|---|---|
| ArtCap (3,606 WikiArt paintings × 5 crowd captions), SemArt (Web Gallery of Art, comments, Text2Art retrieval), Artpedia (2,930 paintings, 9,173 visual / 19,039 contextual sentences) | painting caption and retrieval datasets | paintings only. Artpedia's visual/contextual split is the same split as our "visible" vs "knowledge" labels |
| Hayashi et al. 2024 (arXiv 2403.00068) | artwork explanation with and without the title | models lean on the title: knowledge vs vision |
| ArTest / "Have LVLMs mastered art history?" (KU Leuven, arXiv 2409.03521) | style, author, period | GPT-4o misclassifies canonical works |
| Museum-65 (arXiv 2412.01370) | 65M museum images, QA; not the Met; CC BY-NC | title and origin are the weakest categories |
| "Seeing History Unseen" (CHR 2025) | WCAG alt text, 100 heritage items, 21 experts | the authors advise against fully automated captioning |
| Met-specific | iMet 2019 (keyword labels, arXiv 1906.00901); CLIP-Art trained on iMet (CVPRW 2021); a 2026 CLIP valuation-bias audit on 1,500 Met objects (arXiv 2609.17572) | **no study of VLM descriptions of the Met found** |
| Art + CLIP retrieval | NoisyArt zero-shot CLIP (arXiv 2309.12110) | no SigLIP 2 / modern-embedder evaluation on art found |

## 5. Image–text embedders, instead of or beside descriptions

| Model | Licence | Notes | Pros | Cons |
|---|---|---|---|---|
| SigLIP 2 (2025-02; B 86M, L 303M, So400m, g 1B; NaFlex variants) | Apache-2.0 (HF/timm cards) | multilingual | permissive; small; direct image search | vectors are not text: no hybrid/BM25 and no explanation; GGUF path unconfirmed |
| Qwen3-VL-Embedding 2B / 8B (2026-01) + Qwen3-VL-Reranker | Apache-2.0 (2B card; 8B unconfirmed) | MMEB-V2 77.8 (8B, first as of 2026-01, vendor); `qwen3vl` GGUF exists, a warm-up crash was reported (llama.cpp #20651) | same family as the describer; text and image in one space; a reranker exists | unconfirmed on art; llama.cpp support immature |
| jina-embeddings-v4 (2025-06; ~4B, Qwen2.5-VL-3B base) | **Qwen Research License** (card corrects an earlier CC-BY-NC) | single and multi-vector (ColPali-style) | strong on visually rich documents | licence restricts use; check before considering it |
| jina-clip-v2 (2024-12; 0.9B) | **CC BY-NC 4.0** | 89 languages, 512 px | multilingual | non-commercial |
| MetaCLIP 2 (2025-07/11) | repo: "majority ... CC-BY-NC" | ViT-H/bigG worldwide; distilled S–L | multilingual | non-commercial |
| nomic-embed-vision v1.5 | CC BY-NC 4.0 (2024 blog) | aligned to nomic-embed-text | — | non-commercial |
| EVA-CLIP | unconfirmed | — | — | — |
| ColPali-family | not researched here | built for document pages, not artworks | — | — |

No museum or art retrieval result was found for any of these models.

## 6. Proposed benchmark (100 Met images)

**Sample**: 100 Open Access objects stratified by department, in proportion
to the Met's counts but with at least 5 per major type (paintings, drawings
and prints, photographs, coins and medals, textiles, arms and armour, Asian
art, Egyptian, Greek and Roman, decorative arts), crossed with image size
(three bands from the actual JPEG sizes). Include about 10 famous works and
about 10 anonymous objects, to measure over-identification.

**Models (4 + 1)**:

| # | Model | Why | Cons |
|---|---|---|---|
| 1 | Qwen3.6-35B-A3B, non-thinking, F16 mmproj | installed; the baseline | vision path in llama.cpp is community-only |
| 2 | Qwen3-VL-30B-A3B-Instruct | same active cost; mainline support; best CaptionQA | older generation |
| 3 | Gemma 4 26B-A4B (two token budgets, 280 and 1120) | MoE peer; tests the token-budget speed knob | no caption evidence |
| 4 | Qwen3-VL-8B or Gemma 4 E4B | small/fast: does it reach the 1 s/image target? | likely shallower descriptions |
| 5 (optional) | GLM-4.6V-Flash | MIT, different lineage | zh/en only |

**Measure, per model × backend (SYCL, Vulkan)**:
- s/image split into projector encode, prefill and decode (the llama-server
  timings); check the log for CPU fallback of vision ops (#19543);
- image tokens in, output tokens out; peak VRAM;
- **determinism**: the same 100 images twice, then in reverse order, then on
  a fresh load, at `-np 1` and at the intended concurrency. Bytes must be
  identical (sha256 per description);
- **faithfulness**: FAITHSCORE-style atomic facts, checked by a second model
  and a human spot check of 20 images. Report the share of "visible" claims
  that are false, and the number of named works, people and places, each
  marked correct, wrong or unverifiable against the Met's own record. The
  Met record is used only for this grading, never in the description;
- **retrieval**: 2–3 held-out queries per image written by a human from the
  image alone (not from the Met's metadata); embed the descriptions with the
  node's text encoder; report recall@1/5/10 and MRR, with and without the
  descriptions beside the metadata, and SigLIP 2 image vectors as a
  reference line.

**Decision rule** (proposal): the fastest model whose recall@10 is within
the noise of the best, with zero byte drift at its deployed concurrency and
the lowest wrong-identification count. At 517k images: 1 s/image is about 6
days, 5 s/image about 30 days.

## Sources

- Qwen3.6-35B-A3B card: https://huggingface.co/Qwen/Qwen3.6-35B-A3B ; Qwen3.6-27B: https://huggingface.co/Qwen/Qwen3.6-27B ; unsloth MTP note: https://dell.huggingface.co/models/unsloth/Qwen3.6-35B-A3B-MTP-GGUF
- Qwen3-VL report: https://arxiv.org/abs/2511.21631 ; llama.cpp PR: https://github.com/ggml-org/llama.cpp/pull/16780 ; SYCL issue: https://github.com/ggml-org/llama.cpp/issues/19543
- Qwen3.5 small: https://artificialanalysis.ai/es/articles/qwen3-5-small-models ; https://www.alibabagroup.com/document-1960233590314762240
- llama.cpp multimodal: https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md
- Gemma 4: https://gemma.ai/models/gemma-4/model-card/ ; https://huggingface.co/papers/2607.02770
- InternVL3.5: https://arxiv.org/abs/2508.18265 ; MiniCPM-V 4.6: https://huggingface.co/openbmb/MiniCPM-V-4.6 ; Molmo 2: https://allenai.org/blog/molmo2 , https://arxiv.org/abs/2601.10611 ; GLM-4.6V-Flash: https://huggingface.co/zai-org/GLM-4.6V-Flash ; Kimi-VL: https://huggingface.co/moonshotai/Kimi-VL-A3B-Instruct ; Ministral 3: https://arxiv.org/abs/2601.08584
- CapArena: https://arxiv.org/abs/2503.12329 ; CaptionQA: https://arxiv.org/abs/2511.21025 ; DiffCap-Bench: https://arxiv.org/abs/2605.04503
- DOCCI: https://google.github.io/docci ; IIW: https://google.github.io/imageinwords ; DCI: https://github.com/facebookresearch/DCI ; DetailCaps/CAPTURE: https://arxiv.org/abs/2405.19092 ; CompreCap: https://arxiv.org/abs/2412.08614 ; AMBER: https://arxiv.org/abs/2311.07397 ; FAITHSCORE: https://arxiv.org/abs/2311.01477
- Nondeterminism: https://arxiv.org/abs/2506.09501
- Art: https://arxiv.org/abs/2403.00068 ; https://arxiv.org/abs/2409.03521 ; https://arxiv.org/abs/2512.21337 ; https://arxiv.org/abs/2412.01370 ; https://maehr.github.io/chr2025-seeing-history-unseen/paper/index.html ; https://arxiv.org/abs/1906.00901 ; https://arxiv.org/abs/2609.17572 ; https://arxiv.org/abs/2309.12110 ; ArtCap/SemArt/Artpedia via https://arxiv.org/abs/2409.10921
- Embedders: https://arxiv.org/abs/2502.14786 ; https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B , https://arxiv.org/abs/2601.04720 ; https://huggingface.co/jinaai/jina-embeddings-v4 ; https://huggingface.co/jinaai/jina-clip-v2 ; https://github.com/facebookresearch/MetaCLIP ; https://blog.nomic.ai/posts/nomic-embed-vision
