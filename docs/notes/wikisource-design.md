# Wikisource: a collection (design, 2026-11-19)

*Architecture session, on the operator's request ("talk to architecture for
2, I want a design note"; the request: `docs/notes/wikisource-design-request.md`
on branch `ccr-8a5c4013-0oqgjh`). It decides the request's six points, each
with its options' pros and cons, then names what is built and in what order.
The operator may overrule any decision; the two choices that are the
operator's alone are at the end.*

**What it is for.** A scan-backed transcription source beside gutenberg:
memoirs and history first (the wiki-gutenberg graph, plan §§9–12), the
general collection after. Measured by the planning session (2026-11-19):
it.wikisource's text dump is 0.45 GB (en 3.44, fr 2.60, de 0.58, es 0.25);
its history category holds 543 works, 138 MB of text and 5.2 GB of scans;
memoirs 239 works, 73 MB, 4.1 GB; biographies 93, 37 MB, 1.2 GB.

**The shape, in one paragraph.** ONE collection, `wikisource`, built from the
dated XML dumps of each language's wiki, each language a slice (invariant 11,
as Wikipedia's languages). A record is a WORK: its main-namespace page and
its subpages, with the proofread scan pages they transclude expanded from the
same dump. Every page revision the record used is named with its revision id
and its MediaWiki `sha1`, so the record can be checked against the origin
for as long as Wikimedia keeps revisions. Scans are named and hashed, and
their bytes are an optional kind a node may take. Gutenberg and Wikisource
meet only at ask time, through a two-target annotations collection.

## 1. The origin form, and how a record is checked against it

| option | pros | cons |
|---|---|---|
| **A. The dump's wikitext**: the main namespace, `Page:` (it: ns 108, *Pagina*) and `Index:` (it: ns 110, *Indice*), with the `<pages index=… from=… to=…/>` transclusion expanded by our parser from pages of the same dump | dated and re-fetchable (the run is pinned in the Codex, 09 §9.17, as wikidata's); keeps everything the origin states, including each page's proofreading level and the header template's fields; the parser and its dump reader exist (Wikipedia's multistream reader, `app/bz2_blocks.py`'s block origin; `app/refs/wikisource_readings.py` already reads ns 108/110) | the transclusion expansion is our code, versioned as the parser; templates are not rendered, so text a template generates is kept as its call, not as rendered words |
| B. The rendered text (HTML dumps or the API's parse) | what a reader sees, templates rendered | Wikimedia's HTML dumps are pre-rendered and lossy (Wikipedia's origin form rejected them for that reason); the API has no dated form a verifier can re-fetch |
| C. The scans with our own OCR | independent of the wiki's transcription | throws away the proofreading, which is the point of Wikisource; GPU and disk for text the origin already gives; our OCR is not what the origin states |

**Decided: A.** Recorded beside the plugin as "THE ORIGIN FORM, and why"
(invariant 11, its test).

**How a record verifies.** The record's `origin` names its page — the
work's main-namespace page, an author's — in the run-free name of each of
the four files its unit is read from, joined by `|`:
`<wiki>/pages-articles-multistream.xml.bz2#<id>`, then the run's
page_props, categorylinks and linktarget alike (Wikipedia's parser-6 form;
`app/plugins/wikisource/locate.py`). `extra.pages` lists every page used, in
reading order: page id, revision id, the dump's `<sha1>`, namespace, and for
a `Page:` its proofreading level. `origin_hash` is taken over those
revisions' texts in that order, then their rows of the three tables.

*Built 2026-11-24*: the keyed origin (`origin_key`, the five hooks of
app/keyed_origin.py). `records-vs-origin` resolves a record in the
snapshot's run under an origin base (`local_of`), fetches and parses it
again and compares the whole line. The base is a download of the run
(`hold check --origin DIR`), as Wikipedia's is.

*Built 2026-11-24, the first of the two ways below*: the origin by Range,
AGAINST THE COPY (app/acquire/range_origin.py, generic; the plugin's
`origin_ranges`). The block origin's method (#752, wikidata's) taken over
for a run of container files: the copy places a record's unit — the
streams of the pages its work is made of, found by reading them through
the copy's index of the run — those byte ranges are fetched from the
origin and compared with the copy's, the three tables are fetched whole
and compared (about 45 MB for it, once a check, shared by every record of
the wiki), and a record whose bytes are the origin's is re-parsed from the
copy in the parse pool. A stream of a multistream dump is a bzip2 stream
of its own, so equal bytes decode alike with nothing assumed of the bytes
before. A unit is weighed by its streams when the check's bound reaches
it, the tables its shared pieces. Wikipedia's dump layer has the same
shape and needs only the hook.

| | pros | cons |
|---|---|---|
| against the copy (built) | exact; small; the proven method; no assumption about the wiki | needs the corpus: a peer holding only the SHARD checks against a download of the run, as for Wikipedia |
| copy-free (not built) | a peer checks with the SHARD alone | the run's page index rebuilt from the origin's multistream index, which carries no redirect and no content model: both read from fetched streams, and a content model taken to be a namespace's; a mistake shows as a false failure, never a false pass |

The operator was asked which (2026-11-24); the first is the default taken.
Not yet run through `hold check` on a collection: step 2 makes the first.

- while the run is served: the above;
- after the run is gone from dumps.wikimedia.org: it fetches the same
  revision ids from the wiki's API, one batched `prop=revisions` request per
  record, through the transfer budget (invariant 5). Each revision's content
  must give the recorded `sha1`, and the re-parse must reproduce the record.
  MediaWiki keeps revisions under their ids, so this stays checkable unless a
  revision is deleted; such a record is said "origin withdrew it", never
  passed.

## 2. Bulk first; the scans

The legal rule (bulk before any per-unit request; once a day for a newer
bulk) is written for legal collections. It is taken here too, because the
same waste applies.

| part | bulk | per-unit (API) only for |
|---|---|---|
| text, every namespace the record reads | `pages-articles-multistream.xml.bz2` and its index, the run pinned | a revision checked after its run is gone (§1) |
| categories (the selection, §6) | `categorylinks.sql.gz` of the same run | nothing |
| page properties (the Wikidata item a work is sitelinked to) | `page_props.sql.gz` of the run | nothing |
| scan metadata (file name, size, `sha1`, licence) | Commons' `image.sql.gz` (the `img_sha1` column), only rows for files an `Index:` page names | files uploaded after that dump, until the next one |

**The scans.**

| option | pros | cons |
|---|---|---|
| **A. Name and hash every scan; the bytes an optional kind (`scans`), not taken by default** | nothing about the work is dropped: the record states which file, which pages and its `sha1`, so the facsimile is verifiable (invariant 11); the 10.5 GB of the three categories, and far more for the whole wiki, is spent only by a node that asks; a peer can fetch and check any scan against the sha1 later | a node without the kind cannot show the page image beside the text |
| B. Take every scan | the facsimile on disk, shown beside the text | gigabytes per category and terabytes for whole wikis, for page images the text already transcribes; every file's own licence (§5) has to be gated before its bytes move |
| C. Neither bytes nor hashes | the smallest | the record forgets which facsimile it was proofread against: less than the origin gives (invariant 11), so not allowed |

**Decided: A.** A scan is named in `extra.scans`: Commons or local file
name, `sha1`, size, the page range the record uses, and the licence Commons
states. The `scans` kind fetches bytes per work, checked against the `sha1`,
and is held like gutenberg's figures (`hold check`'s `figures` row reads it).

