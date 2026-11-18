"""WIKIMEDIA'S OWN RECORD THAT AN ENTITY DUMP'S RUN IS COMPLETE — the plugin's
completion check for the update by cadence (docs/notes/collection-updates.md
§6; app/hold/update.py asks it, generic, as wikipedia's dumpstatus.json).

THE ORIGIN'S FILE, read from dumps.wikimedia.org (the coordinator's read-only
probe of `wikidatawiki/entities/`, 2026-11-17):
- An entity run publishes no `dumpstatus.json` (`20261109/dumpstatus.json`
  answered 404).
- Its `wikidata-<R>-md5sums.txt` (and `-sha1sums.txt`) is REWRITTEN as each
  file of the run finishes, and lists only the finished ones, in finishing
  order. 20261109: `all.json.gz` was written at 01:29, `all.json.bz2` at 06:01,
  the BETA nt/ttl up to 21:40, the sums at 21:48, listing all six.
- Not every run carries the full dump. 20261111's sums list lexemes and
  truthy only, and that run has no `all.json` at all.

So (operator, 2026-11-17, verbatim: "if the current dump fails, don't use an
old dump (that should have already been integrated thus it's pointless);
retry gently to fetch it with the already agreed policy"):
- A run is COMPLETE for the collection when its md5sums list
  `wikidata-<R>-all.json.bz2`.
- A run whose sums are not there (404), empty, or list `all.json.gz` without
  the `.bz2` is a run still being written: WAITED for, however long it takes
  (the 3-day pass-over is retired). The update asks again at its nightly
  beat (app/hold/update.py `complete_run`); a NEWER run that completes first
  takes its place, and an older one never does.
- A run whose sums list finished files and none of the full dump carries no
  full dump and never will (`skips`: lexemes or truthy only, 20261111): it
  is not a candidate, and the run before it is asked.
- After the fetch, the dump is held to that md5 line (`expected`), the
  origin's own checksum. A mismatch is the FETCH failing, never the run, and
  it is MENDED, never fetched again whole where it can be told apart
  (app/acquire/repair.py: the dump is one lbzip2 stream, mended by its
  blocks).

Pure, but for `expected`, which reads the fetched sums file. The platform
fetches the sums (app/acquire/completion.py: the budget, the origin's
User-Agent, one request at a time) and hands their bytes here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ENTITIES_DIR = "wikidatawiki/entities"


@dataclass(frozen=True)
class Record:
    """One completion record: for which slice, under what name, where under
    the origin's base."""
    slice: str
    label: str
    rel_path: str


def sums_path(run: str) -> str:
    return f"{ENTITIES_DIR}/{run}/wikidata-{run}-md5sums.txt"


def dump_name(run: str, z: str = "bz2") -> str:
    return f"wikidata-{run}-all.json.{z}"


def parse_sums(body: bytes) -> dict[str, str]:
    """`{file name: md5}` of an md5sums file (`md5sum`'s `<hex>  <name>`, a
    `*` before a binary-mode name)."""
    out: dict[str, str] = {}
    for raw in body.decode("utf-8", errors="replace").splitlines():
        parts = raw.strip().split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 32:
            out[parts[1].lstrip("*").strip()] = parts[0].lower()
    return out


class Md5Sums:
    """The completion check `WikidataSource.update_completion` declares."""
    origin = "Wikimedia"
    #: what a run carries for the collection, in the update's words
    noun = "full dump"

    def records(self, run: str, slices) -> list[Record]:
        """One record whatever the slices: one file holds every family."""
        return [Record(slice="all", label=f"wikidata {run}", rel_path=sums_path(run))]

    @staticmethod
    def _run_of(record: Record) -> str:
        return record.rel_path.split("/")[-2]

    def verdict(self, record: Record, body: bytes | None) -> str | None:
        """None when the run is complete; else what is not, in plain words."""
        run = self._run_of(record)
        if body is None:
            return "no file finished yet (its md5sums are not published)"
        listed = parse_sums(body)
        if dump_name(run) in listed:
            return None
        if dump_name(run, "gz") in listed:
            return ".json.gz done, .bz2 not yet"
        if not listed:
            return "no file finished yet (its md5sums list none)"
        return "carries no full entity dump"

    def skips(self, record: Record, body: bytes | None, now=None) -> str | None:
        """Why this run will never carry the collection's file, or None: its
        sums list finished files and none of the full dump, in either form.
        A run that lists `all.json.gz` is WRITING the dump, and a run with
        no sums yet may be: both are waited for, never passed over."""
        if body is None:
            return None
        run = self._run_of(record)
        listed = parse_sums(body)
        if not listed or dump_name(run) in listed or dump_name(run, "gz") in listed:
            return None
        names = ", ".join(sorted(listed)[:3])
        return f"run {run} carries no full entity dump (its md5sums list {names})"

    def expected(self, run: str, slices, corpus_dir, *, fetch=None) -> list:
        """The fetched dump and its line in the run's md5sums, the origin's
        own checksum (`repair.Expected`; the sums give no length — the
        origin's Range answer does). Raises ValueError when the sums cannot
        be read or do not list the dump."""
        from app.acquire.repair import Expected
        sums = Path(corpus_dir) / sums_path(run)
        try:
            want = parse_sums(sums.read_bytes()).get(dump_name(run))
        except OSError as e:
            raise ValueError(f"{sums.name}: not readable after the fetch ({e})") from None
        if want is None:
            raise ValueError(f"{sums.name} lists no {dump_name(run)}: nothing to hold it to")
        return [Expected(rel_path=f"{ENTITIES_DIR}/{run}/{dump_name(run)}", md5=want)]
