# Hand-off to München: wiki-gutenberg steps 1–3 on the node (2026-11-18)

*Written by the wiki-gutenberg planning session, on the operator's approval
("I approve the recommended choices", 2026-11-18). It could not be sent:
the cloud session may not reach the node's session. Paste it there.*

The plan and the decisions are on branch `ccr-8a5c4013-0oqgjh`:
`docs/notes/wiki-gutenberg-claude-plan.md` §9 (roadmap) and §11
(decisions).
- **Local models only.**
- **Scope:** history and memoirs. Fiction, poetry, drama and children's
  books are out.
- **The first slice:** the 52 Italian memoirs,
  `docs/notes/wiki-gutenberg-memoir-titles-it.toml` plus the subject rule
  in `tools/measure/kg_scope_count.py`.

Asked of the node, in the night windows (24×7 applies only to the later
production runs):

1. **Engine checks.** Qwen3-30B-A3B (MoE) and Gemma 3 27B (dense) on the
   deployed SYCL llama.cpp:
   - the same bytes at temperature 0 over 3 fresh loads (invariant 12);
   - **prefill tok/s and parallel-slot scaling**, both unmeasured (the
     plan assumed 1,500 and 400 tok/s prefill);
   - GLiNER's determinism under torch.
2. **Benchmarks** with the existing harnesses: `kg_link_pick_eval.py`
   (Mewsli-X; an LLM pick against a cross-encoder pick),
   `kg_relations_eval.py` (Re-DocRED), `summary_eval.py`. MoE against
   dense on the non-English rows.
3. **The pilot** (§1): 1,000 chunks end to end.
   - Count mentions per chunk, the share of chunks with ≥ 2 mentions, and
     the community count.
   - Set the `model-agreement` floor from 1,000 mentions re-run twice.
   - Take the Italian rows from the Italian memoir slice.

Report the measured numbers into the plan note, so that the estimates in
§6–8 become counts. Follow measuring.md's checklist. Anything beyond it,
ask the operator.
