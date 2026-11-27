"""An origin DIRECTORY a re-created collection or a sister reads is read
where it lies, through a symbolic link, and handed over at its holder's
forget — never a hard link (operator, 2026-11-27: "Never use hard links,
always consciously load from a path"; app/file_copy.py)."""
from __future__ import annotations

import pytest

from app.hold import forget as forget_mod, recreate, rename as rename_mod
from app.hold.codex import CodexError


def _pair(tmp_path):
    root = tmp_path / "collections"
    origin = root / "p" / "origin"
    (origin / "lang=en").mkdir(parents=True)
    (origin / "lang=en" / "a.txt").write_bytes(b"a" * 100)
    (origin / "b.txt").write_bytes(b"b")
    (root / "q").mkdir()
    for n in ("p", "q"):
        (root / f"{n}.toml").write_text('codex = "sha256:00"\n')
    return root, origin


def test_a_directory_origin_is_read_where_it_lies(tmp_path):
    root, origin = _pair(tmp_path)
    said = recreate.share_origin(root, "p", "q")
    link = root / "q" / "origin"
    assert link.is_symlink() and link.resolve() == origin.resolve() and "where it lies" in said
    assert (link / "lang=en" / "a.txt").read_bytes() == b"a" * 100
    assert all(f.stat().st_nlink == 1 for f in origin.rglob("*") if f.is_file()), "no second name"
    assert "already" in recreate.share_origin(root, "p", "q"), "repeatable"


def test_the_holder_s_forget_hands_its_origin_over_and_removes_the_rest(tmp_path):
    root, origin = _pair(tmp_path)
    recreate.share_origin(root, "p", "q")
    (root / "r").mkdir()
    (root / "r.toml").write_text('codex = "sha256:00"\n')
    recreate.share_origin(root, "p", "r")
    assert forget_mod.origin_heirs(root, "p") == ["q", "r"]
    plan = forget_mod.forget(root, "p", dry_run=True)
    assert plan["origin_handed_to"]["to"] == "q" and origin.is_dir(), "a dry run moves nothing"
    out = forget_mod.forget(root, "p")
    assert out["origin_handed_to"] == {"origin": str(origin), "to": "q", "relinked": ["r"], "moved": True}
    own = root / "q" / "origin"
    assert own.is_dir() and not own.is_symlink() and (own / "lang=en" / "a.txt").read_bytes() == b"a" * 100
    assert (root / "r" / "origin").resolve() == own.resolve()
    assert not (root / "p").exists()


def test_a_hand_over_cut_short_is_finished_by_the_next_forget(tmp_path):
    root, origin = _pair(tmp_path)
    recreate.share_origin(root, "p", "q")
    import os
    os.rename(origin, root / "q" / forget_mod.HANDOVER)      # cut after its first step
    forget_mod.forget(root, "p")
    own = root / "q" / "origin"
    assert own.is_dir() and not own.is_symlink() and (own / "b.txt").read_bytes() == b"b"


def test_the_holder_is_not_renamed_from_under_its_reader(tmp_path):
    root, origin = _pair(tmp_path)
    recreate.share_origin(root, "p", "q")
    with pytest.raises(CodexError, match="reads its origin directory where it lies"):
        rename_mod.rename(root, "p", "s")
    assert origin.is_dir() and (root / "q" / "origin").resolve() == origin.resolve()
