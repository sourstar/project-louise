"""Cellar — the EU Publications Office's repository — as an acquisition
origin: the INVENTORY by SPARQL, each UNIT by content negotiation (09
§9.17; legal-fr-de-eu.md §4.1, §7.1; the eurlex plugin,
app/plugins/eurlex/source.py). Normattiva's export_job.py is the precedent:
a module that talks to an origin's API, behind the generic engine.

THE LISTING IS THE INVENTORY. Cellar publishes no file list; its SPARQL
endpoint (`/webapi/rdf/sparql`, Virtuoso) answers which works have a Formex
manifestation in which language, and when each last changed. `listing`
asks it in WINDOWS — one CELEX year at a time, for the languages the
profile selects — each window counted first (`COUNT`) and then read in
pages (`ORDER BY … LIMIT … OFFSET`): a Virtuoso answer can be cut at a
timeout without saying so (§4.1), so a window whose pages add up to less
than its count is the origin's episode, raised, and the pass lists again
later. Each (act version, language) is one `RemoteFile` whose `rel_path`
is the unit's notice (`lang=<code>/<yyyy>/<CELEX>/notice.xml`) and whose
`mtime` is the work's last modification as Cellar states it: the engine
compares both with what it holds (`fetch_files`), so the listing compared
with what is held IS the delta — a unit is fetched when it is new or its
date moved, never otherwise.

ONE UNIT, WHOLE OR NOT AT ALL (`fetch_unit`):

  1. the BRANCH NOTICE, `GET /resource/celex/<CELEX>` with `Accept:
     application/xml;notice=branch` and the language in Accept-Language;
     Cellar answers 303 to it (§7.1). It must be the CELEX's and the
     language's (rule 2: what the origin returned names the record);
  2. the FORMEX ITEMS: the notice's fmx4 manifestation lists each item's
     URI and NAME (`TECHMD/STREAM_NAME`) and size. Where it lists none, the
     content negotiation for `application/xml;type=fmx4` answers 300
     Multiple Choices with the manifestation's items (§7.1), read by NAME
     — never by order — from the links it lists, and an item a link does
     not name is named by its Content-Disposition. A name that is not a
     plain file name is refused;
  3. each item fetched, its size held to the notice's; the wrapper
     (`….doc.xml`) among them, in the language asked;
  4. written into a staging directory beside the unit, then swapped in
     (the old unit renamed away, the new one renamed into place, the old
     removed): a reader never sees half a unit, and an item the origin no
     longer lists leaves with the old directory.

EVERY BYTE THROUGH THE BUDGET (invariant 5): each response is streamed in
chunks charged to the transfer manager's bucket (`budget.acquire_bytes`),
the SPARQL answers included. TWO CONNECTIONS AT MOST: the spec's endpoint
and its etiquette say so, and the engine's pool holds to them. A 5xx, a
timeout or a dropped connection is retried within the request (a second
apart, doubling: 15 s, the template's rule 3) and then raised in words the
engine reads as a deferral (" 503 to ", "connection timed out"); a 404, a
wrong language or a missing wrapper is the file's own failure.

THE STANDARD ALTERNATIVE, NOT ADOPTED: an RDF/SPARQL client library
(SPARQLWrapper, rdflib) — the SPARQL 1.1 Protocol's POST and its JSON
results format (W3C) are a form field and `json.loads`, and a new
dependency is not worth one query shape. Cellar's ATOM/RSS notification
feeds would make a pass cheaper than a SPARQL inventory; they pay once the
inventory's cost is measured on the node (OWED) and are a second origin
form to record when adopted.

THE PREDICATES (cdm:resource_legal_id_celex, expression_belongs_to_work,
expression_uses_language, manifestation_manifests_expression,
manifestation_type, cmr:lastModificationDate) are the CDM's, as the
notices' element names and third-party clients use them [S]; the probe's
"400 consolidated versions of 2016 regulations in 4.3 s" (§7.1) is the
only SPARQL answer measured from the node. The window's counts per year
and language, the busiest start day and the inventory's time are OWED.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote

import httpx

from ..transfer.manager import current_handle
from . import budget
from .spec import AcquisitionSpec, Endpoint, Filter, RemoteFile, TransferAborted, TransportError, Waiting

PREFIX = "cellar:"
SPARQL_PATH = "/webapi/rdf/sparql"
RESOURCE_PATH = "/resource/celex/"
NOTICE = "notice.xml"
#: the first CELEX year a window asks (the ECSC's acts, 1952); the last is this year
FIRST_YEAR = 1952
#: one page of a window: below Virtuoso's usual result cap (10,000 rows)
PAGE = 5_000
#: one HTTP exchange's bound; a response streams past it chunk by chunk
TIMEOUT_S = 120.0
#: ONE SPARQL QUERY'S bound, apart (München, 2026-11-14, from the node, the
#: count query of one CELEX year in Italian: 2021 answered in 45.9 s, 1990 in
#: 235.5 s; at TIMEOUT_S every heavy year timed out five times and the whole
#: fetch failed). Virtuoso evaluates the whole window before its first byte,
#: so the bound is on the wait for the answer: ~4x the slowest measured.
#: The wait honours a stop every second (`_answer_of`), so a long query never
#: delays a SIGTERM or a `node pause` (invariant 2: bounded in time)
SPARQL_TIMEOUT_S = 900.0
_CHUNK = 1 << 16
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.()\-]*$")
_LINK = re.compile(r"""<a\b[^>]*\bhref\s*=\s*["'](?P<href>[^"']+?/DOC_\d+)["'][^>]*>(?P<text>.*?)</a>""",
                   re.IGNORECASE | re.S)
_HREF = re.compile(r"""(?P<href>https?://[^\s"'<>]+?/DOC_\d+)(?![\w])""")
_DISPOSITION = re.compile(r"""filename\*?=(?:UTF-8'')?["']?(?P<name>[^"';]+)""", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_LANG_PATTERN = re.compile(r"^(?:history/)?lang=(?P<lang>[a-z]{2})/")
_USER_AGENT = (f"louise-rag/0.1 (+https://github.com/sourstar/project-louise) "
               f"httpx/{httpx.__version__}")


# ---- the units, as the engine sees them ---------------------------------------------------

def encode(celex: str, lang: str) -> str:
    return PREFIX + json.dumps({"celex": celex, "lang": lang}, sort_keys=True, separators=(",", ":"))


def is_unit(f: RemoteFile) -> bool:
    return bool(f.remote) and str(f.remote).startswith(PREFIX)


def unit_of(f: RemoteFile) -> dict:
    return json.loads(str(f.remote)[len(PREFIX):])


#: the unit's completion mark, written last (`_land`): the scoped tree
#: (parser 3, 2026-11-14; before it the notice, so a unit fetched then lacks
#: its tree and is completed by the next fetch, its other bytes kept)
TREE_MARK = "tree.json"


def rel_path(lang: str, celex: str, history: bool = False) -> str:
    """The unit's completion mark, relative to the corpus root; parser 4's
    history slice under `history/` (source.HISTORY)."""
    from ..plugins.eurlex.source import HISTORY, unit_rel
    return f"{HISTORY + '/' if history else ''}{unit_rel(lang, celex)}/{TREE_MARK}"


def is_history(celex: str, in_force: str | None) -> bool:
    """Parser 4's history slice, for a unit not held yet: a consolidated
    version, or an act Cellar does not state in force (eurlex_dump.py)."""
    from ..plugins.eurlex.source import _CONSOLIDATED
    return bool(_CONSOLIDATED.match(celex)) or str(in_force or "").lower() not in ("true", "1")


def languages_of(flt: Filter) -> tuple[str, ...]:
    """The languages a profile's filter selects (`lang=<code>/*`); every
    language for a filter that names none (`all`)."""
    from ..plugins.eurlex.source import LANGUAGES
    got = []
    for pattern in flt.include:
        m = _LANG_PATTERN.match(pattern)
        if m and m["lang"] in LANGUAGES and m["lang"] not in got:
            got.append(m["lang"])
        elif pattern in ("*", "lang=*", "lang=*/*"):
            return tuple(sorted(LANGUAGES))
    return tuple(sorted(got))


def _stop_requested() -> bool:
    """Whether the stop bound to this thread (the pass's) is set."""
    handle = current_handle()
    stop = getattr(handle, "stop", None) if handle is not None else None
    return stop is not None and stop.is_set()


def _stop_check() -> None:
    if _stop_requested():
        raise TransferAborted("cellar: stopped between requests")


def _headers(extra: dict | None = None) -> dict:
    return {"User-Agent": _USER_AGENT, **(extra or {})}


def _policy():
    """Cellar's retry policy: backoff.py's, the spec's etiquette over it."""
    from . import backoff, specs
    return backoff.Policy.of(specs.EURLEX_SPEC)


def _get(client: httpx.Client, url: str, headers: dict, what: str, *,
         charge: Callable[[int], None], ok=(200,), follow: bool = True) -> httpx.Response:
    """One GET, its body read in chunks charged to the budget, retried on
    the origin's episode (backoff.py: a 5xx, 408, 429, a timeout, a drop,
    Retry-After honoured) — then raised in words the engine's `transient`
    reads as a deferral."""
    from . import backoff

    def once(hdrs: dict):
        """(status, retry-after, None) for an episode's status, else
        (None, None, the response); a non-ok status raises."""
        with client.stream("GET", url, headers=hdrs, follow_redirects=follow) as r:
            if backoff.retryable_status(r.status_code):
                return r.status_code, r.headers.get("retry-after"), None
            body = bytearray()
            for chunk in r.iter_bytes(_CHUNK):
                body += chunk
                charge(len(chunk))
            if r.status_code not in ok:
                raise TransportError(f"cellar: {r.status_code} to {what} ({url})")
            return None, None, httpx.Response(r.status_code, headers=r.headers, content=bytes(body),
                                              request=r.request)

    r_ = backoff.Retry(what, _policy())
    for _ in r_:
        try:
            try:
                status, after, got = once(headers)
            except httpx.DecodingError:
                # a body its Content-Encoding does not decode ("Error -3 while
                # decompressing data: incorrect header check"): asked again AT
                # ONCE for the body as it is — `Accept-Encoding: identity`, RFC
                # 9110 §12.5.3 — and WITHOUT cooling the endpoint: it answered;
                # its coding of this one body failed. 2026-11-14/15, 3,258
                # Italian units failing so were fetched whole when asked again;
                # 2026-11-18, four Hungarian .html items failed eight times each,
                # and as episodes they cooled Cellar for every worker until
                # EUR-Lex4's pass deferred all its 5,977 units, round after round
                # (München). Undecodable even as identity: the item's verdict,
                # said, and the pass goes on.
                try:
                    status, after, got = once({**headers, "Accept-Encoding": "identity"})
                except httpx.DecodingError as e:
                    raise TransportError(f"cellar: {what}: its body does not decode as its "
                                         f"Content-Encoding says, even asked for identity "
                                         f"({type(e).__name__}: {e})"[:400]) from e
            if got is None:
                r_.episode(f"{status} to {what}", retry_after=after)
                continue
            return got
        except httpx.TimeoutException as e:
            r_.episode(f"connection timed out on {what}: {type(e).__name__}", cause=e)
        except httpx.TransportError as e:
            r_.episode(f"{type(e).__name__} on {what}: {e}", cause=e)
    r_.give_up("cellar: ")


# ---- the inventory --------------------------------------------------------------------------

_PREFIXES = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
             "PREFIX cmr: <http://publications.europa.eu/ontology/cdm/cmr#>\n")
_LANGUAGE_URI = "http://publications.europa.eu/resource/authority/language/"


def _window(year: int, languages: tuple[str, ...]) -> str:
    """The WHERE of one window: the works whose CELEX opens `3<year>` (an
    act of sector 3) or `0<year>` (its consolidated versions), each with a
    manifestation in one of FORMS of an expression in one of `languages`
    (parser 3: before it fmx4 alone, which left out every act Cellar holds
    in XHTML, HTML or PDF only)."""
    from ..plugins.eurlex.source import LANGUAGES
    values = " ".join(f"<{_LANGUAGE_URI}{LANGUAGES[lang]}>" for lang in languages)
    return (f"  ?work cdm:resource_legal_id_celex ?celex .\n"
            f"  FILTER(STRSTARTS(STR(?celex), \"3{year:04d}\") || STRSTARTS(STR(?celex), \"0{year:04d}\"))\n"
            f"  ?expr cdm:expression_belongs_to_work ?work ; cdm:expression_uses_language ?language .\n"
            f"  VALUES ?language {{ {values} }}\n"
            f"  ?manif cdm:manifestation_manifests_expression ?expr ; cdm:manifestation_type ?type .\n"
            f"  FILTER(STR(?type) IN ({', '.join(chr(34) + f + chr(34) for f in FORMS)}))\n")


#: A UNIT'S LAST MODIFICATION, as the listing reads it: the latest of its
#: work's, its expression's and its manifestations' (2026-11-19: Cellar
#: replaced 32015D1735's English Formex at 2026-11-19 and left the work's date
#: at 2026-11-02 — the work's alone never saw it). The held side compares the
#: same: `Notice.modified`, from the unit's tree (from_tree) or branch notice
MODIFIED = ("OPTIONAL { { ?work cmr:lastModificationDate ?modified } UNION "
            "{ ?expr cmr:lastModificationDate ?modified } UNION "
            "{ ?manif cmr:lastModificationDate ?modified } }")


def count_query(year: int, languages: tuple[str, ...]) -> str:
    return (_PREFIXES + "SELECT (COUNT(*) AS ?n) WHERE {\n SELECT DISTINCT ?celex ?language WHERE {\n"
            + _window(year, languages) + " }\n}")


def fingerprint_query(year: int, languages: tuple[str, ...]) -> str:
    """The window's COUNT of (CELEX, language) rows, as `count_query` counts
    them, and the latest last modification among them: one query of the
    count's shape, which says whether the window held from the pass before
    still stands (cellar_listing.py)."""
    return (_PREFIXES + "SELECT (COUNT(*) AS ?n) (MAX(?last) AS ?max) WHERE {\n"
            " SELECT ?celex ?language (MAX(?modified) AS ?last) WHERE {\n"
            + _window(year, languages)
            + "  " + MODIFIED + "\n"
            + " }\n GROUP BY ?celex ?language\n}")


def page_query(year: int, languages: tuple[str, ...], offset: int, force: bool = False) -> str:
    """A page of the window's rows. `force` (parser 4): each work's
    `resource_legal_in-force` too, which places a unit not held yet in the
    base or the history slice (`is_history`)."""
    return (_PREFIXES + "SELECT ?celex ?language (MAX(?modified) AS ?last)"
            + (" (SAMPLE(STR(?inforce)) AS ?force)" if force else "") + " WHERE {\n"
            + _window(year, languages)
            + "  " + MODIFIED + "\n"
            + ("  OPTIONAL { ?work cdm:resource_legal_in-force ?inforce }\n" if force else "")
            + f"}}\nGROUP BY ?celex ?language\nORDER BY ?celex ?language\nLIMIT {PAGE} OFFSET {offset}")


def _answer_of(fn: Callable[[], Any], *, poll_s: float = 1.0) -> Any:
    """`fn()` in a daemon thread, its answer awaited while a stop is checked
    every `poll_s`: a stop raises TransferAborted at once, and the thread is
    left to its own bound (SPARQL_TIMEOUT_S), its answer dropped."""
    import threading
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:            # noqa: BLE001 — raised again in the caller
            box["error"] = e
    import contextvars
    # the pass's transfer handle crosses into the thread (review of a69741dd,
    # S4: unbound, every SPARQL byte was charged at BACKFILL with no stop)
    t = threading.Thread(target=contextvars.copy_context().run, args=(run,), name="cellar-sparql", daemon=True)
    t.start()
    while t.is_alive():
        t.join(poll_s)
        _stop_check()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def sparql(client: httpx.Client, base: str, query: str, *, what: str = "the SPARQL query",
           charge: Callable[[int], None] = budget.acquire_bytes) -> list[dict]:
    """The SPARQL 1.1 Protocol: the query POSTed as a form field, the JSON
    results format back (`results.bindings`), every byte charged. `what`
    names the query in an error ("the count of 1990"), so the slow window is
    said, not guessed."""
    from . import backoff
    url = base.rstrip("/") + SPARQL_PATH
    timeout = httpx.Timeout(SPARQL_TIMEOUT_S, connect=min(TIMEOUT_S, SPARQL_TIMEOUT_S))

    def once() -> tuple[int, bytes]:
        with client.stream("POST", url, data={"query": query, "format": "application/sparql-results+json"},
                           headers=_headers({"Accept": "application/sparql-results+json"}),
                           timeout=timeout) as r:
            if backoff.retryable_status(r.status_code):
                return r.status_code, r.headers.get("retry-after"), b""
            body = bytearray()
            for chunk in r.iter_bytes(_CHUNK):
                body += chunk
                charge(len(chunk))
            return r.status_code, None, bytes(body)
    # a query that ran to its bound is not repeated at once: the duration
    # floor (backoff.py) makes its next wait at least as long, which defers
    # it to the next round — `listing` splits such a window instead
    r_ = backoff.Retry(what, _policy())
    for _ in r_:
        t0 = time.monotonic()
        try:
            status, asked, body = _answer_of(once)
        except httpx.TimeoutException as e:
            r_.episode(f"connection timed out on {what}: {type(e).__name__} after "
                       f"{time.monotonic() - t0:.0f} s (bound {SPARQL_TIMEOUT_S:.0f} s)", cause=e)
            continue
        except httpx.TransportError as e:
            r_.episode(f"{type(e).__name__} on {what}: {e}", cause=e)
            continue
        if backoff.retryable_status(status):
            r_.episode(f"{status} to {what}", retry_after=asked)
            continue
        if status != 200:
            raise TransportError(f"cellar: {status} to {what}: {body[:200]!r}")
        try:
            return list(json.loads(body)["results"]["bindings"])
        except (ValueError, KeyError, TypeError) as e:
            # a page cut mid-answer reads as broken JSON: the origin's episode
            r_.episode(f"connection timed out: {what}'s answer did not parse ({e})", cause=e)
    r_.give_up("cellar: ")


def _epoch(stamp: str | None) -> int | None:
    """`2020-06-16T10:12:00.123+02:00` or `2020-06-16` → epoch seconds."""
    if not stamp:
        return None
    from datetime import datetime, timezone
    text = stamp.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        try:
            d = datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def listing(endpoint: Endpoint, spec: AcquisitionSpec, flt: Filter, *,
            years: range | None = None, client: httpx.Client | None = None,
            charge: Callable[[int], None] = budget.acquire_bytes,
            place: Callable[[str, str, str | None], bool] | None = None,
            say: Callable[[str], None] | None = None) -> Iterator[RemoteFile]:
    """The origin's COMPLETE listing for the profile (the spec's listing
    contract): every window to its count, every page to the end. `place`
    (parser 4): `(celex, lang, in force) → history?`, each work's in-force
    asked with its page. `say`: one line a window — its year, languages,
    rows and seconds, held or asked — so a listing of hours is seen to move
    (München, 2026-11-16: 31 min without a line)."""
    from ..plugins.eurlex.source import LANGUAGES
    languages = languages_of(flt)
    if not languages:
        return
    code_of = {v: k for k, v in LANGUAGES.items()}
    years = years if years is not None else range(FIRST_YEAR, time.gmtime().tm_year + 1)
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S)
    # THE SPLIT, LEARNED (2026-11-16): a count that timed out is split
    # (`_window_files`), and the years after start at the size that answered
    # — measured 2026-11-14, 1990's count for 24 languages ran past 900 s
    # while Italian alone answered in 26-235 s, so asking each year's 24
    # first spent 900 s a year before any row. Never wider again in a pass.
    learned = {"group": len(languages)}
    try:
        for year in years:
            _stop_check()
            t0, n = time.monotonic(), 0
            group = max(1, learned["group"])
            chunks = [languages[i:i + group] for i in range(0, len(languages), group)]
            for chunk in chunks:
                for f in _window_files(client, endpoint.url, year, chunk, code_of, charge, place, learned):
                    n += 1
                    yield f
            if say is not None:
                say(f"cellar: listed {year} ({len(languages)} language(s), {len(chunks)} window(s)): {n} units "
                    f"in {time.monotonic() - t0:.0f} s")
    finally:
        if own:
            client.close()


