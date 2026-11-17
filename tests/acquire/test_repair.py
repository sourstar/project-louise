"""A FETCHED DUMP MENDED, NEVER A DUMB FULL RE-DOWNLOAD (app/acquire/repair.py;
operator, 2026-11-17: "If available, a resume is always preferable over a
dumb full re-download"). Against a loopback origin that serves Range: (a) a
short file resumes from its length; (b) a full-length file with wrong bytes
is mended by its bad bzip2 units alone — a stream of a multistream file, a
block of a single stream — fetched again by Range and written in place;
(c) only units that cannot be told apart go again in full, said why. Every
byte is drawn from the budget; a stop ends at a region or a range and the
next pass resumes there (invariant 2)."""
from __future__ import annotations

import bz2
import hashlib
import random
import threading
from pathlib import Path

import pytest

from app.acquire import budget, repair
from tests.acquire.mock_http_server import MockServer

WORDS = [f"word{i}".encode() for i in range(2000)]


def text(n: int, seed: int) -> bytes:
    rnd = random.Random(seed)
    return b" ".join(rnd.choice(WORDS) for _ in range(n))


@pytest.fixture(scope="module")
def multistream() -> tuple[bytes, list[int]]:
    """Wikipedia's shape: a stream per 'hundred pages', 40 of them."""
    streams = [bz2.compress(text(3000, s), 9) for s in range(40)]
    starts, at = [], 0
    for s in streams:
        starts.append(at)
        at += len(s)
    return b"".join(streams), starts + [at]


@pytest.fixture(scope="module")
def single() -> bytes:
    """Wikidata's shape (lbzip2's output): one stream of several 900 kB blocks."""
    return bz2.compress(text(400_000, 99), 9)


@pytest.fixture
def origin(tmp_path):
    srv = MockServer(tmp_path / "origin")
    yield srv
    srv.httpd.shutdown()


def serve(origin, name: str, data: bytes) -> str:
    p = origin.root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return f"{origin.base_url}/{name}"


def held(tmp_path, data: bytes, name="dump.bz2") -> Path:
    p = tmp_path / "corpus" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def flipped(data: bytes, *at: int) -> bytes:
    b = bytearray(data)
    for i in at:
        b[i] ^= 0x24
    return bytes(b)


def want(data: bytes, name="dump.bz2", size=True) -> repair.Expected:
    return repair.Expected(rel_path=name, md5=hashlib.md5(data).hexdigest(), size=len(data) if size else None)


@pytest.fixture
def drawn(monkeypatch):
    got: list[int] = []
    monkeypatch.setattr(budget, "acquire_bytes", got.append)
    return got


def test_a_file_as_the_origin_has_it_is_ok_and_nothing_moves(tmp_path, origin, multistream, drawn):
    data, _ = multistream
    url = serve(origin, "dump.bz2", data)
    m = repair.ensure(held(tmp_path, data), want(data), url=url, headers={}, log=lambda s: None)
    assert m.state == "ok" and m.refetched == 0 and drawn == [] and origin.request_counts == {}
    assert m.blake2 == hashlib.blake2b(data, digest_size=16).hexdigest()


def test_one_bad_stream_of_a_multistream_file_is_all_that_is_fetched_again(tmp_path, origin, multistream, drawn):
    data, starts = multistream
    url = serve(origin, "dump.bz2", data)
    k = 23
    bad_at = (starts[k] + starts[k + 1]) // 2
    p = held(tmp_path, flipped(data, bad_at))
    m = repair.ensure(p, want(data), url=url, headers={}, log=lambda s: None, region=16 << 10)
    assert m.state == "mended", m.says
    assert p.read_bytes() == data, "the origin's bytes, written in place"
    ((a, b),) = m.ranges
    assert starts[k] <= a <= bad_at < b <= starts[k + 1], "the bad stream's bytes, never its neighbours'"
    assert m.refetched == b - a < (starts[k + 1] - starts[k]) and m.refetched < len(data) / 30
    assert origin.range_requests["dump.bz2"] == [f"bytes={a}-{b - 1}"], "one Range request"
    assert sum(drawn) == m.refetched, "every byte from the budget (invariant 5)"
    assert not repair.sidecar_of(p).exists()


