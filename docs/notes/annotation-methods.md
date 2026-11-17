# Annotation methods: the node updates its own derived annotations

*2026-11-12. Operator, through München: "Can the annotations be updated without manually uploading something???"
(RULE 2). Code: `app/hold/annotation_methods.py`; the worker's claim index keeper; `hold annotations derive`.*

## What changed

An annotations collection whose claims are computed only from collections the node holds now names that
computation, its **method**, in its collection file:

```toml
method = "wikipedia-versions"
```

When the collection it annotates holds a **newer snapshot** than the one the claims are pinned to, the worker
re-runs the method and then runs `update` with the method's whole output. The result is the next snapshot of the
same collection, signed with the node key. Nobody uploads anything.

| Step | What happens |
|---|---|
| Due check, each keeper pass (≤ 300 s) | The target's newest snapshot (a directory listing) against the claims' pin: `state/method.json`, or else the first claim's `target_snapshot`. One update a period (09 §9.4): if this period's snapshot is already written, it waits for the next and says so once. |
| Method, in a child process | Writes the whole claim set into a workspace the worker holds (`.annotations-method.<name>`). |
| Update, in a second child | `unheld="drop"`: a claim whose end the target's snapshot does not hold yet is set aside and named, not refused. The method reads the live manifest, which runs ahead of the snapshot. The claims file is hard-linked into place, not copied (RULE 1; wikipedia's is 4.2 GB). |
| Marker | `state/method.json`: method, snapshot, `against`, counts. |

Two methods exist. **`wikipedia-versions`** reads the wikipedia collection's manifest and page_props, which makes it a
pure function of what the node holds. **`gutenberg-editions`** (2026-11-17, below) re-makes gutenberg-book-editions
every month from the node's gutenberg, wikipedia and wikidata. The classical matcher's claims depend on a language
model's intermediates; they are not a method, and that method carries them as they stand.

## The Library's checkbox, and what it guards (2026-11-13)

The operator: "wikipedia-articles which command? I want a checkbox in the gui". An annotations card shows **update by
itself when wikipedia holds a newer snapshot** for each method that can make its claims. The box is ticked from the
file's `method`. Ticking it writes the key through the file's one writer (`POST /admin/library/<name>/method`), as
`hold config apply <name> --set method=…` does. The card also shows the last derivation from `state/method.json`.

The method's output becomes the **whole** claim set, so ticking it on a collection of other claims would withdraw them.
`annotation_methods.not_fitting` therefore decides where the box appears, and the file's writer and the worker's due
check refuse the same cases. The method fits when either:

- it made the current snapshot (`state/method.json`); or
- the first claim of up to 64 parts spread over the current snapshot has the method's `types` and claim `method`
  (`wikipedia-versions`: `same-work` by `{"kind": "wikidata"}`).

That is a sample, cached per snapshot. A collection that mixes such claims with others in every sampled part would pass.
No collection is built that way: each annotations collection is made by one tool. Not defaulted on (RULE 2 weighed): the
key's absence is what says "claims given by hand", and a hand-made collection that happens to fit must not start
re-deriving by itself.

`hold config apply <annotations collection> --set method=…` wrote the file and then said "no such collection" (exit 2).
`config apply` and `config show` now list annotations collections. They have nothing to apply in a state database; the
worker reads the file at its next annotations pass.

## Where the method is recorded, and why

The method goes in the collection file, not the Codex. The file is node-local desired state (invariant 10), so only
the node that publishes the collection re-derives it. A peer that adopted the collection takes the publisher's
snapshots as it takes any. Putting the method in the Codex would be a wire-format change. It would also move
`collection_id`, which would make wikipedia-articles a new collection. If the method should travel (a peer
reproducing the claims to check them), the place is a Codex field at the next annotations version. That is the
operator's call when such a peer exists.

## Failure, stop, memory

- **Memory.** Each step runs in a spawned child. Measured on the node on 2026-11-11: the method peaks at 6.81 GiB
  and the update at 2.7 GiB. Both peaks end with their child, beside the worker's 1.6–3.8 GB under its 24 GiB cap.
  The node key is read only in the update's child.
