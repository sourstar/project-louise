# wiki-gutenberg — memoir candidates by title: English, French, German, Spanish (+ Italian addendum)

*2026-11-18. `tools/measure/kg_memoir_title_candidates.py` over
`pg_catalog.csv` (aleph.pglaf.org, 2026-11-15). One CSV per language:
`memoir-candidates-<lang>.csv`. The rule is the one the operator decided
on the Italian list (wiki-gutenberg-claude-plan.md §10): memoirs,
biographies, first-person chronicles and eyewitness accounts, travel
included, YES; fiction and history or criticism, NO.*

A candidate is a text whose TITLE holds one of its language's memoir
words, and which the subject rule does not already put in the memoir
group.

| language | words searched |
|---|---|
| en | memoir(s), recollection(s), reminiscence(s), diary, journal, letters, correspondence, autobiography, life of, my life, confession(s), chronicle(s), narrative, travel(s), voyage(s), journey, tour, personal experience(s), eyewitness |
| fr | mémoires, souvenir(s), journal, lettres, correspondance, autobiographie, vie de, ma vie, confession(s), chronique(s), récit(s), voyage(s), relation |
| de | Erinnerungen, Lebenserinnerungen, Denkwürdigkeiten, Memoiren, Tagebuch, Tagebücher, Briefe, Briefwechsel, Autobiographie, Selbstbiographie, Leben, Bekenntnisse, Chronik, Reise(n), Reisebriefe |
| es | memorias, recuerdo(s), diario, cartas, correspondencia, autobiografía, vida de, mi vida, confesiones, crónica(s), relación, viaje(s) |
| it, addendum | viaggio, viaggi, impressioni: the travel words the first Italian pass did not search. The 54 decided books are not proposed again. |

**Each row's category** comes from its record alone (subjects, LoCC,
title), by the first rule that matches. Nothing is read from the books.

| category | the rule | suggestion |
|---|---|---|
| fiction, poetry or drama | the scope rule's OUT (§6) | no |
| periodical | the subject "Periodicals", or an issue number, magazine, weekly, monthly, quarterly, proceedings, bulletin in the title. A volume number alone does not count | no |
| history or criticism | a subject ending "History and criticism", "Historiography", "Study and teaching", "Criticism and interpretation" | no |
| travel account | a subject with "Description and travel", "Voyages and travels", "Discovery and exploration", or LoCC G | yes |
| eyewitness or source | a subject with "Personal narratives", "Sources", "Captivities", "Prisoners", "Pioneers", "Frontier and pioneer life" | yes |
| about a person | a personal-name subject ("Surname, Forename, 1807-1882") | yes |
| review | none of the above | ? |

**Counts:**

| category | suggestion | en | fr | de | es | it |
|---|---|---|---|---|---|---|
| about a person | yes | 374 | 53 | 9 | 5 | 1 |
| eyewitness or source | yes | 58 | 18 | 1 | 0 | 0 |
| travel account | yes | 714 | 74 | 42 | 33 | 14 |
| review | ? | 844 | 149 | 29 | 10 | 1 |
| fiction, poetry or drama | no | 543 | 80 | 38 | 20 | 3 |
| periodical | no | 442 | 40 | 0 | 0 | 0 |
| history or criticism | no | 24 | 4 | 1 | 2 | 0 |
| **total** | | **2999** | **418** | **120** | **70** | **19** |

How to mark:
- Decide a CATEGORY at once, with exceptions by ebook number. Example:
  "en: travel yes, except 1234". Row by row is only needed for
  **review** (en 844, fr 149, de 29, es 10, it 1).
- Or fill the `decision` column of the CSV in a spreadsheet.
- The answers become `wiki-gutenberg-memoir-titles-<lang>.toml`, like
  the Italian one. A "no" is kept, so the book is not proposed again.

What a spot check of 12 English rows per category showed:
- **review** holds real memoirs whose subjects name only a place:
  *John Long's journal, 1768-1782*, *Personal Recollections of
  Birmingham*, *Sixty Years in Southern California … Reminiscences*.
  It also holds what is not a memoir: *The Physical Life of Woman*,
  *Philosophical Letters of Schiller*, legends (*Life of St. Declan*).
- **about a person** includes biographies for children (*The boys' life
  of Edison*). By the rule ("Biography" is IN) they are yes. Say if
  juvenile biographies should be no.
- **travel account** includes scientific expeditions (*Journal of
  Researches … Beagle*). These are eyewitness accounts, so yes.
