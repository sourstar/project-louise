# Locking a held collection

*Design note, 2026-11-28. Built the same day (app/collection_lock.py).
07 §7.8 gains the file key `locked`, 07 §7.3 `hold collection lock|unlock`.*

The operator, 2026-11-27, verbatim:

> When it fits, add a "lock" checkbox per collection, with it the collection
> is not searchable or shareable, no processing or fetching. It simply stays
> frozen as it is. Its embedders/reranker are not loaded (unless there are
> other collections using them). It is to keep an unused collection for
> whatever reason. It can only be renamed, exported or removed.

## 1. The one rule

`locked = true` in `collections/<name>.toml` is a **policy refusal** of
`collections.load_all()`, as a file naming no encoder is. Every reader of
that listing leaves the collection out by that one line:

| the operator's words | where it follows from the refusal |
|---|---|
| not searchable | a search ranks `load_all()` (`query_service`, `retrieval`, the Ask page's list) |
| not shareable | the seeder and the transport serve and take in `load_all()`'s collections |
| no processing or fetching | the worker's round is `load_all(skip_unreadable=True)`: a collection gone from it is retired, its current unit committed first (invariant 2) |
| its models not loaded, unless another uses them | the keeper (`resident`, `model_pins`, `warmup`) wants the models of `load_all()`'s collections, so a model only a locked collection names is no longer wanted |
| only renamed, exported or removed | §3 |

A reader added tomorrow respects the lock without knowing of it. The
alternative — a check at each site, as the freeze has (`worker._is_frozen`
at five places, the seeder, the transport) — is a list every new path must
remember; for a lock that stops *everything* it would drift first.

`load_annotations()` refuses a locked annotations collection the same way
(not read at a question, not shared, no method re-run).

What must still SEE a locked collection reads `collections.load_locked()`
or `locked_by_name()`:

- the Library's Locked card (`web.library_page`) and its name
  (`display_names`: locking one renames no other through the postfix rule);
- "does this node hold it?" — `share.offer.held_ids`, so a locked
  collection is never offered back as one this node lacks (the take route
  checks nothing, and `share fetch` would write into it);
- the certifier's anchor and pointer (`api.certifier_anchor`): a peer whose
  anchor stops listing a collection judges it MISMATCH and opens it
  (app/hold/anchor.py) — a lock withdraws no certification;
- the one-acquirer rule (`collections._one_acquirer`, `sister_refusal`): a
  locked ancestor still holds her origin folder, so a sister reading it does
  not take over its fetch, and a grow of her is refused as before;
- a deposit collection's unfinished erase (`deposit.finish_pending`): a
  privacy duty (02 §2.9), and a removal, which a lock allows;
- a new wikidata collection's dump (`plugins/wikidata/source._corpus_for`):
  read where a locked one holds it, not downloaded again;
- an annotations method's loop check (`cascade`): a loop through a locked
  collection would go live at its unlock — while the cascade keeper itself
  never runs it, nor writes a published marker for a locked source
  (`cascade_keeper._one`, `_publish_newest`: they list what is on disk, not
  `load_all`); and the source's old-run gate does not wait for a locked
  annotator (`cascade.old_run_gate` says it is locked): waiting would stop
  every later update of the source. Its claims derive from the source's
  published snapshots, which stay, and the next one wakes it once unlocked;
- `node show`, which lists it as `locked` with its reason;
- the three acts and the lock box (§3).

## 2. Where the lock lives: the collection's file

| choice | pros | cons |
|---|---|---|
| **the file's `locked` key (taken)** | desired state in the file, as invariant 10 says; survives a restart without a new exception to it; read by the loader that already reads the file each round — no new store read on `load_all`'s 78 call sites; carried by a rename with the file; the lock box is the share box's shape (`collection_file.set_values`) | a new key of 07 §7.8 (amended); every rewrite must carry it (tests/test_toml_roundtrip.py's guards cover it) |
| the state database, as pause and freeze | the same place as its two cousins | a third exception to invariant 10 ("never write desired state into the DB") the operator has not granted; `load_all` would read `state/node.sqlite` on every call |

## 3. What a locked collection allows

| act | how |
|---|---|
| rename | the Library's rename… (the file's `title`, the one other value a locked file takes: `collection_file.LOCKED_SETTABLE`); `hold collection rename` (its local name and folders) |
| export | the Library's export bundle (`_collection_or_404(…, locked_ok=True)`, `jobs._export`); `hold vectors export` |
| remove | the Library's remove… (retire or forget); `hold collection forget`; a deposit's `hold deposit erase` (a privacy duty, 02 §2.9) |
| unlock | its lock box; `hold collection unlock`; `hold config apply --set locked=false` |
| export | also `hold annotations claims` (its claims to a file) |
| read | `hold config show`, `vectors show`, `records show`, `snapshot list|delta`, `index show`, `deposit list|show`, … (`louise._LOCK_ALLOWS`) |

