"""The block index of one bzip2 stream (app/bz2_blocks.py): where each block
begins, in bits, where its bytes fall in the decompressed file, and the unit
lines begun before it — held to the truth line by line; resumed after a stop
or a deadline to the same bytes; read in segments to the same bytes; derived
data keyed on the file's physical identity and built again when it changes
(invariant 6)."""
from __future__ import annotations

import bz2
import json
import os
import random
import time
from pathlib import Path

import pytest

from app import bz2_blocks as bb

LINES = bb.Lines(opens=b"{", head=b"[", tail=b"]")
NAME = "wikidata-20261012-all.json.bz2"


def _units(n: int = 3000, seed: int = 5) -> list[bytes]:
    rng = random.Random(seed)
    words = ["".join(rng.choice("abcdefghij") for _ in range(rng.randint(2, 9))) for _ in range(3000)]
    return [json.dumps({"id": f"Q{i}", "t": " ".join(rng.choice(words) for _ in range(rng.randint(3, 400)))}).encode()
            for i in range(n)]


def _dump(d: Path, units: list[bytes], *, level: int = 1, body: bytes | None = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / NAME
    p.write_bytes(bz2.compress(body if body is not None else b"[\n" + b",\n".join(units) + b"\n]\n", level))
    return p


def _same(a: bb.BlockIndex, b: bb.BlockIndex) -> bool:
    import numpy as np
    return all(np.array_equal(getattr(a, k), getattr(b, k)) for k in ("bits", "dec", "first", "exact"))


@pytest.fixture
def no_cache(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "CACHE_DIR", None)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", None)


def test_every_unit_line_lies_in_the_blocks_the_index_gives(tmp_path, no_cache):
    units = _units()
    p = _dump(tmp_path, units)
    ix = bb.build(p, LINES, threads=2)
    assert ix.blocks > 20 and ix.units == len(units) and ix.framed(LINES) is None
    assert ix.header == b"BZh1" and ix.size == p.stat().st_size
    data = bz2.decompress(p.read_bytes())
    assert int(ix.dec[-1]) == len(data)
    at = 2
    for e, u in enumerate(units):
        a, b = ix.line_blocks(e)
        end = at + len(u) + (2 if e + 1 < len(units) else 1)        # through its newline
        assert int(ix.dec[a]) <= at < int(ix.dec[a + 1]), e
        assert int(ix.dec[b]) <= end - 1 < int(ix.dec[b + 1]), e
        assert data[at:at + len(u)] == u
        at = end
    # every block's bits begin with the block magic, and the last ends at the end-of-stream magic
    raw = p.read_bytes()
    as_int = int.from_bytes(raw, "big")
    width = 8 * len(raw)
    for k in range(ix.blocks + 1):
        bit = int(ix.bits[k])
        assert (as_int >> (width - bit - 48)) & ((1 << 48) - 1) == (0x314159265359 if k < ix.blocks
                                                                      else 0x177245385090)


def test_a_line_on_a_block_boundary_is_given_the_blocks_it_spans(tmp_path, no_cache):
    """`exact`: a unit line beginning on a block's first byte ends in the
    block before it — the one search the index makes for a line's end."""
    p = _dump(tmp_path, _units())
    ix = bb.build(p, LINES, threads=1)
    data = bz2.decompress(p.read_bytes())
    for k in range(1, ix.blocks):
        d = int(ix.dec[k])
        assert bool(ix.exact[k]) == (data[d - 1:d] == b"\n" and data[d:d + 1] == b"{"), k


def test_a_file_framed_otherwise_says_why(tmp_path, no_cache):
    units = _units(200)
    blank = b"[\n" + b",\n".join(units[:100]) + b",\n\n" + b",\n".join(units[100:]) + b"\n]\n"
    ix = bb.build(_dump(tmp_path / "a", units, body=blank), LINES, threads=1)
    assert "are no unit" in (ix.framed(LINES) or "")
    plain = bb.build(_dump(tmp_path / "b", units, body=b"\n".join(units) + b"\n"), LINES, threads=1)
    assert "its first line is not" in (plain.framed(LINES) or "") or "are no unit" in (plain.framed(LINES) or "")
    assert plain.framed(bb.Lines(opens=b"{")) is None, "framed as one unit a line, no head, no tail"


def test_a_stopped_pass_resumes_to_the_same_index(tmp_path, no_cache, monkeypatch):
    p = _dump(tmp_path, _units())
    whole = bb.build(p, LINES, threads=2)
    bb.index_path(p).unlink()
    monkeypatch.setattr(bb, "CHECKPOINT_S", 0.0)
    monkeypatch.setattr(bb, "STEP", 1 << 16)
    asked = [0]

    def stop() -> bool:
        asked[0] += 1
        return asked[0] in (15, 40)

    stops = []
    got = None
    for _ in range(5):
        try:
            got = bb.build(p, LINES, threads=2, stop=stop)
            break
        except bb.Incomplete as e:
            assert e.why == "stopped" and 0 < e.done < e.total
            assert bb.progress(p) == (e.done, e.total)
            stops.append(e.done)
    assert len(stops) == 2 and stops[0] < stops[1], "each pass went on from the last"
    assert got is not None and _same(got, whole)
    assert bb.progress(p) is None and not bb.partial_path(p).exists(), "the pass's state is gone once whole"


def test_a_pass_out_of_time_says_how_far_it_came(tmp_path, no_cache, monkeypatch):
    p = _dump(tmp_path, _units())
    monkeypatch.setattr(bb, "STEP", 1 << 16)
    with pytest.raises(bb.Incomplete, match="its time for this pass ran out"):
        bb.build(p, LINES, threads=1, deadline=time.monotonic())
    assert bb.load(p, LINES) is None
    assert _same(bb.ensure(p, LINES, threads=1), bb.build(p, LINES, threads=1))


def test_segments_give_the_same_index(tmp_path, no_cache, monkeypatch):
    """The reader begun again at a checkpoint every SEGMENT decompressed
    bytes, so its map of blocks never holds the whole file's."""
    p = _dump(tmp_path, _units())
    whole = bb.build(p, LINES, threads=2)
    bb.index_path(p).unlink()
    monkeypatch.setattr(bb, "SEGMENT", 300_000)
    monkeypatch.setattr(bb, "STEP", 1 << 15)
    opened = []
    import indexed_bzip2
    real = indexed_bzip2.open
    monkeypatch.setattr(indexed_bzip2, "open", lambda src, **k: opened.append(src) or real(src, **k))
    assert _same(bb.build(p, LINES, threads=2), whole)
    assert len(opened) > 3, "begun again segment by segment"


def test_the_index_is_kept_by_the_file_s_identity_and_built_again_when_it_changes(tmp_path, no_cache, monkeypatch):
    p = _dump(tmp_path, _units())
    first = bb.ensure(p, LINES, threads=1)
    assert first.path == p.parent / ".pack" / (NAME + bb.SUFFIX), "beside the file without a fast cache"
    import indexed_bzip2
    opened = []
    real = indexed_bzip2.open
    monkeypatch.setattr(indexed_bzip2, "open", lambda src, **k: opened.append(src) or real(src, **k))
    again = bb.ensure(p, LINES, threads=1)
    assert opened == [] and _same(again, first), "fresh: read back, not built"
    # the file rewritten in place (another stream of the same name)
    time.sleep(0.01)
    _dump(tmp_path, _units(seed=6))
    assert bb.load(p, LINES) is None, "stale once the file changed"
    rebuilt = bb.ensure(p, LINES, threads=1)
    assert len(opened) == 1 and not _same(rebuilt, first)
    # the file replaced by another of the same bytes and times (a new inode)
    q = p.with_name(NAME + ".new")
    q.write_bytes(p.read_bytes())
    st = p.stat()
    os.utime(q, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(q, p)
    assert bb.load(p, LINES) is None, "keyed on the inode too"


def test_the_index_is_placed_on_the_fast_cache_by_derived(tmp_path, monkeypatch):
    from app import config, derived
    cache = tmp_path / "fast"
    monkeypatch.setattr(config, "CACHE_DIR", cache)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", None)
    p = _dump(tmp_path / "corpus", _units(300))
    ix = bb.ensure(p, LINES, threads=1)
    assert ix.path == cache / bb.KIND / derived.tag(p) / (NAME + bb.SUFFIX)
    assert not (p.parent / ".pack").exists()


def test_what_is_not_one_bzip2_stream_is_refused(tmp_path, no_cache):
    (tmp_path / "x").mkdir()
    plain = tmp_path / "x" / NAME
    plain.write_bytes(b"[\n{}\n]\n")
    with pytest.raises(bb.BlockIndexError, match="stream header"):
        bb.build(plain, LINES, threads=1)
    units = _units(800)
    two = tmp_path / "y" / NAME
    two.parent.mkdir()
    half = len(units) // 2
    two.write_bytes(bz2.compress(b"[\n" + b",\n".join(units[:half]) + b",\n", 1)
                    + bz2.compress(b",\n".join(units[half:]) + b"\n]\n", 1))
    with pytest.raises(bb.BlockIndexError, match="more than one"):
        bb.build(two, LINES, threads=1)


def test_another_builder_defers_this_one(tmp_path, no_cache):
    from app import derived
    p = _dump(tmp_path, _units(300))
    out = bb.index_path(p)
    with derived.writing(out.with_name(out.name + ".lock"), "another pass"), pytest.raises(derived.Busy):
        bb.build(p, LINES, threads=1)