def versions_query(celex: str, lang: str) -> str:
    """The consolidated versions Cellar holds of one act in one language —
    by the act's own link (`act_consolidated_based_on_resource_legal`), never
    a scan of every CELEX — each with its work's last modification."""
    from ..plugins.eurlex.source import LANGUAGES
    forms = ", ".join(chr(34) + f + chr(34) for f in FORMS)
    return (_PREFIXES + "SELECT ?celex (MAX(?modified) AS ?last) WHERE {\n"
            f"  ?base cdm:resource_legal_id_celex ?bc . FILTER(STR(?bc) = \"{celex}\")\n"
            "  ?work cdm:act_consolidated_based_on_resource_legal ?base ; cdm:resource_legal_id_celex ?celex .\n"
            f"  ?expr cdm:expression_belongs_to_work ?work ; cdm:expression_uses_language "
            f"<{_LANGUAGE_URI}{LANGUAGES[lang]}> .\n"
            f"  ?manif cdm:manifestation_manifests_expression ?expr ; cdm:manifestation_type ?type .\n"
            f"  FILTER(STR(?type) IN ({forms}))\n"
            "  OPTIONAL { ?work cmr:lastModificationDate ?modified }\n"
            "} GROUP BY ?celex ORDER BY ?celex")


def versions_of(base: str, celex: str, lang: str, *, client: httpx.Client,
                charge: Callable[[int], None] = budget.acquire_bytes) -> list[str]:
    """The CELEX of every consolidated version of `celex` Cellar holds in
    `lang` (app/ask/history.py), sorted; raises TransportError."""
    rows = sparql(client, base, versions_query(celex, lang), what=f"the versions of {celex} ({lang})",
                  charge=charge)
    return sorted({r.get("celex", {}).get("value", "") for r in rows} - {""})


