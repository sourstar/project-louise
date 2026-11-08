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
from typing import Callable, Iterator
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
ATTEMPTS = 5
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


def rel_path(lang: str, celex: str) -> str:
    """The unit's notice, relative to the corpus root: its completion mark."""
    from ..plugins.eurlex.source import unit_rel
    return f"{unit_rel(lang, celex)}/{NOTICE}"


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


def _get(client: httpx.Client, url: str, headers: dict, what: str, *,
         charge: Callable[[int], None], ok=(200,), follow: bool = True) -> httpx.Response:
    """One GET, its body read in chunks charged to the budget, retried on
    the origin's episode (5xx, a timeout, a drop) — then raised in words
    the engine's `transient` reads as a deferral."""
    last = ""
    for attempt in range(ATTEMPTS):
        if attempt:
            time.sleep(float(1 << (attempt - 1)))
            _stop_check()
        try:
            with client.stream("GET", url, headers=headers, follow_redirects=follow) as r:
                if r.status_code >= 500:
                    last = f" {r.status_code} to {what}"
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
            last = f" connection timed out on {what}: {type(e).__name__}"
        except httpx.TransportError as e:
            last = f" {type(e).__name__} on {what}: {e}"
    raise TransportError(f"cellar:{last} after {ATTEMPTS} attempts ({url})")


# ---- the inventory --------------------------------------------------------------------------

_PREFIXES = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
             "PREFIX cmr: <http://publications.europa.eu/ontology/cdm/cmr#>\n")
_LANGUAGE_URI = "http://publications.europa.eu/resource/authority/language/"


def _window(year: int, languages: tuple[str, ...]) -> str:
    """The WHERE of one window: the works whose CELEX opens `3<year>` (an
    act of sector 3) or `0<year>` (its consolidated versions), each with an
    fmx4 manifestation of an expression in one of `languages`."""
    from ..plugins.eurlex.source import LANGUAGES
    values = " ".join(f"<{_LANGUAGE_URI}{LANGUAGES[lang]}>" for lang in languages)
    return (f"  ?work cdm:resource_legal_id_celex ?celex .\n"
            f"  FILTER(STRSTARTS(STR(?celex), \"3{year:04d}\") || STRSTARTS(STR(?celex), \"0{year:04d}\"))\n"
            f"  ?expr cdm:expression_belongs_to_work ?work ; cdm:expression_uses_language ?language .\n"
            f"  VALUES ?language {{ {values} }}\n"
            f"  ?manif cdm:manifestation_manifests_expression ?expr ; cdm:manifestation_type ?type .\n"
            f"  FILTER(STR(?type) = \"fmx4\")\n")


def count_query(year: int, languages: tuple[str, ...]) -> str:
    return (_PREFIXES + "SELECT (COUNT(*) AS ?n) WHERE {\n SELECT DISTINCT ?celex ?language WHERE {\n"
            + _window(year, languages) + " }\n}")


def page_query(year: int, languages: tuple[str, ...], offset: int) -> str:
    return (_PREFIXES + "SELECT ?celex ?language (MAX(?modified) AS ?last) WHERE {\n"
            + _window(year, languages)
            + "  OPTIONAL { ?work cmr:lastModificationDate ?modified }\n"
            + f"}}\nGROUP BY ?celex ?language\nORDER BY ?celex ?language\nLIMIT {PAGE} OFFSET {offset}")


