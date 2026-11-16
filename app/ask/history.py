"""HISTORY ON REQUEST (operator, 2026-11-16, verbatim: "No history but
implement fetch upon request with a limit on the number of fetches per query
and a timeout per fetch request. Return in the answer if a fetch was not
successful and why, so it never fails silently. In this a user can always
ask for the history of an article, with care"; "The only new part is the
fetch on demand").

WHEN, WITH CARE. Never on an ordinary search. Only when the question names
an EU act's article by key (a citation the eurlex reference resolves to a
CELEX: "Article 6 of Regulation (EU) 2016/679") AND asks for its past: the
request's `history` flag, or the question's own words (`asks`: "history",
"storia", "historique", "Fassungen", … — RULE 2, the Ask page asks no form),
for every version the collection lacks; or an `as_of` day, for the one
version in force that day where it is not held. Which version that is comes
from the listing, never from the held chain: the base holds an act's
original with its end open, its consolidations being the history slice, so
the chain would say the original covers every later day.
Only in an EUR-Lex collection on parser 4 that does not hold that
language's history slice (`history/lang=<l>`, grown from the Library like
any slice): where it does, the versions are already held.

WHAT. The act's consolidated versions in the language, as Cellar lists them
by the act's own link (`cellar.versions_of`: one SPARQL query), less those
held; then each, newest first (the one covering `as_of` alone when a day is
asked), fetched as the per-unit pass fetches it (`cellar.fetch_unit_from_tree`:
the tree, then its items, into `history/lang=<l>/<yyyy>/<CELEX>/`), so it is
the collection's own corpus, fetched later (CLAUDE.md: a collection holds
only the information in its corpus — its origin's, never another's): the
worker discovers, parses and indexes it like any unit, and a search that
returns it queues it for embedding on demand (the existing queue). In THIS
answer the cited article of each fetched version is pinned as a fact,
labelled as fetched now.

BOUNDED. At most `LIMIT` fetches a query (the listing is not one), each
under `FETCH_TIMEOUT_S`: the fetch runs bound to the transfer manager at
INTERACTIVE priority with a stop event a timer sets at its deadline, which
every wait honours — the budget's (invariant 5: every byte drawn from it),
the backoff's between attempts, a SPARQL answer's poll, the check between
requests — and no single read waits longer than `READ_TIMEOUT_S`. NEVER
SILENT: every version
not fetched is in `fetches` with its reason — `limit`, `timeout`,
`unreachable`, `not-found`, `refused`, `error` — and so is the listing that
could not be read. Each fetch is also in the collection's event log.

WHO FETCHES (operator, 2026-11-16: "Option B but fetch triggered by a query
has higher prio over the regular worker's fetching"). The WORKER, the
corpus's one writer — the web has it read-only. The process answering the
question (`ask`) posts the request in the node's request folder (`post`,
on the writable state volume: a file per request, written then renamed) and
waits, bounded (`wait`); a worker thread takes it within `POLL_S` (`take`:
the rename that claims it), `serve`s it — lands each version in the origin,
whole or not at all, registers exactly those units (discovery.register: a
cost per unit, never a scan of the corpus) and builds the facts — and
answers (`answer`). Not taken within `PICKUP_S` (no worker running), or not
answered within `ANSWER_S`: said, with that reason. The pattern is the
bundle inbox's: files in a folder both processes write, claimed by rename.

FIRST IN LINE. Every request to Cellar runs on one of its connections
(specs.EURLEX_SPEC: 2), taken as INTERACTIVE (transfer/pool.py
`acquire_endpoint_slot`): while it waits, the regular per-unit pass leaves
the next free connection to it; its bytes are served first by the budget
(bucket.py, strict priority with the interactive reserve). Politeness is the
same: the connection count, the cool-down. The node's mode is obeyed: no
fetch where the mode runs no FETCH (paused, seed-only), and in acquire-only
the unit lands but is registered by the next scan (PARSE does not run).
Not below the free-space floor (app/disk_guard.py), and not without the
collection's licence acknowledgement (invariant 9): refused, and said.

THE STANDARD, NOT ADOPTED: the question's words are matched by a short list
per language, not a classifier; a reader who wants the past without saying
so in these words sets `history` (API, MCP, `ask search --history`).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

#: fetches a query may make (the operator's "limit on the number of fetches
#: per query"): enough for an article's recent history, few enough that one
#: question costs Cellar at most a handful of units
LIMIT = 3
#: a fetch's whole time (the operator's "timeout per fetch request"): a unit
#: is a tree and one to four items
FETCH_TIMEOUT_S = 20.0
#: the act's versions, listed by one SPARQL query
LIST_TIMEOUT_S = 30.0
#: no one read of a response waits longer (httpx's timeout, below the deadlines)
READ_TIMEOUT_S = 10.0
#: the whole question, every listing and fetch together, in the worker
REQUEST_S = LIST_TIMEOUT_S + LIMIT * FETCH_TIMEOUT_S
#: the web: a request no worker took by then is answered "unreachable" …
PICKUP_S = 10.0
#: … and one taken, answered at most this long after it was posted
ANSWER_S = PICKUP_S + REQUEST_S + 10.0
#: the worker looks for requests this often, serving at most CONCURRENT at once
#: (Cellar's two connections)
POLL_S = 0.2
CONCURRENT = 2
#: answers nobody read and claims a stopped worker left are removed after this
STALE_S = 600.0
FETCHED, LIMITED, TIMEOUT, UNREACHABLE, NOT_FOUND, REFUSED, ERROR = (
    "fetched", "limit", "timeout", "unreachable", "not-found", "refused", "error")

#: a question asking for an act's past, in the languages EUR-Lex4 embeds and
#: Spanish (`asks`): words, never a guess at intent
_ASKS = re.compile(
    r"\b(?:history|historical|(?:previous|past|earlier|former|old|all)\s+versions?|versions|"
    r"amendment\s+history|as\s+(?:it\s+)?(?:stood|was)|"
    r"storia|storico|versioni|versione\s+(?:precedente|previgente|originaria)|previgente|"
    r"historique|versions?\s+(?:antérieures?|précédentes?|successives|consolidées)|ancienne\s+version|"
    r"historie|geschichte|fassungen|(?:frühere|alte|ursprüngliche)\s+fassung|"
    r"historia|versiones|versión\s+anterior)\b", re.IGNORECASE)


def asks(query: str) -> bool:
    """The question asks, in its own words, for an act's past (the module
    docstring)."""
    return bool(_ASKS.search(query or ""))


from contextlib import contextmanager


@contextmanager
def _bounded(seconds: float):
    """A stop event a timer sets at the deadline (the module docstring).
    Yields it: set, the body was stopped by its deadline."""
    import threading
    stop = threading.Event()
    timer = threading.Timer(max(0.0, seconds), stop.set)
    timer.daemon = True
    timer.start()
    try:
        yield stop
    finally:
        timer.cancel()


@contextmanager
def _cellar(stop, bound: float):
    """One of Cellar's connections, taken as INTERACTIVE — ahead of the
    regular per-unit pass (the module docstring) — and the body bound to the
    transfer manager at INTERACTIVE priority with `stop`, which every wait
    honours: the budget's (invariant 5: every byte drawn from it), the
    backoff's, a SPARQL answer's poll, the check between requests. Yields the
    endpoint's base URL."""
    from app.acquire import specs
    from app.acquire.spec import TransportError
    from app.transfer import pool
    from app.transfer.bucket import Priority
    from app.transfer.manager import get_manager
    spec = specs.ACQUIRE_REGISTRY["eurlex"]
    manager = get_manager()
    ranked = manager.rank_endpoints([(e.url, e) for e in spec.endpoints])
    slot = pool.acquire_endpoint_slot(manager, ranked, spec, stop=stop, bound_s=bound,
                                      priority=Priority.INTERACTIVE)
    if slot is None:
        raise TransportError(f"cellar: no connection free within {bound:.0f} s")
    key, endpoint, sem = slot
    try:
        with manager.bind(Priority.INTERACTIVE, stop=stop, endpoint_key=key):
            yield endpoint.url
    finally:
        sem.release()