def _timed_out(e: TransportError) -> bool:
    from . import backoff
    return isinstance(e, backoff.Deferred) or "timed out" in str(e).lower()


#: httpx's words for the endpoint going away, and the statuses that are an
#: episode (backoff.retryable_status) — not a verdict on the unit
_EPISODE = ("timed out", "timeout", "connecterror", "connection", "readerror", "remoteprotocolerror",
            "network")
_EPISODE_STATUS = re.compile(r"cellar: (?:429|5\d\d) to ")


def _episode(e: TransportError) -> bool:
    """The endpoint going away rather than the unit being bad (engine.transient
    knows rsync's words; these are httpx's and Cellar's)."""
    from . import backoff, engine
    text = str(e).lower()
    return (isinstance(e, backoff.Deferred) or engine.transient(text) or any(m in text for m in _EPISODE)
            or bool(_EPISODE_STATUS.search(text)))


def _window_files(client, base: str, year: int, languages: tuple[str, ...], code_of: dict,
                  charge, place=None, learned: dict | None = None) -> Iterator[RemoteFile]:
    """One CELEX year's files in `languages`. A COUNT THAT TIMES OUT is not
    asked again: the window is split, its languages in two halves, each
    counted and paged on its own, down to one language (the architecture
    session, 2026-11-14: "a Cellar query that timed out at 900 s twice will
    time out a third time; split it rather than repeat it"). Measured the
    same day: 1990's count for all 24 languages ran past 900 s, Italian
    alone answered in 26.5-235.5 s. One language that still times out is
    deferred to the next round."""
    from . import cellar_listing
    lang_text = ",".join(languages) if len(languages) <= 4 else f"{len(languages)} languages"
    counted_at = time.time()                # what the window is known whole as of (review of 7eb82ba1, 3a)
    try:
        t_q = time.monotonic()
        counted = sparql(client, base, fingerprint_query(year, languages),
                         what=f"the count of {year} ({lang_text})", charge=charge)
        _took(learned, f"the count of {year} ({lang_text})", t_q)
    except TransportError as e:
        if len(languages) > 1 and _timed_out(e):
            half = len(languages) // 2
            if learned is not None:
                learned["group"] = min(learned.get("group", len(languages)), max(1, half))
            yield from _window_files(client, base, year, languages[:half], code_of, charge, place, learned)
            yield from _window_files(client, base, year, languages[half:], code_of, charge, place, learned)
            return
        raise
    try:
        want = int(counted[0]["n"]["value"]) if counted else 0
    except (KeyError, ValueError, IndexError):
        raise TransportError(f"cellar: the count of {year} answered {counted!r:.200}") from None
    latest = (counted[0].get("max") or {}).get("value") if counted else None
    force = place is not None
    kept = cellar_listing.held(base, year, languages, want, latest, force=force)
    if kept is not None:
        cellar_listing.vouched(base, year, languages, want, latest, force=force, at=counted_at)
        # the window as the last pass listed it: its count and its latest
        # modification are the same, so no row moved (cellar_listing.py)
        for row in kept:
            celex, lang, mtime = row[:3]
            history = place(celex, lang, row[3]) if force else False
            yield RemoteFile(rel_path=rel_path(lang, celex, history), mtime=mtime,
                             remote=encode(celex, lang))
        return
    listed: list = []
    got = 0
    while got < want:
        t_q = time.monotonic()
        rows = sparql(client, base, page_query(year, languages, got, force),
                      what=f"the {year} page at offset {got} ({lang_text})", charge=charge)
        _took(learned, f"the {year} page at offset {got} ({lang_text}): {len(rows)} rows", t_q)
        if not rows:
            break
        for row in rows:
            celex = row.get("celex", {}).get("value", "")
            lang = code_of.get(row.get("language", {}).get("value", "").rsplit("/", 1)[-1])
            if not celex or lang is None:
                continue
            mtime = _epoch(row.get("last", {}).get("value"))
            if force:
                inforce = row.get("force", {}).get("value")
                listed.append([celex, lang, mtime, inforce])
                history = place(celex, lang, inforce)
            else:
                listed.append([celex, lang, mtime])
                history = False
            yield RemoteFile(rel_path=rel_path(lang, celex, history), mtime=mtime, remote=encode(celex, lang))
        got += len(rows)
    if got != want:
        # a Virtuoso answer cut short says nothing: the window is
        # re-listed next pass (worded for engine.transient)
        raise TransportError(f"cellar: the {year} window answered {got} of the {want} rows its "
                             f"count names (connection timed out, the answer cut)")
    cellar_listing.keep(base, year, languages, want, latest, listed, force=force, at=counted_at)


# ---- one unit ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Fetched:
    files: dict[str, int]          # name → bytes
    moved: int
    #: bytes of items read from the Data Dump instead of asked for (datadump.py)
    from_dump: int = 0
    #: where the work's tree came from: `cellar`, `kept` (a unit of another
    #: language fetched it), `dump` (the Data Dump's MTD) — work_trees.py
    tree_from: str = "cellar"
    #: seconds asking Cellar for the tree, and for the items (the report
    #: says which dominates: whether trees from the dump would pay)
    tree_s: float = 0.0
    items_s: float = 0.0


def resource_url(base: str, celex: str) -> str:
    return base.rstrip("/") + RESOURCE_PATH + quote(celex, safe="")


def items_from_choices(body: bytes) -> list[tuple[str, str | None]]:
    """The 300 Multiple Choices' items: `(item URI, its name or None)` for
    each `…/DOC_<n>` it links, in its order — names read from the link's
    text where it is a file name."""
    text = body.decode("utf-8", errors="replace")
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for m in _LINK.finditer(text):
        href = m["href"]
        name = _TAGS.sub("", m["text"]).strip()
        if href not in seen:
            seen.add(href)
            out.append((href, name if _NAME.match(name or "") else None))
    for m in _HREF.finditer(text):
        if m["href"] not in seen:
            seen.add(m["href"])
            out.append((m["href"], None))
    return out


#: the manifestations a unit's text is taken from, best first (invariant 11;
#: München, 2026-11-14, every Italian work in force: Formex for 30,249 of
#: 43,623; 12,360 more in XHTML and HTML, 786 in HTML alone, 228 in PDF alone).
#: Where Formex exists it holds the most (formulas as text, figures as items,
#: the amendments' markup); where it does not, the next form is the origin's
#: whole text of the act and is taken, never the act left out
FORMS = ("fmx4", "xhtml", "html", "pdfa2a", "pdfa1a", "pdfa1b", "pdfx", "pdf")
#: the non-inferred tree (rdf.py): the assertions the branch notice lacks
RDF_ACCEPT = "application/rdf+xml;notice=non-inferred-tree"


def next_form(note, after: str = "fmx4") -> str | None:
    """The next form after `after` (FORMS order) the notice lists items for,
    each named by a plain file name: where Formex holds no wrapper of THIS
    act (formex.own_wrapper — München, 2026-11-16: five works whose Formex
    names no NO.CELEX, a mistyped one, or only other acts'), its text is
    read from it. Formex's items still land: the reason stays verifiable."""
    i = FORMS.index(after)
    return next((f for f in FORMS[i + 1:] if note.items.get(f)
                 and all(it.name and _NAME.match(it.name) for it in note.items[f])), None)


def form_of(note) -> str | None:
    """The best manifestation the notice lists items for (FORMS). A Formex
    manifestation whose named items include no wrapper (`….doc.xml`) gives
    way to the next form the notice lists: its body is found by the wrapper's
    DOC.MAIN.PUB, and without one the unit was refused ("holds 0 Formex
    wrappers"; 82 Italian consolidations of 1976–79, München 2026-11-15).
    Where it is the only form, or an item is unnamed (the 300 Multiple
    Choices path), it stays Formex as before. A PACKAGE — the manifestation
    served as one zip (`formex.unpack`: 32024R1689, every act of the OJ's
    act-by-act publication) — is Formex: its wrapper is inside, and skipping
    it read those acts from XHTML (2026-11-19)."""
    from ..plugins.eurlex import formex
    listed = [f for f in FORMS if note.items.get(f)]
    for f in listed:
        if f == "fmx4" and len(listed) > 1:
            items = note.items["fmx4"]
            if all(it.name for it in items) and not any(formex.is_wrapper(it.name) or formex.is_package(it.name)
                                                        for it in items):
                continue
        return f
    return None


def lacks_package(unit: Path, note) -> bool:
    """Whether the held unit lacks the Formex package (formex.unpack: the
    manifestation served as one zip) that `note` — its own tree's — lists as
    the form to read: a unit landed while `form_of` read such a manifestation
    as XHTML (2026-11-19, 32024R1689), to be re-formed."""
    return bool(lacked_packages(unit, note))


def lacked_packages(unit: Path, note) -> list[str]:
    """The names of the Formex packages `lacks_package` finds missing, in
    the tree's order: what the dump build records for a unit to re-form, so
    the per-unit pass can see by a stat whether it is held since
    (eurlex_dump.incomplete_due)."""
    from ..plugins.eurlex import formex
    if form_of(note) != "fmx4":
        return []
    return [it.name for it in note.items.get("fmx4", [])
            if it.name and formex.is_package(it.name) and not (Path(unit) / it.name).is_file()]


