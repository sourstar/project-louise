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
