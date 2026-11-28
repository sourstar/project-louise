"""The Met's Open Access, listed and fetched: an Inventory and a UnitFetcher
(spec.py) for `MET_SPEC` (specs.py), docs/notes/image-collections.md §8–§12.

THE BULK FIRST. The listing names the Open Access CSV (`MetObjects.csv`, the
Met's bulk, CC0) before any object, so a pass lands it first. Then one unit
per object the API lists (`/public/collection/v1/objects`, every id in one
answer): the object's API JSON, kept as served, and the image files its
`primaryImage` and `additionalImages` name, the Met's own bytes from
images.metmuseum.org. The JSON is the unit's COMPLETION MARK and lands last,
so a unit is held whole or not at all (invariant 2): an image already landed
is not fetched again, and a pass stopped between two images resumes there.

Corpus layout, relative to the corpus root:

    MetObjects.csv
    objects/<id // 1000, 4 digits>/<id>/object.json
    objects/<id // 1000, 4 digits>/<id>/<the image's own file name>

EVERY BYTE FROM THE BUDGET (invariant 5): the listing's answer through
`budget.acquire_bytes`, every file through `HttpsTransport.fetch_one`, which
draws per chunk. The Met asks for at most 80 requests a second; this spec
keeps to 4 connections and 0.05 s between request starts per endpoint
(`MET_SPEC.etiquette`, specs.py), at most 20 a second.

A 5xx or a transport fault is a `TransportError` (the engine's deferral);
an object the API no longer serves (404) is a `TransportError` too, never a
document error: the listing that named it is older than the answer.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

from . import budget
from .spec import AcquisitionSpec, Endpoint, RemoteFile, Transport, TransportError

API_URL = "https://collectionapi.metmuseum.org"
OBJECTS = "public/collection/v1/objects"
#: Git LFS's media host serves the CSV's bytes (github.com's blob page is HTML)
CSV_URL = "https://media.githubusercontent.com"
CSV_REMOTE = "media/metmuseum/openaccess/master/MetObjects.csv"
CSV_PATH = "MetObjects.csv"
IMAGES_HOST = "images.metmuseum.org"
MARK = "object.json"
HTTP_TIMEOUT_S = 60
ATTEMPTS = 4

_UNIT = re.compile(r"^objects/(\d{4})/(\d+)/object\.json$")


def unit_dir(object_id: int) -> str:
    """The object's directory, relative to the corpus root."""
    return f"objects/{int(object_id) // 1000:04d}/{int(object_id)}"


def unit_path(object_id: int) -> str:
    return f"{unit_dir(object_id)}/{MARK}"


def object_id_of(rel_path: str) -> int | None:
    m = _UNIT.match(str(rel_path).partition("#")[0].strip("/"))
    if m is None or int(m.group(2)) // 1000 != int(m.group(1)):
        return None
    return int(m.group(2))


def image_urls(obj: dict) -> list[str]:
    """The object's image files, in the order the API states them: the
    primary image, then the additional ones; empty strings dropped, a URL
    named twice taken once (one file on disk)."""
    out: list[str] = []
    for u in [obj.get("primaryImage") or ""] + list(obj.get("additionalImages") or []):
        u = str(u or "").strip()
        if u and u not in out:
            out.append(u)
    return out


def image_name(url: str, taken: set[str]) -> str:
    """The file name an image lands under: its own, from the URL's path;
    a name another image of the same object already took gets its order in
    front (`2-<name>`), so no file overwrites another."""
    name = PurePosixPath(urlsplit(url).path).name or "image"
    if name in (MARK, CSV_PATH) or name.startswith("."):
        name = f"_{name}"
    base, n = name, 1
    while name in taken:
        n += 1
        name = f"{n}-{base}"
    taken.add(name)
    return name


def image_names(obj: dict) -> list[tuple[str, str]]:
    """`(url, file name)` of every image, in order — the one rule the fetcher
    lands them by and the plugin reads them by."""
    taken: set[str] = set()
    return [(u, image_name(u, taken)) for u in image_urls(obj)]


def _endpoint_for(url: str) -> tuple[Endpoint, str]:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise TransportError(f"met: not an https URL: {url!r}")
    path = parts.path.lstrip("/") + (f"?{parts.query}" if parts.query else "")
    return Endpoint(transport=Transport.HTTPS, url=f"https://{parts.netloc}"), path


