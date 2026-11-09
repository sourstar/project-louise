# PubMed: a record whose only abstract is an OtherAbstract embeds its title alone

*Found 2026-10-19, 01:29, by pubmed-v8's vectors-sample during its switch
([char-cut-truncation.md](char-cut-truncation.md) §5). A decision for the
operator at the end.*

## What was found

One sampled shard (year=1995-1999) missed its quorum: 9 of its 16 rows under
the tolerance (0.999914), all NCBI's MedCPT vectors (`genesis:medcpt-pubmed`),
all title-only rows — "Editorial.", "[Young people first].", "Japan's TFR
plunges to record low of 1.43." — PMIDs 12,287,128–12,321,495.

Their records hold an abstract the origin states: an `OtherAbstract` of type
"PIP" (POPLINE's), kept since parser 8 in `extra.other_abstracts`. But the
record's `sections` are `[Title]` (+ `Abstract` where `<Abstract>` exists),
and the pair layer cuts and embeds the sections: the title alone. NCBI
embedded title AND that abstract. So NCBI's vector is not this space's
function for those records.

## What was done (02 §2.4's remedy, RULE 1)

`tools/pubmed_other_abstract_rows.py` read the snapshot's parts once:
39,994,988 records, **63,921 cut to a title alone beside an other abstract**,
54,548 of them NCBI's rows (the other 9,373 were embedded here, from the
record's own input, and are right). The 54,548, and one more title-only row
of unknown cause (PMID 5158766, no other abstract), were dropped and their
documents marked to embed again (`louise dev drop-rows`, 54,549 rows). They
are re-embedded from their records: the title alone, the space's function.

## The decision (operator)

The space is now right, and those 63,921 documents are found by their title
only, although the origin gives each an abstract. Embedding the other
abstract where there is no `<Abstract>` would give them what NCBI's vectors
had: parser 9 (`sections` gains an "Abstract" from the first other abstract
when `<Abstract>` is absent — or from all of them), so `content_hash` moves
for those records alone, and a new Codex carried by A1c (`--rebuilt`,
`--reconcile`): ~64 k documents embedded again, every other vector kept.
Which other abstracts count (PIP only, every type, the English one first) is
the operator's to say.

## The residual misses: NCBI's texts against ours (2026-10-19)

The POPLINE rows were found by vectors. Every other archive row whose text
differs from our record is found by text: `tools/medcpt_text_compare.py`
fingerprints both sides after the normalisation MedCPT's uncased tokenizer
applies (case, accents, whitespace) and lists the archive's rows that
differ.

Chunk 35 (2023): 995,815 NCBI texts. 957,327 are the same, 22,244 differ
(2.2 %), and 16,244 are not in our table. Of those that differ, 19,483 are
archive rows. A random 12 of them:

- **3 are revisions since NCBI's run:** a retitled paper (35231123,
  35982541, with its abstract rewritten) and a copyright year that moved
  (35925699).
- **9 differ in characters the tokenizer does not fold:** U+2212 minus or
  an em dash where NCBI has `-`, curly vs straight quotes, NCBI's literal
  `&amp;nbsp;` and `&gt;`, and LaTeX quotes.

Every one is a vector of a text that is not the record's, so every one is
embedded again (`dev drop-rows` on `differs.txt`).

All 37 chunks (09:39–11:58, 39,993,653 of our rows fingerprinted): 142,540
texts differ, 0.4 %. Chunk 35 is the worst; the 2 % it suggested was wrong by
five times. Chunks 0–10 and 13–27 differ by 0.01–0.2 %. Chunks 11–12
(6,936 and 27,633) and 28–36 (7,000–22,000 each) differ more. Why those
chunks differ more was not measured; chunk 35's sample is the only breakdown.
Only
**60,947** of the differing rows are an archive's. The rest are rows we
embedded ourselves from our record's text, which are right whatever NCBI
read. Those 60,947 were dropped and their documents reset at 12:00 on
2026-10-19, to be embedded again in the 23:00 pass.

