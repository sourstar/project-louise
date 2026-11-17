# Reading historical languages: material available, and readers by language and period (2026-11-16)

*Research only: nothing built, nothing measured (operator, 2026-11-16: "No
benchmarking, give me the available options"). Problem: the chat model's
summaries and answers are wrong on pre-modern sources because it misreads
the language. Goal: "translation plugins", one per language and period,
chosen from the source's language and date. Italian first.*

"Verified" = the page or Hub card was opened on 2026-11-16.

## Where the chat model reads sources today

| | book summary (`app/summarize.py`) | Ask answer (`app/ask/chat.py`) |
|---|---|---|
| language and period of the source | not given | language code only, no date |
| a held human translation | not used | its title only ("other editions held") |
| how much text | one pass up to 45k chars; else first 120 chapters, 24k each | 600 chars a source (`TEXT_CHARS`) |
| model | `CHAT_MODEL`, every language | same |

A period is already known for many records: Gutenberg's RDF gives each
author's birth and death years (`app/plugins/gutenberg/rdf.py`); Wikidata
gives works' dates; PG tags some stages by ISO 639-3 code (`enm`, `ang`).

## Italian

No model or tool normalises or translates historical Italian; no
historical-Italian LLM or GGUF exists. What exists:

| kind | name | link | period | size | licence | verified |
|---|---|---|---|---|---|---|
| method | temporal-context prompt ("Italian text from 1687"), Levchenko, IRCDL 2026 | ceur-ws.org/Vol-4240/paper45.pdf | 1600s | — | paper CC BY 4.0 | yes: −58 % perplexity; long s (ſ) costs +28 % tokens |
| method | historical forms → modern lemmas with MIDIA and TLIO, Alzetta & Montemagni, CLiC-it 2025 | ceur-ws.org/Vol-4112/3_main_long.pdf | 1200–1900 | 3 gold corpora | paper CC BY 4.0 | yes |
| tool | Norma (trainable normaliser) | github.com/comphist/norma | any | — | LGPL-3 | yes |
| tool | cSMTiser (character SMT normaliser) | github.com/clarinsi/csmtiser | any | — | LGPL-3 | yes |
| model | medieval-it5-base (modern → medieval, the WRONG direction; retrainable reversed) | hf.co/leobertolazzi/medieval-it5-base | 1200–1300 | 248M | not stated | yes |
| model | BERToldo (BERTs by period; encoder, not chat) | github.com/dhfbk/historical-bert (weights on Google Drive) | several | — | Apache-2.0 | yes |
| parallel | ita2medieval (medieval verse ↔ modern paraphrase) | hf.co/datasets/leobertolazzi/ita2medieval | 1200–1300 | 6.7K pairs | **none stated** (scraped) | yes |
| parallel | oldIT2modIT (part of the modern side is GPT-4o) | hf.co/datasets/cybernetic-m/oldIT2modIT | 1200–1300 | 200 | MIT | yes |
| parallel | Dante, Boccaccio in public-domain English (Longfellow, Payne) | gutenberg.org/ebooks/1004, /23700 | 1300s | whole works | PD | yes |
| parallel | Ventisettana ↔ 1834 English *Promessi Sposi* | ceur-ws.org/Vol-3596/paper48.pdf | 1827 | whole novel | ? | search only |
| corpus | Italian Wikisource | hf.co/datasets/wikimedia/wikisource (20231201.it) | all | 65K texts | CC BY-SA 3.0 | yes |
| corpus | Corpus OVI | ovi.cnr.it | <1400 | 30.2M tokens | search only, no download | from sources |
| corpus | MIDIA (lemmas, POS) | corpusmidia.unito.it | 1200–1900 | 7.5M words | search, CSV export | from sources |
| corpus | DanteSearch XML | dantesearch.dantenetwork.it/DanteSearchXML.zip | 1300s | all Dante | unknown | yes |
| lexicon | TLIO | tlio.ovi.cnr.it | <1400 | — | online only | yes |
| lexicon | MIDIA *formario*: ~550K historical forms → modern lemma + POS (TreeTagger lexicon, built on Morph-it!) | not published; ask infocorpusmidia@gmail.com | 1200–1900 | 70K lemmas | corpus CC BY-NC 4.0; lexicon terms unknown | 2026-11-17: no download (see below) |
| lexicon | Crusca's five Vocabolario editions | accademiadellacrusca.it | 1612–1923 | — | online only | from documents |
| lexicon | Tommaseo-Bellini | tommaseobellini.it | 1800s | — | all rights reserved | yes |
| eval | Bologna historical-Italian LLM benchmark | github.com/Zhangshibf/Benchmarking-Large-Language-Model-on-Historical-Italian | 1200–1900 | ~200 a task | CC BY 4.0 | yes: Minerva-7B 0.16, Claude 0.93 on Manzoni word sense |
| eval | Zibaldone NER | arxiv.org/abs/2505.20113 | 1817–1832 | 2,899 mentions | ? | paper |
| eval | UD_Italian-Old | universaldependencies.org/treebanks/it_old | 1300s | 119K tokens | CC BY-SA 4.0 | yes |

## Word lists for scoring or checking words (2026-11-17)

Asked by the louise-ocr session (via München): what can score or check
OCR words of 1200–1800 Italian. Nothing is downloaded or held by Louise yet.

| resource | what | where | licence | usable now |
|---|---|---|---|---|
| MIDIA *formario* | ~550K historical forms → modern lemma, POS; made for TreeTagger over MIDIA's 1200–1900 texts; reused by CODIT (Prague) | not published (D'Achille & Iacobini, SLI LIV, 2022, pp. 210–212); contact infocorpusmidia@gmail.com | corpus CC BY-NC 4.0 (site); the lexicon's terms unknown | no: ask the team (operator's choice; nothing sent) |
| MIDIA corpus | 7.5M words, ~800 texts, searchable by form, lemma, period | corpusmidia.unito.it (search, per-query CSV export) | CC BY-NC 4.0, cite MIDIA | search only: no bulk; NC is a yellow class (invariant 9) |
| Morph-it! | 568,771 modern forms → lemma, 28.5K lemmas (Baroni & Zanchetta, Bologna) | sslmit.unibo.it/morphit (site answered 402 on 2026-11-17) | CC BY-SA 2.0 and LGPL (dual, per its readme; confirm on the licence file) | yes, for words still in use; no old forms |
| Italian Wikisource | 65K texts, old editions among them; a frequency list by period can be derived | hf.co/datasets/wikimedia/wikisource (20231201.it) | CC BY-SA 3.0 | yes: derive the list |
| UD_Italian-Old | Dante's works, 119K tokens, lemmatised | universaldependencies.org/treebanks/it_old | CC BY-SA 4.0 | yes: small, 1300s only |
| DanteSearch XML | all of Dante, lemmatised | dantesearch.dantenetwork.it/DanteSearchXML.zip | unknown | no: terms first |
| TLIO, OVI | Old Italian dictionary and corpus, <1400 | tlio.ovi.cnr.it, ovi.cnr.it | online only, no download | no |
| RNNTagger (Old Italian) | neural tagger and lemmatiser listing Old Italian | cis.uni-muenchen.de/~schmid/tools/RNNTagger | academic, per its page (unchecked) | a model, not a list: torch, behind HTTP (invariant 7) |
| BERToldo | BERT models by period (encoder, can score words in context) | github.com/dhfbk/historical-bert | Apache-2.0 | yes: a model, torch |

