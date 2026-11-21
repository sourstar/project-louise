"""The acquisition engine is COMMON (operator, 2026-11-08: "The rag engine
must be common … No per exception spaghetti code"). An origin the transports
cannot list or fetch declares an `Inventory` and/or a `UnitFetcher` on its
spec (app/acquire/spec.py); the engine asks them and names no plugin.

- The guard: app/acquire/engine.py and the transports import no plugin's
  acquisition module and name no source id. The one path not yet moved is on
  the allow-list below with its reason; the list only shrinks.
- The protocols, end to end with fakes and no network: an Inventory's files
  are what a pass plans, its reports are logged with the collection, a
  `built_on_request` inventory is never asked a HEAD, and a UnitFetcher's
  unit is landed through the engine's one generic path."""
from __future__ import annotations

import ast
import re
from pathlib import Path

from app import db
from app.acquire import engine, specs
from app.acquire.spec import (AcquisitionSpec, ChecksumSource, DeltaSpec, Endpoint, Filter,
                              RemoteFile, Transport)
from tests.acquire.test_engine_transfer import _collection, _manager

ROOT = Path(__file__).resolve().parents[2]
COMMON = ("app/acquire/engine.py", "app/acquire/transport_https.py", "app/acquire/transport_ftp.py",
          "app/acquire/transport_rsync.py", "app/acquire/transport_hf_hub.py", "app/acquire/transports.py")

#: per-origin modules the common engine may still import, each with its reason
#: and the move that removes it (for the architecture session)
NOT_YET_MOVED = {
    "export_job": "Normattiva's on-request export (2026-10-13): a file built by an API job, adopted "
                  "when the update feed names nothing; to become a UnitFetcher once `fetch` can "
                  "answer 'adopted' and 'deferred' (it is live on the node: moved with a measured pass)",
}


def _imports(rel: str) -> set[str]:
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.level >= 1:
            out |= {a.name for a in n.names} if not n.module else {n.module.split(".")[0]}
        elif isinstance(n, ast.Import):
            out |= {a.name.split(".")[-1] for a in n.names}
    return out


def test_the_common_engine_imports_no_origin_s_own_module():
    per_origin = {p.stem for p in (ROOT / "app" / "acquire").glob("*.py")} - {
        "__init__", "backoff", "budget", "checksum", "claim", "engine", "mirrors", "seeding", "spec", "specs",
        "transports", "transport_https", "transport_ftp", "transport_rsync", "transport_hf_hub",
        "transport_torrent", "torrent_collection", "torrent_landed", "torrent_publish",
        "updatefiles", "zipdelta", "file_origin", "member_origin", "unit_origin"}
    for rel in COMMON:
        bad = (_imports(rel) & per_origin) - set(NOT_YET_MOVED)
        assert not bad, f"{rel} imports {sorted(bad)}: declare an Inventory/UnitFetcher on the spec instead"
    assert set(NOT_YET_MOVED) <= per_origin, "an allow-list entry whose module is gone: remove it"


def test_the_common_engine_names_no_source():
    names = set(specs.ACQUIRE_REGISTRY)
    for rel in COMMON:
        code = "\n".join(l for l in (ROOT / rel).read_text(encoding="utf-8").splitlines()
                         if not l.strip().startswith("#"))
        body = re.sub(r'"""[\s\S]*?"""', "", code)
        hits = sorted(n for n in names if re.search(rf"""["']{re.escape(n)}["']""", body))
        assert not hits, f"{rel} names {hits}: the engine reads what a spec declares, never its id"


class FakeInventory:
    built_on_request = True

    def __init__(self, files):
        self.files, self.asked, self._reports = files, [], ["one expression reported deleted"]

    def list(self, endpoint, spec, profile_filter):
        self.asked.append(profile_filter)
        return list(self.files)

    def reports(self):
        out, self._reports = self._reports, []
        return out


