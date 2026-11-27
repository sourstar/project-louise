# Perugia on a 16 GB card: the test plan

**Why** (operator, 2026-11-27): "In a few days the node Perugia will be
resumed to test the new backend on the AMD RX 9060 XT and, even more
important, test the behavior with a 16 GB GPU that is the most common size
for consumer market"; "That's the purpose of all those optimizations and the
quest for a universal embedder".

What follows is a plan: every figure in §1 was measured on the B70, under
SYCL (München, 2026-11-27, `node resident show` in chat mode). None was
measured on the RX 9060 XT. On ROCm or Vulkan the same models load at other
sizes, and Perugia's keeper learns them there (§2a).

## 1. What a 16 GB card has to hold (B70 figures, MiB loaded)

| Role | Today | With one universal embedder |
|---|---|---|
| Query encoders (a question to every collection) | nemotron-3-embed-1b 4,404 · qwen3-embedding-4b 5,685 · dar-legal-it 8,453 · bge-m3 1,335 · medcpt-query 484 = **20,361** | nemotron-3-embed-1b **4,404** |
| Rerankers | nemotron-1b 3,348 · medcpt 423 | nemotron-1b 3,348 (medcpt while pubmed keeps its pair) |
| Speech | omnivoice 3,120 (STT and VAD are on the iGPU and CPU: 0) | the same |
| Chat | 35B-A3B 21,716: never fits 16 GB | `chat_small` (9B): its size on the AMD card is the first number owed |
| Translation | madlad 8,005 | the same; loaded only for a question in another language |

Read plainly:
- Today, the query side of one question needs 20.4 GB of encoders alone, which is more than the card holds. Every question across all collections then parks or reloads encoders.
- With one embedder, the query side is about 4.4 + 3.3 + 3.1 = 10.9 GB before the chat model.
- Whether `chat_small` fits beside that in 16 GB is the question the test answers.

## 2. What is measured (docs/notes/measuring.md §6 checklist first: the deployed flags and env, counted numbers, free space)

a. **The backend on the RX 9060 XT.** Every encoder passes the
   reproducibility gate (invariant 12):
   - ROCm: `ROCBLAS_DEFAULT_ATOMICS_MODE=0` in each model's YAML `env`;
   - Vulkan: no mode is offered, so measure, never assume.

   Use the existing harness, `llama-amd-rx-9060-xt` and
   tools/vega_repro_check.py, and record each model's loaded and at-rest
   size as the keeper learns them.
b. **Each mode of use** (embed, query, custom):
   - what `node resident show` holds;
   - every refusal for room (503 no_room_on_device);
   - the parking cycles;
   - the answer timing line (wait before the model, time to first token);
   - embed throughput per encoder.
c. **Today against universal, on the same corpus.** Hold gutenberg (Qwen3-Embedding-4B) and gutenberg-nemo (Nemotron, same layers, one shared text index). The same questions go to each, on the judged set where one exists:
   - quality: MRR, as tools/measure does;
   - the card: what loads, what parks, the seconds to the first answer.

   This is the direct test of one embedder against two.

d. **The space cut** (operator, 2026-11-27: "We will also measure the
   quality by reducing the space size as allowed by nemo"). Nemotron's card
   allows its 2,048 dimensions cut to 1,024 or 512, each slice renormalised.
   Of 2026-11-03 the operator said: "keep the full vectors of Nemotron, it
   will be interesting to verify how much the quality drops halfening them
   once we have a full overview".
   - Measured once, on wikidata W1, the 1,024 cut cost nothing: full − cut
     +0.009 MRR, p = 0.165 (nemotron-embed.md).
   - Owed: 1,024 and 512 on every judged set, gutenberg-nemo included, with
     no second embedding. tools/measure/wikidata_encoder_ab.py's `-m1024`
     derives the cut from the stored vectors; it gains `-m512` and a
     collection argument.
   - What a cut buys a consumer node is disk and the vector index's memory,
     not card memory: the encoder is the same model. At f16, wikidata's
     ~190 GB of vectors become ~95 GB at 1,024 and ~47 GB at 512.
   - If a cut holds, the collection that uses it is a DERIVED sister (02
     §2.7, 09 §9.3 `derivation: truncate`): her vectors are computed from her
     sister's without the card, and the query vector is cut the same way.
     The Codex field is in app/hold/codex.py. The command that makes such a
     sister, and the query side that cuts, are not built.

## 3. Before Perugia resumes

- Merged and deployed on both nodes:
  - gutenberg's in-place refit (its new identity: Perugia's test copy of the old one is superseded, not updated, by the operator's word of 2026-11-27);
  - the hard-link removal;
  - the annotations retarget.
- gutenberg-nemo made by the book (`POST /api/hold/collection/new`).
- Perugia's disk and held set read on resume. A 16 GB card says nothing of disk, so Perugia may hold only §2c's pair.
- The backend build the operator names for the RX 9060 XT.
