"""NeuRIS's listing — the inventory of German federal law, read through the
origin's own API (`Endpoint.listing`; docs/notes/legal-fr-de-eu.md §3.2,
§7.1; the neuris plugin's docstring for the form and the record).

WHAT THE ORIGIN OFFERS (probed from the node, 2026-10-28, §7.1): only
`testphase.rechtsinformationen.bund.de` answers; `/v1/legislation` lists
every expression (5,511 then), paginated (`size` ≤ 300, `pageIndex` from 0,
`totalItems`); there is no bulk zip (`/v1/bulk-zip-links`: 404); the
changelog (`/v1/legislation/changelog?from=&to=`, OffsetDateTimes) answered
`allChanged: true` over a week — the test phase signals a full refresh, not
a delta.

WHAT THIS LISTS. Two files per expression, under the corpus root by the
expression's ELI, each named by the MANIFESTATION's generation date:

    eli/bund/bgbl-1/{year}/{natId}/{pointInTime}/{version}/deu/{generated}.zip
    eli/bund/bgbl-1/{year}/{natId}/{pointInTime}/{version}/deu/{generated}.json

the manifestation ZIP (the main text, every annex, the images: the annexes
are files of their own, `anlage-regelungstext-N.xml`, and fetching
`regelungstext-1.xml` alone would drop them, invariant 11) and the
per-expression JSON (`/v1/legislation/eli/…/deu`: the listing item again,
with `risAbbreviation` and the parts). The generation date is in the path,
so a regenerated manifestation is a NEW file and an unchanged one is the
same path.

THE DELTA is the full listing compared with what is held, BY PATH — the
engine's own gate (`fetch_files`, a row per path): a path held and complete
is skipped, a new path fetched. The changelog is read and obeyed, never
trusted to be empty: a day it answers `allChanged: true` says nothing about
any one expression, and the comparison stands; a day it answers
`allChanged: false`, every expression its `changed` names gets that day's
stamp as the files' `mtime` (the end of the day, epoch seconds), so the
engine fetches them again although their paths are held, and every
`deleted` one is REPORTED (`drain_reports`, an event with the collection's
name) and never removed — what we held stays verifiable as we held it.
The stamp needs no state of ours: it is the changelog's own answer, read
again each pass over the last CHANGELOG_DAYS days, and it lives in the
engine's `fetch_files.remote_mtime` beside the path. When the day leaves the
window the stamp goes back to none and the expression is fetched once more —
two fetches for one change, the price of keeping nothing beside the corpus.

ETIQUETTE AND THE BUDGET. One request at a time here (the files are the
engine's, two connections at most: NEURIS_SPEC); every page and every
changelog answer is charged to the central budget (invariant 5,
`budget.acquire_bytes`); a 5xx or a timeout is retried within the pass
(ATTEMPTS, about 15 s in all, as transport_https's ranges are) and then
DEFERS the pass (`ListingDeferred`, an `httpx.TransportError`, which the
worker logs as the origin's episode, never the collection's error); a 4xx is
a defect and raised as one. A listing that changed under the pages, or that
the origin's search window (OpenSearch's 10,000) would cut, is never
truncated: it raises.

THE STANDARD, NOT ADOPTED: the listing is a Hydra `Collection` (JSON-LD,
`view.next`); a generic Hydra client would add a dependency for one origin.
Pages are asked for by index under a stable sort (`legislationIdentifier`),
which the API's own pagination guide documents.
"""
from __future__ import annotations

import calendar
import re
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone

import httpx

from . import budget
from .spec import AcquisitionSpec, Endpoint, RemoteFile, TransportError

LISTING = "v1/legislation"
CHANGELOG = "v1/legislation/changelog"
#: the API's maximum page (openapi: `size` ≤ 300)
PAGE_SIZE = 300
#: OpenSearch's result window: a listing past it cannot be paged to its end
WINDOW = 10_000
#: how many days of the changelog each pass reads
CHANGELOG_DAYS = 7
#: one request's attempts on a 5xx or a timeout: 1 + 2 + 4 + 8 s between them
ATTEMPTS = 5
HTTP_TIMEOUT_S = 60.0

