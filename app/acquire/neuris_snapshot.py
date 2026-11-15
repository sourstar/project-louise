"""NeuRIS's daily SNAPSHOT as a local TRANSPORT for the manifestation zips
(06 §6.2, bulk first; operator, 2026-11-14: "every time there is a bulk
download that saves a relevant amount of single requests to the source, we
start from the bulk download first").

WHAT THE ORIGIN PUBLISHES (München, from the node, 2026-11-14). The API's
`/v1/bulk-zip-links` names, among its datasets, `legislation`: one zip of
every manifestation, rebuilt daily (`legislation_2026-11-14T06:10:30…Z.zip`,
631 MB, 110,696 members, 5,525 expressions, the listing's count), on the
origin's object storage, with Range. Its members are the API zip's own, under
the same names (`eli/bund/<agent>/<year>/<natId>/<pit>/<version>/deu/
<generated>/…`), plus a `metadaten.xml` per work, which no manifestation zip
carries and which is not read here. It states no size and no checksum per
manifestation; the zip's per-member CRC-32 holds.

WHAT IT SAVES. One request for each manifestation zip a pass has to fetch:
5,525 for a full fetch, against one download of the snapshot. The
per-expression JSON (abbreviation, dates, Gazette, force) is not in it: it is
still asked of the API, one request per expression.

THE ORIGIN IS STILL THE API (the legal session's conditions, 2026-11-14):
  1. a manifestation is taken from the snapshot only when the listing names
     it NOW — the same expression ELI and the same generation date in the
     member's path (`handles`); any other, and one the snapshot does not
     hold, is fetched per law;
  2. members are read and hashed normalized from parser 3 (the plugin's
     NORMALIZATION: no per-build GUID, no HTML `erzeugt`), so a snapshot
     member and a per-law member of one manifestation give one
     `origin_hash` across days;
  3. `records-vs-origin` re-fetches per law from the API (`refetch`), never
     from the snapshot.
A snapshot member is the origin's by PROVENANCE: the origin publishes it and
names it from its own API (06 §6.2, amended 2026-11-14), and the zip's CRC
holds on every member read.

WHEN IT IS DOWNLOADED (`prepare`, before a pass fetches): when the pass has
at least SHARE of the listing's zips to fetch — "relevant" counted as the
share of requests the bulk would answer (06 §6.2). A daily delta of a few
laws is fetched per law. The download is the engine's own Range transport,
drawn from the budget (invariant 5), resumable; it lives in the files cache
(app/derived.py, kind `neuris-snapshot`, keyed on the corpus), the newest
only, re-downloaded when the API names another.

REVISED IN PLACE (2026-11-15, München's probe of NeuRIS5's inconclusive
records-vs-origin): the portal revises manifestations WITHOUT moving their
generation date — it annotated every `ris:abkuerzung` with domainTerm "Interne
Abkürzung" — and the listing, by path, and the test phase's changelog
(`allChanged`) never name such a law again: the corpus fell behind silently and
no record could be verified. So the snapshot is also taken at least every
REVISIT_S (a week, one download) whatever the pass's share, and every held
manifestation it holds at the listing's generation date is compared with it,
member by member after the plugin's normalization: one that differs is the
origin's revision and is rewritten from the snapshot in the same pass
(`prepare`'s answer, added to the pass by the engine), no request per law.

THE STANDARD, NOT ADOPTED: none for "a bulk as a transport of a per-unit
origin" beyond the mirror (06 §6.2); EUR-Lex's Data Dump (datadump.py) is
the same pattern under another proof.
"""
from __future__ import annotations

import os
import re
import threading
import zipfile
import zlib
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import budget
from .neuris_listing import EXPRESSION_ELI, LISTING
from .spec import Endpoint, RemoteFile, Transport, TransportError