def _current_unit(dest: Path, note_now_mtime: int | None, form: str | None):
    """A unit already on disk that is still the origin's: its notice's own
    last modification is at or after the listing's, and every item the notice
    lists for the chosen form is there with the notice's size (the same check
    a fetch makes). Returns `(notice bytes, {name: bytes})` or None."""
    from ..plugins.eurlex import notice as notice_mod
    path = dest / NOTICE
    if note_now_mtime is None or not path.is_file():
        return None
    try:
        data = path.read_bytes()
        note = notice_mod.parse(data)
    except (OSError, notice_mod.NoticeError):
        return None
    held = _epoch(note.modified or note.last_modified)
    form = form or form_of(note)
    if held is None or held < note_now_mtime or form is None:
        return None
    files: dict[str, bytes] = {}
    for it in note.items.get(form, []):
        f = dest / (it.name or "")
        if not it.name or not f.is_file() or (it.size is not None and f.stat().st_size != it.size):
            return None
        files[it.name] = f.read_bytes()
    return (data, files) if files else None


def fetch_unit(base: str, celex: str, lang: str, dest: Path, *,
               client: httpx.Client | None = None, listed_mtime: int | None = None,
               charge: Callable[[int], None] = budget.acquire_bytes, dump=None,
               trees=None) -> Fetched:
    """The unit of (CELEX, language) into `dest` (the unit's directory),
    whole or not at all: the branch notice, the items of the best form the
    notice lists (FORMS), and the non-inferred tree scoped to the language
    (`tree.json`, rdf.py), which is written LAST and is the unit's completion
    mark. A unit already on disk whose notice is as recent as the listing
    says (`listed_mtime`) and whose items match the notice keeps its bytes:
    only what it lacks moves (a unit fetched before the tree was kept gains
    its tree alone). `dump` (a `datadump.Dump` of this language): an item it
    may give (datadump.py's rule, on the tree Cellar serves now, asked FIRST
    for that) is read from it instead of asked for; never passed by
    `records-vs-origin`, which re-fetches from Cellar. `trees` (a
    `work_trees.WorkTrees`): the WORK's tree, asked once for every language
    and taken from it, or from the dump's MTD, where the unit's notice dates
    it current; never passed by `records-vs-origin` either."""
    from ..plugins.eurlex import formex, notice as notice_mod, rdf
    from ..plugins.eurlex.source import LANGUAGES
    code = LANGUAGES[lang]
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S)
    moved = 0
    from_dump = 0

    def count(n: int) -> None:
        nonlocal moved
        moved += n
        charge(n)
    try:
        url = resource_url(base, celex)
        accept_lang = {"Accept-Language": code.lower()}
        kept = _current_unit(dest, listed_mtime, None)
        if kept is not None:
            notice_bytes, files = kept
            note = notice_mod.parse(notice_bytes)
        else:
            r = _get(client, url, _headers({"Accept": "application/xml;notice=branch", **accept_lang}),
                     f"the notice of {celex} ({code})", charge=count)
            notice_bytes = r.content
            try:
                note = notice_mod.parse(notice_bytes)
            except notice_mod.NoticeError as e:
                raise TransportError(f"cellar: the notice of {celex} ({code}) is not one: {e}") from None
            files = {}
        if note.celex != celex:
            raise TransportError(f"cellar: asked for {celex}, the notice is {note.celex}'s")
        if note.display_language and note.display_language != code:
            raise TransportError(f"cellar: asked for {celex} in {code}, the notice is in {note.display_language}")
        form = form_of(note)

        def ask_tree() -> dict:
            _stop_check()
            return _read_tree(_tree(client, url, celex, count), celex)
        # the work's tree FIRST: the dump's items are judged on its dates
        if trees is not None:
            full, tree_from = trees.tree_for(_work_of(note), note.dated, ask_tree)
        else:
            full, tree_from = ask_tree(), "cellar"
        if not files:
            listed = [(it.uri, it.name, it.size) for it in note.items.get(form, [])] if form else []
            if not listed:
                _stop_check()
                r = _get(client, url, _headers({"Accept": "application/xml;type=fmx4", **accept_lang}),
                         f"the fmx4 manifestation of {celex} ({code})", charge=count, ok=(300, 200))
                listed = [(uri, name, None) for uri, name in items_from_choices(r.content)]
                form = "fmx4" if listed else form
            if not listed:
                raise TransportError(f"cellar: {celex} ({code}) lists no item in any of {', '.join(FORMS)}")
            for uri, name, size in listed:
                _stop_check()
                taken = dump.take(uri, form, name, size, full) if dump is not None else None
                if taken is not None and name and _NAME.match(name) and name not in (NOTICE, rdf.TREE_FILE) \
                        and name not in files and len(taken) == size:
                    files[name] = taken
                    from_dump += len(taken)
                    continue
                r = _get(client, uri, _headers(accept_lang), f"{name or uri} of {celex} ({code})", charge=count)
                if not name:
                    m = _DISPOSITION.search(r.headers.get("content-disposition", ""))
                    name = m["name"].strip() if m else None
                if not name or not _NAME.match(name) or name in (NOTICE, rdf.TREE_FILE):
                    raise TransportError(f"cellar: {uri} of {celex} ({code}) names no plain file ({name!r})")
                if name in files:
                    raise TransportError(f"cellar: two items of {celex} ({code}) named {name}")
                if size is not None and size != len(r.content):
                    raise TransportError(f"cellar: {name} of {celex} ({code}) is {len(r.content)} bytes, "
                                         f"the notice says {size}")
                files[name] = r.content
        if form == "fmx4":
            try:
                view = formex.unpack(files)[0]          # a package read through
                formex.own_wrapper(view, celex, view.__getitem__)
            except formex.FormexError as e:
                raise TransportError(f"cellar: {celex} ({code}): {e}") from None
            # a text in another language under this one's expression lands as
            # the origin files it; the parser labels it (operator, 2026-11-16, B)
        tree = rdf.scope(full, code)
        files[NOTICE] = notice_bytes
        files[rdf.TREE_FILE] = rdf.dumps(tree)
        _land(dest, files)
        return Fetched(files={n: len(b) for n, b in files.items()}, moved=moved, from_dump=from_dump,
                       tree_from=tree_from)
    except httpx.HTTPError as e:
        raise TransportError(f"cellar: {type(e).__name__}: {e}") from e
    finally:
        if own:
            client.close()


def fetch_unit_from_tree(base: str, celex: str, lang: str, dest: Path, *,
                         client: httpx.Client | None = None,
                         charge: Callable[[int], None] = budget.acquire_bytes,
                         trees=None, listed_mtime: int | None = None,
                         bulk: Callable[[str], dict | None] | None = None,
                         bulk_not_before: int | None = None) -> Fetched:
    """A PARSER-4 unit of (CELEX, language) into `dest`, whole or not at all:
    the work's non-inferred tree scoped to the language (`tree.json`) and the
    items of the best form the TREE lists (`form_of` on `from_tree.notice`) —
    no notice (app/plugins/eurlex/from_tree.py: the tree holds every
    assertion about the act). One request for the tree and one per item. The
    unit the Data Dump builds (eurlex_dump.py) is this one, byte for byte:
    what `records-vs-origin` re-fetches to check it. `listed_mtime`: the
    listing's date of the unit; a tree `trees` kept serves it only where
    that tree dates the unit at least as late (review 2026-11-21, Q1).
    `bulk` (work_trees.BulkTrees): the work's tree from the Data Dump's MTD
    by CELEX, taken FIRST where it dates the unit at least `bulk_not_before`
    (bulk first, 2026-11-26: the tree landed is byte for byte the one Cellar
    serves, München's proof on 20 units); never passed by `records-vs-origin`.
    An item the bulk's tree names that Cellar does not serve as named sends
    the unit back to Cellar's own tree, once, before it counts as failed: the
    two are then not of one moment."""
    from ..plugins.eurlex import formex, from_tree, rdf
    from ..plugins.eurlex.source import LANGUAGES
    code = LANGUAGES[lang]
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S)
    moved = 0

    def count(n: int) -> None:
        nonlocal moved
        moved += n
        charge(n)
    #: the tree's parse, once per bytes (a kept tree is judged on it, then used)
    parsed: dict[str, Any] = {}

    def parse(data: bytes):
        if parsed.get("data") is not data:
            scoped = rdf.scope(_read_tree(data, celex), code)
            try:
                note = from_tree.notice(scoped, code, celex)
            except from_tree.TreeError as e:
                raise TransportError(f"cellar: the tree of {celex}: {e}") from None
            parsed.update(data=data, scoped=scoped, note=note)
        return parsed["scoped"], parsed["note"]

    def current(data: bytes) -> bool:
        """A KEPT TREE serves this unit only where its own latest
        modification of the unit (the work's, the expression's and the
        manifestations' — `Notice.modified`, as TreeUnits.fetch reads a held
        one) is at least the listing's: the engine records the listing's
        date as fetched, so a tree older than it would stand as current for
        good. Where the listing states no date (the dump's incomplete list),
        the window alone, as before."""
        if listed_mtime is None:
            return True
        try:
            note = parse(data)[1]
        except TransportError:
            return False
        held = _epoch(note.modified or note.last_modified)
        return held is not None and held >= listed_mtime
    try:
        url = resource_url(base, celex)
        t0 = time.monotonic()
        # the tree is the WORK's, every language's in it (no Accept-Language):
        # `trees` (work_trees.RecentTrees) serves one Cellar served for the
        # work's other languages within the pass
        taken = _bulk_tree(bulk, celex, code, bulk_not_before)
        kept = trees.peek(celex) if taken is not None and hasattr(trees, "peek") else None
        if kept is not None:
            # a tree Cellar served this pass that dates the unit LATER than
            # the bulk's wins, with no request (review of 9870a240)
            try:
                k_note = parse(kept)[1]
            except TransportError:
                k_note = None
            k_when = _epoch(k_note.modified or k_note.last_modified) if k_note is not None else None
            b_when = _epoch(taken[1].modified or taken[1].last_modified)
            if k_when is not None and (b_when is None or k_when > b_when):
                taken, data, tree_from = None, kept, "kept"
        if taken is not None:
            (scoped, note), tree_from = taken, "dump"
        elif kept is not None and tree_from == "kept":
            scoped, note = parse(data)
        else:
            if trees is not None:
                data, tree_from = trees.get(celex, lambda: _tree(client, url, celex, count), current)
            else:
                data, tree_from = _tree(client, url, celex, count), "cellar"
            scoped, note = parse(data)
        tree_s = time.monotonic() - t0 if tree_from == "cellar" else 0.0
        try:
            form = form_of(note)
            listed = note.items.get(form) or [] if form else []
            if not listed or any(not it.name or not _NAME.match(it.name) for it in listed):
                raise TransportError(f"cellar: the tree of {celex} ({code}) names no item of any of "
                                     f"{', '.join(FORMS)} by a plain file name")
            files: dict[str, bytes] = {}
            t0 = time.monotonic()
            for it in listed:
                _stop_check()
                if it.name in files or it.name in (NOTICE, rdf.TREE_FILE):
                    raise TransportError(f"cellar: two items of {celex} ({code}) named {it.name}")
                files[it.name] = _get(client, it.uri, _headers({"Accept-Language": code.lower()}),
                                      f"{it.name} of {celex} ({code})", charge=count).content
            if form == "fmx4":
                try:
                    view = formex.unpack(files)[0]          # a package read through
                    formex.own_wrapper(view, celex, view.__getitem__)
                except formex.FormexError as e:
                    # no wrapper of THIS act: the next form the tree lists is its
                    # text, beside the Formex items (`next_form`)
                    alt = next_form(note)
                    if alt is None:
                        raise TransportError(f"cellar: {celex} ({code}): {e}") from None
                    for it in note.items[alt]:
                        _stop_check()
                        if it.name in files or it.name in (NOTICE, rdf.TREE_FILE):
                            raise TransportError(f"cellar: two items of {celex} ({code}) named {it.name}")
                        files[it.name] = _get(client, it.uri, _headers({"Accept-Language": code.lower()}),
                                              f"{it.name} of {celex} ({code})", charge=count).content
                # a text in another language under this one's expression lands as
                # the origin files it; the parser labels it (operator, 2026-11-16, B)
        except TransportError as e:
            if tree_from != "dump" or isinstance(e, TransferAborted) or _episode(e):
                raise                       # the endpoint going away says nothing of the bulk's tree
            # the bulk's tree and what Cellar serves now are not of one
            # moment: the unit is asked again with Cellar's own tree, once
            return fetch_unit_from_tree(base, celex, lang, dest, client=client, charge=charge, trees=trees,
                                        listed_mtime=listed_mtime)
        items_s = time.monotonic() - t0
        files[rdf.TREE_FILE] = rdf.dumps(scoped)
        _land(dest, files)
        return Fetched(files={n: len(b) for n, b in files.items()}, moved=moved, tree_from=tree_from,
                       tree_s=tree_s, items_s=items_s)
    except httpx.HTTPError as e:
        raise TransportError(f"cellar: {type(e).__name__}: {e}") from e
    finally:
        if own:
            client.close()


