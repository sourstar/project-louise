# Request to the architecture session: a Wikisource design note (2026-11-19)

*From the wiki-gutenberg planning session, on the operator's request: "talk
to architecture for 2, I want a design note". It could not be sent (the
architecture session needs a trusted device). Paste it there.*

**The ask:** a design note for a WIKISOURCE collection, a scan-backed
source of memoirs (and history) beside gutenberg. It serves the
wiki-gutenberg graph first, the general collection after.
- Context: `docs/notes/wiki-gutenberg-claude-plan.md` §§9–12 (the
  roadmap, the decisions, and the selection rule pinned by Codex version
  12 `annotates[].select`) and `docs/notes/wiki-gutenberg-memoir-titles-*.toml`.
- Branch: `ccr-8a5c4013-0oqgjh`.

**Measured on 2026-11-19 (Wikidata Query Service):**
- Only 4,122 Wikidata items carry a Gutenberg ebook id (P2034), of about
  78,000 PG texts. 54 of them are typed memoir, autobiography or diary.
- Our 66 Italian memoirs: 3 are known to Wikidata, and none is linked to
  it.wikisource.
- 241,707 Wikidata items link a Commons scan (P996).
- Items sitelinked to each Wikisource: it 47k+, en 345k+, fr 87k+, de
  283k+, es 33k+.
  - More than 95% carry no genre (P136), so Wikidata cannot select
    memoirs on Wikisource; the categories must come from Wikisource
    itself.
- Text dumps (`pages-articles.xml.bz2`, 2026-11-01): it 0.45 GB, en 3.44,
  fr 2.60, de 0.58, es 0.25.
- The fetch amounts per language and book category:
  `docs/notes/wikisource-fetch-breakdown.md`.

**Points the note should decide** (pros and cons for each, per CLAUDE.md
Style):

1. **The origin form** (invariant 11): the dump's wikitext (the proofread
   Page: namespace), the rendered text, or the scan plus our own OCR. And
   how a record verifies against the origin: Page revision ids? The scan's
   sha1 from Commons?
2. **Bulk first** (München's rule of 2026-11-15, CLAUDE.md #679): the dumps
   are the bulk, and the per-page API is used only for what they lack.
   Whether scans are fetched at all, or only their sha1 and metadata.
3. **What a work is:** the ns0 page and its subpages. A work may span
   several Index files. Editions and translations across languages: one
   collection, with languages as slices.
4. **Overlap with gutenberg:** the same work in both. Same-work claims;
   which transcription the graph reads; no double embedding.
5. **Licences:** Wikisource text is CC BY-SA (attribution per page
   history); scans carry PD or Commons licences. The gate is invariant 9.
6. **The selection:** `wiki-gutenberg-select` reads LCSH, which Wikisource
   lacks. Wikisource categories per language plus operator-checked title
   lists, pinned the same way (Codex 12).

Please write it as `docs/notes/wikisource-design.md` and tell the operator.
