# Ask Louise from an e-reader: the answer as an EPUB

*2026-11-22. The operator: "evaluate a 'fake' epub that is instead the ask
louise interface to be able to provide an interface to ebook readers
supporting epub on a remote folder." DESIGN, nothing built. What the readers
can do was researched the same day, from their source code and
documentation (sources at the end; † marks a forum or user report).*

## 1. The finding

An EPUB cannot be the interface itself: the readers that matter run no
script from inside a book (§2). The catalogue the readers already browse
can be. An **OPDS catalogue with search** turns the reader's own search box
into the question. Each answer comes back as an EPUB made for that question:
the "fake" book is the **result**, not the interface. That runs today on:
- KOReader, which runs on Kobo, Kindle, PocketBook and Android;
- Thorium, Librera and Moon+;
- Boox through either of those apps.

It uses no script, no form, and no token inside a file. Stock Kindle, stock
Kobo, Apple Books and Play Books have no catalogue client, so they need a
different way in (D, §3).

## 2. What the readers can do

| reader | catalogue (OPDS), search, login | remote folder | scripts inside a book |
|---|---|---|---|
| **KOReader** (Kobo, Kindle, PocketBook, Android) | OPDS 1.x, and 2.0 since v2026.07 [2]. Search by OpenSearch, an Atom template or an OPDS 2 link [1]. Basic auth, sent to the catalogue's origin only [1]. **15 s per block, 60 s per request** [3] | WebDAV, FTP, Dropbox: read, upload, make folders [4] | **none**: its EPUB engine has no JavaScript [5] |
| Thorium (desktop) | OPDS 1 and 2, search, Basic auth and OPDS Authentication [26] | none | yes (Chromium). Network calls likely work, but that is an inference from its code [27] |
| Librera (Android) | OPDS 1. **Search only through an Atom-templated `rel="search"` link**, not an OpenSearch description. Basic and Digest auth [31] | Dropbox, Google Drive, OneDrive [31] | unverified |
| Moon+ Reader (Android) | OPDS "online library" [30]. Search unverified | WebDAV, Dropbox (sync) [30] | unverified |
| PocketBook (native) | OPDS hidden outside Russia; a flag or a third-party connector enables it [18][19]† | Dropbox sync† [20] | unverified |
| Boox (native) | PushRead takes OPDS URLs; search and login unverified [21]. Android, so KOReader and Moon+ install | WebDAV, Dropbox, Google Drive, OneDrive [22] | unverified |
| Kobo (Nickel) | none; KoboOPDS is a third-party beta† [9] | Dropbox and Google Drive, download only, some models [10][11] | limited; "no interactive JavaScript elements" [12] |
| Kindle (native) | none; Send to Kindle takes an EPUB [15] | none | stripped [16]† |
| Apple Books | none | none | yes, with consent before remote resources [13][23] |
| Google Play Books | none | none | none [24] |

The EPUB standard (Reading Systems 3.3, W3C Recommendation, 2024) makes
scripting a SHOULD, network access a MAY, and form submission OPTIONAL. It
tells reading systems to treat every script as untrusted [38]. OPDS 1.2 has
the wider support; 2.0 is a living standard [40]. Calibre 9.18 serves both
from the same URLs, chosen by the `Accept` header [39].

## 3. Four ways, and the status quo

| way | what the reader does | pros | cons |
|---|---|---|---|
| **A. A scripted EPUB** (the literal "fake EPUB": an app inside a book that asks the node) | opens "Ask Louise.epub" and types into it | one file; works offline until asked | runs on Thorium and Apple Books only (KOReader, Kindle and Play Books run no script; Kobo is limited). Forms are optional and calibre blocks them. The book must carry the node's address and a token, which breaks invariant 8, or ask for a login the spec does not guarantee. Every reader is told to distrust it. **Rejected** |
| **B. A remote folder Louise serves** (WebDAV, files made on demand) | browses a folder | KOReader, Boox and Moon+ browse WebDAV | **no reader gives a text box over a folder**, so the question is asked elsewhere (phone, Ask page). Good only as delivery: "my answers" |
| **C. An OPDS catalogue with search** (recommended) | adds a catalogue once, then types the question into the reader's own search box | the reader's own interface; no script; Basic auth, which every client above supports. The standard: OPDS 1.2 (specs.opds.io/opds-1.2) + OpenSearch 1.1. Precedent: later.ink and Readeck serve EPUBs made on request over OPDS [41][43] | needs a catalogue client (KOReader, Thorium, Librera, Moon+, Boox through an app). Stock Kindle and Kobo cannot use it. KOReader's 60 s bound on a download |
| **D. Push to the reader's cloud** (Kobo's Dropbox or Google Drive folder, Send to Kindle) | asks on the phone; the answer lands on the device | the only way to stock Kindle and Kobo | the question and the answer go to a third party; their credentials have to be held by path; bandwidth budget (invariant 5); nothing to ask from the device itself |
| status quo: the device's web browser on the Ask page | opens the node's URL | nothing to build | Kindle's and Kobo's browsers are poor on e-ink; no book to keep |

