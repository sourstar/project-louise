"""Findings 5 and 8 of the independent read of 534b9d48 (2026-11-27): a placed
index that is gone is refused, never made empty; a sister's room counts the
language groups where they live, and her SHARD's seed."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import text_index
from app.hold import recreate
from tests.hold.test_carry import pair, quiet  # noqa: F401


def test_a_member_whose_placed_index_is_gone_is_refused_and_nothing_is_made(tmp_path):
    from types import SimpleNamespace
    from app import fts
    root = tmp_path / "collections"
    (root / "beta" / "state").mkdir(parents=True)
    (root / "beta.toml").write_text('codex = "sha256:00"\n', encoding="utf-8")
    text_index.write_placement(root, "beta", "gone/index", "id1", writer="gone")
    beta = SimpleNamespace(name="beta", fts_dir=root / "beta" / "index", codex=None)
    with pytest.raises(fts.IndexMissing, match="which does not exist; nothing is made there"):
        fts.get_index("en", beta)
    assert not (root / "gone").exists(), "no empty index made where her sister's was"


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
    shared = root / "first" / "index"
    _index(root, "old", [(shared, 5000)])
    text_index.write_placement(root, "old", "first/index", "abc", writer="first")
    (root / "sis").mkdir(parents=True)
    copied = recreate.to_copy(root, "old", "sis", index_by_path=False, parts=("index",))["index"]
    by_path = recreate.to_copy(root, "old", "sis", index_by_path=True, parts=("index",))["index"]
    assert by_path == 100, "her catalogue alone"
    assert copied == 100 + 5000 + 2, "and the groups copied from the index she reads"
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


def test_a_re_creation_s_room_counts_its_seed_and_its_bundle_tree(pair):  # noqa: F811
    """Finding E: case A counts the SHARD's parts (the seed is a copy) and
    `state/bundles`, before anything is copied."""
    from app.hold import seed, snapshot as sn
    root, _old, _new = pair
    b = root / "old" / "state" / "bundles" / "x"
    b.mkdir(parents=True)
    (b / "possession.json").write_bytes(b"p" * 300)
    src = sn.load(root / "old" / "shard" / "snapshots" / "2026-08.json")
    parts = sum(int(n) for _p, n, _r in seed.parts_of(src))
    got = recreate.to_copy(root, "old", "new", parts=recreate.RECREATE_PARTS)
    assert got["shard"] == parts and got["state/bundles"] == 300


def _seed_order_issues(source: str) -> tuple[int, list[str]]:
    """Every `recreate.seed_parts(...)` of `source`, and what is wrong with
    the room check before it: in the same function, a `recreate.room(...)`
    that makes (no `make=False`) and counts the SHARD's parts, then its
    `free < need` refusal, then the seed — nothing seeded in between."""
    import ast

    def call(node, name):
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == name and getattr(node.func.value, "id", None) == "recreate"

    issues, seeds = [], 0
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        own = [n for n in ast.walk(fn)]
        for seed in (n for n in own if call(n, "seed_parts")):
            seeds += 1
            rooms = [n for n in own if call(n, "room") and n.lineno < seed.lineno
                     and not any(k.arg == "make" and isinstance(k.value, ast.Constant) and k.value.value is False
                                 for k in n.keywords)]
            if not rooms:
                issues.append(f"line {seed.lineno}: no room check before the seed")
                continue
            room = max(rooms, key=lambda n: n.lineno)
            parts = next((ast.unparse(k.value) for k in room.keywords if k.arg == "parts"), "")
            if parts not in ("recreate.RECREATE_PARTS", "recreate.SISTER_PARTS"):
                issues.append(f"line {seed.lineno}: the room check counts {parts or 'no parts'}, not the seed's")
            if any(call(n, "seed_parts") and room.lineno < n.lineno < seed.lineno for n in own):
                issues.append(f"line {seed.lineno}: another seed between its room check and it")
            refused = [n for n in own if isinstance(n, ast.If) and room.lineno < n.lineno < seed.lineno
                       and "free < need" in ast.unparse(n.test) and any(isinstance(b, ast.Raise) for b in n.body)]
            if not refused:
                issues.append(f"line {seed.lineno}: no `free < need` refusal between the room check and the seed")
    return seeds, issues


def test_every_seed_follows_its_room_check_and_its_refusal():
    """Finding E's ORDER: the room for the SHARD's parts (the seed is a copy)
    is checked, and refused short, BEFORE `seed_parts` copies a byte — for
    the re-creation (case A) and for a sister alike."""
    from app.hold import recreate

    assert recreate.SHARD_PARTS in recreate.RECREATE_PARTS and recreate.SHARD_PARTS in recreate.SISTER_PARTS
    source = (Path(__file__).resolve().parents[2] / "app" / "louise.py").read_text(encoding="utf-8")
    seeds, issues = _seed_order_issues(source)
    assert seeds >= 2, "the re-creation's and the sister's seeds"
    assert not issues, issues


def test_the_order_check_finds_a_room_check_moved_after_the_seed():
    moved = ("def f(root, a, b):\n"
             "    parts = recreate.seed_parts(root, a, b)\n"
             "    need, free = recreate.room(root, a, b, parts=recreate.RECREATE_PARTS)\n"
             "    if free < need:\n"
             "        raise E()\n")
    assert _seed_order_issues(moved) == (1, ["line 2: no room check before the seed"])
    unrefused = ("def f(root, a, b):\n"
                 "    need, free = recreate.room(root, a, b, parts=recreate.RECREATE_PARTS)\n"
                 "    parts = recreate.seed_parts(root, a, b)\n")
    assert _seed_order_issues(unrefused)[1] == ["line 3: no `free < need` refusal between the room check and the seed"]
    dry = ("def f(root, a, b):\n"
           "    need, free = recreate.room(root, a, b, make=False, parts=recreate.COPIED)\n"
           "    parts = recreate.seed_parts(root, a, b)\n")
    assert _seed_order_issues(dry)[1] == ["line 3: no room check before the seed"]
