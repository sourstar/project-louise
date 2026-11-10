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


def test_a_large_snapshot_is_never_loaded_in_memory_and_groups_nothing_until_built(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path / "fast"))
    monkeypatch.setattr(claim_index, "LARGE", 2)
    text, _entities, _ = two_targets(tmp_path)
    make_graph(tmp_path, claims_file(a_claim("pubmed:2", "pubmed:3"), a_claim("pubmed:4", "pubmed:3"),
                                     a_mention(text)), annotator="a graph builder")
    tree = col.shard_dir_of(tmp_path, "graph")
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