## 3. What a work is

- **A record is a work.** That is the main-namespace page with no `/` in its
  title, plus every subpage (`Title/…`), in the order the main page links
  them, then any subpage it does not link, by title. The order rule is part
  of the parser's version (invariant 3).
- **Its text** is the subpages' wikitext with each `<pages index=… from=…
  to=…/>` expanded from the `Page:` pages of the same run.
  - One section per subpage, the subpage title as its heading.
  - A work spanning several `Index:` files (multi-volume) is one record
    that lists every index it reads.
- **Its fields** come from the work's header template (it: `{{Intestazione}}`,
  en: `{{header}}`): title, author (with the `Autore:`/`Author:` page it
  links), year, and the work's own notes. The whole template call is also
  kept in `extra.header`.
- **Its categories** are kept as the record's subjects (`extra.categories`).
  The Wikidata item from `page_props` is kept as a key.
- **Author pages** (`Autore:`, `Author:`) are records too, in their own slice
  kind (`authors`). They state the authors' dates and works, and nothing the
  origin gives is left out.
- **Edge cases** are counted in the build's report, never guessed:
  - a main-namespace page that transcludes nothing is a work of wiki text, an
    ordinary record;
  - a page that is only a disambiguation or a redirect is no record;
  - a subpage whose parent page is missing becomes a work of its own.
- **Languages are slices** (`lang=it`, `lang=en`, …), each held and embedded
  or not, as Wikipedia's. A translation on another language's Wikisource is
  another record, joined by a claim (§4), never merged.

| alternative not taken | why |
|---|---|
| a record per subpage (chapter) | splits a work's identity across records; the graph and the same-work claims are about works |
| a record per `Index:` (per printed volume) | a work transcribed from two volumes would be two records, and a volume holding several works one |

## 4. The overlap with gutenberg

Collections meet at ask time only (CLAUDE.md, the fundamental rule).
Neither collection reads the other's records.

| question | decision | pros | cons |
|---|---|---|---|
| how a shared work is joined | a NEW annotations collection, `gutenberg-wikisource-editions` (Codex 9, two targets): `same-work` claims by Wikidata (an item with both a P2034 Gutenberg id and a Wikisource sitelink, from wikidata's own collection) and by a title + author matcher over the two collections' records; `translation-of` across languages as for book-editions | each collection keeps its identity, and gutenberg-book-editions is not re-created again; the method is written in the Codex; only 4,122 items carry P2034, so the matcher is needed and is measured on a hand-checked sample before use | one more annotations collection to keep current |
| (alternative) widen gutenberg-book-editions to two targets | one collection of editions | a re-creation of book-editions each time either target re-creates, the 7,383 claims re-pinned twice as often |
| which transcription the graph reads | the selection (§6) prefers the Wikisource record when **every** page it transcludes is proofread (level ≥ 3), else the Gutenberg record; the other is linked by its claim, not read | one reading per work, GPU time spent once; the scan-backed text is the verifiable one (the operator wanted to verify the results); deterministic and written in the rule | a work Wikisource has only partly proofread is read from Gutenberg even if most of its pages are good |
| embedding twice | each collection embeds its own records, **both** copies | one collection, one space, and no collection's embedding depending on another's annotations (that would make a collection's state a function of a second collection) | the same work's text is embedded twice; at the measured sizes (250 MB of text for the three categories, the whole it-wikisource 0.45 GB compressed) this costs days of GPU time, not weeks, and the graph does not double |

## 5. Licences (invariant 9)

| what | class | gate |
|---|---|---|
| the text | **yellow**, as Wikipedia: CC BY-SA, "attribution + share-alike" | the Wikimedia terms text, laid byte-exact as the collection's terms; one acknowledgement before the first byte moves (acquisition) and before discovery or exchange (ingestion), through `app/licensing.py` |
| attribution | per page history | each record names its pages' revision ids, so the history URL of every page is derivable; the credit shown beside a Wikisource hit (as #762 does for the readings) is "<lang>.wikisource.org, its contributors, CC BY-SA", with the link |
| scans (the optional kind) | per file, as Commons states it (`extra.scans[].licence`) | the `scans` kind takes only files whose Commons licence is a free one (PD or CC); any other is named and not fetched |

The transcriptions of public-domain works are often public domain themselves
in some jurisdictions. The site's terms state CC BY-SA, and the terms the
origin states are what is gated. No claim of our own about the law is made.

## 6. The selection

`wiki-gutenberg-select` reads LCSH subjects, which Wikisource lacks, and
Wikidata carries a genre (P136) on fewer than 5 % of sitelinked items. So:

- **The rule is per language and pinned**: Codex 12's `annotates[].select`
  `{id, version, sha256}`, the same field (branch `ccr-8a5c4013-0oqgjh`,
  still to be reviewed for main, §"Built in order" step 0). Rule
  `wikisource-select`, version 1, holds:
  - per language, the categories (and the subcategories the dump's
    `categorylinks` gives, to a stated depth) that mean memoirs, diaries,
    autobiographies, letters, chronicles, travel accounts and history;
  - the operator-checked title lists, one per language, as for Gutenberg,
    each by its sha256;
  - the §4 preference rule, so the reading choice is in the pin.
- **It reads the records' own categories** (`extra.categories`), never the
  live wiki, so the same snapshot gives the same selection (invariant 4 (5)).
- **Children's books are out**, as the operator ruled for Gutenberg, where a
  category or the header says so.

| alternative not taken | why |
|---|---|
| select by Wikidata genre | under 5 % coverage |
| read the whole wiki into the graph | the operator's scope is memoirs, then history; the rest is GPU time "tainting the scope" |
| hand lists alone | the categories are the origin's own classification; the lists are for what the categories miss |

## Built in order

0. **Codex 12** (`annotates[].select`): DONE 2026-11-23, ported from
   `ccr-8a5c4013-0oqgjh` (b026bce5, de8146c7; the branch shares no history
   with main) with the operator's "A" built: each pinned rule is the SHARD
   member `select/<sha256 hex>.toml` (09 §9.4, §9.16), checked by `roots`
   (tests/hold/test_codex_select.py).
1. **The plugin** `app/plugins/wikisource/`. It covers the dump reader
   (shared with Wikipedia's), the transclusion expansion, the work assembly,
   the header and category fields, and the author records. Its origin-form
   statement and its tests include:
   - a work spanning two indexes;
   - a subpage with no parent page;
   - a page that is not proofread, kept with its level;
   - `records-vs-origin` both ways (by Range, and by revision id after the
     run is gone).

   *Built so far* (2026-11-23): the source (works index, discover, fetch,
   parse), the run's acquisition, and **the run's tables** (branch
   `wikisource-tables`, `tables.py`). Categories and page properties come
   from the run's `categorylinks.sql.gz` and `page_props.sql.gz`, plus
   `linktarget.sql.gz`, never from the wikitext.
   - *The schema, read from the origin* (by Range, 2026-11-23): every run
     served, it.wikisource 20260701–20261101 and the other four languages'
     20261101, has MediaWiki 1.45's `categorylinks`. `cl_to` is gone;
     `cl_target_id` points into `linktarget (lt_id, lt_namespace, lt_title)`.
     Another column list fails the file.
   - *A second statement layout*: from the 20261001 runs every SQL dump
     writes one tuple a line. Wikipedia's page_props reader read such a file
     as empty, silently. It now reads both layouts (its pack version is 2).
   - *What a record holds*: every page it reads (work, subpages, scan pages)
     keeps its category rows (each column but `cl_from`, as stated) and its
     properties in `extra.pages`. The WORK's categories are those of its page
     and its subpages, each name once, in MediaWiki's text form: they are
     `subjects` and `extra.categories`. A scan page's (ProofreadPage's quality
     tracking) stays its own. The work page's item is `extra.wikidata` and the
     key `wikidata:Q…`; its other properties are `extra.page_props`. An
     author's record works the same way.
   - *The unit*: after the pages, each page's page_props and categorylinks
     unit, then the linktarget rows they name. Each piece is framed and
     length-prefixed, so `origin_hash` covers them. The discovery signature
     covers the three files.
   - *Missing or refused tables*: a run missing a table, or holding one the
     reader refuses, is deferred as the container's report.
   - `PARSER_VERSION` stays 1: no collection exists.
   - *The keyed origin* (2026-11-24): `origin_of`, `local_of`,
     `origin_files`, `run_of_locator`, `carried_origin` (locate.py). A
     verifier's own download of the run gives the same record and the same
     `origin_hash`; the same origin read in a later run moves it
     (tests/test_wikisource_origin.py).
   - *The origin by Range, against the copy* (2026-11-24;
     app/acquire/range_origin.py, `origin_ranges`): a record's streams and
     the run's tables compared with the origin's bytes, a unit weighed by
     its streams (§1). Against a mock origin on loopback: the same bytes,
     a stream, a table and a length that differ, an origin gone, without
     Range or silent, a page the run does not hold, the pool's hand-over.
   - Not yet: records-vs-origin through `hold check` on a collection (step
     2 makes the first one); the reader by revision id for a run no longer
     served (§1); the copy-free reader, if peers are to check without the
     corpus.
2. **The collection**, it first:
   - `hold collection create` from a recipe (the dated run pinned);
   - the acknowledgement;
   - records build, snapshot, check;
   - then en, fr, de and es as slices.
3. **Scan metadata** from Commons' `image.sql.gz`; the `scans` kind after,
   only if the operator asks for the bytes.
4. **`gutenberg-wikisource-editions`**: the matcher measured on a
   hand-checked sample of 50 pairs per language before its claims are
   written.
5. **The selection file** `wikisource-select` with the operator's title
   lists, then the graph's annotations Codex names it.

## The operator's two choices

**Decided (operator, 2026-11-19, in München's session):**
1. Scans: "Names + hashes (Recommended)". The `scans` kind is not fetched.
2. Wikimedia terms: "Acknowledge (Recommended)". The acknowledgement is
   recorded, byte-exact, when the plugin's first fetch runs (invariant 9).

**Decided (operator, 2026-11-23): "A".** The selection rule's file (the one
`annotates[].select` names by its sha256, Codex 12) travels INSIDE the
annotations collection's SHARD, as `LICENSE-TERMS` does (09 §9.13): a peer
checks the file against the Codex's hash and reads which records the claims
are about from what it downloaded alone, never from our repository. 09 is
amended, with its test, when Codex 12 is ported (step 0), before the first
publication of `gutenberg-wikisource-editions` or of any annotations
collection that names a selection. Put as: A, the file in the SHARD (a peer
verifies the selection; a wire-format amendment and a test) against B, the
hash only (no wire change; the selection unverifiable without our repo).

The choices as they were put:

**1. Scans: take the bytes or not.** The page images of the works, about
10.5 GB for the three Italian categories alone.

| choice | effect |
|---|---|
| **names and hashes only (recommended)** | every scan verifiable and fetchable later; no page image shown until a node takes the kind |
| take the bytes for the graph's scope | page images beside the text for the selected works; about 10.5 GB for Italian, more per language |

If you say nothing: names and hashes only.

**2. The text's licence acknowledgement.** Wikisource's text is CC BY-SA,
the same as Wikipedia's. The collection needs your acknowledgement of
Wikimedia's terms before its first byte is fetched, as Wikipedia's did. If
you say nothing, nothing is fetched.