def sparql(client: httpx.Client, base: str, query: str, *,
           charge: Callable[[int], None] = budget.acquire_bytes) -> list[dict]:
    """The SPARQL 1.1 Protocol: the query POSTed as a form field, the JSON
    results format back (`results.bindings`), every byte charged."""
    url = base.rstrip("/") + SPARQL_PATH
    last = ""
    for attempt in range(ATTEMPTS):
        if attempt:
            time.sleep(float(1 << (attempt - 1)))
            _stop_check()
        try:
            with client.stream("POST", url, data={"query": query, "format": "application/sparql-results+json"},
                               headers=_headers({"Accept": "application/sparql-results+json"})) as r:
                if r.status_code >= 500:
                    last = f" {r.status_code} to the SPARQL query"
                    continue
                body = bytearray()
                for chunk in r.iter_bytes(_CHUNK):
                    body += chunk
                    charge(len(chunk))
                if r.status_code != 200:
                    raise TransportError(f"cellar: {r.status_code} to the SPARQL query: {bytes(body[:200])!r}")
            try:
                return list(json.loads(bytes(body))["results"]["bindings"])
            except (ValueError, KeyError, TypeError) as e:
                # a page cut mid-answer reads as broken JSON: the origin's episode
                last = f" connection timed out: the SPARQL answer did not parse ({e})"
                continue
        except httpx.TimeoutException as e:
            last = f" connection timed out on the SPARQL query: {type(e).__name__}"
        except httpx.TransportError as e:
            last = f" {type(e).__name__} on the SPARQL query: {e}"
    raise TransportError(f"cellar:{last} after {ATTEMPTS} attempts")


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
            counted = sparql(client, endpoint.url, count_query(year, languages), charge=charge)
            try:
                want = int(counted[0]["n"]["value"]) if counted else 0
            except (KeyError, ValueError, IndexError):
                raise TransportError(f"cellar: the count of {year} answered {counted!r:.200}") from None
            got = 0
            while got < want:
                rows = sparql(client, endpoint.url, page_query(year, languages, got), charge=charge)
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
    finally:
        if own:
            client.close()


# ---- one unit ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Fetched:
    files: dict[str, int]          # name → bytes
    moved: int


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


def fetch_unit(base: str, celex: str, lang: str, dest: Path, *,
               client: httpx.Client | None = None,
               charge: Callable[[int], None] = budget.acquire_bytes) -> Fetched:
    """The unit of (CELEX, language) into `dest` (the unit's directory),
    whole or not at all."""
    from ..plugins.eurlex import formex, notice as notice_mod
    from ..plugins.eurlex.source import LANGUAGES
    code = LANGUAGES[lang]
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S)
    moved = 0

    def count(n: int) -> None:
        nonlocal moved
        moved += n
        charge(n)
    try:
        url = resource_url(base, celex)
        accept_lang = {"Accept-Language": code.lower()}
        r = _get(client, url, _headers({"Accept": "application/xml;notice=branch", **accept_lang}),
                 f"the notice of {celex} ({code})", charge=count)
        notice_bytes = r.content
        try:
            note = notice_mod.parse(notice_bytes)
        except notice_mod.NoticeError as e:
            raise TransportError(f"cellar: the notice of {celex} ({code}) is not one: {e}") from None
        if note.celex != celex:
            raise TransportError(f"cellar: asked for {celex}, the notice is {note.celex}'s")
        if note.display_language and note.display_language != code:
            raise TransportError(f"cellar: asked for {celex} in {code}, the notice is in {note.display_language}")
        listed = [(it.uri, it.name, it.size) for it in note.items.get("fmx4", [])]
        if not listed:
            _stop_check()
            r = _get(client, url, _headers({"Accept": "application/xml;type=fmx4", **accept_lang}),
                     f"the fmx4 manifestation of {celex} ({code})", charge=count, ok=(300, 200))
            listed = [(uri, name, None) for uri, name in items_from_choices(r.content)]
        if not listed:
            raise TransportError(f"cellar: {celex} ({code}) lists no fmx4 item")
        files: dict[str, bytes] = {}
        for uri, name, size in listed:
            _stop_check()
            r = _get(client, uri, _headers(accept_lang), f"{name or uri} of {celex} ({code})", charge=count)
            if not name:
                m = _DISPOSITION.search(r.headers.get("content-disposition", ""))
                name = m["name"].strip() if m else None
            if not name or not _NAME.match(name) or name == NOTICE:
                raise TransportError(f"cellar: {uri} of {celex} ({code}) names no plain file ({name!r})")
            if name in files:
                raise TransportError(f"cellar: two items of {celex} ({code}) named {name}")
            if size is not None and size != len(r.content):
                raise TransportError(f"cellar: {name} of {celex} ({code}) is {len(r.content)} bytes, "
                                     f"the notice says {size}")
            files[name] = r.content
        wrappers = [n for n in files if formex.is_wrapper(n)]
        if len(wrappers) != 1:
            raise TransportError(f"cellar: {celex} ({code}) holds {len(wrappers)} Formex wrappers, not one")
        stated = re.search(rb"<LG\.DOC>\s*([A-Z]{2})\s*</LG\.DOC>", files[wrappers[0]])
        if stated and stated.group(1).decode().lower() != lang:
            raise TransportError(f"cellar: asked for {celex} in {lang}, its Formex is in "
                                 f"{stated.group(1).decode().lower()}")
        files[NOTICE] = notice_bytes
        _land(dest, files)
        return Fetched(files={n: len(b) for n, b in files.items()}, moved=moved)
    except httpx.HTTPError as e:
        raise TransportError(f"cellar: {type(e).__name__}: {e}") from e
    finally:
        if own:
            client.close()


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
    for name in sorted(files, key=lambda n: (n == NOTICE, n)):
        with open(staging / name, "wb") as f:
            f.write(files[name])
            f.flush()
            os.fsync(f.fileno())
    if dest.exists():
        os.replace(dest, old)
    os.replace(staging, dest)
    shutil.rmtree(old, ignore_errors=True)


