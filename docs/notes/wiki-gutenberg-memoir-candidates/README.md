# wiki-gutenberg — memoir candidates by title: English, French, German, Spanish (+ Italian addendum)

*2026-11-18. `tools/measure/kg_memoir_title_candidates.py` over
`pg_catalog.csv` (aleph.pglaf.org, 2026-11-15). One CSV per language:
`memoir-candidates-<lang>.csv`. The rule is the one the operator decided
on the Italian list (wiki-gutenberg-claude-plan.md §10): memoirs,
biographies, first-person chronicles and eyewitness accounts, travel
included, YES; fiction and history or criticism, NO. Amended the same
day: **no books for children**, and **anything not shown to be a memoir is
out** ("it is a waste of GPU time and even worse it's tainting the
scope").*

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
title, bookshelves), by the first rule that matches. Nothing is read from
the books.

| category | the rule | suggestion |
|---|---|---|
| fiction, poetry or drama | the scope rule's OUT (§6) | no |
| for children | a "Juvenile …" subject or a children's bookshelf. It also takes such books out of the memoir group itself (`kg_scope_count.is_juvenile`) | no |
| guidebook | a "Guidebooks" subject | no |
| not a memoir by its subjects | Quotations, Essays, Sermons, Speeches, Addresses, Handbooks/manuals, Textbooks, Cookery, Health and hygiene, Readers, Dictionaries, Encyclopedias, Catalogs, Hymns, Prayers, Legends | no |
| periodical | the subject "Periodicals", or an issue number, magazine, weekly, monthly, quarterly, proceedings or bulletin in the title. A volume number alone does not count | no |
| history or criticism | a subject ending "History and criticism", "Historiography", "Study and teaching" or "Criticism and interpretation" | no |
| travel account | a subject with "Description and travel", "Voyages and travels" or "Discovery and exploration", or LoCC G | yes |
| eyewitness or source | a subject with "Personal narratives", "Sources", "Captivities", "Prisoners", "Pioneers" or "Frontier and pioneer life" | yes |
| about a person | a personal-name subject ("Surname, Forename, 1807-1882") AND a life word or an own-account word in the title (life of, story of, biography, memoir, recollections, diary …) | yes |
| review: own account in the title | none of the above, but the title says it is the author's own account (memoirs, recollections, reminiscences, diary, autobiography, personal, confessions, journal of a …) | ? |
| not a memoir by its record | none of the above. Out by default: nothing in the record shows a memoir | no |

**Counts** (after the amendment):

| category | suggestion | en | fr | de | es | it |
|---|---|---|---|---|---|---|
| travel account | yes | 689 | 73 | 42 | 33 | 14 |
| eyewitness or source | yes | 56 | 18 | 1 | 0 | 0 |
| about a person | yes | 286 | 44 | 7 | 5 | 0 |
| review: own account in the title | ? | 305 | 74 | 8 | 2 | 0 |
| fiction, poetry or drama | no | 546 | 80 | 38 | 20 | 3 |
| for children | no | 100 | 1 | 2 | 0 | 0 |
| guidebook | no | 2 | 0 | 0 | 0 | 0 |
| not a memoir by its subjects | no | 47 | 2 | 0 | 0 | 0 |
| periodical | no | 431 | 40 | 0 | 0 | 0 |
| history or criticism | no | 22 | 3 | 1 | 2 | 0 |
| not a memoir by its record | no | 541 | 83 | 21 | 8 | 2 |
| **total yes** | | 1031 | 135 | 50 | 38 | 14 |
| **total ?** | | 305 | 74 | 8 | 2 | 0 |
| **total no** | | 1689 | 209 | 62 | 30 | 5 |

How to mark:
- Decide a CATEGORY at once, with exceptions by ebook number. Example:
  "en: travel yes, except 1234". Only **review: own account in the
  title** needs reading row by row: en 305, fr 74, de 8, es 2.
- Or fill the `decision` column of the CSV in a spreadsheet.
- The answers become `wiki-gutenberg-memoir-titles-<lang>.toml`, like
  the Italian one. A "no" is kept, so the book is not proposed again.

Still in the "?" rows, from a spot check of 15 English rows:
- **Real memoirs:** *The Greville Memoirs*, *Personal Narrative of Events,
  From 1799 to 1815*, *Reminiscences of the Thirty-Fourth Regiment, Mass.*,
  *Recollections of Manilla*.
- **Not memoirs:**
  - *Reminiscences of a Stock Operator* is a novel in memoir form.
  - *Confessions and Criticisms* is essays.
  - *Memoir of the Services of the Bengal Artillery* is a unit history.
