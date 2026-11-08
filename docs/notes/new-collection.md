# Making a collection: one workflow

*Operator, 2026-11-06: "A mess up in a collection embedding is the most
severe flaw we can have, this is not acceptable. I expected that a clear
workflow for the creation of a new collection had been implemented." And:
"Any new case not covered must be added to this framework. The base idea is
at some point to also make a wizard."*

**The rule.** Every collection is made by one workflow:
`louise hold collection new` on the command line, its routes (Library,
LocalAGI, OpenAI vector stores), and later its wizard. It is never made by
commands chained by hand.

- **What the operator gives:** only what only the operator knows. That is
  the local name, plus the plugin and recipe for a new space.
- **Everything else is derived and checked:** the endpoint, the models, the
  CPU twin, the window, the cut, the instruction and the tolerance.
- **Embedding stays off** until the gate has passed and the operator turns it
  on.

## The steps

Every case runs a subset of these, always in this order. Each step is
checked against the one before it, and each says what it found.

| # | Step | What it does | Refuses when |
|---|---|---|---|
| 1 | resolve | Reads the recipe or the source collection. Finds the endpoint, the models and the CPU twin on the node. | A twin does not state a CPU run of the same weights. |
| 2 | check | Checks the target before anything is measured. A collection of that name already made is held to the recipe by `space_id`. | The platform image is unknown, or an existing collection is another space. |
| 3 | calibrate | The card against the CPU twin, for each role (document; also query when the space has a query encoder). Uses the space's own ctx, cut, instruction, dtype, and set or pairs. The CPU lane is kept in the node's store. | An endpoint does not answer (exit 3). |
| 4 | tolerance | Takes the measured tolerance. A recipe that states none takes it. | The recipe states another tolerance: a tolerance is measured, never typed. |
| 5 | make | Does one of: create, copy a held space, reuse a held reference, deposit, annotations, peer fetch. | The plugin or recipe is refused (07 §7.3). |
| 6 | carry | Re-creation only: takes from the predecessor what is provably identical (RULE 1). Its parts are seeded, each checked against its root. A sample of chunks is re-encoded by the new run and held to the stored vectors. Then the manifest, text index and vectors are copied and proven by digest. | The sample falls below the space's tolerance (exit 1). The copy is not its source's. There is no room for it. |
| 7 | cpu-reference | Writes the collection's copy of the CPU reference from the store. Nothing is computed twice. | The twin is not reproducible. |
| 8 | gate | `hold embedder check`, for each role. | The gate fails (exit 1). |
| 9 | swap | Re-creation only: prints the predecessor's `forget` and its size, for the operator. After it, takes the predecessor's name and its collection file. It deletes nothing. | The predecessor is still held (exit 2, until the operator forgets it). |

The workflow is resumable. Its progress is kept in
`<LOUISE_ROOT>/state/new/<name>.json`, from before the collection exists. Run it
again after a stop: a step already done is checked, not redone, and the CPU
lane resumes from the store's journal. For example, the calibration is reused
when the card still states the run it was measured on. A different request
under the same name starts afresh and says so.

## The cases

The operator gives the first column; the workflow finds everything else.

