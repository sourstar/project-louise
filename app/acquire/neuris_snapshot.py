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

WHEN IT IS USED (operator, 2026-11-15, CLAUDE.md: "until the bulk download is
not fully used no cherry picking is allowed. No exceptions"): ALWAYS FIRST.
Every pending zip the snapshot holds at the listing's generation date is
written from it, whatever share of the listing it is; per law only what no
snapshot holds (a manifestation newer than the snapshot, the per-expression
JSON, which the snapshot does not carry). The held snapshot is used, or a
newer one the API names; the link is read at most once a day (LINK_READ_S),
and a newer snapshot downloaded once (the engine's Range transport, the
budget, resumable). It lives in the files cache (app/derived.py, kind
`neuris-snapshot`, keyed on the corpus), the newest only.

REVISED IN PLACE (2026-11-15, München's probe of NeuRIS5's inconclusive
records-vs-origin): the portal revises manifestations WITHOUT moving their
generation date — it annotated every `ris:abkuerzung` with domainTerm "Interne
Abkürzung" — and the listing, by path, and the test phase's changelog
(`allChanged`) never name such a law again: the corpus fell behind silently and
no record could be verified. So the snapshot is also taken whatever the pass's
pending when the API names a newer one than the last compared (at most once a
day, REVISIT_MIN_S) and at least every REVISIT_S (a week), and every held
manifestation it holds at the listing's generation date is compared with it,
member by member after the plugin's normalization: one that differs is the
origin's revision and is rewritten from the snapshot in the same pass
(`prepare`'s answer, added to the pass by the engine), no request per law.

THE STANDARD, NOT ADOPTED: none for "a bulk as a transport of a per-unit
origin" beyond the mirror (06 §6.2); EUR-Lex's Data Dump (datadump.py) is
the same pattern under another proof.
"""
from __future__ import annotations

import io
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
HTTP_TIMEOUT_S = 60.0
#: the longest a held corpus goes without being compared with the snapshot
#: (REVISED IN PLACE above), whether or not the API names a newer one
REVISIT_S = 7 * 86400
#: a NEWER snapshot than the one last compared is compared once this long has
#: passed since that comparison: at most one download a day. 2026-11-15: the
#: portal annotated every manifestation in place after the 11-14 snapshot was
#: cut, its per-law answers carried it at once and the snapshot only from its
#: next build; a week's wait left every record "changed at the origin"
REVISIT_MIN_S = 86400
#: how often a pass may READ the snapshot link to learn
#: whether a newer snapshot is out (operator, 2026-11-15: "once a day"); the
#: last read is recorded beside the comparison (LINK_READ)
LINK_READ_S = 86400
LINK_READ = "link-read.json"
#: when the held corpus was last compared with a snapshot, beside it in the
#: place: NOT the zip's date, which is its download's (2026-11-15: a snapshot
#: downloaded by a full fetch and never compared read as "compared today")
COMPARED = "compared.json"
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

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._zip: zipfile.ZipFile | None = None
        self._path: Path | None = None
        self._index: dict[str, list[zipfile.ZipInfo]] = {}
        self._taken = 0
        self._reports: list[str] = []

    # -- what the engine asks -------------------------------------------------

    def prepare(self, files: list[RemoteFile], pending: list[RemoteFile], collection,
                endpoint: Endpoint) -> list[str]:
        """Before a pass fetches: THE BULK FIRST, NO EXCEPTIONS (operator,
        2026-11-15: "until the bulk download is not fully used no cherry
        picking is allowed"; CLAUDE.md). Every pending zip the snapshot holds
        at the listing's generation date is written from it, whatever share of
        the listing it is; only what no snapshot holds goes per law. The
        snapshot used is the held one, or a newer one the API names, the link
        read at most once a day (LINK_READ_S) unless none is held. When the
        comparison is due (REVISIT_*), every held manifestation is compared
        and the ones the origin revised in place are answered, for the engine
        to rewrite from the snapshot in this pass. A failure leaves the pass per
        law and is said."""
        want = [f for f in pending if manifestation_key(f.rel_path)]
        self.close()
        held = self._held_zip(collection)
        # what the held snapshot cannot give would go per law: BEFORE that, the
        # API is asked whether a newer snapshot is out (operator, 2026-11-15,
        # Frankfurt's item 2, A: "Peek at a newer snapshot's index … before any
        # per-law fetch, and download it when it holds a pending law"), outside
        # the once-a-day read
        lacking = want if held is None else [f for f in want if manifestation_key(f.rel_path)
                                             not in _held_keys(held)]
        url: str | None = None
        if held is None or lacking or self._link_read_due(collection):
            try:
                url = link(endpoint)
            except TransportError as e:
                self._report(f"snapshot link not read ({e})"[:400])
            else:
                self._mark_link_read(collection)
        named = Path(urlsplit(url).path).name if url else None
        compare = self._revisit_due(collection, named or (held.name if held else None))
        if not want and not compare:
            return []
        newer = bool(url) and held is not None and named != held.name
        take_newer = newer and (compare or (lacking and self._holds_any(url, lacking)))
        try:
            if url and (held is None or take_newer):
                path = self._download(url, collection)
            elif held is not None:
                path = held
            else:
                self._report("snapshot not taken: the API names no legislation snapshot now and none is "
                             "held; fetched per law")
                return []
            self._open(path)
        except (TransportError, OSError, zipfile.BadZipFile) as e:
            self.close()
            self._report(f"snapshot not taken: {type(e).__name__}: {e}; fetched per law"[:400])
            return []
        if want:
            inside = sum(1 for f in want if manifestation_key(f.rel_path) in self._index)
            self._report(f"snapshot {path.name}: {len(self._index)} manifestation(s); {inside} of the "
                         f"{len(want)} zip(s) to fetch are in it with the listing's generation date and "
                         f"written from it; the {len(want) - inside} it does not hold, per law")
        if not compare:
            return []
        revised = self._revised(files, {f.rel_path for f in pending}, collection, path)
        self._mark_compared(collection, path)
        self._report(f"snapshot {path.name}: {len(revised)} held manifestation(s) the origin revised in "
                     f"place (the same generation date), rewritten from it in this pass")
        return revised

    def _holds_any(self, url: str, files: list[RemoteFile]) -> bool:
        """Does the snapshot `url` names hold any of `files`' manifestations?
        Read from its CENTRAL DIRECTORY alone, by HTTP Range (a few MB of the
        631 MB: zipfile over a ranged reader, `RangeFile`), never the whole."""
        try:
            keys = remote_keys(url)
        except (TransportError, OSError, zipfile.BadZipFile) as e:
            self._report(f"the newer snapshot's index not read ({type(e).__name__}: {e}); the held one "
                         f"is used"[:400])
            return False
        hit = sum(1 for f in files if manifestation_key(f.rel_path) in keys)
        self._report(f"{Path(urlsplit(url).path).name}: its index read by range; it holds {hit} of the "
                     f"{len(files)} zip(s) the held snapshot does not")
        return hit > 0

    def _held_zip(self, collection) -> Path | None:
        """The snapshot the place holds, the newest by name (its build's
        time), or None."""
        where = self.place(collection)
        got = sorted(where.glob("legislation_*.zip")) if where.is_dir() else []
        return got[-1] if got else None

    def _revisit_due(self, collection, named: str | None = None) -> bool:
        """The held corpus never compared with a snapshot; last compared
        longer than REVISIT_S ago; or REVISIT_MIN_S ago and the API names
        another snapshot (`named`) than that comparison's (COMPARED, never
        the zip's own date)."""
        import json
        import time
        try:
            mark = json.loads((self.place(collection) / COMPARED).read_text())
            at, against = float(mark["at"]), str(mark.get("snapshot") or "")
        except (OSError, ValueError, KeyError, TypeError):
            return True
        age = time.time() - at
        if age >= REVISIT_S:
            return True
        return bool(named) and named != against and age >= REVISIT_MIN_S

    def _link_read_due(self, collection) -> bool:
        """The snapshot link never read, or last read LINK_READ_S ago."""
        import json
        import time
        try:
            at = float(json.loads((self.place(collection) / LINK_READ).read_text())["at"])
        except (OSError, ValueError, KeyError, TypeError):
            return True
        return time.time() - at >= LINK_READ_S

    def _mark_link_read(self, collection) -> None:
        import json
        import time
        where = self.place(collection)
        where.mkdir(parents=True, exist_ok=True)
        mark = where / LINK_READ
        tmp = mark.with_name(mark.name + ".part")
        tmp.write_text(json.dumps({"at": time.time()}))
        os.replace(tmp, mark)

    def _mark_compared(self, collection, path: Path) -> None:
        import json
        import time
        mark = self.place(collection) / COMPARED
        tmp = mark.with_name(mark.name + ".part")
        tmp.write_text(json.dumps({"snapshot": path.name, "at": time.time()}))
        os.replace(tmp, mark)

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


def _held_keys(path: Path) -> set[str]:
    """The manifestation keys a held snapshot holds (its central directory)."""
    with zipfile.ZipFile(path) as zf:
        return {k for k in (member_key(n) for n in zf.namelist()) if k is not None}


class RangeFile(io.RawIOBase):
    """A remote file read by HTTP Range, seekable, so `zipfile` reads a zip's
    end record and central directory from it and nothing else (the standard
    ZIP layout puts both at the end; RFC 9110 §14 ranges). Every byte drawn
    from the budget (invariant 5). Blocks of BLOCK bytes, kept while open."""
    BLOCK = 1 << 20

    def __init__(self, url: str, *, client: httpx.Client | None = None) -> None:
        self.url = url
        self._own = client is None
        self._c = client or httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=True)
        self._pos = 0
        self._blocks: dict[int, bytes] = {}
        r = self._get(0, 0)
        total = (r.headers.get("content-range") or "").rpartition("/")[2]
        if r.status_code != 206 or not total.isdigit():
            raise TransportError(f"{url}: no byte ranges served ({r.status_code})")
        self.size = int(total)

    def _get(self, a: int, b: int) -> httpx.Response:
        try:
            r = self._c.get(self.url, headers={"Range": f"bytes={a}-{b}"})
        except httpx.HTTPError as e:
            raise TransportError(f"{self.url}: {type(e).__name__}: {e}") from e
        budget.acquire_bytes(len(r.content))
        if r.status_code not in (200, 206):
            raise TransportError(f"{self.url}: {r.status_code} to a range")
        return r

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self._pos = {0: offset, 1: self._pos + offset, 2: self.size + offset}[whence]
        return self._pos

    def read(self, n: int = -1) -> bytes:
        end = self.size if n is None or n < 0 else min(self.size, self._pos + n)
        out = bytearray()
        while self._pos < end:
            i = self._pos // self.BLOCK
            if i not in self._blocks:
                a = i * self.BLOCK
                self._blocks[i] = self._get(a, min(self.size, a + self.BLOCK) - 1).content
            block = self._blocks[i]
            off = self._pos - i * self.BLOCK
            take = block[off: off + (end - self._pos)]
            if not take:
                break
            out += take
            self._pos += len(take)
        return bytes(out)

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)

    def close(self) -> None:
        if self._own:
            self._c.close()
        super().close()


def remote_keys(url: str) -> set[str]:
    """The manifestation keys the snapshot at `url` holds, from its central
    directory read by range."""
    with RangeFile(url) as f, zipfile.ZipFile(f) as zf:
        return {k for k in (member_key(n) for n in zf.namelist()) if k is not None}


SNAPSHOT = NeurisSnapshot()
