"""What the Library's p2p refresh asked for every part, asked once
(2026-11-22, München's profile of /admin/library: 16,426 calls of
`contribute.predecessors`, two files opened and parsed each, and 76,517 of
`Codex.space_id`, each a digest of the space)."""
from __future__ import annotations

import json
import os

from app.hold import codex as cx
from app.share import contribute, possession
from tests.hold.conftest import pubmed


def _reads(monkeypatch) -> list:
    seen: list = []
    real = json.loads

    def counting(s, *a, **k):
        seen.append(1)
        return real(s, *a, **k)
    monkeypatch.setattr(json, "loads", counting)
    return seen


def test_predecessors_are_read_once_until_a_file_changes(tmp_path, monkeypatch):
    a, b, c = (f"sha256:{x * 64}" for x in "abc")
    contribute.add_predecessor(tmp_path, "c", a)
    state = tmp_path / "c" / "state"
    assert contribute.predecessors(tmp_path, "c") == [a]
    seen = _reads(monkeypatch)
    for _ in range(100):
        assert contribute.predecessors(tmp_path, "c") == [a]
    assert seen == [], "unchanged files: not read again"
    contribute.add_predecessor(tmp_path, "c", b)
    assert contribute.predecessors(tmp_path, "c") == [a, b], "an append is seen at the next call"
    # a carry's marker, written beside it: seen too, first in the list
    tmp = state / ".carry.json.tmp"
    tmp.write_text(json.dumps({"done": True, "same_space": True, "predecessors": [c]}), encoding="utf-8")
    os.replace(tmp, state / "carry.json")
    assert contribute.predecessors(tmp_path, "c") == [c, a, b]
    (state / "carry.json").unlink()
    assert contribute.predecessors(tmp_path, "c") == [a, b], "a marker removed is seen"
    got = contribute.predecessors(tmp_path, "c")
    got.append("mutated")
    assert contribute.predecessors(tmp_path, "c") == [a, b], "a caller's list is its own"


def test_the_prefixes_name_the_space_without_hashing_it_again(tmp_path, monkeypatch):
    codex = pubmed()
    calls = []
    real = cx.Codex._space_id_of
    monkeypatch.setattr(cx.Codex, "_space_id_of", lambda self, sp: calls.append(1) or real(self, sp))
    first = possession.prefixes(tmp_path, "c", codex)
    for _ in range(100):
        assert possession.prefixes(tmp_path, "c", codex) == first
    assert len(calls) == 1, "the space id is the Codex's, computed once"
    assert first == [f"{codex.collection_id}/{codex.space_segment}/"]


def test_a_replaced_codex_has_its_own_space_id():
    import dataclasses
    codex = pubmed()
    before = codex.space_id
    other = dataclasses.replace(codex, space=dataclasses.replace(codex.space, ctx=codex.space.ctx + 1))
    assert other.space_id != before and codex.space_id == before
    assert other.space_segment == cx.segment_of(other.space_id)
