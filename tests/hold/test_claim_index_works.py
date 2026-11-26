"""THE WORK COMPONENTS of a large annotations snapshot's claim index
(app/hold/claim_index.py, 2026-11-26): each record named by a work claim,
the work it is in and that work's members, so a question's grouping opens no
frame. München's run of tools/measure/works_walk.py on the node: fifty
wikipedia records took 4.89 s cold, 4.19 of them decompressing 618 MB of
frames for 456 claims — a frame of a thousand claims read for each one.

The grouping read through the components is the grouping read through the
claims, record for record, length for length, centre for centre."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from app import collections as collections_mod, config, db
from app.ask import works
from app.hold import annotations as ann, claim_index, collection as col
from app.share import seeder
from tests.hold.test_annotations import a_claim, a_target, claims_file, make

N = 9


def _ev(**tokens):
    return {"signals": ["wikidata-edition"], "tier": "wikidata", "wikidata": "Q1", **tokens}


CLAIMS = (
    # a star: three editions around pubmed:1, with their lengths and the centre's
    a_claim("pubmed:2", "pubmed:1", evidence=_ev(subject_tokens=40, object_tokens=90)),
    a_claim("pubmed:3", "pubmed:1", type="translation-of", evidence=_ev(subject_tokens=10, object_tokens=90)),
    a_claim("pubmed:4", "pubmed:1"),
    # another, around pubmed:5 ...
    a_claim("pubmed:6", "pubmed:5", evidence=_ev(subject_tokens=7, object_tokens=70)),
    a_claim("pubmed:7", "pubmed:5"),
    # ... joined to the first through pubmed:8, an edition both works name: ONE work of eight
    a_claim("pubmed:8", "pubmed:1"),
    a_claim("pubmed:8", "pubmed:5", evidence=_ev(subject_tokens=12)),
    # pubmed:9 is in no work
)


@pytest.fixture
def held(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path / "fast"))
    monkeypatch.setattr(claim_index, "LARGE", 0)
    target, hashes = a_target(tmp_path, texts=tuple(f"text {i}" for i in range(1, N + 1)))
    codex, snap = make(tmp_path, claims=claims_file(*CLAIMS))
    tcoll = collections_mod.Collection(
        name="pubmed", plugin="pubmed", profile=None, embed_mode_default=None,
        corpus_dir=tmp_path / "pubmed" / "shard", manifest_path=tmp_path / "pubmed" / "state" / "manifest.sqlite",
        fts_dir=tmp_path / "pubmed" / "index", lance_dir=tmp_path / "pubmed" / "lance", codex=target)
    with db.connect(tcoll) as con:
        for i in range(1, N + 1):
            uid = f"pubmed:{i}"
            con.execute("INSERT INTO documents(uid, source, native_id, path, title, lang, content_hash) "
                        "VALUES(?,?,?,?,?,?,?)", (uid, "pubmed", str(i), f"x/{i}", f"T{i}", "en", hashes[uid]))
    annotations = SimpleNamespace(name="pubmed-same-work", codex=codex, plugin="annotations", license_ack=None,
                                  manifest_path=tmp_path / "pubmed-same-work" / "state" / "manifest.sqlite")
    tree = col.shard_dir_of(tmp_path, "pubmed-same-work")
    dest = claim_index.place(tree, seeder.cached_snapshot(tree).hash)
    ann._indexes.clear()
    yield tmp_path, tcoll, annotations, tree, dest
    ann._indexes.clear()


def _groups(root, tcoll, annotations, uids):
    ann._indexes.clear()
    return works.work_groups(tcoll, uids, root=root, annotations=[annotations])


def test_the_components_group_as_the_claims_do_and_open_no_frame(held, monkeypatch):
    root, tcoll, annotations, tree, dest = held
    assert (dest / "meta.json").is_file() and not claim_index.has_works(dest), "built by create, without them"
    asked = [["pubmed:2"], ["pubmed:6", "pubmed:9"], ["pubmed:9"], ["pubmed:4", "pubmed:7", "pubmed:3"]]
    by_claims = [_groups(root, tcoll, annotations, u) for u in asked]
    work_of, members = by_claims[0]
    assert set(work_of) == {f"pubmed:{i}" for i in range(1, 9)} and len(members) == 1, "one work of eight"
    assert by_claims[2] == ({}, {}), "a record no claim names is in no work"

    got = claim_index.add_works(tree, dest)
    assert got["works"] == 1 and got["uids"] == 8 and got["largest"] == 8
    assert claim_index.has_works(dest) and claim_index.add_works(tree, dest)["skipped"]
    read = []
    from app.pack import records as pack_records
    real = pack_records.read_frame_at
    monkeypatch.setattr(pack_records, "read_frame_at", lambda *a, **k: read.append(a) or real(*a, **k))
    by_components = [_groups(root, tcoll, annotations, u) for u in asked]
    assert by_components == by_claims, "record for record, length for length, centre for centre"
    assert read == [], "no frame opened"
    (key, eds), = by_components[0][1].items()
    sized = {e.uid: (e.size, e.centre) for e in eds}
    assert sized["pubmed:1"] == (90, True) and sized["pubmed:2"] == (40, False) and sized["pubmed:5"] == (70, True)
    assert sized["pubmed:8"] == (12, False) and sized["pubmed:4"] == (None, False)


def test_a_lookup_is_two_binary_searches_and_a_slice(held):
    root, tcoll, annotations, tree, dest = held
    claim_index.add_works(tree, dest)
    idx = claim_index.load(tree, seeder.cached_snapshot(tree))
    number, members = idx.work_component("pubmed:3")
    assert number >= 0 and sorted(m for m, _t, _c in members) == [f"pubmed:{i}" for i in range(1, 9)]
    assert idx.work_component("pubmed:5")[0] == number
    assert idx.work_component("pubmed:9") == (-1, [])
    assert idx.work_component("wikidata:Q42") == (-1, [])


def test_an_index_built_before_them_reads_the_claims_until_they_are_added(held, monkeypatch):
    root, tcoll, annotations, tree, dest = held
    idx = claim_index.load(tree, seeder.cached_snapshot(tree))
    assert idx.work_component("pubmed:2") is None, "none yet: the caller reads the claims"
    claim_index.add_works(tree, dest)
    assert idx.work_component("pubmed:2") is None, "looked for at most every SEEK_LOOK_S"
    idx._works_looked = 0.0
    assert idx.work_component("pubmed:2")[0] >= 0, "then taken and kept"


def test_two_uids_of_one_first_hash_are_told_apart(held, monkeypatch):
    """A 64-bit hash over 12 M uids collides once in ~200,000 snapshots: the
    second hash and the uid's own bytes keep two such uids apart, at build
    and at every lookup."""
    root, tcoll, annotations, tree, dest = held
    real = claim_index.uid_hash
    monkeypatch.setattr(claim_index, "uid_hash",
                        lambda uid: 7 if uid in ("pubmed:2", "pubmed:6") else real(uid))
    parts = [claim_index.part_works(str(tree / p), frozenset())
             for p in json.loads((dest / "parts.json").read_text())]
    arrays = claim_index.works_from(parts)
    assert int((arrays["work_uid_hash"] == 7).sum()) == 2
    claim_index._write_works(dest, arrays)
    idx = claim_index.load(tree, seeder.cached_snapshot(tree))
    a, b = idx.work_component("pubmed:2"), idx.work_component("pubmed:6")
    assert a[0] == b[0] >= 0, "the same work all the same (pubmed:8 joins them)"
    assert sorted(m for m, _t, _c in a[1]) == [f"pubmed:{i}" for i in range(1, 9)]


def test_the_components_are_the_graphs_connected_ones():
    """Min-label propagation with pointer jumping, against a path, a star,
    two pieces and a node alone."""
    a = np.array([0, 1, 2, 3, 6, 6, 9], dtype=np.int64)
    b = np.array([1, 2, 3, 4, 5, 7, 8], dtype=np.int64)
    label = claim_index._components(11, a, b)
    assert label.tolist() == [0, 0, 0, 0, 0, 5, 5, 5, 8, 8, 10]
    long = np.arange(2000, dtype=np.int64)
    assert set(claim_index._components(2001, long[::-1].copy(), long[::-1].copy() + 1).tolist()) == {0}


def test_the_maintainer_adds_them_to_a_built_index_and_a_stop_writes_nothing(held, monkeypatch):
    root, tcoll, annotations, tree, dest = held
    monkeypatch.setattr(collections_mod, "load_annotations", lambda: [annotations])
    said = []
    assert claim_index.maintain(root, stop=lambda: True, log=said.append) == [] and not claim_index.has_works(dest)
    claim_index.maintain(root, log=said.append)
    assert claim_index.has_works(dest)
    assert any("work components written (8 records in 1 works, the largest of 8)" in s for s in said), said
    said.clear()
    claim_index.maintain(root, log=said.append)
    assert not [s for s in said if "work components" in s], "once"


def test_the_worker_adds_them_in_a_process_of_its_own(held):
    """Its peak (~4.4 GB for 12.3 M claims) lives and dies with a child,
    bounded by a data limit of its own: never in the worker's process."""
    root, tcoll, annotations, tree, dest = held
    got = claim_index.add_works_isolated(tree, dest, poll_s=0.05)
    assert got["works"] == 1 and got["uids"] == 8 and claim_index.has_works(dest)
    assert claim_index.add_works_isolated(tree, dest, poll_s=0.05)["skipped"], "once"
    for name in claim_index.WORKS:
        (dest / f"{name}.npy").unlink()
    (dest / "works.json").unlink()
    got = claim_index.add_works_isolated(tree, dest, stop=lambda: True, poll_s=0.0)
    assert "stopped" in got and not claim_index.has_works(dest), "a stop writes nothing"


def test_the_hand_command_adds_them_where_the_worker_does_not_index(held, capsys):
    """München, 2026-11-26: the node in query mode runs no index work, so the
    keeper waited; `hold annotations index` is the hand that adds them."""
    import json as _json
    from types import SimpleNamespace as NS
    from app import louise
    root, tcoll, annotations, tree, dest = held
    assert not claim_index.has_works(dest)
    assert louise.cmd_annotations_index(NS(root=str(root), collection="pubmed-same-work")) == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["skipped"] == "built" and out["works"]["works"] == 1 and claim_index.has_works(dest)
    assert louise.cmd_annotations_index(NS(root=str(root), collection="pubmed-same-work")) == 0
    out = _json.loads(capsys.readouterr().out)
    assert "works" not in out, "once: what it lacks only"
