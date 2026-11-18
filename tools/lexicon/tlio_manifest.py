"""TLIO's held pages and their manifest, for incremental updates (operator,
2026-11-17: "Keep a track of each page content and date so that we can
quickly make an incremental fetch upon updates"; then "make 2 kind of
updates": a light one and a full scan). docs/notes/historical-languages.md,
"TLIO fetch". Non-commercial, local: nothing here is shared or published.

    python -m tools.lexicon.tlio_manifest RAW --backfill        no network
    python -m tools.lexicon.tlio_manifest RAW --update [--parallel N]
    python -m tools.lexicon.tlio_manifest RAW --update=full [--parallel N]

RAW holds `voci/<id>.htm.gz` (the entries, raw bytes gzipped) and `lists/`
(the 26 lemma lists, any name, gzipped or not). `manifest.sqlite` (WAL) is
written beside them: per entry its status (ok, missing, withdrawn), the
sha256 and size of its raw bytes, the server's Last-Modified and ETag
where known, when it was fetched and last checked, TLIO's own revision
date ("u.r.", ultima revisione) and the editor's date (0.8).

- --backfill: a row for every page held and not yet recorded, from its
  bytes (sha256, size, dates) and its file's mtime as the fetch time. No
  request is sent.
- --update (LIGHT, the default update): the 26 lists and the info page
  read again (~27 requests), each list's hash compared with the last, the
  entries new to the lists fetched, those gone from them marked
  withdrawn. It cannot see an entry revised in place.
  If more than 1 % of the held entries would be withdrawn, it stops and
  changes no entry: a misread list looks exactly like a mass removal.
- --update=full: the same, then every held entry asked with an HTTP
  conditional request (RFC 9110 §13: If-None-Match, If-Modified-Since —
  the standard for exactly this; a page without validators is asked
  If-Modified-Since its fetch time) and refetched only if it changed.
  TLIO replaces an entry in place, so the old page is not kept (invariant
  11: a superseded version the origin no longer serves cannot be checked).

Requests: back to back (no pause while answers come right), `--parallel`
workers; after a failure a pause comes back, from 1 s doubled to at most
60 s, halved after each clean answer (multiplicative increase and decrease,
as TCP's backoff); 20 failures in a row stop the pass. SIGTERM or SIGINT:
the pages in flight end, then exit 130; a re-run resumes. Only the main
thread writes the manifest. This is an operator tool outside app/: it does
not draw from the node's transfer budget (invariant 5 covers app/); run it
under deploy/supervise-pass.sh.
"""
from __future__ import annotations

import argparse
import calendar
import concurrent.futures as cf
import gzip
import hashlib
import os
import re
import signal
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from email.utils import formatdate
from pathlib import Path

BASE = "https://tlio.ovi.cnr.it"
LETTERS = "abcdefghijklmnopqrstuvwxyz"
UA = {"User-Agent": "louise-research (local, non-commercial)"}
#: an entry ("000120") or a cross-reference ("r01519") linked from a list
_ID = re.compile(rb"voci/([a-z]?\d+)\.htm")
#: an update that would withdraw more than this share of the held entries
#: stops, changing nothing: a list misread looks exactly like a mass removal
MAX_WITHDRAWN = 0.01
_UR = re.compile(rb"u\.r\.\s*(\d{2}\.\d{2}\.\d{4})")
_ED = re.compile(rb"0\.8\s*(?:<[^>]+>\s*)*[^<]*?(\d{2}\.\d{2}\.\d{4})")
_INFO = re.compile(rb"Nel [^<]{3,40}? il lemmario conta[^<]*?pubblicate")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, sha256 TEXT, size INTEGER,
    last_modified TEXT, etag TEXT, fetched_at TEXT, checked_at TEXT,
    revised TEXT, editor_date TEXT, changed_at TEXT);
