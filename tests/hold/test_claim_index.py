"""A LARGE annotations snapshot's claims are searched from an index on disk
(app/hold/claim_index.py; wikipedia-articles: 12.3 M claims), which answers
exactly as the in-memory index does, and is never loaded into the web."""
from app import config
from app.hold import annotations as ann, claim_index, collection as col
from tests.hold.test_annotations import (a_claim, a_community, a_mention, a_relation, claims_file,
                                         make_graph, two_targets)


def _ids(claims):
    return sorted((c.uid, ann.claim_text(c.claim)) for c in claims)


def test_the_disk_index_answers_as_the_memory_index_does(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path / "fast"))
    text, _entities, _ = two_targets(tmp_path)
    make_graph(tmp_path, claims_file(a_mention(text), a_relation(text), a_community(),
                                     a_claim("pubmed:2", "pubmed:3"), a_claim("pubmed:4", "pubmed:3")),
               annotator="a graph builder")
    tree = col.shard_dir_of(tmp_path, "graph")
    mem = ann.index_of(tree)
    out = claim_index.build(tree)
    assert out["claims"] == 5 and str(tmp_path / "fast") in out["place"]
    assert claim_index.build(tree)["skipped"] == "built", "once per snapshot"
    from app.share import seeder
    disk = claim_index.load(tree, seeder.cached_snapshot(tree))
    for uid in set(mem.by_target) | {"pubmed:9", "wikidata:Q42"}:
        assert _ids(disk.by_target.get(uid)) == _ids(mem.by_target.get(uid, ())), uid
    for uid in set(mem.graph) | {"pubmed:2"}:
        assert _ids(disk.graph.get(uid)) == _ids(mem.graph.get(uid, ())), uid
    assert disk.snapshot_id == mem.snapshot_id
    # the plugins the claims name (02 §2.10, amended 2026-11-11: which held
    # collections they may apply to), the same from either index
    assert disk.plugins == mem.plugins == frozenset({"pubmed", "wikidata"})
    import json
    meta = json.loads((claim_index.place(tree, seeder.cached_snapshot(tree).hash) / "meta.json").read_text())
    meta.pop("plugins")
    (claim_index.place(tree, seeder.cached_snapshot(tree).hash) / "meta.json").write_text(json.dumps(meta))
    assert claim_index.load(tree, seeder.cached_snapshot(tree)).plugins is None, \
        "an index built before the plugins were listed: unknown, its lookups decide"


def test_a_large_snapshot_is_never_loaded_in_memory_and_groups_nothing_until_built(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path / "fast"))
    monkeypatch.setattr(claim_index, "LARGE", 2)
    text, _entities, _ = two_targets(tmp_path)
    make_graph(tmp_path, claims_file(a_claim("pubmed:2", "pubmed:3"), a_claim("pubmed:4", "pubmed:3"),
                                     a_mention(text)), annotator="a graph builder")
    tree = col.shard_dir_of(tmp_path, "graph")
    # create builds it now (2026-11-10): a snapshot adopted from a peer, or
    # made before that, is one with none — as this one once its index goes
    import shutil
    from app.share import seeder
    shutil.rmtree(claim_index.place(tree, seeder.cached_snapshot(tree).hash))
    ann._indexes.clear()
    loaded = []
    real = ann.claim_of
    monkeypatch.setattr(ann, "claim_of", lambda r: loaded.append(1) or real(r))
    idx = ann.index_of(tree)
    assert idx.by_target.get("pubmed:3", ()) == () and loaded == [], "not built: nothing loaded, nothing grouped"
    assert "louise hold annotations index graph" in capsys.readouterr().out
    claim_index.build(tree)
    loaded.clear()
    idx = ann.index_of(tree)
    assert isinstance(idx, claim_index.DiskClaimIndex)
    assert sorted(c.claim["subject"] for c in idx.by_target.get("pubmed:3")) == ["pubmed:2", "pubmed:4"]
    assert len(loaded) == 2, "only the claims naming the record are read"


