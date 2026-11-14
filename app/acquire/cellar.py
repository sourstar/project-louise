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
from .spec import AcquisitionSpec, Endpoint, Filter, RemoteFile, TransferAborted, TransportError

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
_LANG_PATTERN = re.compile(r"^lang=(?P<lang>[a-z]{2})/")
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


def rel_path(lang: str, celex: str) -> str:
    """The unit's completion mark, relative to the corpus root."""
    from ..plugins.eurlex.source import unit_rel
    return f"{unit_rel(lang, celex)}/{TREE_MARK}"


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


def _stop_check() -> None:
    handle = current_handle()
    stop = getattr(handle, "stop", None) if handle is not None else None
    if stop is not None and stop.is_set():
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
    r_ = backoff.Retry(what, _policy())
    for _ in r_:
        try:
            with client.stream("GET", url, headers=headers, follow_redirects=follow) as r:
                if backoff.retryable_status(r.status_code):
                    r_.episode(f"{r.status_code} to {what}", retry_after=r.headers.get("retry-after"))
                    continue
                body = bytearray()
                for chunk in r.iter_bytes(_CHUNK):
                    body += chunk
                    charge(len(chunk))
                if r.status_code not in ok:
                    raise TransportError(f"cellar: {r.status_code} to {what} ({url})")
                return httpx.Response(r.status_code, headers=r.headers, content=bytes(body),
                                      request=r.request)
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


def count_query(year: int, languages: tuple[str, ...]) -> str:
    return (_PREFIXES + "SELECT (COUNT(*) AS ?n) WHERE {\n SELECT DISTINCT ?celex ?language WHERE {\n"
            + _window(year, languages) + " }\n}")


def page_query(year: int, languages: tuple[str, ...], offset: int) -> str:
    return (_PREFIXES + "SELECT ?celex ?language (MAX(?modified) AS ?last) WHERE {\n"
            + _window(year, languages)
            + "  OPTIONAL { ?work cmr:lastModificationDate ?modified }\n"
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
    t = threading.Thread(target=run, name="cellar-sparql", daemon=True)
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
            charge: Callable[[int], None] = budget.acquire_bytes) -> Iterator[RemoteFile]:
    """The origin's COMPLETE listing for the profile (the spec's listing
    contract): every window to its count, every page to the end."""
    from ..plugins.eurlex.source import LANGUAGES
    languages = languages_of(flt)
    if not languages:
        return
    code_of = {v: k for k, v in LANGUAGES.items()}
    years = years if years is not None else range(FIRST_YEAR, time.gmtime().tm_year + 1)
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        for year in years:
            _stop_check()
            yield from _window_files(client, endpoint.url, year, languages, code_of, charge)
    finally:
        if own:
            client.close()


def _timed_out(e: TransportError) -> bool:
    from . import backoff
    return isinstance(e, backoff.Deferred) or "timed out" in str(e).lower()


def _window_files(client, base: str, year: int, languages: tuple[str, ...], code_of: dict,
                  charge) -> Iterator[RemoteFile]:
    """One CELEX year's files in `languages`. A COUNT THAT TIMES OUT is not
    asked again: the window is split, its languages in two halves, each
    counted and paged on its own, down to one language (the architecture
    session, 2026-11-14: "a Cellar query that timed out at 900 s twice will
    time out a third time; split it rather than repeat it"). Measured the
    same day: 1990's count for all 24 languages ran past 900 s, Italian
    alone answered in 26.5-235.5 s. One language that still times out is
    deferred to the next round."""
    lang_text = ",".join(languages) if len(languages) <= 4 else f"{len(languages)} languages"
    try:
        counted = sparql(client, base, count_query(year, languages),
                         what=f"the count of {year} ({lang_text})", charge=charge)
    except TransportError as e:
        if len(languages) > 1 and _timed_out(e):
            half = len(languages) // 2
            yield from _window_files(client, base, year, languages[:half], code_of, charge)
            yield from _window_files(client, base, year, languages[half:], code_of, charge)
            return
        raise
    try:
        want = int(counted[0]["n"]["value"]) if counted else 0
    except (KeyError, ValueError, IndexError):
        raise TransportError(f"cellar: the count of {year} answered {counted!r:.200}") from None
    got = 0
    while got < want:
        rows = sparql(client, base, page_query(year, languages, got),
                      what=f"the {year} page at offset {got} ({lang_text})", charge=charge)
        if not rows:
            break
        for row in rows:
            celex = row.get("celex", {}).get("value", "")
            lang = code_of.get(row.get("language", {}).get("value", "").rsplit("/", 1)[-1])
            if not celex or lang is None:
                continue
            yield RemoteFile(rel_path=rel_path(lang, celex),
                             mtime=_epoch(row.get("last", {}).get("value")),
                             remote=encode(celex, lang))
        got += len(rows)
    if got != want:
        # a Virtuoso answer cut short says nothing: the window is
        # re-listed next pass (worded for engine.transient)
        raise TransportError(f"cellar: the {year} window answered {got} of the {want} rows its "
                             f"count names (connection timed out, the answer cut)")


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


