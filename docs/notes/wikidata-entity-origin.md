# Wikidata's origin by entity: the re-creation at parser 3 (runbook for München)

*2026-11-17. Operator: "Choice B", then "Do the wikidata step as well" (docs/notes/collection-updates.md
§6). Branch `wikidata-entity-origin`, stacked on `wikipedia-page-origin`; the mechanism is
[wikipedia's](wikipedia-page-origin.md) (app/keyed_origin.py). NOTHING HERE RUNS WITHOUT THE OPERATOR'S
WORD. Every deletion is named with its size and is the operator's yes, given in München's session.*

## What changes, and what is carried

```
wikidatawiki/entities/20261012/wikidata-20261012-all.json.bz2#123456
  becomes
wikidatawiki/entities/all.json.bz2#Q42
```

The entity is named by its id and read in the snapshot's run through the run's ENTITY TABLE. The node
builds the table from the run's pack in one pass: entity id to the line's ordinal, a side table in the
pack's directory, ~690 MB for 118 M entities. Parser 3, so a new Codex: a re-creation (RULE 1; nothing
official was published from wikidata). Nothing else in a line moves. The parse reads the same pack and
labels.

| what | how |
|---|---|
| the record parts (~5,500 of ~20,000 entities, cut by content) | `hold records carry`: each line's `origin` rewritten in the bytes, every other byte taken; each part held to the old one (uids, `records_root`, counts) and to the content cut a build by the book makes |
| the snapshot | the same id as the old one, frozen from the carried parts: a build's by the book (the mechanism's tests, and tests/test_wikidata_entity_origin.py line for line and part for part) |
| manifest, text index, vector table | `dev carry --rebuilt`, MOVED |
| the vectors' reconcile | from the carry's proof: 0 to embed again |
| `space_id` | unchanged (the parser is not part of it, 09 §9.3) |
| annotations collections that ANNOTATE wikidata | created again from their own claims against the new collection, every target named (step 7) |
| methods with wikidata among their INPUTS (gutenberg-editions) | nothing: an input is a plugin, so the new collection is read (cascade.py) |

**The monthly update stays refused**, by its own reason. The origin's signal that an entity dump's run is
complete is not verified (collection-updates.md §6 names the candidates). The first probe from the node
settles it, and it is the operator's to approve before the hook is written.

## Sizes and times

The node's own numbers come from the dry runs. These estimates use wikipedia's measured carry rate:
224 ms a part at 1 process, 105 ms at 3, for parts of 1.45 MiB. Wikidata's parts are larger (20,000
entities each), so scale by their bytes, which `--dry-run` prints.

- **The entity table:** 12 M entities in 2.8 s here (threads), so ~30 s for 118 M. It is built at the
  first lookup (records-vs-origin) and is ~690 MB in the pack's directory.
- **Peak extra space:** the new parts (wikidata's latest snapshot's bytes, printed by the dry run), plus
  the new snapshot file, plus the table, plus the claims export of each annotations collection that
  annotates wikidata.

## The steps

**0. Before anything** (read only):

```bash
df -h /data /shards "$LOUISE_CACHE_DIR" "$LOUISE_FILES_CACHE_DIR"
louise hold collection show wikidata          # parser 2, its latest snapshot id S, its parts
louise hold collection list                   # the annotations collections; `show` each to see whether it annotates wikidata
```

**1. Deploy** the image carrying parser 3. Then re-check the live collection; it parses at 2:

```bash
louise hold check wikidata --checks records-vs-origin --origin <wikidata's corpus>
```

**2. The new Codex**, the live one with the parser and the image changed:

```bash
python -c "import json,sys; c=json.load(open(sys.argv[1])); c['parser']['version']=3; \
  c['toolchain']['platform_image']=sys.argv[2]; c.pop('collection_id',None); \
  json.dump(c,open('/tmp/wikidata3.json','w'))" /data/collections/wikidata/shard/codex.json <deployed image digest>
louise hold collection create --recipe /tmp/wikidata3.json --name wikidata.next \
  --encoder <url> --api <api> --embed-model <model>        # the [encoder] of collections/wikidata.toml
```

The family map member (`slicing/<sha256>.json`) and the calibration member are copied from wikidata
(`complete_members`).

**3. Pause** both, then **4. carry the manifest, text index and vector table** (moved):

```bash
louise node pause wikidata
louise node pause wikidata.next
louise dev carry wikidata wikidata.next --rebuilt
louise dev carry wikidata wikidata.next --rebuilt --yes
```

STOP if it names any difference but `parser` and `toolchain.platform_image`.

**5. The parts** (its own container, `deploy/supervise-pass.sh`, the night profile):

```bash
louise hold records carry wikidata.next --from wikidata --dry-run
louise hold records carry wikidata.next --from wikidata
```

Each part is checked against the content cut as it is rewritten. A part that does not end where the rule
ends it refuses the carry, names the part and writes nothing. The way round it is then `records build
wikidata.next --records-from wikidata`, which takes the same lines. The carry ends with snapshot S of
wikidata.next, signed.

**6. The vectors and the checks:**

```bash
louise dev carry wikidata wikidata.next --reconcile --yes          # 0 to embed again
louise hold check wikidata.next --checks roots,records,chunks,records-vs-origin,vectors-sample \
  --origin <wikidata's corpus>
```

The first lookup builds the run's entity table, ~30 s at 118 M entities. It needs the run's pack, which is
already there. Expect records-vs-origin to pass with 0 failed.

**7. Each annotations collection that annotates wikidata** (wiki-gutenberg, if held: gutenberg and
wikidata) is created again, every target named. The claims must be pinned to wikidata's S; `hold
annotations derive <c> --dry-run` or its `show` says so, and if they are not, `hold annotations update`
comes first:

```bash
louise hold annotations claims <c> --out /data/tmp/<c>.claims.jsonl
louise hold collection create annotations --name <c>.next --annotates gutenberg --from-snapshot <its pin> \
  --annotates wikidata.next --from-snapshot S --snapshot <c's latest snapshot id> \
  --claims /data/tmp/<c>.claims.jsonl --dry-run
# then the same without --dry-run, and `hold config apply <c>.next --set method=<its method>` if it has one
```

**8. The forgets**, the operator's yes with the sizes their dry runs print:

```bash
louise hold collection forget wikidata --dry-run        # the old parts and snapshots; the corpus and the pack KEPT
louise hold collection forget <c> --dry-run
```

**9. The names and resume:**

```bash
louise hold collection rename wikidata.next wikidata
louise hold collection rename <c>.next <c>
louise node resume wikidata
louise hold config apply wikidata --set embed=<the mode it had>
louise hold collection update wikidata --dry-run   # refused, naming the unverified completion signal (by design)
```
