# Upstream reports, ready to file (2026-10-18)

One file per recipient, each section one issue. The operator files them.
Nothing here has been filed yet.

In the sections written out since 2026-11-26 the issue itself lies between
`<!-- issue:… -->` and `<!-- /issue -->`: its first line is the title, and
nothing in it names this project, its node or its people — a test holds it
(tests/test_upstream_reports_ready_to_file.py).

**Checked against each tracker on 2026-10-18; none is already known.** The
rule (operator): skip an issue upstream already knows, unless its cause is
unknown there and we have the fix. Searched intel/torch-xpu-ops,
pytorch/pytorch, wmde/WikidataTextifier, philippesaade-wmde and
exowanderer/WikidataTextEmbedding, both Hugging Face datasets' discussions,
ggml-org/llama.cpp and mudler/LocalAI. The near hits, and why they differ:
- LocalAI #12480: a backend that dies on its own stays listed. B3 is a live
  backend left untracked after a failed eviction.
- LocalAI #8562: llama.cpp's auto-fit and `gpu_layers`, not B11.
- Issues that quote B10's or B13's log lines inside other bugs.
- llama.cpp #27771 and #27769: other SYCL wrong answers (bf16 matmul, flash
  attention), on Xe-LPG.

| file | to | issues |
|---|---|---|
| [intel-torch-xpu-ops.md](intel-torch-xpu-ops.md) | intel/torch-xpu-ops | fp32 `torch.softmax` wrong for row lengths 513–768, … (B70, torch 2.14.0/2.14.1+xpu) |
| [wmde-wikidata-textifier.md](wmde-wikidata-textifier.md) | wmde/WikidataTextifier | random alias order; formatter failures silently drop values |
| [wmde-wikidata-embedding-project.md](wmde-wikidata-embedding-project.md) | philippesaade-wmde (WikidataTextEmbedding and the HF datasets) | stale vectors kept as current (~14 %); text depends on a live wiki page; the corpus drops entity data; the card's counts |
| [ggml-llama-cpp-sycl.md](ggml-llama-cpp-sycl.md) | ggml-org/llama.cpp | SYCL embeddings non-reproducible through oneDNN's GEMM; F16×F32 batched matmul wrong for a non-contiguous `a` on the Arc Pro B70 (upstream-bugs.md S1; the same on runtimes 26.27 and 26.35, added 2026-10-28); **a refused pool commit aborts the process (localai-bugs.md B18), written out 2026-11-26** |
| [mudler-localai.md](mudler-localai.md) | mudler/LocalAI | B1–B14 of localai-bugs.md, condensed; **B17, B19, B20, B21, B22, B24, B25, B26 written out to be filed as they are** (2026-11-26; B17 as a feature request) |
| [wikidata.md](wikidata.md) | the Wikidata team (WMDE) | STANDING (operator, 2026-10-24): every improvement to Wikidata the build finds — data defects, missing labels as a worklist, and an index of the two WMDE code files |

**Checked again on 2026-11-26, for the issues written out that day** (a web
search of both trackers, and the threads read). None is known with its cause:
- LocalAI #11970 (closed as an enhancement, v4.9.0) quotes B26's 500 in
  passing and says nothing of why: B26 is written to name it.
- LocalAI #12204 and #12425 (both merged) repair a failure BEFORE the first
  chunk of a stream; B21 is the failure after it, which neither touches.
- llama.cpp #29581 (open, stale) is B19's handler in llama.cpp's own server,
  with the fix proposed and not merged; LocalAI's `grpc-server.cpp` has its
  own copy, so B19 is written for LocalAI, naming it.
- llama.cpp #27595 (open, unconfirmed) shows B18's abort and is read there as
  a usage problem; the cause as a defect, and the fix, are ours to give.
- Nothing found for B20, B22, B24, B25, or B17's request.

Not included, because there is no reproducer yet:
- jina-v3's batched `encode` failing in `torch.unique` on the B70; a plain
  `torch.unique` reproduces nothing.
- llama-server corrupting long texts when one request carries several
  (STATUS, `EMBED_BATCH=1`, 2026-08).
