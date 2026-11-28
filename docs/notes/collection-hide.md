# Hiding a collection from readers who are not admins

*Design note, 2026-11-28. Built the same day (07 §7.8 `hidden`,
`collections.hidden_from`, app/viewing.py).*

The operator, 2026-11-28:

> Implement a "hide" check per collection that hides the collection to the
> non admin users: collections whose embedding is in progress and still too
> low to produce results.
> Same for annotations, I may want to keep some annotations for admin purpose
> only

## 1. The rule

`hidden = true` in `collections/<name>.toml`:

- **To the node and to an admin, nothing changes.** It is fetched, embedded,
  indexed and shared as its `share` says. The admin Library shows it with a
  `hidden` badge and its hide box, and the admin's searches, reader and API
  find it as before.
- **To every other viewer it does not exist.** That means a user, the
  depositor, a guest, and nobody (07 §7.4's viewers; `auth.admin` is the
  test). It is not listed, not searched, not read, and not counted. Named,
  it is the name nothing holds: the same 400 or 404 as an unknown name, so a
  route never says it exists.
- **A hidden ANNOTATIONS collection** is never consulted for that viewer. Its
  claims are not attached to a hit, its works do not group editions, its
  edges are not walked, and its chip is not on the Ask page.

It is desired state in the collection's file (invariant 10), like `share`
and `locked`. It is written by the hide box (`POST
/admin/library/{name}/hidden`) or by `hold config apply --set hidden=…`,
through the file's one writer. A locked file does not take it
(`LOCKED_SETTABLE`).

## 2. Who is looking: one ContextVar

The filter needs the viewer at about thirty call sites: the search's
resolvers, the annotations loader, the works grouping, the graph walk,
facets, figures, the PDF's names, the API's default collection, the kept
search, the vector stores and LocalAGI's listing. Most of them do not have
the request object.

| choice | pros | cons |
|---|---|---|
| **A ContextVar set by the gate for every request (taken: app/viewing.py)** | One line in `auth.ViewerGate`. Sync routes, streamed answers and `run_in_threadpool` inherit it (Starlette copies the context). A reader site added tomorrow is filtered without plumbing. | A plain thread pool does not inherit it, so the search's pools carry it with the cancel token (`cancel.carry`). A pool that does not carry it is "nobody" in the web process (below). |
| A `viewer` parameter threaded through each function | Explicit at every call. | About thirty signatures. A site that forgets the parameter shows everything. |

**Fail-closed.** The web's container process (`app/web_main.py`) calls
`viewing.serve_readers()`. Outside a request — a thread that lost the
viewer — that process is looked at by nobody, and nobody sees a hidden
collection, so a missed pool shows less, never more. Every other process
(the worker, the command line, the tools, a test process outside its
requests) is the OPERATOR and sees everything, as before.

## 3. Where it is applied

| what | where |
|---|---|
| lists and federated search | `collections.load_public` (the Ask page, the users' Library, the reader), `query_service.resolve_collections` / `withheld_from` (`/api/search`, `/ask/query`, `/ask/relay`, `/v1/chat/completions`, `/v1/models`, `/connect`, MCP, `/api/verify`) |
| annotations | `query_service.resolve_annotations` (named: unknown), `ask.annotations._held` (attach, works), the Ask page's chips (`web._ask_annotations`, their "applies to" names from `load_public`) |
| collections read on disk | `collections.resolve_seen` / `listed_seen`: the graph walk (`ask/traverse`), `/api/ask/facets`, `/api/ask/figure` and its preview |
| the API's default collection | `api._collection`: to a reader, the first collection they may see (`/api/status`, `/api/stats`, `/api/events`, `/api/books`); the query log stays in the default's manifest (`_log_con`). `/api/events` drops a line naming a hidden collection. |
| kept results | `web.keep_search` records whether an admin's search made it; `kept_search` never gives one to another reader |
| hit naming | `_ask_result`'s plugin → collection map from `load_public` |
| machine interfaces | `/v1/vector_stores` (listing and `_store`), LocalAGI's listing and search |

**A deposit collection on its depositor's own routes stays the
depositor's.** That covers LocalAGI's write, entries and erase routes, its
search, and the vector stores for the deposit token. Hiding is about
readers, and a deposit collection is never on an open route anyway (07
§7.4).

**Two old leaks closed on the way.** The routes that read the collections on
disk (the graph walk, facets, figures) and the Ask page's annotation chips
did not apply 07 §7.4's deposit rule. They do now: a deposit collection is
shut there to anyone but the operator (`closed_names_here`).

## 4. Not done

- **Display names.** A hidden collection still takes part in the postfix
  rule (`display_names`), so a reader's label for "Project Gutenberg ·
  Qwen3" implies that another Project Gutenberg exists. The label names no
  content, and it keeps every viewer's names the same.
- **Creating under a hidden name.** LocalAGI's create of a name a hidden
  collection holds answers 409 "held already", because two collections
  cannot share a name.
- **Peers.** The peer routes (`/api/share/*`, the certifier's anchor and
  pointer) are not reader routes. A hidden collection is shared as its
  `share` says; to keep it from peers, untick share.
- **Hit counts.** A federated search with a hidden collection left out says
  nothing about it to the reader, by design: for them it does not exist.
