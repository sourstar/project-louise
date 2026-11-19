# Project Gutenberg ebook IDs (P2034) for Wikidata: the batch (2026-11-19)

*Operator, 2026-11-19: "Do 1", give back to Wikidata. Made by
`tools/measure/pg_wikidata_match.py` from `pg_catalog.csv` (2026-11-15) and
the Wikidata Query Service (2026-11-19). **Nothing has been sent to
Wikidata.** The operator runs the batch under their own account.*

**4,302 new statements on 3,510 work items** (en 3,366, fr 389, de 354,
es 115, it 78). The files:
- `quickstatements.txt`: one line per statement, `Q…<TAB>P2034<TAB>"N"`.
- `matches.csv`: the evidence for each line (ebook, language, title,
  Gutenberg author, author item, work item, the P2034 values the item
  already has).

How a line was made, precision before recall:
- **The author:** a Wikidata person with a Gutenberg author ID (P1938).
  The label holds the surname, and the birth and death years equal the
  catalogue's. Exactly one, or no line.
- **The work:** an item by that author (P50) whose label in the ebook's
  language is exactly the title, cut at its first subtitle mark. Exactly
  one, or no line.
- **Never an edition or a series.** The first pass's hand check (25
  lines) found a modern publisher's 2026 edition and a book series among
  the targets, so items typed as an edition/translation or as a series
  are excluded.

| outcome | ebooks |
|---|---|
| new statement | 4,302 |
| already on Wikidata | 1,225 |
| several candidate works (left out) | 347 |
| no work found | 42,777 |

**Checked by hand:** 30 random lines of the final batch. All 30 target
the right work item (title, author and Wikidata's own description agree).
Two conventions show:
- A translation's ebook goes on the original work (*Jim Harrison,
  boxeur* on Conan Doyle's *Rodney Stone*), as is usual for P2034.
- A multi-volume ebook set (Pepys's diary in 69 files) gives several
  values on one work.

**How to run it:**
- Before running: 4,302 edits are a large batch. Wikidata asks that bulk
  imports be announced first: Wikidata:Project chat or WikiProject Books,
  with a link to `matches.csv`.
- QuickStatements (https://quickstatements.toolforge.org), logged in
  with the operator's Wikidata account: New batch, Version 1 format,
  paste `quickstatements.txt`, Import, then Run.
- QuickStatements runs it at its own paced rate.
