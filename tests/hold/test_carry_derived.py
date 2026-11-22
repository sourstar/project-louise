"""`dev carry` TAKES THE DERIVED STATE ITS TARGET CAN PROVE VALID (RULE 1;
app/hold/carry.py `derived_state`). München, 2026-11-17, copied
`state/cpu-reference` and `state/wikipedia-archive-ids` into the re-created
wikipedia by hand, since they are identical bytes. The carry does it now,
each only where what it is keyed on is the target's too: the CPU reference
in the same space and calibration set, the archive's ids by each shard's
physical identity (keyed on the walked path, which holds the collection's
name, until today), the scan cursors with the manifest when the parser did
not move — and the target's own scan then reads nothing again."""
from __future__ import annotations

import os

import numpy as np
import pytest

from app import collections as collections_mod
from app import db, discovery
from app.hold import carry as carry_mod
from app.hold import cpu_reference as cpu_ref
from app.plugins.wikipedia import archive
from app.plugins.wikipedia import source as wsrc
from app.rage import reference as rage
from tests.hold import wiki_runs
from tests.hold.conftest import PLATFORM
from tests.hold.test_origin_reach import build

RUN_ID = "sha256:" + "ab" * 32


def _scan(name: str) -> dict:
    c = collections_mod.by_name(name)
    con = db.connect(c)
    try:
        return discovery.sync_collection(con, c)
    finally:
        con.close()


@pytest.fixture
def pair(isolated_config, data_dir, config_dir, monkeypatch):
    """wikipedia at parser 6, scanned and built, with a CPU reference kept;
    `wikipedia.next` the same recipe at parser 5 — a re-creation that moves
    the parser alone (`--rebuilt`), created and empty."""
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    monkeypatch.setenv("LOUISE_PLATFORM_IMAGE", PLATFORM)
    root, corpus, w6, _snap = build(data_dir, config_dir)
    _r, w5 = wiki_runs.node_collection(data_dir, config_dir, "wikipedia.next", parser=5, shard_records=4,
                                       archives=(wsrc.ARCHIVE_ID,))
    assert w5.space_id == w6.space_id and w5.collection_id != w6.collection_id
    run = {"driver": "cpu", "weights": w6.space.encoder.weights, "engine": "sha256:" + "cd" * 32}
    # kept by the collection, as before the node's store (2026-11-06)
    kept = cpu_ref.write_in(cpu_ref.legacy_directory(root, "wikipedia", "document"), "document",
                            run_id=RUN_ID, run=run, set_id=w6.space.calibration.set,
                            vectors=[[1.0, 0.0], [0.0, 1.0]])
    other = cpu_ref.write_in(cpu_ref.legacy_directory(root, "wikipedia", "query"), "query",
                             run_id=RUN_ID, run=run, set_id="sha256:" + "ee" * 32, vectors=[[1.0, 0.0]])
    return root, corpus, w6, w5, kept, other