LINKS = "v1/bulk-zip-links"
DATASET = "legislation"
KIND = "neuris-snapshot"
#: the share of the listing's manifestation zips a pass must have to fetch
#: for the snapshot to be taken: below it, the pass's laws are fetched one by
#: one (a daily delta); at or above it (a first fetch, a full refresh), one
#: download answers them
SHARE = 0.25
HTTP_TIMEOUT_S = 60.0
#: the longest a held corpus goes without being compared with the snapshot
#: (REVISED IN PLACE above): one download a week
REVISIT_S = 7 * 86400
#: threads comparing held manifestations with the snapshot (zlib releases the GIL)
COMPARE_THREADS = 8
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: an expression ELI is eight path segments; the generation date the ninth
_ELI_PARTS = 8


def manifestation_key(rel_path: str) -> str | None:
    """`eli/…/deu/<generated>` of a listed zip `eli/…/deu/<generated>.zip`,
    or None for anything else (the JSON, a name not in the origin's form)."""
    if not rel_path.endswith(".zip"):
        return None
    parts = rel_path[:-4].split("/")
    if len(parts) != _ELI_PARTS + 1 or not _DATE.match(parts[-1]):
        return None
    if not EXPRESSION_ELI.match("/".join(parts[:_ELI_PARTS])):
        return None
    return "/".join(parts)


def member_key(name: str) -> str | None:
    """The manifestation a snapshot member belongs to, or None (a work's
    `metadaten.xml`, a directory entry, a name not in the origin's form)."""
    parts = name.split("/")
    if len(parts) <= _ELI_PARTS + 1 or not parts[-1] or not _DATE.match(parts[_ELI_PARTS]):
        return None
    if not EXPRESSION_ELI.match("/".join(parts[:_ELI_PARTS])):
        return None
    return "/".join(parts[:_ELI_PARTS + 1])


def link(endpoint: Endpoint, *, client: httpx.Client | None = None) -> str | None:
    """The `legislation` snapshot's URL `/v1/bulk-zip-links` names now, or
    None when it names none (a null `contentUrl`, the dataset absent).
    Charged to the budget (invariant 5); a failure is the origin's episode."""
    from .transport_https import headers_for
    url = f"{endpoint.url.rstrip('/')}/{LINKS}"
    own = client is None
    c = client or httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=True)
    try:
        r = c.get(url, headers={**headers_for(endpoint), "Accept": "application/json"})
        budget.acquire_bytes(len(r.content))
        r.raise_for_status()
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise TransportError(f"{url}: {type(e).__name__}: {e}") from e
    finally:
        if own:
            c.close()
    for ds in body.get("dataSet") or []:
        if (ds or {}).get("name") == DATASET:
            got = ((ds.get("distribution") or {}).get("contentUrl"))
            return str(got) if got else None
    return None


