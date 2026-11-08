# The legal collections on Nemotron (2026-11-08) — PROPOSED, for the architecture session

**The operator, 2026-11-08:**
- "Create the planned legal collections, use Nemotron embedder."
- Then: "one collection per nation … Normattiva is already existing, skip it.
  Talk to the architecture session before."
- And: "Eurlex all languages together. France, Germany, different countries.
  EU same entity, different languages."

Nothing here is built until the architecture session agrees.

## The collections

| collection | law | origin | record | languages |
|---|---|---|---|---|
| `legi` | French | DILA LEGI (+ JORF for the originals' ELI) | one per origin file (see open question 3) | fr |
| `neuris` | German federal | NeuRIS API (the testphase host) | one expression of one act | de |
| `eurlex` | EU | Cellar (Formex 4 + CDM notice) | one version of one act in one language | all 24, each a slice downloadable and embeddable on its own |

Normattiva is skipped: it already exists.

## Amendments

- **One EU collection** (CLAUDE.md invariant 11, 02 §2.3). This reverses
  the 2026-10-28 rule of one EU collection per language. The languages are
  slices, as Wikipedia's are. The platform passes no parameter to a plugin,
  so this is also the form the code already supports.
- **D3:** Nemotron-3-Embed-1B for all three, in one shared space. The
  recipes get an identical `[space]` block, and every plugin chunks with
  `akn_article`. EU-it no longer joins Normattiva's space.
- **`eli` gets one owner**, `eli-refs` (legal-fr-de-eu.md §5.2; 06 §6.5),
  in 09b9ebd. A defect was fixed with it: `louise_lookup` resolved none of
  Normattiva's key forms.

| | pros | cons |
|---|---|---|
| **Nemotron, one shared space (proposed)** | one engine to gate and keep resident; one list across the three, in any language | not chosen on legal questions per language; 2,048 dims; a pin is irreversible |
| one encoder per language, by bake-off (D3 as it was) | the best model measured for each language | several engines on one card; no list across collections; the bake-off runs are still owed |

## EU languages: each downloadable and embeddable on its own

The operator, 2026-11-08: "EU laws, make every language downloadable and
embeddable separately." This needs no new mechanism. It is Wikipedia's,
from 07 §7.8:

- **The slice:** `lang=<code>/…`, one per EU language.
- **Downloaded:** the acquisition spec's `slice_profiles` give each slice
  its own download (`lang=fr` → that language's manifestations), so a node
  fetches only the languages its collection file lists in `slices`.
- **Embedded:** `embed_slices` names the slices the encoder spends on, out
  of those held. The others are searchable by words only, and are "not
  embedded here", never pruned.
- **Example collection file:** `slices = ["lang=en", "lang=fr",
  "lang=de", "lang=it", "lang=es"]`, `embed_slices = ["lang=en", "lang=fr",
  "lang=de", "lang=it"]`. Adding a language later is an edit of these
  lists: nothing already held is re-fetched and nothing is re-created.
- **The native id:** hashed over (CELEX, expression, language), so the
  slices never collide.

| | pros | cons |
|---|---|---|
| **a slice per language (chosen)** | one collection; each node takes and embeds only what it wants; a language is added without re-creation | every language's slice must be built the same way, so the plugin's slicing rule is fixed at create |
| everything held, everything embedded | complete everywhere | ~6× the encoder time of four languages; 24 downloads per node |

## The shared space block

These are the values, not the final TOML.

- Encoder: weights `sha256:09596ced…ecbf2c`, labelled "Nemotron-3-Embed-1B
  f16".
- Vectors: 2,048 dims, f16, mean pooling, normalised.
- Window: ctx 8,191, cut by tokens (the model's context_size 8,192 less one).
- Instructions: `passage: ` on documents, `query: ` on queries.
- `exactness = "exact"`: required from Codex version 10.
- `[space.calibration]`: set `sha256:7d06a2f6…`, quorum n = 16, min_pass =
  12 (every recipe's).
- Tolerance: measured by `new`.
- `[space.encoder.engine]`: read on the node.

## Open questions, from the plugin research

1. **How they are made, by [`new-collection.md`](new-collection.md)'s
   cases.**
   - `legi` is **case A**, a new space. Wikidata's Nemotron space has
     another chunker, so it is not this one. Built.
   - `neuris` and `eurlex` are **case E**, "a second collection in an
     existing space", given legi as the space's collection. Case E is
     **not built** (phase 2).
   - Running A three times on identical blocks would reach the same
     `space_id`, but it is a chain the framework forbids. That gap goes to
     the architecture session. The options are:

   | option | pros | cons |
   |---|---|---|
   | **build case E first (phase 2), then neuris and eurlex** (recommended) | the framework's own route; the space is copied and its CPU reference reused, nothing measured twice | neuris and eurlex wait for phase 2 |
   | three spaces, each case A | everything built today | no single list across the three; three calibrations and gates; the shared-space decision is dropped |
   | declare "A into a held space_id" as a new case | quick | a second route for E's job, which the framework exists to prevent |
2. **NeuRIS: annexes are separate files.** The proposed fetch is the
   manifestation zip plus the per-expression JSON, with `origin_hash` taken
   over the zip's members.
   - How `allChanged` is obeyed is open.
   - Where a per-expression change stamp lives is open.
   - `jurabk` keys contain spaces (`SGB 5`); whether that is allowed is
     open.
   - **Built 2026-11-08** (`app/plugins/neuris`, `app/acquire/neuris_listing.py`,
     `app/refs/neuris.py`, `neuris-recipe.toml`): `allChanged: true` leaves
     the listing compared with what is held, by path; `false` stamps the
     `changed` files for a fetch again and reports `deleted` ones. The stamp
     is the changelog's own answer, kept in `fetch_files.remote_mtime`, no
     file beside the corpus. A `jurabk` key is verbatim, space included
     (05 §5.2 says "verbatim" and nothing more); a round-trip test holds it.
     `origin_hash` covers the zip's members AND the expression's JSON, since
     the record is parsed from both. Owed from the node: the busiest day,
     versions per act, the annexes in the zip, ETags, `ris:verweise`.
3. **LEGI: article-only records would drop `TEXTE_VERSION`, `SECTION_TA`
   and `TEXTELR` content** (invariant 11). The proposal is one record per
   origin file, of four kinds; only articles carry validity.
   - Out-of-range dates (`2222-02-22`, `2999-01-01`) need an id rule.
   - `MODIFIE_MORT_NE` needs an index rule.
   - The tarballs are read through a container pack.
4. **Cellar:** the acquisition is SPARQL listing plus content negotiation.
   The body item is picked by name. **Built 2026-11-08** (not created):
   the `eurlex` plugin (`app/plugins/eurlex/`), its reference source
   (`app/refs/eurlex.py`, `celex` and `eli:eu:`), the SPARQL listing and
   the unit fetch (`app/acquire/cellar.py`, `EURLEX_SPEC`), and the recipe
   [`eurlex-recipe.toml`](eurlex-recipe.toml) for case E. What is owed from
   the node is listed in the recipe's header.
5. **Owed before `create`:**
   - each origin's terms text, byte-exact;
   - the counts from the node: the busiest start day, LEGI Work-key
     stability, Cellar's counts per year and language, NeuRIS versions per
     act.

## legi: built for case A (2026-11-08)

Open question 3 is answered as the operator decided: one record per origin
file, four kinds, validity on articles only. The plugin is
`app/plugins/legi/`, the reference source `app/refs/legi.py`, the
acquisition `LEGI_SPEC` with a directory-index listing, the recipe
[`legi-recipe.toml`](legi-recipe.toml) (its `[space]` block is the shared
one, loaded by `new`'s reader in `tests/test_legi_recipe.py`).

- **Amendment, the id:** a LEGITEXT id names two files (`texte/version/`
  and `texte/struct/`), so a text's key is the id qualified by that folder.
  With the bare id the two collided on the real fixtures.
- **Slicing:** by kind and year, with parts cut by content, so a version
  the origin adds to a past year re-issues its own part.

Owed from the node, never guessed:
- DILA's index page, pasted raw (the reader takes Apache's and nginx's forms);
- one member of the current baseline, against the 2017 fixtures;
- the deletion lists' line format;
- the personal-data warning, before the class is pinned;
- Work-key stability, the busiest start day, what the ~241,000 empty ETATs are;
- discovery's memory and time, and the baseline's pack build, over ~5.25 M members;
- whether DILA drops a period's increments at its next baseline (`origin_versions`).

## Real fixtures found (GitHub)

| origin | repository | commit | what | caveat |
|---|---|---|---|---|
| LEGI | `Legilibre/legi-php` `files_exemples/` | a028bac0 | articles, a section, a text's struct and version files | older format |
| Cellar | `op-cellar-git/eurlex-xml-format` | a4b89104 | the Office's CDM notice samples | hand-edited |
| Cellar | `eur-lex-rs/eur-lex` `data/` | 6e3db12c | Formex 4, original and consolidated | — |
| NeuRIS | `digitalservicebund/ris-search` | 81b58f82 | LegalDocML.de | test data |

## The common acquisition API (2026-11-08) — FOR THE ARCHITECTURE SESSION'S REVIEW

The operator: "The rag engine must be common, if the api does not cover your
case always talk to the architecture session for a solution for all approach.
No per exception spaghetti code." The three plugins had each added a hook of
their own; they are now two protocols on `AcquisitionSpec`
(app/acquire/spec.py), which the engine asks without naming any plugin:

| extension point | what it covers | declared by |
|---|---|---|
| `inventory: Inventory` — `list(endpoint, spec, profile_filter)`, optional `reports()` and `built_on_request` | an origin that lists itself: a directory index, its own API, a query | legi (`HtmlIndex`), neuris (`NeurisListing`), eurlex (`CellarInventory`) |
| `unit_fetcher: UnitFetcher` — `handles(remote)`, `fetch(endpoint, remote, dest)`, `refetch(spec, locator, base)` | a unit served as several requests, landed whole; its records-vs-origin re-fetch (app/acquire/unit_origin.py) | eurlex (`CellarUnits`) |
| `SliceScheme.hint_of(row, context)` | a slice read from the manifest row without a parse | every plugin with a hint (was an if-branch per plugin name in `slice_hint`) |

**Guard:** tests/acquire/test_common_engine.py. The engine and the
transports import no origin's module and name no source id. It fails on the
code before this change, which imported `cellar`.

**Not yet moved, on the guard's allow-list:** Normattiva's on-request export
(`export_job`, 2026-10-13). It is live on the node. It can become a
UnitFetcher once `fetch` can answer "adopted" and "deferred", and moving it
needs a measured pass.

| | pros | cons |
|---|---|---|
| **two protocols on the spec (built, awaiting review)** | one engine; a new origin is a declaration, not an engine edit; the guard keeps it so | two more fields on the spec; the review may want another shape |
| the per-plugin hooks as they were | nothing to review | three special cases in the engine, growing with every origin |

## Next: a Nemotron Normattiva (operator, 2026-11-08) — PLANNED

The operator: "When you are done with them, I want you to make a nemo version
of normattiva, then run a comparison test before doing the full embedding."

- **What it is:** `normattiva-nemo`, the same plugin, corpus and records as
  `normattiva`, in the shared legal space (Nemotron), cut by the space's
  layer (`legal_article`, legal-chunking-retrieval.md §3); `normattiva`
  cuts by `akn_article`. The existing `normattiva` (DAR-legal-it) is
  untouched.
- **The comparison before the full embedding:** two parts.
  - The bake-off München runs for the operator's 2026-11-07 request
    (legal-embedders-fr-de-eu.md): `lemur-it` hybrid MRR@10, the deciding
    row; JuriFindIT, in-domain for DAR-legal-it.
  - Then, on the collection itself: `tools/legal_pilot.py coverage` and
    `run`, against the old collection. Only the chunks the pilot's
    questions reach are embedded on demand. The full embedding waits for
    its numbers and the operator's go.
- **How it is made: case J, built 2026-11-08** (operator: "Make J, it is
  time to do it"). A RELAYERED sister of `normattiva` in `legi`'s space
  (2026-11-08): her manifest copied with its index marks emptied, no text
  index, her parts built from normattiva's record lines (the same record
  bytes, none parsed) and cut by her layer; the worker then cuts and
  indexes every document with `legal_article`. None of the DAR vectors is
  carried (new-collection.md J; hold/carry.py A1i relayered; 02 §2.7
  amended 2026-11-08). The parts are REWRITTEN with the same bytes: a
  variant that hard-links them and recomputes only each part's chunk
  entries is written down here, to build if the measured build time says
  it pays:

  ```bash
  louise node pause normattiva
  louise hold collection new normattiva --name normattiva-nemo --sister-of normattiva \
      --space-from legi --cpu-model vega-nemotron-3-embed-1b-cpu --dry-run
  louise hold collection new normattiva --name normattiva-nemo --sister-of normattiva \
      --space-from legi --cpu-model vega-nemotron-3-embed-1b-cpu
  louise node resume normattiva
  ```

  It needs legi created first. Its embedding stays off until the comparison
  above, and the operator's go.
