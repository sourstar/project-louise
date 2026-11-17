"""The update by cadence, end to end on a fake origin (app/hold/update.py;
docs/notes/collection-updates.md §1–§3). A wikipedia collection built from
its Codex's first run takes the origin's next complete run: fetched through
the engine from a loopback origin (no network), read under the same
collection (02 §2.7), built carrying every unchanged line, frozen into the
next snapshot with `origin_run`, and its old run deleted only once that
snapshot is published (06 §6.2). Each step resumes from the marker."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import collections as collections_mod
from app import db, discovery
from app.acquire import specs
from app.acquire.spec import Endpoint, Transport
from app.hold import records as rec
from app.hold import snapshot as sn
from app.hold import update, update_state
from app.pack import build as pack_build
from app.pack import records as pack_records
from app.plugins.wikipedia import archive
from app.plugins.wikipedia import source as wsrc
from app.plugins.wikipedia.ids import native_id_of
from tests.acquire.mock_http_server import MockServer
from tests.hold import wiki_runs
from tests.hold.conftest import KEY, PLATFORM

NOW = datetime(2026, 12, 2, 3, 0, tzinfo=timezone.utc)      # after 2026-11's cutoff, 2026-12-01
RUNS = ("20261201", "20261101", "20261001")                 # 20261201 is not before that cutoff
NEW = "20261101"
FIRST_PAGES = [(20, "Cheese", "Cheese is a dairy product."), (21, "Bread", "Bread is baked."),
               (24, "Salt", "Salt is a mineral.")]
NEW_PAGES = [(20, "Cheese", "Cheese is a dairy product made from milk."), (22, "Wine", "Wine is fermented."),
             (24, "Salt", "Salt is a mineral.")]


def uid(pid: int) -> str:
    return f"wikipedia:{native_id_of('tr', pid)}"


def status(done: bool = True) -> bytes:
    state = "done" if done else "in-progress"
    return json.dumps({"jobs": {"articlesmultistreamdump": {"status": state},
                                "pagepropstable": {"status": "done"},
                                "xmlstubsdump": {"status": "done"}}, "version": "0.8"}).encode()


@dataclasses.dataclass
class Node:
    root: Path
    corpus: Path
    origin: MockServer

    def publish(self, run: str, pages, *, done: bool = True) -> None:
        wiki_runs.dump(self.origin.root, run, pages)
        (self.origin.root / "trwiki" / run / "dumpstatus.json").write_bytes(status(done))

    def pass_(self, **kw) -> update.Outcome:
        kw.setdefault("now", NOW)
        kw.setdefault("fetch_runs", lambda spec: RUNS)
        kw.setdefault("log", lambda m: None)
        return update.run(self.root, "wikipedia", **kw)

    def marker(self) -> dict:
        return update_state.read(self.root, "wikipedia")

    def snapshot(self, sid: str) -> sn.Snapshot:
        return sn.load(self.root / "wikipedia" / "shard" / "snapshots" / f"{sid}.json")

    def lines(self, snap: sn.Snapshot) -> dict[str, dict]:
        out = {}
        for s in snap.shards:
            for r in pack_records.iter_records(self.root / "wikipedia" / "shard" / s.path):
                out[r["uid"]] = r
        return out


@pytest.fixture
def node(isolated_config, data_dir, config_dir, monkeypatch, tmp_path):
    """wikipedia at the plugin's parser (6: each origin names its page, so the
    pass runs; operator, 2026-11-17, "Choice B")."""
    yield from _node(data_dir, config_dir, monkeypatch, tmp_path)


@pytest.fixture
def node5(isolated_config, data_dir, config_dir, monkeypatch, tmp_path):
    """wikipedia whose Codex names parser 5: each origin names the dated file
    and byte range, so the pass refuses it until it is re-created."""
    yield from _node(data_dir, config_dir, monkeypatch, tmp_path, parser=5)


def _node(data_dir, config_dir, monkeypatch, tmp_path, *, parser: int | None = None):
    for cache in (wsrc._stream_cache, archive._groups, archive._layouts):
        cache.clear()
    monkeypatch.setenv("LOUISE_PLATFORM_IMAGE", PLATFORM)
    origin = MockServer(tmp_path / "wikimedia")
    real = specs.ACQUIRE_REGISTRY["wikipedia"]
    monkeypatch.setitem(specs.ACQUIRE_REGISTRY, "wikipedia", dataclasses.replace(
        real, endpoints=(Endpoint(transport=Transport.HTTPS, url=origin.base_url, max_conns=2),),
        snapshot_index=lambda: RUNS, estimated_bytes={**real.estimated_bytes, "tr": 50_000}))
    root, _cx = wiki_runs.node_collection(data_dir, config_dir, parser=parser)
    corpus = root / "wikipedia" / "origin"
    # 2026-10, as the node holds it: built from the Codex's first run
    wiki_runs.dump(corpus, wiki_runs.FIRST, FIRST_PAGES)
    c = collections_mod.by_name("wikipedia")
    con = db.connect(c)
    try:
        discovery.sync_collection(con, c)
    finally:
        con.close()
    rec.build(root, "wikipedia", corpus_dir=corpus, snapshot_id="2026-10", log=lambda *_: None)
    sn.create(root, "wikipedia", snapshot_id="2026-10", log=lambda *_: None)
    yield Node(root, corpus, origin)
    origin.shutdown()


def test_the_first_snapshot_names_the_codexs_run(node):
    assert node.snapshot("2026-10").origin_run == wiki_runs.FIRST
    raw = json.loads((node.root / "wikipedia" / "shard" / "snapshots" / "2026-10.json").read_bytes())
    assert raw["origin_run"] == wiki_runs.FIRST


def test_the_dry_run_says_what_it_would_fetch_and_writes_nothing(node):
    node.publish(NEW, NEW_PAGES)
    got = node.pass_(dry_run=True)
    assert got.state == "dry-run" and got.exit == 0
    assert (got.period, got.run) == ("2026-11", NEW)
    fetch = got.plan["fetch"]
    assert fetch["files_pending"] == fetch["files"] == 3 and fetch["slices"] == ["lang=tr"]
    assert fetch["bytes_estimated"] == 50_000 and fetch["need"] == 55_000
    assert "update 2026-11 from Wikimedia's 2026-11-01 run: fetch 3 of 3 file(s) of 1 slice(s)" in got.says
    assert node.marker() is None, "a dry run writes no marker"
    assert not (node.corpus / "trwiki" / NEW).exists(), "and moves no byte"


def test_not_due_before_the_cutoff(node):
    got = node.pass_(now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    assert got.state == "not-due" and got.exit == 0
    assert "the next update, 2026-11, after 2026-12-01 00:00 UTC" in got.says
    assert node.origin.request_counts == {}, "nothing asked of the origin before the cutoff"


def test_an_incomplete_run_is_waited_for_never_fetched(node):
    node.publish(NEW, NEW_PAGES, done=False)
    got = node.pass_()
    assert got.state == "waiting" and got.exit == update.EXIT_WAITING
    assert got.says == ("waiting for Wikimedia's 2026-11-01 run to complete: "
                        "trwiki: articlesmultistreamdump in-progress")
    assert node.marker()["step"] == "due"
    assert node.origin.request_counts == {"trwiki/20261101/dumpstatus.json": 1}
    assert node.origin.last_user_agent["trwiki/20261101/dumpstatus.json"].startswith("louise-rag/"), \
        "Wikimedia's User-Agent policy: a product string, never the library's default"
    shown = update.describe(node.root, "wikipedia")
    assert shown["says"] == ("update 2026-11: waiting for Wikimedia's 2026-11-01 run to complete: "
                             "trwiki: articlesmultistreamdump in-progress")


def test_the_whole_update(node, monkeypatch):
    before = node.lines(node.snapshot("2026-10"))     # read now: a re-issued part keeps its path
    node.publish(NEW, NEW_PAGES)
    parsed: list[str] = []
    real = pack_build._record_of
    monkeypatch.setattr(pack_build, "_record_of",
                        lambda c, p, row: parsed.append(row["uid"]) or real(c, p, row))
    got = node.pass_()
    # unsigned here (no node key): never published, so the old run stays
    assert got.state == "waiting" and got.step == "old-run"
    assert got.says.startswith("waiting for snapshot 2026-11 to be published")
    mk = node.marker()
    assert mk["period"] == "2026-11" and mk["run"] == NEW and mk["moved"] is True
    assert mk["from"] == {"snapshot": "2026-10", "run": wiki_runs.FIRST}
    for step in ("due", "fetch", "run", "records", "snapshot", "embed"):
        rec_ = mk["steps"][step]
        assert rec_["start"] <= rec_["end"] and isinstance(rec_["counts"], dict), step
    assert mk["steps"]["fetch"]["counts"]["fetched"] == 3
    assert mk["steps"]["run"]["counts"]["changed"] == 1 and mk["steps"]["run"]["counts"]["added"] == 1

    snap = node.snapshot("2026-11")
    assert snap.origin_run == NEW and snap.collection_id == node.snapshot("2026-10").collection_id, \
        "the same collection follows its origin's run (02 §2.7)"
    assert snap.previous == node.snapshot("2026-10").hash
    assert set(snap.delta.added) == {uid(22)} and set(snap.delta.changed) == {uid(20)}
    assert [(t.uid, t.reason) for t in snap.delta.tombstoned] == [(uid(21), "withdrawn")]
    lines = node.lines(snap)
    assert set(lines) == {uid(20), uid(22), uid(24)}, "nothing is built from the old run's files"
    # EACH ORIGIN NAMES ITS PAGE, read in the run the snapshot names (09 §9.5
    # amended 2026-11-17): no line says which run, and the unchanged page's
    # line is the very bytes it was in the run before
    assert all(r["origin"].startswith("trwiki/pages-articles-multistream.xml.bz2#") for r in lines.values())
    assert not any("/20261" in r["origin"] for r in lines.values())
    assert pack_records.canonical_line(lines[uid(24)]) == pack_records.canonical_line(before[uid(24)]), \
        "an unchanged page's line is byte-identical across two runs"
    assert lines[uid(20)]["origin"] == before[uid(20)]["origin"] and \
        lines[uid(20)]["origin_hash"] != before[uid(20)]["origin_hash"], "a changed page: its bytes, not its name"

    # THE UNCHANGED PAGE WAS CARRIED, never parsed — and its line is the
    # line a parse of the new run writes, byte for byte (RULE 1)
    assert sorted(set(parsed)) == sorted({uid(20), uid(22)})
    c = collections_mod.by_name("wikipedia")
    con = db.connect(c)
    try:
        row = dict(con.execute("SELECT uid, native_id, path, lang FROM documents WHERE uid=?",
                               (uid(24),)).fetchone())
    finally:
        con.close()
    by_the_book = real(c, c.plugin_instance(), row)[0]
    assert pack_records.canonical_line(by_the_book) == pack_records.canonical_line(lines[uid(24)])

    assert update.describe(node.root, "wikipedia")["says"].startswith(
        "update 2026-11: waiting for snapshot 2026-11 to be published")
    old = node.corpus / "trwiki" / wiki_runs.FIRST
    assert old.is_dir(), "the old run stays until the snapshot is published"

    # published: the old run leaves the corpus, and the update is done
    sn.sign_snapshot(node.root, "wikipedia", snap, KEY, now="2026-12-02T04:00:00Z")
    got = node.pass_()
    assert got.state == "done" and got.exit == 0, got.says
    assert not old.exists()
    assert (node.corpus / "trwiki" / NEW).is_dir()
    mk = node.marker()
    assert mk["step"] == "done" and mk["steps"]["old-run"]["counts"]["files"] == 3
    assert "records changed" in update.describe(node.root, "wikipedia")["says"]
    assert "3 records changed" in got.says
    con = db.connect(c)
    try:
        paths = [r[0] for r in con.execute("SELECT rel_path FROM fetch_files")]
    finally:
        con.close()
    assert paths and all(f"/{NEW}/" in p for p in paths), "the old run's bookkeeping went with it"
    assert collections_mod.by_name("wikipedia").snapshot == NEW
    # once more: nothing is due until the next cutoff, and the origin is not asked
    asked = dict(node.origin.request_counts)
    assert node.pass_().state == "not-due" and node.origin.request_counts == asked


def test_the_old_run_waits_for_the_annotations_cascade(node, monkeypatch):
    from app.hold import cascade
    node.publish(NEW, NEW_PAGES)
    node.pass_()
    sn.sign_snapshot(node.root, "wikipedia", node.snapshot("2026-11"), KEY, now="2026-12-02T04:00:00Z")
    monkeypatch.setattr(cascade, "annotators", lambda root, cid: ["wikipedia-versions"])
    got = node.pass_()
    assert got.state == "waiting" and "wikipedia-versions not woken yet" in got.says
    assert (node.corpus / "trwiki" / wiki_runs.FIRST).is_dir()
    woken = node.root / "wikipedia-versions" / "state" / "woken.json"
    woken.parent.mkdir(parents=True)
    cid = node.snapshot("2026-11").collection_id
    woken.write_text(json.dumps({"sources": {cid: "2026-11"}, "state": "running", "started": None,
                                 "finished": None, "error": None, "tries": 0}))
    assert node.pass_().state == "done"
    assert not (node.corpus / "trwiki" / wiki_runs.FIRST).exists()


def test_a_failed_step_is_said_retried_next_night_three_times_then_waits(node, monkeypatch):
    node.publish(NEW, NEW_PAGES)
    real_build = rec.build
    monkeypatch.setattr(rec, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk on fire")))
    got = node.pass_()
    assert got.state == "failed" and got.exit == update.EXIT_FAILED and got.step == "records"
    assert "step records failed (1 of 3): RuntimeError: disk on fire" == got.says
    assert update.describe(node.root, "wikipedia")["says"] == (
        "update 2026-11: step records failed — RuntimeError: disk on fire; tried again next night (1 of 3)")
    later_tonight = node.pass_(now=NOW.replace(hour=5))
    assert later_tonight.state == "waiting" and "failed tonight" in later_tonight.says
    fetched = dict(node.origin.request_counts)
    for night in (3, 4):
        assert node.pass_(now=NOW.replace(day=night)).state == "failed"
    assert node.marker()["failure"]["tries"] == 3
    got = node.pass_(now=NOW.replace(day=5))
    assert got.state == "refused" and got.exit == update.EXIT_REFUSED and "waits for a human" in got.says
    # fixed, and released by a human: it resumes AT records — nothing fetched again
    monkeypatch.setattr(rec, "build", real_build)
    got = node.pass_(now=NOW.replace(day=5), retry=True)
    assert got.step == "old-run", got.says
    assert node.origin.request_counts == fetched, "a resumed pass never fetches what it holds"


def test_a_stop_resumes_at_the_step_it_stopped_in(node, monkeypatch):
    import threading
    node.publish(NEW, NEW_PAGES)
    stop = threading.Event()
    real = discovery.sync_collection

    def scan_then_stop(con, c, **kw):
        stop.set()
        return {**real(con, c, stop=None), "interrupted": True}
    monkeypatch.setattr(discovery, "sync_collection", scan_then_stop)
    got = node.pass_(stop=stop)
    assert got.state == "stopped" and got.exit == update.EXIT_STOPPED and got.step == "run"
    assert node.marker()["moved"] is True, "the run moved before its scan began"
    assert collections_mod.by_name("wikipedia").snapshot == NEW
    monkeypatch.setattr(discovery, "sync_collection", real)
    assert node.pass_().step == "old-run"
    assert node.snapshot("2026-11").origin_run == NEW


def test_the_node_holding_the_collection_makes_the_pass_wait_not_fail(node):
    from app.hold import lock as lock_mod
    node.publish(NEW, NEW_PAGES)
    held = lock_mod.take(lock_mod.state_dir(node.root, "wikipedia"), "wikipedia", "the node (worker)")
    try:
        got = node.pass_()
    finally:
        held.release()
    assert got.state == "waiting" and got.step == "run"
    assert "the node scans run 20261101 itself" in got.says
    assert node.marker()["failure"] is None, "waiting is not failing"


def test_a_collection_whose_plugin_declares_no_completion_check_is_not_updated(isolated_config, data_dir):
    got = update.run(data_dir / "collections", "gutenberg", now=NOW, log=lambda m: None)
    assert got.state == "refused" and got.exit == update.EXIT_REFUSED
    assert "not updated by this pass" in got.says and "dated dumps" in got.says


def test_the_command_line_exits_as_supervise_pass_reads_it(node, capsys):
    from app import louise
    node.publish(NEW, NEW_PAGES, done=False)
    rc = louise.main(["hold", "collection", "update", "wikipedia", "--root", str(node.root),
                      "--dry-run", "--json"])
    # the dry run asks the real clock: before or after the cutoff, never an error
    assert rc in (0, 3)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["state"] in ("dry-run", "waiting")


def test_the_loader_reads_a_snapshots_run_once_however_often_it_loads(node, monkeypatch):
    """A cost test (scheduling.md §6 item 7): the worker loads every
    collection every round, and the current run reads the newest snapshot."""
    from app.hold import origin_run
    reads = []
    real = origin_run._tail_run
    monkeypatch.setattr(origin_run, "_tail_run", lambda p: reads.append(p) or real(p))
    origin_run._RUNS.clear()
    for _ in range(25):
        assert collections_mod.by_name("wikipedia").snapshot == wiki_runs.FIRST
    assert len(reads) == 1


def test_a_collection_whose_origin_names_the_run_is_planned_and_never_run(node5, monkeypatch, capsys):
    """A collection whose Codex names wikipedia's parser 5 — each origin the
    dated file and byte range — is refused by the pass and by the keeper
    until it is re-created at parser 6 (collection-updates.md §6) — exit 2,
    said in plain words — and its dry run still plans."""
    from app import louise
    node = node5
    assert collections_mod.by_name("wikipedia").codex.parser.version == 5
    node.publish(NEW, NEW_PAGES)
    snap = node.snapshot("2026-10")
    parts, gib = len(snap.shards), sum(s.bytes for s in snap.shards) / 2 ** 30
    got = node.pass_()
    assert got.state == "refused" and got.exit == update.EXIT_REFUSED == 2
    assert got.says == (f"not updated by this pass: every part would be re-issued (wikipedia: {parts:,} parts, "
                        f"{gib:.1f} GiB to every peer) because each record's origin names the dated file and "
                        f"byte range (parser 5); re-create at parser 6 first, whose origin names the page "
                        f"(collection-updates.md §6)")
    assert node.marker() is None and node.origin.request_counts == {}, "nothing asked, nothing written"
    plan = node.pass_(dry_run=True)
    assert plan.state == "dry-run" and plan.plan["fetch"]["files_pending"] == 3, "München sizes the fetch"
    assert update.next_step(node.root, "wikipedia", collections_mod.by_name("wikipedia").codex,
                            now=NOW).step is None, "the keeper runs nothing"
    assert update.describe(node.root, "wikipedia")["says"] == got.says
    capsys.readouterr()
    rc = louise.main(["hold", "collection", "update", "wikipedia", "--root", str(node.root)])
    assert rc == 2 and "re-create at parser 6 first" in capsys.readouterr().out
    assert update.describe(node.root, "wikipedia")["state"] == "waits-recreation"


def test_only_parser_5_declares_that_its_origin_names_the_run():
    from app.plugins.wikipedia.source import WikipediaSource, WikipediaSourceV5
    assert WikipediaSourceV5.origin_names_run is True and WikipediaSourceV5.PARSER_VERSION == 5
    assert WikipediaSource.origin_names_run is False and WikipediaSource.PARSER_VERSION == 6


def test_the_pass_runs_a_collection_at_parser_6(node):
    """Not refused: planned and due like any dated-dump collection."""
    assert not update.origin_names_run(collections_mod.by_name("wikipedia").codex)
    node.publish(NEW, NEW_PAGES)
    assert node.pass_(dry_run=True).state == "dry-run"
