"""NO HARD LINKS (operator, 2026-11-27, verbatim: "Never use hard links,
always consciously load from a path. It already happened that you deleted
important files because you forgot the hard links").

A hard link is a second name for the same bytes. Nothing at either name says
the other exists, so a write through one changes what the other holds, and
deleting a "copy" that was a link leaves files whose holder nobody counted.
Whatever two owners must both hold is held one of three ways, each named
where it is used:

- **read where it lies (R):** a pass that only reads bytes loads them from
  their own path, with no second name. Where the pass needs them unchanged
  underneath, it records the file's `identity` and checks it before each
  pass and at the end (`check`): a change refuses in words, never a wrong
  result;
- **a real copy (C):** bytes that become another owner's own are copied to
  a hidden name beside the destination and renamed into place, after a
  `preflight` that names the bytes needed and the bytes free. `cp
  --reflink=auto` where the filesystem clones blocks (a reflink is a
  copy-on-write copy: two files, each written and deleted on its own),
  else a byte copy;
- **a move (M):** `os.replace`, only where the operation consumes its
  source by design.

A symbolic link is not a hard link: it names its target's path, and
deleting it never deletes the target. The existing ones stay.

`tests/test_no_hard_links.py` fails on any hard link in app/, tools/ or
deploy/.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

GIB = 2 ** 30

#: (st_dev, st_ino, st_size, st_mtime_ns): the same file, unchanged
Identity = tuple[int, int, int, int]


class NoRoom(OSError):
    """A copy that does not fit where it is written: nothing was copied."""


class Changed(RuntimeError):
    """A file read where it lies changed under the pass reading it."""


def identity(path: Path | str) -> Identity:
    st = os.stat(path)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def fd_identity(fd: int) -> Identity:
    st = os.fstat(fd)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def check(path: Path | str, want: Identity, what: str) -> None:
    """Raise `Changed` unless `path` is still the file `want` describes."""
    try:
        got = identity(path)
    except OSError as e:
        raise Changed(f"{what}: {path} is gone ({e.strerror}) while it was read; nothing "
                      f"was written from it — run it again") from None
    if got != want:
        raise Changed(f"{what}: {path} changed while it was read (another file, or the same one "
                      f"written); nothing was written from it — run it again")


def _existing(path: Path) -> Path:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def tree_bytes(path: Path | str) -> int:
    """The bytes of the regular files under `path` (or of `path`, a file);
    a symbolic link counts nothing."""
    p = Path(path)
    if p.is_symlink():
        return 0
    if p.is_file():
        return p.stat().st_size
    total = 0
    for base, _dirs, files in os.walk(p):
        for f in files:
            try:
                st = os.lstat(Path(base) / f)
            except OSError:
                continue
            if not os.path.islink(Path(base) / f):
                total += st.st_size
    return total


def free_bytes(where: Path | str) -> int:
    return shutil.disk_usage(_existing(Path(where))).free


def preflight(need: int, where: Path | str, what: str) -> None:
    """Raise `NoRoom` when `need` bytes do not fit on the filesystem holding
    `where` (or its nearest existing parent). A copy-on-write filesystem may
    need none of it (`cp --reflink`); that is not counted on."""
    free = free_bytes(where)
    if need > free:
        raise NoRoom(f"{what} copies {need / GIB:.2f} GiB and {free / GIB:.2f} GiB is free on "
                     f"{_existing(Path(where))}: nothing was copied. Free the space, then run it again")


def _cp(src: Path, dst: Path) -> None:
    """`src` (a file or a directory) copied to `dst`, which must not exist:
    a reflink where the filesystem has one, else a byte copy — the bytes,
    never a link: a symbolic link is followed (`-L`), and hard links among
    what is copied are not kept (`--no-preserve=links`)."""
    cp = shutil.which("cp")
    if cp:
        # -L: a symbolic link is copied as the bytes it reaches (a seeded
        # part a link once named is copied whole, finding 8 of the third read)
        r = subprocess.run([cp, "-a", "-L", "--no-preserve=links", "--reflink=auto", str(src), str(dst)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return
        if dst.exists() or dst.is_symlink():
            shutil.rmtree(dst) if dst.is_dir() and not dst.is_symlink() else dst.unlink()
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=False)
    else:
        shutil.copy2(src, dst)


def copy_file(src: Path | str, dst: Path | str) -> None:
    """(C) `src` copied to `dst`: to a hidden name beside it, then renamed
    into place — `dst` is never seen half-written."""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.copying")
    if tmp.exists() or tmp.is_symlink():
        shutil.rmtree(tmp) if tmp.is_dir() and not tmp.is_symlink() else tmp.unlink()
    try:
        _cp(src, tmp)
        os.replace(tmp, dst)
    finally:
        if tmp.exists() or tmp.is_symlink():
            shutil.rmtree(tmp, ignore_errors=True) if tmp.is_dir() else tmp.unlink(missing_ok=True)


def copy_tree(src: Path | str, dst: Path | str) -> int:
    """(C) The directory `src` copied whole to `dst`, which must not exist:
    to a hidden sibling, renamed into place. Returns the files copied."""
    src, dst = Path(src), Path(dst)
    if dst.exists():
        raise FileExistsError(f"{dst} exists: a copy never writes over a directory")
    copy_file(src, dst)
    return sum(len(files) for _b, _d, files in os.walk(dst))


class HardLinkMember(Exception):
    """An archive member that is a hard link: never made (operator,
    2026-11-27: "Never use hard links")."""


def refuse_hard_links(base: str = "data"):
    """A tarfile extraction filter: tarfile's own `base` filter ("data", or
    "tar" where symbolic links must stay as they are), and a HARD-LINK
    member refused in words — both of tarfile's filters let one through
    ("data" one whose target is inside the destination)."""
    import tarfile
    inner = {"data": tarfile.data_filter, "tar": tarfile.tar_filter}[base]

    def keep(member, path):
        if member.islnk():
            raise HardLinkMember(f"{member.name} is a hard link to {member.linkname} in the archive: "
                                 f"never made (no hard links); nothing more is extracted")
        return inner(member, path)
    return keep
