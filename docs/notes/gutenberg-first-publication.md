# Gutenberg: the work's first publication year — what the bulk sources give

Measured 2026-11-16. Read-only on the node: no collection, manifest or
config was written; every download went to `/mnt/tank/louise-measure/gfp/`,
every script is in `/root/measure/gfp/` (listed at the end). Requirement
(operator, 2026-11-16): every Gutenberg book in a search result carries the
WORK's first publication year — not PG's release date (`dcterms:issued`),
not the transcribed edition's year (MARC 260/906, the "Original
publication:" header).

**Answer.** No single bulk source covers the collection. A six-step
chain (Wikidata, then two independent library catalogues that must agree)
gives a year to **46.5 %** of the books, about **93 %** of them right to
within one year. Wikidata alone gives **19.8 %**, at 97 % on the gold set.
The other **53.5 %** gets no year that can be trusted: 15.3 % have a single
catalogue year that is right only 55–67 % of the time, and 38.2 % have no
candidate at all.

## Method

- **Sample.** `random.Random(20261116).sample(sorted(in-scope native_ids), 1200)`
  over the gutenberg manifest (79,866 in scope; `extract_rdf.py`). One id
  (195) has no `pg195.rdf`, so the denominator is **1,199**. Coverage below is
  on these 1,199 books (95 % CI about ±2.8 pp at 45 %, ±2.3 pp at 20 %).