"Not in our table" grows from chunk 19 on, to 16,244 in chunk 35. Those are
PMIDs NCBI embedded that our records do not hold or that are out of scope;
nothing is compared for them.

## Decided: B (operator, 2026-11-08), and how it is built

Put to the operator again on 2026-11-08, when 46 `other-abstract-only` lines
came up in half an hour of pubmed's run: 44 plain-language summaries in
English, one publisher abstract in German and one in French. The operator
chose **B**: every type counts, the English one first, else the first in
document order.

**Parser 9** (`app/plugins/pubmed/source.py`, `section_abstract`): a
citation with no `<Abstract>` takes that OtherAbstract into `sections` as its
"Abstract". The pair layer then gives MedCPT (title, that abstract), which
is what NCBI embedded for the POPLINE rows. Every OtherAbstract stays whole
in `extra.other_abstracts`, and the issue stays flagged, since it is the
origin's shape. Every other citation parses to the same sections, so its
`content_hash` and chunk do not move.

**Parser 8 is kept** (`PubmedSourceV8`, `at_parser_version`,
`collections.plugin_at`). A collection whose Codex names 8 (the live pubmed)
parses at 8 in the worker, in `hold check` and in the equivalence of images.
So deploying parser 9 changes nothing for the live pubmed until it is
re-created. It is removed once no Codex on a node names 8.

**The re-creation (RULE 1, 02 §2.4 A1c, 02 §2.7 amended 2026-11-08).** The
moved lines are named by the line itself (`line_moved_since`): no
"Abstract" section, and an OtherAbstract with text. So the build takes the
live snapshot's 40 M lines and parses only those again:

1. Deploy the image carrying parser 9. Check that the live pubmed's
   `hold check --checks records-vs-origin` still passes under it (it parses
   at 8).
2. Create the new collection from the live pubmed's WHOLE Codex. Its
   parser and its image change; nothing else does: the space, the layers,
   the slicing, the origin, the licence. The members it names (the
   calibration member, LICENSE-TERMS) are copied from the live pubmed and
   checked against the new Codex (`collection.complete_members`, 2026-11-08):

       python -c "import json,sys; c=json.load(open(sys.argv[1])); c['parser']['version']=9; \
         c['toolchain']['platform_image']=sys.argv[2]; c.pop('collection_id',None); \
         json.dump(c,open('/tmp/pubmed9.json','w'))" <root>/pubmed/shard/codex.json <deployed image digest>
       louise hold collection create --recipe /tmp/pubmed9.json --name pubmed9 \
         --encoder <url> --query-encoder <query_url> --api <api> --embed-model <m> --query-model <q>

   The encoder flags are those of the live `collections/pubmed.toml`
   `[encoder]`. The licence needs no new acknowledgement: one is of a terms
   TEXT (04 §4.4), the new Codex pins the same `terms_sha256`, and this
   peer's record of it acknowledges both.
3. Pause pubmed. `louise dev carry pubmed pubmed9 --rebuilt --yes` (with
   `--copy` where reflinks are available). It also copies pubmed's
   `state/ack.json` into pubmed9, so the acknowledgement outlives the old
   collection's forget.
4. `louise hold records build pubmed9 --corpus <pubmed's origin> --records-from pubmed`,
   in its own container under `deploy/supervise-pass.sh` (2026-11-08: run
   by `docker exec` it shared the worker's 24 GiB cap and was OOM-killed
   with it at 3,914 s). The log names how many lines are parsed again:
   measured 24,463 `other-abstract-only` (estimated ~64 k), in 20,073 s for
   41,116,303 records and 1,396 parts. Then `hold records validation
   pubmed9` (the snapshot refuses a SHARD without `validation.json`, 09
   §9.4), then `hold snapshot create`.
5. `louise dev carry pubmed pubmed9 --reconcile --yes`: every document whose
   `content_hash` did not move keeps its vectors, and the moved ones are
   reset to embed again.
6. `hold check pubmed9`: records-vs-origin and vectors-sample, before use.
7. Resume. The worker embeds the moved documents (MedCPT). The old
   collection stays paused, and forgetting it is the operator's call, with
   its size.
