# Wikisource: what a collection would fetch, per book category

*(2026-11-19. Operator: "since it is a huge project I need a per book category
break down of the fetch data amount"; order "english first, then italian,
german, french and spanish". Companion to docs/notes/wikisource-design.md.)*

**Measured, not inferred**, by `tools/measure/wikisource_fetch_breakdown.py`
(tests: tests/test_measure_wikisource_breakdown.py) from MediaWiki's own
published database dumps, streamed and never stored: `page` (`page_len`),
`linktarget`, `templatelinks` (which proofread Page: pages a work
transcludes), `categorylinks` (dumps of 2026-11-01) and the `image` tables
(each wiki's own, then Commons' of 2026-10-01: 17.3 GB, one pass for the five
languages; the latest Commons `image` dump is empty).

## Totals (each scan file once)

| Language | Works | Text (wikitext) | Scan files | Scans | Size unknown |
|---|---:|---:|---:|---:|---:|
| English | 72,286 | 8.85 GB | 28,535 | 543.0 GB | 222 |
| Italian | 18,122 | 1.26 GB | 3,457 | 76.5 GB | 20 |
| German | 296,063 | 1.97 GB | 170,215 | 186.3 GB | 303 |
| French | 55,337 | 7.57 GB | 20,540 | 427.3 GB | 135 |
| Spanish | 29,341 | 0.80 GB | 2,129 | 31.3 GB | 6 |
| **All five** | **471,149** | **20.4 GB** | **224,876** | **1,264 GB** | 686 |

The design note keeps scans as **names and hashes only** (operator's choice):
the fetch for the records is the TEXT column; the scans column is what a node
holding the scans would add.

## Per category

A WORK is a top-level page of the main namespace (its subpages are its
chapters). TEXT = its own pages' wikitext + the proofread pages of every scan
it transcludes. The category is read from Wikisource's own categories,
**first match wins** in this order: encyclopaedia entry → film → fiction →
poetry → theatre → memoir → travel → biography → history → law and official →
religion → reference; a work no pattern matches is "other".

### English

| Category | Works | Text | Scan files | Scans | Commonest categories |
|---|---:|---:|---:|---:|---|
| memoir | 498 | 53 MB | 336 | 2.6 GB | Letters; Memoirs; Autobiographies |
| history | 744 | 501 MB | 724 | 13.0 GB | History; History of New Zealand; History of the United States |
| biography | 430 | 411 MB | 783 | 9.9 GB | Obituaries; Biographies; Biographies of writers |
| travel | 109 | 54 MB | 166 | 1.4 GB | Travel; Travelogues; Travel in England |
| fiction | 5,023 | 2,112 MB | 4,572 | 95.8 GB | Short stories in periodicals; Mystery short stories; Novels |
| poetry | 4,507 | 402 MB | 2,262 | 18.6 GB | Poems; Early modern poetry; Scottish poetry |
| theatre | 267 | 100 MB | 201 | 4.2 GB | Ancient drama; Plays; Elizabethan drama |
| religion | 813 | 255 MB | 328 | 9.4 GB | Individual Christian hymns; Religion; Sermons |
| law and official | 13,282 | 845 MB | 2,495 | 9.9 GB | United States Supreme Court decisions; Case missing lower court; United States Supreme Court decisions on taxation |
| reference | 649 | 792 MB | 689 | 24.7 GB | PD-US-periodical; Newspaper articles; Periodicals |
| film | 535 | 335 MB | 620 | 181.4 GB | Film; Silent film; Novels adapted into films |
| other | 45,429 | 3,128 MB | 15,490 | 176.5 GB |  |

### Italian

| Category | Works | Text | Scan files | Scans | Commonest categories |
|---|---:|---:|---:|---:|---|
| memoir | 239 | 73 MB | 141 | 4.1 GB | Lettere; Diari di viaggio; Autobiografie |
| history | 543 | 138 MB | 263 | 5.2 GB | Storia; Documenti storici Italia; Documenti di storia regionale |
| biography | 93 | 37 MB | 64 | 1.2 GB | Biografie; Biografie dei consiglieri comunali di Roma |
| fiction | 1,035 | 229 MB | 438 | 10.7 GB | Novelle; Romanzi; Racconti |
| poetry | 8,334 | 136 MB | 386 | 6.6 GB | Poesie; Sonetti; CantaStoria |
| theatre | 539 | 74 MB | 136 | 2.7 GB | Teatro; Teatro-L; Commedia dell'Arte |
| religion | 46 | 11 MB | 11 | 0.3 GB | Bibbia; Sacra Bibbia (riveduta Diodati 1821); Religione |
| law and official | 735 | 72 MB | 63 | 0.8 GB | Diritto; Diritto-D; Diritto-L |
| reference | 10 | 24 MB | 10 | 1.0 GB | Dizionari |
| other | 6,548 | 658 MB | 2,267 | 51.3 GB |  |

### German

| Category | Works | Text | Scan files | Scans | Commonest categories |
|---|---:|---:|---:|---:|---|
| memoir | 4,115 | 29 MB | 6,643 | 4.3 GB | Tagebuch von Hans Brass; Erinnerung; Autobiographie |
| history | 4,758 | 278 MB | 42,975 | 39.2 GB | Regionalgeschichte; Geschichte; Politische Geschichte |
| biography | 409 | 20 MB | 1,682 | 3.0 GB | Biographie; Biographisches Jahrbuch und Deutscher Nekrolog; Ernst von Leutsch Nekrolog |
| travel | 272 | 16 MB | 2,691 | 3.7 GB | Reisebericht; Zeitschrift (Reisen); Reise mit der Yacht Maria 1854 zu den Färöern |
| fiction | 3,087 | 158 MB | 27,966 | 29.6 GB | Märchen; Erzählung; Novelle |
| poetry | 10,287 | 42 MB | 6,960 | 6.1 GB | Gedicht; Sagenballade; Meyer Gedichte |
| theatre | 202 | 21 MB | 4,282 | 2.2 GB | Theaterwissenschaft; Drama; Zeitschrift (Theater) |
| religion | 513 | 51 MB | 4,651 | 4.1 GB | Religionswissenschaft / Theologie; Predigt; Zeitschrift (Theologie) |
| law and official | 4,589 | 95 MB | 3,290 | 2.4 GB | Rechtswissenschaft; Rechtsnorm; Bundesgesetzblatt (Deutschland) |
| reference | 182,067 | 938 MB | 52,171 | 88.8 GB | Paulys Realencyclopädie der classischen Altertumswissenschaft; RE:Kurztext überprüfen; RE:Fertig |
| film | 4 | 0 MB | 0 | 0.0 GB | Film; Zeitschrift (Film) |
| other | 85,760 | 539 MB | 36,804 | 39.0 GB |  |

### French

| Category | Works | Text | Scan files | Scans | Commonest categories |
|---|---:|---:|---:|---:|---|
| memoir | 502 | 367 MB | 580 | 11.4 GB | Lettres; Correspondance; Mémoires |
| history | 223 | 142 MB | 229 | 6.0 GB | Documents historiques; Études historiques; Monuments historiques |
| biography | 496 | 810 MB | 799 | 30.0 GB | Biographies; Notices biographiques; Nécrologie de la Revue pédagogique |
| travel | 283 | 377 MB | 406 | 12.9 GB | Voyage; Explorations et voyages en Chine; Récits de voyage |
| fiction | 5,198 | 1,952 MB | 3,861 | 91.1 GB | Romans français; Nouvelles; Fables d’Ésope |
| poetry | 9,519 | 869 MB | 1,956 | 28.8 GB | Sonnets; Poèmes d’Émile Verhaeren; Poèmes de Paul Verlaine |
| theatre | 1,513 | 776 MB | 953 | 18.4 GB | Théâtre; Comédies; Théâtre à formater |
| religion | 179 | 259 MB | 206 | 6.0 GB | Religions et croyances; Hymnes; Bible |
| law and official | 302 | 101 MB | 339 | 7.9 GB | Droit en France; Constitutions françaises; Droit |
| reference | 3,592 | 1,321 MB | 1,095 | 26.2 GB | Revue littéraire (RDDM); Revue musicale (RDDM); Chroniques de Maupassant |
| other | 33,530 | 4,906 MB | 12,939 | 277.3 GB |  |

### Spanish

| Category | Works | Text | Scan files | Scans | Commonest categories |
|---|---:|---:|---:|---:|---|
| memoir | 91 | 10 MB | 7 | 0.2 GB | Recuerdos de un hacendado; Recuerdos y Fantasías; Autobiografías |
| history | 1,407 | 54 MB | 909 | 7.1 GB | Historia de España; Historia de Chile; Historia de Argentina |
| biography | 655 | 10 MB | 25 | 0.5 GB | Biografías; Manual de Biografía y de bibliografía de los escritores españoles del siglo XIX; Biografías escritas por Bartolomé Mitre |
| travel | 46 | 9 MB | 19 | 0.5 GB | Relatos de viajes; Viajes y exploración; Literatura de viajes de Viktor Pinchuk |
| fiction | 4,070 | 181 MB | 221 | 4.5 GB | Cuentos; Fábulas; El libro de los cuentos |
| poetry | 8,233 | 32 MB | 150 | 3.0 GB | Poesías; Sonetos; Sonetos de Francisco de Quevedo |
| theatre | 430 | 44 MB | 40 | 0.6 GB | Teatro de Lope de Vega; Teatro de Tirso de Molina; Comedias |
| religion | 15 | 2 MB | 5 | 0.1 GB | La Sagrada Biblia; Himnos religiosos; La Biblia |
| law and official | 1,079 | 152 MB | 96 | 3.1 GB | Resoluciones del Consejo de Seguridad de las Naciones Unidas; Tratados internacionales; Decretos de Argentina |
| reference | 2,007 | 28 MB | 19 | 1.4 GB | Diccionario de Arquitectura Civil; Diccionario A; Diccionario C |
| other | 11,308 | 309 MB | 764 | 13.6 GB |  |

## Caveats

- **"other" is not a category**: it holds works whose categories are only
  maintenance ones (licence tags such as PD-USGov, "Undated works", "Texts
  without a source", SAL quality levels, "Testi del XIX secolo"). English
  45,429 works, French 33,530: much of it is out of reach of a category rule
  and needs Wikidata or the text to classify.
- **A scan file shared by works of different categories is counted in each**
  (an anthology); within one category, once. The totals above count each file
  once, so the categories' scans add up to more than the total.
- **German works are small and many**: de.wikisource keeps each letter,
  article and lexicon entry as its own top-level page (Pauly's RE alone,
  ~150,000), and its scans are mostly one image per page (170,215 files).
- **English "film"**: the scans are the films' VIDEOS (181 GB for 535 works);
  English scans without film are 362 GB.
- **French "history" is thin by category** (223 works): French Wikisource
  files most history under the author or the periodical (Revue des deux
  Mondes, "reference" here), not under a subject.
- The rules were checked on each language's commonest categories and fixed
  where they mislabelled (historical novels as history, "Prix Goncourt" as law,
  lexicon entries as history, newspaper columns as history); the remaining
  noise is visible in the "commonest categories" column (French history still
  takes "Annales de mathématiques" by its word "Annales").
- Sizes unknown: 686 of 224,876 files, absent from both image tables (deleted
  or renamed since the dumps).
