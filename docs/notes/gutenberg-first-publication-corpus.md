# Gutenberg's first publication year: what its own corpus states

Measured by München, 2026-11-16, read-only, on the node. The operator's
rule (CLAUDE.md #701): a collection holds only the information contained
in its corpus. So this reads ONLY Gutenberg's corpus: our parsed records,
the origin's RDF catalogue entries, and the original ebook files (plain
text, HTML, EPUB package and XHTML).

**Sample:** 1,199 random PG books (seed 20261116) + a gold set of 72 books
with first-publication years from standard references (Pride and Prejudice
1813, Don Quixote 1605, Italian/French/German/Spanish/Portuguese/Dutch/
Polish books) — 1,268 books in all. "Gold: has / exact / ±1" counts the gold
books where a signal states a year (its earliest), and how many are right.

## The parsed records (what the collection holds today)

| record field | books with a year | gold: has / exact / ±1 | what it is |
|---|---:|---:|---|
| `date` (`edition_date`: marc906 → marc260 → header) | 14.0 % | 1 / 1 / 1 | the transcribed EDITION's year |
| `extra.marc.520` (PG's summary, any year in it) | 27.8 % | 67 / 52 / 53 | first publication where it says "published in YYYY" (below), else story years |
| `extra.marc.906`, `extra.marc.260` | 12.9 %, 13.3 % | 1 / 1 / 1 | the edition's year |
| `extra.text_header."Original publication"` | 7.6 % | 0 | the edition's imprint |
| `extra.issued`, `text_header."Release date"`, `"Most recently updated"` | 99.6 %, 88.9 %, 56.8 % | 0 | PG's own dates |
| `extra.creators_detail.birth` / `.death` | 86.6 % / 86.4 % | 0 / 3 (death) | the author's life |
| `extra.subjects_by_scheme.LCSH`, `bookshelves` | 18.4 %, 9.1 % | 0 / 1 | subject periods ("19th century") |

## The origin's RDF and the ebook texts

| where | books with a year | gold: has / exact / ±1 |
|---|---:|---:|
| RDF `marc520` "(first/originally) published (in) YYYY" | **12.0 %** | **54 / 52 / 53** |
| RDF `marc906` / `marc260` / `dcterms:description` | 14.1 / 14.4 / 0.2 % | 1 / 1 / 1 |
| text header "Original publication:" | 14.4 % | 1 / 1 / 1 |
| plain text: title-page years (first 80 lines) | 81.0 % | 45 / 18 / 19 |
| plain text: title-page Roman numerals | 3.4 % | 2 / 1 / 1 |
| plain text: "first/originally published …" | 3.5 % | 1 / 1 / 1 |
| plain text: copyright line | 22.3 % | 8 / 3 / 4 |
| plain text: "Entered according to Act of Congress" | 1.9 % | 0 |
| EPUB package `dc:date` | 98.5 % | 71 / 0 / 0 (PG's release; `opf:event="publication"` is PG's) |
| EPUB XHTML: "first/originally published …" | 9.7 % | 5 / 3 / 4 |
| EPUB XHTML: "published/appeared in YYYY" | 6.9 % | 5 / 2 / 2 |
| EPUB XHTML: title page | 80.2 % | 46 / 21 / 22 |
| EPUB image file names | 0.8 % | 1 / 0 / 0 |
| the EARLIEST year anywhere above | 87.9 % | 64 / 40 / 41 |

## Findings

1. The corpus states a book's FIRST publication in one place reliably:
   PG's summary, `marc520` "… published in YYYY" — 12 % of books, 52 of
   54 gold books exact. These summaries are PG's automatically generated
   ones ("This is an automatically generated summary."), part of PG's
   catalogue entry, i.e. of the corpus.
2. Everything else that carries a year is the TRANSCRIBED EDITION's year
   (marc906/260, the header's "Original publication", most title pages —
   Pride and Prejudice 1894, Jane Eyre 1897, Don Quixote 1880), PG's own
   dates (release, update, EPUB `dc:date`), the author's life, or years the
   text mentions (The Three Musketeers' title page 1628). Title pages are
   right only where the transcribed edition IS the first (Der Prozess 1925,
   Dracula 1897), and nothing in the corpus says when that is the case.
3. The text's own statements ("first published in …" in a preface or a
   transcriber's note) add ~3-10 % of books, at 3 of 5 exact on the gold.
4. Some books state nothing: The Count of Monte Cristo, Zarathustra, both
   Werthers, Six Characters in Search of an Author, Emilia Galotti.

Per-book data on the node: `/mnt/tank/louise-measure/gfp/corpus_says.csv`
(RDF + plain text) and `corpus_says2.csv` (parsed records + EPUB), with
their summaries beside them; scripts in `/root/measure/gfp/corpus_says*.py`.