# ---- built as the snapshot is written, and by the worker for one that has none -------------

def _held_root(tmp_path, monkeypatch):
    """The node's own root, so that the collections it holds are listed
    (`collections.load_annotations`) as the worker lists them."""
    root = tmp_path / "louise" / "collections"
    root.mkdir(parents=True)
    (tmp_path / "config" / "collections").mkdir(parents=True)
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path / "louise"))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path / "fast"))
    monkeypatch.setattr(claim_index, "LARGE", 2)
    return root


def _make(root, claims, log):
    from tests.hold.test_annotations import TARGET_SNAPSHOT, a_target
    from tests.hold.conftest import KEY, NOW, PLATFORM
    a_target(root)
    return ann.create(root, "same", target_ref="pubmed", from_snapshot=TARGET_SNAPSHOT, claims=claims,
                      platform_image=PLATFORM, snapshot_id="2026-09", key=KEY, now=NOW, nonce="0f" * 16, log=log)


def test_a_large_snapshot_is_indexed_as_create_and_update_write_it(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from tests.hold.conftest import KEY, NOW
    root = _held_root(tmp_path, monkeypatch)
    said: list[str] = []
    _codex, snap = _make(root, claims_file(a_claim(), a_claim("pubmed:3", "pubmed:1"), a_claim("pubmed:2", "pubmed:4")),
                         said.append)
    tree = col.shard_dir_of(root, "same")
    built = claim_index.load(tree, snap)
    assert built is not None and str(tmp_path / "fast") in str(built.dest), "derived: under LOUISE_CACHE_DIR"
    assert sorted(c.claim["object"] for c in built.by_target.get("pubmed:1")) == ["pubmed:1", "pubmed:2"]
    assert any("claim index of 2026-09 built (3 claims" in m for m in said)
    got = ann.update(root, "same", claims=claims_file(a_claim(), a_claim("pubmed:2", "pubmed:4"),
                                                     a_claim("pubmed:4", "pubmed:3")),
                     key=KEY, now=NOW, log=said.append, clock=datetime(2026, 10, 23, tzinfo=timezone.utc))
    assert claim_index.load(tree, got.snapshot) is not None, "the update's snapshot too"
    small = tmp_path / "small"
    small.mkdir()
    monkeypatch.setattr(claim_index, "LARGE", 250_000)
    _c, small_snap = _make(small, claims_file(a_claim()), said.append)
    assert claim_index.load(col.shard_dir_of(small, "same"), small_snap) is None, "a small one keeps the memory index"


def test_the_workers_pass_builds_a_missing_index_and_skips_a_built_one(tmp_path, monkeypatch):
    import shutil
    from app.share import seeder
    root = _held_root(tmp_path, monkeypatch)
    _make(root, claims_file(a_claim(), a_claim("pubmed:3", "pubmed:1"), a_claim("pubmed:2", "pubmed:4")),
          lambda m: None)
    tree = col.shard_dir_of(root, "same")
    snap = seeder.cached_snapshot(tree)
    shutil.rmtree(claim_index.place(tree, snap.hash))          # adopted from a peer, made before: none
    builds, said = [], []
    real = claim_index.build
    monkeypatch.setattr(claim_index, "build", lambda *a, **k: builds.append(1) or real(*a, **k))
    assert claim_index.maintain(root, stop=lambda: True, log=said.append) == [], "stopped: nothing starts"
    (got,) = claim_index.maintain(root, log=said.append)
    assert got["collection"] == "same" and got["claims"] == 3 and claim_index.load(tree, snap) is not None
    assert claim_index.maintain(root, log=said.append) == [] and builds == [1], "a built one is skipped"
    shutil.rmtree(claim_index.place(tree, snap.hash))
    monkeypatch.setattr(claim_index, "build", lambda *a, **k: (_ for _ in ()).throw(OSError("no space left")))
    failed: dict[str, str] = {}
    said.clear()
    assert claim_index.maintain(root, log=said.append, failed=failed) == []
    assert claim_index.maintain(root, log=said.append, failed=failed) == []
    assert len(said) == 1 and "no space left" in said[0], "a failed build is said once, not retried each pass"


def test_the_parse_pool_builds_the_bytes_one_process_builds(tmp_path, monkeypatch):
    from tests.hold.test_annotations_stream import a_wide_target, star_claims
    from tests.hold.test_annotations_update_stream import create
    monkeypatch.setattr(ann, "SHARD_RECORDS", 300)
    a_wide_target(tmp_path, 1600, per_part=300)
    create(tmp_path, star_claims(1600))
    tree = col.shard_dir_of(tmp_path, "same")
    files = {}
    for procs in (1, 2):
        monkeypatch.setattr(config, "PARSE_PROCESSES", procs)
        monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path / f"fast-{procs}"))
        out = claim_index.build(tree)
        assert out["claims"] == 1200 and out["parts"] == 4
        files[procs] = {p.name: p.read_bytes() for p in sorted(claim_index.Path(out["place"]).iterdir())}
    assert files[1] == files[2] and sum(n.endswith(".npy") for n in files[1]) == 5


