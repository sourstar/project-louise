"""The origin as the BYTE RANGES a record's unit is read from, over the
network by Range, compared with the node's copy — what `records-vs-origin`
asks of an origin whose dated run is several container files, one of them
read in part (a MULTISTREAM bzip2 dump: every stream a bzip2 stream of its
own, its offsets in the run's index).

The fifth generic origin, beside member_origin.py (a zip's members),
file_origin.py (a mirror tree), unit_origin.py (a unit fetched again) and
block_origin.py (the blocks of ONE bzip2 stream), and block_origin's method
taken over (operator, 2026-11-18: "A"; "do not reinvent the wheel"): the
copy says which bytes hold a sampled record's unit, those bytes are fetched
from the origin and compared with the copy's, and where they are equal the
record re-parsed from the copy is the record re-parsed from the origin.
Chosen by what the spec and the plugin declare: an HTTPS spec of layout
"container-files", a Codex naming its run, and a plugin that says what a
record reads (`origin_ranges`, plugin_io.OriginRanges). Wikisource is the
first (app/plugins/wikisource/source.py); Wikipedia's dump layer has the
same shape and no hook yet.

WHAT A RECORD READS, as its plugin says:
  - RANGES of a file (a work's pages are in a few streams of the dump):
    each fetched whole by one Range and compared with the copy's same
    bytes. They are the record's UNIT, weighed by their bytes — when the
    check's bound reaches it (`weigh`), since finding them reads the copy;
  - files WHOLE (the run's three tables: gzip, no part of it stands alone):
    SHARED by every record of that wiki, fetched once a check in pieces of
    WHOLE_STEP and compared piece by piece, the first difference ending it.

WHY EQUAL BYTES REPRODUCE THE RECORD FROM THE ORIGIN. A stream of a
multistream dump is a complete bzip2 stream: it decodes from its own bytes
alone, with no state from the streams before it — a stronger case than
block_origin's blocks, which rest on the stream's header and on where the
origin begins a block. So where the origin's bytes over a record's streams
are the copy's, and its tables are the copy's, the unit `origin_hash`
covers — those pages' revisions, then their rows of the tables — is read
from the same bytes whichever side is read. WHAT IS NOT RE-READ: which
pages a work is made of is found in the copy's own index of the run (every
page's title and stream), built from the copy's whole dump, which the
acquisition held to the origin's checksums; a verifier who wanted that
proved now would read the whole dump. block_origin says the same of
wikidata's label table.

IT NEEDS THE COPY. A peer that holds a SHARD and no corpus cannot use it:
it checks against a download of the run (`hold check --origin DIR`), as for
Wikipedia today. THE ALTERNATIVE NOT BUILT, written down
(docs/notes/wikisource-design.md §1): a copy-free reader — the streams and
the tables fetched into a temporary base and the run's page index rebuilt
from the origin's multistream index. It would let a peer check with the
SHARD alone; it needs the facts the multistream index does not carry (a
page's redirect, its content model) read from the fetched streams, and
rests on a content model being a namespace's. It pays when peers are to
check without a copy.

THE OUTCOMES, 03 §3.2's and no other (app/hold/check.py), as block_origin's:
  - every range and every whole file equal: the copy's re-parse, judged by
    `check.verdict`;
  - a range, a whole file or a file's length different: CHANGED AT THE
    ORIGIN, the reason named;
  - an origin that does not answer, answers 404 or 410 (the run no longer
    served) or ignores Range: unreachable, its reason named, and the origin
    is not asked again in that check — a deferral, never a failure;
  - the copy cannot place the record (no copy of the run, a page the run
    does not hold): unreadable, named.

Every byte is drawn from the transfer budget after it arrived
(zipdelta.HttpRangeReader; invariant 5), at most the endpoint's `max_conns`
at once.
"""
from __future__ import annotations

import os
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .spec import AcquisitionSpec, TransferAborted, Transport, TransportError