def _celex_of(citation) -> str | None:
    for k in list(getattr(citation, "keys", ())) + list(getattr(citation, "also", ())):
        ns, _, rest = str(k).partition(":")
        if ns == "celex" and rest and not rest.startswith("0"):
            return rest
    return None


def _language(collection, query_lang: str | None) -> str:
    from app.plugins.eurlex.source import LANGUAGES
    lang = (query_lang or "").lower()
    return lang if lang in LANGUAGES else "en"


def _base_held(collection, lang: str) -> bool:
    from app import slice_scope
    slices = getattr(collection, "slices", None)
    if slices is None or slices == "all":
        return True
    return slice_scope.is_held(f"lang={lang}/type=R/year=2000", tuple(str(s) for s in slices))


def _history_held(collection, lang: str) -> bool:
    from app import slice_scope
    slices = getattr(collection, "slices", None)
    if slices is None or slices == "all":
        return True
    return slice_scope.is_held(f"history/lang={lang}/type=R/year=2000", tuple(str(s) for s in slices))


def _classify(e: BaseException, stopped: bool, bound: float) -> tuple[str, str]:
    from app.acquire.spec import TransportError
    text = f"{type(e).__name__}: {e}"[:300]
    if stopped:
        return TIMEOUT, f"not done within {bound:.0f} s ({text})"
    low = text.lower()
    if "timed out" in low or "timeout" in low:
        return TIMEOUT, text
    if " 404" in low or "not found" in low or "names no item" in low:
        return NOT_FOUND, text
    if " 403" in low or " 401" in low:
        return REFUSED, text           # the origin said no
    if isinstance(e, TransportError) or "connect" in low or "unreachable" in low:
        return UNREACHABLE, text       # a connection refused included: nothing answered
    return ERROR, text


