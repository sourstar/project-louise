"""An origin DIRECTORY a re-created collection or a sister reads is read
where it lies, through a symbolic link, and handed over at its holder's
forget — never a hard link (operator, 2026-11-27: "Never use hard links,
always consciously load from a path"; app/file_copy.py)."""
from __future__ import annotations

from pathlib import Path

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


# ---- finding 4: a hand-over cut at any step, three heirs ---------------------

class Killed(Exception):
    """The process killed at a step."""


def _three(tmp_path):
    root, origin = _pair(tmp_path)
    for n in ("r", "s"):
        (root / n).mkdir()
        (root / f"{n}.toml").write_text('codex = "sha256:00"\n')
    for n in ("q", "r", "s"):
        recreate.share_origin(root, "p", n)
    return root, origin


@pytest.mark.parametrize("cut", ["after the first rename", "after the second rename", "after one relink"])
def test_a_hand_over_killed_at_any_step_is_completed_for_every_heir(tmp_path, monkeypatch, cut):
    import os
    root, origin = _three(tmp_path)
    renames, relinks = [], []
    real_rename, real_link = os.rename, forget_mod._relative_link

    def rename(a, b):
        real_rename(a, b)
        renames.append(b)
        if (cut == "after the first rename" and len(renames) == 1) or \
                (cut == "after the second rename" and len(renames) == 2):
            raise Killed(cut)

    def relink(target, link):
        real_link(target, link)
        relinks.append(link)
        if cut == "after one relink":
            raise Killed(cut)
    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(forget_mod, "_relative_link", relink)
    with pytest.raises(Killed):
        forget_mod.forget(root, "p")
    assert (root / ".origin-handover.p.json").is_file(), "the journal outlives the kill"
    monkeypatch.setattr(os, "rename", real_rename)
    monkeypatch.setattr(forget_mod, "_relative_link", real_link)
    forget_mod.forget(root, "p")
    own = root / "q" / "origin"
    assert own.is_dir() and not own.is_symlink() and (own / "lang=en" / "a.txt").read_bytes() == b"a" * 100
    for n in ("r", "s"):
        assert (root / n / "origin").is_symlink() and (root / n / "origin").resolve() == own.resolve(), n
    assert not (root / "p").exists() and not list(root.glob(".origin-handover.*"))


# ---- finding 7: a hand-over across filesystems is refused before anything --------

def test_a_hand_over_across_filesystems_is_refused_before_anything(tmp_path, monkeypatch):
    root, origin = _pair(tmp_path)
    recreate.share_origin(root, "p", "q")
    real = forget_mod._device
    monkeypatch.setattr(forget_mod, "_device", lambda p: real(p) + (1 if Path(p).name == "q" else 0))
    for dry in (True, False):
        with pytest.raises(CodexError, match="on another filesystem: the hand-over is a rename"):
            forget_mod.forget(root, "p", dry_run=dry)
    assert (root / "p.toml").is_file() and origin.is_dir() and (root / "q" / "origin").is_symlink(), \
        "nothing was done"


# ---- finding H: the heir's write lock; the caches keyed on the origin ------------

def test_the_hand_over_takes_the_heir_s_write_lock_before_anything(tmp_path):
    from app.hold import lock as lock_mod
    root, origin = _pair(tmp_path)
    recreate.share_origin(root, "p", "q")
    held = lock_mod.take(lock_mod.state_dir(root, "q"), "q", "the worker")
    try:
        with pytest.raises(lock_mod.Held):
            forget_mod.forget(root, "p")
        assert (root / "p.toml").is_file() and origin.is_dir(), "nothing removed"
    finally:
        held.release()
    forget_mod.forget(root, "p")
    assert (root / "q" / "origin").is_dir() and not (root / "q" / "origin").is_symlink()


def test_the_caches_keyed_on_the_origin_move_with_it(tmp_path, monkeypatch):
    from app import derived
    root, origin = _pair(tmp_path)
    recreate.share_origin(root, "p", "q")
    cache = tmp_path / "cache" / "pack" / derived.key(origin.resolve())
    cache.mkdir(parents=True)
    (cache / "built").write_bytes(b"c")
    monkeypatch.setattr(derived, "held", lambda: [p for p in (tmp_path / "cache" / "pack").iterdir()])
    forget_mod.forget(root, "p")
    moved = tmp_path / "cache" / "pack" / derived.key((root / "q" / "origin").resolve())
    assert not cache.exists() and (moved / "built").read_bytes() == b"c", "carried, never rebuilt"