No language model for chat or generation in historical Italian exists
(searched 2026-11-16). For 1500–1800 the spelling rules in
`app/historical.py` (ſ, et, -tione, h-) and its marker words are a small
start, written by hand, not a list.

## Scoring OCR readings by period (2026-11-17, for louise-ocr)

Asked by louise-ocr via München: scoring OCR candidates of 1200–1800
Italian WITHOUT modernising (choose between two engines' readings of a
block, flag improbable words). Nothing here is adopted without the
operator's choice. "Counted" = measured in this session; else stated.

**(1) What can score candidates by period**

| resource | period | scores by | licence | caveat |
|---|---|---|---|---|
| TLIO's lists of forms (each entry's "0.1", every attested spelling; ABBONDARE has ~150) | <1400 | lookup: a form is attested or not, and under which lemma | none stated; the operator, 2026-11-17: "assume we can use it for non commercial purposes, I will clarify the licensing myself" | being fetched (below); non-commercial; NOT shared or published until the licence is recorded |
| Italian Wikisource, frequency by century | 1200–1900 | log-frequency of the exact form in its century | CC BY-SA 3.0 (a derived list is CC BY-SA, attributed) | many texts are modern critical editions, partly regularised; prints of the 1500s–1700s are closer |
| BERToldo till1500 / 1500-1700 / 1700-1900 | as named | masked-LM pseudo-log-likelihood of a word in context | Apache-2.0 | uncased (see 3) |
| Morph-it! | 1900s | lookup | CC BY-SA 2.0 / LGPL | modern forms only: a "probably modern" signal, never a reason to reject an old form |
| UD_Italian-Old | 1300s (Dante) | lookup, lemma | CC BY-SA 4.0 | 119K tokens |
| MIDIA *formario* | 1200–1900 | lookup | not published | ask the team (operator's choice) |

Lists are evidence FOR a reading, never against it: an OCR form absent from
every list is flagged for review, not rejected; old spelling varies freely
(*abondare / habundare / abunnare*).

**(2) A Wikisource frequency list by century — counted.** The dump
(hf.co/datasets/wikimedia/wikisource, config 20231201.it, one Parquet
file, 115 MB, 65,047 pages) has no date field, but each work's root page
begins with its *Intestazione* (author, year or "XVI secolo", subject)
and its subpages inherit it (`IncludiIntestazione`). Dated so: 12,078 of
15,440 works. Words by century of the work: 1200s 0.5M, 1300s 1.5M,
1400s 0.8M, 1500s 3.0M, 1600s 0.9M, 1700s 1.1M, 1800s 5.8M. Proposed
format, as louise-ocr asked: TSV `form<TAB>count<TAB>century`, the form
as written (case-folded only, no accent or ſ folding), a header naming the
snapshot, the licence and the attribution. Where: built by a script on
the node's side into louise-ocr's data directory (my container is
ephemeral and not reachable from there); a few hours' work once chosen.

**Built (2026-11-17): `tools/lexicon/wikisource_period_freq.py`**, in
louise-ocr's format (`form	count	century	source`, UTF-8; lower-cased, ſ as
s, accents and elision kept; century = its first year), with `LICENCE`
(the CC BY-SA 3.0 legal code, byte-exact, sha256 3f941b3b…f72f) and
`SOURCES` beside it. 10 s on 8 cores; 602,396 rows for 1200–1899 from the
20231201.it dump (sha256 of the TSV f46d3ffd…056f). The data is not in the
repository: the node builds it (`python -m tools.lexicon.wikisource_period_freq
it.parquet --out /mnt/ssd3/louise-ocr/lexicon/it-old/`). TLIO's forms
cannot travel this way: this repository is public and TLIO's use is
non-commercial and unshared until the operator records its licence.