def test_one_bad_block_of_a_single_stream_is_all_that_is_fetched_again(tmp_path, origin, single, drawn):
    url = serve(origin, "dump.bz2", single)
    blocks = repair.find_magic(single, repair.BLOCK_MAGIC)
    assert len(blocks) >= 3 and single[:4] == b"BZh9"
    bad_at = (blocks[1] // 8 + blocks[2] // 8) // 2
    p = held(tmp_path, flipped(single, bad_at))
    m = repair.ensure(p, want(single, size=False), url=url, headers={}, log=lambda s: None, region=256 << 10)
    assert m.state == "mended" and p.read_bytes() == single
    ((a, b),) = m.ranges
    assert blocks[1] // 8 <= a <= bad_at < b <= -(-blocks[2] // 8) + 1
    # the length asked of the origin by a one-byte Range (the sums name none), then the block
    assert origin.range_requests["dump.bz2"] == ["bytes=0-0", f"bytes={a}-{b - 1}"]
    assert sum(drawn) == m.refetched + 1


def test_a_damaged_stream_header_or_end_is_found_by_the_walk(tmp_path, origin, single, drawn):
    url = serve(origin, "dump.bz2", single)
    for at in (2, len(single) - 3):               # the header's level byte; the end's combined CRC
        p = held(tmp_path, flipped(single, at))
        m = repair.ensure(p, want(single), url=url, headers={}, log=lambda s: None, region=256 << 10)
        assert m.state == "mended" and p.read_bytes() == single, (at, m.says)
        assert any(a <= at < b for a, b in m.ranges) and m.refetched < len(single) / 2


def test_a_short_file_resumes_from_its_length(tmp_path, origin, multistream, drawn):
    data, _ = multistream
    url = serve(origin, "dump.bz2", data)
    p = held(tmp_path, data[:len(data) // 3])
    m = repair.ensure(p, want(data, size=False), url=url, headers={}, log=lambda s: None)
    assert m.state == "resumed" and p.read_bytes() == data
    assert origin.range_requests["dump.bz2"] == ["bytes=0-0", f"bytes={len(data) // 3}-{len(data) - 1}"], \
        "the length, then the rest from the file's own length: never byte 0 again"
    assert m.refetched == len(data) - len(data) // 3


def test_a_longer_file_is_cut_at_the_origins_length(tmp_path, origin, multistream, drawn):
    data, _ = multistream
    url = serve(origin, "dump.bz2", data)
    p = held(tmp_path, data + b"junk past the end")
    m = repair.ensure(p, want(data), url=url, headers={}, log=lambda s: None)
    assert (m.state, m.refetched) == ("cut", 0) and p.read_bytes() == data, m.says
    assert origin.request_counts == {}, "the length was the record's: nothing asked"


@pytest.mark.parametrize("case", ["gzip", "all blocks check out", "more than half bad"])
def test_what_cannot_be_told_apart_goes_again_in_full_and_says_why(tmp_path, origin, multistream, case):
    import gzip
    data, starts = multistream
    if case == "gzip":
        data = gzip.compress(data)
        p = held(tmp_path, flipped(data, len(data) // 2), name="props.sql.gz")
        w, why = want(data, name="props.sql.gz"), "not a bzip2 file"
    elif case == "all blocks check out":
        p = held(tmp_path, data)
        w, why = repair.Expected("dump.bz2", "0" * 32, len(data)), "checks out"
    else:
        p = held(tmp_path, flipped(data, *[(starts[i] + starts[i + 1]) // 2 for i in range(0, 40, 1)]))
        w, why = want(data), "more than 50%"
    url = serve(origin, p.name, data)
    m = repair.ensure(p, w, url=url, headers={}, log=lambda s: None, region=16 << 10)
    assert m.state == "again" and why in m.says and "fetched again in full" in m.says, m.says
    assert not p.exists() and not repair.sidecar_of(p).exists(), "removed: the next fetch takes it whole"


def test_a_stop_ends_at_a_region_and_the_next_pass_resumes_there(tmp_path, origin, multistream, monkeypatch):
    from app import parallel
    data, starts = multistream
    url = serve(origin, "dump.bz2", data)
    bad_at = (starts[30] + starts[31]) // 2
    p = held(tmp_path, flipped(data, bad_at))
    stop = threading.Event()
    stop.set()
    tasks: list = []
    real = parallel.each_part
    monkeypatch.setattr(parallel, "each_part", lambda fn, ts, **k: tasks.append(len(ts)) or real(fn, ts, **k))
    region = 64 << 10
    with pytest.raises(repair.Stopped, match="checked"):
        repair.ensure(p, want(data), url=url, headers={}, stop=stop, log=lambda s: None, region=region)
    side = repair.sidecar_of(p)
    assert side.exists() and '"regions": 1' in side.read_text(), "recorded after the region it ended"
    m = repair.ensure(p, want(data), url=url, headers={}, log=lambda s: None, region=region)
    n = -(-len(data) // region)
    assert tasks == [n, n - 1], "the second pass checks only the regions the first did not"
    assert m.state == "mended" and p.read_bytes() == data


def test_a_stop_in_the_fetch_resumes_the_ranges_left(tmp_path, origin, multistream):
    data, starts = multistream
    url = serve(origin, "dump.bz2", data)
    p = held(tmp_path, flipped(data, (starts[5] + starts[6]) // 2, (starts[30] + starts[31]) // 2))
    stop = threading.Event()

    class StopsAfterOne(repair._Origin):
        def write(self, f, start, end):
            super().write(f, start, end)
            stop.set()
    with pytest.raises(repair.Stopped, match="1 of 2 bad range"):
        repair.ensure(p, want(data), url=url, headers={}, stop=stop, log=lambda s: None,
                      origin=StopsAfterOne(url, {}), region=16 << 10)
    first = list(origin.range_requests["dump.bz2"])
    assert len(first) == 1
    m = repair.ensure(p, want(data), url=url, headers={}, log=lambda s: None, region=16 << 10)
    assert m.state == "mended" and p.read_bytes() == data
    assert len(origin.range_requests["dump.bz2"]) == 2, "the range done is not fetched again"
    assert m.refetched == sum(b - a for a, b in m.ranges)


def test_the_region_check_runs_on_the_parse_pool(tmp_path, multistream, monkeypatch):
    """Multi-threading is a must: the regions go to `parallel.each_part`, a
    child per core the node allows — here two spawned children."""
    from app import config, parallel
    data, starts = multistream
    p = held(tmp_path, flipped(data, (starts[12] + starts[13]) // 2))
    monkeypatch.setattr(config, "PARSE_PROCESSES", 2)
    bounds = [(str(p), a, min(len(data), a + (128 << 10)), len(data)) for a in range(0, len(data), 128 << 10)]
    walk = repair.Walk()
    for events in parallel.each_part(repair.check_region, bounds):
        for ev in events:
            walk.take(ev)
    ((a, b),) = walk.finish(len(data))
    assert starts[12] <= a < b <= starts[13] and walk.streams == 40
