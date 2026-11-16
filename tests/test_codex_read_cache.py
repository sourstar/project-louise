"""The Codex is read once per file, not once per load (München, 2026-11-16:
after #720 the worker's next GIL holders were the stats publisher and the
share fetch re-reading every codex.json on every `load_all`). Kept while
the file is the same one — its inode's device, number, size, mtime and
ctime — and read again the moment it is rewritten or replaced; a file that
does not read raises every time."""
from __future__ import annotations

import os

import pytest

from app import collections as collections_mod
from app.hold import codex as cx
from tests.conftest import codex_collection


@pytest.fixture
def held(isolated_config, data_dir, config_dir, monkeypatch):
    monkeypatch.setenv("LOUISE_ROOT", str(data_dir))
    codex_collection("books", louise_root=data_dir, config_dir=config_dir)
    return data_dir / "collections" / "books" / "shard" / "codex.json"


def test_the_same_file_gives_the_same_codex_without_reading_it_again(held, monkeypatch):
    first = collections_mod.read_codex(held)
    assert first == cx.loads(held.read_bytes())
    collections_mod.load_all()                     # the node's other collection, read once too
    monkeypatch.setattr(cx, "loads", lambda data: pytest.fail("read again although the file is the same"))
    assert collections_mod.read_codex(held) is first
    by_load = [c.codex for c in collections_mod.load_all() if c.name == "books"]
    assert by_load == [first] and by_load[0] is first, "load_all shares it, whatever path spells the file"


def test_a_file_rewritten_or_replaced_is_read_again(held):
    first = collections_mod.read_codex(held)
    st = held.stat()
    os.utime(held, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    again = collections_mod.read_codex(held)
    assert again == first and again is not first, "a new mtime: read again"
    replaced = held.with_name("codex.json.new")
    replaced.write_bytes(held.read_bytes())
    os.replace(replaced, held)
    assert collections_mod.read_codex(held) is not again, "a new inode: read again"


def test_a_file_that_does_not_read_is_refused_every_time(held):
    good = held.read_bytes()
    held.write_bytes(good + b" ")
    for _ in range(2):
        with pytest.raises(cx.CodexError):
            collections_mod.read_codex(held)
    held.write_bytes(good)
    assert collections_mod.read_codex(held).collection_id == cx.loads(good).collection_id
