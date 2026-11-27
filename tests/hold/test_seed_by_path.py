"""A seeded SHARD reads her sister's parts WHERE THEY LIE (operator,
2026-11-27: "By path"; RULE 1: gutenberg's SHARD is 95 GB): a relative
symbolic link a part, named in `.seeded.json`, checked at every read, handed
over at her sister's forget — never a copy, never a hard link."""
from __future__ import annotations

import os

import pytest

from app.hold import collection as col, discard as discard_mod, forget as forget_mod, rename as rename_mod
from app.hold import seed, seeded, snapshot as sn
from app.hold.codex import CodexError
from app.pack import records as pack_records
from tests.hold.test_carry import pair, quiet  # noqa: F401


def _held(root, *names):
    for n in names:
        (root / f"{n}.toml").write_text('codex = "sha256:00"\n', encoding="utf-8")


def _seeded(root):
    out = seed.seed_shards(root, "new", "old", log=quiet)
    snap = sn.create(root, "new", snapshot_id=out["snapshot_id"], log=quiet)
    return out, snap


def _reissue(part, data=None):
    """The source re-issues a part at its path: another file there."""
    tmp = part.with_name(part.name + ".next")
    tmp.write_bytes(part.read_bytes() if data is None else data)
    os.replace(tmp, part)


def test_every_part_is_a_relative_link_to_her_sister_s_and_nothing_is_copied(pair):  # noqa: F811
    root, _old, _new = pair
    out, snap = _seeded(root)
    src = sn.load(root / "old" / "shard" / "snapshots" / "2026-08.json")
    assert out["parts"] == {"by_path": len(seed.parts_of(src)), "present": 0}
    assert [s.to_dict() for s in snap.shards] == [s.to_dict() for s in src.shards]
    mine, theirs = col.shard_dir_of(root, "new"), col.shard_dir_of(root, "old")
    record = seeded.load(mine)
    for path, _b, part_root in seed.parts_of(src):
        link = mine / path
        assert link.is_symlink() and not os.path.isabs(os.readlink(link)), "a RELATIVE link"
        assert link.resolve() == (theirs / path).resolve()
        assert (theirs / path).stat().st_nlink == 1, "one name for the bytes: no hard link"
        assert record[path]["root"] == part_root and record[path]["from"] == "old"
    again = seed.seed_shards(root, "new", "old", log=quiet)
    assert again["parts"] == {"by_path": 0, "present": len(seed.parts_of(src))}, "repeatable"
    rel = snap.shards[0].path
    assert pack_records.read_index(mine / rel).uids, "read through the link"


def test_a_part_her_sister_re_issues_is_refused_never_read(pair):  # noqa: F811
    root, _old, _new = pair
    _out, snap = _seeded(root)
    mine, theirs = col.shard_dir_of(root, "new"), col.shard_dir_of(root, "old")
    rel = snap.shards[0].path
    _reissue(theirs / rel)                                  # the same bytes in another file: still hers
    assert pack_records.read_index(mine / rel).uids
    _reissue(theirs / rel, (theirs / snap.shards[1].path).read_bytes())    # other bytes
    with pytest.raises(seeded.Reissued, match=rf"{rel}.*re-issued it since \(its latest snapshot: 2026-08\)"):
        pack_records.read_index(mine / rel)
    with pytest.raises(seeded.Reissued, match="Nothing was read from it"):
        pack_records.read_frame_at(mine / rel, 0, 10, 10, 1)
    with pytest.raises(seeded.Reissued):
        sn.create(root, "new", snapshot_id="2026-09", log=quiet)
    assert isinstance(seeded.Reissued("x"), CodexError)


def test_her_sister_s_forget_hands_the_parts_over_and_removes_the_rest(pair):  # noqa: F811
    root, _old, _new = pair
    _held(root, "old", "new")
    _out, snap = _seeded(root)
    mine = col.shard_dir_of(root, "new")
    before = {s.path: (mine / s.path).read_bytes() for s in snap.shards}
    plan = forget_mod.forget(root, "old", dry_run=True)
    assert plan["parts_handed_to"]["to"] == {"new": len(seed.parts_of(snap))}
    assert (mine / snap.shards[0].path).is_symlink(), "a dry run moves nothing"
    out = forget_mod.forget(root, "old")
    assert out["parts_handed_to"]["moved"] is True and not (root / "old").exists()
    for rel, data in before.items():
        part = mine / rel
        assert part.is_file() and not part.is_symlink() and part.read_bytes() == data
        assert part.stat().st_nlink == 1
    assert seeded.load(mine) == {}, "hers now: nothing read elsewhere"
    assert pack_records.read_index(mine / snap.shards[0].path).uids


def test_a_third_seeded_from_her_reads_the_holder_s_file_and_is_relinked_at_its_forget(pair):  # noqa: F811
    root, _old, _new = pair
    _held(root, "old", "new", "third")
    _out, snap = _seeded(root)
    third, mine, theirs = root / "third" / "shard", col.shard_dir_of(root, "new"), col.shard_dir_of(root, "old")
    third.mkdir(parents=True)
    counts = {"by_path": 0, "present": 0}
    seed._seed_all(mine, third, seed.parts_of(snap), "new", counts)
    rel = snap.shards[0].path
    assert seeded.load(third)[rel]["from"] == "old", "one hop: named after the file's holder"
    assert (third / rel).resolve() == (theirs / rel).resolve()
    forget_mod.forget(root, "old")
    assert not (mine / rel).is_symlink() and (third / rel).is_symlink()
    assert (third / rel).resolve() == (mine / rel).resolve() and seeded.load(third)[rel]["from"] == "new"
    assert pack_records.read_index(third / rel).uids


def test_her_sister_is_not_renamed_from_under_her(pair):  # noqa: F811
    root, _old, _new = pair
    _held(root, "old", "new")
    _seeded(root)
    with pytest.raises(CodexError, match="reads its SHARD parts where they lie"):
        rename_mod.rename(root, "old", "older")
    assert (root / "old").is_dir()


def test_a_discard_moves_her_links_so_they_still_reach_the_parts(pair):  # noqa: F811
    import sqlite3
    root, _old, _new = pair
    _out, snap = _seeded(root)
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE control(key TEXT PRIMARY KEY, value TEXT)")
    try:
        discard_mod.discard(root, "new", snap.snapshot_id, con=con)
    finally:
        con.close()
    away = discard_mod.discarded_dir(root, "new", snap.snapshot_id)
    for s in snap.shards:
        assert (away / s.path).is_symlink() and (away / s.path).stat().st_size == s.bytes
    assert discard_mod.held_records(root, "new", snap.snapshot_id).paths