**Recommendation: C**, with B's "my answers" shown as a catalogue shelf
rather than a folder. Building it does not stop D being added later.

## 4. Design of C

**What the reader adds, once (RULE 2).** The Connect page shows the
catalogue's URL, `https://<node>/opds`. The user name is the person's name
in `users.toml`, and the password is their token (`louise user add` gives
it). Nothing else is asked: the collections, the mode and the language are
what `ask chat` takes by default.
- An "e-reader" settings set belongs to the person's account, never to a
  form in the reader.

**The catalogue** (OPDS 1.2, Atom; served by `app/opds.py`, Jinja templates
like the PDF's):
- `/opds`, a navigation feed:
  - *Ask* (search);
  - *My answers*, the person's kept answers, newest first;
  - *Collections*, browsing by facet: a later step.
- Search is offered TWICE. An OpenSearch description
  (`/opds/search.xml`) serves KOReader and Thorium. An Atom-templated link
  `rel="search" href="/opds/search?q={searchTerms}"` serves Librera, which
  reads nothing else [31].

**A search returns its feed once the SEARCH is done, not the answer.** The
chat model takes longer than a reader waits, and the search answers within
KOReader's 60 s; that bound is still to measure (§6). The feed holds:
1. **"Louise: <question>"**, an acquisition link (`application/epub+zip`)
   to `/opds/answer/<id>.epub`.
   - The answer job starts when the search returns. It is the Ask page's
     relay job (`/ask/relay`), owned by the person.
   - The EPUB is made when it is downloaded. The download waits for the job
     up to 45 s, under KOReader's 60, and never longer (a bounded wait).
   - An answer still being written then gives the EPUB with its sources and
     one line: "the answer was not finished; *My answers* has it when it
     is".
2. **The relevance list's hits, in rank order**, each a book (below). A
   second list that answers another question is its own feed, linked from
   this one, never mixed into it (invariant 4).

**The answer EPUB is the "save pdf" document, written as EPUB.** One
document serves every interface: `ask_pdf.from_chat` → `AskPdf`. What the
PDF holds, the EPUB holds:
- the question and how it was answered;
- the facts first, as pinned blocks;
- the answer, each `[n]` a note reference (`epub:type="noteref"`; KOReader
  shows it as a popup);
- every reference in rank order: cited or not, its collection, language,
  rank, **what moved it and by how much** (invariant 4, rule 2), the
  passage, the origin link, and this node's reader link;
- the plates, as files inside the book. They are read by
  `figures.figure_bytes` with the figure route's refusals, so nothing is
  fetched (invariant 5). Every link is written, never followed;
- metadata: title "Louise: <question>", the node as creator, the date, and
  the answer's id.

**Each hit is a book:**
- **Gutenberg: the original EPUB from the node's own corpus,** where the
  record was parsed from one (the origin form since parser 7; a record
  parsed from the `.txt` is served as below). The bytes are byte-exact and
  so stable, which matters because KOReader's sync knows a book by its
  content hash [41].
- **Every other collection: the document as the reader page assembles it**
  (`app/reader.py`: its chunks in order from the index stores, its
  reference line, its origin link), written as an EPUB with **fixed bytes**:
  - a fixed zip timestamp;
  - the record's uid and snapshot as its identifier.

  So the same document downloaded twice is the same book.
- **The licence travels with it** (invariant 9, through `app/licensing.py`):
  - a yellow collection's book carries its attribution and licence line,
    as the reader page shows it;
  - a red collection, or one without its acknowledgement, lists the hit
    with no acquisition link and says why, as the figure route does.

**How the EPUB is written** (do not reinvent the wheel):
- The standard is EPUB 3.3 OCF and package. The zip comes from the standard
  library (`mimetype` first and stored), and the documents from the Jinja
  environment the PDF uses.
- A `toc.ncx` is added beside `nav.xhtml` for EPUB 2 readers.
- EbookLib was not taken: AGPL-3.0, and a new dependency.
- Checked by **EPUBCheck** (W3C/DAISY, the standard validator), which needs
  Java in the test image: the operator's word (a new dependency).

**Login.** The gate (`app/auth.py`, `tokens_of`) learns
`Authorization: Basic` (RFC 7617), the password being the token, for the
READER paths it already guards.
- Over plain HTTP the token is as exposed as the bearer token is today.
  Over HTTPS where the node has it.
- No book ever carries a token (invariant 8).
- KOReader sends the credentials only to the catalogue's origin [1].

**Kept answers.** *My answers* reads the relay jobs and `ask_pdf.keep`
(memory, 500 answers, 24 h), so a restart forgets them. Keeping them on disk
is personal data with its own rules: not in this step.

## 5. What it is not

- No script, form or network call inside any book. No deep link into a
  downloaded book: OPDS opens a book at its start. The answer EPUB carries
  the passage itself, and the reader link serves a browser.
- No new search. The catalogue calls what the Ask page calls, and the EPUB
  is what the PDF would be.
- Not a second account system: the users and tokens of 07 §7.4.

## 6. What it takes to build, and what to measure first

- **Measure first, on the node:** the Ask page's search time under corpus
  load, with `tools/measure/query_under_load.py`, against KOReader's 60 s.
  If search alone can exceed it, the feed answers with the job's link and
  *My answers* fills in later.
- **Build:**
  - the feed and OpenSearch templates and routes;
  - Basic auth in the gate;
  - the EPUB writer for `AskPdf` and for a reader document;
  - the Gutenberg original as an acquisition;
  - the Connect page's lines.
- **Tests:**
  - the feeds against the OPDS 1.2 and Atom schemas;
  - both search links;
  - bounded download waits;
  - same document, same bytes;
  - the licence refusals;
  - no token in any book;
  - EPUBCheck.
- **End to end:** KOReader's Linux build (its emulator) can drive the
  catalogue in a container, from adding it to downloading the answer.

## 7. The question for the operator

**What it is:** how e-readers reach Ask Louise.

| choice | effect | pros | cons |
|---|---|---|---|
| **C now** (recommended) | an OPDS catalogue; the search box asks, the answer is an EPUB | readers' own interface; standard; no script, no token in a file | not stock Kindle or Kobo; Java in the test image for EPUBCheck |
| C, then D | the same, then answers pushed to Kobo's or Kindle's cloud | reaches stock devices | third parties see the questions; their credentials held by path |
| A | a scripted book | one file | runs on two readers; a token inside a file |
| status quo | the device browser | nothing built | poor on e-ink, nothing kept |

If the operator says nothing: nothing is built; this note stands as the
evaluation.

## Sources

[1] https://github.com/koreader/koreader/tree/master/plugins/opds.koplugin ·
[2] https://github.com/koreader/koreader/pull/15696 ·
[3] https://github.com/koreader/koreader/blob/master/frontend/socketutil.lua ·
[4] https://github.com/koreader/koreader/tree/master/plugins/cloudstorage.koplugin/providers ·
[5] https://www.mobileread.com/forums/showthread.php?p=4138150 ·
[9] https://www.mobileread.com/forums/showthread.php?p=4609748 ·
[10] https://help.kobo.com/hc/en-us/articles/360033830114 ·
[11] https://help.kobo.com/hc/en-us/articles/15335985512983 ·
[12] https://github.com/kobolabs/epub-spec ·
[13] https://github.com/w3c/epub-tests/tree/main/epub33/reports ·
[15] https://blog.the-ebook-reader.com/2022/11/17/new-send-to-kindle-webpage-can-send-epubs-and-documents-to-kindles/ ·
[16] https://i.blackhat.com/EU-21/Wednesday/EU-21-Franken-How-Your-Ebook.pdf ·
[18] https://www.mobileread.com/forums/showthread.php?p=4585629 ·
[19] https://codeberg.org/codedge/pb-opds-connector ·
[20] https://www.mobileread.com/forums/showthread.php?p=3743595 ·
[21] https://help.boox.com/hc/en-us/articles/10992026883732-PushRead ·
[22] https://shop.boox.com/blogs/news/new-feature-integrated-third-party-cloud-storage ·
[23] https://help.apple.com/itc/booksassetguide/en.lproj/itc1415cffcd.html ·
[24] https://support.google.com/books/partner/answer/3316879 ·
[26] https://github.com/edrlab/thorium-reader/blob/develop/src/main/services/opds.ts ·
[27] https://github.com/edrlab/thorium-reader/blob/develop/src/main/streamer/streamerNoHttp.ts ·
[30] https://moondownload.com/faq.html ·
[31] https://github.com/foobnix/LibreraReader ·
[38] https://www.w3.org/TR/epub-rs-33/ ·
[39] https://calibre-ebook.com/whats-new ·
[40] https://specs.opds.io/opds-2.0 ·
[41] https://pypi.org/project/later-ink/ ·
[43] https://codeberg.org/readeck/readeck
