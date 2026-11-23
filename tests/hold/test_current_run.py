"""A collection keeps its identity as its origin moves (02 §2.7, amended
2026-11-17, operator: B). The run it holds NOW is one answer
(app/hold/origin_run.py `current_run`): the Codex's first run, the newest
snapshot's `origin_run`, an update's moved run — the latest — read by the
node's loader, the records build, discovery and the collection file's
`snapshot` check alike."""
import json

import pytest

from app import collections as collections_mod
from app.hold import origin_run as orun
from app.hold import records as rec
from app.hold import update_state
from app.plugins.base import DiscoverState
from app.plugins.wikipedia import SOURCE, archive
from app.plugins.wikipedia import source as wsrc
from app.plugins.wikipedia.ids import native_id_of
from tests.hold import wiki_runs


@pytest.fixture
def wp(isolated_config, data_dir, config_dir):
    wsrc._stream_cache.clear()
    archive._groups.clear()
    archive._layouts.clear()
    root, cx = wiki_runs.node_collection(data_dir, config_dir)
    return root, cx


def _snapshot_file(root, run, sid):
    d = root / "wikipedia" / "shard" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    body = {"collection_id": "x", "delta": {"added": [], "changed": [], "tombstoned": []},
            "figures": [], "files": {}, "kind": "louise-snapshot", "previous": None,
            "relations": [], "shards": [], "snapshot_id": sid, "version": 2}
    if run is not None:
        body["origin_run"] = run
    (d / f"{sid}.json").write_text(json.dumps(body, sort_keys=True, separators=(",", ":")))


def test_the_codex_names_the_first_run_and_the_loader_reads_it(wp):
    root, cx = wp
    assert orun.codex_run(cx) == wiki_runs.FIRST
    assert orun.current_run(root, "wikipedia", cx) == wiki_runs.FIRST
    assert collections_mod.by_name("wikipedia").snapshot == wiki_runs.FIRST


def test_a_snapshot_made_before_the_field_reads_the_codexs_run(wp):
    root, cx = wp
    _snapshot_file(root, None, "2026-10")
    assert orun.current_run(root, "wikipedia", cx) == wiki_runs.FIRST


def test_the_newest_snapshots_run_is_the_collections(wp):
    root, cx = wp
    _snapshot_file(root, None, "2026-10")
    _snapshot_file(root, "20261101", "2026-11")
    assert orun.current_run(root, "wikipedia", cx) == "20261101"
    assert collections_mod.by_name("wikipedia").snapshot == "20261101"
    local = rec.local_collection(root, "wikipedia", cx)
    assert local.snapshot == "20261101", "the records build reads the same answer"


def test_an_update_moves_the_run_once_its_marker_says_so(wp):
    root, cx = wp
    _snapshot_file(root, "20261101", "2026-11")
    mk = {"collection_id": cx.collection_id, "period": "2026-12", "run": "20261201",
          "step": "run", "moved": False}
    update_state.write(root, "wikipedia", mk)
    assert orun.current_run(root, "wikipedia", cx) == "20261101", "fetched is not moved"
    update_state.write(root, "wikipedia", {**mk, "moved": True})
    assert orun.current_run(root, "wikipedia", cx) == "20261201"
    assert collections_mod.by_name("wikipedia").snapshot == "20261201"


def test_a_run_never_moves_back(wp):
    root, cx = wp
    _snapshot_file(root, "20261201", "2026-12")
    # a marker left by the update that MADE 2026-12: its period is not after it
    update_state.write(root, "wikipedia", {"period": "2026-12", "run": "20261201", "moved": True,
                                           "step": "done"})
    assert orun.current_run(root, "wikipedia", cx) == "20261201"
    update_state.write(root, "wikipedia", {"period": "2027-01", "run": "20261101", "moved": True,
                                           "step": "records"})
    assert orun.current_run(root, "wikipedia", cx) == "20261201", "the latest, whatever says otherwise"


def test_the_file_may_state_a_later_run_and_never_an_earlier_one(wp, config_dir, capsys):
    assert wp
    path = config_dir / "collections" / "wikipedia.toml"
    base = path.read_text()
    path.write_text(base + 'snapshot = "20261101"\n')
    assert collections_mod.by_name("wikipedia").snapshot == wiki_runs.FIRST, \
        "the run is the one answer, never the file's"
    assert "the run is not read from the file" in capsys.readouterr().err
    path.write_text(base + 'snapshot = "20260901"\n')
    with pytest.raises(ValueError, match="first run is '20261001'"):
        collections_mod.by_name("wikipedia")


def test_a_rewrite_of_the_file_does_not_write_the_run(wp, config_dir):
    from app import collection_file
    root, _cx = wp
    _snapshot_file(root, "20261101", "2026-11")
    c = collections_mod.by_name("wikipedia")
    collection_file.rewrite(c)
    assert "snapshot" not in (config_dir / "collections" / "wikipedia.toml").read_text()


def test_another_origin_keeps_its_pin_as_before(isolated_config, data_dir, config_dir):
    from tests.conftest import codex_collection
    codex_collection("gb", louise_root=data_dir, config_dir=config_dir,
                     toml_extra='snapshot = "20260801"\n')
    assert collections_mod.by_name("gb").snapshot == "20260801"
    cx = collections_mod.by_name("gb").codex
    assert orun.current_run(data_dir / "collections", "gb", cx) is None


