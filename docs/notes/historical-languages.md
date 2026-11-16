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
| lexicon | MIDIA lexicon: 571,779 historical form → modern lemma pairs | (Alzetta & Montemagni) | 1300–1500 | 70K lemmas | ? | availability unverified |
| lexicon | Crusca's five Vocabolario editions | accademiadellacrusca.it | 1612–1923 | — | online only | from documents |
| lexicon | Tommaseo-Bellini | tommaseobellini.it | 1800s | — | all rights reserved | yes |
| eval | Bologna historical-Italian LLM benchmark | github.com/Zhangshibf/Benchmarking-Large-Language-Model-on-Historical-Italian | 1200–1900 | ~200 a task | CC BY 4.0 | yes: Minerva-7B 0.16, Claude 0.93 on Manzoni word sense |
| eval | Zibaldone NER | arxiv.org/abs/2505.20113 | 1817–1832 | 2,899 mentions | ? | paper |
| eval | UD_Italian-Old | universaldependencies.org/treebanks/it_old | 1300s | 119K tokens | CC BY-SA 4.0 | yes |

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

`app/historical.py`, called from `api._answer_messages` when the request's
`speed` is false (Quality, the default; the Ask page now sends `speed` with
the answer as it did with the search). Per source the answer is given:

- **when:** the passage has ≥ 2 markers of older Italian (period words such
  as *sanza, puote, avea*; elided *'l, i'*; split *de la*; the spellings B
  rewrites), or the record's language is an ISO 639-3 historical stage
  (`enm`, `fro`, `gmh` …), or the question names an older period (a
  century, *Trecento*, *medieval*, a year before 1800) and the passage has
  one marker. Per passage, so a modern book quoting Dante is read so only
  where it quotes.
- **A:** the head says «older Italian» (or the stage's name) and the system
  prompt says how to read such a source.
- **B:** ſ→s, &/et→e, -tione→-zione (never -stione), h- words (huomo→uomo)
  in the copy the model reads, and the head says which rules ran. The hit
  the page and the PDF show is untouched (invariant 11).
- **C:** an edition of the same work in the answer's language or English
  that is among the sources given is named: "a translation of it is
  source [k]".
- The answer ends with one line saying which sources were read so, also
  logged (`chat`).

The markers are a heuristic of ours: no detector exists. Tests:
`tests/test_historical.py`.
