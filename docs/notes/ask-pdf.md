# The Ask page's "save pdf"

*2026-11-11. The operator: "Next to 'read aloud' I would like to have a
'save pdf', with the query, the answer and all the references with the link
to the original source. Page format portrait A4 but configurable in the
settings to portrait letter." Layout proposed with two sample files and
accepted; how it is made, choice **A**: the server makes the file.*

## What it is

- **The button** sits beside "read aloud" in the answer's head and shows
  once the answer is complete. **The paper** is a choice in the Ask
  page's Settings, A4 (the default) or Letter, kept in the browser like the
  counts.
- **What goes in is what the reader saw** (`POST /ask/pdf`,
  app/ask_pdf.py `AskPdf`). The page sends:
  - the question as asked;
  - the answer as shown, in the language it is shown in;
  - per entry, its title and passage as shown, with the published ones
    where the page translated them;
  - the settings.

  The node does not search again: a second search could rank differently.
- **The layout** (templates/pdf/answer.html):
  - a header line (LOUISE, the reader's date and time, the node);
  - the question;
  - one line of how it was answered (language, collections, mode, counts);
  - the answer, with each [n] a link to its reference;
  - every reference in rank order. Each says cited or not, its collection,
    language, section and rank, and what moved it (invariant 4, rule 2). It
    also gives the passage (marked "translated from …" where it was), the
    **original source** link, and the link to **this node's reader** at the
    passage;
  - a footer on every page with the question and "page n of N".
- **Not in it yet:** which chat model wrote the answer. The answer job does
  not record it, and the line is not written from a guess.

## How it is made

- **The standard:** HTML laid out by CSS Paged Media (W3C: `@page`, margin
  boxes, `counter(pages)`), rendered by **WeasyPrint 70.0** (BSD-3).
  - The standard alternative is the browser's print to PDF from the same
    HTML and CSS: the operator's choice B, not taken because it means a
    print dialog and page numbers that differ per browser.
- **Nothing is fetched.** WeasyPrint's `URLFetcher` is replaced by one that
  refuses every URL and allows no protocol, so links are written, never
  followed (invariant 5). Every character is text until a rule makes it
  markup:
  - Jinja autoescapes;
  - the answer's markdown is the Ask page's own subset (`md`), ported
    escape-first;
  - only http(s) origin links are written;
  - the reader link is built from its parts.
- **Bounded.** At most 2 PDFs are laid out at once, and a third waits up
  to 30 s, then gets a 503 that says so. The request's sizes are capped
  (200 references, 40,000 characters a passage).
- **Measured** (this session's container, not the node): one page in
  1.5–2.1 s, about 30 KB.
- **The image:** Pango, HarfBuzz and the Noto fonts (core + CJK) cover
  every script a collection holds. About 140 MB installed, using Ubuntu
  24.04's sizes; trixie's are to be read at the build. An image without
  them answers 503 and says what supplies it.

## Through the other interfaces (2026-11-11)

The operator: "Since we produce it internally, make it available through the
other interfaces too." One document and one renderer serve every interface
(`app/ask_pdf.py` `make`):
- **`louise ask pdf QUESTION [--paper a4|letter] [--out FILE]`:** Louise
  answers, as `ask chat` does, and the PDF is written to a file. Its
  references are the sources the answer numbered, each with the passage the
  model was shown (`chat.ask(passages=True)`).
- **`POST /api/ask/pdf`:** the same over HTTP. With `answer` and
  `references`, it lays out a client's own answer instead, without asking
  the model.
- **MCP `louise_pdf`:** an agent's own answer and the hits it used,
  returned as an embedded resource (`application/pdf`). It is offered to MCP
  clients only. Louise's chat model never gets it (05 §5.4, amended).
- **`/v1/chat/completions` (Open WebUI), a link beside the sources**
  (corrected the same day — the operator: "The links to the origin are
  available in the list of the sources"; the note had said this protocol
  could not carry it). With `sources: true`, the answer and its sources are
  kept (`ask_pdf.keep`: this process's memory, 500 answers, 24 hours, a
  128-bit id) and the stream's chunk after the last word carries
  `louise_pdf`, the path `/api/ask/pdf/<id>` (the non-stream answer, the
  same key). `GET /api/ask/pdf/<id>?paper=a4|letter` lays it out. It is an
  Ask path, behind the same gate as the reader's links (app/auth.py
  `READER_PATHS`). An answer no longer kept (a day later, or after a
  restart) gets 404 and says to ask again. The pipe lists it as one more
  source, on the person's `PAPER` valve.

Tests for the link: tests/test_openwebui_pipe.py (listed, opens, only with
`sources: true`) and tests/ask/test_pdf_interfaces.py (kept a day, bounded,
the 404). Tests: tests/test_ask_pdf.py (the markdown, links, names, the document,
limits, the paper sizes and links of real PDFs, no fetch, the route) and
tests/test_ask_save_pdf_browser.py (what the page posts, in Chromium).