CREATE TABLE IF NOT EXISTS lists (name TEXT PRIMARY KEY, sha256 TEXT, read_at TEXT);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def http_date(iso_ts: str) -> str:
    return formatdate(calendar.timegm(time.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ")), usegmt=True)


def connect(raw: Path) -> sqlite3.Connection:
    raw.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(raw / "manifest.sqlite", isolation_level=None, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def dates_of(page: bytes) -> tuple[str | None, str | None]:
    """(TLIO's last revision "u.r.", the editor's date in 0.8) from the page."""
    ur = _UR.findall(page)
    ed = _ED.search(page)
    return (ur[-1].decode() if ur else None), (ed.group(1).decode() if ed else None)


def read_maybe_gz(p: Path) -> bytes:
    b = p.read_bytes()
    return gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b


def listed_ids(raw: Path) -> list[str]:
    ids: set[str] = set()
    for p in (raw / "lists").glob("*"):
        if p.is_file() and not p.name.startswith("info"):
            ids |= {m.decode() for m in _ID.findall(read_maybe_gz(p))}
    return sorted(ids)


def _row_from_file(p: Path) -> tuple:
    page = gzip.decompress(p.read_bytes())
    ur, ed = dates_of(page)
    t = iso(p.stat().st_mtime)
    return (p.name.split(".")[0], hashlib.sha256(page).hexdigest(), len(page), t, t, ur, ed)


def backfill(raw: Path, db: sqlite3.Connection, workers: int | None = None) -> int:
    """A row for every held page not yet recorded, from its bytes; threads
    for the reading and hashing, the main thread for the writes."""
    have = {r[0] for r in db.execute("SELECT id FROM entries")}
    todo = [p for p in (raw / "voci").glob("*.htm.gz") if p.name.split(".")[0] not in have]
    rows = []
    with cf.ThreadPoolExecutor(workers or os.cpu_count()) as ex:
        rows = list(ex.map(_row_from_file, todo))
    db.execute("BEGIN IMMEDIATE")
    db.executemany("INSERT INTO entries(id,status,sha256,size,fetched_at,checked_at,revised,editor_date)"
                   " VALUES(?,'ok',?,?,?,?,?,?)", rows)
    db.execute("COMMIT")
    return len(rows)


class Client:
    """Back-to-back requests with a shared backoff pause."""

    def __init__(self, base: str = BASE):
        self.base, self.pause, self.lock = base, 0.0, threading.Lock()
        self.fails = 0

    def get(self, path: str, cond: dict | None = None) -> tuple[int, bytes, dict] | None:
        wait = 1.0
        while True:
            with self.lock:
                pause = self.pause
            if pause:
                time.sleep(pause)
            try:
                req = urllib.request.Request(self.base + path, headers={**UA, **(cond or {})})
                with urllib.request.urlopen(req, timeout=60) as r:
                    body, status, headers = r.read(), r.status, dict(r.headers)
                ok = True
            except urllib.error.HTTPError as e:
                if e.code in (304, 404):
                    body, status, headers, ok = b"", e.code, dict(e.headers), True
                else:
                    ok = False
            except Exception:
                ok = False
            with self.lock:
                if ok:
                    self.fails = 0
                    self.pause = self.pause / 2 if self.pause >= 0.2 else 0.0
                else:
                    self.fails += 1
                    self.pause = min(60.0, max(1.0, self.pause * 2))
                    if self.fails >= 20:
                        return None
            if ok:
                return status, body, headers
            time.sleep(min(wait, 60.0)); wait *= 2


def read_lists(raw: Path, db: sqlite3.Connection, client: Client, log=print) -> None:
    """The 26 lists and the info page read again; a changed hash is said."""
    (raw / "lists").mkdir(exist_ok=True)
    for name, path in [("info", "/TLIOlemm/infolemm.htm")] + [(l, f"/TLIOlemm/?q={l}") for l in LETTERS]:
        got = client.get(path)
        if got is None or got[0] != 200:
            raise SystemExit(f"list {name} unreachable: stopped, nothing changed")
        body = got[1]
        sha = hashlib.sha256(body).hexdigest()
        old = db.execute("SELECT sha256 FROM lists WHERE name=?", (name,)).fetchone()
        if old and old[0] != sha:
            log(f"list {name}: changed")
        (raw / "lists" / f"{name}.html.gz").write_bytes(gzip.compress(body))
        db.execute("INSERT OR REPLACE INTO lists VALUES(?,?,?)", (name, sha, now()))
        if name == "info":
            m = _INFO.search(body)
            if m:
                log("info: " + m.group(0).decode("cp1252", "replace"))


def store(raw: Path, db: sqlite3.Connection, eid: str, page: bytes, headers: dict) -> bool:
    """The page kept and its row written; True if its bytes changed."""
    sha = hashlib.sha256(page).hexdigest()
    old = db.execute("SELECT sha256 FROM entries WHERE id=?", (eid,)).fetchone()
    tmp = raw / "voci" / f"{eid}.htm.gz.tmp"
    tmp.write_bytes(gzip.compress(page)); tmp.replace(raw / "voci" / f"{eid}.htm.gz")
    ur, ed = dates_of(page)
    t = now()
    changed = bool(old and old[0] and old[0] != sha)
    db.execute("""INSERT INTO entries(id,status,sha256,size,last_modified,etag,fetched_at,checked_at,revised,editor_date,changed_at)
                  VALUES(?,'ok',?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET status='ok', sha256=excluded.sha256, size=excluded.size,
                  last_modified=excluded.last_modified, etag=excluded.etag, fetched_at=excluded.fetched_at,
                  checked_at=excluded.checked_at, revised=excluded.revised, editor_date=excluded.editor_date,
                  changed_at=CASE WHEN ? THEN excluded.checked_at ELSE entries.changed_at END""",
               (eid, sha, len(page), headers.get("Last-Modified"), headers.get("ETag"), t, t, ur, ed,
                t if changed else None, changed))
    return changed


def update(raw: Path, db: sqlite3.Connection, client: Client, full: bool, parallel: int = 1,
           log=print, stop: dict | None = None, refresh_lists: bool = True) -> dict:
    stop = stop if stop is not None else {"asked": False}
    (raw / "voci").mkdir(exist_ok=True)
    backfill(raw, db)
    if refresh_lists:
        read_lists(raw, db, client, log)
    ids = listed_ids(raw)
    listed = set(ids)
    rows = {r[0]: r for r in db.execute("SELECT id,status,last_modified,etag,fetched_at FROM entries")}
    gone = [i for i, r in rows.items() if r[1] == "ok" and i not in listed]
    held_ok = sum(1 for r in rows.values() if r[1] == "ok")
    if held_ok and len(gone) > MAX_WITHDRAWN * held_ok:
        raise SystemExit(f"{len(gone)} of {held_ok} held entries are not in the lists: stopped, no entry"
                         " changed (a misread list looks like a mass removal; check the lists)")
    if gone:
        db.executemany("UPDATE entries SET status='withdrawn', checked_at=? WHERE id=?", [(now(), i) for i in gone])
        log(f"withdrawn from the lists: {len(gone)}")
    todo = ids if full else [i for i in ids if i not in rows or rows[i][1] in ("withdrawn",)]
    log(f"{len(ids)} listed, {len(todo)} to {'check' if full else 'fetch'} ({'full' if full else 'light'} update)")
    summary = {"listed": len(ids), "withdrawn": len(gone), "new": 0, "changed": 0, "unchanged": 0, "missing": 0}

    def ask(eid: str):
        r = rows.get(eid)
        cond = {}
        if r and r[1] == "ok":
            if r[3]:
                cond["If-None-Match"] = r[3]
            if r[2] or r[4]:
                cond["If-Modified-Since"] = r[2] or http_date(r[4])
        return eid, client.get(f"/voci/{eid}.htm", cond)

    it = iter(todo)
    with cf.ThreadPoolExecutor(parallel) as ex:
        pending = {ex.submit(ask, i) for _, i in zip(range(parallel), it)}
        while pending:
            done, pending = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for f in done:
                eid, got = f.result()
                if got is None:
                    raise SystemExit("20 failures in a row: stopped; a re-run resumes")
                status, body, headers = got
                if status == 304:
                    db.execute("UPDATE entries SET checked_at=? WHERE id=?", (now(), eid)); summary["unchanged"] += 1
                elif status == 404:
                    db.execute("INSERT INTO entries(id,status,checked_at) VALUES(?,'missing',?) ON CONFLICT(id)"
                               " DO UPDATE SET status='missing', checked_at=excluded.checked_at", (eid, now()))
                    summary["missing"] += 1
                else:
                    known = eid in rows and rows[eid][1] == "ok"
                    if store(raw, db, eid, body, headers):
                        summary["changed"] += 1; log(f"{eid}: changed")
                    elif known:
                        summary["unchanged"] += 1
                    else:
                        summary["new"] += 1
                if not stop["asked"]:
                    nxt = next(it, None)
                    if nxt is not None:
                        pending.add(ex.submit(ask, nxt))
    if stop["asked"]:
        log("stopped: a re-run resumes")
        raise SystemExit(130)
    log(", ".join(f"{k} {v}" for k, v in summary.items()))
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("raw", type=Path)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--backfill", action="store_true")
    g.add_argument("--update", nargs="?", const="light", choices=["light", "full"])
    ap.add_argument("--parallel", type=int, default=1)
    a = ap.parse_args(argv)
    db = connect(a.raw)
    if a.backfill:
        print(f"backfill: {backfill(a.raw, db)} page(s) recorded from their bytes, no request sent")
        return 0
    stop = {"asked": False}
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.__setitem__("asked", True))
    update(a.raw, db, Client(), a.update == "full", a.parallel, stop=stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