Everything else is refused before it acts, saying it is locked and how to
unlock it: any other command naming it (exit 2; `louise._locked_refusal`, a
WHITELIST, so a command added later is refused until listed; it reads every
argument that names a collection — `collection`, and `--name`, `--sister-of`,
`dev carry`'s two — `louise._LOCK_ARGS`), any other
value of its file (`collection_file.set_values`), any other Library route
(409, `web._not_served`).

## 4. The refusals of the lock itself

THE LOCK CHANGES NOTHING FOR A COLLECTION STILL SERVED. Its refusals are
asked by the file's one writer (`collection_file.set_values`), so the lock
box, `hold collection lock|unlock` and `--set locked=…` ask the same ones.

**A writer whose text index a sister reads** (docs/notes/sister-shared-index.md)
is not locked: locked, it stops indexing for both, and her text leg misses
every document she takes in afterwards — silently, which is the one thing a
lock must not do to another collection. She is unshared first (`hold
collection unshare <sister>`, an index of her own built again by the
worker), or locked as well. Locking the reader is always allowed.

**A collection that fetches an origin folder a served collection reads and
does not fetch** is not locked either — whichever of them fetches, by the
one-acquirer rule or by its own file's `acquire_enabled` (EUR-Lex5.next's
`true` beside EUR-Lex5's `false`): locked, it keeps the folder as it is, so
nothing would fetch for the other, and nothing would say why. The other is
locked as well. Likewise **a collection is not unlocked** while the one that
fetches its folder is locked.

**A reader is not unlocked before the writer of her index**: served, she
would search an index nobody writes, the worker's settle would withhold her
text leg (`text_index.settle`), and the reason would stay in her placement
until the next boot. The writer is unlocked first.

**Nothing served and a collection locked** — every collection locked, or the
last served one locked beside files refused for other reasons — is a node kept
as it is, not a misconfigured one (every file refused, none for a lock, still
raises in `default_collection`): its control rows go where a node holding none
writes them (`_control_fallback`), never into a locked collection, which the
routes scoped to the default collection (the books listing, a discover, a
reset) would then act on; and the Library, with the lock box that undoes it,
stays up.

## 5. Lock and unlock, in the worker

**Lock.** At its next round the worker finds the collection gone from
`load_all` and leaves it as it leaves a removed file (`worker._leave`,
`retire_collection`): its current unit committed, its threads stopped and
joined (60 s bound), its look at the gate retired, its text-index and catalog
writers released, its connections closed, encoders only it named unloaded,
its torrents dropped at once (`seeding.retain` — not the graceful drain of
sharing off), its write lock released. Nothing is deleted on disk; the one
write is the line "collection X removed — ingestion stopped, data kept" in
its own event log. A `hold` command that writes (rename, forget) and finds
the worker still holding it waits for that release as for a pause
(`louise._take_lock`, PAUSE_WAIT_S).

**Unlock.** The line is removed from the file. The worker adopts the
collection as a new one: boot validation again (`spaces.validate_boot`), the
file's embed mode applied (a Library runtime override is not kept — invariant
10), a full discover — a fetch where it acquires — so what changed upstream
while it was locked arrives as after any stop. Its statements are kept on disk
and the gate's recheck is keyed on the space and run, not the name: a run
already gated is not gated again.

**`hold vectors export`** embeds the bundle's sixteen challenge rows (09
§9.7), so it loads the collection's encoder for that act; the Library's
export bundle reads the stored vectors and loads nothing.

## 6. Not done

- No "who locked it, when" — the file holds no history. If it is wanted,
  the freeze's history pattern (`node_state.FROZEN_HISTORY_KEY`) is the
  standard here.
- A federated search over every collection leaves a locked one out without
  naming it; named, it answers 409 "locked" (`query_service.resolve`).
- Its event log is not on the System page while it is locked.
- A locked deposit collection answers LocalAGI's routes 409 "locked", but for
  the depositor's own erase (`DELETE …/entry/delete`), which is a removal and
  a privacy duty (02 §2.9). LocalAGI's reset is refused like any other write.
- A locked file that the loader refuses for another reason (an unmounted
  build area, wrong model keys) is shown under "Held, not served" with that
  reason, and is reached by the command line's rename and forget only.