# ---- the origin re-fetched, for records-vs-origin -------------------------------------------

class CellarOrigin:
    """What `records-vs-origin` re-fetches for an eurlex collection: a
    record's `origin` is its unit (`lang=<code>/<yyyy>/<CELEX>`), fetched by
    `fetch_unit` from the spec's endpoint into a temporary base at the same
    relative path, so the plugin reads it as it reads the corpus
    (app/acquire/member_origin.py and file_origin.py do the same for zip
    members and mirror trees)."""

    def __init__(self, spec: AcquisitionSpec) -> None:
        import atexit
        import tempfile
        self.spec = spec
        self.base = Path(tempfile.mkdtemp(prefix="louise-cellar-origin-"))
        atexit.register(self.close)
        self.bytes_read = 0

    @staticmethod
    def _rel(locator: str) -> str:
        return str(locator).partition("#")[0].strip("/")

    def unit(self, locator: str) -> tuple[str, int | None]:
        return self._rel(locator), None

    def weigh(self, unit: str) -> int | None:
        """A unit's size is known once it is fetched: weighing fetches it
        (the check's bound takes units one by one, so nothing is fetched
        that the bound does not reach)."""
        try:
            out = self.materialise(unit)
        except FileNotFoundError:
            return None
        return sum(p.stat().st_size for p in out.iterdir() if p.is_file())

    def materialise(self, locator: str) -> Path:
        rel = self._rel(locator)
        out = self.base / rel
        if out.is_dir():
            return out
        parts = rel.split("/")
        if len(parts) != 3 or not parts[0].startswith("lang="):
            raise FileNotFoundError(f"{rel}: not a Cellar unit (lang=<code>/<yyyy>/<CELEX>)")
        lang, celex = parts[0].partition("=")[2], parts[2]
        for endpoint in self.spec.endpoints:
            try:
                got = fetch_unit(endpoint.url, celex, lang, out)
            except TransportError:
                continue
            self.bytes_read += got.moved
            return out
        raise FileNotFoundError(f"{rel}: no endpoint of {self.spec.source_id} served it")

    def close(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


def origin_for_codex(codex) -> CellarOrigin | None:
    from .specs import ACQUIRE_REGISTRY
    spec = ACQUIRE_REGISTRY.get(getattr(codex, "plugin", None))
    if spec is None or spec.listing is not listing:
        return None
    return CellarOrigin(spec)