- **Gold set** (`gold.tsv`, 72 books): first-publication years from the
  standard references (the years the work's encyclopedia entries give),
  in English, French, Italian, German, Spanish, Portuguese, Dutch and Polish. They include
  pg1342 = 1813, Don Quixote pg996/pg2000 = 1605 (first part), Faust I
  = 1808. *Excluded* (`gold_excluded.tsv`,
  11): works whose serial and book years differ (Uncle Tom's Cabin, The
  Jungle, Germinal, Crime and Punishment, Pinocchio, The Hound of the
  Baskervilles, The War of the Worlds, L'Innocente, Ulysses, The Secret
  Garden), and Le rouge et le noir (released 1830 with 1831 on the title page).
  Whether a serial counts as the first publication is a decision for the
  operator. The gold set leans towards famous books.
- **Silver set** (141 sample books outside the gold): books Wikidata dates
  through a stated link (step 1 or 2 below). They are less famous than the
  gold, and are used to measure the catalogue sources against Wikidata.
- **Agreement** means within ±1 year. A year that is ≥ the PG release year
  is impossible. So is a year before the author was 10, or a year after the
  transcribed edition (the edition is an upper bound).

## The sources (sample n = 1,199; gold n = 72)

| source | coverage | gold: covered / exact / ±1 | silver ±1 | how fetched, size | licence |
|---|---:|---|---:|---|---|
| Wikidata P2034 (PG id) → item, P629 → work P577 | 4.3 % | 42 / 40 / 41 | 50/50 | node records `family=work,other` (9.7 GB zst of 46 GB, dump 20261012), one pass ~10 min, 4 cores | CC0 |
| Wikidata (a) PG book Wikipedia link → QID → P577 | 10.3 % | 70 / 66 / 68 | 120/120 | + `wb_items_per_site.sql.gz` 1.92 GB (2026-11-08) | CC0 |
| Wikidata (b) PG author link → QID → P50 works, title equal → P577 | 16.8 % | 69 / 63 / 65 | — | same pass | CC0 |
| periodical issue date in the PG title | 2.2 % | 0 / – / – | 3/3 | RDF title | PG |
| Open Library, edition linked to PG → work `first_publish_date` | 1.4 % | 14 / 0 / 1 | 1/10 | `ol_dump_{editions,works}_2026-10-31` 12.63 + 4.08 GB | OL: "public domain"*  |
| Open Library, PG-linked edition → earliest non-PG edition of its work | 2.9 % | 22 / 14 / 15 | 7/14 | same | same |
| Open Library by author + title, earliest non-PG edition (cleaned) | 55.5 % | 71 / 40 / 45 | 81/111 | + `ol_dump_authors` 0.78 GB | same |
| HathiTrust Hathifile by author (LC form + birth) + title, earliest volume (cleaned) | 45.1 % | 68 / 31 / 34 | 56/93 | `hathi_full_20261101.txt.gz` 1.23 GB, 19.8 M lines | free download; partly OCLC-derived records* |
| **Hathi = OL** (both cleaned, agree ±1, book not a translation) | 29.6 % | 28 / 21 / 24 | 44/49 | — | — |
| PG `marc520` "published in YYYY" (a model's summary) | 8.1 % | 53 / 52 / 53 | 72/72 | RDF | PG |
| PG edition year (header / 260 / 906) — *not a first-publication year* | 15.2 % | 1 / 1 / 1 | 11/18 | RDF + txt header | PG |
| Wikidata (a) P1191/P571 fallback when no P577 | 0.8 % | 0 | — | — | CC0 |

\* The licences need checking before use (invariant 9). Open Library's licensing page is rendered by JavaScript, so it was
not re-read today. The Hathifiles carry member-library records, some derived from OCLC WorldCat. "Cleaned" = the
plausibility filter of §Method.

Other sources looked at, with no usable coverage:

- PG's `pg_catalog.csv` (columns `Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves`) has no first-publication field.
- Standard Ebooks: the OPDS and Atom "all" feeds answer 401 (Patrons Circle). The catalogue holds about 1.4 k books (< 2 %).
- ISFDB: the downloads page now needs a login. ISFDB is the authority for magazine SF (2.9 % of the sample sits in the PG SF shelf), so it is worth a manual download.
- Library of Congress: the RDF's `marc010` LCCN (on 18 % of the books) points to the transcribed edition's record, so it gives the same edition year.
- VIAF: authors only.

## What the errors look like

- **Wikidata.** 4 gold errors out of 71. Zarathustra gives 1885 (the complete work), not 1883 (part 1). Thérèse Raquin
  gives 1869, not 1867. Une vie and Die Räuber are off by one year. The (b) route adds a **title collision** risk: "Faust" matches
  *Faust. Ein Fragment* (1790). 26 sample books (2.2 %) have (b) hits with different years, and the chain takes the
  earliest. Where a book also has a catalogue agreement, (b) agrees with it 33 times out of 37 (89 %). Example
  disagreements: Michelet, *Les Femmes de la Révolution*: (b) 1898, real 1854. Huneker, *Chopin*: 1916 against 1900.
- **Open Library.** Its errors run **earlier**: 16 of 71 gold books. They are placeholder dates (1800, 1822 for
  several unrelated works) and different works that share a title. A work's `first_publish_date` is useless: 13 of 14 gold
  books are later than the truth, because the value is usually a reprint's or PG's own date.
- **Hathi.** Its errors run **later**: 32 of 68 gold books. The first edition is often not digitised (Don Quixote 1847/1905,
  Leviathan 1881, Discours 1894), so the earliest volume is a reprint. For less famous books, where the volume
  held is often the first edition, the error is smaller (silver 56/93).
- **Translations.** For a translated book, a catalogue title match finds the TRANSLATION's year. Example: Pushkin, *La fille du
  capitaine*: 1854, not 1836. The chain therefore uses catalogue years only for books with no `marcrel:trl` agent.
  Removing them raises silver agreement for "Hathi = OL" from 48/57 to 44/49.
- **The edition year is not a proxy.** Where Wikidata dates the work, the PG edition year equals that date in only 14 of 27
  books (18 of 27 within ±1).

## Sanity check: a year after the author's death

This is flagged, not assumed wrong: posthumous publication is real. In the chain below:

- **Wikidata steps:** 13 of 237 books. All inspected cases are genuine posthumous first editions: Shakespeare's First
  Folio plays (1623), *Life and Letters of Charles Darwin* (1887), London's *The Red One* (1918) and Gissing's
  *Will Warburton* (1905).
- **"Hathi = OL" step:** 17 of 267 books.
- **A single catalogue year:** 27 of 214 books, up to 820 years after the death. These are mostly wrong.

## Recommended precedence chain

The order goes from stated relations to agreement between independent catalogues:

1. **Wikidata P2034** = the PG id → item. If the item has P629, use the work's P577; else the item's P577. Take the
   earliest non-deprecated value of year precision or finer.
2. **Wikidata via the book's Wikipedia link** (the RDF `dcterms:description` "Wikipedia page about this book: <url>"),
   with the same rule.
3. **Wikidata, author + title**: the author's QID from `pgterms:webpage`. Items with P50 = that author whose label,
   alias or P1476 normalises equal to the PG main title. An edition collapses into its work; take the earliest P577.
4. **Periodical issue date** in a PG issue title (No./Vol./Number + a month, or the author "Various").
5. **Hathi = OL**, for books not translated: both catalogue years after the plausibility filter, within ±1. Take the earlier.
6. **A single catalogue year** (the earlier of Hathi and OL, cleaned, not translated) **only when** the PG edition year
   or `marc520` agrees within ±1. This is corroboration; the edition year is never used alone.

| after step | coverage (n = 1,199) | added | accuracy of the added years |
|---|---:|---:|---|
| 1–2 (Wikidata, stated link) | 12.0 % | 144 | gold 69/71 ±1 (67 exact) |
| 3 (Wikidata, author + title) | 19.8 % | 93 | 89 % against the step-5 agreement |
| 4 (issue date) | 21.7 % | 23 | not measured (the issue's own date; assumed right) |
| 5 (Hathi = OL) | 44.0 % | 267 | gold 24/28; silver 44/49 (90 %) |
| 6 (corroborated single year) | **46.5 %** | 31 | silver 19/20, gold 14/14 |

The weighted accuracy of steps 1–6 is about 93 % within ±1, from the per-step figures above.

**Not recommended, with the measured reason:**

- A single uncorroborated catalogue year: silver 29/43, gold 21/38. It would bring coverage to 61.8 %.
- OL `first_publish_date` (1/14 on the gold).
- `marc520`: 53/53 on the gold and 72/72 on the silver, but it is a model's automatically generated statement. It is
  used only as a corroborator in step 6. Using it as a source would add 1.6 pp.
- The edition year (excluded by the operator).

## Residual: 641 books (53.5 %) with no trusted year

- 183 (15.3 %) have a single catalogue year that nothing corroborates. These are the 55–67 % years above.
- 458 (38.2 %) have no candidate at all.

All 641, by kind (`extra_stats.py`; the first kind that applies):

| kind | books |
|---|---:|
| English, by a dated author, no Wikidata item and no agreeing catalogues (not diagnosed further) | 281 |
| another language, by a dated author (Finnish 41 of the residual's languages) | 92 |
| author with no dates in PG (a name-only match is too risky to use) | 87 |
| no author | 59 |
| a translation (catalogue years date the translation; the original has no Wikidata link) | 50 |
| periodical issue or "Various" anthology with no year in its title | 37 |
| pulp / magazine fiction (SF shelf; the PG header names the magazine issue) | 35 |

94 of the 641 have a PG edition year. For a magazine story or an obscure first printing that year is often the true
first publication, but without a second source it cannot be told apart from a reprint.

What could supply more (not measured here):

1. Wikidata itself: step 3 found Wikidata work items for 93 sample books (7.8 %) that carry no P2034. Adding the
   missing P2034 statements upstream turns these into stated links. Only 4,055 PG ids carry one today.
2. ISFDB (its download needs a login) for the magazine fiction.
3. National bibliographies with bulk dumps — Fennica (Finnish), BnF, DNB — through the same author + title agreement rule.

## Field paths used

- **PG RDF** (`/corpora-custom/gutenberg/<id>/pg<id>.rdf`):
  - `pgterms:ebook/dcterms:title`, `dcterms:alternative`, `dcterms:issued`.
  - `dcterms:description` matching `Wikipedia page about this book:\s*(\S+)`.
  - `dcterms:creator/pgterms:agent/{pgterms:name, pgterms:birthdate, pgterms:deathdate, pgterms:webpage/@rdf:resource}`.
  - `marcrel:trl/pgterms:agent` marks a translation.
  - `pgterms:marc520` matching `published in (\d{4})`; `pgterms:marc260`, `pgterms:marc906`.
  - `dcterms:language/rdf:Description/rdf:value`, `pgterms:bookshelf`.
- **PG text header:** the first 8 KB of `pg<id>.txt`, `Original publication:\s*(.+)`; the last year in it.
- **Wikidata sitelinks:** `wb_items_per_site` rows `(ips_row_id, ips_item_id, ips_site_id, ips_site_page)`. Matched on
  `site = <host lang>wiki`; the page is the URL path, percent-decoded, `_` → space, first letter upper-cased.
  1,367 of 1,406 links resolve, all 167 book links among them.
- **Wikidata entity** (the record's `extra`):
  - `claims.P2034[].mainsnak.datavalue.value` (string).
  - `claims.{P577,P1191,P571}[].mainsnak.datavalue.value.{time,precision}`, with `rank != "deprecated"` and
    `precision >= 9`; the year is `time[:time.index("-", 1)]`.
  - `claims.{P629,P50,P31,P407,P747}[].mainsnak.datavalue.value.numeric-id`.
  - `claims.P1476[]…value.text`, `labels.*.value`, `aliases.*[].value`.
- **Open Library editions** (TSV col 5 = JSON):
  - `identifiers.project_gutenberg[]` (2,498 links), `ocaid` / `source_records[] "ia:…"` ending `(\d{5})gut` (1,553).
    Together they cover **2,432 PG ids in all of OL** (3 % of PG).
  - `works[].key`, `publish_date` (free text; the first year in it), `publishers[]`.
  - An edition is PG-derived if it carries `project_gutenberg`, a publisher matching "gutenberg", or an ocaid ending "gut".
- **Open Library works:** `key`, `title`, `authors[].author.key`, `first_publish_date`.
- **Open Library authors:** `key`, `name`, `personal_name`, `birth_date`, `remote_ids.wikidata`. A PG agent matches
  through its QID or through the same name tokens and birth year; name-only matches (4,679) are not used.
- **Hathifile** (no header, 26 tab columns): `author` (26), `title` (12), `imprint` (13), `rights_date_used` (17),
  `bib_fmt` (20) = `BK`. The author key is (surname, forenames) with the parenthetical fuller form dropped, plus the
  first year in the author string. A title matches on the main title: before a volume suffix, `: ; / . (`, ", or,",
  with no punctuation and no leading article (`common.py`).

## Files

Scripts are in `/root/measure/gfp/`, in run order:

- `extract_rdf.py` (sample + RDF), `header_year.py`.
- `wd_sitelinks.py`, `wd_prep.py`, `wd_scan.py` (run in a throwaway container from `louise-rag:local` with pyarrow and orjson).
- `ol_pass1.sh`, `ol_stage_a.py`, `ol_stage_bc.sh`, `ol_author_route.py` (`authors`, `works`), `ol_author_editions.sh`.
- `hathi_match.py`, `common.py`.
- `analyze.py`, then `table.py`, `extra_stats.py`, `gold_table.py`, `inspect_rows.py`.

Data and outputs are in `/mnt/tank/louise-measure/gfp/` (18 GB): `per_book.csv` (every source per book),
`analysis.json`, `table.txt`, `extra_stats.txt` and the dumps in `ol/`, `wd/` and `hathi/`.

## Gold set, per source

Bold = off by more than one year. "Wikidata (chain B)" is steps 1–3.

| pg | work | lang | gold | Wikidata (chain B) | Hathi = OL | Hathi | OL by author | marc520 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 11 | Alice's Adventures in Wonderland | en | 1865 | 1865 | – | **1869** | 1865 | 1865 |
| 20 | Paradise Lost | en | 1667 | 1667 | – | **1725** | 1667 | 1667 |
| 23 | Narrative of the Life of Frederick Douglass | en | 1845 | 1845 | 1845 | 1845 | 1845 | – |
| 33 | The Scarlet Letter | en | 1850 | 1850 | 1850 | 1850 | 1850 | 1850 |
| 35 | The Time Machine | en | 1895 | 1895 | 1895 | 1895 | 1895 | 1895 |
| 42 | Strange Case of Dr Jekyll and Mr Hyde | en | 1886 | 1886 | – | 1886 | **1875** | 1886 |
| 45 | Anne of Green Gables | en | 1908 | 1908 | 1908 | 1908 | 1908 | 1908 |
| 46 | A Christmas Carol | en | 1843 | 1843 | – | **1846** | **1887** | 1843 |
| 55 | The Wonderful Wizard of Oz | en | 1900 | 1900 | – | **1966** | 1899 | – |
| 74 | The Adventures of Tom Sawyer | en | 1876 | 1876 | 1875 | 1875 | 1875 | 1876 |
| 84 | Frankenstein | en | 1818 | 1818 | 1818 | 1818 | 1818 | 1818 |
| 98 | A Tale of Two Cities | en | 1859 | 1859 | – | 1859 | **1832** | 1859 |
| 135 | Les Miserables (Hapgood tr.) | en | 1862 | 1862 | – | 1862 | **1822** | 1862 |
| 147 | Common Sense | en | 1776 | 1776 | – | **1844** | 1776 | – |
| 215 | The Call of the Wild | en | 1903 | 1903 | 1903 | 1903 | 1903 | 1903 |
| 345 | Dracula | en | 1897 | 1897 | 1897 | 1897 | 1897 | 1897 |
| 514 | Little Women | en | 1868 | 1868 | – | 1868 | **1848** | 1868 |
| 768 | Wuthering Heights | en | 1847 | 1847 | 1846 | 1847 | 1846 | 1847 |
| 799 | De la terre a la lune | fr | 1865 | 1865 | – | **1890** | 1865 | 1865 |
| 829 | Gulliver's Travels | en | 1726 | 1726 | – | **1815** | **1826** | 1726 |
| 996 | Don Quixote (Ormsby tr.) | en | 1605 | 1605 | – | **1905** | **1833** | – |
| 1184 | The Count of Monte Cristo | en | 1844 | 1844 | – | **1851** | **1830** | – |
| 1228 | On the Origin of Species | en | 1859 | 1859 | 1859 | 1859 | 1859 | 1859 |
| 1232 | The Prince | en | 1532 | 1532 | – | **1686** | **1515** | 1532 |
| 1257 | The Three Musketeers | en | 1844 | 1844 | – | **1849** | 1844 | 1844 |
| 1260 | Jane Eyre | en | 1847 | 1847 | 1847 | 1847 | 1847 | 1847 |
| 1322 | Leaves of Grass | en | 1855 | 1855 | 1855 | 1855 | 1855 | 1855 |
| 1342 | Pride and Prejudice | en | 1813 | 1813 | – | **1817** | 1813 | 1813 |
| 2000 | Don Quijote | es | 1605 | 1605 | – | **1847** | **1905** | – |
| 2034 | Waverley | en | 1814 | 1814 | – | 1814 | **1800** | – |
| 2229 | Faust I (de) | de | 1808 | 1808 | **1790** | **1790** | **1790** | 1808 |
| 2610 | Notre-Dame de Paris (en) | en | 1831 | 1831 | – | 1831 | **1822** | 1831 |
| 2701 | Moby-Dick | en | 1851 | 1851 | 1851 | 1851 | 1851 | 1851 |
| 3023 | Faust Part 1 (en) | en | 1808 | 1808 | **1790** | **1790** | **1790** | 1808 |
| 3207 | Leviathan | en | 1651 | 1651 | – | **1881** | 1651 | 1651 |
| 3300 | The Wealth of Nations | en | 1776 | 1776 | – | 1776 | **1743** | 1776 |
| 3333 | Os Lusiadas | pt | 1572 | 1572 | – | – | 1572 | 1572 |
| 4650 | Candide (fr) | fr | 1759 | 1759 | – | **1763** | 1759 | 1759 |
| 5781 | Le grand Meaulnes | fr | 1913 | 1913 | – | – | 1913 | 1913 |
| 6099 | Les Fleurs du mal | fr | 1857 | 1857 | – | **1868** | **1855** | 1857 |
| 6498 | Kabale und Liebe | de | 1784 | 1784 | – | **1868** | 1784 | – |
| 7205 | Also sprach Zarathustra | de | 1883 | **1885** | – | **1893** | 1883 | – |
| 7461 | Therese Raquin | fr | 1867 | **1869** | 1868 | 1868 | 1868 | 1867 |
| 9108 | Emilia Galotti | de | 1772 | 1772 | – | **1803** | 1772 | – |
| 9186 | Nathan der Weise | de | 1779 | 1779 | 1779 | 1779 | 1779 | 1779 |
| 9980 | Platero y yo | es | 1914 | 1914 | 1914 | 1914 | 1914 | 1914 |
| 11024 | Max Havelaar | nl | 1860 | 1860 | – | – | **1959** | 1860 |
| 13846 | Discours de la methode | fr | 1637 | 1637 | – | **1894** | 1637 | 1637 |
| 13951 | Les trois mousquetaires | fr | 1844 | 1844 | – | **1849** | 1844 | 1844 |
| 17013 | Fortunata y Jacinta | es | 1887 | 1887 | 1887 | 1887 | 1887 | – |
| 17223 | Pepita Jimenez | es | 1874 | 1874 | 1874 | 1874 | 1874 | 1874 |
| 17340 | Marianela | es | 1878 | 1878 | **1883** | **1883** | **1883** | 1878 |
| 17457 | Une vie | fr | 1883 | 1884 | 1883 | 1883 | 1883 | 1883 |
| 18457 | Sei personaggi in cerca d'autore | it | 1921 | 1921 | – | 1921 | **1900** | – |
| 19657 | Notre-Dame de Paris (fr) | fr | 1831 | 1831 | – | 1831 | **1822** | 1831 |
| 19794 | Die Leiden des jungen Werther | de | 1774 | 1774 | **1787** | **1787** | **1787** | – |
| 19942 | Candide (en) | en | 1759 | 1759 | – | **1897** | **1746** | 1759 |
| 22367 | Die Verwandlung | de | 1915 | 1915 | – | **1935** | 1916 | – |
| 23297 | La Gioconda | it | 1899 | – | 1899 | 1900 | 1899 | – |
| 26298 | The Metamorphosis | en | 1915 | 1915 | – | **1935** | 1915 | – |
| 28961 | Cuore (en) | en | 1886 | 1886 | – | **1892** | **1889** | 1886 |
| 31536 | Pan Tadeusz | pl | 1834 | 1834 | 1834 | 1834 | 1834 | 1834 |
| 34811 | Buddenbrooks | de | 1901 | 1901 | – | **1904** | 1901 | 1901 |
| 42673 | Forse che si forse che no | it | 1910 | 1910 | 1910 | 1910 | 1910 | 1910 |
| 42942 | O Primo Bazilio | pt | 1878 | 1878 | – | **1990** | **1887** | 1878 |
| 45334 | I promessi sposi | it | 1827 | 1827 | – | 1827 | **1800** | – |
| 47804 | Die Rauber | de | 1781 | 1782 | 1781 | 1781 | 1781 | 1781 |
| 49836 | Niebla | es | 1914 | 1914 | 1914 | 1914 | 1914 | 1914 |
| 52006 | Les liaisons dangereuses | fr | 1782 | 1782 | – | **1797** | 1782 | 1782 |
| 55236 | I Canti | it | 1831 | 1831 | – | **1876** | **1885** | – |
| 55752 | Dom Casmurro | pt | 1899 | 1899 | – | **1936** | 1899 | 1900 |
| 69327 | Der Prozess | de | 1925 | 1925 | – | – | – | – |
