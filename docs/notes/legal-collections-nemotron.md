# The legal collections on Nemotron — decision, space, runbook (2026-11-08)

**The operator, 2026-11-08:** "Create the planned legal collections, use
Nemotron embedder."

The planned collections are the ones in [`legal-fr-de-eu.md`](legal-fr-de-eu.md)
§8: France (`legi`), Germany (`neuris`), and the EU, with one collection per
language (`eurlex-en`, `eurlex-fr`, `eurlex-de`, `eurlex-it`).

## Amendments this names

- **D3 amended.** The old rule was one encoder per language, chosen by the
  bake-off. Now Nemotron-3-Embed-1B encodes all six collections, by the
  operator's choice. The per-language bake-off rows
  ([`legal-embedders-fr-de-eu.md`](legal-embedders-fr-de-eu.md)) stay as a
  measurement, and no longer decide anything.
- **One shared legal space.** D3's earlier option A comes back. All six
  recipes carry the same `[space]` block, and every plugin cuts with
  `akn_article` at the same version and parameters. `space_id` is a pure
  function of those bytes (09 §9.3), so each collection made by case A
  lands in the same space:
  - one question searches all six as one list, in any language;
  - one encoder stays resident (with wikidata, whose chunker differs, so it
    is another space).
- **EU-it leaves Normattiva's space.** It was planned as a sister of
  Normattiva's DAR-legal-it space. It now joins the Nemotron legal space.
  Whether Normattiva follows is the measurement of
  legal-embedders-fr-de-eu.md §"Nemotron-3-Embed-1B for Normattiva too",
  and that switch is the operator's.
- **The `eli` namespace has one owner.** It is now `eli-refs`
  (app/refs/eli.py), which routes each key to its jurisdiction
  (legal-fr-de-eu.md §5.2; 06 §6.5 amended). Building it found a defect,
  fixed at the same time: `louise_lookup` resolved none of Normattiva's
  three key forms (`urn:`, `eli:it:`, `akn:`).

| | pros | cons |
|---|---|---|
| **Nemotron, one shared space (chosen)** | one engine to gate and keep resident; cross-language and cross-collection answers; the 8B family leads MLEB, and the 1B is distilled from it | not chosen on legal questions per language (the bake-off's rule); 2,048 dims, about 2.7× the vector bytes of a 768 model; a pin is irreversible, so a better French-only model later means a new collection |
| one encoder per language, by bake-off (D3 as it was) | each language gets its best measured model | three or four engines on one card; no single list across collections; still waiting on the bake-off runs |

## The shared space block

This block is identical in every legal recipe. Its bytes are what make the
six collections one space.

- encoder: `nvidia/Nemotron-3-Embed-1B-BF16` at c0c9fea9, as an f16 GGUF.
  Weights `sha256:09596ced…ecbf2c`
  ([`nemotron-embed.md`](nemotron-embed.md) "Done");
- 2,048 dims, f16, mean pooling, normalised;
- ctx 8,192, cut by tokens (the served `context_size`);
- document instruction `passage: ` and query instruction `query: `
  (Codex version 11);
- tolerance: not typed, because `new` measures it;
- `[space.encoder.engine]`: read on the node and pasted in (below).

## Runbook (München)

**Before anything runs:**

1. Deploy the branch.
2. Paste the engine block into all six recipes, the same block in each.
   Print it with:
   `louise hold embedder run --encoder http://192.168.178.10:30286 --api vega --model vega-nemotron-3-embed-1b`
3. Record each licence acknowledgement, which the create dialog refuses
   without (invariant 9). The terms texts each plugin still owes are listed
   in its recipe header.
4. Take the probes and counts each plugin's docstring names as owed. They
   must come before `create`, because the id width and the Work key are
   pinned there.

**Then, one collection at a time:**

1. Run the dry run first:
   `louise hold collection new <name> --recipe /config/recipes/<name>-recipe.toml --name <name> --encoder http://192.168.178.10:30286 --api vega --embed-model vega-nemotron-3-embed-1b --cpu-model vega-nemotron-3-embed-1b-cpu --dry-run`
2. Read the dry run's `space_id`. All six must print the same one. A
   different one means the blocks differ, so stop.
3. Run the same command without `--dry-run`.

**The order:** France, the EU (en, fr, de, it), then Germany, as §7.1
proposed. Embedding stays `off` until the gate passes and the operator
turns it on (new-collection.md).

## What is built and what is owed

The status of each plugin is in its own source docstring and in
[`STATUS.md`](STATUS.md) under 2026-11-08.
