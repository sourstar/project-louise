# A compliant collection: the check its author runs

*Design and state, 2026-11-25. The operator, to München: "a consistency
check shall be available also for whom is willing to create a compliant
collection"; and the same day: "Every problem encountered with a collection
must become a lesson learned reflected in RAGE". The lessons are the Legal
session's [`eurlex5-lessons-2026-11-25.md`](eurlex5-lessons-2026-11-25.md).*

## 1. What it is

One command that tells the author of a plugin whether the collection they
built is compliant, in plain words, with what is missing and what would
supply it. "Compliant" must be something a stranger can TEST: a lesson that
stays a note protects nobody who has not read the note.

## 2. Two families of checks

| | the PLAN's checks | the BUILD's checks |
|---|---|---|
| about | a COPY: its records against its roots, its origin, its space | how the collection was MADE on this node: its manifest, as the plugin's scan and parse left it |
| named in | the Codex's `validation` (03 §3.2) | nowhere on the wire |
| attested | yes (09 §9.8) | never |
| who runs them | any peer that holds the copy | the builder: they need the manifest |
| command | `louise hold check <collection>` | `louise hold check <collection> --build` |
| code | `app/hold/check.py` | `app/build_check.py` |

They are kept apart on purpose. A build check in the Codex's plan would be a
wire change, and a peer that received a copy has no manifest to run it on.

## 3. The command: the choice put to the operator

| choice | pros | cons |
|---|---|---|
| A. `louise hold check <collection>` covers every check; the build's with `--build` | one verb, the one already known; runs on any held collection, a sample on a laptop included (it needs a root directory, not a node in production) | a check that needs a model server (the gate) or the origin (records against origin) cannot run offline: it says so and what would supply it |
| B. also `louise plugin check <plugin> --sample DIR`: a throwaway collection from a sample of the corpus, scanned twice, then A | the author needs no collection yet; it tests the PLUGIN before anything is built | more to build; a second command |
| C. as before | nothing to build | the lessons stay notes |

Recommended, and what is being built unless he says otherwise: A now, B
after it as a thin wrapper around A.

## 4. The build's checks

| check | the lesson (row of the lessons note) | what it reads | state |
|---|---|---|---|
| `units` | B1–B3: one unit, one live row. EUR-Lex5 held 37,695 and then 405 units twice | the manifest; the plugin's `ref_of_unit` (a container of many documents is not judged) | BUILT |
| `chunkless` | E1: a document indexed with no chunk states why, as an issue. 57 were told apart by hand | the manifest and its issues | BUILT |
| `rescan` | B1, C1, C4, C5: a second scan of an unchanged corpus inserts, changes and retires nothing, and costs listing and stat | the corpus and a COPY of the manifest (a scan writes) | to build |
| `parse-bound` | A1, A2: every document's parse is bounded in time and a document that hits the bound is isolated | the worker's journal and the rows that did not come back; and, for the platform itself, a test plugin with a deliberately catastrophic pattern | to build |
| `origin-hash` | invariant 11: every record carries the hash of its origin unit | the records | refused at build today; the check reports it |
| `licence`, `space-pin` | invariants 9 and 1 | the same resolutions the node uses | exist in the plan and at start; the check reports them together |
| `words` | E2: every word of the origin's files is in a section, a note or a kept tree | the plugin's real fixtures | built for eurlex as a test; the generic harness is to build |
| `bulk-ledger` | F1: a bulk-fed plugin records what the bulk gave, what it cannot give and why | the plugin's ledger | built for eurlex; the generic check is to build |

Every check reads only, costs one pass over the manifest at most, and has
three outcomes: pass, fail, inconclusive. An inconclusive check says why it
could not judge and is neither a pass nor a failure.

## 5. What a failure looks like

```
{"check": "chunkless", "result": "fail",
 "says": "2 of 57 document(s) indexed with no chunk state no reason",
 "supplies": "the plugin's parse reports an issue for a document that legitimately has no text (…); a document that HOLDS text and gave no chunk is a parse to fix",
 "detail": {"by_reason": {"no-text-layer": 16, "title-only": 39}, "with_no_reason": 2, "first": ["…", "…"]}}
```

## 6. Not measured

Neither check has run on the node's collections. The first run there is
the reading: how many documents each collection holds with no chunk and no
stated reason (Wikipedia's redirects, for one, are indexed with no chunk
today), and what a pass over a manifest of 76 M rows costs.