#: records read ahead of the re-parse, so the fetches run while it works
LOOKAHEAD = 64
#: a whole file is fetched and compared in pieces of this many bytes
WHOLE_STEP = 8 << 20
_SAME = "same"


class OriginBytesChanged(ValueError):
    """The origin's bytes over a record's unit are not the copy's."""


class RangeOrigin:
    """One check's reader of one Codex's dated run of container files:
    bound to the copy (`prepare`), it gives `records-vs-origin` each
    record's unit (`unit`, `weigh`) and its comparisons (`many_with`)."""

    def __init__(self, spec: AcquisitionSpec, codex, plugin, *,
                 reader_for: Callable[[str], Any] | None = None) -> None:
        self.spec, self.codex, self.plugin = spec, codex, plugin
        self.endpoint = spec.endpoints[-1]        # the origin's own base, as the Codex states it
        self.url = str((getattr(codex.origin, "spec", None) or {}).get("url") or self.endpoint.url).rstrip("/")
        self.max_conns = max(1, int(getattr(self.endpoint, "max_conns", 1) or 1))
        self._reader_for = reader_for or self._http_reader
        self.base: Path | None = None             # the copy: the corpus the records were parsed from
        self.run: str | None = None
        self.why_not: str | None = "not prepared"
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        """A CHECK'S OWN: what one check asked of the origin is never another's."""
        self.bytes_read = 0
        self.requests = 0
        self._away: str | None = None             # why the origin is asked no more this check
        self._local = threading.local()
        self._readers: list[Any] = []
        self._reads: dict[str, Any] = {}          # locator -> plugin_io.OriginRanges
        self._ranges: dict[str, list[tuple[Path, int, int]]] = {}
        self._locator_of: dict[str, str] = {}     # unit -> its record's locator (`weigh`)
        self._pieces: dict[tuple, tuple[str, str | None]] = {}    # a range, a file, a length -> its outcome
        self._piece_locks: dict[tuple, threading.Lock] = {}
        self._settled: dict[str, tuple[str, str | None]] = {}     # locator -> its outcome

    # ---- binding ----------------------------------------------------------------

    def _http_reader(self, url: str):
        from . import transport_https, zipdelta
        return zipdelta.HttpRangeReader(url, transport_https.headers_for(self.endpoint), keep_alive=True)

    def _reader(self, rel: str):
        """This thread's Range client of one file: a held connection a
        thread and file, at most `max_conns` threads."""
        held = getattr(self._local, "readers", None)
        if held is None:
            held = self._local.readers = {}
        r = held.get(rel)
        if r is None:
            r = held[rel] = self._reader_for(f"{self.url}/{rel}")
            with self._lock:
                self._readers.append(r)
        return r

    def close(self) -> None:
        """Every held connection closed (the end of a check's comparisons)."""
        with self._lock:
            readers, self._readers = self._readers, []
        for r in readers:
            close = getattr(r, "close", None)
            if callable(close):
                close()
        self._local = threading.local()

    def prepare(self, copy, run: str | None, *, stop: Callable[[], bool] | None = None,
                deadline: float | None = None, log: Callable[[str], None] | None = None) -> str | None:
        """Bound to the copy at `copy` and the snapshot's `run`. None when
        ready; else why not, in plain words (also `why_not`). Nothing is
        built here: what places a record is the plugin's own index of the
        copy, made when the first unit is weighed."""
        self.base, self.run = (Path(copy) if copy else None), run
        self._reset()
        self.why_not = None
        if not run:
            self.why_not = "the snapshot names no run of the origin to read its records in"
        elif self.base is None or not self.base.is_dir():
            self.base = None
            self.why_not = ("no copy of the origin was found on this node (the collection's corpus), and the "
                            "bytes a record is read from are found in the copy — `--origin-else DIR` names it")
        return self.why_not

    # ---- the units ---------------------------------------------------------------

    def _rel(self, path: Path) -> str:
        try:
            return Path(path).relative_to(self.base).as_posix()
        except ValueError:
            return Path(path).as_posix()

    def _read_of(self, locator: str):
        from app import plugin_io
        with self._lock:
            got = self._reads.get(locator)
        if got is None:
            got = plugin_io.origin_ranges(self.plugin, locator, self.base, run=self.run)
            with self._lock:
                self._reads[locator] = got
        return got

    def _ranges_of(self, locator: str) -> list[tuple[Path, int, int]]:
        with self._lock:
            got = self._ranges.get(locator)
        if got is None:
            got = list(self._read_of(locator).ranges())
            with self._lock:
                self._ranges[locator] = got
        return got

    def unit(self, locator: str):
        """`records-vs-origin`'s unit of a record: named by the record's own
        piece of the file read in part, weighed only when the bound reaches
        it (`weigh`), with the files read whole as its shared pieces. A
        record the plugin cannot read here is a unit of its own weighing
        nothing: its comparison says why."""
        from app.plugin_io import OriginReads
        name = str(locator).partition("|")[0]
        with self._lock:
            self._locator_of.setdefault(name, locator)
        if self.base is None:
            return OriginReads(name, 0)
        try:
            reads = self._read_of(locator)
        except Exception:        # noqa: BLE001 — said by the comparison, record by record
            return OriginReads(name, 0)
        shared = tuple((self._rel(p), _size(p)) for p in reads.whole)
        return OriginReads(name, None, shared, reads.group)

    def weigh(self, unit: str) -> int | None:
        """The bytes of a unit's ranges, found in the copy (the plugin reads
        it: a work's pages are found by reading them). 0 where the copy
        cannot place it — its comparison says why."""
        with self._lock:
            locator = self._locator_of.get(unit)
        if locator is None or self.base is None:
            return 0
        try:
            return sum(e - s for _p, s, e in self._ranges_of(locator))
        except Exception:        # noqa: BLE001 — unplaced: taken, and said by its comparison
            return 0

    # ---- the comparisons ----------------------------------------------------------

    def _ask(self, rel: str, start: int, end: int) -> bytes:
        """Bytes [start, end) of the origin's `rel`, by Range; the origin
        made away (`_away`) and FileNotFoundError raised when it cannot answer."""
        from . import backoff, zipdelta
        if self._away is not None:
            raise FileNotFoundError(self._away)
        try:
            data = self._reader(rel).read(start, end - 1)
        except TransferAborted:
            raise
        except zipdelta.RangeRefused as e:
            why = (f"the origin no longer serves run {self.run} ({e.status} to {self.url}/{rel})"
                   if e.status in (404, 410) else f"the origin answered {e.status} to a Range of {rel}")
            self._away = why
            raise FileNotFoundError(why) from None
        except zipdelta.RangeUnsupported:
            self._away = f"the origin does not serve {rel} by Range (it answered with the whole file)"
            raise FileNotFoundError(self._away) from None
        except (TransportError, backoff.Deferred, OSError) as e:
            self._away = f"the origin did not answer ({e})"
            raise FileNotFoundError(self._away) from None
        with self._lock:
            self.bytes_read += len(data)
            self.requests += 1
        if len(data) != end - start:
            self._away = f"the origin gave {len(data):,} bytes for a Range of {end - start:,}"
            raise FileNotFoundError(self._away)
        return data

    def _total(self, rel: str) -> int:
        from . import backoff, zipdelta
        if self._away is not None:
            raise FileNotFoundError(self._away)
        try:
            got = int(self._reader(rel).total())
        except TransferAborted:
            raise
        except zipdelta.RangeRefused as e:
            self._away = (f"the origin no longer serves run {self.run} ({e.status} to {self.url}/{rel})"
                          if e.status in (404, 410) else f"the origin answered {e.status} to a Range of {rel}")
            raise FileNotFoundError(self._away) from None
        except zipdelta.RangeUnsupported:
            self._away = f"the origin does not serve {rel} by Range (it answered with the whole file)"
            raise FileNotFoundError(self._away) from None
        except (TransportError, backoff.Deferred, OSError) as e:
            self._away = f"the origin did not answer ({e})"
            raise FileNotFoundError(self._away) from None
        with self._lock:
            self.bytes_read += 1                  # the length is read off a one-byte Range
            self.requests += 1
        return got

    def _once(self, key: tuple, work: Callable[[], tuple[str, str | None]]) -> tuple[str, str | None]:
        """One piece's outcome, worked out once a check whichever records
        and threads need it."""
        with self._lock:
            got = self._pieces.get(key)
            lock = self._piece_locks.setdefault(key, threading.Lock())
        if got is not None:
            return got
        with lock:
            with self._lock:
                got = self._pieces.get(key)
            if got is None:
                got = work()
                with self._lock:
                    self._pieces[key] = got
        return got

    def _length(self, path: Path) -> tuple[str, str | None]:
        rel = self._rel(path)

        def work():
            try:
                mine = path.stat().st_size
            except OSError as e:
                return "errored", f"the copy's {rel} could not be read: {e}"
            try:
                theirs = self._total(rel)
            except FileNotFoundError as e:
                return "unreachable", str(e)
            if theirs != mine:
                return "changed", (f"the origin's {Path(rel).name} is {theirs:,} bytes and the copy's {mine:,}: "
                                   f"not the file the copy holds")
            return _SAME, None
        return self._once(("length", rel), work)

    def _range(self, path: Path, start: int, end: int) -> tuple[str, str | None]:
        rel = self._rel(path)

        def work():
            try:
                theirs = self._ask(rel, start, end)
            except FileNotFoundError as e:
                return "unreachable", str(e)
            try:
                ours = _pread(path, start, end)
            except OSError as e:
                return "errored", f"the copy's bytes {start:,}-{end - 1:,} of {rel} could not be read: {e}"
            if theirs != ours:
                return "changed", f"the origin's bytes {start:,}-{end - 1:,} of {rel} are not the copy's"
            return _SAME, None
        return self._once(("range", rel, start, end), work)

    def _whole(self, path: Path) -> tuple[str, str | None]:
        rel = self._rel(path)

        def work():
            got = self._length(path)
            if got[0] != _SAME:
                return got
            size = path.stat().st_size
            # NEVER THE WHOLE FILE IN ONE RANGE: an origin may answer a Range
            # that covers the whole representation with 200 and the whole
            # body (RFC 9110 §14.2 lets it), which the Range client reads as
            # an origin that ignores Range. A small file goes in two halves
            step = WHOLE_STEP if size > WHOLE_STEP else max(1, (size + 1) // 2)
            for start in range(0, size, step):
                end = min(size, start + step)
                try:
                    theirs = self._ask(rel, start, end)
                except FileNotFoundError as e:
                    return "unreachable", str(e)
                try:
                    ours = _pread(path, start, end)
                except OSError as e:
                    return "errored", f"the copy's {rel} could not be read: {e}"
                if theirs != ours:
                    return "changed", f"the origin's {rel} is not the copy's (bytes {start:,}-{end - 1:,})"
            return _SAME, None
        return self._once(("whole", rel), work)

    def compare(self, locator: str) -> tuple[str, str | None]:
        """A record's unit at the origin against the copy's: ("same", None),
        ("changed", why), ("unreachable", why), or ("errored", why) where
        the copy cannot place the record or its own bytes cannot be read."""
        with self._lock:
            got = self._settled.get(locator)
        if got is not None:
            return got
        got = self._compare(locator)
        with self._lock:
            self._settled[locator] = got
        return got

    def _compare(self, locator: str) -> tuple[str, str | None]:
        if self.base is None:
            return "unreachable", self.why_not or "the origin cannot be read here"
        try:
            reads = self._read_of(locator)
            ranges = self._ranges_of(locator)
        except FileNotFoundError as e:
            return "unreachable", f"the copy does not hold the run's file: {e}"
        except Exception as e:   # noqa: BLE001 — the copy cannot place it: unreadable, named
            return "errored", f"{type(e).__name__}: {e}"[:200]
        for path in reads.whole:
            got = self._whole(Path(path))
            if got[0] != _SAME:
                return got
        for path in dict.fromkeys(p for p, _s, _e in ranges):
            got = self._length(Path(path))
            if got[0] != _SAME:
                return got
        for path, start, end in ranges:
            got = self._range(Path(path), start, end)
            if got[0] != _SAME:
                return got
        return _SAME, None

    def materialise(self, locator: str) -> Path:
        """`_reparse_from`'s preparation of one record (a check without the
        pool): its unit compared, and the copy returned to re-parse from
        when it is the origin's. `check.OriginAway` when the origin cannot
        answer, its reason said; OriginBytesChanged when its bytes differ."""
        outcome, why = self.compare(locator)
        if outcome == "unreachable":
            from app.hold.check import OriginAway
            raise OriginAway(why)
        if outcome == "errored":
            raise OSError(why)
        if outcome != _SAME:
            raise OriginBytesChanged(why)
        return self.base

    def many_with(self, local_many: Callable[[Any], Any]) -> Callable[[Any], Any]:
        """`Ctx.reparse_many` for this origin: each record's unit compared
        with the origin's (fetched here, under this process's budget, at
        most `max_conns` at once, read ahead of the re-parse), and a record
        whose unit is the origin's re-parsed from the copy by `local_many`
        (the parse pool's); every other record given its outcome here."""
        def many(items):
            side: deque = deque()

            def routed():
                window: deque = deque()
                with ThreadPoolExecutor(max_workers=self.max_conns, thread_name_prefix="origin-ranges") as ex:
                    def settle(row):
                        uid, locator, line, fut = row
                        outcome, why = fut.result()
                        if outcome == _SAME:
                            return uid, locator, line
                        side.append((uid, outcome, None if why is None else f"{uid}: {why}"[:300]))
                        return None

                    for uid, locator, line in items:
                        window.append((uid, locator, line, ex.submit(self.compare, locator)))
                        while window and (len(window) > LOOKAHEAD or window[0][3].done()):
                            got = settle(window.popleft())
                            if got is not None:
                                yield got
                    while window:
                        got = settle(window.popleft())
                        if got is not None:
                            yield got

            try:
                for got in local_many(routed()):
                    while side:
                        yield side.popleft()
                    yield got
                while side:
                    yield side.popleft()
            finally:
                self.close()
        return many


def _size(path) -> int | None:
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def _pread(path: Path, start: int, end: int) -> bytes:
    fd = os.open(path, os.O_RDONLY)
    try:
        return os.pread(fd, end - start, start)
    finally:
        os.close(fd)


def for_codex(codex) -> RangeOrigin | None:
    """The range origin of a Codex whose plugin's spec is a dated run of
    container files over HTTPS, whose `origin.spec` names its run, and whose
    plugin says what a record reads (`origin_ranges`); None for any other."""
    from app.hold import origin_run

    from .specs import ACQUIRE_REGISTRY
    spec = ACQUIRE_REGISTRY.get(getattr(codex, "plugin", None))
    if spec is None or spec.transport is not Transport.HTTPS or spec.layout != "container-files":
        return None
    if not origin_run.is_dated(codex, spec) or origin_run.named_run(codex) is None:
        return None
    plugin = origin_run._plugin_at(codex)
    if plugin is None or not callable(getattr(plugin, "origin_ranges", None)):
        return None
    return RangeOrigin(spec, codex, plugin)
