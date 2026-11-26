"""tools/measure/works_walk.py: where "one hit per work" spends its seconds
for one collection's candidates — the claims walked, the frames read for
them, the collection's own relations, the members' rows — with no model
asked and nothing written (München, 2026-11-26: "works 9,229 [claims 8,837
…]" for wikipedia's fifty records, the seek table in place, and the line
cannot say which of the three "claims" is)."""
from __future__ import annotations

import json

import pytest

from app import collections as collections_mod, config, fts, retrieval
from app.hold import annotations as ann, claim_index
from tests.ask.test_annotation_facts import setup
from tools.measure import works_walk


@pytest.fixture
def on_disk(tmp_path, monkeypatch):
    """A target of four records and an annotations collection whose claims
    are read through the DISK index, as wikipedia's 12.3 M are."""
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path / "fast"))
    monkeypatch.setattr(claim_index, "LARGE", 0)
    target, tcoll, held, codex = setup(tmp_path)
    ann._indexes.clear()
    yield tmp_path, tcoll, held
    ann._indexes.clear()


def test_a_walk_says_its_lookups_its_frames_its_relations_and_its_rows(on_disk):
    """pubmed:1 same-work pubmed:2, pubmed:3 translation-of pubmed:1: one work
    of three, reached from pubmed:2; pubmed:4 is in none."""
    root, tcoll, held = on_disk
    cold = works_walk.walk_once(tcoll, ["pubmed:2", "pubmed:4"], root=root, annotations=[held])
    assert cold["error"] is None, cold
    assert (cold["candidates"], cold["candidates_in_a_work"], cold["works"], cold["members"]) == (2, 1, 1, 3)
    assert cold["largest_works"] == [3]
    # every record reached is looked up once: the two candidates, and the two the claims named
    assert cold["calls"]["lookup"] == 4 and cold["claims_returned"] == 4, cold
    assert cold["frames_read"] == cold["distinct_frames_read"] == 1, "two claims, one frame: read once"
    assert cold["frames_from_cache"] == cold["calls"]["frame_asked"] - 1 >= 1
    assert cold["frame_lines"] == 2 and cold["frame_bytes"] > cold["frame_compressed_bytes"] > 0
    assert cold["calls"]["walk"] == 1 and cold["calls"]["rows"] == 1
    assert (cold["rows_asked"], cold["rows_held"]) == (3, 3), "the members' rows, in one query"
    assert set(cold["seconds"]) >= {"lookup", "frame_read", "frame_asked", "rows", "walk"}
    assert cold["seconds"]["walk"] <= cold["seconds"]["lookup"] + cold["seconds"]["rows"] + 1.0
    warm = works_walk.walk_once(tcoll, ["pubmed:2", "pubmed:4"], root=root, annotations=[held])
    assert "frames_read" not in warm and warm["distinct_frames_read"] == 0, "the frame is the index's own now"
    assert warm["frames_from_cache"] == warm["calls"]["frame_asked"] and warm["works"] == 1
    # A FRAME ANOTHER READER READS meanwhile is counted apart, never as the index's (the node's first run
    # said "-56 from the cache": 512 reads against 456 frames asked)
    from app.ask import annotations as ask_annotations
    from app.pack import records as pack_records
    real_rows = ask_annotations._target_rows
    part = next(p for p in (root / held.name / "shard").rglob("*.zst"))

    def rows_and_a_read(collection, uids):
        index = pack_records.read_index(part)
        pack_records.read_frame(part, index, 0)
        return real_rows(collection, uids)
    ask_annotations._target_rows = rows_and_a_read
    try:
        other = works_walk.walk_once(tcoll, ["pubmed:2", "pubmed:4"], root=root, annotations=[held])
    finally:
        ask_annotations._target_rows = real_rows
    assert other["frames_read_by_others"] == 1 and "frames_read" not in other and other["frames_from_cache"] >= 1
    # the tool put back what it wrapped
    assert pack_records.read_frame_at.__module__ == "app.pack.records"
    assert claim_index.DiskClaimIndex.claims_of.__qualname__ == "DiskClaimIndex.claims_of"


def test_a_walk_that_fails_says_so_with_what_it_counted(on_disk, monkeypatch):
    from app.ask import annotations as ask_annotations
    root, tcoll, held = on_disk
    monkeypatch.setattr(ask_annotations, "_target_rows", lambda c, uids: (_ for _ in ()).throw(OSError("disk")))
    got = works_walk.walk_once(tcoll, ["pubmed:2"], root=root, annotations=[held])
    assert got["error"] == "OSError: disk" and got["works"] == 0 and got["calls"]["lookup"] >= 1


@pytest.fixture
def held_text(isolated_config):
    c = collections_mod.default_collection()
    fts.index_book("it", 2, [{"id": "fake:2:0:1", "chapter": "", "text": "la balena bianca e il capitano"}], c)
    fts.index_book("en", 1, [{"id": "fake:1:0:1", "chapter": "", "text": "the white whale and the captain"}], c)
    yield c
    fts.close_all()


def test_the_candidates_are_the_text_legs_and_no_model_is_asked_nor_anything_written(held_text, monkeypatch,
                                                                                    capsys, tmp_path):
    asked, written, grouped = [], [], []
    real = retrieval._reranked
    monkeypatch.setattr(retrieval, "_reranked", lambda *a, **k: asked.append("reranker") or real(*a, **k))
    monkeypatch.setattr(retrieval.verify_on_use, "cite", lambda *a, **k: written.append("cite"))
    monkeypatch.setattr(retrieval, "_enqueue_lazy_embed", lambda *a, **k: written.append("lazy"))
    mine = retrieval._one_hit_per_work
    monkeypatch.setattr(retrieval, "_one_hit_per_work", lambda hits, *a, **k: grouped.append(1) or mine(hits, *a, **k))
    patched = (retrieval._reranked, retrieval._one_hit_per_work, retrieval.verify_on_use.cite,
               retrieval._enqueue_lazy_embed)
    out = tmp_path / "w.json"
    assert works_walk.main(["--collection", held_text.name, "--query", "balena", "--times", "2",
                            "--out", str(out)]) == 0
    got = json.loads(out.read_text(encoding="utf-8"))
    assert json.loads(capsys.readouterr().out) == got, "printed and written the same"
    assert got["candidates"] == 1 and len(got["runs"]) == 2 and len(got["left_out"]) == 3
    assert all(r["error"] is None and r["works"] == 0 for r in got["runs"]), "no annotations about it: no work"
    assert (asked, written, grouped) == ([], [], []), "its own replacements stood while it searched"
    assert (retrieval._reranked, retrieval._one_hit_per_work, retrieval.verify_on_use.cite,
            retrieval._enqueue_lazy_embed) == patched, "and what was there before is there again"
    with pytest.raises(SystemExit, match="no collection of that name"):
        works_walk.measure("nowhere", "balena")