#: `eli/bund/bgbl-1/2026/318/2026-10-22/1/deu` — the expression's ELI
#: (legal-fr-de-eu.md §3.3): jurisdiction, agent, year, natural id, point in
#: time, version, language; every segment a safe path segment
EXPRESSION_ELI = re.compile(
    r"^eli/(?P<jur>bund)/(?P<agent>[a-z0-9][a-z0-9-]*)/(?P<year>\d{4})/"
    r"(?P<nat>[A-Za-z0-9][A-Za-z0-9_-]*)/(?P<pit>\d{4}-\d{2}-\d{2})/"
    r"(?P<version>\d+)/(?P<lang>[a-z]{3})$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ELI_IN = re.compile(r"eli/bund/[a-z0-9-]+/\d{4}/[A-Za-z0-9_-]+/\d{4}-\d{2}-\d{2}/\d+/[a-z]{3}")


class ListingDeferred(httpx.TransportError):
    """The origin did not answer the listing this pass (a 5xx past the
    retries, a timeout, a listing that moved under its pages): its episode,
    never the collection's error. An `httpx.TransportError`, so the worker's
    acquisition logs it as a deferral and the next discovery asks again."""


def expression_eli(text: str) -> str | None:
    """The expression ELI inside an identifier or URL the API gives
    (`/v1/legislation/eli/…/deu/2023-04-26/regelungstext-1.xml` → `eli/…/deu`)."""
    m = _ELI_IN.search(str(text or ""))
    return m.group(0) if m else None


def manifestation_of(item: dict) -> tuple[str | None, str | None]:
    """`(generation date, the zip's URL path)` from the item's encodings: the
    zip's own, else the date the XML's URL carries and the API's zip route
    for it (openapi: `…/{language}/{pointInTimeManifestation}.zip`)."""
    eli = str(item.get("legislationIdentifier") or "")
    zip_date = zip_url = xml_date = None
    for enc in item.get("encoding") or []:
        url = str((enc or {}).get("contentUrl") or "")
        fmt = str((enc or {}).get("encodingFormat") or "")
        tail = url.split("/" + eli + "/", 1)[1] if eli and ("/" + eli + "/") in url else ""
        head = tail.split("/", 1)[0]
        if fmt == "application/zip" or url.endswith(".zip"):
            d = head.removesuffix(".zip")
            if _DATE.match(d):
                zip_date, zip_url = d, url.lstrip("/")
        elif _DATE.match(head) and xml_date is None:
            xml_date = head
    if zip_date:
        return zip_date, zip_url
    if xml_date:
        return xml_date, f"{LISTING}/{eli}/{xml_date}.zip"
    return None, None


def files_of(item: dict, stamp: int | None = None) -> tuple[RemoteFile, RemoteFile] | str:
    """The expression's two files, or why it cannot be placed (a report)."""
    eli = str(item.get("legislationIdentifier") or "")
    if not EXPRESSION_ELI.match(eli):
        return f"listing: an expression whose ELI is not in the origin's form, not fetched: {eli!r}"
    generated, zip_url = manifestation_of(item)
    if generated is None:
        return f"listing: {eli} names no manifestation (no zip or XML encoding), not fetched"
    json_url = str(item.get("@id") or f"/{LISTING}/{eli}").lstrip("/")
    return (RemoteFile(rel_path=f"{eli}/{generated}.zip", remote=zip_url, mtime=stamp),
            RemoteFile(rel_path=f"{eli}/{generated}.json", remote=json_url, mtime=stamp))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NeurisListing:
    """`Endpoint.listing` for NeuRIS: `listing(endpoint, spec)` yields every
    expression's two files, complete or raising; `drain_reports()` gives what
    it found worth saying (an unplaceable item, a deletion the changelog
    reports) to the engine, which logs it with the collection's name."""

    def __init__(self, *, page_size: int = PAGE_SIZE, changelog_days: int = CHANGELOG_DAYS,
                 now: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.page_size = int(page_size)
        self.changelog_days = int(changelog_days)
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()
        self._reports: list[str] = []

    # -- what the engine reads ------------------------------------------------

    def drain_reports(self) -> list[str]:
        with self._lock:
            out, self._reports = self._reports, []
        return out

    def _report(self, note: str) -> None:
        with self._lock:
            if note not in self._reports:
                self._reports.append(note)
                del self._reports[:-200]

    def __call__(self, endpoint: Endpoint, spec: AcquisitionSpec) -> Iterator[RemoteFile]:
        base = endpoint.url.rstrip("/")
        from .transport_https import headers_for
        headers = {**headers_for(endpoint), "Accept": "application/json"}
        with httpx.Client(timeout=HTTP_TIMEOUT_S, headers=headers, follow_redirects=True) as client:
            items = self._items(client, base)
            stamps = self._stamps(client, base)
        out: list[RemoteFile] = []
        for item in items:
            eli = str(item.get("legislationIdentifier") or "")
            got = files_of(item, stamps.get(eli))
            if isinstance(got, str):
                self._report(got)
                continue
            out.extend(got)
        return iter(out)

    # -- the requests -----------------------------------------------------------

    def _get(self, client: httpx.Client, url: str, params: dict) -> dict:
        """One JSON answer, charged to the budget; a 5xx or a transport fault
        retried, then a deferral; any other status the defect it is."""
        last = ""
        for attempt in range(ATTEMPTS):
            if attempt:
                self._sleep(float(1 << (attempt - 1)))
            try:
                r = client.get(url, params=params)
            except httpx.TransportError as e:
                last = f"{type(e).__name__}: {e}"
                continue
            budget.acquire_bytes(len(r.content))
            if r.status_code >= 500:
                last = f"{r.status_code} to {url}"
                continue
            if r.status_code != 200:
                raise TransportError(f"neuris: {r.status_code} to {url} {params}: {r.text[:200]}")
            try:
                got = r.json()
            except ValueError as e:
                raise TransportError(f"neuris: {url} answered no JSON: {e}") from e
            if not isinstance(got, dict):
                raise TransportError(f"neuris: {url} answered {type(got).__name__}, not an object")
            return got
        raise ListingDeferred(f"neuris: {url}: {last} after {ATTEMPTS} attempts")

    def _items(self, client: httpx.Client, base: str) -> list[dict]:
        """Every expression, every page: the count the origin states, or a
        raise — never a truncated inventory (the listing contract)."""
        url = f"{base}/{LISTING}"
        items: list[dict] = []
        seen: set[str] = set()
        total: int | None = None
        page = 0
        while True:
            got = self._get(client, url, {"size": self.page_size, "pageIndex": page,
                                          "sort": "legislationIdentifier"})
            stated = got.get("totalItems")
            if not isinstance(stated, int) or stated < 0:
                raise TransportError(f"neuris: the listing states no totalItems (page {page})")
            if total is None:
                total = stated
                if total > WINDOW:
                    raise TransportError(
                        f"neuris: the listing states {total} expressions, past the origin's "
                        f"{WINDOW}-result window: paging cannot reach its end, and a truncated "
                        f"inventory is refused — list it in parts (by year) first")
            elif stated != total:
                raise ListingDeferred(f"neuris: the listing moved while it was read "
                                      f"({total} then {stated} expressions)")
            members = got.get("member") or []
            for m in members:
                item = (m or {}).get("item") if isinstance(m, dict) else None
                if not isinstance(item, dict):
                    continue
                eli = str(item.get("legislationIdentifier") or "")
                if eli in seen:
                    self._report(f"listing: {eli} listed twice; the first is taken")
                    continue
                seen.add(eli)
                items.append(item)
            page += 1
            if not members or page * self.page_size >= total:
                break
        if len(seen) != total:
            raise ListingDeferred(f"neuris: the listing gave {len(seen)} distinct expressions "
                                  f"where it states {total}; nothing is fetched from a partial one")
        return items

    def _stamps(self, client: httpx.Client, base: str) -> dict[str, int]:
        """`{expression ELI: stamp}` from the changelog's last days. A day it
        answers `allChanged` stamps nothing (the comparison by path stands);
        a deletion is reported, never acted on. A changelog that cannot be
        read this pass leaves the comparison by path, and says so."""
        now = self._now()
        today = calendar.timegm(time.gmtime(now)[:3] + (0, 0, 0))
        stamps: dict[str, int] = {}
        for k in range(self.changelog_days - 1, -1, -1):
            start = today - k * 86400
            end = start + 86400
            try:
                got = self._get(client, f"{base}/{CHANGELOG}",
                                {"from": _iso(start), "to": _iso(min(end, now))})
            except (ListingDeferred, TransportError) as e:
                self._report(f"changelog {_iso(start)[:10]} unread ({e}); the listing compared "
                             f"by path is the delta")
                continue
            if got.get("allChanged") is True:
                continue
            for c in got.get("changed") or []:
                eli = expression_eli((c or {}).get("@id")) or expression_eli((c or {}).get("contentUrl"))
                if eli:
                    stamps[eli] = max(stamps.get(eli, 0), int(end))
            for d in got.get("deleted") or []:
                what = str((d or {}).get("@id") or d)
                self._report(f"changelog {_iso(start)[:10]}: the origin reports {what} deleted; the "
                             f"copy held stands and nothing is removed (invariant 11)")
        return stamps


def day_end(day_iso: str) -> int:
    """The stamp a changelog day gives: the end of that day, epoch seconds."""
    d = datetime.fromisoformat(day_iso).replace(tzinfo=timezone.utc) + timedelta(days=1)
    return int(d.timestamp())