class NeurisSnapshot:
    """The spec's UnitFetcher (spec.UnitFetcher) for NeuRIS: a listed zip
    the prepared snapshot holds is written from its members; every other
    file goes the transport's way. `prepare` decides per pass."""

    def __init__(self, *, share: float = SHARE) -> None:
        self.share = float(share)
        self._lock = threading.Lock()
        self._zip: zipfile.ZipFile | None = None
        self._path: Path | None = None
        self._index: dict[str, list[zipfile.ZipInfo]] = {}
        self._taken = 0
        self._reports: list[str] = []

    # -- what the engine asks -------------------------------------------------

    def prepare(self, files: list[RemoteFile], pending: list[RemoteFile], collection,
                endpoint: Endpoint) -> list[str]:
        """Before a pass fetches: the snapshot opened when the pass has at
        least `share` of the listing's zips to fetch, or when the held corpus
        has not been compared with it for REVISIT_S; downloaded first when the
        API names a newer one than the place holds; else nothing (the pass is
        per law). A failure leaves the pass per law and is said. Returns the
        held zips the origin revised in place (REVISED IN PLACE), for the
        engine to fetch in this pass — from the snapshot."""
        listed = sum(1 for f in files if manifestation_key(f.rel_path))
        want = [f for f in pending if manifestation_key(f.rel_path)]
        self.close()
        due = self._revisit_due(collection)
        if not due and (not want or len(want) < self.share * max(1, listed)):
            if want:
                self._report(f"snapshot not taken: {len(want)} of {listed} manifestation zip(s) to "
                             f"fetch, under {self.share:.0%}; fetched per law")
            return []
        try:
            url = link(endpoint)
            if url is None:
                self._report("snapshot not taken: the API names no legislation snapshot now; "
                             "fetched per law")
                return []
            path = self._download(url, collection)
            self._open(path)
        except (TransportError, OSError, zipfile.BadZipFile) as e:
            self.close()
            self._report(f"snapshot not taken: {type(e).__name__}: {e}; fetched per law"[:400])
            return []
        held = sum(1 for f in want if manifestation_key(f.rel_path) in self._index)
        self._report(f"snapshot {path.name}: {len(self._index)} manifestation(s); {held} of the "
                     f"{len(want)} zip(s) to fetch are in it with the listing's generation date, "
                     f"the rest per law")
        revised = self._revised(files, {f.rel_path for f in pending}, collection, path)
        os.utime(path)                     # compared now: the next revisit is REVISIT_S away
        self._report(f"snapshot {path.name}: {len(revised)} held manifestation(s) the origin revised in "
                     f"place (the same generation date), rewritten from it in this pass")
        return revised

    def _revisit_due(self, collection) -> bool:
        """No snapshot held, or the held one compared longer than REVISIT_S ago."""
        import time
        where = self.place(collection)
        held = sorted(where.glob("*.zip"), key=lambda p: p.stat().st_mtime) if where.is_dir() else []
        return not held or time.time() - held[-1].stat().st_mtime >= REVISIT_S

    def _revised(self, files: list[RemoteFile], pending: set[str], collection, path: Path) -> list[str]:
        """The held, listed zips whose members, after the plugin's
        normalization, differ from the snapshot's at the same generation
        date: the origin's revisions in place. One snapshot handle a thread."""
        from concurrent.futures import ThreadPoolExecutor

        from app.plugins.neuris.source import normalized
        local = threading.local()
        candidates = [f for f in files if f.rel_path not in pending
                      and manifestation_key(f.rel_path) in self._index
                      and (Path(collection.corpus_dir) / f.rel_path).is_file()]

        def differs(f: RemoteFile) -> bool:
            zf = getattr(local, "zf", None)
            if zf is None:
                zf = local.zf = zipfile.ZipFile(path)
            try:
                with zipfile.ZipFile(Path(collection.corpus_dir) / f.rel_path) as held:
                    mine = {i.filename: held.read(i) for i in held.infolist() if not i.is_dir()}
                theirs = {i.filename: zf.read(i.filename) for i in self._index[manifestation_key(f.rel_path)]}
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError, KeyError):
                return True                # a held zip unreadable, or the snapshot's: fetched again
            return normalized(mine) != normalized(theirs)
        with ThreadPoolExecutor(max_workers=COMPARE_THREADS, thread_name_prefix="neuris-compare") as pool:
            flags = list(pool.map(differs, candidates))
        return [f.rel_path for f, d in zip(candidates, flags) if d]

    def handles(self, remote: RemoteFile) -> bool:
        key = manifestation_key(remote.rel_path)
        return key is not None and key in self._index

    def fetch(self, endpoint: Endpoint, remote: RemoteFile, dest: Path) -> int:
        """The manifestation's zip at `dest`, written from the snapshot's
        members under their own names (every member's CRC-32 checked on
        read). No byte crosses the network: 0 moved."""
        key = manifestation_key(remote.rel_path)
        infos = self._index.get(key or "")
        if not infos:
            raise TransportError(f"{remote.rel_path}: not in the snapshot")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as out:
                for info in infos:
                    with self._lock:
                        if self._zip is None:
                            raise TransportError("the snapshot was closed during the pass")
                        data = self._zip.read(info)       # raises BadZipFile on a CRC mismatch
                    out.writestr(zipfile.ZipInfo(info.filename, date_time=info.date_time), data)
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError, TransportError) as e:
            # a member that fails its CRC or its inflate (or a snapshot gone mid-pass) is not
            # the origin's bytes: this zip is fetched per law instead
            part.unlink(missing_ok=True)
            self._report(f"{remote.rel_path}: not taken from the snapshot ({type(e).__name__}: {e}); "
                         f"fetched per law"[:400])
            from .transport_https import HttpsTransport
            return HttpsTransport().fetch_one(endpoint, remote, dest, 0)
        os.replace(part, dest)
        with self._lock:
            self._taken += 1
        return 0

    def refetch(self, spec, locator: str, base: Path) -> tuple[Path, int]:
        """records-vs-origin: the record's unit — its manifestation zip and the
        expression's JSON beside it — fetched again PER LAW from the API under
        `base` at its corpus path, never from the snapshot (condition 3)."""
        rel = str(locator).partition("#")[0].strip("/")
        key = manifestation_key(rel)
        if key is None:
            raise FileNotFoundError(f"{rel}: not a NeuRIS manifestation zip (eli/…/deu/<date>.zip)")
        eli, generated = key.rsplit("/", 1)
        out = base / rel
        if out.is_file() and out.with_suffix(".json").is_file():
            return out, 0
        from .transport_https import HttpsTransport
        t = HttpsTransport()
        moved = 0
        for endpoint in spec.endpoints:
            try:
                moved += t.fetch_one(endpoint, RemoteFile(rel_path=rel, remote=f"{LISTING}/{eli}/{generated}.zip"),
                                     out, 0)
                moved += t.fetch_one(endpoint, RemoteFile(rel_path=rel[:-4] + ".json",
                                                          remote=f"{LISTING}/{eli}"),
                                     out.with_suffix(".json"), 0)
            except TransportError:
                continue
            return out, moved
        raise FileNotFoundError(f"{rel}: no endpoint of {spec.source_id} served it")

    def reports(self) -> list[str]:
        with self._lock:
            out, self._reports = self._reports, []
            if self._taken:
                out.append(f"{self._taken} manifestation zip(s) written from the snapshot "
                           f"{self._path.name if self._path else ''}, none asked of the API")
                self._taken = 0
        return out

    def close(self) -> None:
        with self._lock:
            if self._zip is not None:
                self._zip.close()
            self._zip, self._path, self._index = None, None, {}

    # -- the snapshot itself ----------------------------------------------------

    def _report(self, note: str) -> None:
        with self._lock:
            if note not in self._reports:
                self._reports.append(note)
                del self._reports[:-50]

    @staticmethod
    def place(collection) -> Path:
        """The snapshot's directory: the files cache when the node names one
        (app/derived.py, keyed on the corpus), else the collection's state."""
        from app import derived
        got = derived.place_kept(KIND, Path(collection.corpus_dir))
        return got if got is not None else Path(collection.corpus_dir).parent / "state" / KIND

    def _download(self, url: str, collection) -> Path:
        """The snapshot `url` names, in `place`, by the engine's Range
        transport (budget, resumable); a held one of the same name is used;
        the older ones in the place go once the new one is whole."""
        from .transport_https import HttpsTransport
        parts = urlsplit(url)
        name = Path(parts.path).name
        if not name.endswith(".zip"):
            raise TransportError(f"{url}: the snapshot is not a zip")
        where = self.place(collection)
        where.mkdir(parents=True, exist_ok=True)
        dest = where / name
        if not dest.is_file():
            part = dest.with_name(dest.name + ".part")
            endpoint = Endpoint(transport=Transport.HTTPS, url=f"{parts.scheme}://{parts.netloc}")
            remote = RemoteFile(rel_path=name, remote=parts.path.lstrip("/")
                                + (f"?{parts.query}" if parts.query else ""))
            HttpsTransport().fetch_ranged(endpoint, remote, dest,
                                          part.stat().st_size if part.is_file() else 0)
        for old in where.glob("*.zip"):
            if old != dest:
                old.unlink(missing_ok=True)
        return dest

    def _open(self, path: Path) -> None:
        zf = zipfile.ZipFile(path)
        index: dict[str, list[zipfile.ZipInfo]] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            key = member_key(info.filename)
            if key is not None:
                index.setdefault(key, []).append(info)
        with self._lock:
            self._zip, self._path, self._index = zf, path, index


SNAPSHOT = NeurisSnapshot()
