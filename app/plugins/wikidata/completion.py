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

So:
- A run is COMPLETE for the collection when its md5sums list
  `wikidata-<R>-all.json.bz2`.
- A sums file not there (404), empty, or listing `all.json.gz` without the
  `.bz2` is a run still being written: waited for.
- A run whose sums list files and none of the full dump carries no full dump
  and never will (`skips`): the pass asks the run before it. The run the
  cutoff names is therefore the last run begun before the cutoff whose sums
  list `all.json.bz2` (02 §2.2's rule for dated dumps, applied to the runs
  that carry this collection's file).
- After the fetch, the dump's md5 is held to that line (`verify`), the
  origin's own checksum. A mismatch is the FETCH failing, never the run: the
  file is removed, its size named, so the next night fetches it again rather
  than adopting it.

Pure, but for `verify`, which reads the fetched file. The platform fetches the
sums (app/acquire/completion.py: the budget, the origin's User-Agent, one
request at a time) and hands their bytes here.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

ENTITIES_DIR = "wikidatawiki/entities"
#: how long after its run began a run listing all.json.gz may still bring all.json.bz2
WRITE_DAYS = 3


def _day(run: str):
    from datetime import date
    return date(int(run[:4]), int(run[4:6]), int(run[6:8]))


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
            return f"{record.label}: the run has published no md5sums yet"
        listed = parse_sums(body)
        if dump_name(run) in listed:
            return None
        if dump_name(run, "gz") in listed:
            return f"{record.label}: all.json.gz is done and all.json.bz2 is still being written"
        if not listed:
            return f"{record.label}: its md5sums list no finished file yet"
        return f"{record.label}: carries no full entity dump"

    def skips(self, record: Record, body: bytes | None, now=None) -> str | None:
        """Why this run will never carry the collection's file, or None: its
        sums list finished files and none of the full dump; or they list
        `all.json.gz` without the `.bz2` more than WRITE_DAYS after the run
        began (20261109 wrote both within five hours), so a run whose `.bz2`
        failed does not hold the update for ever."""
        if body is None:
            return None
        run = self._run_of(record)
        listed = parse_sums(body)
        if not listed or dump_name(run) in listed:
            return None
        if dump_name(run, "gz") in listed:
            if now is None or (now.date() - _day(run)).days <= WRITE_DAYS:
                return None
            return (f"run {run} listed all.json.gz and no all.json.bz2 {WRITE_DAYS} days after it began: "
                    f"its .bz2 is taken as never coming")
        names = ", ".join(sorted(listed)[:3])
        return f"run {run} carries no full entity dump (its md5sums list {names})"

    def verify(self, run: str, slices, corpus_dir) -> list[str]:
        """The fetched dump held to the run's md5sums line: `[]` when it
        matches, else what failed. A mismatch removes the file (its size
        named) so the next fetch takes it again."""
        corpus = Path(corpus_dir)
        sums, dump = corpus / sums_path(run), corpus / ENTITIES_DIR / run / dump_name(run)
        try:
            want = parse_sums(sums.read_bytes()).get(dump_name(run))
        except OSError as e:
            return [f"{sums.name}: not readable after the fetch ({e})"]
        if want is None:
            return [f"{sums.name} lists no {dump_name(run)}: nothing to verify it against"]
        h = hashlib.md5()
        with open(dump, "rb") as f:
            for block in iter(lambda: f.read(8 << 20), b""):
                h.update(block)
        if h.hexdigest() == want:
            return []
        size = dump.stat().st_size
        dump.unlink()
        return [(f"{dump.name}: md5 {h.hexdigest()}, the origin's sums say {want}: the fetch is bad, not the run. "
                 f"The file ({size / 2**30:.1f} GiB) is removed and fetched again at the next night")]
