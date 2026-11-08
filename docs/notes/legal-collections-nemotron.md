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
- Window: ctx 8,192, cut by tokens.
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
3. **LEGI: article-only records would drop `TEXTE_VERSION`, `SECTION_TA`
   and `TEXTELR` content** (invariant 11). The proposal is one record per
   origin file, of four kinds; only articles carry validity.
   - Out-of-range dates (`2222-02-22`, `2999-01-01`) need an id rule.
   - `MODIFIE_MORT_NE` needs an index rule.
   - The tarballs are read through a container pack.
4. **Cellar:** the acquisition is SPARQL listing plus content negotiation.
   The body item is picked by name.
5. **Owed before `create`:**
   - each origin's terms text, byte-exact;
   - the counts from the node: the busiest start day, LEGI Work-key
     stability, Cellar's counts per year and language, NeuRIS versions per
     act.

## Real fixtures found (GitHub)

| origin | repository | commit | what | caveat |
|---|---|---|---|---|
| LEGI | `Legilibre/legi-php` `files_exemples/` | a028bac0 | articles, a section, a text's struct and version files | older format |
| Cellar | `op-cellar-git/eurlex-xml-format` | a4b89104 | the Office's CDM notice samples | hand-edited |
| Cellar | `eur-lex-rs/eur-lex` `data/` | 6e3db12c | Formex 4, original and consolidated | — |
| NeuRIS | `digitalservicebund/ris-search` | 81b58f82 | LegalDocML.de | test data |