**(3) BERToldo on CPU.** Eight models (Palmero Aprosio, Menini, Tonelli,
LT4HALA 2022): BERT-base architecture (110M parameters), 128-token
window; trained on Wikisource and Liberliber, split till1500,
1500-1700, 1700-1900, plus "all" and four continued from
dbmdz/bert-base-italian-xxl-uncased. Scoring a word = mask each of its
subwords and sum the log-probabilities (pseudo-log-likelihood, Salazar et
al., ACL 2020): one forward pass per subword, so comparing two readings
costs a few passes for the words that differ only. Expected tens of ms a
pass on CPU — NOT measured. Caveats: UNCASED, and BERT's uncased
tokenizer normally also strips accents, so *perché/perche* and probably
ſ are not distinguished — check the weights' tokenizer config before use;
weights are on Google Drive only (no Hub card); torch or ONNX in
louise-ocr's own container, never in the app (invariant 7); llama.cpp has
no masked-LM head, so not through the node's engine.

**(4) What a period plugin should carry**

- *Characters, by MUFI* (Medieval Unicode Font Initiative, the standard
  for medieval characters — do not invent codepoints): ſ, the tironian
  et ⁊, &, ę (e caudata), æ/œ, the nasal macron and tilde (ā ẽ ũ = a
  following m/n), ꝑ ꝓ p̄ (per, pro, pre), ꝯ (con), q̃/ꝗ, superscript
  letters, ligatures (ct, st, ſt, ff); and the OUTPUT convention the
  plugin declares: diplomatic (marks kept) or semi-diplomatic
  (abbreviations expanded in brackets), never silently mixed.
