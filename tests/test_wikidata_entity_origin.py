"""Wikidata parser 3: a record's `origin` names its ENTITY, resolved in the
snapshot's run through the entity table the node builds of the run's pack
(09 §9.5 amended 2026-11-17, the operator's "Choice B" and "Do the wikidata
step as well"; app/plugins/wikidata/entities.py, app/keyed_origin.py).

The form; both forms resolving to the line discovery names; the table —
built in one pass from the pack, placed with it by the dump's physical
identity, rebuilt when the dump changes; parser 2's line carried with its
origin alone rewritten, equal to a parse at parser 3, line for line and part
for part; and the update pass refusing until the origin's completion signal
is verified."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import collections as collections_mod
from app import keyed_origin, plugin_io
from app.hold import line_carry
from app.pack import build as pack_build
from app.pack import records as pack_records
from app.plugins.base import DiscoverState
from app.plugins.wikidata import entities, ids, pack
from app.plugins.wikidata.source import (
    EntityNotInRun,
    WikidataSource,
    WikidataSourceV2,
    entity_origin,
)
from tests.test_plugins_wikidata_source import a_dump, entity_lines

RUN = "20261012"
V3, V2 = WikidataSource(), WikidataSourceV2()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    pack.reset_caches()
    entities.reset()
    yield
    pack.reset_caches()
    entities.reset()


def _refs(root: Path):
    (root / "state").mkdir(exist_ok=True)
    return list(V3.discover(DiscoverState(corpus_root=root, state_dir=root / "state")))


def test_parser_3_names_the_entity_and_parser_2_the_ordinal(tmp_path):
    a_dump(tmp_path)
    ref = _refs(tmp_path)[-1]
    assert ref.native_id == "3331"
    assert plugin_io.record_origin(V3, ref.locator, tmp_path, ref.native_id) == \
        "wikidatawiki/entities/all.json.bz2#Q3331"
    old = plugin_io.record_origin(V2, ref.locator, tmp_path, ref.native_id)
    assert old == f"wikidatawiki/entities/{RUN}/wikidata-{RUN}-all.json.bz2#5"
    assert V3.carried_origin(old, ref.native_id, 2) == "wikidatawiki/entities/all.json.bz2#Q3331"
    assert V3.carried_origin(old, ref.native_id, 1) is None, "a version the carry does not know is parsed"
    p31 = str(ids.PROPERTY_BASE + 31)
    assert entity_origin(f"wikidatawiki/entities/{RUN}/wikidata-{RUN}-all.json.bz2#3", p31) == \
        "wikidatawiki/entities/all.json.bz2#P31", "a property by its own id"
    with pytest.raises(ValueError, match="names Q5"):
        entity_origin("wikidatawiki/entities/all.json.bz2#Q5", "42")
    with pytest.raises(ValueError, match="not a wikidata origin"):
        entity_origin("elsewhere#1", "42")
    assert keyed_origin.declared(V3).built and keyed_origin.declared(V2) is None
    assert V2.origin_names_run and not V3.origin_names_run
    reg = collections_mod.PLUGIN_REGISTRY["wikidata"]
    assert reg.PARSER_VERSION == 3 and collections_mod.plugin_at(reg, 2).PARSER_VERSION == 2


def test_both_forms_resolve_to_the_line_discovery_names(tmp_path):
    a_dump(tmp_path)
    for ref in _refs(tmp_path):
        new = plugin_io.record_origin(V3, ref.locator, tmp_path, ref.native_id)
        old = plugin_io.record_origin(V2, ref.locator, tmp_path, ref.native_id)
        for plugin in (V3, V2):
            for origin in (new, old):
                local, locator = plugin_io.local_locator(plugin, origin, tmp_path, run=RUN)
                assert locator == ref.locator and local.is_file(), (plugin.PARSER_VERSION, origin)
        assert plugin_io.local_locator(V3, old, tmp_path)[1] == ref.locator, "the ordinal needs no run"
    with pytest.raises(ValueError, match="read in a run"):
        plugin_io.local_locator(V3, "wikidatawiki/entities/all.json.bz2#Q42", tmp_path)
    with pytest.raises(EntityNotInRun, match="holds no Q999"):
        plugin_io.local_locator(V3, "wikidatawiki/entities/all.json.bz2#Q999", tmp_path, run=RUN)
    with pytest.raises(FileNotFoundError):
        plugin_io.local_locator(V3, "wikidatawiki/entities/all.json.bz2#Q42", tmp_path, run="20261101")
    unit, size = plugin_io.origin_unit(V3, "wikidatawiki/entities/all.json.bz2#Q42", tmp_path, run=RUN)
    assert unit == f"wikidatawiki/entities/{RUN}/wikidata-{RUN}-all.json.bz2" and size > 0


def test_the_entity_table_is_built_from_the_pack_keyed_on_the_dump_and_rebuilt_when_it_moves(
        tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.CACHE_DIR", tmp_path / "cache")
    dump = a_dump(tmp_path / "one")
    other = a_dump(tmp_path / "two", lines=entity_lines()[::-1])
    for root in (tmp_path / "one", tmp_path / "two"):
        _refs(root)                                        # discovery builds each pack
    built = []
    real = entities.build
    monkeypatch.setattr(entities, "build", lambda d, **k: built.append(d) or real(d, **k))
    assert entities.index_of(dump, "42") == 0 and entities.index_of(other, "42") == 5
    t1, t2 = entities.path_of(dump), entities.path_of(other)
    assert t1 != t2 and t1.parent == pack.pack_dir(dump), "a side table of the pack, keyed on the dump"
    assert str(t1).startswith(str(tmp_path / "cache")), "placed by app/derived.py under LOUISE_CACHE_DIR"
    assert entities.index_of(dump, "33010486") == 2, "every entity of the dump, a pruned one too"
    assert entities.index_of(dump, "999") is None
    assert len(built) == 2
    assert entities.index_of(dump, "5") == 1 and len(built) == 2, "fresh: not built again"
    # the dump replaced: its pack and its table are built again
    a_dump(tmp_path / "one", lines=entity_lines()[1:])
    st = dump.stat()
    os.utime(dump, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    pack.reset_caches()
    entities.reset()
    _refs(tmp_path / "one")
    assert entities.index_of(dump, "42") is None and entities.index_of(dump, "5") == 0 and len(built) == 3


def _record(plugin, row, base):
    """`pack_build._record_of`'s line for a row, its layers stubbed (the line
    holds no layer and no chunk)."""
    coll = SimpleNamespace(corpus_dir=base, codex=SimpleNamespace(record_line=1), name="wikidata")
    layers = SimpleNamespace(collection=coll, index=lambda nid: 0,
                             chunker_at=lambda layer: (lambda *a, **k: iter(())))
    token = pack_build._task_layers.set(layers)
    try:
        record = pack_build._record_of(coll, plugin, row)[0]
    finally:
        pack_build._task_layers.reset(token)
    return pack_records.canonical_line(record)


def test_parser_2s_line_carried_is_a_parse_at_parser_3_line_for_line_and_part_for_part(tmp_path):
    a_dump(tmp_path)
    refs = [r for r in _refs(tmp_path) if r.scope == "document"]
    rows = [{"uid": f"wikidata:{r.native_id}", "native_id": r.native_id, "path": r.locator} for r in refs]
    old = [_record(V2, row, tmp_path) for row in rows]
    new = [_record(V3, row, tmp_path) for row in rows]
    carry = lambda o, n: V3.carried_origin(o, n, 2)
    assert [line_carry.rewrite_line(line, carry)[0] + b"\n" for line in old] == new, "line for line"
    # part for part: parser 2's part carried, against parser 3's part written by the build's writer
    src, book = tmp_path / "p2" / "00001.jsonl.zst", tmp_path / "p3" / "00001.jsonl.zst"
    pack_records.write_shard(src, [pack_records.loads_line(x) for x in old], lines=old, frame_records=2)
    pack_records.write_shard(book, [pack_records.loads_line(x) for x in new], lines=new, frame_records=2)
    got = line_carry.carry_part(str(src), str(tmp_path / "out.carry"), "wikidata", 3, 2, None, False)
    assert Path(got["dst"]).read_bytes() == book.read_bytes() and got["changed"] == len(rows)
    # and records-vs-origin's re-parse, in the run, writes the same line
    for row, line in zip(rows, new):
        origin = pack_records.loads_line(line)["origin"]
        _local, locator = plugin_io.local_locator(V3, origin, tmp_path, run=RUN)
        assert _record(V3, {**row, "path": locator}, tmp_path) == line


def test_the_update_pass_refuses_wikidata_until_its_completion_signal_is_verified():
    from app.hold import update
    codex = SimpleNamespace(plugin="wikidata", parser=SimpleNamespace(version=3), update=SimpleNamespace(),
                            origin=SimpleNamespace(spec={"delta": {"mode": "dated-dumps"}}))
    why = update.not_updated(codex)
    assert why is not None and "not verified yet" in why and "md5sums" in why
    assert not update.origin_names_run(codex)
    assert update.origin_names_run(SimpleNamespace(plugin="wikidata", parser=SimpleNamespace(version=2)))
