# Annotation methods: the node updates its own derived annotations

*2026-11-12. Operator, through München: "Can the annotations be updated without manually uploading something???"
(RULE 2). Code: `app/hold/annotation_methods.py`; the worker's claim index keeper; `hold annotations derive`.*

## What changed

An annotations collection whose claims are computed only from collections the node holds now names that
computation, its **method**, in its collection file:

```toml
method = "wikipedia-versions"
```

When the collection it annotates holds a **newer snapshot** than the one the claims are pinned to, the worker
re-runs the method and then runs `update` with the method's whole output. The result is the next snapshot of the
same collection, signed with the node key. Nobody uploads anything.

| Step | What happens |
|---|---|
| Due check, each keeper pass (≤ 300 s) | The target's newest snapshot (a directory listing) against the claims' pin: `state/method.json`, or else the first claim's `target_snapshot`. One update a period (09 §9.4): if this period's snapshot is already written, it waits for the next and says so once. |
| Method, in a child process | Writes the whole claim set into a workspace the worker holds (`.annotations-method.<name>`). |
| Update, in a second child | `unheld="drop"`: a claim whose end the target's snapshot does not hold yet is set aside and named, not refused. The method reads the live manifest, which runs ahead of the snapshot. The claims file is hard-linked into place, not copied (RULE 1; wikipedia's is 4.2 GB). |
| Marker | `state/method.json`: method, snapshot, `against`, counts. |

Only **`wikipedia-versions`** is a method today. It reads the wikipedia collection's manifest and page_props, which
makes it a pure function of what the node holds. gutenberg's works, titles, matches and merge files depend on API or
language-model intermediates. They are not methods, so they keep `hold annotations update --claims`.

## Where the method is recorded, and why

The method goes in the collection file, not the Codex. The file is node-local desired state (invariant 10), so only
the node that publishes the collection re-derives it. A peer that adopted the collection takes the publisher's
snapshots as it takes any. Putting the method in the Codex would be a wire-format change. It would also move
`collection_id`, which would make wikipedia-articles-cache a new collection. If the method should travel (a peer
reproducing the claims to check them), the place is a Codex field at the next annotations version. That is the
operator's call when such a peer exists.

## Failure, stop, memory

- **Memory.** Each step runs in a spawned child. Measured on the node on 2026-11-11: the method peaks at 6.81 GiB
  and the update at 2.7 GiB. Both peaks end with their child, beside the worker's 1.6–3.8 GB under its 24 GiB cap.
  The node key is read only in the update's child.
- **Stop.** A stop terminates the child at once (exit 130). The only exception is the update's last writes, the
  snapshot and its signature, which a SIGTERM handler lets finish. The handler is per process because a per-thread
  mask does not reach numpy's BLAS threads (the first test of this died mid-block). Everything before those writes
  is resumable, as `update` already was. The snapshot is now written to a temporary file and renamed: a write cut
  short used to leave a file that every later update refused as "different bytes".
- **Failures** are logged once per (collection, method, target snapshot). A child killed by a signal (the OOM killer)
  is retried at the next pass, three times. `hold annotations derive <c> [--dry-run]` runs the same thing by hand,
  or says whether it is due and why.

## Speed

`wiki_versions.claim_lines` renders the claims from one template whose constant parts come from `json.dumps`. It is
byte for byte the old `json.dumps(claim, sort_keys=True)` per line (test), and ~8× faster: 200 k claims in 0.23 s
against 1.87 s. That is about 15 s instead of ~115 s of one core for wikipedia's 12.3 M. The tool
`tools/annotations_from_wikipedia.py` uses it too.

## For München (on the operator's word, after the deploy)

1. `louise hold config apply wikipedia-articles-cache --set method=wikipedia-versions`
2. `louise hold annotations derive wikipedia-articles-cache --dry-run`. Expected: not due, "pinned to wikipedia
   <its snapshot>".
3. Nothing else. The worker derives once wikipedia's next snapshot is written. Its log line names the counts. Time
   the first one (method and update wall times, peak RSS of each child).

## Standardised alternative, not taken

A workflow engine's derived datasets (Airflow datasets, Dagster assets) re-run a step when an input's version moves.
Adopting one would add a service beside the worker for a single trigger, the target's snapshot id, which the worker
already reads. It would pay once several derivations chain.
