"""An origin that names its unit by the origin's own stable key, resolved in
a run (09 §9.5 amended 2026-11-17; app/keyed_origin.py): every plugin that
declares it implements the platform's hooks, at the parser versions it
declares it from; and the shared table answers a key from one row group."""
from __future__ import annotations

import pyarrow.parquet as pq
import pytest

from app import collections as collections_mod
from app import keyed_origin
from app.keyed_origin import KeyTable, KeyTableError


def _versions(plugin):
    """The plugin at every parser version it carries (`at_parser_version`)."""
    out = [plugin]
    at = getattr(plugin, "at_parser_version", None)
    for v in range(1, int(getattr(plugin, "PARSER_VERSION", 0))):
        older = at(v) if callable(at) else None
        if older is not None:
            out.append(older)
    return out


def test_a_keyed_origin_is_declared_with_its_hooks_and_only_from_its_parser():
    declared = []
    for name, plugin in sorted(collections_mod.PLUGIN_REGISTRY.items()):
        for p in _versions(plugin):
            k = keyed_origin.declared(p)
            if k is None:
                continue
            declared.append((name, p.PARSER_VERSION))
            assert keyed_origin.missing_hooks(p) == [], (name, p.PARSER_VERSION)
            assert not getattr(p, "origin_names_run", False), \
                f"{name} {p.PARSER_VERSION}: an origin naming a key does not name the run"
            assert k.key and k.index and k.since <= p.PARSER_VERSION
    assert ("wikipedia", 6) in declared and ("wikipedia", 5) not in declared


def test_the_table_sorts_refuses_a_key_twice_and_answers_from_one_row_group(tmp_path):
    t = KeyTable("a test table", ("a", "b"))
    keys = [9, 3, 7, 1, 5]
    tables = list(t.tables(keys, {"a": [90, 30, 70, 10, 50], "b": [-9, -3, -7, -1, -5]}, where="x",
                           row_group=2))
    assert [tb.num_rows for tb in tables] == [2, 2, 1]
    w = pq.ParquetWriter(tmp_path / "t.parquet", t.schema())
    for tb in tables:
        w.write_table(tb)
    w.close()
    assert [t.lookup(tmp_path / "t.parquet", k) for k in (1, 5, 9)] == [(10, -1), (50, -5), (90, -9)]
    assert t.lookup(tmp_path / "t.parquet", 4) is None and t.lookup(tmp_path / "t.parquet", 0) is None
    with pytest.raises(KeyTableError, match="key 3 is listed twice"):
        list(t.tables([3, 1, 3], {"a": [0, 0, 0], "b": [0, 0, 0]}, where="y", row_group=8))
    assert [tb.num_rows for tb in t.tables([], {"a": [], "b": []}, where="z", row_group=8)] == [0]