def _consolidation_day(celex: str) -> int | None:
    from app.plugins.eurlex.source import _CONSOLIDATED
    m = _CONSOLIDATED.match(celex)
    return int(m["date"]) if m else None


def wanted_versions(listed: list[str], held: set[str], as_of: int | None, history: bool) -> list[str]:
    """Of the act's listed consolidations, what this question asks for and
    the collection lacks: with `as_of`, the one in force that day (the latest
    whose date is on or before it); with `history`, every one, newest first."""
    dated = sorted((d, c) for c in listed if (d := _consolidation_day(c)) is not None)
    if as_of is not None:
        before = [c for d, c in dated if d <= as_of]
        pick = before[-1:] if before else []
        return [c for c in pick if c not in held]
    if history:
        return [c for _d, c in reversed(dated) if c not in held]
    return []


def fetch(query: str, collections: Iterable, *, as_of: int | None = None, history: bool = False,
          store=None, query_lang: str | None = None, client=None, halt=None) -> dict[str, Any]:
    """THE WORKER'S: `{"facts": [...], "fetches": [...], "landed": {name:
    [unit, …]}}` for this question (the module docstring) — each version
    landed in the collection's origin. Nothing asked of the origin unless it
    names an EU act and asks for its past. `halt`: the worker's stop, asked
    between requests."""
    import time
    out: dict[str, Any] = {"facts": [], "fetches": [], "landed": {}}
    if not history and as_of is None:
        return out
    from app.acquire import cellar
    import httpx
    cited = _cited(query, store)
    if not cited:
        return out
    budget = LIMIT
    deadline = time.monotonic() + REQUEST_S
    own = client is None
    c = client or httpx.Client(timeout=READ_TIMEOUT_S, follow_redirects=True)

    def left() -> float:
        return deadline - time.monotonic()

    def over(entry: dict) -> bool:
        if halt is not None and halt.is_set():
            out["fetches"].append({**entry, "status": ERROR, "why": "the worker is stopping"})
            return True
        if left() <= 0:
            out["fetches"].append({**entry, "status": TIMEOUT,
                                   "why": f"the question's whole bound of {REQUEST_S:.0f} s passed"})
            return True
        return False
    try:
        for collection in collections:
            if not eligible(collection):
                continue
            lang = _language(collection, query_lang)
            refusal = _license_refusal(collection)
            if refusal is not None:
                out["fetches"].append({"collection": collection.name, "act": None, "language": lang,
                                       "version": None, "status": REFUSED, "why": refusal})
                continue
            if not _base_held(collection, lang):
                out["fetches"].append({"collection": collection.name, "act": None, "language": lang,
                                       "version": None, "status": REFUSED,
                                       "why": f"{collection.name} holds no lang={lang}: no version of it is fetched"})
                continue
            if _history_held(collection, lang):
                continue                     # its history is held: nothing to fetch
            corpus = Path(collection.corpus_dir)
            for want in cited:
                celex = _celex_of(want.citation)
                head = {"collection": collection.name, "act": celex, "language": lang, "version": None}
                if over(head):
                    continue
                stop = None
                bound = min(LIST_TIMEOUT_S, left())
                try:
                    with _bounded(bound) as stop, _cellar(stop, bound) as base:
                        listed = cellar.versions_of(base, celex, lang, client=c)
                except Exception as e:   # noqa: BLE001 — said, never fatal
                    status, why = _classify(e, bool(stop and stop.is_set()), bound)
                    out["fetches"].append({**head, "status": status,
                                           "why": f"the versions of {celex} could not be listed: {why}"})
                    continue
                held = {v for v in listed if _held(corpus, lang, v)}
                for version in wanted_versions(listed, held, as_of, history):
                    entry = {**head, "version": version}
                    if budget <= 0:
                        out["fetches"].append({**entry, "status": LIMITED,
                                               "why": f"at most {LIMIT} fetches a question"})
                        continue
                    if over(entry):
                        continue
                    short = _short_of_space(corpus)
                    if short is not None:
                        out["fetches"].append({**entry, "status": REFUSED, "why": short})
                        continue
                    budget -= 1
                    dest = corpus / _rel(lang, version)
                    stop = None
                    bound = min(FETCH_TIMEOUT_S, left())
                    try:
                        with _bounded(bound) as stop, _cellar(stop, bound) as base:
                            cellar.fetch_unit_from_tree(base, version, lang, dest, client=c)
                    except Exception as e:   # noqa: BLE001 — said, never fatal
                        status, why = _classify(e, bool(stop and stop.is_set()), bound)
                        out["fetches"].append({**entry, "status": status, "why": why})
                        continue
                    out["fetches"].append({**entry, "status": FETCHED, "why": None})
                    out["landed"].setdefault(collection.name, []).append(str(dest))
                    fact = _fact(collection, want, dest)
                    if fact is not None:
                        out["facts"].append(fact)
    finally:
        if own:
            c.close()
    _log(collections, out["fetches"])
    return out