def test_the_carry_takes_what_it_can_prove_and_says_what_it_leaves(pair, monkeypatch):
    root, corpus, w6, w5, kept, other = pair
    ids = root / "wikipedia" / "state" / archive.IDS_DIR
    held = sorted(p.name for p in ids.glob("*.npy"))
    assert held and all(n.startswith(archive.PHYSICAL) for n in held), "a scan keys them physically now"
    # the node's cache as it is: keyed on the walked path before 2026-11-17 — and one stale entry
    shard = corpus / "data" / "tr" / "000.parquet"
    physical, legacy = archive.ids_names(shard, shard.stat())
    os.replace(ids / physical, ids / legacy)
    np.save(ids / ("0" * 32 + ".npy"), np.arange(3))
    said: list[str] = []
    plan = carry_mod.carry(root, "wikipedia", "wikipedia.next", rebuilt=True, apply=False, log=said.append)
    w = w5.space.encoder.weights
    assert plan["derived"]["cpu-reference"]["unproven"] == ["document/" + kept.name]
    assert rage.resolve(w5, "document", w, 2) is None
    assert any("derived state would carry" in m for m in said)
    plan = carry_mod.carry(root, "wikipedia", "wikipedia.next", rebuilt=True, apply=True, copy=True,
                           log=said.append)
    got = plan["derived"]
    # the CPU reference (operator, 2026-11-22: "per model, not per collection"): nothing
    # copied, nothing written. The old collection's copy names no inputs, so it is never
    # filed under a key: said (unproven), never what the target's gate reads; another
    # set's refused, named
    assert got["cpu-reference"]["unproven"] == ["document/" + kept.name] and got["cpu-reference"]["refused"]
    assert rage.resolve(w5, "document", w, 2) is None
    assert not (root / "wikipedia.next" / "state" / "cpu-reference").exists(), "no collection copy"
    assert kept.is_file() and other.is_file(), "the old collection's files are left as they were"
    # once the node's store holds it (made for the node), the target resolves it and the
    # old copy is said to be the same bytes
    rage.put(w5, "document", run_id=RUN_ID, run=cpu_ref.read(kept).run_object,
             vectors=cpu_ref.read(kept).vectors)
    again = carry_mod.carry(root, "wikipedia", "wikipedia.next", rebuilt=True, apply=True, copy=True,
                            log=said.append)["derived"]["cpu-reference"]
    assert again["present"] == 1 and again["unproven"] == []
    assert rage.resolve(w5, "document", w, 2) is not None, "what the target's gate reads"
    # the archive's ids: each current shard's, under its physical key; the stale one left
    mine = root / "wikipedia.next" / "state" / archive.IDS_DIR
    assert sorted(p.name for p in mine.glob("*.npy")) == held
    assert (mine / physical).read_bytes() == (ids / legacy).read_bytes(), "the walked path's entry, re-keyed"
    assert got[archive.IDS_DIR] == {"carried": len(held), "present": 0, "stale": 1,
                                    "bytes": sum((mine / n).stat().st_size for n in held)}
    # the scan cursors: not with a parser that moved — discovery is the parser's
    assert got["scan cursors"]["carried"] == [] and "parser moved" in got["scan cursors"]["why"]
    assert not list((root / "wikipedia.next" / "state").glob("*_scan_cursor.json"))
    # the TARGET's own scan, under its own name, finds every carried entry: no parquet is read for coverage
    import pyarrow.parquet as pq
    real = pq.read_table

    def no_ids(path, *a, **kw):
        assert kw.get("columns") != ["id"], f"the coverage of {path} read again"
        return real(path, *a, **kw)
    monkeypatch.setattr(pq, "read_table", no_ids)
    assert _scan("wikipedia.next")["seen"] == 64, "no cursor carried: the target scans once by its own"
    # carried again: everything is there, nothing copied twice
    again = carry_mod.derived_state(root, "wikipedia", "wikipedia.next", w6, w5, rebuilt=True, apply=True,
                                    log=lambda m: None)
    assert again["cpu-reference"]["present"] == 1 and again[archive.IDS_DIR]["present"] == len(held)


def test_the_scan_cursors_travel_with_the_manifest_when_the_parser_did_not_move(pair):
    """Corpus-relative keys on (mtime, size), valid with the manifest they
    stand beside: carried, the target's first scan re-enumerates nothing."""
    root, _corpus, w6, _w5, _kept, _other = pair
    carry_mod.carry(root, "wikipedia", "wikipedia.next", rebuilt=True, apply=True, copy=True, log=lambda m: None)
    got = carry_mod.derived_state(root, "wikipedia", "wikipedia.next", w6, w6, rebuilt=False, apply=True,
                                  log=lambda m: None)
    assert got["scan cursors"]["carried"] == ["wikipedia-archive_scan_cursor.json",
                                              "wikipedia_scan_cursor.json"]
    for n in got["scan cursors"]["carried"]:
        assert (root / "wikipedia.next" / "state" / n).read_bytes() == (root / "wikipedia" / "state" / n).read_bytes()
    assert _scan("wikipedia.next")["seen"] == 0, "every container the cursor names is skipped"


def test_a_cpu_reference_of_another_space_is_never_carried(pair):
    root, _corpus, w6, _w5, _kept, _other = pair
    import dataclasses
    other_space = dataclasses.replace(w6, space=dataclasses.replace(w6.space, dim=w6.space.dim + 1))
    got = carry_mod._carry_cpu_reference(root, "wikipedia", "wikipedia.next", w6, other_space, apply=True)
    assert got["carried"] == 0 and "spaces differ" in got["why"]
