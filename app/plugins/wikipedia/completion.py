"""WIKIMEDIA'S OWN RECORD THAT A DUMP RUN IS COMPLETE — the plugin's
completion check for the update by cadence (docs/notes/collection-updates.md
§1 step 0; app/hold/update.py asks it, generic).

THE STANDARD, never a page scrape: every dated run of every wiki publishes
`<wiki>/<run>/dumpstatus.json`, the dump scheduler's own state of each of
its jobs (`{"jobs": {"<job>": {"status": "done" | "in-progress" | "waiting"
| "failed" | "skipped", "updated": …, "files": {"<name>": {"size", "url",
"md5", "sha1"}, …}}, …}, "version": …}`; wikitech.wikimedia.org,
"Dumps/Dumpstatus"). A run is complete for a slice when the job that writes
each file the collection reads is `done` in its wiki's record: the
multistream dump and its index, and the run's page_props (parser 5's QIDs).
A run whose record is absent has not begun for that wiki.

THE JOB THAT WRITES THE FILE, read off the record (trwiki's 20261101 record,
read 2026-11-17): a wiki dumped in parts writes
`<wiki>-<run>-pages-articles-multistream.xml.bz2`, the one file the
collection reads, in `articlesmultistreamdumprecombine`, after
`articlesmultistreamdump` has written the parts
(`…-multistream1.xml-p1p1500000.bz2`, …). Until 2026-11-17 only
`articlesmultistreamdump` was asked, so such a wiki read as complete before
its file was written. Now the job whose `files` list the name answers and,
where no job lists it yet, the recombine job where the wiki has one.

THE ORIGIN'S CHECKSUM: each listed file's `md5` and `size` are what the
fetched file is held to (`expected`); a mismatch is mended
(app/acquire/repair.py — the multistream dump stream by stream).

Pure: the platform fetches the records (through the transfer budget, with
the origin's etiquette — app/acquire/completion.py) and hands their bytes
here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

#: the jobs whose output the collection reads, as dumpstatus.json names them
JOBS = ("articlesmultistreamdump", "pagepropstable")
#: per file the collection reads (its name after `<wiki>-<run>-`), the jobs
#: that may write it, the one asked first first
WRITERS = (("pages-articles-multistream.xml.bz2", ("articlesmultistreamdumprecombine", "articlesmultistreamdump")),
           ("pages-articles-multistream-index.txt.bz2",
            ("articlesmultistreamdumprecombine", "articlesmultistreamdump")),
           ("page_props.sql.gz", ("pagepropstable",)))


@dataclass(frozen=True)
class Record:
    """One completion record to read: for which held slice, under what
    plain name, and where, relative to the origin's base URL."""
    slice: str
    label: str
    rel_path: str


def _jobs(body: bytes | None) -> dict | None:
    try:
        jobs = json.loads(body).get("jobs") or {}
    except (ValueError, AttributeError, TypeError):
        return None
    return jobs if isinstance(jobs, dict) else None


def _parts(record: Record) -> tuple[str, str]:
    """(wiki, run) of a record's `<wiki>/<run>/dumpstatus.json`."""
    wiki, run = record.rel_path.split("/")[:2]
    return wiki, run


def _owner(jobs: dict, name: str, writers: tuple[str, ...]) -> str:
    """The job that writes `name`: the one whose files list it, else the
    first of `writers` the record has and does not skip."""
    for job, j in jobs.items():
        if isinstance(j, dict) and name in (j.get("files") or {}):
            return job
    for job in writers:
        if isinstance(jobs.get(job), dict) and jobs[job].get("status") != "skipped":
            return job
    return writers[-1]


class DumpStatus:
    """The completion check `WikipediaSource.update_completion` declares."""
    origin = "Wikimedia"
    jobs = JOBS
    #: what a run carries for the collection, in the update's words
    noun = "run"

    def records(self, run: str, slices) -> list[Record]:
        out = []
        for s in sorted(slices):
            key, _, lang = s.partition("=")
            if key != "lang" or not lang:
                raise ValueError(f"{s!r}: a wikipedia slice delivered whole is lang=<code>")
            wiki = f"{lang}wiki"
            out.append(Record(slice=s, label=wiki, rel_path=f"{wiki}/{run}/dumpstatus.json"))
        return out

    def verdict(self, record: Record, body: bytes | None) -> str | None:
        """None when the run is complete for this record's slice; else what
        is not, in plain words."""
        if body is None:
            return f"{record.label}: the run has not begun (no dumpstatus.json)"
        jobs = _jobs(body)
        if jobs is None:
            return f"{record.label}: dumpstatus.json does not read as the dump scheduler's state"
        wiki, run = _parts(record)
        waiting: list[str] = []
        for tail, writers in WRITERS:
            job = _owner(jobs, f"{wiki}-{run}-{tail}", writers)
            status = jobs[job].get("status") if isinstance(jobs.get(job), dict) else None
            said = f"{job} {status or 'not listed'}"
            if status != "done" and said not in waiting:
                waiting.append(said)
        return f"{record.label}: {', '.join(waiting)}" if waiting else None

    def expected(self, run: str, slices, corpus_dir, *, fetch=None) -> list:
        """Each file the collection reads, with the md5 and size its wiki's
        record lists (`repair.Expected`), the records asked again through
        `fetch` (the platform's: the budget, the origin's User-Agent, one at
        a time). A file its record gives no md5 is not held to one."""
        from app.acquire.repair import Expected
        if fetch is None:
            return []
        recs = self.records(run, slices)
        bodies = fetch(recs)
        out = []
        for r in recs:
            jobs = _jobs(bodies.get(r.rel_path)) or {}
            wiki, _ = _parts(r)
            for tail, writers in WRITERS:
                name = f"{wiki}-{run}-{tail}"
                entry = ((jobs.get(_owner(jobs, name, writers)) or {}).get("files") or {}).get(name)
                md5 = entry.get("md5") if isinstance(entry, dict) else None
                if isinstance(md5, str) and len(md5) == 32:
                    size = entry.get("size")
                    out.append(Expected(rel_path=f"{wiki}/{run}/{name}", md5=md5.lower(),
                                        size=size if isinstance(size, int) and size > 0 else None))
        return out
