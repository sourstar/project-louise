"""NeuRIS's daily snapshot as the transport of a pass's manifestation zips
(app/acquire/neuris_snapshot.py; 06 §6.2 bulk first), against a LOCAL mock of
the API and its object storage only. The legal session's three conditions
(2026-11-14): a zip is taken from the snapshot only for the manifestation the
listing names now (same expression ELI, same generation date); a snapshot
member and a per-law member of one manifestation give one `origin_hash`
across days (parser 3's normalization); records-vs-origin re-fetches per law
from the API, never from the snapshot."""
from __future__ import annotations

import io
import os
import json
import re
import threading
import time
import uuid
import zipfile
from dataclasses import replace
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from app import db
from app.acquire import engine, specs
from app.acquire import neuris_listing as nl
from app.acquire import neuris_snapshot as nsnap
from app.acquire.spec import Endpoint, RemoteFile, Transport
from app.plugins.base import DiscoverState
from app.plugins.neuris import SOURCE
from app.plugins.neuris import source as nsrc
from tests import neuris_support as ns
from tests.acquire.test_engine import _collection
from tests.acquire.test_neuris_listing import NOW, Api, _Handler

EXPRESSIONS = [ns.S1016, *ns.S1234, ns.N130]
SNAP = "legislation_2026-11-14T06:10:30.445433173Z.zip"


def _regenerated(xml: bytes) -> bytes:
    return re.sub(rb'GUID="[^"]*"', lambda m: f'GUID="{uuid.uuid4()}"'.encode(), xml)


def snapshot_bytes(expressions, *, generated: dict | None = None, rerender: bool = False,
                   corrupt: str | None = None) -> bytes:
    """The origin's snapshot: every manifestation's files under their ELI
    path, a `metadaten.xml` per work, members interleaved as the origin's are."""
    buf = io.BytesIO()
    names = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for e in expressions:
            gen, d = ns.directory(e)
            gen = (generated or {}).get(e, gen)
            for p in sorted(d.iterdir()):
                data = p.read_bytes()
                if rerender and p.suffix == ".xml":
                    data = _regenerated(data)
                names.append((f"{e}/{gen}/{p.name}", data))
            work = "/".join(e.split("/")[:5])
            names.append((f"{work}/metadaten.xml", b"<metadaten><vollzitat>x</vollzitat></metadaten>"))
        written = set()
        for name, data in sorted(names, key=lambda t: t[0][::-1]):      # not in path order
            if name in written:
                continue                                                    # one metadaten per work
            written.add(name)
            zf.writestr(name, data)
    raw = buf.getvalue()
    if corrupt is not None:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            info = zf.getinfo(corrupt)
        # flip a byte of that member's compressed data: its CRC no longer holds
        at = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra) + 2
        raw = raw[:at] + bytes([raw[at] ^ 0xFF]) + raw[at + 1:]
    return raw


