"""Findings 5 and 8 of the independent read of 534b9d48 (2026-11-27): a carry
never moves an index another reads where it lies; a placed index that is
gone is refused, never made empty; a sister's room counts the language
groups where they live."""
from __future__ import annotations

import pytest

from app import text_index
from app.hold import carry, recreate
from app.hold.codex import CodexError
from tests.hold.test_carry import pair, quiet  # noqa: F401


def test_a_carry_that_moves_an_index_another_reads_is_refused(pair):  # noqa: F811
    root, _old, _new = pair
    (root / "x" / "state").mkdir(parents=True)
    (root / "x.toml").write_text('codex = "sha256:00"\n', encoding="utf-8")
    text_index.write_placement(root, "x", "old/index", "id1", reads="old")
    before = sorted(p.name for p in (root / "old").iterdir())
    with pytest.raises(CodexError, match="x reads its text index where it lies"):
        carry.carry(root, "old", "new", copy=False, apply=True, log=quiet)
    assert sorted(p.name for p in (root / "old").iterdir()) == before, "nothing moved"
    assert not (root / "new" / "state" / carry.MARKER).exists(), "nothing begun"


def test_a_reader_whose_placed_index_is_gone_is_refused_and_nothing_is_made(tmp_path):
    from types import SimpleNamespace
    from app import fts
    root = tmp_path / "collections"
    (root / "beta" / "state").mkdir(parents=True)
    (root / "beta.toml").write_text('codex = "sha256:00"\n', encoding="utf-8")
    text_index.write_placement(root, "beta", "alpha/index", "id1", reads="alpha")
    beta = SimpleNamespace(name="beta", fts_dir=root / "beta" / "index", codex=None)
    with pytest.raises(fts.IndexMissing, match="which does not exist; nothing is made there"):
        fts.get_index("en", beta)
    assert not (root / "alpha").exists(), "no empty index made where her sister's was"


def _index(root, name, groups, catalogue=b"c" * 100):
    d = root / name / "index"
    (d / "_catalog").mkdir(parents=True)
    (d / "_catalog" / "c").write_bytes(catalogue)
    for g, n in groups:
        (g / "en").mkdir(parents=True, exist_ok=True)
        (g / "en" / "meta.json").write_text("{}", encoding="utf-8")
        (g / "en" / "seg").write_bytes(b"s" * n)
    return d


def test_her_room_counts_her_sister_s_groups_where_they_live(tmp_path):
    root = tmp_path / "collections"
    shared = root / text_index.SHARED / "abc"
    _index(root, "old", [(shared, 5000)])
    text_index.write_placement(root, "old", f"{text_index.SHARED}/abc", "abc")
    (root / "sis").mkdir(parents=True)
    copied = recreate.to_copy(root, "old", "sis", index_by_path=False, parts=("index",))["index"]
    by_path = recreate.to_copy(root, "old", "sis", index_by_path=True, parts=("index",))["index"]
    assert by_path == 100, "her catalogue alone"
    assert copied == 100 + 5000 + 2, "and the groups copied from the shared directory"
    alone = tmp_path / "alone"
    own = _index(alone, "old", [])
    _index(alone, "x", [(own, 700)])
    (alone / "sis").mkdir(parents=True)
    assert recreate.to_copy(alone, "old", "sis", index_by_path=False, parts=("index",))["index"] == 100 + 702
    assert recreate.to_copy(alone, "old", "sis", index_by_path=True, parts=("index",))["index"] == 100


def test_a_sister_s_room_counts_the_seed_s_bytes_and_nothing_once_seeded(pair):  # noqa: F811
    from app.hold import seed, snapshot as sn
    root, _old, _new = pair
    src = sn.load(root / "old" / "shard" / "snapshots" / "2026-08.json")
    want = sum(int(b) for _p, b, _r in seed.parts_of(src))
    assert want > 0
    assert recreate.to_copy(root, "old", "new", parts=(recreate.SHARD_PARTS,)) == {"shard": want}
    assert recreate.SHARD_PARTS in recreate.SISTER_PARTS, "the sister's carry counts her copy"
    seed.seed_shards(root, "new", "old", log=quiet)
    assert recreate.to_copy(root, "old", "new", parts=(recreate.SHARD_PARTS,)) == {"shard": 0}, \
        "a resumed seed counts what is left"