- *Distribution rules*, as evidence: u/v by position (v initial, u
  medial, until about 1600), i/j/y, ſ never final, h- before vowels
  (*huomo, hauere*), -tione/-zione, word division (*de la*, *allhora*),
  the line-end hyphen ("=" or "-"), Roman numerals, catchwords and
  running heads to drop from the text, not to score.
- *Abbreviations*: Cappelli's *Lexicon abbreviaturarum*, all 14,357
  entries, digitised by Ad fontes (University of Zurich), public domain,
  a ZIP of an Excel table plus an image per abbreviation
  (adfontes.uzh.ch, "Cappelli: Downloadable Data"; crowdsourced, errors
  possible). Mostly Latin and manuscript, which old Italian prints share.
- *Latin*: passages in Latin are common in these books; the plugin needs
  a Latin lexicon or the language decided per block, or Latin words read
  as improbable Italian.
- *Ground truth to measure*: none found for Italian prints. GT4HistOCR
  (CC BY 4.0) is German Fraktur and Early Modern Latin; a measured set of
  Italian pages would have to be made.

## TLIO fetch (2026-11-17)

Operator: "Can you gently fetch all the content and make a glossary?",
then "So far assume we can use it for non commercial purposes, I will
clarify the licensing myself". The terms text recorded with the data
(TERMS.txt, sha256 b7e29c1a15627afe0e92925f05e0b5d466c37193c46c10722b7ba3614249ce1d)
states the site gives no licence, CNR's IRIS says "all rights reserved,
unless stated otherwise", and the operator's words. 43,911 published
entries (the 26 lists of tlio.ovi.cnr.it/TLIOlemm/?q=<letter>), one
request at a time, at most one a second, raw pages kept gzipped for
verification; robots.txt disallows /TLIO/ (the query system) only, which
is not used. Non-commercial; not shared with peers, not published, not
in this repository until the licence is recorded.

## Other languages (top picks; full tables in this session's reports, to be added)

- **English.** OE: Bosworth-Toller ("free for any purpose") and Wiktionary
  glosses in the prompt. ME: Wiktionary ME, Wycliffe↔modern Bible verses
  (scrollmapper, verse-aligned). EModE: VARD 2 (CC BY-NC-SA) or MorphAdorner
  as a spelling pre-pass; MonadGPT has a GGUF. Corpora: EEBO/ECCO/Evans-TCP,
  CC0 (hf.co/Vintage-LLM).
- **German.** 1700–1899: Transnormer ByT5 (hf.co/textplus-bbaw, Apache-2.0,
  98.9 % word accuracy), DTAK lexicon pairs (dtaec-lexicon, CC0). ENHG:
  glossary in the prompt (Volk et al., KONVENS 2024). MHG: ReM's
  normalisation layer (CC BY-SA), Lexer (terms unconfirmed). OHG: nothing.
- **French.** 1600–1800: rbawden/modern_french_normalisation (CC BY 4.0,
  97 % word accuracy, 6M params, CPU). Middle French: FreEM-SemiD-norm.
  Old French: BFM2022 (Licence Ouverte), Wiktionary glosses; no translator.