def eligible(collection) -> bool:
    """An EUR-Lex collection on parser 4: the only one whose history is a
    slice of the same origin."""
    from app.acquire import cellar
    return getattr(collection, "plugin", None) == "eurlex" and cellar._tree_mode(collection)


def _cited(query: str, store) -> list:
    from app.ask import articles
    return [c for c in articles.citations(query, (), store) if _celex_of(c.citation)]


# ---- the request between the process answering and the worker ------------------------

def requests_dir(where: Path | None = None) -> Path:
    """The node's request folder, on its writable state volume, beside
    node.sqlite."""
    if where is not None:
        return Path(where)
    from app import config
    return config.louise_root() / "state" / "history-requests"


def _write(path: Path, data: dict) -> None:
    import json
    import os
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def post(request: dict, where: Path | None = None) -> str:
    """The request in the folder, whole (written, then renamed); its id."""
    import secrets
    d = requests_dir(where)
    d.mkdir(parents=True, exist_ok=True)
    rid = secrets.token_hex(8)
    _write(d / f"{rid}.req", {**request, "id": rid})
    return rid


def take(where: Path | None = None, n: int = CONCURRENT) -> list[tuple[str, dict]]:
    """THE WORKER'S: up to `n` requests claimed, oldest first — the rename to
    `.taken` is the claim, so one worker and one cancelling caller never both
    have one. One `scandir` when nothing waits."""
    import json
    import os
    d = requests_dir(where)
    if n <= 0 or not d.is_dir():
        return []
    with os.scandir(d) as it:
        waiting = sorted((e.stat().st_mtime, e.name) for e in it if e.name.endswith(".req"))
    out: list[tuple[str, dict]] = []
    for _m, name in waiting[:n]:
        rid = name[:-4]
        try:
            os.rename(d / name, d / f"{rid}.taken")
            out.append((rid, json.loads((d / f"{rid}.taken").read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue                     # cancelled meanwhile, or not readable: not ours
    return out


def answer(rid: str, result: dict, where: Path | None = None) -> None:
    d = requests_dir(where)
    _write(d / f"{rid}.res", result)
    (d / f"{rid}.taken").unlink(missing_ok=True)


def wait(rid: str, where: Path | None = None, *, pickup_s: float = PICKUP_S,
         answer_s: float = ANSWER_S, poll_s: float = 0.1) -> dict | str:
    """The worker's answer, or why there is none: "not-taken" (no worker took
    it within `pickup_s`; the request is withdrawn), "no-answer" (taken, not
    answered within `answer_s`)."""
    import json
    import os
    import time
    d = requests_dir(where)
    t0 = time.monotonic()
    while True:
        res = d / f"{rid}.res"
        if res.is_file():
            try:
                got = json.loads(res.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                got = None
            res.unlink(missing_ok=True)
            if isinstance(got, dict):
                return got
            return "no-answer"
        waited = time.monotonic() - t0
        if waited > pickup_s and (d / f"{rid}.req").exists():
            try:
                os.rename(d / f"{rid}.req", d / f".{rid}.withdrawn")
                (d / f".{rid}.withdrawn").unlink(missing_ok=True)
                return "not-taken"
            except OSError:
                pass                     # taken in between: wait for its answer
        if waited > answer_s:
            return "no-answer"
        time.sleep(poll_s)


def sweep(where: Path | None = None, now: float | None = None) -> int:
    """Answers nobody read and claims a stopped worker left, older than
    STALE_S, removed."""
    import os
    import time
    d = requests_dir(where)
    if not d.is_dir():
        return 0
    now = time.time() if now is None else now
    gone = 0
    with os.scandir(d) as it:
        for e in it:
            if e.name.endswith((".res", ".taken", ".tmp")) and now - e.stat().st_mtime > STALE_S:
                Path(e.path).unlink(missing_ok=True)
                gone += 1
    return gone


def ask(query: str, collections: Iterable, *, as_of: int | None = None, history: bool = False,
        store=None, query_lang: str | None = None, where: Path | None = None,
        pickup_s: float = PICKUP_S, answer_s: float = ANSWER_S) -> dict[str, Any]:
    """THE ANSWERING PROCESS'S: `{"facts", "fetches"}` from the worker for a
    history or as-of question citing an EU act's article; nothing posted
    otherwise. A worker that did not take it, or did not answer, is said for
    each collection it would have served."""
    out: dict[str, Any] = {"facts": [], "fetches": []}
    if not history and as_of is None:
        return out
    named = [c for c in collections if eligible(c)]
    if not named or not _cited(query, store):
        return out
    rid = post({"query": query, "collections": [c.name for c in named], "as_of": as_of,
                "history": bool(history), "query_lang": query_lang}, where)
    got = wait(rid, where, pickup_s=pickup_s, answer_s=answer_s)
    if isinstance(got, dict):
        return {"facts": list(got.get("facts") or []), "fetches": list(got.get("fetches") or [])}
    status, why = ((UNREACHABLE, f"the worker did not take the request within {pickup_s:.0f} s — "
                                 f"is it running?") if got == "not-taken"
                   else (TIMEOUT, f"the worker did not answer within {answer_s:.0f} s"))
    out["fetches"] = [{"collection": c.name, "act": None, "language": None, "version": None,
                       "status": status, "why": why} for c in named]
    return out


def serve(request: dict, *, halt=None, client=None, register=None) -> dict[str, Any]:
    """THE WORKER'S: one request answered — the node's mode and each
    collection's pause obeyed (the module docstring), the versions fetched
    and landed, exactly those units registered (`register(collection,
    units)`; discovery.register by default) where the mode parses."""
    from app import collections as collections_mod, node_state, refs
    names = list(request.get("collections") or [])
    entry = lambda name, status, why: {"collection": name, "act": None, "language": None,  # noqa: E731
                                       "version": None, "status": status, "why": why}
    mode = node_state.peek_worker_state()
    if not node_state.runs(mode, node_state.FETCH):
        return {"facts": [], "fetches": [entry(n, REFUSED, f"the node is in {mode}: nothing is fetched")
                                         for n in names]}
    paused = node_state.paused_collections()
    by_name = {c.name: c for c in collections_mod.load_all()}
    fetches = [entry(n, REFUSED, f"{n} is paused on this node: nothing is fetched for it") for n in names
               if n in paused]
    fetches += [entry(n, ERROR, f"{n} is not held here") for n in names if n not in by_name]
    serve_these = [by_name[n] for n in names if n in by_name and n not in paused]
    try:
        store = refs.get_store()
    except Exception:   # noqa: BLE001 — the grammar needs no reference data
        store = None
    got = fetch(str(request.get("query") or ""), serve_these, as_of=request.get("as_of"),
                history=bool(request.get("history")), store=store, query_lang=request.get("query_lang"),
                client=client, halt=halt)
    if node_state.runs(mode, node_state.PARSE):
        for name, units in got["landed"].items():
            try:
                (register or _register)(by_name[name], [Path(u) for u in units])
            except Exception as e:   # noqa: BLE001 — landed; the next scan registers it
                fetches.append(entry(name, ERROR, f"fetched, registered by the next scan: "
                                                  f"{type(e).__name__}: {e}"[:300]))
    return {"facts": got["facts"], "fetches": fetches + got["fetches"]}


def _register(collection, units: list[Path]) -> None:
    """Exactly these units into the manifest (discovery.register), then the
    slice pass from its cursor over the new rows."""
    from contextlib import closing
    from app import db, discovery, slice_scope
    plugin = collection.plugin_instance()
    with closing(db.connect(collection)) as con:
        discovery.register(con, collection, [plugin.ref_of_unit(u) for u in units])
        slice_scope.apply(con, collection)


def serve_one(rid: str, request: dict, where: Path | None = None, *, halt=None) -> None:
    """THE WORKER THREAD'S: serve, then answer — whatever happens, an answer."""
    try:
        result = serve(request, halt=halt)
    except Exception as e:   # noqa: BLE001 — the caller is told, never left waiting
        result = {"facts": [], "fetches": [{"collection": None, "act": None, "language": None,
                                            "version": None, "status": ERROR,
                                            "why": f"{type(e).__name__}: {e}"[:300]}]}
    answer(rid, result, where)


def summary(got: dict[str, Any], *, requested: bool) -> dict[str, Any]:
    """The answer's `history` (05 §5.3): what was asked of Cellar for this
    question and how each fetch ended, with its reason."""
    return {"requested": requested, "limit": LIMIT, "timeout_s": FETCH_TIMEOUT_S,
            "fetches": list(got.get("fetches") or [])}


def _license_refusal(collection) -> str | None:
    """Invariant 9: no byte moves from a yellow or red origin without the
    acknowledgement (app/licensing.py, THE resolution)."""
    from app import licensing
    try:
        if licensing.state_for(collection) != licensing.MISSING:
            return None
        return (f"{collection.name}: license_class={licensing.license_class_for(collection)} needs an "
                f"acknowledgement of the terms before anything is fetched (invariant 9)")
    except Exception as e:   # noqa: BLE001 — fail closed
        return f"{collection.name}: its license state cannot be judged ({type(e).__name__}: {e})"[:300]


def _short_of_space(corpus: Path) -> str | None:
    from app import disk_guard
    u = disk_guard.usage(corpus)
    if u is None or not u["short"]:
        return None
    return (f"{u['free'] / disk_guard.GIB:.1f} GiB free on {u['path']}, under its floor of "
            f"{u['floor'] / disk_guard.GIB:.1f} GiB: nothing is fetched until space is freed")


def _log(collections: Iterable, fetches: list[dict]) -> None:
    """Each fetch in its collection's event log (CLAUDE.md: every log line
    names its collection); a log that cannot be written never fails the answer."""
    if not fetches:
        return
    from contextlib import closing
    from app import db
    by_name = {getattr(c, "name", None): c for c in collections}
    for f in fetches:
        c = by_name.get(f.get("collection"))
        if c is None:
            continue
        what = f"history on request: {f.get('version') or f.get('act')} ({f.get('language')}) {f['status']}"
        try:
            with closing(db.connect(c)) as con:
                db.log_event(con, "info" if f["status"] == FETCHED else "warn", "history",
                             what + (f": {f['why']}" if f.get("why") else ""))
        except Exception:   # noqa: BLE001 — the answer says it; the log is the node's copy
            pass


def _rel(lang: str, celex: str) -> str:
    from app.plugins.eurlex.source import HISTORY, unit_rel
    return f"{HISTORY}/{unit_rel(lang, celex)}"


def _held(corpus: Path, lang: str, celex: str) -> bool:
    from app.plugins.eurlex.source import HISTORY, unit_rel
    rel = unit_rel(lang, celex)
    return any((b / rel / "tree.json").is_file() for b in (corpus, corpus / HISTORY))


def _fact(collection, want, unit: Path) -> dict | None:
    """The cited article of a version fetched now, as a pinned fact."""
    plugin = collection.plugin_instance()
    ref = plugin.ref_of_unit(unit)
    if ref is None:
        return None
    try:
        doc = plugin.parse(plugin.fetch(ref))
    except Exception:   # noqa: BLE001 — fetched, not readable: the fetch is said, no fact
        return None
    article = want.citation.article
    texts = [body for label, body in doc.sections if want.read(label) == article]
    if not texts:
        return None
    x = doc.extra or {}
    return {"entity_id": f"celex:{x.get('celex')}~art{article}", "kind": "article",
            "source_id": collection.name,
            "label": f"{doc.title} — {want.citation.label} (version of {x.get('valid_from')}, fetched now from Cellar)",
            "fields": {"text": "\n".join(texts), "in force from": x.get("valid_from"),
                       "version": f"{collection.plugin}:{ref.native_id}", "fetched": True},
            "via": None}
