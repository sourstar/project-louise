"""The share tree names the SHARD's files by symbolic link, never by hard
link (operator, 2026-11-27, app/file_copy.py): the seed reads each file
where it lies, and removing the tree removes only the links."""
from __future__ import annotations

import shutil

import pytest

from app.hold import shard as shard_mod
from app.share import seeder
from app.share.members import Member


def _shard(tmp_path):
    shard = tmp_path / "shard"
    (shard / "records").mkdir(parents=True)
    f = shard / "records" / "part-0001.jsonl"
    f.write_bytes(b"x" * 70_000)
    return shard, f


def test_a_placed_file_is_a_symbolic_link_to_the_shard_s_own(tmp_path):
    shard, f = _shard(tmp_path)
    share = tmp_path / "state" / "share"
    member_dir = seeder._place(share, "snap1", "records-0001", "records/part-0001.jsonl", f)
    placed = member_dir / "records" / "part-0001.jsonl"
    assert placed.is_symlink() and placed.resolve() == f.resolve()
    assert f.stat().st_nlink == 1, "the SHARD's file has one name: no hard link was made"
    assert placed.read_bytes() == f.read_bytes()
    # placed again (a twin's publish): the link replaced, the file untouched
    seeder._place(share, "snap1", "records-0001", "records/part-0001.jsonl", f)
    assert placed.is_symlink() and f.stat().st_nlink == 1
    # the tree removed: the SHARD keeps its file
    shutil.rmtree(share)
    assert f.read_bytes() == b"x" * 70_000


def test_libtorrent_hashes_the_file_through_the_link(tmp_path):
    """The torrent a seed serves is the snapshot's: the infohash computed
    over the linked path is the one the member's (path, length, root) give."""
    pytest.importorskip("libtorrent")
    shard, f = _shard(tmp_path)
    rel = "records/part-0001.jsonl"
    member = Member("records-0001", (shard_mod.MemberFile(rel, shard_mod.pieces_root_of(f),
                                                          f.stat().st_size),))
    (t,) = member.torrents()
    member_dir = seeder._place(tmp_path / "share", "snap1", member.name, rel, f)
    out = seeder.build_torrent(member_dir, t, tmp_path / "torrents")
    assert out is not None and out.is_file(), "the linked file hashes to the snapshot's infohash"


def test_a_share_tree_of_a_seeded_sister_survives_her_sister_s_forget(tmp_path, isolated_config, corpus_dir):
    """Finding 3 of the read of 534b9d48, checked again with copies (choice
    B): `_place` links to the file it is given, resolved — her own copy, not
    her sister's — so her share tree reads on after her sister is forgotten."""
    from app.hold import collection as col, forget as forget_mod, seed, snapshot as sn
    from tests.hold.test_carry import _create, _old, quiet
    from app.hold import records as rec
    root, _o = _old(tmp_path, corpus_dir)
    _create(root, "new")
    rec.write_validation(root, "new")
    for n in ("old", "new"):
        (root / f"{n}.toml").write_text('codex = "sha256:00"\n', encoding="utf-8")
    out = seed.seed_shards(root, "new", "old", log=quiet)
    snap = sn.create(root, "new", snapshot_id=out["snapshot_id"], log=quiet)
    mine = col.shard_dir_of(root, "new")
    rel = snap.shards[0].path
    assert not (mine / rel).is_symlink() and (mine / rel).stat().st_nlink == 1, "her own copy"
    member_dir = seeder._place(root / "new" / "state" / "share", snap.snapshot_id, "records-x", rel, mine / rel)
    placed = member_dir / rel
    data = placed.read_bytes()
    forget_mod.forget(root, "old")
    assert not (root / "old").exists() and placed.read_bytes() == data
    assert placed.resolve() == (mine / rel).resolve()