- **Stop.** A stop terminates the child at once (exit 130). The only exception is the update's last writes, the
  snapshot and its signature, which a SIGTERM handler lets finish. The handler is per process because a per-thread
  mask does not reach numpy's BLAS threads (the first test of this died mid-block). Everything before those writes
  is resumable, as `update` already was. The snapshot is now written to a temporary file and renamed: a write cut
  short used to leave a file that every later update refused as "different bytes".
- **Failures** are logged once per (collection, method, target snapshot). A child killed by a signal (the OOM killer)
  is retried at the next pass, three times. `hold annotations derive <c> [--dry-run]` runs the same thing by hand,
  or says whether it is due and why.

## Speed

`wiki_versions.claim_lines` renders the claims from one template whose constant parts come from `json.dumps`. It is
byte for byte the old `json.dumps(claim, sort_keys=True)` per line (test), and ~8× faster: 200 k claims in 0.23 s
against 1.87 s. That is about 15 s instead of ~115 s of one core for wikipedia's 12.3 M. The tool
`tools/annotations_from_wikipedia.py` uses it too.

## For München (on the operator's word, after the deploy)

The collection is `wikipedia-articles` on the node (the rename to `-cache` planned in #570 has not been made; after
it, the new name).

1. `louise hold config apply wikipedia-articles --set method=wikipedia-versions` (done 2026-11-13; or the card's
   checkbox after the next web deploy)
2. `louise hold annotations derive wikipedia-articles --dry-run`. Expected: not due, "pinned to wikipedia
   <its snapshot>".
3. Nothing else. The worker derives once wikipedia's next snapshot is written. Its log line names the counts. Time
   the first one (method and update wall times, peak RSS of each child).

## gutenberg-editions: gutenberg-book-editions re-made every month (2026-11-17)

The operator: the collection must stop depending on hand-run tools and web APIs. The node re-derives it by itself,
monthly, "as if a monthly Wikipedia snapshot were downloaded", from its own collections: "if there is a local wikipedia
collection it is pointless to download a wikipedia snapshot; peers can always check against the snapshot." Nothing is
fetched from the network while it runs. Code: `app/gutenberg_editions.py`, `app/wikipedia_titles.py`,
`app/gutenberg_titles.py` (the same-title rule, moved out of its tool unchanged).

Turn it on with `method = "gutenberg-editions"` in the collection file, or the card's checkbox **update by itself every
month from the node's Gutenberg, Wikipedia and Wikidata**.

**What it writes.** The collection's whole claim set, tier by tier:

| Tier | Made from | When |
|---|---|---|
| Wikidata | Each book's "Wikipedia page about this book" links, looked up in the node's wikipedia, then the page's Wikidata item. A book naming two items (an anthology) claims nothing. A work is a star on its lowest ebook number. | Always |
| Same title | gutenberg's records, by the rule of 2026-10-22 | Only if the current snapshot holds such claims |
| Language stage | gutenberg's records, by the historical-markers matcher | Only if the current snapshot holds such claims |
| Everything else | Carried: its line of the claims file, byte for byte (the classical matcher's claims, the editions notes, a person's) | Always |

A claim that did not change is written as its current line, so it keeps its uid and its bytes. The same inputs give the
same file.

**How a title is found locally.** The link's title is normalised as the Wikipedia API normalises it: underscores become
spaces, extra spaces go, the first letter is upper-cased. A redirect is followed to its target, up to three steps. The
item comes from the page's page_props, as its record holds it. Where the node holds a wikidata collection, the item must
be in it. One pass over the wikipedia manifest finds every title: it keeps 8 bytes a page (423 MB for 52.9 M pages),
sorted, so a redirect's target costs a lookup and not a second pass. Only redirect pages are read from the dump.

**What the node cannot answer**, where the API could:

- a link to a Wikipedia the node does not hold (it holds 11 languages; Gutenberg also links fi, nl, sv, la and others);
- a title the run's own index lists, on a page the manifest holds no row of ("held by the run, not read yet"), and
  any title the manifest misses when the run's dump or index is not on disk ("no dump to tell"). A title is "no page"
  only when the run's index does not list it (below, 2026-11-17);
