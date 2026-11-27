# Hand steps: what the API does not cover yet

**The rule** (operator, 2026-11-27, relayed verbatim by München): "Anything
requiring a manual edit because not covered by the API goes back to
architecture for implementation or fix."

- **Counts as a hand step:**
  - a hand-edited collection file, slice list or config value;
  - commands chained by hand where one workflow should run them;
  - any step done outside a command or its route.
- **What happens to one:** it is not done first and reported after. It
  comes here, goes to the architecture session, and waits for the command
  or route that does it.
- **Not a hand step:** a read-only measurement (a count, a timing on a
  copy).

## The inventory

| # | Hand step | Seen | Fixed by | State |
|---|---|---|---|---|
| 1 | A re-created collection's file kept what `create` writes (`slices = []`, `embed = "off"`) where its predecessor held "all"/"all"; both were set by hand | gutenberg.next, 2026-11-19 | `hold/carry.py carry_file`: a re-creation carries its predecessor's file, `embed` off until the operator turns it on | fixed |
| 2 | A case-J sister's file was written with `slices = []`, so she held nothing until her slices were set by hand. For a same-layers sister it also gave another text-index identity, so she never joined her sister's group | found 2026-11-27, gutenberg-nemo's case | #957: every case-J sister's file takes her sister's slices | fixed in #957 |
| 3 | A sister in a new space with her sister's layers (only her encoder side changed) had no case in the workflow, so it would have been made by hand-chained commands | 2026-11-27, gutenberg-nemo | #957: `hold collection new --sister-of S --encoder-from C`, also `POST /api/hold/collection/new` | fixed in #957 |

A new entry gives what was done or needed by hand, where it was seen, and
what would do it instead.