class _SnapHandler(_Handler):
    def do_GET(self):
        api: Api = self.server.api                    # type: ignore[attr-defined]
        url = urlparse(self.path)
        with api.lock:
            api.hits[url.path] = api.hits.get(url.path, 0) + 1
        if url.path == "/v1/bulk-zip-links":
            name = getattr(api, "snap_name", SNAP)
            content = None if api.snapshot is None else f"{self.server.base}/snapshots/{name}"
            body = {"@type": "DataCatalog", "dataSet": [
                {"@type": "Dataset", "name": "rechtsprechung", "distribution": {"contentUrl": "x"}},
                {"@type": "Dataset", "name": "legislation", "distribution": {"contentUrl": content}}]}
            return self._send(200, json.dumps(body).encode())
        if url.path.startswith("/snapshots/legislation_"):
            data = api.snapshot or b""
            rng = self.headers.get("Range")
            if rng:
                a, b = rng.removeprefix("bytes=").split("-")
                a, b = int(a), (int(b) if b else len(data) - 1)
                chunk = data[a:b + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{a + len(chunk) - 1}/{len(data)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                return self.wfile.write(chunk)
            return self._send(200, data, "application/zip")
        with api.lock:
            api.hits[url.path] -= 1                   # the parent counts it
        return super().do_GET()


@pytest.fixture
def server():
    api = Api(EXPRESSIONS)
    api.snapshot = snapshot_bytes(EXPRESSIONS)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SnapHandler)
    srv.api = api                                     # type: ignore[attr-defined]
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"   # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield api, srv.base
    srv.shutdown()


def _spec(base, snapshot=None):
    listing = nl.NeurisListing(now=lambda: NOW, sleep=lambda s: None)
    return replace(specs.NEURIS_SPEC, endpoints=(Endpoint(transport=Transport.HTTPS, url=base, max_conns=2),),
                   inventory=listing, unit_fetcher=snapshot or nsnap.NeurisSnapshot())


def _members(path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def _events(con):
    return [r[0] for r in con.execute("SELECT message FROM events WHERE stage='acquire'")]


def test_a_full_fetch_writes_every_listed_zip_from_the_snapshot_and_asks_the_api_for_the_json(tmp_path, server):
    api, base = server
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert got["fetched"] == 10 and got["failed"] == 0
    assert api.zips_built == 0, "no manifestation zip asked of the API"
    assert sum(n for p, n in api.hits.items() if p.startswith("/v1/legislation/eli/")) == 5, \
        "the five JSONs, per request"
    for e in EXPRESSIONS:
        gen = ns.directory(e)[0]
        held = _members(collection.corpus_dir / e / f"{gen}.zip")
        assert held == _members(io.BytesIO(ns.zip_bytes(e))), "the API zip's members, names and bytes"
        assert not any(n.endswith("metadaten.xml") for n in held), "no member the API zip lacks"
    refs = list(SOURCE.discover(DiscoverState(corpus_root=collection.corpus_dir)))
    assert len(refs) == 5 and all(r.error is None for r in refs)
    assert any("written from the snapshot" in m for m in _events(con))
    assert not list(nsnap.NeurisSnapshot.place(collection).glob("*.part"))


def test_a_snapshot_member_and_a_per_law_member_give_one_origin_hash_across_days(tmp_path, server):
    """Condition 2: the snapshot was rendered on another day (every GUID new);
    parser 3's normalization makes its zip the same origin unit."""
    api, base = server
    api.snapshot = snapshot_bytes(EXPRESSIONS, rerender=True)
    a = _collection(tmp_path / "a", plugin="neuris")
    engine.run_acquisition(db.connect(a), a, _spec(base), "full")
    b = tmp_path / "b"
    ns.lay_out(b, ns.S1016)                           # the per-law zip of another day
    gen = ns.directory(ns.S1016)[0]
    from_snapshot = _members(a.corpus_dir / ns.S1016 / f"{gen}.zip")
    per_law = _members(b / ns.S1016 / f"{gen}.zip")
    assert from_snapshot != per_law, "the bytes differ: another render"
    body = (b / ns.S1016 / f"{gen}.json").read_bytes()
    assert nsrc.origin_hash(nsrc.normalized(from_snapshot), body) == \
        nsrc.origin_hash(nsrc.normalized(per_law), body)


def test_a_manifestation_the_snapshot_holds_at_another_generation_date_is_fetched_per_law(tmp_path, server):
    api, base = server
    api.snapshot = snapshot_bytes(EXPRESSIONS, generated={ns.N130: "2025-01-01"})
    collection = _collection(tmp_path, plugin="neuris")
    got = engine.run_acquisition(db.connect(collection), collection, _spec(base), "full")
    assert got["fetched"] == 10 and api.zips_built == 1, "N130 at the listing's date, from the API"
    assert (collection.corpus_dir / ns.N130 / f"{ns.directory(ns.N130)[0]}.zip").is_file()


def test_a_daily_delta_is_fetched_per_law_and_the_snapshot_is_not_downloaded(tmp_path, server):
    api, base = server
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    engine.run_acquisition(con, collection, _spec(base), "full")
    links = api.hits.get("/v1/bulk-zip-links", 0)
    api.regenerate(ns.N130, "2026-11-07")
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert got["fetched"] == 2 and api.zips_built == 1, "the one zip the snapshot does not hold: per law"
    assert api.hits.get("/v1/bulk-zip-links", 0) == links + 1, \
        "a law the held snapshot lacks: the link asked whether a newer one is out (operator, A)"
    assert any("the 1 it does not hold, per law" in m for m in _events(con))


def test_a_pending_zip_the_held_snapshot_holds_never_goes_per_law(tmp_path, server):
    """Operator, 2026-11-15: "until the bulk download is not fully used no
    cherry picking is allowed. No exceptions" (Frankfurt's review, item 1: a
    retried failure, or a stopped pass's rest, went to the API under 25 %)."""
    api, base = server
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    engine.run_acquisition(con, collection, _spec(base), "full")
    gen = ns.directory(ns.N130)[0]
    con.execute("UPDATE fetch_files SET fetch_done=0 WHERE rel_path=?", (f"{ns.N130}/{gen}.zip",))
    con.commit()
    built = api.zips_built
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert got["fetched"] == 1 and api.zips_built == built, "one of five pending, from the held snapshot"


def test_no_snapshot_named_means_every_zip_per_law_and_it_is_said(tmp_path, server):
    api, base = server
    api.snapshot = None
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert got["fetched"] == 10 and api.zips_built == 5
    assert any("names no legislation snapshot" in m for m in _events(con))


def test_a_member_that_fails_its_crc_is_fetched_per_law(tmp_path, server):
    api, base = server
    gen = ns.directory(ns.S1016)[0]
    api.snapshot = snapshot_bytes(EXPRESSIONS, corrupt=f"{ns.S1016}/{gen}/regelungstext-1.xml")
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert got["fetched"] == 10 and got["failed"] == 0 and api.zips_built == 1
    assert _members(collection.corpus_dir / ns.S1016 / f"{gen}.zip") == _members(io.BytesIO(ns.zip_bytes(ns.S1016)))
    assert any("not taken from the snapshot" in m for m in _events(con))


def test_records_vs_origin_refetches_per_law_from_the_api_never_the_snapshot(tmp_path, server):
    """Condition 3."""
    api, base = server
    spec = _spec(base)
    gen = ns.directory(ns.N130)[0]
    rel = f"{ns.N130}/{gen}.zip"
    links = api.hits.get("/v1/bulk-zip-links", 0)
    path, moved = spec.unit_fetcher.refetch(spec, rel + "#member", tmp_path / "base")
    assert path == tmp_path / "base" / rel and moved > 0
    assert api.zips_built == 1 and api.hits.get(f"/snapshots/{SNAP}", 0) == 0
    assert api.hits.get("/v1/bulk-zip-links", 0) == links
    assert json.loads(path.with_suffix(".json").read_bytes()) == ns.api_json(ns.N130)
    with pytest.raises(FileNotFoundError):
        spec.unit_fetcher.refetch(spec, "eli/not/a/unit.zip", tmp_path / "base")


def test_the_keys_are_the_origins_form_and_nothing_else():
    gen = ns.directory(ns.S1016)[0]
    assert nsnap.manifestation_key(f"{ns.S1016}/{gen}.zip") == f"{ns.S1016}/{gen}"
    assert nsnap.manifestation_key(f"{ns.S1016}/{gen}.json") is None
    assert nsnap.manifestation_key("eli/bund/x.zip") is None
    assert nsnap.member_key(f"{ns.S1016}/{gen}/regelungstext-1/art-z1.html") == f"{ns.S1016}/{gen}"
    assert nsnap.member_key("eli/bund/bgbl-1/2000/s1016/metadaten.xml") is None
    assert nsnap.member_key(f"{ns.S1016}/{gen}/") is None
    s = nsnap.NeurisSnapshot()
    assert not s.handles(RemoteFile(rel_path=f"{ns.S1016}/{gen}.zip")), "nothing prepared: per law"



# ---- revised in place (2026-11-15) -------------------------------------------------------------------

def _annotated(xml: bytes) -> bytes:
    """What the portal did to every manifestation under the same date (München's
    probe of NeuRIS5): a domainTerm annotation on the abbreviation."""
    return xml.replace(b"<ris:abkuerzung ", b'<ris:abkuerzung domainTerm="Interne Abk\xc3\xbcrzung" ', 1)


def test_a_manifestation_revised_in_place_is_rewritten_from_the_snapshot_at_the_weekly_revisit(tmp_path, server):
    api, base = server
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    engine.run_acquisition(con, collection, _spec(base), "full")
    gen = ns.directory(ns.S1016)[0]
    held = collection.corpus_dir / ns.S1016 / f"{gen}.zip"
    before = _members(held)
    # the origin re-renders (every GUID new: not a revision) and annotates S1016 in place
    revised = snapshot_bytes(EXPRESSIONS, rerender=True)
    import io as _io
    out = _io.BytesIO()
    with zipfile.ZipFile(_io.BytesIO(revised)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename.startswith(f"{ns.S1016}/{gen}/regelungstext-1.xml"):
                data = _annotated(data)
            zout.writestr(info.filename, data)
    api.snapshot = out.getvalue()
    # a quiet pass: nothing pending, and the snapshot compared under a week ago — not taken
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert got["fetched"] == 0 and _members(held) == before
    # a week on: taken, compared, and only the revised one rewritten (the re-render is no revision)
    mark = nsnap.NeurisSnapshot.place(collection) / nsnap.COMPARED
    mark.write_text(json.dumps({"snapshot": "x", "at": 0}))
    for p in nsnap.NeurisSnapshot.place(collection).glob("*.zip"):
        p.unlink()                                 # the API names a newer one: downloaded again
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert got["fetched"] == 1 and api.zips_built == 0, "the one revised zip, from the snapshot"
    assert "Interne Abk" in _members(held)[f"{ns.S1016}/{gen}/regelungstext-1.xml"].decode("utf-8")
    assert any("revised in place" in m and " 1 held manifestation" in m for m in _events(con))



def test_a_snapshot_downloaded_but_never_compared_is_compared_at_the_next_pass(tmp_path, server):
    """2026-11-15: the first fetch on #672 found a snapshot a full fetch had
    downloaded the day before, read its date as "compared today", and revised
    nothing. The comparison has its own mark."""
    api, base = server
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    engine.run_acquisition(con, collection, _spec(base), "full")
    (nsnap.NeurisSnapshot.place(collection) / nsnap.COMPARED).unlink()      # as before #672: none
    assert nsnap.NeurisSnapshot()._revisit_due(collection), "a fresh zip is no comparison"
    engine.run_acquisition(con, collection, _spec(base), "full")
    assert any("revised in place" in m for m in _events(con))
    assert not nsnap.NeurisSnapshot()._revisit_due(collection)



def test_a_newer_snapshot_is_compared_after_a_day_and_the_same_one_is_not(tmp_path, server):
    """2026-11-15: the portal revised after the 11-14 snapshot; its next build
    carries it. A newer snapshot is compared once a day has passed since the
    last comparison; the one already compared is not downloaded again."""
    api, base = server
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    engine.run_acquisition(con, collection, _spec(base), "full")
    mark = nsnap.NeurisSnapshot.place(collection) / nsnap.COMPARED
    snap = nsnap.NeurisSnapshot()
    assert not snap._revisit_due(collection, SNAP), "the same snapshot, just compared"
    assert not snap._revisit_due(collection, "legislation_2026-11-15T23:11:10Z.zip"), "a newer one, under a day"
    mark.write_text(json.dumps({"snapshot": SNAP, "at": time.time() - nsnap.REVISIT_MIN_S - 1}))
    assert snap._revisit_due(collection, "legislation_2026-11-15T23:11:10Z.zip"), "a newer one, a day on"
    assert not snap._revisit_due(collection, SNAP), "the same one, a day on: not before a week"
    mark.write_text(json.dumps({"snapshot": SNAP, "at": time.time() - nsnap.REVISIT_S - 1}))
    assert snap._revisit_due(collection, SNAP), "a week on, whichever"



def test_the_link_is_read_at_most_once_a_day(tmp_path, server):
    """Operator, 2026-11-15: "once a day". Passes under the share read the
    snapshot link only when the last read is a day old."""
    api, base = server
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    engine.run_acquisition(con, collection, _spec(base), "full")
    place = nsnap.NeurisSnapshot.place(collection)
    (place / nsnap.LINK_READ).write_text(json.dumps({"at": time.time()}))
    links = api.hits.get("/v1/bulk-zip-links", 0)
    for _ in range(3):
        engine.run_acquisition(con, collection, _spec(base), "full")
    assert api.hits.get("/v1/bulk-zip-links", 0) == links, "three passes, no read: one a day"
    (place / nsnap.LINK_READ).write_text(json.dumps({"at": time.time() - nsnap.LINK_READ_S - 1}))
    engine.run_acquisition(con, collection, _spec(base), "full")
    engine.run_acquisition(con, collection, _spec(base), "full")
    assert api.hits.get("/v1/bulk-zip-links", 0) == links + 1, "a day on: one read, then a day again"



def test_a_newer_snapshot_holding_a_pending_law_is_peeked_at_by_range_and_taken(tmp_path, server):
    """Operator, 2026-11-15, A (Frankfurt's item 2): before any per-law fetch,
    a newer snapshot's index is read by range, and the snapshot downloaded
    when it holds a pending law; per law only what neither holds."""
    api, base = server
    collection = _collection(tmp_path, plugin="neuris")
    con = db.connect(collection)
    engine.run_acquisition(con, collection, _spec(base), "full")
    (nsnap.NeurisSnapshot.place(collection) / nsnap.LINK_READ).write_text(json.dumps({"at": time.time()}))
    # the origin regenerates N130 and a newer snapshot carries it
    api.regenerate(ns.N130, "2026-11-07")
    api.snapshot = snapshot_bytes(EXPRESSIONS, generated={ns.N130: "2026-11-07"})
    api.snap_name = "legislation_2026-11-15T23:11:10.000000000Z.zip"
    got = engine.run_acquisition(con, collection, _spec(base), "full")
    assert got["fetched"] == 2 and api.zips_built == 0, "N130 from the newer snapshot, not the API"
    assert any("its index read by range; it holds 1 of the 1" in m for m in _events(con))


def test_the_range_reader_reads_a_zip_s_index_without_the_whole(tmp_path, server):
    api, base = server
    big = snapshot_bytes(EXPRESSIONS)
    api.snapshot = big
    f = nsnap.RangeFile(f"{base}/snapshots/{SNAP}")
    with zipfile.ZipFile(f) as zf:
        names = zf.namelist()
    assert names and f.size == len(big)
    assert len(f._blocks) * nsnap.RangeFile.BLOCK >= 0
