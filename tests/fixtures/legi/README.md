# LEGI fixtures — where they come from

**Real files (byte for byte, never edited):** DILA's LEGI XML as mirrored by
Legilibre, `github.com/Legilibre/legi-php` at commit
`a028bac0c736910c6c7632049549702694aaee15` (2017-11-06), directory
`files_exemples/`. Licence Ouverte 2.0 (the data's, DILA). The format is the
one of 2017; the files DILA serves now may differ (owed: a member of the
current baseline read on the node).

| file here | file there | what it is | sha256 |
|---|---|---|---|
| `LEGIARTI000006398122.xml` | `files_exemples/LEGIARTI000006398122.xml` | an article version, ETAT `ABROGE`, 1952-06-29 → 2001-01-01, Code des instruments monétaires et des médailles art. 1 | `e54159c0…8ac6` |
| `LEGIARTI000019325262.xml` | `files_exemples/cas extremes/LEGIARTI000019325262.xml` | an article version, ETAT `MODIFIE`, 2004-10-26 → 2007-11-09, CASF annexe 2-4 (149 KB) | `36dbca92…94a6` |
| `LEGISCTA000006135575.xml` | `files_exemples/LEGISCTA000006135575.xml` | a SECTION_TA | `19066dc5…b93d` |
| `struct-LEGITEXT000006070666.xml` | `files_exemples/struct/LEGITEXT000006070666.xml` | a TEXTELR (the text's structure) | `5d367815…1cfe` |
| `version-LEGITEXT000006070666.xml` | `files_exemples/version/LEGITEXT000006070666.xml` | a TEXTE_VERSION | `3625ddef…3706` |

`dtd/` — DILA's DTDs as mirrored by `github.com/Legilibre/dtd-legi` at
commit `2c64cbf51a1bf640ad6d5d2889e4414a87b6c667` (2018-03-09), every
`.dtd` of the repository, unedited.

**HAND-MADE variants** — not in this directory: built in
`tests/legi_fixtures.py` from the real files above, each one a real file
with named fields replaced (its every other byte the origin's form):

- `ART_MORT_NE` — `LEGIARTI000019325262.xml` with its ETAT (and its
  VERSION's) set to `MODIFIE_MORT_NE`;
- `ART_PLACEHOLDER` — `LEGIARTI000006398122.xml` with its DATE_DEBUT set
  to `2222-02-22`;
- `article(...)` — `LEGIARTI000006398122.xml` with its id, number, état,
  dates, VERSIONS, text, text id/title and LIENS replaced (the version
  chains, code articles and repeal links the tests need);
- `texte_version(...)` — `version-LEGITEXT000006070666.xml` with its ids,
  title, nature, number, NOR, dates, état and visas replaced (the codes and
  the loi n° 78-17 the citation tests resolve against).

The tarballs the tests read (`Freemium_legi_global_*.tar.gz`,
`LEGI_*.tar.gz`, the deletion lists) are built in the tests from these, at
the member paths DILA's tarballs use (`tests/legi_fixtures.py member`).
