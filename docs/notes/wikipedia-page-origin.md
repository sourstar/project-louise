# Wikipedia's origin by page: the re-creation at parser 6 (runbook for München)

*2026-11-17. Operator: "Choice B" (docs/notes/collection-updates.md §6). Branch `wikipedia-page-origin`.
NOTHING HERE RUNS WITHOUT THE OPERATOR'S WORD. Every deletion is named with its size and is the
operator's yes, given in München's session.*

## What changes, and what is carried

A record's `origin` names the PAGE, read in the snapshot's run through Wikimedia's own indexes (09 §9.5,
amended):

```
itwiki/20260801/itwiki-20260801-pages-articles-multistream.xml.bz2#4414568085::4414664693|itwiki/20260801/itwiki-20260801-page_props.sql.gz#265993916::265994007
  becomes
itwiki/pages-articles-multistream.xml.bz2#<page id>|itwiki/page_props.sql.gz#<page id>
```

This is parser 6, so the Codex is a new one: a re-creation (RULE 1; nothing official was published from
wikipedia). Nothing else in a line moves.

| what | how | cost |
|---|---|---|
| the record parts (26,551, 33.0 GiB) | `hold records carry`: each line's `origin` rewritten in the bytes, every other byte taken; each part held to the old one (uids, `records_root`, counts) | new parts, ~33 GiB written |
| the snapshot | the same id as the old one, frozen from the carried parts; byte for byte the one a build by the book makes (tests/hold/test_line_carry.py) | minutes |
| manifest, text index, vector table | `dev carry --rebuilt`, MOVED (no copy) | no space |
| the vectors' reconcile | `dev carry --reconcile` reads the carry's proof: every content hash is the old one, 0 to embed again | seconds |
| `space_id` | unchanged: the parser is not part of it (09 §9.3; test `test_the_space_does_not_move`) | — |
| `wikipedia-articles` (12.3 M claims) | created again from its own snapshot's claims, against the new collection (`create annotations`, by the book) | ~20-30 min, ~4 GB export |

The live Codex keeps parser 5 (`WikipediaSourceV5`) until the swap. The deploy changes nothing for it, and
the update pass refuses it: "re-create at parser 6 first".

## Measured here, and the node's estimates

This sandbox has 4 cores. The parts are synthetic and wikipedia-shaped: 2,000 records each, 1.45 MiB stored
and 8.9 MiB of lines per part. The node's parts average 1.27 MiB stored.

| step | measured | node estimate (26,551 parts) |
|---|---|---|
| carry, 1 process | 224 ms a part | ~99 min |
| carry, 2 processes | 137 ms a part | ~61 min |
| carry, 3 processes | 105 ms a part | ~46 min |
| carry, the night profile (12 cores) | — | ~15-20 min, if it scales as 1 → 3 did; the drive may bound it first |
| index table, one wiki of 2 M lines | 5.0 s (read 4.1 s) | enwiki's ~24 M lines: ~1 min; built at the first lookup |
| lookup | 1.13 ms with a row-group read; 88 µs once held | — |

**Peak extra space: ~40 GiB on the SHARD's pool.** That is the new parts (~33 GiB), the new snapshot file
(~1.3 GB: its delta lists every record), the claims export (~4 GB) and wikipedia-articles' new parts.
Add ~0.2 GB of index tables under `LOUISE_CACHE_DIR`. The old parts free ~33 GiB at step 11's forget.

## The steps

**0. Before anything** (read only):

```bash
df -h /data /shards "$LOUISE_CACHE_DIR"              # need >= 45 GiB free where the SHARDs live
louise hold collection show wikipedia                 # parser 5, its latest snapshot id S, 26,551 parts
louise hold annotations derive wikipedia-articles --dry-run   # must say "pinned to wikipedia S"
```

If wikipedia-articles is not pinned to S, the operator decides first. The usual answer is `hold annotations
update wikipedia-articles` against S, before step 8.