- **Spanish.** Siglo de Oro: modernisa-byt5-base (hf.co/modernisa, Apache-2.0,
  CER 4.2 % on verse drama, untested on prose). Medieval: no model; Wiktionary
  Old Spanish glosses, OSTA corpus (CC BY-NC-SA), CODEA's regularised
  presentations as pairs (terms unknown). 1700–1900: Post Scriptum letters,
  original ↔ modernised (licence unconfirmed).

The seq2seq normalisers (ByT5, FSMT, M2M) do not run in llama.cpp: each
would be a small HTTP service (invariant 7), like TRANSLATE_URL.

## Options for Italian

| option | what it does | pros | cons |
|---|---|---|---|
| status quo | — | no work | summaries stay wrong |
| A. period in the prompt | "Italian text, c. 1320" from the author's dates or the work's | free; measured −58 % perplexity on 1600s | does not supply vocabulary the model lacks |
| B. typographic pre-pass, 1500–1800 | ſ→s, u/v, &→e, -tione→-zione, on the copy the model reads (never the corpus, invariant 11) | deterministic, cheap; ſ is the main token cost | spelling only; rules written by hand per period |
| C. held human translation | read the English edition the collection holds (Dante, Boccaccio …) beside the original | human quality; the operator's 2026-11-03 idea 1 | famous works only; matching passages across editions is coarse |
| D. few-shot examples | 3–5 old↔modern pairs in the prompt | no new model | ita2medieval has no licence (invariant 9); oldIT2modIT is 200 pairs, part GPT-written |
| E. glossary pinned in the prompt | historical forms → modern words (MIDIA, TLIO) | fixes vocabulary, the real gap for 1200–1500 | TLIO has no download; MIDIA's lexicon availability unverified |
| F. LoRA of the chat model → GGUF | trained on ita2medieval + Wikisource | largest gain expected for 1200–1500 | training run, data licence, one more model to deploy |

A **reader** (the "translation plugin") is one entry per (ISO 639 language,
period): its prompt line (A), its pre-pass rules (B), its examples (D), its
glossary (E) and optionally its model server (F). Summaries and Ask pick it
from the record's language and date, and the answer says which reader ran.

## Built (2026-11-16): A, B, C under Quality

Operator: "A, B, C, make it part of the quality search settings and use
them only if the returned results fall into the use case (some recent
books may have ancient language quotes, also use the prompt as trigger)".
Reviewed by the architecture session (through München, the same day):
design right, five defects fixed, three rulings applied.

`app/historical.py`, on BOTH answer paths when the request's `speed` is
false (Quality, the default): `prepare` from `api._answer_messages`
(/v1/chat/completions and the Ask page, which now sends `speed` with the
answer), `read_sources` from `app/ask/chat.py`'s tool results (`ask chat`,
`speed` on its body). Per source the model is given:

- **when:** the passage has ≥ 2 markers of older Italian (period words
  such as *sanza, puote, avea*; elided *'l, ch'i'*; the lower-case split
  preposition *de la* before a lower-case word; the spellings B rewrites,
  but not *et*); or the record's language is an ISO 639-3 historical stage
  (`enm`, `fro`, `gmh` …); or the work was first published before 1800
  (`first_published`, the one date field, attached elsewhere: the WORK's
  first publication) and this edition is the work in its own language — no
  translator credited, `work_lang` equal where known; where the work's
  language is not known, the year lowers the bar to one marker; or the
  question names an older period and the passage has one marker. Per
  passage, so a modern book quoting Dante is read so only where it quotes.
  Not markers (the review): *dee, uopo, anco, fora*, capitalised *de la*
  (Pedro de la Cruz), *&*, *et*.
- **A:** the head says «older Italian» (or the stage), and "first
  published <year>" where known; the system prompt says how to read such
  a source, and that a quotation from a modernised source says so. In
  `ask chat` it goes before the answer-language sentence, which stays last.