- an item that the page names and the node's wikidata lacks (merged or deleted since). The dump lists no redirects, so
  a merged item is seen but not followed.

In each case the book keeps the item that its current claim gives it through that same link. A book that has no claim
yet claims nothing. The dry run counts both. It never reads "the node cannot tell" as "no item", so a gap in the node's
collections does not withdraw anything. Nor does a run older than the claim, whatever it answers.

**When it runs.** Once a period, not when gutenberg moves. It is due when this collection's next snapshot can be written
(one update a period, 09 §9.4) and the method has not run in the period open now. A run that has nothing to write is not
repeated in its period. `state/method.json` records the period, whether a snapshot was written, and in `against` the
snapshot ids of everything read: gutenberg, wikipedia and wikidata. The method declares wikipedia and wikidata as its
inputs, for the cascade of docs/notes/collection-updates.md §4.

**The dry run.** `louise hold annotations derive gutenberg-book-editions --dry-run` says whether it is due. It also
shows what a run would change, per tier: re-derived, kept identical, changed, added, withdrawn, carried, set aside
(naming a record gutenberg's snapshot no longer holds). It lists up to 20 added and 20 withdrawn claims per tier, and
counts how the titles were answered. Nothing is written.

**Cost**, measured on synthetic fixtures in a 4-core sandbox (2026-11-17):

| Fixture | 1 process | 2 | 3 |
|---|---|---|---|
| The manifest pass, 3 M rows (1.8 GiB) | 6.0 s | 4.3 s | 2.9 s |
| The whole run: a 2 M-row manifest (1.1 GiB), 20,000 gutenberg records in 8 parts, all three tiers, 4,658 titles (425 through a redirect) | 9.8 s | | 11.5 s |

At 2 M rows the pool's start-up outweighs what it saves; at 3 M it halves the pass. Peak memory of the whole run was
335 MiB for the parent and 320 MiB for the largest child. The pass is linear: 52.9 M rows is about 53 s on 3
processes. On 12 the disk is the bound (about 32 GiB to read at this fixture's row size), so expect about a minute. The
sorted index adds about 400 MiB to the parent. The node's real time is to be read off its first run.

**For München** (after the deploy, on the operator's word):

1. `louise hold annotations derive gutenberg-book-editions --dry-run`. Read the tiers. A large `withdrawn` under
   wikidata, or many "not answerable here" titles, is a reason to stop and look before ticking the box.
2. `louise hold config apply gutenberg-book-editions --set method=gutenberg-editions`, or the card's checkbox.
3. Nothing else. The worker runs it in the next open period and logs its counts.

Open:

- The editions-note claims (`gutenberg-editions-note`) are carried until they are withdrawn by hand, as decision 1 A
  planned. The method does not withdraw them.
- A claim withdrawn by hand from the Wikidata or same-title tier is made again. The tools kept no list of such
  exclusions, so none is applied. The classical matcher's exclusions stay applied, because its claims are carried.

## From the delta (2026-11-17)

Every derivation's update is now re-pinned from the delta. An end record that the
target's snapshots since the claims' pin did not change keeps the hash the claims
already state. Only where that can pay: docs/notes/collection-updates.md §4 "As
built" gives the rule and the numbers. The bytes are the whole read's, and
`finish(since=None)` still does the whole read.

- **`Method.delta`** is the optional delta producer, and `WholeRun` is its
  refusal.
- **`whole_because`** says why a method runs whole.
- **`derive_from_delta(root, name, method_name, deltas)`** is the cascade's entry
  point. `method_name` None means claims given by hand, which are re-checked. It
  runs in children, as `derive_isolated` does.
- **Neither method has a delta producer.** Their inputs (live manifests, page_props,
  redirects) are not in any snapshot's delta.
- **gutenberg-editions' gutenberg read** goes through a part cache keyed on each
  part's root.

## In the cascade (2026-11-17)

The due check is one of the three triggers of the annotations cascade's round (app/hold/cascade_keeper.py,
docs/notes/collection-updates.md §4). The other two are a declared source's published snapshot and a wake still
pending. `maintain` is that round for the collections with a method, and the worker runs the whole round. A method's
`inputs` are declarations, so `hold config apply --set method=…` and the Library's box refuse one that closes a
cycle, and name the loop.

## "No page" only on proof (2026-11-17, the node's first dry run)

The node's dry run withdrew 83 wikidata-tier claims and added 19. Real editions were among them. Eight books linked
through "Journey_to_the_Centre_of_the_Earth" lost their claims against gutenberg:3748, and two linked through
"When_the_Sleeper_Wakes" lost theirs against 775. Both titles are enwiki redirects, and the manifest holds no row for
either. The Bouvard and Pécuchet and the Britannica stars went whole for the same reason. Layer 1 holds those articles
under the archive's 2024 titles ("Bouvard et Pécuchet", page 1078856), and the links use today's titles. It was not a
title-form fault: titles are stored NFC as raw UTF-8, and a fixture with é and æ titles in both layers resolves. The
resolver answered "no page" whenever the manifest missed a title. `found_of` then dropped the book's item, and the
claim went. That broke the method's own rule: what the node cannot answer is never read as "no item".

**What changed** (app/wikipedia_titles.py). THE RUN IS AUTHORITATIVE FOR TITLES. Where a wiki's run has its
multistream index on disk, every wanted title of that wiki is looked up there. The index lists every page of the run,
redirects included. It is one pass per language, with set membership, and the languages run in parallel on the parse
pool. The page is then read from the manifest by its id, in either layer, through the primary key:

| The run's index | Answer |
|---|---|
| does not list the title | NONE, "no page" |
| lists it, and the manifest holds the page with text | its item; `by page id` where the manifest holds it under another title, that title said |
| lists it, and it is a redirect | the page read from the run's dump at the index's offset (the plugin's `redirect_of`, read-only), then followed within MAX_HOPS and the loop rule |
| lists it, not a redirect, held without text | its item |
| lists it, not a redirect, and no row holds it | UNKNOWN, "held by the run, not read yet" |

A 2024 title the run now gives to another page answers with that page, never with the one layer 1 held under it. The
manifest's titles are the fallback, only for a wiki whose index is not on disk. There a found title answers as before,
and a missing one is UNKNOWN, "no dump to tell". An index cut short falls back the same way, and its misses are
"unreadable". The manifest's title pass, 52.9 M rows and 423 MB, is made only for a fallback wiki. The old
untitled-rows rule is gone. The index's titles are matched both as stored and in the dump's XML-escaped form, so a
title with `&` or `'` is found whichever form the index writes.

**Evidence older than the claim** (app/gutenberg_editions.py `claim_times`, `made_after`). Nothing records when a
claim was made. 09 §9.5's claim has no date key, and its record's `date` is null. The contributor's "…, 2026-10-09" is
text, not a field. A snapshot's id is its period, not its writing: `create` names the period that closed, and an update
can catch up on an old one. So a claim is dated by the first snapshot holding it, as its signature's earliest time. A
claim keeps its uid across updates, so that snapshot is the first whose delta adds the uid. A run cut on or before
that day never overrides the claim, in either direction. A "no item" (no page, no item, a loop) and another item are
both no evidence: the claim is kept through its URL. A claim of unknown date, or a run of unknown date, is treated the
same way. On the node, the run is 20260801 and the tools' claims of 2026-10-09 came after it. Until the node holds a
later run, that run changes none of them.

**The dry run** says it. `by_reason` counts the new codes. `no_page_titles` lists every "no page" title (at most 100),
each with up to five of its books. `by_page_id_titles` lists up to 20 titles the manifest holds under another title,
with that title. `found_by_page_id` and `runs` are counted. Under `books`, "kept: run older than the claim (no
page)" and "kept: run older than the claim (other item)" show how many the second rule kept. The first counts every
NONE: no page, no item, a loop.

**Star stability.** The centre is the lowest ebook number among the books whose links name that one item. It depends
on the membership alone, in any order. An unchanged membership gives the same claims byte for byte, including the URL
each book names. A member that leaves takes only its own claim. When the centre leaves, the star is re-centred and every
claim in it is new. That is how the node got 83 withdrawn against 19 added.

**Where the redirects went** is München's question, not answered here. `louise dev wikipedia-coverage [--lang en]
[--samples N]` (app/wikipedia_coverage.py) is read-only and writes nothing. Per wiki, it compares the run's index page
ids among the articles (discovery's namespace rule) with the rows of both layers. It reports how many no row holds and
how many of those the archive covers. A covered page is one the dump pass skips, so with no archive row it is held by
neither layer. It also reports the rows the run does not list, and a seeded sample with titles. Discovery is
unchanged. If records are lost, that is a separate fix.

**Cost**, measured on synthetic indexes in a 4-core sandbox (2026-11-17). Titles of one to six words, 46 bytes a line:

| Index | bz2 | Decompression alone | `look_up`, 550 titles | `index_pages` (the check) |
|---|---|---|---|---|
| 5 M lines (229 MB) | 46 MB | 8.0 s | 8.7 s, 105 MiB | 8.5 s |
| 24 M lines (1.13 GB) | 223 MB | 40.9 s | 43.7 s, 111 MiB | 43.8 s, 701 MiB |

A pass is bz2 decompression. A 16 MB slice from the middle of the live enwiki index holds no stream start, so the file
is one stream and one wiki is one core; the wikis run in parallel. The decompression runs in a thread fed 1 MiB
chunks, at a 0.1 ms GIL switch interval. With `bz2.open` and the default interval, `look_up` on 5 M lines took
10.3 s. enwiki's index of the pinned run is 283.6 MB bz2, 1.27 times the 24 M fixture, so expect about 55 s for a
pass. The coverage check's manifest pass read 3 M rows (961 MiB) in 6.4 s on one process and 3.8 s on three. That is
about 70 s for 52.9 M rows on three, and the index passes run beside it.

**One pass a wiki, every hop a lookup.** Index first, a redirect's target is looked up in the run's index again: a
second full pass per language for every round, about 55 s each on enwiki. So the pass keeps a table instead
(`RunIndex`): per line, the 64-bit hash of its title (Python's own, in the child that built it) and its page id; per
stream, its first line and its offset. That is 12 bytes a line. Each wiki is then resolved whole in its own pool task
(`resolve_by_run`): the pass, every round's lookups in the table, the pages by id, the redirects read from the dump in
the dump's order. On the 24 M-line fixture the pass that builds the table took 44.2 s, against 43.7 s for set
membership alone (40.9 s of either is decompression). The table holds 278 MiB, at 444 MiB peak RSS. A lookup round of
8,000 titles takes 1.32 s.

**Index first against manifest first** was measured end to end on one synthetic run. It has 5 M pages and 400 of them
are redirects to articles, with no manifest row, as on the node. The manifest is 2,003 MiB and the index holds 100 pages
a stream. There are 8,200 wanted titles: 7,000 articles, the 400 redirects and 800 absent titles. Manifest first is
commit 0b6e182, index first is this one:

| | 1 process | 3 processes |
|---|---|---|
| Manifest first: the title pass, then the index for the 1,200 missed | 26.1 s (7.6 s of it the index) | 15.6 s |
| Index first: one pass, 2 rounds in its table, 7,400 pages by id | 11.2 s (0.29 s of it by id) | 10.2 s |

Each page-id lookup is a uid read on the primary key (`UID_SQL`, plan-checked): two uids a page, one for each layer,
500 a query. The fixture's redirect streams hold one page each, so its 400 dump reads cost nothing. A real stream of
100 pages costs tens of ms to read, and those reads run one after another in the wiki's task: allow about 15 s for 500
redirects on enwiki. On the node, manifest first was the 52.9 M-row pass, about a minute on three processes, then
enwiki's index pass, about 55 s. Index first is enwiki's pass and its reads, about 70 s, with the other wikis beside
it. The 423 MB title index is no longer built unless a wiki has no index on disk.

Open: the coverage check's numbers on the node (the redirects' rows).

## Standardised alternative, not taken

A workflow engine's derived datasets (Airflow datasets, Dagster assets) re-run a step when an input's version moves.
Adopting one would add a service beside the worker for a single trigger, the target's snapshot id, which the worker
already reads. It would pay once several derivations chain.
