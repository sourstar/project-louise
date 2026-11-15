"""An origin's bulk file read from the files cache (app/derived.py `staged`).

Operator, 2026-11-15: "Whatever reading is actively ongoing on bulk, you can
use ssd3 as cache", then "Make this kind of ssd3 caching the norm". A file a
pass reads at random — EUR-Lex's Data Dump zips, a seek per member — is read
from a copy in the files cache, made upon need, current only while its size
and time are the origin's, and gone when idle like every files-cache unit;
without a files cache, or without room, the reader stays on the origin."""
from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

from app import config, derived
from app.acquire import datadump


@pytest.fixture
def caches(tmp_path, monkeypatch):
    fast, files = tmp_path / "ssd5", tmp_path / "ssd3"
    monkeypatch.setattr(config, "CACHE_DIR", fast)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", files)
    monkeypatch.setattr(config, "PUBMED_PACK_DIR", None, raising=False)
    derived._touched.clear()
    return files


def _origin(tmp_path: Path, data: bytes = b"PK-bytes") -> Path:
    o = tmp_path / "tank" / "datadump-it" / "LEG_MTD.zip"
    o.parent.mkdir(parents=True, exist_ok=True)
    o.write_bytes(data)
    return o


def test_a_bulk_file_is_read_from_its_copy_in_the_files_cache(caches, tmp_path):
    o = _origin(tmp_path)
    said: list[str] = []
    got = derived.staged(o, log=said.append)
    assert got == derived.place(derived.ORIGIN_COPY, o) / o.name
    assert str(got).startswith(str(caches)), "under the files cache, keyed on the origin's identity"
    assert got.read_bytes() == o.read_bytes() and int(got.stat().st_mtime) == int(o.stat().st_mtime)
    assert any("staged in" in s for s in said), said
    said.clear()
    assert derived.staged(o, log=said.append) == got and said == [], "current: no second copy"
    assert not [p for p in got.parent.iterdir() if p.name.endswith(".tmp")]


def test_a_changed_origin_is_copied_again(caches, tmp_path):
    o = _origin(tmp_path)
    first = derived.staged(o)
    o.write_bytes(b"PK-other-and-longer")
    t = time.time() + 5
    os.utime(o, (t, t))
    again = derived.staged(o)
    assert again == first and again.read_bytes() == b"PK-other-and-longer"


def test_no_files_cache_or_no_room_leaves_the_reader_on_the_origin(caches, tmp_path, monkeypatch):
    o = _origin(tmp_path)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", None)
    assert derived.staged(o) == o
    monkeypatch.setattr(config, "FILES_CACHE_DIR", caches)
    full = shutil._ntuple_diskusage(total=1000, used=995, free=5)
    monkeypatch.setattr(derived.shutil, "disk_usage", lambda p: full)
    said: list[str] = []
    assert derived.staged(o, log=said.append) == o
    assert any("not staged" in s and "read from its origin" in s for s in said), said
    assert not (derived.place(derived.ORIGIN_COPY, o) / o.name).exists()


def test_a_missing_origin_or_a_file_already_in_a_cache_is_not_staged(caches, tmp_path):
    gone = tmp_path / "tank" / "nothing.zip"
    assert derived.staged(gone) == gone
    inside = caches / "neuris-snapshot" / "x" / "legislation.zip"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"z")
    assert derived.staged(inside) == inside


def test_an_idle_copy_goes_like_every_files_cache_unit(caches, tmp_path):
    o = _origin(tmp_path)
    copy = derived.staged(o)
    unit = copy.parent
    assert (derived.ORIGIN_COPY, unit) in derived.units()
    old = time.time() - derived.IDLE_S - 60
    for p in (unit, copy, derived.stamp_of(unit)):
        if p.exists():
            os.utime(p, (old, old))
    gone = derived.evict_idle()
    assert [g["kind"] for g in gone] == [derived.ORIGIN_COPY] and not unit.exists()
    assert o.exists(), "the origin is never touched"
    assert derived.staged(o).read_bytes() == o.read_bytes(), "made again upon need"


def test_the_data_dump_reads_its_zips_from_the_files_cache(caches, tmp_path):
    folder = tmp_path / "tank" / "datadump-it"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "LEG_IT_FMX.zip", "w") as z:
        z.writestr("u/fmx4/a.xml", b"<a/>")
    with zipfile.ZipFile(folder / "LEG_MTD.zip", "w") as z:
        z.writestr("u/tree_non_inferred.rdf", b"not rdf")
    (folder / datadump.STATEMENT).write_text(json.dumps({"language": "it",
                                                          "created": "2026-11-08T01:00:00+01:00"}))
    dumps = datadump.Dumps(tmp_path / "tank", log=lambda *_: None)
    dump = dumps.for_language("it")
    assert dump is not None and len(dump.zips) == 2
    assert all(Path(z.filename).parent.parent == caches / derived.ORIGIN_COPY for z in dump.zips), \
        [z.filename for z in dump.zips]
    dumps.mtd_tree("u")
    assert all(str(z.filename).startswith(str(caches)) for z in dumps._mtd)