def test_the_worker_keeps_them_in_a_thread_that_leaves_at_the_stop(tmp_path, monkeypatch):
    import shutil
    import threading
    from app import worker
    from app.share import seeder
    root = _held_root(tmp_path, monkeypatch)
    _make(root, claims_file(a_claim(), a_claim("pubmed:3", "pubmed:1"), a_claim("pubmed:2", "pubmed:4")),
          lambda m: None)
    tree = col.shard_dir_of(root, "same")
    snap = seeder.cached_snapshot(tree)
    shutil.rmtree(claim_index.place(tree, snap.hash))
    (root / ".annotations-create.stopped").mkdir()            # a create killed mid-pass
    monkeypatch.setattr(worker, "_maintenance_allowed", lambda: not worker._stop.is_set())
    monkeypatch.setattr(worker, "_lower_own_priority", lambda offset: None)
    t = threading.Thread(target=worker._claim_index_keeper, daemon=True)
    t.start()
    try:
        for _ in range(400):
            if claim_index.load(tree, snap) is not None:
                break
            t.join(0.05)
        assert claim_index.load(tree, snap) is not None, "built at boot"
        assert not (root / ".annotations-create.stopped").exists(), "the stopped create's workspace cleared"
    finally:
        worker._stop.set()
        t.join(timeout=10)
        worker._stop.clear()
    assert not t.is_alive(), "out at the stop"


def test_the_workers_build_runs_in_a_process_of_its_own(tmp_path, monkeypatch):
    """The merge and sort peak (~1.5 GB at 12.3 M claims) lives and dies with
    a child, never on top of the worker's own memory; the child writes the
    bytes the in-process build writes; a stop terminates it and publishes
    nothing."""
    import os
    import shutil
    from app.share import seeder
    root = _held_root(tmp_path, monkeypatch)
    _make(root, claims_file(a_claim(), a_claim("pubmed:3", "pubmed:1"), a_claim("pubmed:2", "pubmed:4")),
          lambda m: None)
    tree = col.shard_dir_of(root, "same")
    snap = seeder.cached_snapshot(tree)
    dest = claim_index.place(tree, snap.hash)
    in_process = {p.name: p.read_bytes() for p in dest.iterdir() if p.suffix in (".npy", ".json")}
    shutil.rmtree(dest)
    pids = []
    real = claim_index.build
    monkeypatch.setattr(claim_index, "build", lambda *a, **k: pids.append(os.getpid()) or real(*a, **k))
    got = claim_index.build_isolated(tree, snap)
    assert pids == [], "built in another process: nothing ran here"
    assert got["claims"] == 3
    assert {p.name: p.read_bytes() for p in dest.iterdir() if p.suffix in (".npy", ".json")} == in_process
    shutil.rmtree(dest)
    got = claim_index.build_isolated(tree, snap, stop=lambda: True, poll_s=0.0)
    assert "stopped" in got and not (dest / "meta.json").exists(), "a stop publishes nothing"