def _say_cellar(name: str) -> Callable[[str], None]:
    """A log line naming the collection (its corpus's name, as TreeInventory's)."""
    return lambda message: print(f"{time.strftime('%H:%M:%S')} [{name} info:cellar] {message}", flush=True)


#: the listing phase's windows at once, at most (the endpoint's etiquette
#: decides below it: TransferManager.endpoint_slots)
LISTING_SLOTS = 4
#: the listing phase's own endpoint key: its episodes cool the listing
#: threads (review of a69741dd, S2), never the fetch threads (Z2)
LISTING_KEY = "cellar-listing"
#: languages a listing-phase window starts with (see `_prepare_bulk`)
LISTING_START_GROUP = 1


def _took(learned, what: str, t0: float) -> None:
    """Each listing query's time, said where the caller asked for it (the
    listing phase: architecture, 2026-11-26, "measure first": whether a page
    costs a whole evaluation of its window)."""
    say = getattr(learned, "say", None)
    if say is not None:
        say(f"cellar: {what} in {time.monotonic() - t0:.1f} s")


class _Learned(dict):
    """`_window_files`' `learned`, shared by the listing phase's threads: the
    group only ever narrows, under a lock (a halving one thread learned is
    never undone by another's write)."""

    def __init__(self, group: int, say=None) -> None:
        super().__init__(group=max(1, group))
        import threading
        self._guard = threading.Lock()
        self.say = say

    @property
    def group(self) -> int:
        return max(1, int(self["group"]))

    def __setitem__(self, k, v) -> None:
        with self._guard:
            super().__setitem__(k, min(int(v), int(self.get(k, v))) if k == "group" else v)


#: a covered window's fingerprint is asked again after this (once a day)
WINDOW_REFRESH_S = 24 * 3600.0
#: a window whose re-ask failed is not asked again before this, nor before
#: the next prepare (review of 4076ab4c, Z1: 50 units, 50 counts of 900 s)
WINDOW_FAILED_HOLD_S = 15 * 60.0


class WindowNotListed(Waiting):
    """A unit asked first whose listing window is not known whole since the
    dump was cut: it WAITS. Nothing is asked of Cellar; the engine records
    nothing (spec.Waiting); TreeUnits.fetch marks it waiting in the dump's
    ledger (`eurlex_dump.mark_waiting`, never tried), so it is due again
    after `engine.retry_wait_s(n)` of its own count — 60 s up to 2 h — and
    the count restarts from 0 once a pass lists its window (`mark_listed`);
    a unit inside its wait is reached only when the wait ends."""


def window_year(celex: str) -> int | None:
    """The CELEX year of the listing window a CELEX falls in (`_window`:
    sector 3 acts and 0 consolidations), or None."""
    m = re.match(r"^[03](\d{4})", celex)
    return int(m.group(1)) if m else None


def _bulk_tree(bulk, celex: str, code: str, listed_mtime: int | None):
    """(scoped tree, notice) of the unit from the bulk's tree, or None: no
    bulk, the MTD lacks the work, its tree does not read as the unit's, or it
    dates the unit before the listing does (the rule a kept tree meets)."""
    from ..plugins.eurlex import from_tree, rdf
    full = bulk(celex) if bulk is not None else None
    if full is None:
        return None
    scoped = rdf.scope(full, code)
    try:
        note = from_tree.notice(scoped, code, celex)
    except from_tree.TreeError:
        return None
    if listed_mtime is not None:
        held = _epoch(note.modified or note.last_modified)
        if held is None or held < listed_mtime:
            return None
    return scoped, note


def _work_of(note) -> str | None:
    """The work's cellar uuid, from the notice's dated WORK URI."""
    from . import datadump, work_trees
    return next((datadump.work_uuid(u) for u in note.dated if work_trees._depth(u) == 0), None)


def _tree(client: httpx.Client, url: str, celex: str, count) -> bytes:
    return _get(client, url, _headers({"Accept": RDF_ACCEPT}), f"the non-inferred tree of {celex}",
                charge=count).content


def _read_tree(data: bytes, celex: str) -> dict:
    from ..plugins.eurlex import rdf
    try:
        return rdf.read(data)
    except rdf.RdfError as e:
        raise TransportError(f"cellar: the non-inferred tree of {celex} is not Cellar's form: {e}") from None


def _land(dest: Path, files: dict[str, bytes]) -> None:
    """Every file into a staging directory beside `dest`, fsynced, the
    notice last; then the swap. A crash leaves either the old unit or the
    new one, and at most a staging directory the next fetch clears."""
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{dest.name}.staging"
    old = parent / f".{dest.name}.old"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    staging.mkdir()
    # the tree last: it is the completion mark (fetch_unit); the notice before it
    for name in sorted(files, key=lambda n: (n == TREE_MARK, n == NOTICE, n)):
        with open(staging / name, "wb") as f:
            f.write(files[name])
            f.flush()
            os.fsync(f.fileno())
    if dest.exists():
        os.replace(dest, old)
    os.replace(staging, dest)
    shutil.rmtree(old, ignore_errors=True)


def _syncfs(path: Path) -> None:
    """Every dirty page of the filesystem holding `path` to disk: Linux's
    syncfs(2) through libc; elsewhere sync(2)."""
    import ctypes
    import ctypes.util
    fd = os.open(path, os.O_RDONLY)
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
        if getattr(libc, "syncfs", None) is None or libc.syncfs(fd) != 0:
            os.sync()
    except OSError:
        os.sync()
    finally:
        os.close(fd)


def land_many(root: Path, units: list[tuple[Path, dict[str, bytes]]]) -> None:
    """`_land` for a batch, by GROUP COMMIT (München, 2026-11-16, measured:
    EUR-Lex4's dump build had its pool's children in `_land`, the tank's
    disks 40-50 % busy at ~32 fsyncs/s, 30 of 297 chunks in 54 min — one
    fsync per FILE of ~900,000 units on spinning disks). Every unit's files
    into its staging directory unsynced; ONE syncfs; then the swaps; ONE
    syncfs more. The same guarantee as `_land`: no unit becomes visible at
    its path before its bytes are on disk, and a crash leaves the old unit
    or the new, and staging directories the next build clears. The standard
    form is the database's group commit (one log flush for many
    transactions); `_land` stays for a single unit."""
    if not units:
        return
    staged: list[tuple[Path, Path, Path]] = []
    for dest, files in units:
        parent = dest.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = parent / f".{dest.name}.staging"
        old = parent / f".{dest.name}.old"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
        staging.mkdir()
        for name in sorted(files, key=lambda n: (n == TREE_MARK, n == NOTICE, n)):
            (staging / name).write_bytes(files[name])
        staged.append((dest, staging, old))
    _syncfs(root)                      # every staged byte durable before any becomes visible
    for dest, staging, old in staged:
        if dest.exists():
            os.replace(dest, old)
        os.replace(staging, dest)
    _syncfs(root)                      # the swaps durable
    for _dest, _staging, old in staged:
        shutil.rmtree(old, ignore_errors=True)


# ---- the two extension points the engine reads (spec.py: Inventory, UnitFetcher) ----

