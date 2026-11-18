"""The origin as the compressed BLOCKS of one bzip2 stream, over the network by
Range — what `records-vs-origin` asks of an origin whose dated dump is ONE
`.bz2` file of one stream (operator, 2026-11-18: "A"). The fourth generic
origin beside member_origin.py (a zip's members), file_origin.py (a mirror
tree) and unit_origin.py (a unit fetched again), chosen by what the spec and
the Codex declare: an HTTPS spec of layout "single-file", a Codex naming its
run (app/hold/origin_run.py), the run's data file a `.bz2`, and a plugin that
frames it one unit a line (`origin_lines`, app/bz2_blocks.py `Lines`).

Wikidata is the case: `wikidata-<RUN>-all.json.bz2`, 103 GB, one stream, no
index the origin publishes. Until this the nightly check had no origin to
ask: it re-parsed the kept copy and said INCONCLUSIVE "kept copy" (München,
2026-11-18: 9,795 records reproduced from the copy, 0 differing).

HOW. The copy's BLOCK INDEX (app/bz2_blocks.py: indexed_bzip2's block
offsets, and where the unit lines begin) says which blocks hold a sampled
record's line — the record's entity, its ordinal through the run's entity
table, then two searches. A record's UNIT is that block range; its blocks are
SHARED PIECES (03 §3.2, amended 2026-11-17, #745), so a block that several
records' ranges hold is fetched and counted once, and the bound
(`check.ORIGIN_BYTES`) counts the bytes a check really fetches. The stream's
4-byte header ("BZh" and the level) is a shared piece of every unit. Each
block's bytes, [⌊start bit / 8⌋, ⌈end bit / 8⌉), are fetched by Range from
the Codex's `origin.spec.url` and the run's file path, through the budgeted
HTTPS client the mend uses (zipdelta.HttpRangeReader: every byte drawn from
the budget after it arrived, invariant 5), with at most the endpoint's
`max_conns` requests at once, and compared with the copy's same bytes.

WHY EQUAL BYTES REPRODUCE THE RECORD FROM THE ORIGIN. A bzip2 block is
decoded from its own bits alone: its 48-bit magic, its CRC, its Huffman
tables and its data. No state passes from one block to the next — the
run-length step before the transform is cut at the block — and the stream
header gives only the block-size level the decoder allows. So where the
origin's bits over a block's span are the copy's, and its header is the
copy's, the origin's bits decode to the copy's bytes there, which hold the
record's line (the index places it; the block's CRC holds the decoding). The
record re-parsed from the copy — the pool's re-parse over the copy, the
kept-copy check's — is then the record re-parsed from the origin's bytes, and
a line that comes back byte for byte is REPRODUCED FROM THE ORIGIN. What is
not re-read is everything before the block: that the origin's stream begins
a block where the copy's does is what the dated run's immutability (the same
URL serves the same bytes, D1), its equal length, its equal header and the
block magic in both at that bit make the only reading — a verifier that
wanted it proved bit by bit would read every earlier block, the whole file.
And the line is textified with the copy's label table (06 §6.4: a verifier
needs the same dump's labels), built from the copy's every line, which the
acquisition held to the origin's own md5 (the run's md5sums); the sampled
blocks prove the record's own unit, the bytes `origin_hash` covers.

THE ALTERNATIVE NOT TAKEN: decode the fetched bytes on their own — shift the
block's bits to follow a fresh `BZh9` at bit 32 and close it as a stream of
one block, as the mend checks a block (app/acquire/repair.py `check_block`)
— and re-parse what comes out. It proves no more: equal bits decode to equal
bytes, so it would decode the copy's bytes again, and it rests on the same
premise (that the origin begins a block there). It would pay only where the
bytes differ, to tell a moved record from a changed one; there the record is
said CHANGED AT THE ORIGIN, the outcome 03 §3.2 gives a record whose origin
unit is no longer the bytes it was parsed from, "neither reproduced nor held
against the record".

THE OUTCOMES, 03 §3.2's and no other (app/hold/check.py):
  - a range equal, header and length equal: the copy's re-parse, judged by
    `check.verdict` — same, moved, changed or failed, exactly as the origin's
    re-parse would be judged, since its bytes are the same;
  - a range, the header or the length different: CHANGED AT THE ORIGIN, the
    reason named (more than a tenth so is INCONCLUSIVE, as for every origin);
  - an origin that does not answer, answers 404 or 410 (the run no longer
    served), or ignores Range: unreachable, its reason named, and the origin
    is not asked again in that check — a deferral, never a failure; all
    unreachable is INCONCLUSIVE with that reason;
  - the copy's own lines not placeable (no index, a framing not the plugin's,
    an entity the run does not hold): unreadable, named — never a failure.
"""
from __future__ import annotations

