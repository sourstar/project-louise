# Project Gutenberg catalogue: proposed corrections (DRAFT, 2026-11-18)

*Not sent. Found while choosing wiki-gutenberg's memoirs
(wiki-gutenberg-claude-plan.md §10). Source: `pg_catalog.csv`
(aleph.pglaf.org, 2026-11-15). Tools: `tools/measure/pg_volume_consistency.py`
(part A) and `kg_memoir_title_candidates.py` (part B).*

**Where to send it: not confirmed.**
- gutenberg.org/help/errata.html answered 503 on 2026-11-18, and
  gutenberg.org refuses this container.
- Standard Ebooks' upstream guide names `errata<year>@pglaf.org` for
  transcription errors. Whether catalogue fixes (subjects, LoCC,
  categories) take the same route is to be read on the errata page
  before sending.
- Nothing goes out without the operator's yes.

## A. Volumes of one work catalogued as different kinds of book (send)

1,901 multi-volume works were compared, leaving collections out
(complete works, writings, anthologies).
- 24 disagree on memoir, fiction, for-children or LoCC
  (`volume-inconsistencies.csv`).
- The 16 not proposed differ rightly: a series on different people or
  places (*Little Journeys* LoCC, *Seeing Europe*, *Historic Tales*), a
  digest by genre (*The World's Greatest Books*), periodicals.

The 8 proposed. Each takes the heading another volume of the same work
already carries, adjusted to the volume's topic:

| work | volumes now | proposed |
|---|---|---|
| Suetonius, *The Lives of the Twelve Caesars* | 6386–6397 carry "Emperors -- Rome -- Biography -- Early works to 1800"; **6398** (Grammarians) and **6399** (Lives of the Poets) carry only "Rome -- History -- Empire" | 6398: add "Grammarians -- Rome -- Biography -- Early works to 1800"; 6399: add "Poets, Latin -- Biography -- Early works to 1800" |
| Hubbard, *Little Journeys to the Homes of the Great* | 8 volumes carry a "-- Biography" heading; **6306, 18118** (painters, persons only), **13619** ("Authors, English -- Homes and haunts"), **20318** ("Composers"), **23761** ("Orators") do not | add "Artists -- Biography" (6306, 18118), "Authors, English -- Biography" (13619), "Composers -- Biography" (20318), "Orators -- Biography" (23761) |
| Rui de Pina, *Chronica de el-rei D. Affonso V* | Vol. III (**24508**) carries "Biography" and LoCC CT; DP; Vols. I (**25987**) and II (**21911**) do not | harmonise the three volumes. Checked: a court chronicle of the reign, so removing "Biography"/CT from 24508 may be the right fix |
| Fleury de Chaboulon, *Memoirs of the Private Life, Return, and Reign of Napoleon in 1815* | Vol. 2 (**24718**) carries "Biography", LoCC CT; DC; Vol. 1 (**22345**) does not | add "Biography" and LoCC CT to 22345 |
| Rosen, *Der deutsche Lausbub in Amerika: Erinnerungen und Eindrücke* | Vol. 2 (**59218**) carries "Spanish-American War, 1898 -- Personal narratives"; Vols. 1 and 3 (**39809, 59219**) carry only "United States -- Description and travel". The book is the author's memoir | add a personal-name heading for Rosen, Erwin, 1876-1923, and "German Americans -- Biography" (cataloguer to word it) to 39809, 59219 |
| Arnault, *Souvenirs d'un sexagénaire* (4 vols) | no volume carries a memoir heading; LoCC DC on Tome IV (**24549**) only | add "Authors, French -- 19th century -- Biography" to 22633, 23953, 24383, 24549; LoCC DC on 22633, 23953, 24383 |
| Humboldt, *Personal Narrative of Travels to the Equinoctial Regions* | Vol. 1 (**6322**): G; QE; QH; F2201; F2301; **7014, 7254**: F2201 | LoCC G; F2201 on 7014, 7254 (a travel narrative throughout) |
| Humboldt, *Reise in die Aequinoctial-Gegenden* | **22492, 24746**: G; QE; QH; F2201; F2301; **28280, 38638**: F2201 | LoCC G; F2201 on 28280, 38638 |

**Checked on the text (2026-11-18).** The opening pages of every volume
named above were read, from aleph.pglaf.org:

| work | what the text is | the proposal |
|---|---|---|
| Suetonius, vols. 13–14 | "Lives of Eminent Grammarians", "Lives of the Poets" (Terence, Juvenal …): biographies | confirmed |
| *Little Journeys*, the five volumes | biographical sketches of painters, artists, English authors, composers, orators, in the series' form | confirmed |
| *Chronica de el-rei D. Affonso V*, vols. I–II | a court chronicle of the reign by Rui de Pina (ed. Lisboa 1901–02): history, not a life written as such | **direction uncertain.** Vol. III's "Biography" and CT may be the error, rather than their absence on I–II. Proposed as "harmonise; the cataloguer chooses" |
| Fleury de Chaboulon, vol. 1 | the author's account as Napoleon's ex-secretary, 1815 ("I have only attempted to place Napoleon on the stage of action") | confirmed; "-- Personal narratives" fits too |
| Rosen, *Lausbub*, Teil 1 and 3 | published in the "Memoiren Bibliothek" series; first-person, emigration and army life | confirmed |
| Arnault, *Souvenirs* | the introduction discusses "Mémoires" and calls the book "ces Mémoires" | confirmed |
| Humboldt, en vol. 2, de Band 3 | the travel narrative, chapters 16–23 | confirmed |

7 of 8 are confirmed; the Affonso V proposal is reworded.

Four more volumes look inconsistent but are not. Goethe's *Wilhelm
Meister* Band 6–8 and *Wanderjahre* 3 lack the topical "-- Fiction"
heading, but all carry "Bildungsromans". *Aunt Jo's Scrap Bag* vol. 1 is
"Children's stories", PZ.

## B. A record that contradicts itself (check on the text first)

These are rows from our memoir review list. The PG category ("Category:"
bookshelf) and the LCSH subjects disagree.

| conflict | rows |
|---|---|
| PG category **Biographies**, no LCSH biography or memoir heading | 252 |
| PG category Novels, Humour, Crime …, no LCSH fiction heading | 30 |
| PG categories **both** Biographies and Novels / Humour / Crime | 12 |

The list: `memoir-category-conflicts.csv`. Its `checked_on_text` column
is empty: none is proposed until the book's front matter is read.
Examples:
- *Mark Twain's Burlesque Autobiography* (Humour, no fiction heading).
- *The Memoirs of Count Grammont*: some volumes are "Historical Novels",
  others "Biographies".
- *Mémoires de Mr. d'Artagnan*: Courtilz's pseudo-memoir, filed both
  ways.

## C. One observation, not per book

**PG's categories differ between volumes in 1,437 of the 1,901
multi-volume works.** They look assigned per file, not per work. Some are
plainly wrong: Fabre's *The Life of the Fly* is in "Biographies".
- **Suggestion to PG:** assign categories per work.
- **For us:** wiki-gutenberg's selection reads the LCSH subjects and
  LoCC, which PG's catalogue team assigns, and not these categories.

## What this changed in our own selection (kg_scope_count.py, 2026-11-18)

- **Fiction genres without the word "fiction" are now out.**
  "Bildungsromans", "-- Legends", "Love stories", "Sea stories", "Fairy
  tales" let 10 books into the scope.
- **A memoir word inside a fiction heading no longer makes a memoir.**
  "Diaries -- Fiction" put *A Lame Dog's Diary* and Lu Xun's *狂人日記*
  in the memoir group.
- **The scope is now 14,533 books, of which 3,594 are memoirs.**