class CellarInventory:
    """Cellar's `Inventory`: `listing` above, for the profile's languages."""

    built_on_request = False

    def held_back(self, collection) -> str | None:
        """BULK FIRST, NO EXCEPTIONS (CLAUDE.md; operator, 2026-11-15, B): a
        collection on parser 4 asks Cellar nothing per unit until the Data
        Dump of every language it holds has been built into it
        (eurlex_dump.py, its stamp); then only what the dump lacks."""
        if not _tree_mode(collection):
            return None
        from . import eurlex_dump
        langs = held_languages(collection)
        stamped = eurlex_dump.stamp(Path(collection.corpus_dir))
        missing = [l for l in langs if l not in stamped]
        if missing:
            return (f"{getattr(collection, 'name', 'eurlex')}: the Data Dump of {', '.join(missing)} is not "
                    f"built into it yet (louise dev eurlex-dump); bulk first, so Cellar is not asked")
        return None

    def for_collection(self, spec: AcquisitionSpec, collection) -> AcquisitionSpec:
        """The spec a collection fetches by (engine.effective_spec): on
        parser 4, the listing that places each unit (`TreeInventory`) and
        the fetch of the tree and the items alone (`TREE_UNITS`)."""
        if not _tree_mode(collection):
            return spec
        from dataclasses import replace
        return replace(spec, inventory=TreeInventory(Path(collection.corpus_dir), collection.name),
                       unit_fetcher=TREE_UNITS)

    def list(self, endpoint: Endpoint, spec: AcquisitionSpec, profile_filter: Filter):
        return listing(endpoint, spec, profile_filter)


class CellarUnits:
    """Cellar's `UnitFetcher`: a unit is (CELEX, language), its completion
    mark the notice the listing names; fetched by `fetch_unit`, whole. It
    counts what each unit took from where (`reports`)."""

    def __init__(self) -> None:
        import threading
        from collections import Counter
        self._lock = threading.Lock()
        self._counts: Counter = Counter()
        self._since = time.monotonic()
        self._bulk: dict[str, dict] = {}            # corpus root → {lang: work_trees.BulkTrees}
        self._dates: dict[str, dict] = {}           # corpus root → {(celex, lang): listed mtime}
        self._cover: dict[str, dict] = {}           # corpus root → {(year, lang): at}, known whole since the dump
        self._after: dict[str, dict] = {}           # corpus root → {lang: the stamped dump's created, epoch}
        self._due: dict[str, set] = {}              # corpus root → the units asked first this pass
        self._base_url: dict[str, str | None] = {}  # corpus root → Cellar's base, for a window asked again
        self._window_locks: dict[tuple, Any] = {}   # (root, year, lang) → one refresher at a time
        self._window_failed: dict[tuple, float] = {}  # (root, year, lang) → when its re-ask failed

    def _count(self, got: "Fetched") -> None:
        with self._lock:
            self._counts["units"] += 1
            self._counts[f"tree:{got.tree_from}"] += 1
            self._counts["bytes_moved"] += got.moved
            self._counts["bytes_from_dump"] += got.from_dump
            self._counts["tree_ms"] += int(got.tree_s * 1000)
            self._counts["items_ms"] += int(got.items_s * 1000)

    def reports(self) -> list[str]:
        """What the units since the last report took from where: their
        number and rate, their trees (Cellar, kept from another language's
        unit, the Data Dump's MTD), the bytes moved and the bytes the dump
        gave. Nothing when no unit came."""
        with self._lock:
            c, self._counts = self._counts, type(self._counts)()
            elapsed, self._since = time.monotonic() - self._since, time.monotonic()
        if not c["units"]:
            return []
        rate = c["units"] / elapsed * 3600 if elapsed > 0 else 0.0
        return [f"cellar: {c['units']} units in {elapsed / 60:.0f} min ({rate:.0f}/h); trees "
                f"{c['tree:cellar']} from Cellar, {c['tree:kept']} kept, {c['tree:dump']} from the dump"
                + (f", {c['tree:held']} held and current" if c["tree:held"] else "") + "; "
                f"{c['bytes_moved'] >> 20} MiB moved, {c['bytes_from_dump'] >> 20} MiB from the dump"
                + (f"; asking: trees {c['tree_ms'] / 1000:.0f} s, items {c['items_ms'] / 1000:.0f} s"
                   if c["tree_ms"] or c["items_ms"] else "")]

    def handles(self, remote: RemoteFile) -> bool:
        return is_unit(remote)

    def fetch(self, endpoint: Endpoint, remote: RemoteFile, dest: Path) -> int:
        from . import datadump, work_trees
        unit = unit_of(remote)
        # dest is the unit's completion mark: <root>/lang=<l>/<yyyy>/<CELEX>/tree.json
        got = fetch_unit(endpoint.url, unit["celex"], unit["lang"], dest.parent,
                         listed_mtime=remote.mtime, dump=datadump.dumps().for_language(unit["lang"]),
                         trees=work_trees.store_for(dest.parents[3]))
        self._count(got)
        return got.moved

    def refetch(self, spec: AcquisitionSpec, locator: str, base: Path, *,
                parser: int | None = None) -> tuple[Path, int]:
        """records-vs-origin: a record's `origin` is its unit
        (`lang=<code>/<yyyy>/<CELEX>`, parser 4 also `history/lang=…`), fetched
        again under `base` at the same relative path, so the plugin reads it as
        it reads the corpus. `parser`: the Codex's — before 4 a unit holds the
        branch notice (`fetch_unit`), from 4 the tree and the items alone
        (`fetch_unit_from_tree`, the unit the Data Dump builds)."""
        rel = str(locator).partition("#")[0].strip("/")
        out = base / rel
        if out.is_dir():
            return out, 0
        parts = rel.split("/")
        if parts and parts[0] == "history" and (parser is None or parser >= 4):
            parts = parts[1:]
        if len(parts) != 3 or not parts[0].startswith("lang="):
            raise FileNotFoundError(f"{rel}: not a Cellar unit (lang=<code>/<yyyy>/<CELEX>)")
        lang, celex = parts[0].partition("=")[2], parts[2]
        fetch = fetch_unit if parser is not None and parser < 4 else fetch_unit_from_tree
        for endpoint in spec.endpoints:
            try:
                got = fetch(endpoint.url, celex, lang, out)
            except TransportError:
                continue
            return out, got.moved
        raise FileNotFoundError(f"{rel}: no endpoint of {spec.source_id} served it")


def held_languages(collection) -> list[str]:
    """The languages a collection's file holds, in the base or the history
    (`slices`; every language where it names none). Not slice_scope.held,
    which is None for an origin that divides by slice itself."""
    from .. import slice_scope
    from ..plugins.eurlex.source import LANGUAGES
    slices = getattr(collection, "slices", None)
    if slices is None or slices == "all":
        return sorted(LANGUAGES)
    held = tuple(str(s) for s in slices)
    return [l for l in sorted(LANGUAGES) if any(
        slice_scope.is_held(f"{p}lang={l}/type=R/year=2000", held) for p in ("", "history/"))]


def _tree_mode(collection) -> bool:
    version = getattr(getattr(getattr(collection, "codex", None), "parser", None), "version", None)
    return version is not None and int(version) >= 4


class TreeInventory(CellarInventory):
    """Parser 4's listing (eurlex_dump.py): each unit at the place it is
    held — the base or the history slice, fixed when first held — and a unit
    not held yet placed by its work's in-force (`is_history`)."""

    def __init__(self, corpus: Path, name: str | None = None) -> None:
        self.corpus = Path(corpus)
        self.name = name or self.corpus.name

    def for_collection(self, spec, collection):
        return spec

    def _say(self, message: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} [{self.name} info:cellar] {message}", flush=True)

    def reports(self) -> list[str]:
        """What the engine says as warnings of the collection after the
        listing (spec.Inventory `reports`): each Data Dump folder whose
        datadump.json does not read, once a day (eurlex_dump.unread_statements;
        coordinator, 2026-11-21: never skipped silently). Nothing is held
        back for it."""
        from . import datadump, eurlex_dump
        base = datadump.dumps().base
        return eurlex_dump.unread_statements(base, self.corpus) if base is not None else []

    def _place(self, celex: str, lang: str, in_force: str | None) -> bool:
        from ..plugins.eurlex.source import HISTORY, unit_rel
        rel = unit_rel(lang, celex)
        if (self.corpus / rel / TREE_MARK).is_file():
            return False
        if (self.corpus / HISTORY / rel / TREE_MARK).is_file():
            return True
        return is_history(celex, in_force)

    def list(self, endpoint: Endpoint, spec: AcquisitionSpec, profile_filter: Filter):
        """WHAT THE BULK LACKS, AS THE BULK STATES IT, FIRST: the units the
        Data Dump build recorded as listed and not held whole
        (eurlex_dump.INCOMPLETE), not held now and not tried — those of the
        profile's slices — with no listing at all; the complete listing once
        none is left, or once every one left waits out an episode (Q2:
        eurlex_dump.incomplete_due). A short listing is safe: nothing reads a unit absent
        from it as gone from the origin (engine.py). Bulk first holds: the
        record exists only after the dump was used whole (`held_back`)."""
        from . import eurlex_dump, engine
        due = []
        for lang, celex, inf in eurlex_dump.incomplete_due(self.corpus, languages_of(profile_filter)):
            f = RemoteFile(rel_path=rel_path(lang, celex, self._place(celex, lang, inf)), mtime=None,
                           remote=encode(celex, lang))
            if engine._apply_filters([f], spec.filters, profile_filter):
                due.append(f)
        if due:
            self._say(f"the {len(due)} unit(s) the Data Dump lacks whole, asked first, without a listing")
            return due
        return listing(endpoint, spec, profile_filter, place=self._place, say=self._say)