class MetListing:
    """Every object id the API lists, in one answer, after the CSV — the
    whole listing or a raise, never truncated (the listing contract)."""

    def __init__(self, *, client_factory=None) -> None:
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=True,
                                 headers={"Accept": "application/json"}))
        self._reports: list[str] = []

    def list(self, endpoint: Endpoint, spec: AcquisitionSpec, profile_filter=None):
        base = endpoint.url.rstrip("/")
        last = ""
        with self._client_factory() as client:
            for _ in range(ATTEMPTS):
                try:
                    r = client.get(f"{base}/{OBJECTS}")
                except httpx.TransportError as e:
                    last = f"{type(e).__name__}: {e}"
                    continue
                budget.acquire_bytes(len(r.content))
                if r.status_code >= 500:
                    last = f"{r.status_code}"
                    continue
                if r.status_code != 200:
                    raise TransportError(f"met: {r.status_code} to {base}/{OBJECTS}: {r.text[:200]}")
                got = r.json()
                break
            else:
                raise TransportError(f"met: {base}/{OBJECTS}: {last} after {ATTEMPTS} attempts")
        ids = got.get("objectIDs") if isinstance(got, dict) else None
        total = got.get("total") if isinstance(got, dict) else None
        if not isinstance(ids, list) or (isinstance(total, int) and total != len(ids)):
            raise TransportError(f"met: the listing states {total!r} objects and gives "
                                 f"{len(ids) if isinstance(ids, list) else 'no list'}")
        out = [RemoteFile(rel_path=CSV_PATH, remote=CSV_REMOTE)]
        for oid in sorted({int(i) for i in ids}):
            out.append(RemoteFile(rel_path=unit_path(oid), remote=f"{OBJECTS}/{oid}"))
        self._reports.append(f"listing: the CSV and {len(out) - 1:,} objects")
        return iter(out)

    def reports(self) -> list[str]:
        out, self._reports = self._reports, []
        return out


class MetUnits:
    """One object, whole: its images first, its API JSON last."""

    def __init__(self, transport=None) -> None:
        self._transport = transport

    def _t(self):
        if self._transport is None:
            from .transport_https import HttpsTransport
            self._transport = HttpsTransport()
        return self._transport

    def handles(self, remote: RemoteFile) -> bool:
        return remote.rel_path == CSV_PATH or object_id_of(remote.rel_path) is not None

    def fetch(self, endpoint: Endpoint, remote: RemoteFile, dest: Path) -> int:
        if remote.rel_path == CSV_PATH:
            csv_ep = Endpoint(transport=Transport.HTTPS, url=CSV_URL)
            return self._t().fetch_one(csv_ep, RemoteFile(rel_path=CSV_PATH, remote=CSV_REMOTE), dest, 0)
        oid = object_id_of(remote.rel_path)
        if oid is None:
            raise TransportError(f"met: {remote.rel_path}: not an object unit")
        return self._unit(endpoint, oid, dest)

    def _unit(self, endpoint: Endpoint, oid: int, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        staged = dest.with_name(dest.name + ".staged")
        moved = self._t().fetch_one(endpoint, RemoteFile(rel_path=unit_path(oid), remote=f"{OBJECTS}/{oid}"),
                                    staged, 0)
        try:
            obj = json.loads(staged.read_bytes())
        except ValueError as e:
            staged.unlink(missing_ok=True)
            raise TransportError(f"met: object {oid}: the API answered no JSON ({e})") from e
        if not isinstance(obj, dict) or int(obj.get("objectID") or -1) != oid:
            staged.unlink(missing_ok=True)
            raise TransportError(f"met: object {oid}: the API answered another object or a message")
        for url, name in image_names(obj):
            target = dest.parent / name
            if target.is_file():          # landed by an earlier, stopped pass: not fetched again
                continue
            ep, path = _endpoint_for(url)
            moved += self._t().fetch_one(ep, RemoteFile(rel_path=name, remote=path), target, 0)
        os.replace(staged, dest)          # the completion mark, last
        return moved

    def refetch(self, spec: AcquisitionSpec, locator: str, base: Path, *,
                parser: int | None = None) -> tuple[Path, int]:
        rel = str(locator).partition("#")[0].strip("/")
        oid = object_id_of(rel)
        if oid is None:
            raise FileNotFoundError(f"{rel}: not a Met object unit (objects/NNNN/<id>/object.json)")
        out = base / rel
        if out.is_file():
            return out, 0
        for endpoint in spec.endpoints:
            try:
                return out, self._unit(endpoint, oid, out)
            except TransportError:
                continue
        raise FileNotFoundError(f"{rel}: no endpoint of {spec.source_id} served it")


LISTING = MetListing()
UNITS = MetUnits()
