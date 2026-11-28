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
| 4 | A sister's later updates on the same node: her sister acquires their one corpus (the loader's rule, `collections._one_acquirer`: a sister whose file states no `acquire_enabled` does not acquire while the collection she derives from is held and they read one physical origin folder, since two fetches there under two manifests' claims would race; a stated value wins, and with her sister gone she acquires, with no edit), and her next snapshot from her sister's new parts (`hold snapshot create --shards-from`, 02 §2.7) is not run by the worker | 2026-11-27, gutenberg-nemo (independent read of b28b0feb) | not built: the worker would take her sister's new parts after each of her sister's snapshots. Growing a sister (the Library's grow, `hold records fetch`) is refused in words (`collections.sister_refusal`), so no edit turns her fetch back on | OPEN |
| 5 | After `node pause <collection>`, a wait of unknown length by hand before the next command: the pause returned 0 at once, the worker let go of the collection's write lock only at the end of its current unit (80 s after the pause; 20-90 s earlier that night), and the jobs POSTed 1-5 s after it were refused at the carry, exit 3, "held by the node (worker) … retry when it is done" | 2026-11-28 03:3xZ, München, creating gutenberg-nemo by the API | branch `pause-waits`: `node pause <collection>` waits, bounded (`--wait`, 120 s: the worker's 60 s retirement and a round to reach it), until the worker has let go (exit 0) or says the pause is still being applied (exit 3, the route's 503); a command that needs the write lock of a paused collection the worker still holds (`_take_write_lock`, the carries and swaps of `hold collection new`) waits for it within the same bound | fixed by `pause-waits` |

A new entry gives what was done or needed by hand, where it was seen, and
what would do it instead.
