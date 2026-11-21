"""P3 of the EUR-Lex review (2026-11-21), the engine's half: the parse
pool's memory estimate of a unit was its on-disk `size`, and a Formex
PACKAGE (a .zip the unit holds, opened by app/plugins/eurlex/formex.py
`unpack`) is compressed bytes, ~4.3x less than what its parse holds — a big
package went into an uncapped shared child. The plugin now says what a parse
reads (`plugin_io.parse_size`): the eurlex source counts a package's members
unpacked, from the zip's central directory, inflating nothing. The row's
`size` keeps its meaning (discovery's change test)."""
from __future__ import annotations

import os
import zipfile
from types import SimpleNamespace

import pytest

from app import parallel
from app.plugins.eurlex.source import EurlexSource

K = 26


def _unit(tmp_path, *, members: int = 4, member_bytes: int = 200_000):
    """A unit holding its tree, a notice and one Formex package of XML."""
    unit = tmp_path / "lang=en" / "2024" / "32024R1689"
    unit.mkdir(parents=True)
    (unit / "tree.rdf").write_bytes(b"<rdf/>" * 100)
    pkg = unit / "OJABA_L_202401689_ENG.fmx4.zip"
    line = b"<P>Article 1. This Regulation lays down harmonised rules.</P>\n"
    body = (line * (member_bytes // len(line) + 1))[:member_bytes]
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for i in range(members):
            z.writestr(f"L_202401689EN.{i:06d}01.fmx.xml", body)
    on_disk = sum(p.stat().st_size for p in unit.iterdir())
    return unit, on_disk, members * member_bytes


def test_a_package_is_counted_unpacked_and_its_size_keeps_its_meaning(tmp_path, monkeypatch):
    unit, on_disk, unpacked = _unit(tmp_path)
    row = {"uid": "eurlex:1", "path": str(unit), "size": on_disk}
    # the central directory only: reading a member fails the test
    monkeypatch.setattr(zipfile.ZipFile, "read", lambda *a, **k: pytest.fail("a member was inflated"))
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda *a, **k: pytest.fail("a member was opened"))
    got = EurlexSource().parse_size(row)
    assert got == on_disk + unpacked, (got, on_disk, unpacked)
    assert got > 4 * on_disk, "the package's members are what the parse holds"
    assert row["size"] == on_disk, "the row's size is untouched"


def test_a_unit_without_a_package_reads_its_files(tmp_path):
    unit = tmp_path / "lang=de" / "2020" / "32020R0001"
    unit.mkdir(parents=True)
    (unit / "tree.rdf").write_bytes(b"x" * 1000)
    (unit / "L_2020001DE.01000101.fmx.xml").write_bytes(b"y" * 5000)
    (unit / "broken.zip").write_bytes(b"not a zip at all")             # read as the file it is
    row = {"path": str(unit), "size": 6016}
    assert EurlexSource().parse_size(row) == 6016
    assert EurlexSource().parse_size({"path": str(tmp_path / "gone"), "size": 9}) is None


class _Plugin:
    PARSE_PEAK_PER_BYTE = K

    def __init__(self, sizes: dict[str, int]):
        self.sizes = sizes
        self.asked: list[str] = []

    def parse_size(self, row):
        self.asked.append(row["path"])
        return self.sizes.get(row["path"])


def test_the_estimate_takes_what_the_parse_reads(monkeypatch):
    """A 40 MB package unpacking to 172 MB: 1.0 GB estimated on its size, 4.5
    GB on what its parse reads — the large lane's, never a shared child's."""
    monkeypatch.setattr(parallel, "_parse_sizes", type(parallel._parse_sizes)())
    zipped, unpacked = 40 * 2 ** 20, 172 * 2 ** 20
    plugin = _Plugin({"/u/1": unpacked})
    coll = SimpleNamespace(name="eurlex", plugin_instance=lambda: plugin)
    row = {"uid": "e:1", "path": "/u/1", "size": zipped}
    assert parallel._large(K, [row]) == 0, "on its compressed size it is not large"
    assert parallel.learn_parse_sizes(coll, [row, {"uid": "e:2", "path": "/u/2", "size": 7}]) == 2
    assert parallel.parse_size_of(row) == unpacked
    assert parallel._large(K, [row]) == K * unpacked >= parallel.LARGE_PEAK_BYTES
    assert parallel.peak_of(K, {"uid": "e:2", "path": "/u/2", "size": 7}) == K * 7, \
        "a unit the plugin says nothing about keeps its size"
    # asked once per unit, kept by its path and size; a changed unit is asked again
    assert parallel.learn_parse_sizes(coll, [row]) == 0 and plugin.asked == ["/u/1", "/u/2"]
    assert parallel.learn_parse_sizes(coll, [{**row, "size": zipped + 1}]) == 1
    # a plugin that declares no peak, or answers nothing, is never asked
    other = SimpleNamespace(name="g", plugin_instance=lambda: object())
    assert parallel.learn_parse_sizes(other, [{"uid": "g:1", "path": "/g/1", "size": 1}]) == 0


def test_compute_sends_an_unpacked_package_to_the_large_lane(monkeypatch):
    """Through `compute`: the row that is large only unpacked runs as a part
    of its own in the lane, its bound the time its unpacked bytes take."""
    monkeypatch.setattr(parallel, "_parse_sizes", type(parallel._parse_sizes)())
    from app import config, scheduled_checks
    monkeypatch.setattr(scheduled_checks, "cgroup_memory", lambda cgroup=None: None)
    monkeypatch.setattr(config, "PARSE_PROCESSES", 4)
    monkeypatch.setattr(parallel, "_compute", lambda kind, c, part: [(True, r["uid"]) for r in part])
    zipped, unpacked = 40 * 2 ** 20, 172 * 2 ** 20
    plugin = _Plugin({"/u/1": unpacked})
    coll = SimpleNamespace(name="eurlex", plugin_instance=lambda: plugin)
    lanes: list = []

    class Lane:
        def __init__(self, kind, collection, part, allowance):
            lanes.append([r["uid"] for r in part])
            self.part = part

        def add_done_callback(self, cb):
            cb(self)

        def result(self, timeout=None):
            return [(True, r["uid"]) for r in self.part]

        def cancel(self):
            return False

    class Pool:
        def submit(self, fn, kind, collection, part):
            return Lane(kind, collection, part, None)
    monkeypatch.setattr(parallel, "_LaneTask", Lane)
    monkeypatch.setattr(parallel, "pool", lambda: Pool())
    parallel.forget_users()
    rows = [{"uid": "e:0", "path": "/u/0", "size": 10}, {"uid": "e:1", "path": "/u/1", "size": zipped},
            {"uid": "e:2", "path": "/u/2", "size": 10}]
    assert parallel.compute("fts", coll, rows) == [(True, "e:0"), (True, "e:1"), (True, "e:2")]
    assert ["e:1"] in lanes, f"the package alone, in the lane: {lanes}"
    assert parallel.reserved() == 0
    assert parallel._large_lane.acquire(blocking=False)
    parallel._large_lane.release()