def form_of(note) -> str | None:
    """The best manifestation the notice lists items for (FORMS)."""
    return next((f for f in FORMS if note.items.get(f)), None)


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
    held = _epoch(note.last_modified)
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
            wrappers = [n for n in files if formex.is_wrapper(n)]
            if len(wrappers) != 1:
                raise TransportError(f"cellar: {celex} ({code}) holds {len(wrappers)} Formex wrappers, not one")
            stated = re.search(rb"<LG\.DOC>\s*([A-Z]{2})\s*</LG\.DOC>", files[wrappers[0]])
            if stated and stated.group(1).decode().lower() != lang:
                raise TransportError(f"cellar: asked for {celex} in {lang}, its Formex is in "
                                     f"{stated.group(1).decode().lower()}")
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


# ---- the two extension points the engine reads (spec.py: Inventory, UnitFetcher) ----

class CellarInventory:
    """Cellar's `Inventory`: `listing` above, for the profile's languages."""

    built_on_request = False

    def list(self, endpoint: Endpoint, spec: AcquisitionSpec, profile_filter: Filter):
        return listing(endpoint, spec, profile_filter)


class CellarUnits:
    """Cellar's `UnitFetcher`: a unit is (CELEX, language), its completion
    mark the notice the listing names; fetched by `fetch_unit`, whole."""

    def handles(self, remote: RemoteFile) -> bool:
        return is_unit(remote)

    def fetch(self, endpoint: Endpoint, remote: RemoteFile, dest: Path) -> int:
        from . import datadump, work_trees
        unit = unit_of(remote)
        # dest is the unit's completion mark: <root>/lang=<l>/<yyyy>/<CELEX>/tree.json
        return fetch_unit(endpoint.url, unit["celex"], unit["lang"], dest.parent,
                          listed_mtime=remote.mtime, dump=datadump.dumps().for_language(unit["lang"]),
                          trees=work_trees.store_for(dest.parents[3])).moved

    def refetch(self, spec: AcquisitionSpec, locator: str, base: Path) -> tuple[Path, int]:
        """records-vs-origin: a record's `origin` is its unit
        (`lang=<code>/<yyyy>/<CELEX>`), fetched again under `base` at the same
        relative path, so the plugin reads it as it reads the corpus."""
        rel = str(locator).partition("#")[0].strip("/")
        out = base / rel
        if out.is_dir():
            return out, 0
        parts = rel.split("/")
        if len(parts) != 3 or not parts[0].startswith("lang="):
            raise FileNotFoundError(f"{rel}: not a Cellar unit (lang=<code>/<yyyy>/<CELEX>)")
        lang, celex = parts[0].partition("=")[2], parts[2]
        for endpoint in spec.endpoints:
            try:
                got = fetch_unit(endpoint.url, celex, lang, out)
            except TransportError:
                continue
            return out, got.moved
        raise FileNotFoundError(f"{rel}: no endpoint of {spec.source_id} served it")


INVENTORY = CellarInventory()
UNITS = CellarUnits()