**1. Deploy** the image carrying parser 6 (the operator's word). Then re-check the live collection:

```bash
louise hold check wikipedia --checks records-vs-origin --origin <wikipedia's corpus>
```

It must pass, because it parses at 5. Expect ~30 min (10-30's hand check took 30.5 min for all eight
checks).

**2. The new Codex**, the live one with the parser and the image changed. Nothing else changes:

```bash
python -c "import json,sys; c=json.load(open(sys.argv[1])); c['parser']['version']=6; \
  c['toolchain']['platform_image']=sys.argv[2]; c.pop('collection_id',None); \
  json.dump(c,open('/tmp/wikipedia6.json','w'))" /data/collections/wikipedia/shard/codex.json <deployed image digest>
louise hold collection create --recipe /tmp/wikipedia6.json --name wikipedia.next \
  --encoder <url> --api <api> --embed-model <model>        # the [encoder] of collections/wikipedia.toml
```

It prints the new id. The calibration member and LICENSE-TERMS are copied from wikipedia
(`complete_members`). The terms text is the same, so the acknowledgement is carried at step 4.

**3. Pause** both:

```bash
louise node pause wikipedia
louise node pause wikipedia.next
```

**4. Carry the manifest, text index and vector table (moved, not copied):**

```bash
louise dev carry wikipedia wikipedia.next --rebuilt           # the dry run: what moves, re-pinned to the same space
louise dev carry wikipedia wikipedia.next --rebuilt --yes
```

It must say the spaces are the same. A refusal naming anything but `parser` and
`toolchain.platform_image` means STOP. It also links wikipedia's origin and copies `state/ack.json`.

**5. The parts** (in its own container under `deploy/supervise-pass.sh`, at the night profile):

```bash
louise hold records carry wikipedia.next --from wikipedia --dry-run   # 26,551 parts, 52.9 M records, 33.0 GiB, the free space
louise hold records carry wikipedia.next --from wikipedia
```

Expect ~15-46 min (above). A log line comes every 500 parts. A stop exits 130 and a rerun resumes at the
part. A refusal exits 2 and writes nothing. If the parts are not cut as a build by the book cuts them, it
names the parts and says to use `hold records build wikipedia.next --records-from wikipedia` instead, which
is slower: it re-chunks every record. It ends with snapshot S of wikipedia.next, signed with the node key,
and `state/line-carry.json`. The log names an example origin, before and after.

**6. The vectors:**

```bash
louise dev carry wikipedia wikipedia.next --reconcile --yes
```

Expect "52.9 M document(s) keep their vectors … the line carry proved … 0 to embed again", in seconds.

**7. The checks, before use:**

```bash
louise hold check wikipedia.next --checks roots,records,chunks,records-vs-origin,vectors-sample \
  --origin <wikipedia's corpus>
```

Expect records-vs-origin to pass with 0 failed. The first lookup of each wiki builds its index table
(~1 min for enwiki). Expect vectors-sample to pass at the space's tolerance. Then compare the two
snapshots' parts. Every `records_root`, `chunks_root`, count and first/last id is equal; only `root` and
`bytes` differ (the carry already held each part to this).

**8. wikipedia-articles follows** the new collection. Its Codex names wikipedia's collection_id, so it is
created again:

```bash
louise hold annotations claims wikipedia-articles --out /data/tmp/wikipedia-articles.claims.jsonl   # ~12.3 M lines, ~4 GB
louise hold collection create annotations --name wikipedia-articles.next --annotates wikipedia.next \
  --from-snapshot S --snapshot <wikipedia-articles' latest snapshot id> \
  --claims /data/tmp/wikipedia-articles.claims.jsonl --dry-run
louise hold collection create annotations --name wikipedia-articles.next --annotates wikipedia.next \
  --from-snapshot S --snapshot <wikipedia-articles' latest snapshot id> \
  --claims /data/tmp/wikipedia-articles.claims.jsonl
louise hold config apply wikipedia-articles.next --set method=wikipedia-versions
```

Every end is read in wikipedia.next's S and every pin is checked: by the book. A claim pinned elsewhere
refuses the file, naming the line. Expect ~20-30 min (the delta measurement's model gives ~19 min on 3
processes for a whole pass). The export may be deleted afterwards (~4 GB, the operator's yes). Then:

```bash
louise hold annotations derive wikipedia-articles.next --dry-run    # "not due", pinned to wikipedia.next S
louise hold collection show wikipedia.next                           # "woke … " lines: the cascade declares it
```

**9. The forgets** are the operator's yes, with the sizes their dry runs print:

```bash
louise hold collection forget wikipedia --dry-run              # its old parts (~33 GiB) and snapshots; the corpus must be KEPT (wikipedia.next reads it)
louise hold collection forget wikipedia-articles --dry-run     # its parts and claims file (~4 GB+)
```

Then the same commands with `--yes`, on the operator's word.

**10. The names:**

```bash
louise hold collection rename wikipedia.next wikipedia --dry-run
louise hold collection rename wikipedia.next wikipedia
louise hold collection rename wikipedia-articles.next wikipedia-articles --dry-run
louise hold collection rename wikipedia-articles.next wikipedia-articles
```

**11. Resume:**

```bash
louise node resume wikipedia
louise hold config apply wikipedia --set embed=<the mode it had>
louise hold collection update wikipedia --dry-run     # planned, not refused: "update … from Wikimedia's … run"
```

## After

The next monthly run re-issues only the parts whose pages changed. An unchanged page's line is
byte-identical (test `test_the_whole_update`). Read the first update's step timings off `state/update.json`
into the scheduling log, with the parts re-issued against 26,551.

**Wikidata is next.** It has the same problem, and its JSON dump has no index (collection-updates.md §6).
