"""WIKIMEDIA'S OWN RECORD THAT A DUMP RUN IS COMPLETE — the plugin's
completion check for the update by cadence (docs/notes/collection-updates.md
§1 step 0; app/hold/update.py asks it, generic).

THE STANDARD, never a page scrape: every dated run of every wiki publishes
`<wiki>/<run>/dumpstatus.json`, the dump scheduler's own state of each of
its jobs (`{"jobs": {"<job>": {"status": "done" | "in-progress" | "waiting"
| "failed" | "skipped", "updated": …, "files": {…}}, …}, "version": …}`;
wikitech.wikimedia.org, "Dumps/Dumpstatus"). A run is complete for a slice
when the jobs whose files the collection reads are `done` in its wiki's
file: `articlesmultistreamdump` (the multistream dump and its index) and
`pagepropstable` (the run's page_props, parser 5's QIDs). A run whose file
is absent has not begun for that wiki.

Pure: the platform fetches the files (through the transfer budget, with the
origin's etiquette — app/acquire/completion.py) and hands their bytes here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

#: the jobs whose output the collection reads, as dumpstatus.json names them
JOBS = ("articlesmultistreamdump", "pagepropstable")


@dataclass(frozen=True)
class Record:
    """One completion record to read: for which held slice, under what
    plain name, and where, relative to the origin's base URL."""
    slice: str
    label: str
    rel_path: str


class DumpStatus:
    """The completion check `WikipediaSource.update_completion` declares."""
    origin = "Wikimedia"
    jobs = JOBS

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
        try:
            jobs = json.loads(body).get("jobs") or {}
        except (ValueError, AttributeError):
            return f"{record.label}: dumpstatus.json does not read as the dump scheduler's state"
        waiting = []
        for job in self.jobs:
            status = (jobs.get(job) or {}).get("status") if isinstance(jobs, dict) else None
            if status != "done":
                waiting.append(f"{job} {status or 'not listed'}")
        return f"{record.label}: {', '.join(waiting)}" if waiting else None