class TreeUnits(CellarUnits):
    """Parser 4's `UnitFetcher`. A unit held whose tree is as recent as the
    listing says is answered without a request (the Data Dump built it, or an
    earlier pass); any other unit is fetched from Cellar — the tree, then the
    items it names (`fetch_unit_from_tree`) — into the place the listing
    names. Before the pass, a Data Dump newer than the one built into the
    collection is built first (`prepare`: bulk first)."""

    def fetch(self, endpoint: Endpoint, remote: RemoteFile, dest: Path) -> int:
        from ..plugins.eurlex import from_tree, rdf
        from ..plugins.eurlex.source import LANGUAGES
        unit = unit_of(remote)
        if dest.is_file() and remote.mtime is not None:
            try:
                note = from_tree.notice(rdf.loads(dest.read_bytes()), LANGUAGES[unit["lang"]], unit["celex"])
                held = _epoch(note.modified or note.last_modified)
            except (OSError, ValueError, from_tree.TreeError):
                held = None
            if held is not None and held >= remote.mtime and not self._reform(dest, remote, note):
                self._count(Fetched(files={}, moved=0, tree_from="held"))
                return 0
        root = _corpus_of(dest, remote)
        from . import work_trees
        try:
            bulk, not_before = self._bulk_of(root, unit, remote, dest)
        except WindowNotListed as e:
            # it waits (nothing asked); marked so the asked-first list lets the
            # complete listing have its turn (review of 13743848, 3)
            if root is not None and remote.mtime is None:
                from . import eurlex_dump
                eurlex_dump.mark_waiting(root, unit["lang"], unit["celex"], str(e))
            raise
        try:
            got = fetch_unit_from_tree(endpoint.url, unit["celex"], unit["lang"], dest.parent,
                                       trees=work_trees.recent_for(root) if root is not None else None,
                                       listed_mtime=remote.mtime if remote.mtime is not None else not_before,
                                       bulk=bulk, bulk_not_before=not_before)
        except TransportError as e:
            # a unit the dump lacked that Cellar does not give either: not
            # asked first again (eurlex_dump.mark_tried), so the complete
            # listing has its turn; the endpoint going away is no verdict,
            # but a unit asked first waits out its episode before it is due
            # again (eurlex_dump.mark_deferred; review 2026-11-21, Q2: one
            # such unit kept the complete listing from ever running). A stop
            # is neither
            from . import eurlex_dump
            from ..transfer.bucket import TransferAborted as _Aborted
            if root is not None and not isinstance(e, (TransferAborted, _Aborted)):
                if not _episode(e):
                    eurlex_dump.mark_tried(root, unit["lang"], unit["celex"], str(e))
                elif remote.mtime is None:
                    eurlex_dump.mark_deferred(root, unit["lang"], unit["celex"], str(e))
            raise
        if root is not None:
            work_trees.record_source(root, unit["lang"], unit["celex"], got.tree_from,
                                     getattr(bulk, "created", None))
        if remote.mtime is None and root is not None:
            from . import eurlex_dump
            eurlex_dump.mark_fetched(root, unit["lang"], unit["celex"])   # asked first once
        self._count(got)
        return got.moved

    def _bulk_of(self, root: Path | None, unit: dict, remote: RemoteFile, dest: Path):
        """The bulk's tree source for this unit and the date it must reach
        (architecture's decision, 2026-11-26):
          - a unit HELD already: none (review of 1be767f6: the dump build
            leaves to the API exactly the held units newer than the dump);
          - a unit with a listing date: the bulk, not older than it;
          - a unit asked first, its window known whole since the dump: the
            bulk where the listing shows it unchanged since `created`,
            Cellar's tree (or a newer kept one) where changed or absent;
          - its window not known whole since the dump: it WAITS
            (`WindowNotListed`), never taken from the bulk on silence;
          - a CELEX no window lists (another sector): Cellar's tree."""
        if root is None or dest.exists() or dest.parent.exists():
            return None, None
        key = str(root.resolve())
        with self._lock:
            bulk = (self._bulk.get(key) or {}).get(unit["lang"])
            dates = self._dates.get(key) or {}
            cover = self._cover.get(key) or {}
            after = (self._after.get(key) or {}).get(unit["lang"])
        if bulk is None:
            return None, None
        if remote.mtime is not None:
            return bulk, remote.mtime
        year = window_year(unit["celex"])
        if year is None or after is None:
            return None, None
        at = cover.get((year, unit["lang"]))
        if at is None:
            raise WindowNotListed(f"cellar: {unit['celex']} ({unit['lang']}): its {year} window is not "
                                  f"known whole since the dump was cut; nothing asked, it waits for the listing")
        when = dates.get((unit["celex"], unit["lang"]))
        if when is None:
            # absent from the listing, or listed with no modification date (58
            # of the node's 240,392 rows): nothing says it is unchanged since
            # the dump, so Cellar's tree — never the bulk's on silence
            return None, None
        if when > after:
            return None, when                   # changed since the dump: Cellar's tree, no older than this
        if time.time() - at > WINDOW_REFRESH_S:
            # THE BOUND, ENFORCED AT FETCH TIME, on the one path that takes the
            # bulk's tree (architecture's decisions on d863230c, Y3, and on
            # 6f6e72ab, 2): a window counted more than a day ago is asked
            # again HERE, once (its fingerprint, under its lock), and the unit
            # is judged on the fresh count; a window that cannot be asked
            # leaves the unit waiting
            self._refresh_window(key, year, unit["lang"])
            with self._lock:
                when = (self._dates.get(key) or {}).get((unit["celex"], unit["lang"]))
                at = (self._cover.get(key) or {}).get((year, unit["lang"]))
            if at is None or time.time() - at > WINDOW_REFRESH_S:
                raise WindowNotListed(f"cellar: {unit['celex']} ({unit['lang']}): its {year} window was known "
                                      f"whole more than a day ago and could not be asked again; it waits")
            if when is None:
                return None, None
            if when > after:
                return None, when
        return bulk, when

    def _refresh_window(self, key: str, year: int, lang: str) -> None:
        """The (year, language) window asked again — its fingerprint, its
        pages where it moved — by ONE fetch thread (a lock per window; the
        others wait for it and find it fresh), and `_cover`/`_dates` refreshed
        from what it now vouches. An episode leaves both as they were."""
        from . import cellar_listing
        from ..plugins.eurlex.source import LANGUAGES
        with self._lock:
            import threading
            lock = self._window_locks.setdefault((key, year, lang), threading.Lock())
            base_url = self._base_url.get(key)
        with lock:
            _stop_check()                               # a stop while queued on the lock asks nothing
            with self._lock:
                at = (self._cover.get(key) or {}).get((year, lang))
                due, after = set(self._due.get(key) or ()), dict(self._after.get(key) or {})
                failed = self._window_failed.get((key, year, lang))
            if base_url is None or (at is not None and time.time() - at <= WINDOW_REFRESH_S):
                return                                  # another thread refreshed it meanwhile
            if failed is not None and time.time() - failed < WINDOW_FAILED_HOLD_S:
                return                                  # failed a moment ago: its units wait, nothing asked
            code_of = {v: k for k, v in LANGUAGES.items()}
            try:
                # bound WITHOUT the endpoint's key, as the engine's listings are
                # (engine._listing_bound; review of 4076ab4c, Z2): a count that
                # times out must not cool Cellar for every fetch thread
                from ..transfer.manager import Direction, get_manager
                handle = current_handle()
                bound = get_manager().bind(handle.priority, Direction.DOWN, stop=handle.stop) \
                    if handle is not None else contextlib.nullcontext()
                with bound, httpx.Client(timeout=TIMEOUT_S) as client:
                    for _ in _window_files(client, base_url, year, (lang,), code_of, budget.acquire_bytes,
                                           place=lambda *a: False, learned={"group": 1}):
                        pass
            except TransferAborted:
                raise
            except (TransportError, OSError) as e:
                # an episode, a window that moved between its count and its
                # pages, or the listing not written (ENOSPC): remembered, so the
                # window's other units wait at once (Z1, Z5, Z3); said once
                with self._lock:
                    self._window_failed[(key, year, lang)] = time.time()
                _say_cellar(Path(key).name)(f"the {year} window ({lang}) was not asked again "
                                            f"({type(e).__name__}: {str(e)[:200]}): its units wait")
                return
            mine = {u for u in due if u[1] == lang and window_year(u[0]) == year}
            dates, cover = cellar_listing.coverage(mine, after, base_url)
            with self._lock:
                if (year, lang) in cover:
                    # the window's units REPLACED, not merged: one the fresh
                    # window lacks is absent, never kept at its old date (review
                    # of 556d3a15, N2); one new dict, set before the cover
                    known = {k: v for k, v in (self._dates.get(key) or {}).items() if k not in mine}
                    known.update(dates)
                    self._dates[key] = known
                    self._cover.setdefault(key, {})[(year, lang)] = cover[(year, lang)]

    def _reform(self, dest: Path, remote: RemoteFile, note) -> bool:
        """A held unit lacking the Formex package its tree lists is asked
        again, once the dump build has re-formed this language's units from
        the bulk (eurlex_dump.packages_read): what is still lacking then is
        what the bulk does not give (bulk first)."""
        from . import eurlex_dump
        if not lacks_package(dest.parent, note):
            return False
        root = _corpus_of(dest, remote)
        return root is not None and unit_of(remote)["lang"] in eurlex_dump.packages_read(root)

    def prepare(self, files: list[RemoteFile], pending: list[RemoteFile], collection,
                endpoint: Endpoint) -> list[str]:
        """A newer Data Dump built in before Cellar is asked (bulk first).
        A newer dump that does not read, or whose build fails for any other
        reason, HOLDS THE PASS BACK (spec.HeldBack, said why; review
        2026-11-21, Q6): its failure was the engine's "bulk transport not
        prepared" and the pass asked Cellar for every unit, the newer bulk
        unused. A stop — the pass's, which the engine binds around prepare —
        ends the build at its next chunk boundary and the pass with it (Q7:
        none was read, and a SIGTERM waited for the whole build)."""
        from . import datadump, eurlex_dump
        from .spec import HeldBack
        dumps = datadump.dumps()
        langs = sorted({unit_of(f)["lang"] for f in pending if is_unit(f)})
        if dumps.base is None:
            # no dump folder named: nothing newer to build, but a language
            # whose dump is stamped into the corpus holds the pass back
            # (review of 13743848, 1: an unset EURLEX_DATADUMP_DIR sent every
            # tree to Cellar)
            self._prepare_bulk_held(None, collection, langs, pending, endpoint)
            return []
        try:
            eurlex_dump.build_newer(dumps.base, Path(collection.corpus_dir), langs, stop=_stop_requested)
        except (TransferAborted, HeldBack):
            raise
        except Exception as e:  # noqa: BLE001 — whatever stopped the bulk, Cellar waits for it
            raise HeldBack(f"{getattr(collection, 'name', 'eurlex')}: a newer Data Dump of {', '.join(langs)} "
                           f"was not built ({type(e).__name__}: {str(e)[:300]}); bulk first, so Cellar is "
                           f"not asked for its units until it is") from e
        self._prepare_bulk_held(dumps.base, collection, langs, pending, endpoint)
        return []

    def _prepare_bulk_held(self, dump_base, collection, langs, pending, endpoint) -> None:
        """`_prepare_bulk`, whatever stops it holding the pass back (review of
        7eb82ba1, 1: an OSError in the listing phase — ENOSPC on the fast
        drive — left prepare as itself, the engine went on "not prepared",
        and every tree was asked of Cellar: F4 again)."""
        from .spec import HeldBack
        name = getattr(collection, "name", "eurlex")
        try:
            self._prepare_bulk(dump_base, Path(collection.corpus_dir), langs, pending, name,
                               getattr(endpoint, "url", None))
        except (TransferAborted, HeldBack):
            raise
        except Exception as e:  # noqa: BLE001 — whatever stopped the bulk, Cellar waits for it
            raise HeldBack(f"{name}: the trees the Data Dump holds were not readied ({type(e).__name__}: "
                           f"{str(e)[:300]}); bulk first, so Cellar is not asked for them until they are") from e

    def _prepare_bulk(self, dump_base: Path, corpus: Path, langs: list[str], pending: list[RemoteFile],
                      name: str, base_url: str | None = None) -> None:
        """The trees the bulk holds, ready BEFORE the pass (bulk first,
        2026-11-26): each language's MTD — the one of the dump stamped into
        the corpus — indexed by CELEX, the stop asked between chunks. Then
        WHAT CHANGED SINCE THE DUMP WAS CUT, from the listing (architecture's
        decision, 2026-11-26): a (CELEX year, language) of a unit asked first
        whose window was not known whole after the stamped dump's `created`
        is listed FIRST — a windowed listing, a count and its pages, kept and
        re-used — the stop asked between windows; a window that cannot be
        listed leaves its units waiting. An MTD that does not open holds the
        pass back."""
        from app import parallel
        from . import cellar_listing, datadump, eurlex_dump, work_trees
        from .spec import HeldBack
        from ..plugins.eurlex.source import LANGUAGES
        say = _say_cellar(name)
        try:
            bulk = work_trees.prepare_bulk(dump_base, corpus, langs, threads=parallel.processes(),
                                           stop=_stop_requested, log=say)
        except work_trees.Stopped as e:
            raise TransferAborted(f"cellar: {e}") from None
        except OSError as e:
            raise HeldBack(f"{name}: the Data Dump's MTD does not read ({str(e)[:300]}); bulk first, so "
                           f"Cellar is not asked for the trees it holds until it does") from e
        due = {(unit_of(f)["celex"], unit_of(f)["lang"]) for f in pending
               if is_unit(f) and f.mtime is None and unit_of(f)["lang"] in bulk}
        after: dict[str, float] = {}
        for lang, when in eurlex_dump.stamp(corpus).items():
            got = datadump._stamp(when)
            if got is not None:
                after[lang] = got.timestamp()
        dates, cover = cellar_listing.coverage(due, after, base_url) if due and base_url else ({}, {})
        # A COVERED WINDOW IS ASKED AGAIN ONCE A DAY (architecture's decision,
        # 2026-11-26, the operator's bulk-check rule): its fingerprint, for a
        # window known whole more than REFRESH_S ago that holds units still
        # due — a change Cellar made since is then seen. THE BOUND LEFT: a
        # change within the day before a unit lands lands the dump's tree; the
        # next complete listing sees its date and fetches it again.
        missing: dict[int, set[str]] = {}
        stale = 0
        now = time.time()
        for celex, lang in due:
            year = window_year(celex)
            if year is None:
                continue
            at = cover.get((year, lang))
            if at is None or now - at > WINDOW_REFRESH_S:
                stale += at is not None and lang not in missing.get(year, set())
                missing.setdefault(year, set()).add(lang)
        unlisted: set[tuple[int, str]] = set()
        if missing and base_url:
            t0, asked = time.monotonic(), 0
            code_of = {v: k for k, v in LANGUAGES.items()}
            say(f"listing what changed since the dump: {sum(len(v) for v in missing.values())} "
                f"(year, language) window(s) of {len(missing)} year(s), {stale} of them known whole more "
                f"than a day ago, before any unit is built from it")
            # THE LISTING PHASE IN PARALLEL (architecture, 2026-11-26, on the
            # live pass: one query at a time on one connection, py-spy reading
            # "the 1993 page at offset 5000 (6 languages)" — a 13-language
            # count had timed out and been halved — while Cellar's endpoint
            # allows LISTING_SLOTS; CLAUDE.md "Multi-threading is a must").
            # Windows start ONE language wide (LISTING_START_GROUP: a grouped
            # count is what times out, 900 s, before it is split; a single
            # language answered in 26-235 s, 11-14), up to LISTING_SLOTS at
            # once, each on its own client, in the pass's context (its stop,
            # keyless as before); the split, should one still be needed, is
            # learned once for all (`_Learned`, under a lock)
            import contextvars
            import threading
            from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
            from ..transfer.bucket import Priority
            from ..transfer.manager import Direction, current_handle, get_manager
            from .specs import EURLEX_SPEC
            mgr = get_manager()
            # ONE SOURCE OF TRUTH for the slots (review of a69741dd, S1): the
            # endpoint's etiquette, at most LISTING_SLOTS
            slots = max(1, min(LISTING_SLOTS, mgr.endpoint_slots(EURLEX_SPEC.endpoints[0], EURLEX_SPEC)))
            learned = _Learned(LISTING_START_GROUP, say=say)
            outer = current_handle()
            # THE PHASE'S OWN STOP (B1): set by the pass's stop or by a local
            # fault, so the windows in flight end at their next request and none
            # begins after it
            phase = threading.Event()
            failed: list[tuple[int, tuple[str, ...], str]] = []
            guard = threading.Lock()

            def year_windows(year: int) -> int:
                """One year's windows, shaped when its turn comes (the split
                learned by an earlier year narrows it), bound to the phase's
                stop under a LISTING key: an episode cools the listing threads,
                never the fetch threads (Z2; S2)."""
                n_listed = 0
                bound = mgr.bind(outer.priority if outer is not None else Priority.BACKFILL, Direction.DOWN,
                                 stop=phase, endpoint_key=LISTING_KEY)
                with bound:
                    langs_y = sorted(missing[year])
                    i = 0
                    while i < len(langs_y):
                        group = learned.group
                        window = tuple(langs_y[i:i + group])
                        i += group
                        _stop_check()
                        if mgr.cooled_for(LISTING_KEY) > 0:
                            # Cellar asked the listing to wait: this window is not
                            # asked now; its units wait (S2: no burst after a 503)
                            with guard:
                                failed.append((year, window, f"cooled {mgr.cooled_for(LISTING_KEY):.0f} s more"))
                            continue
                        t1 = time.monotonic()
                        try:
                            with httpx.Client(timeout=TIMEOUT_S) as client:
                                n = sum(1 for _ in _window_files(client, base_url, year, window, code_of,
                                                                 budget.acquire_bytes, place=lambda *a: False,
                                                                 learned=learned))
                        except TransferAborted:
                            raise
                        except TransportError as e:    # the origin's: this window's units wait
                            with guard:
                                failed.append((year, window, f"{type(e).__name__}: {str(e)[:200]}"))
                            continue
                        except BaseException:
                            phase.set()                # a LOCAL fault: every other window ends now
                            raise
                        say(f"cellar: listed {year} ({', '.join(window)}): {n} units in "
                            f"{time.monotonic() - t1:.0f} s")
                        n_listed += 1
                return n_listed

            pool = ThreadPoolExecutor(max_workers=slots, thread_name_prefix="cellar-listing")
            ended: BaseException | None = None
            try:
                pending_f = {pool.submit(contextvars.copy_context().run, year_windows, y) for y in sorted(missing)}
                while pending_f:
                    done, pending_f = wait(pending_f, timeout=0.2, return_when=FIRST_EXCEPTION)
                    for fut in done:
                        if fut.cancelled():
                            continue
                        e = fut.exception()
                        if e is None:
                            asked += fut.result()
                        elif ended is None or isinstance(ended, TransferAborted) and not isinstance(e, TransferAborted):
                            ended = e                  # a local fault outranks the stop it caused
                    if ended is not None or (outer is not None and outer.stop is not None and outer.stop.is_set()):
                        phase.set()
                        for fut in pending_f:
                            fut.cancel()
                        if ended is None:
                            ended = TransferAborted("cellar: stopped between requests")
            finally:
                if ended is not None:
                    phase.set()
                pool.shutdown(wait=True, cancel_futures=True)
            if ended is not None:
                raise ended
            for year, window, why in failed:
                unlisted |= {(year, l) for l in window}
                say(f"cellar: the {year} window ({', '.join(window)}) is not listed ({why}): its units wait")
            dates, cover = cellar_listing.coverage(due, after, base_url)
            # only the windows LISTED this pass end their units' wait (review
            # of d863230c, Y1: a re-listing that failed reset it; Y2: every
            # covered window's units were marked, ~122k marks a pass)
            now_listed = {(y, l) for y, ls in missing.items() for l in ls} - unlisted
            waiting = {(l, c) for l, units_ in eurlex_dump.incomplete(corpus).items()
                       for c, e in units_.items() if isinstance(e, dict) and e.get("waiting")}
            back = [(l, c) for c, l in due if (window_year(c), l) in now_listed and (window_year(c), l) in cover
                    and (l, c) in waiting]          # only a unit that waits (review of 6f6e72ab, 3)
            if back:
                eurlex_dump.mark_listed(corpus, back)       # their wait is over: n from 0
            say(f"listed {asked} window(s) in {time.monotonic() - t0:.0f} s")
        elif missing:
            unlisted = {(y, l) for y, ls in missing.items() for l in ls}
        cover = {k: at for k, at in cover.items() if k not in unlisted}
        key = str(corpus.resolve())
        with self._lock:
            self._bulk[key] = bulk
            self._dates[key] = dates
            self._cover[key] = cover
            self._after[key] = after
            self._due[key] = due
            self._base_url[key] = base_url
            for k in [k for k in self._window_failed if k[0] == key]:
                del self._window_failed[k]              # a new prepare asks every window afresh
        changed = sum(1 for k, d in dates.items() if d > after.get(k[1], float("inf")))
        unchanged = len(dates) - changed
        say(f"trees from the bulk: {', '.join(sorted(bulk)) or 'no language'}; of {len(due)} unit(s) asked first, "
            f"{unchanged} unchanged since the dump (its tree), {changed} changed (Cellar's), "
            f"{len(due) - len(dates)} not vouched for (Cellar's, or waiting: {len(unlisted)} window(s) unlisted)")


def _corpus_of(dest: Path, remote: RemoteFile) -> Path | None:
    """The corpus root a unit's file lands under: `dest` less its rel_path."""
    text, rel = str(dest), str(remote.rel_path)
    return Path(text[:-len(rel)]) if text.endswith(rel) else None


INVENTORY = CellarInventory()
UNITS = CellarUnits()
TREE_UNITS = TreeUnits()