- **B:** ſ→s, et→e (never before al., cetera, seq.), -tione→-zione (never
  -stione), h- words (huomo, hauea …) in the copy the model reads. "&" is
  never rewritten. The hit the page, the PDF and API clients show is
  untouched (invariant 11).
- **C:** an edition of the same work in the answer's language or English
  among the sources given is named: "a translation of it is source [k]".
- **What the reader sees:** a badge on the source (`reading` in
  `api.source_entries` and `ask chat`'s `sources`; the Ask page's relay
  event `reading`), never a line in the answer, which speech would speak
  and the PDF print. The log line stays (`chat`).

The markers are a heuristic of ours: no detector exists. Tests:
`tests/test_historical.py`, the review's modern sentences among them.


## The hybrid (2026-11-16, operator's "B" then "A")

Relayed by München from the architecture session: whether a WORK is in an
older stage of its language becomes an ANNOTATION; the answer-time helps
stay at answer time.

- **The claim** `language-stage` (09 §9.5, 02 §2.10, §9.14; Codex version
  9): `subject` a record, `object` the stage's canonical id
  (`iso639-3:<code>`, or a Wikidata item where 639-3 has none), method
  `matcher` (historical-markers 1, the record alone: its markers, PG's
  marc520 year, the RDF's translator credit — "A collection has only the
  information contained in its corpus", operator, the same day), `wikidata`
  (the work's P407; an annotations collection may take claims from an
  outside source, labelled by method: operator's "A", #703 — the method is
  allowed, its builder is the next PR) or `human`; a human
  claim may say `object: null`, "not an older stage". Identified by (type,
  subject, method kind), so a person's and the matcher's coexist; read in
  the order human, Wikidata, matcher. `object_hash` null; the edge
  `annotations:language-stage` to the stage, none for null.
- **Where** (operator's "A"): in gutenberg-book-editions, re-created at
  version 9 (its 4,387 claims carried unchanged), title gaining "and the
  stage of each edition's language". München re-creates and shares on the
  operator's word, after Frankfurt's review.
- **The matcher** (`app/historical.py` `stage_claim`, run by
  `tools/annotations_language_stage.py` under supervise-pass, parts on the
  parse pool, resumable per part): the MEDIEVAL markers over the whole
  record — ≥ 2 per 1,000 words, or ≥ 0.5 where PG's summary states a first
  publication before 1582 — never for a translation (a `trl` credit),
  never for a work first published from 1582. The stage claimed is
  Wikidata's Old Italian, Q135913388 ("from about 960 until the founding
  of the Accademia della Crusca in 1582"). 1582–1800 Italian has no
  Wikidata item and no 639-3 code (searched 2026-11-16: "Middle Italian",
  "Early Modern Italian", "italiano rinascimentale", none), so the matcher
  claims nothing for it (architecture: never a label of ours); such books
  are read by their passages, where the Renaissance spellings are markers.
  Naming the stage by a standard identifier is vocabulary, like an ISO
  639-3 code: nothing about a record is read from Wikidata (architecture,
  under #701).
- **The merge** keeps every line of the existing claims file and replaces
  the matcher's earlier stage claims of any version.
- **`same-work`** (02 §2.10, 09 §9.5, amended): "two records are versions
  of one work — editions in one language or in different languages". The
  sentence said "in different languages"; gutenberg-book-editions holds
  7,154 claims (4,357 wikidata, 2,797 matcher) of which 5,644 join editions
  in ONE language (counted on the node by München), so the collection was
  right and the sentence wrong (architecture's ruling). A one-language
  refusal, built and withdrawn the same day, is not in the code. An older
  source whose same-work partner carries no stage is pointed at it as
  "a modern version of it".
- **At answer time** (Quality): the claim is attached to the hit as the
  fact `language_stage` (app/ask/annotations.py); a record with a claim is
  read by it (or not at all for a person's null); a record without one is
  read by its passage alone; no year decides it (b2af94f's trigger is
  gone). The badge names the method: "by its text", "by Wikidata", "by
  hand", "by this passage". C also points at a modern version in the same
  language ("a modern version of it is source [k]").