import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .spec import AcquisitionSpec, TransferAborted, Transport, TransportError

#: records read ahead of the re-parse, so the fetches run while it works
LOOKAHEAD = 512
#: the nightly check builds the copy's block index for at most this long
#: before it re-parses the kept copy instead (app/louise.py `_check_origin`);
#: the next night resumes the build where it stopped
NIGHTLY_BUILD_S = 3600.0
_SAME = "same"


class OriginBlocksChanged(ValueError):
    """The origin's bytes over a record's blocks are not the copy's."""


class BlockOrigin:
    """One check's reader of one Codex's dated single-stream dump: bound to
    the copy (`prepare`), it gives `records-vs-origin` each record's unit
    (`unit`) and its comparisons (`many_with`)."""

    def __init__(self, spec: AcquisitionSpec, codex, plugin, lines, *,
                 reader_for: Callable[[str], Any] | None = None) -> None:
        self.spec, self.codex, self.plugin, self.lines = spec, codex, plugin, lines
        self.endpoint = spec.endpoints[-1]        # the origin's own base, as the Codex states it
        self.url = str(((getattr(codex.origin, "spec", None) or {}).get("url") or self.endpoint.url)).rstrip("/")
        self.max_conns = max(1, int(getattr(self.endpoint, "max_conns", 1) or 1))
        self._reader_for = reader_for or self._http_reader
        self.base: Path | None = None             # the copy: the corpus the records were parsed from
        self.run: str | None = None
        self.rel: str | None = None               # the run's data file, under the origin and the copy
        self.dump: Path | None = None
        self.index = None
        self.why_not: str | None = "not prepared"
        self.bytes_read = 0
        self.requests = 0
        self._lock = threading.Lock()
        self._head_lock = threading.Lock()
        self._local = threading.local()
        self._away: str | None = None             # why the origin is asked no more this check
        self._head: tuple[str, str | None] | None = None
        self._blocks: dict[int, Any] = {}         # block -> its comparison: an Event and a verdict
        self._units: dict[tuple[int, int], Any] = {}
        self._reads: dict[tuple[int, int], Any] = {}
        self._pieces: dict[int, str] = {}

    # ---- binding ----------------------------------------------------------------

    def _http_reader(self, url: str):
        from . import transport_https, zipdelta
        return zipdelta.HttpRangeReader(url, transport_https.headers_for(self.endpoint), keep_alive=True)

    def _reader(self):
        """This thread's Range client: one held connection a thread, at most
        `max_conns` threads."""
        r = getattr(self._local, "reader", None)
        if r is None:
            r = self._local.reader = self._reader_for(f"{self.url}/{self.rel}")
        return r

    def run_file(self, run: str) -> str | None:
        """The run's data file, `rel_path` under the origin's base: the one
        `.bz2` of the run's files the spec's filters admit."""
        from app.hold import origin_run
        try:
            files = origin_run.run_files(self.spec, run, (getattr(self.codex.origin, "spec", None) or {})
                                         .get("filters"))
        except ValueError:
            return None
        got = [f["rel_path"] for f in files if str(f["rel_path"]).endswith(".bz2")]
        return got[0] if len(got) == 1 else None

    def prepare(self, copy, run: str | None, *, stop: Callable[[], bool] | None = None,
                deadline: float | None = None, log: Callable[[str], None] | None = None) -> str | None:
        """Bound to the copy at `copy` and the snapshot's `run`, the copy's
        block index loaded or built (within `deadline`). None when ready;
        else why not, in plain words (also `why_not`). Raises
        `bz2_blocks.Incomplete` when `stop()` stopped the build (its state
        written: the caller exits as a stop does)."""
        from app import bz2_blocks, derived
        self.base, self.run = (Path(copy) if copy else None), run
        self.index, self.why_not, self.dump = None, None, None
        # A CHECK'S OWN: what one check asked of the origin is never another's
        self.bytes_read = self.requests = 0
        self._away, self._head = None, None
        self._blocks, self._units, self._reads, self._pieces = {}, {}, {}, {}
        self._local = threading.local()
        if not run:
            self.why_not = "the snapshot names no run of the origin to read its records in"
        elif self.base is None:
            self.why_not = ("this node keeps no copy of the origin, and the blocks a record is read from are "
                            "found in the copy's block index")
        elif (rel := self.run_file(run)) is None:
            self.why_not = f"run {run} names no single .bz2 file in the plugin's spec"
        else:
            self.rel = rel
            self.dump = self._copy_file(rel)
            if self.dump is None:
                self.why_not = f"the copy holds no {Path(rel).name} (run {run}) under {self.base}"
        if self.why_not is not None:
            return self.why_not
        try:
            self.index = bz2_blocks.ensure(self.dump, self.lines, stop=stop, deadline=deadline, log=log)
        except bz2_blocks.Incomplete as e:
            if e.why == "stopped":
                raise
            self.why_not = str(e)
        except derived.Busy as e:
            self.why_not = f"{e} (its block index)"
        except (bz2_blocks.BlockIndexError, OSError) as e:
            self.why_not = f"{self.dump.name}: {e}"
        else:
            framing = self.index.framed(self.lines)
            if framing is not None:
                self.why_not = f"{self.dump.name}: {framing}, so a record's ordinal cannot be placed in its blocks"
                self.index = None
        return self.why_not

    def _copy_file(self, rel: str) -> Path | None:
        p = self.base / rel
        if p.is_file():
            return p
        from app.plugins import walk
        name = Path(rel).name
        for q in walk.files(self.base, name):
            if q.is_file() and q.name == name:
                return q
        return None

    # ---- the units ---------------------------------------------------------------

    def _piece(self, k: int) -> str:
        p = self._pieces.get(k)
        if p is None:
            p = self._pieces[k] = f"{self.rel}#block {k}"
        return p

    def _reads_of(self, a: int, b: int):
        got = self._reads.get((a, b))
        if got is None:
            from app.plugin_io import OriginReads
            ix = self.index
            pieces = [(f"{self.rel}#header", 4)]
            for k in range(a, b + 1):
                s, e = ix.byte_range(k, k)
                pieces.append((self._piece(k), e - s))
            got = self._reads[(a, b)] = OriginReads(f"{self.rel}#blocks {a}-{b}", 0, tuple(pieces))
        return got

    def blocks_of(self, locator: str) -> tuple[int, int]:
        """(first, last) block of the copy's stream holding the record's line:
        the plugin resolves the record's origin in the run to the copy's file
        and the line's ordinal (wikidata: the entity through the run's
        entity table), the index places the ordinal."""
        from app import plugin_io
        if self.index is None:
            raise FileNotFoundError(self.why_not or "the copy's block index is not loaded")
        path, local = plugin_io.local_locator(self.plugin, locator, self.base, run=self.run)
        if Path(path).name != self.dump.name:
            raise ValueError(f"{locator}: read from {Path(path).name} in run {self.run}, not {self.dump.name}")
        sep = getattr(self.plugin, "LOCATOR_SEP", None) or plugin_io.DEFAULT_MEMBER_SEP
        member = str(local).rpartition(sep)[2]
        if not member.isdigit():
            raise ValueError(f"{locator}: its line in {self.dump.name} is not named by an ordinal ({member!r})")
        return self.index.line_blocks(int(member))

    def unit(self, locator: str):
        """`records-vs-origin`'s unit of a record: its block range, weighing
        nothing of its own; its blocks and the header are pieces it shares
        with every unit that reads them. Without the copy's index the unit
        is the FILE, weighed whole — what a verifier would read, compared in
        part as before (03 §3.2) — and its comparisons say why. A record the
        index cannot place is a unit of its own, weighing nothing: its
        comparison says why."""
        if self.index is None:
            return self._whole()
        try:
            a, b = self.blocks_of(locator)
        except Exception:        # noqa: BLE001 — said by the comparison, record by record
            return f"{self.rel}#{locator}", 0
        return self._reads_of(a, b)

    def _whole(self) -> tuple[str, int]:
        if self.dump is not None:
            try:
                return str(self.rel), self.dump.stat().st_size
            except OSError:
                pass
        est = getattr(self.spec, "estimated_bytes", None) or {}
        return str(self.rel or self.spec.source_id), int(max(est.values(), default=0) or (1 << 40))

    # ---- the comparisons ----------------------------------------------------------

    def _ask(self, start: int, end: int) -> bytes:
        """Bytes [start, end) of the origin's file, by Range; the origin made
        away (`_away`) and FileNotFoundError raised when it cannot answer."""
        from . import backoff, zipdelta
        if self._away is not None:
            raise FileNotFoundError(self._away)
        try:
            data = self._reader().read(start, end - 1)
        except TransferAborted:
            raise
        except zipdelta.RangeRefused as e:
            why = (f"the origin no longer serves run {self.run} ({e.status} to {self.url}/{self.rel})"
                   if e.status in (404, 410) else f"the origin answered {e.status} to a Range of {self.rel}")
            self._away = why
            raise FileNotFoundError(why) from None
        except zipdelta.RangeUnsupported:
            self._away = f"the origin does not serve {self.rel} by Range (it answered with the whole file)"
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

    def _copy_bytes(self, start: int, end: int) -> bytes:
        import os
        fd = os.open(self.dump, os.O_RDONLY)
        try:
            return os.pread(fd, end - start, start)
        finally:
            os.close(fd)

    def _header(self) -> str | None:
        """Once a check: the origin's length and 4-byte header against the
        copy's. None when they agree; else why the origin's file is not the
        copy's (every record then CHANGED AT THE ORIGIN)."""
        with self._head_lock:              # asked once, whichever thread comes first
            return self._header_once()

    def _header_once(self) -> str | None:
        if self._head is not None:
            return self._head[1]
        if self._away is not None:
            raise FileNotFoundError(self._away)
        total = self.index.size
        try:
            got_total = self._reader().total()
        except TransferAborted:
            raise
        except Exception:                               # noqa: BLE001 — the Range below says why
            got_total = None
        head = self._ask(0, 4)
        mine = self.index.header
        why = None
        if got_total is not None and got_total != total:
            why = (f"the origin's {Path(self.rel).name} is {got_total:,} bytes and the copy's {total:,}: not the "
                   f"file the copy holds")
        elif head != mine:
            why = f"the origin's stream header is {head!r} and the copy's {mine!r}"
        self._head = ("header", why)
        return why

    def _block_equal(self, ks: list[int]) -> dict[int, bool]:
        """Each block of `ks` (contiguous) compared once a check: the blocks
        no other comparison has claimed are fetched in one Range, the others
        waited for."""
        mine: list[int] = []
        theirs: list[Any] = []
        with self._lock:
            for k in ks:
                slot = self._blocks.get(k)
                if slot is None:
                    slot = self._blocks[k] = [threading.Event(), None]
                    mine.append(k)
                else:
                    theirs.append((k, slot))
        out: dict[int, bool] = {}
        try:
            runs: list[list[int]] = []
            for k in mine:
                if runs and runs[-1][-1] == k - 1:
                    runs[-1].append(k)
                else:
                    runs.append([k])
            for run in runs:
                s, e = self.index.byte_range(run[0], run[-1])
                theirs_bytes = self._ask(s, e)
                ours = self._copy_bytes(s, e)
                for k in run:
                    ks_, ke = self.index.byte_range(k, k)
                    eq = theirs_bytes[ks_ - s:ke - s] == ours[ks_ - s:ke - s]
                    with self._lock:
                        self._blocks[k][1] = eq
                        self._blocks[k][0].set()
                    out[k] = eq
        except BaseException as exc:
            # a block this call claimed and could not compare: its waiters
            # are told so, and a later comparison may claim it again
            with self._lock:
                for k in mine:
                    slot = self._blocks.get(k)
                    if slot is not None and slot[1] is None:
                        slot[1] = exc
                        slot[0].set()
                        del self._blocks[k]
            raise
        for k, slot in theirs:
            slot[0].wait()
            if isinstance(slot[1], BaseException):
                if isinstance(slot[1], FileNotFoundError):
                    raise FileNotFoundError(str(slot[1]))
                raise slot[1]
            out[k] = bool(slot[1])
        return out

    def compare(self, a: int, b: int) -> tuple[str, str | None]:
        """Blocks a..b of the origin against the copy's: ("same", None), or
        ("changed", why), or ("unreachable", why)."""
        try:
            differs = self._header()
            if differs is not None:
                return "changed", differs
            eq = self._block_equal(list(range(a, b + 1)))
        except FileNotFoundError as e:
            return "unreachable", str(e)
        bad = [k for k in range(a, b + 1) if not eq[k]]
        if not bad:
            return _SAME, None
        s, e = self.index.byte_range(bad[0], bad[-1])
        return "changed", (f"the origin's bytes {s:,}-{e - 1:,} of {self.rel} (block {bad[0]:,}"
                           + (f" to {bad[-1]:,}" if len(bad) > 1 else "") + ") are not the copy's")

    def _unit_future(self, ex: ThreadPoolExecutor, key: tuple[int, int]):
        with self._lock:
            fut = self._units.get(key)
            if fut is None:
                fut = self._units[key] = ex.submit(self.compare, *key)
        return fut

    def materialise(self, locator: str) -> Path:
        """`_reparse_from`'s preparation of one record (image-equivalence, a
        check without the pool): its blocks compared, and the copy returned
        to re-parse from when they are the origin's. FileNotFoundError when
        the origin cannot answer; OriginBlocksChanged when its bytes differ."""
        a, b = self.blocks_of(locator)
        outcome, why = self.compare(a, b)
        if outcome == "unreachable":
            raise FileNotFoundError(why)
        if outcome != _SAME:
            raise OriginBlocksChanged(why)
        return self.base

    def many_with(self, local_many: Callable[[Any], Any]) -> Callable[[Any], Any]:
        """`Ctx.reparse_many` for this origin: each record's blocks compared
        with the origin's (fetched here, under this process's budget, at
        most `max_conns` at once, read ahead of the re-parse), and a record
        whose blocks are the origin's re-parsed from the copy by
        `local_many` (the parse pool's, `louise._reparse_many_from` over the
        copy); every other record given its outcome here."""
        def many(items):
            side: deque = deque()

            def routed():
                window: deque = deque()
                with ThreadPoolExecutor(max_workers=self.max_conns, thread_name_prefix="origin-blocks") as ex:
                    def settle(row):
                        uid, locator, line, fut = row
                        if isinstance(fut, tuple):
                            side.append((uid, *fut))
                            return None
                        outcome, why = fut.result()
                        if outcome == _SAME:
                            return uid, locator, line
                        side.append((uid, outcome, why))
                        return None

                    for uid, locator, line in items:
                        try:
                            key = self.blocks_of(locator)
                        except FileNotFoundError as e:
                            window.append((uid, locator, line, ("unreachable", str(e))))
                        except Exception as e:     # noqa: BLE001 — the copy cannot place it: unreadable
                            window.append((uid, locator, line,
                                           ("errored", f"{uid}: {type(e).__name__}: {e}"[:200])))
                        else:
                            window.append((uid, locator, line, self._unit_future(ex, key)))
                        while window and (len(window) > LOOKAHEAD or isinstance(window[0][3], tuple)
                                          or window[0][3].done()):
                            got = settle(window.popleft())
                            if got is not None:
                                yield got
                    while window:
                        got = settle(window.popleft())
                        if got is not None:
                            yield got

            for got in local_many(routed()):
                while side:
                    yield side.popleft()
                yield got
            while side:
                yield side.popleft()
        return many


def for_codex(codex) -> BlockOrigin | None:
    """The block origin of a Codex whose plugin's spec is a dated single-file
    dump over HTTPS, whose `origin.spec` names its run, whose run's data file
    is a `.bz2`, and whose plugin frames it one unit a line; None for any
    other."""
    from app.hold import origin_run

    from .specs import ACQUIRE_REGISTRY
    spec = ACQUIRE_REGISTRY.get(getattr(codex, "plugin", None))
    if spec is None or spec.transport is not Transport.HTTPS or spec.layout != "single-file":
        return None
    if not origin_run.is_dated(codex, spec) or origin_run.named_run(codex) is None:
        return None
    plugin = origin_run._plugin_at(codex)
    lines = getattr(plugin, "origin_lines", None) if plugin is not None else None
    if lines is None:
        return None
    bo = BlockOrigin(spec, codex, plugin, lines)
    return bo if bo.run_file(origin_run.named_run(codex)) is not None else None
