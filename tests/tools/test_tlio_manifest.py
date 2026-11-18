"""tools/lexicon/tlio_manifest.py against a local HTTP server (Python's own,
which answers If-Modified-Since with 304): the backfill sends nothing, the
light update fetches only entries new to the lists and marks the gone ones,
the full update refetches only what changed."""
from __future__ import annotations

import functools
import gzip
import http.server
import os
import threading
import time
from pathlib import Path

import pytest

from tools.lexicon import tlio_manifest as tm

PAGE = b"<html><title>ABACO s.m.</title> 0.8 Pietro G. Beltrami 10.03.1998. 1 Arte. [u.r. 16.09.2009]</html>"


@pytest.fixture()
def site(tmp_path: Path):
    root = tmp_path / "site"
    (root / "voci").mkdir(parents=True)
    (root / "TLIOlemm").mkdir()
    (root / "TLIOlemm" / "index.html").write_bytes(
        b"<a href='https://tlio.ovi.cnr.it/voci/000001.htm'> <a href='https://tlio.ovi.cnr.it/voci/000002.htm'>"
        b" <a href='https://tlio.ovi.cnr.it/voci/r01519.htm'>")
    (root / "TLIOlemm" / "infolemm.htm").write_bytes(
        b"<p>Nel maggio 2023 il lemmario conta oltre 58.268 entrate a fronte delle 44.003 voci pubblicate.</p>")
    for i in ("000001", "000002", "r01519"):
        (root / "voci" / f"{i}.htm").write_bytes(PAGE.replace(b"ABACO", i.encode()))
    seen: list[str] = []

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            seen.append(self.path)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


def held(raw: Path, eid: str, page: bytes, mtime: float) -> None:
    (raw / "voci").mkdir(parents=True, exist_ok=True)
    p = raw / "voci" / f"{eid}.htm.gz"
    p.write_bytes(gzip.compress(page))
    os.utime(p, (mtime, mtime))


def test_the_backfill_records_held_pages_from_their_bytes_alone(tmp_path: Path):
    raw = tmp_path / "raw"
    held(raw, "000001", PAGE, 1_763_380_000)
    db = tm.connect(raw)
    assert tm.backfill(raw, db) == 1 and tm.backfill(raw, db) == 0
    row = db.execute("SELECT status,size,fetched_at,revised,editor_date,etag FROM entries").fetchone()
    assert row == ("ok", len(PAGE), tm.iso(1_763_380_000), "16.09.2009", "10.03.1998", None)


def test_the_light_update_fetches_only_what_is_new_and_marks_what_is_gone(tmp_path: Path, site):
    root, base, seen = site
    raw = tmp_path / "raw"
    held(raw, "000001", PAGE.replace(b"ABACO", b"000001"), time.time())
    held(raw, "r01519", PAGE, time.time())                     # a cross-reference, listed
    held(raw, "000009", PAGE, time.time())                     # no longer in the lists
    db = tm.connect(raw)
    tm.MAX_WITHDRAWN, keep = 0.5, tm.MAX_WITHDRAWN             # 1 of 3 held is gone, on purpose
    try:
        s = tm.update(raw, db, tm.Client(base), full=False, log=lambda *_: None)
    finally:
        tm.MAX_WITHDRAWN = keep
    assert s["new"] == 1 and s["withdrawn"] == 1 and s["changed"] == 0
    assert [p for p in seen if p.startswith("/voci/")] == ["/voci/000002.htm"]   # held ones not asked
    st = dict(db.execute("SELECT id,status FROM entries"))
    assert st == {"000001": "ok", "000002": "ok", "r01519": "ok", "000009": "withdrawn"}
    assert db.execute("SELECT last_modified FROM entries WHERE id='000002'").fetchone()[0]
    assert db.execute("SELECT count(*) FROM lists").fetchone()[0] == 27


def test_the_full_update_refetches_only_a_changed_entry(tmp_path: Path, site):
    root, base, seen = site
    raw = tmp_path / "raw"
    db = tm.connect(raw)
    tm.update(raw, db, tm.Client(base), full=False, log=lambda *_: None)
    s = tm.update(raw, db, tm.Client(base), full=True, refresh_lists=False, log=lambda *_: None)
    assert s["unchanged"] == 3 and s["changed"] == 0                         # all 304, r-id included
    time.sleep(1.1)
    (root / "voci" / "000002.htm").write_bytes(PAGE.replace(b"Arte", b"Arte del conto"))
    s = tm.update(raw, db, tm.Client(base), full=True, refresh_lists=False, parallel=2, log=lambda *_: None)
    assert s["changed"] == 1 and s["unchanged"] == 2
    assert db.execute("SELECT changed_at IS NOT NULL FROM entries WHERE id='000002'").fetchone()[0] == 1
    assert b"Arte del conto" in gzip.decompress((raw / "voci" / "000002.htm.gz").read_bytes())


def test_cross_reference_ids_are_listed():
    assert tm._ID.findall(b"voci/000120.htm voci/r01519.htm") == [b"000120", b"r01519"]


def test_an_update_that_would_withdraw_many_held_entries_stops_and_changes_nothing(tmp_path: Path, site):
    root, base, seen = site
    raw = tmp_path / "raw"
    for i in ("000007", "000008", "000009"):
        held(raw, i, PAGE, time.time())                        # none of them in the lists
    db = tm.connect(raw)
    with pytest.raises(SystemExit, match="no entry"):
        tm.update(raw, db, tm.Client(base), full=False, log=lambda *_: None)
    assert set(r[0] for r in db.execute("SELECT status FROM entries")) == {"ok"}
    assert not [p for p in seen if p.startswith("/voci/")]
