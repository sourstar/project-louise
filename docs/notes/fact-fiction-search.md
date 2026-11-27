# Facts or literature: what a search returns (2026-11-27)

*The operator, 2026-11-27, after the framing was measured not to work on
either chat model (the 9B and the 35B count a novel among a fact's sources
whatever the header and WORK_RULE say; tools/measure's judged set): "If a
novel is cited as a novel for now it is fine. The problem is when a novel
is aggregated as a fact. The search itself shouldn't return novels when I'm
looking only for facts and vice versa." Then, on the three choices put to
them: "1 and 2 agree, 3 facts all non specified collections by default ok
but Wikipedia is a mixed bag, maybe an annotation could be added?"*

## 1. The decisions

- **A search has a KIND: facts, literature or all.**
  - In facts, it returns no literary work: a novel, a poem, a play, a tale
    retold, as its record states them (`retrieval.work_verdict`).
  - In literature, it returns only literary works.
  - All is today's search.
- **The kind is chosen AUTOMATICALLY from the question, SHOWN, and one tap
  changes it** (decision 1). The page says "facts only: 3 literary works
  left out", and the tap re-runs the search with the other kind or with
  all. A filter is the reader's to see and undo (CLAUDE.md invariant 4,
  "removing is a filter the reader asks for"). A guess that hides results
  is never silent.
- **A work of opinion or belief stays in a facts search, attributed to its
  author** as WORK_RULE already says (decision 2).
- **A collection whose records carry no kind counts as facts** (decision
  3): EUR-Lex, the national laws, PubMed, deposits.
  - It is left out of a literature search.
  - Wikipedia is the exception below.

## 2. Wikipedia: an annotation, from Wikidata

Wikipedia holds articles about fictional things (a character, a place, an
event of a novel) and about real ones side by side. Its own categories say
what an article is ABOUT, and read as a genre they would call an
encyclopedia literature (the reason `CLASSIFIED_PLUGINS` is Gutenberg
alone). So the kind of a Wikipedia article comes from an annotations
collection, as the `language-stage` claims do (02 §2.10):

- **The claim** `work-kind` on a Wikipedia article: `object`
  `fictional-entity` or `creative-work`.
  - Its method is Wikidata: the article's item (its sitelink) has
    P31/P279* reaching a fictional entity (fictional character,
    location, event, universe; a mythological or legendary figure) or a
    creative work (literary work, novel, play, poem, film).
  - It is labelled "by Wikidata" wherever it is shown, never as a fact of
    the article itself (CLAUDE.md, "A collection holds only the
    information contained in its corpus": an annotations collection may
    take claims from outside, each naming its method).
  - A PERSON CORRECTS IT, as with the stage: a `human` claim on the same
    article wins.
- **In a facts search** a `fictional-entity` article is left out like a
  novel. A `creative-work` article stays: it states facts ABOUT the work
  (its author, its date, its reception).
- **In a literature search** both are kept.
- **Its plot section** (a `creative-work` article's "Plot" or "Synopsis")
  narrates the work. Treating those chunks as literature is step 3, built
  only if the judged set shows plot passages taken as fact.

## 3. How it runs

- **THE GUTENBERG RECORD'S KIND** is computed once from its own record by
  `work_verdict`, and kept as a column of the manifest's documents. That
  is a schema change, the operator's request. A rule change recomputes it
  at the next records build: the column is derived and re-creatable.
- **THE FILTER** applies to the candidates BEFORE the rerank, so a left-out
  record costs no rerank time. It over-fetches where the filter thins a
  leg, so the reranker still sees its depth.
- **THE QUESTION'S KIND**:
  - the chat model is asked in one short call (bounded at 2 s, cached by
    question): facts, literature, or unsure;
  - unsure, or no answer in time, gives all;
  - a question that names a work, or asks what a work says, is
    literature. The judged set's `about_the_work` questions test that.
- **THE PAGE**:
  - the chip shows the kind, and how many were left out with their basis
    ("by its Gutenberg category", "by Wikidata");
  - one tap gives the other kind or all;
  - `/v1` and MCP take `kind` as a parameter (default: automatic);
  - the trace records the kind and the left-out count.

## 4. The steps, each measured on tools/measure's judged set (both chat models)

1. Gutenberg's kind column, the filter, the question's kind, the chip.
   Done when no literary source is counted as a fact's evidence in a facts
   search, and the about-the-work questions get literature.
2. Wikipedia's `work-kind` annotations from Wikidata (P31/P279* over the
   held dump, through the sitelinks).
3. Only if measured necessary: plot sections as literature.