class FakeUnits:
    def __init__(self):
        self.fetched = []

    def handles(self, remote):
        return str(remote.remote or "").startswith("unit:")

    def fetch(self, endpoint, remote, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        (dest.parent / "part.xml").write_bytes(b"<part/>")
        dest.write_bytes(b"<mark/>")                    # the completion mark, last
        self.fetched.append(remote.rel_path)
        return 14

    def refetch(self, spec, locator, base):
        raise NotImplementedError


def _spec(inventory, units=None) -> AcquisitionSpec:
    return AcquisitionSpec(
        source_id="fake", transport=Transport.HTTPS,
        endpoints=(Endpoint(transport=Transport.HTTPS, url="https://origin.invalid", max_conns=1),),
        layout="one-file-per-doc", checksums=ChecksumSource(kind="none"),
        delta=DeltaSpec(mode="full-resync"), profiles={"demo": Filter(include=("a/*",))},
        estimated_bytes={"demo": 1}, inventory=inventory, unit_fetcher=units)


def test_an_inventory_is_what_a_pass_plans_and_its_reports_are_said(tmp_path, monkeypatch):
    files = [RemoteFile(rel_path="a/1/mark.xml", remote="unit:1", mtime=1),
             RemoteFile(rel_path="b/2/mark.xml", remote="unit:2", mtime=1)]
    inv, units = FakeInventory(files), FakeUnits()
    spec = _spec(inv, units)
    _manager(workers=1)
    collection = _collection(tmp_path, plugin="fake")
    con = db.connect(collection)
    heads = []
    monkeypatch.setattr(engine.transport_https.HttpsTransport, "size_of",
                        lambda self, ep, f: heads.append(f) or None)
    out = engine.run_acquisition(con, collection, spec, "demo")
    assert inv.asked and inv.asked[0].include == ("a/*",), "asked for the profile's filter"
    assert units.fetched == ["a/1/mark.xml"], "the profile's file, through the unit fetcher"
    assert out["fetched"] == 1 and heads == [], "built on request: never a HEAD"
    assert (collection.corpus_dir / "a/1/part.xml").is_file()
    row = con.execute("SELECT fetch_done FROM fetch_files WHERE rel_path='a/1/mark.xml'").fetchone()
    assert row["fetch_done"] == 1
    logged = [r["message"] for r in con.execute("SELECT message FROM events")]
    assert any("reported deleted" in m for m in logged), "the inventory's report, said as an event"


# ---- a unit fetcher's prepare: a stop and a bulk that must be used first end the pass --------
# (review 2026-11-21, Q6 and Q7: an unreadable newer bulk was "not prepared", a warning, and the
# pass went on to the origin's per-unit API for everything; a stop never reached a long build)

class PreparingUnits(FakeUnits):
    def __init__(self, raises=None, on_prepare=None):
        super().__init__()
        self.raises, self.on_prepare = raises, on_prepare
        self.seen_stop, self.closed = "unbound", False

    def prepare(self, files, pending, collection, endpoint):
        from app.transfer.manager import current_handle
        handle = current_handle()
        self.seen_stop = getattr(handle, "stop", None) if handle is not None else "unbound"
        if self.on_prepare is not None:
            self.on_prepare()
        if self.raises is not None:
            raise self.raises
        return []

    def close(self):
        self.closed = True


def _pass(tmp_path, units, **kw):
    files = [RemoteFile(rel_path="a/1/mark.xml", remote="unit:1", mtime=1)]
    spec = _spec(FakeInventory(files), units)
    _manager(workers=1)
    collection = _collection(tmp_path, plugin="fake")
    con = db.connect(collection)
    return con, engine.run_acquisition(con, collection, spec, "demo", **kw)


def test_a_bulk_that_must_be_used_first_and_cannot_holds_the_pass_said_never_the_api(tmp_path):
    from app.acquire.spec import HeldBack
    why = "fake: the newer bulk does not read (LEG_MTD.zip: File is not a zip file); bulk first"
    units = PreparingUnits(raises=HeldBack(why))
    con, out = _pass(tmp_path, units)
    assert units.fetched == [] and out["fetched"] == 0 and out["stopped"] == why
    assert units.closed, "what prepare opened, let go"
    warned = [r["message"] for r in con.execute("SELECT message FROM events WHERE level='warn'")]
    assert why in warned
    assert con.execute("SELECT COUNT(*) FROM fetch_files").fetchone()[0] == 0, "nothing recorded against a file"


def test_any_other_failure_of_prepare_still_leaves_the_pass_to_the_transport(tmp_path):
    units = PreparingUnits(raises=RuntimeError("an index that did not open"))
    con, out = _pass(tmp_path, units)
    assert units.fetched == ["a/1/mark.xml"] and out["fetched"] == 1