# ---------------------------------------------------------------- discovery

def _refs(corpus, run):
    return list(SOURCE.discover(DiscoverState(corpus_root=corpus, snapshot=run)))


def test_discovery_reads_the_current_run_and_withdraws_what_it_no_longer_holds(tmp_path):
    wsrc._stream_cache.clear()
    corpus = tmp_path / "origin"
    wiki_runs.dump(corpus, "20261001", [(20, "Cheese", "old"), (21, "Bread", "old"),
                                        (23, "Template:Box", "not an article")])
    wiki_runs.dump(corpus, "20261101", [(20, "Cheese", "new"), (22, "Wine", "new")])
    refs = _refs(corpus, "20261101")
    docs = [r for r in refs if r.scope == "document" and not r.deleted]
    assert sorted(int(r.native_id) for r in docs) == [native_id_of("tr", 20), native_id_of("tr", 22)]
    assert all("/20261101/" in r.locator for r in docs)
    gone = [r for r in refs if r.deleted]
    assert [int(r.native_id) for r in gone] == [native_id_of("tr", 21)], \
        "the page the run before held and this one does not; never a template"
    assert gone[0].extra["reason"] == "withdrawn"
    assert "20261101 no longer holds page 21" in gone[0].error


def test_a_first_run_withdraws_nothing(tmp_path):
    wsrc._stream_cache.clear()
    corpus = tmp_path / "origin"
    wiki_runs.dump(corpus, "20261001", [(20, "Cheese", "old")])
    assert not [r for r in _refs(corpus, "20261001") if r.deleted]


def test_a_withdrawn_page_leaves_the_manifest_through_discovery(wp):
    """The deletion signal through the same path PubMed's DeleteCitation
    takes (app/discovery.py): out of every index first, then the error."""
    from app import db, discovery, unindexable
    root, _cx = wp
    corpus = root / "wikipedia" / "origin"
    wiki_runs.dump(corpus, "20261001", [(20, "Cheese", "old"), (21, "Bread", "old")])
    c = collections_mod.by_name("wikipedia")
    con = db.connect(c)
    try:
        discovery.sync_collection(con, c)
        con.execute("UPDATE documents SET rdf_done=1, fts_done=1, embed_done=1")
        con.commit()
        wiki_runs.dump(corpus, "20261101", [(20, "Cheese", "new")])
        _snapshot_file(root, "20261101", "2026-11")
        c = collections_mod.by_name("wikipedia")
        assert c.snapshot == "20261101"
        withdrawn = []
        from unittest import mock
        with mock.patch.object(unindexable, "withdraw_many",
                               lambda con_, coll, uids: withdrawn.extend(uids) or len(uids)):
            stats = discovery.sync_collection(con, c)
        assert not stats.get("interrupted")
        rows = {r["native_id"]: dict(r) for r in con.execute(
            "SELECT native_id, path, error, embed_done FROM documents")}
    finally:
        con.close()
    bread = rows[str(native_id_of("tr", 21))]
    assert bread["error"].startswith("withdrawn:") and bread["embed_done"] == 0
    # before the tombstone commits, and once more after it (discovery.py, the
    # review of #799's defect 7): a stage that indexed it in between is undone
    assert withdrawn == [f"wikipedia:{native_id_of('tr', 21)}"] * 2
    cheese = rows[str(native_id_of("tr", 20))]
    assert cheese["error"] is None and "/20261101/" in cheese["path"]


def test_a_page_never_hashed_is_compared_with_the_unit_it_still_names(wp):
    """The first scan writes no `txt_hash`; the next run's scan hashes the
    old run's unit while it is on disk, so an unchanged page is re-pointed
    with its marks kept — and with the old run gone, it is changed, as
    before (no evidence)."""
    import shutil

    from app import db, discovery
    root, _cx = wp
    corpus = root / "wikipedia" / "origin"
    wiki_runs.dump(corpus, "20261001", [(20, "Cheese", "same"), (24, "Salt", "same")])
    c = collections_mod.by_name("wikipedia")
    con = db.connect(c)
    try:
        discovery.sync_collection(con, c)
        assert con.execute("SELECT COUNT(*) FROM documents WHERE txt_hash IS NULL").fetchone()[0] == 2
        con.execute("UPDATE documents SET rdf_done=1, fts_done=1, embed_done=1")
        con.commit()
        wiki_runs.dump(corpus, "20261101", [(20, "Cheese", "same"), (24, "Salt", "same")])
        _snapshot_file(root, "20261101", "2026-11")
        stats = discovery.sync_collection(con, collections_mod.by_name("wikipedia"))
        assert stats["changed"] == 0 and stats["relocated"] == 2
        assert con.execute("SELECT COUNT(*) FROM documents WHERE embed_done=1 AND txt_hash IS NOT NULL "
                           "AND path LIKE '%/20261101/%'").fetchone()[0] == 2
        # the old run gone before the next one is read: no evidence, changed
        wiki_runs.dump(corpus, "20261201", [(20, "Cheese", "same"), (24, "Salt", "same")])
        con.execute("UPDATE documents SET txt_hash=NULL")
        con.commit()
        shutil.rmtree(corpus / "trwiki" / "20261101")
        _snapshot_file(root, "20261201", "2026-12")
        stats = discovery.sync_collection(con, collections_mod.by_name("wikipedia"))
        assert stats["changed"] == 2
    finally:
        con.close()
