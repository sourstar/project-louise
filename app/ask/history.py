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

WHO WRITES. The process answering the question (the web) never writes the
corpus — it is mounted read-only there (München, 2026-11-16). It lands the
unit, whole or not at all (`cellar._land`), in the collection's FETCHED
inbox beside its manifest (`inbox_dir`, on the writable state volume), at
the path the unit takes in the corpus; the worker, the corpus's one writer,
moves it into the origin (`adopt`), registers exactly those units in the
manifest (discovery.register: a cost per unit, never a scan of the corpus)
and its slice pass places them. The pattern is the bundle inbox's
(app/bundles/importer.py `inbox_dir`, worker `_drain_bundle_inbox`). The
cited article is pinned from the inbox copy, so the answer does not wait for
the worker. Not below the free-space floor (app/disk_guard.py), and not
without the collection's licence acknowledgement (invariant 9): refused,
and said.

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
    """The body bound to the transfer manager at INTERACTIVE priority, with a
    stop event set at its deadline (the module docstring). Yields the event:
    set, the body was stopped by its deadline."""
    import threading
    from app.transfer.bucket import Priority
    from app.transfer.manager import get_manager
    stop = threading.Event()
    timer = threading.Timer(seconds, stop.set)
    timer.daemon = True
    timer.start()
    try:
        with get_manager().bind(Priority.INTERACTIVE, stop=stop):
            yield stop
    finally:
        timer.cancel()


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
          store=None, query_lang: str | None = None, client=None) -> dict[str, Any]:
    """`{"facts": [...], "fetches": [...]}` for this question (the module
    docstring); nothing asked of the origin unless it names an EU act and
    asks for its past."""
    out: dict[str, Any] = {"facts": [], "fetches": []}
    if not history and as_of is None:
        return out
    from app.ask import articles
    from app.acquire import cellar
    import httpx
    cited = [c for c in articles.citations(query, (), store) if _celex_of(c.citation)]
    if not cited:
        return out
    budget = LIMIT
    own = client is None
    c = client or httpx.Client(timeout=READ_TIMEOUT_S, follow_redirects=True)
    try:
        for collection in collections:
            if getattr(collection, "plugin", None) != "eurlex" or not cellar._tree_mode(collection):
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
            spec_base = _endpoint()
            corpus = Path(collection.corpus_dir)
            inbox = inbox_dir(collection)
            for want in cited:
                celex = _celex_of(want.citation)
                stop = None
                try:
                    with _bounded(LIST_TIMEOUT_S) as stop:
                        listed = cellar.versions_of(spec_base, celex, lang, client=c)
                except Exception as e:   # noqa: BLE001 — said, never fatal
                    status, why = _classify(e, bool(stop and stop.is_set()), LIST_TIMEOUT_S)
                    out["fetches"].append({"collection": collection.name, "act": celex, "language": lang,
                                           "version": None, "status": status,
                                           "why": f"the versions of {celex} could not be listed: {why}"})
                    continue
                held = {v for v in listed if _held(corpus, lang, v) or _held(inbox, lang, v)}
                for version in wanted_versions(listed, held, as_of, history):
                    entry = {"collection": collection.name, "act": celex, "language": lang,
                             "version": version}
                    if budget <= 0:
                        out["fetches"].append({**entry, "status": LIMITED,
                                               "why": f"at most {LIMIT} fetches a question"})
                        continue
                    short = _short_of_space(inbox)
                    if short is not None:
                        out["fetches"].append({**entry, "status": REFUSED, "why": short})
                        continue
                    budget -= 1
                    dest = inbox / _rel(lang, version)
                    stop = None
                    try:
                        with _bounded(FETCH_TIMEOUT_S) as stop:
                            cellar.fetch_unit_from_tree(spec_base, version, lang, dest, client=c)
                    except Exception as e:   # noqa: BLE001 — said, never fatal
                        status, why = _classify(e, bool(stop and stop.is_set()), FETCH_TIMEOUT_S)
                        out["fetches"].append({**entry, "status": status, "why": why})
                        continue
                    out["fetches"].append({**entry, "status": FETCHED, "why": None})
                    fact = _fact(collection, want, dest)
                    if fact is not None:
                        out["facts"].append(fact)
    finally:
        if own:
            c.close()
    _log(collections, out["fetches"])
    return out


def summary(got: dict[str, Any], *, requested: bool) -> dict[str, Any]:
    """The answer's `history` (05 §5.3): what was asked of Cellar for this
    question and how each fetch ended, with its reason."""
    return {"requested": requested, "limit": LIMIT, "timeout_s": FETCH_TIMEOUT_S,
            "fetches": list(got.get("fetches") or [])}


def inbox_dir(collection) -> Path:
    """Where the units fetched on request wait for the worker: beside the
    collection's manifest (its writable state), laid out as in the corpus."""
    return Path(collection.manifest_path).parent / "fetched"


def adopt(collection, *, log=lambda m: None) -> list[Path]:
    """The worker's half (the module docstring): every whole unit waiting in
    the collection's inbox moved into its origin, whole or not at all, and
    taken out of the inbox; one the origin already holds is only taken out.
    Returns the units now in the origin. A unit that cannot be moved stays,
    said, and is tried again next time."""
    import shutil
    from app.acquire import cellar
    from app.plugins.eurlex.source import unit_files
    inbox = inbox_dir(collection)
    if not inbox.is_dir():
        return []
    corpus = Path(collection.corpus_dir)
    landed: list[Path] = []
    for tree in sorted(inbox.glob("*/lang=*/*/*/tree.json")):
        unit = tree.parent
        if any(part.startswith(".") for part in unit.relative_to(inbox).parts):
            continue                       # a staging directory of an interrupted landing
        rel = unit.relative_to(inbox)
        target = corpus / rel
        try:
            if not (target / tree.name).is_file():
                cellar._land(target, {e.name: Path(e.path).read_bytes() for e in unit_files(unit)})
                landed.append(target)
            shutil.rmtree(unit)
        except OSError as e:
            log(f"history on request: {rel} not moved into the origin: {type(e).__name__}: {e}"[:300])
    return landed


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


def _endpoint() -> str:
    from app.acquire import specs
    return specs.ACQUIRE_REGISTRY["eurlex"].endpoints[0].url


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