| Case | When | The operator gives | Steps | Example |
|---|---|---|---|---|
| **A. A new space** | A plugin and a recipe of its own | plugin, recipe, name | 1–5, 7, 8 | wikidata on Nemotron |
| **B. A new space with its own question encoder** | `space.query.encoder` in the recipe | as A | 1–5, 7, 8, for both roles | — |
| **C. A pair space** | The calibration set is query–document pairs | as A, plus the pair file | as B | pubmed (MedCPT) |
| **D. The same document side as a held collection** | A re-creation whose document side is unchanged | as A, plus the held collection | 1, 2, 5 (the member reused), 7, 8 | wikidata, parser 2 |
| **E. A copy of a held space** | A second collection in an existing space | plugin, recipe, the space's collection, name | 1, 2, 5 (copied), 7, 8 | a second collection in wikipedia's space |
| **F. A re-creation** | A collection replaced, nothing official published from it (RULE 1). Built on A: the same records and space, another engine | plugin, recipe, the predecessor's name (`--re-create`) | 1–9 (A's, then 6 and 9); on D or E, theirs, then 6 and 9 | normattiva on its archived engine; wikipedia-v8 → wikipedia |
| **G. A private collection** | LocalAGI or an OpenAI vector store; not shared | name only | as E (the served model's held space); the first on an encoder is A | `notes` |
| **H. Annotations** | Claims about another collection; no encoder | claims file, name | 1, 2, 5 | gutenberg-book-editions |
| **I. A peer's collection** | A copy fetched from a peer | collection id, name | 1, 5 (fetched), 7, 8, on this node's encoder | Perugia's gutenberg |
| **J. Sister, daughter, corrective** | Lineage (02 §2.7) | the mother, plus what changes | as A, B or C into a new space; else 5, 7, 8 | none yet ("No official daughters") |

### Simple examples

```bash
# A — a new space (wikidata on Nemotron)
louise hold collection new wikidata --recipe recipes/wikidata-nemo.json --name wikidata \
    --encoder http://node:30286 --api vega --embed-model vega-nemotron-3-embed-1b \
    --cpu-model vega-nemotron-3-embed-1b-cpu

# E — a second collection in a held space (neuris in legi's): the space, its
# layers, reference, endpoint and model are legi's; only the plugin, recipe and name are given
louise hold collection new neuris --recipe recipes/neuris.toml --name neuris --space-from legi \
    --cpu-model vega-nemotron-3-embed-1b-cpu

# F — a re-creation: the collection held under --name, on another engine;
# its endpoint, model and platform image are the predecessor's
louise hold collection new normattiva --recipe normattiva-recreate.toml --name normattiva \
    --re-create --cpu-model vega-dar-legal-it-cpu
# (its CPU twin is deploy/vega/stage/models/vega-dar-legal-it-cpu.yaml: the live
# vega-dar-legal-it with device:cpu and nothing else changed — the same backend,
# weights, env and threads; staged into LocalAI's models folder, then a restart)

# G — a private collection, from LocalAGI or any OpenAI SDK: only a name
curl -X POST http://node/api/collections -H "Authorization: Bearer $(cat deposit.token)" \
     -d '{"name": "notes"}'
python -c 'from openai import OpenAI
OpenAI(base_url="http://node/v1", api_key=open("deposit.token").read().strip()).vector_stores.create(name="notes")'
```

The other cases take the same command with the case's own inputs, shown in
§"Status" as each one is built.

## Adding a case

A situation that no row above covers is a NEW CASE, and it is added here
before it is used. It is never run as a chain of commands.

1. Declare it in `app/hold/workflow.py`: what the operator gives, what is
   derived, its steps, and one example.
2. Use the existing steps. Add a step only when none does the job, and give
   it its refusals.
3. Add its row above, with a simple example.
4. Tests:
   - its own tests;
   - `tests/test_creation_framework.py` fails when a case is declared but not
     documented here;
   - the same test fails when any code writes a Codex outside the
     framework's steps.

## The wizard

The wizard comes later, on the same declarations.

- It asks only a case's "operator gives". The Library asks only the name
  (RULE 2).
- It shows what the workflow derived (the endpoint, the twin, the window,
  the cut).
- It runs the steps with their progress, and stops at the first refusal,
  saying it in plain words.

## Status

| Case | State |
|---|---|
| A | Built: `new` (#331, #333, #334; on the framework in phase 1). Run on the node: wikidata, 2026-11-06, exit 0, the gate PASSED (tolerance 0.999995, exact, CPU worst 0.999996). |
| B | Refused at resolve, before anything is asked (phase 1); built in phase 3. |
| F | Built: `new --re-create` (2026-11-07, pulled forward from phase 3 for normattiva; app/hold/recreate.py, tests/hold/test_collection_recreate.py). On A's steps: the predecessor's records and space, another engine. A recipe that moves the records (a parser) is refused at check: its carry is `dev carry --rebuilt`'s, by hand, until F on D. Not yet run on the node: the runbook below. |
| E | Built: `new --space-from <held collection>` (2026-11-08, pulled forward from phase 2 for the legal collections: neuris and eurlex in legi's Nemotron space; tests/hold/test_collection_new_space_from.py). The space and layers copied, the reference vectors REUSED (`calibration_from`), never re-embedded, so the review's "re-embeds and only warns" cannot arise on this route; a plugin that cuts otherwise than the space's layers, or a recipe stating another space, is refused at resolve; the endpoint and model are the source's file's. Not yet run on the node. |
| C, D, G–J | Planned, phases below. |

**The plan.** Each phase ships with tests and is measured on the node before
the next one starts.

1. **The framework.** Done.
   - `app/hold/workflow.py`: the steps, the cases, and a run's progress
     (`<LOUISE_ROOT>/state/new/<name>.json`).
   - `new` rebuilt on it: steps numbered per case, a done calibration
     reused when the card states the same run.
   - An existing collection is held to the recipe by `space_id`, its
     engine and its calibration set (`col.dry_codex`, the Codex the recipe
     would make), not by five fields.
   - A `space.query.encoder` is refused at resolve.
   - The guard test, `tests/test_creation_framework.py`, with its
     allow-list naming each path not yet moved.
   - Calibrate's dtype: checked, not a defect (below).
2. **E, D, G, I.**
   - The space copy, the reused reference, the private collection (the
     LocalAGI and OpenAI routes go through the workflow), the peer copy.
   - Each ends with the CPU reference and the gate.
3. **B, C, F, H.**
   - The query role measured (calibrate measures the document side only
     today), and pairs.
   - Re-creation with the carry and the swap: built on A (2026-11-07, for
     normattiva). On D and E with them.
   - Annotations.
4. **J; the hand route closed.**
   - Lineage.
   - `create`, `create deposit`, `derive`, `continue`, `replace`, `share
     fetch` and the Library's create become steps of the workflow.
   - The guard test's allow-list is empty.
5. **The wizard.** The command line (`new --wizard`) first, then the Library
   dialog.

## Runbook: normattiva on its archived engine (case F)

**The decision** (operator, 2026-11-07): "B: re-create normattiva with a
Codex naming the archived current engine, and carry its vectors over
unchanged". Nothing official was published from it, so this is a
re-creation, not a daughter (RULE 1).

**It starts with the dry run** (the pilot rule: München reads a dry run
before every whole run). Each `new` below is run first with `--dry-run`,
which stops before calibrate, writes nothing and takes no lock.

- Its Codex names the backend `louise-dar-legal-it` (`sha256:173c04c1…`).
  Its files are lost, so 09 §9.3 (an engine kept for the Codex's life)
  cannot hold.
- The engine serving it now is archived and verified:
  `vega-dar-legal-it` → `louise-python-20261021a`, wrapper
  `vega-louise-pytorch` (bare `sha256:dfb5e81a…`, with the wrapper
  `sha256:4724a9b9…`).
- By hand, it reproduced 200 random stored vectors: 196 byte-identical, 4
  within cos 0.99999, worst 0.9999999995.

**1. The recipe and the twin** (files, nothing run). Take the recipe
normattiva was created from (`normattiva-recipe.toml`, as `normattiva-1861`
used it), and change two things:
- replace `[space.encoder.engine]` (and its `env`) with the `engine` that
  `louise hold embedder run --encoder http://192.168.178.10:30286 --api vega
  --model vega-dar-legal-it` prints;
- delete `tolerance`, which is measured.

Keep the rest. The twin, `vega-dar-legal-it-cpu` (the one
`tools/cross_device_check.py` names), must state a CPU run of the same
weights.

**2. The dry run, read before anything runs.**

```bash
louise hold collection new normattiva --recipe /config/recipes/normattiva-recreate.toml \
    --name normattiva --re-create --cpu-model vega-dar-legal-it-cpu --dry-run
```

It runs resolve and check, which refuse exactly as the whole run does, with
the same exit and sentence. A recipe whose records or space are not
normattiva's is refused, naming the field (for example a plugin default that
has moved since 2026-10-14; pin it in the recipe, `[parser]`, …). Then it
prints:
- `differs`: the fields the recipe changes. Expected: `space.encoder.engine`
  alone. Anything else is named, and is read before the run.
- `space_id`: kept, normattiva's.
- `to carry`: the documents, the vector rows and the bytes to copy, per tree
  (`lance`, `index`, the manifest), counted from normattiva, with the time
  the count took. These are the counts this runbook does not have.
- `room`: needed against free. Short is the carry's refusal (exit 2).
- `sample`: its size and seed. The seed is the one the carry draws with if
  calibrate measures the run the card states now.
- `locks`, then the steps in order, each `would run` or `kept`.

Go on only when it exits 0 and `differs` is the engine alone.

**3. Pause, then run.** The carry needs normattiva's write lock:

```bash
louise node pause normattiva
louise hold collection new normattiva --recipe /config/recipes/normattiva-recreate.toml \
    --name normattiva --re-create --cpu-model vega-dar-legal-it-cpu
```

The endpoint, the model and the platform image are normattiva's own. What
to expect:
- **make**: `normattiva.next`, paused until the swap.
- **carry**: the 2026-09 parts seeded as hard links. Then 200 chunks
  (`--sample`, the default) are re-encoded and held to the stored vectors at
  the measured tolerance, before any copy. Then the manifest, text index and
  vector table are copied (as a reflink where the filesystem has one; the
  room is checked first). The counts and bytes are the dry run's.
  For reference only, not counted for this runbook: 341,959 documents
  (STATUS, 2026-10-14) and 8,178,785 rows (scheduling-log, 2026-10-23, when
  old versions held 78 GB of the table's 82 GB; a table not cleaned since
  copies them all). The vector table and the text index are proven byte for
  byte by digest, which reads both tables once. All of it is recorded in
  `<LOUISE_ROOT>/state/new/normattiva.json`.
- **cpu-reference**, then **gate**.
- **swap**: stops with exit 2.

**4. The forget, with the operator's yes.** The swap prints
`louise hold collection forget normattiva --yes` and its size:
- what goes: the vectors, index and manifest (the size freed is the one the
  swap prints, counted once per inode, not a figure from this note);
- what stays: the parts, which `normattiva.next` hard-linked, and
  `/corpora/normattiva`, which it reads.

It also prints what still keeps `normattiva.next` in quarantine; run that
first, with A1's attested check of the carried table under the validated
run beside it: `louise hold check normattiva.next --checks
roots,chunks,vectors-sample`. `louise hold collection forget normattiva
--dry-run` names what goes before the yes.
The forget is the operator's yes, with that size, in the session that runs
it. The workflow deletes nothing.

**5. Run step 3's command again**, after its dry run (step 2's command:
the steps up to the gate now show as `kept`, the carry with its counts). `normattiva.next`
becomes `normattiva`, with normattiva's collection file (its codex, and
`embed = "off"`, excepted). Then:
- `louise node resume normattiva`;
- `louise hold config apply normattiva --set embed=<the mode it printed>`.

## What the review found (2026-11-06)

| Finding | Where | Fixed in |
|---|---|---|
| Calibrate cut by characters a space that cuts by tokens; the store keyed no cut | calibrate, cpu_reference | #327 |
| An existing collection compared by engine id, not weights | `new` | #333 |
| The target checked after the measurement; the name optional | `new` | #334 |
| An existing collection held to five fields, not its `space_id` (engine, dim, dtype, pooling, layers unchecked) | `new` | phase 1 |
| A recipe with any `space.query.encoder` needs the query side; `new` fails at create, after measuring | `new` | phase 1 (refused first), phase 3 (built) |
| Calibrate measures in f32, whatever the space's dtype | calibrate | not a defect: 03 §3.1 computes every cosine in f32, the gate's too; the dtype is how vectors are stored |
| The gate passes with no CPU comparison when no CPU reference is kept | gate | phase 2 (every case makes one) |
| The Library's create sets a hand tolerance, makes no CPU reference, runs no gate, and embeds on demand | Library | phase 4 |
| A private collection's first space (`[deposit].space`) carries a hand tolerance | deposit | phase 2 |
| A space copy re-embeds the reference and only warns when it differs | `create --space-from` | phase 2 (refused) |

**Not covered, on purpose.** These are outdated and are not cases:
- Codex versions below 3 (image-based encoders);
- a whole Codex given as JSON to `create`;
- tolerance "by decision";
- `pack build`;
- the hand chain calibrate → create → cpu-reference → check.
