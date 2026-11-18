"""records-vs-origin asks a dated one-stream bzip2 dump by its BLOCKS
(app/acquire/block_origin.py; app/bz2_blocks.py; operator, 2026-11-18: "A"):
wikidata's re-creation at parser 3, its run's dump written as ONE bzip2 stream
of many blocks (compresslevel 1: a block per 100 kB), served with Range by
the acquisition tests' mock origin on loopback — never the real network
(tests/conftest.py asks no origin).

Each record's unit is its blocks, the copy's block index places it, the
blocks are fetched once a check whichever records share them, through the
budgeted Range client, and compared with the copy's; the outcomes are 03
§3.2's."""
from __future__ import annotations

import bz2
import json
import random
import shutil
from pathlib import Path

import pytest

from app import bz2_blocks, louise
from app.acquire import block_origin
from app.hold import carry as carry_mod
from app.hold import check as chk
from app.hold import collection as col
from app.hold import line_carry
from app.hold import snapshot as sn
from tests.acquire.mock_http_server import MockServer
from tests.hold import wikidata_runs as wr
from tests.test_plugins_wikidata_source import entity_lines

RUN = wr.RUN
REL = f"wikidatawiki/entities/{RUN}/wikidata-{RUN}-all.json.bz2"
#: filler entities: an English label (a record each) and ~15 kB of aliases in
#: a language no collection textifies — so the dump spans many blocks while
#: the records stay small
FILLERS = 80


def _filler(n: int, rng: random.Random) -> bytes:
    alias = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz ") for _ in range(15_000))
    return json.dumps({"type": "item", "id": f"Q{900000 + n}",
                       "labels": {"en": {"language": "en", "value": f"Filler {n}"}},
                       "aliases": {"xx": [{"language": "xx", "value": alias}]},
                       "claims": {}, "lastrevid": 1000 + n, "modified": "2026-10-01T00:00:00Z"},
                      separators=(",", ":")).encode()


def many_blocks(corpus: Path, run: str) -> Path:
    """The run's dump as one bzip2 stream of many blocks: the six fixture
    entities, Q42 and Q5 side by side, among the fillers."""
    rng = random.Random(1118)
    real = entity_lines()
    fill = [_filler(n, rng) for n in range(FILLERS)]
    lines = [real[0], real[1]] + fill[:20] + [real[2]] + fill[20:40] + [real[3]] + fill[40:60] \
        + [real[4]] + fill[60:] + [real[5]]
    d = corpus / "wikidatawiki" / "entities" / run
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"wikidata-{run}-all.json.bz2"
    p.write_bytes(bz2.compress(b"[\n" + b",\n".join(lines) + b"\n]\n", 1))
    return p


@pytest.fixture(autouse=True)
def _tokenizer():
    from app.plugins.wikidata import chunker
    wr.register_tokenizer()
    yield
    chunker._tokenizers.pop(wr.TOKENIZER, None)


@pytest.fixture
def recreated(data_dir, config_dir, isolated_config, tmp_path, capsys, monkeypatch):
    """wikidata.next at parser 3 naming its run, its parts carried, its
    snapshot frozen — the runbook's steps 2-6 (test_codex_run's) on a dump of
    many blocks."""
    from app.acquire import backoff
    monkeypatch.setattr(backoff, "SCALE", 0.0)
    root, corpus, _live = wr.live(data_dir, config_dir, dump=many_blocks)
    assert louise.main(wr.create_args(root, wr.whole_recipe(root, tmp_path))) == 0
    capsys.readouterr()
    new = col.read(root, "wikidata.next")
    carry_mod.carry(root, "wikidata", "wikidata.next", rebuilt=True, apply=True, log=lambda *_: None)
    line_carry.carry(root, "wikidata.next", "wikidata", log=lambda *_: None)
    snap = sn.load(root / "wikidata.next" / "shard" / "snapshots" / f"{wr.SID}.json")
    louise._member_origins.clear()
    yield root, corpus, new, snap
    louise._member_origins.clear()


@pytest.fixture
def origin(tmp_path):
    """The mock origin, serving its own copy of the run's file."""
    served = tmp_path / "served"
    served.mkdir()
    server = MockServer(served)
    yield server
    server.shutdown()


def _serve(server: MockServer, corpus: Path) -> Path:
    dst = server.root / REL
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(corpus / REL, dst)
    return dst


def _reader(codex, url: str) -> block_origin.BlockOrigin:
    mo = louise._member_origin(codex)
    assert isinstance(mo, block_origin.BlockOrigin), "a dated one-stream .bz2 dump is asked by its blocks"
    mo.url = url
    return mo


def _check(root, corpus, codex, snap, *, origin_else=True, origin_dir=None):
    """`hold check`'s own path (run_check): the nightly's `--origin-else`
    unless said otherwise."""
    import argparse
    a = argparse.Namespace(root=root, collection="wikidata.next", snapshot=None, seed="00" * 32,
                           checks="records-vs-origin", sample=None, origin_dir=origin_dir,
                           origin_else=(str(corpus) if origin_else else None), origin_mib=None,
                           against=None, shards=None, live=None)
    _name, _codex, _snap, _seed, report = louise.run_check(a)
    return report.outcome("records-vs-origin")


def _ranges(server: MockServer) -> list[tuple[int, int]]:
    out = []
    for h in server.range_requests.get(REL, []):
        a, _, b = h.split("=", 1)[1].partition("-")
        out.append((int(a), int(b)))
    return out


def test_the_fixture_is_one_stream_of_many_blocks(recreated):
    _root, corpus, codex, snap = recreated
    ix = bz2_blocks.build(corpus / REL, bz2_blocks.Lines(opens=b"{", head=b"[", tail=b"]"), threads=2)
    assert ix.blocks >= 10 and ix.units == FILLERS + 6
    assert ix.framed(louise._member_origin(codex).lines) is None
    assert snap.origin_run == RUN


def test_equal_blocks_reproduce_every_record_from_the_origin(recreated, origin, monkeypatch):
    root, corpus, codex, snap = recreated
    _serve(origin, corpus)
    from app.acquire import budget
    drawn = []
    real = budget.acquire_bytes
    monkeypatch.setattr(budget, "acquire_bytes", lambda n: drawn.append(n) or real(n))
    mo = _reader(codex, origin.base_url)
    o = _check(root, corpus, codex, snap)
    assert o.result == chk.PASS, o.why
    assert o.detail["origin"] == "reachable" and o.detail["failed"] == [] and o.detail["changed"] == []
    assert o.detail["compared"] == FILLERS + 5, "every record but the pruned scholarly article"
    assert "re-parsed from the origin" in o.why
    # THE BUDGET (invariant 5): every byte the origin sent was drawn from it
    assert sum(drawn) == mo.bytes_read + 1 > 0, "the blocks, the header and the one-byte length probe"
    # the bound counts what was fetched — the blocks once and the header — to
    # within the byte two neighbouring blocks share where their boundary is
    # not on a byte (fetched once in a run, counted with each block)
    ix = mo.index
    assert mo.bytes_read <= o.detail["origin_bytes"] <= mo.bytes_read + ix.blocks


def test_a_block_shared_by_two_records_is_fetched_once(recreated, origin):
    root, corpus, codex, snap = recreated
    _serve(origin, corpus)
    mo = _reader(codex, origin.base_url)
    o = _check(root, corpus, codex, snap)
    assert o.result == chk.PASS, o.why
    ranges = _ranges(origin)
    blocks = [r for r in ranges if r[0] > 0]
    assert len(blocks) == len(set(blocks)), "no range asked twice"
    ix = mo.index
    covered = set()
    for a, b in blocks:
        ks = [k for k in range(ix.blocks) if ix.byte_range(k, k)[0] >= a and ix.byte_range(k, k)[1] - 1 <= b]
        assert not covered.intersection(ks), "no block fetched twice"
        covered.update(ks)
    # Q42 and Q5 are side by side in the dump: one block, one unit, one fetch
    q42 = mo.blocks_of("wikidatawiki/entities/all.json.bz2#Q42")
    q5 = mo.blocks_of("wikidatawiki/entities/all.json.bz2#Q5")
    assert q42 == q5
    assert mo.requests == len(blocks) + 1, "one Range a run of blocks, and the header"
    assert o.detail["origin_units"] < o.detail["compared"], "records sharing a unit are compared off one fetch"


def test_a_block_changed_at_the_origin_is_changed_at_the_origin_never_failed(recreated, origin):
    """One block's bytes rewritten at the origin (its length and header the
    same): the records it holds are CHANGED AT THE ORIGIN — their origin unit
    is no longer the bytes they were parsed from (03 §3.2) — and named with
    the reason; the rest reproduce."""
    root, corpus, codex, snap = recreated
    served = _serve(origin, corpus)
    ix = bz2_blocks.build(corpus / REL, louise._member_origin(codex).lines, threads=1)
    k = ix.blocks // 2
    s, e = ix.byte_range(k, k)
    data = bytearray(served.read_bytes())
    data[(s + e) // 2] ^= 0xFF
    served.write_bytes(bytes(data))
    _reader(codex, origin.base_url)
    o = _check(root, corpus, codex, snap)
    assert o.detail["failed"] == [], "never a failure: the origin's unit is not the bytes the record was read from"
    assert o.detail["changed"], o.why
    assert "are not the copy's" in o.why
    n = len(o.detail["changed"])
    if n > chk.CHANGED_AT_ORIGIN_SHARE * o.detail["compared"]:
        assert o.result == chk.INCONCLUSIVE and o.detail["origin"] == "changed"
    else:
        assert o.result == chk.PASS and f"{n} changed at the origin" in o.why


def test_an_origin_file_of_another_length_changes_every_record(recreated, origin):
    root, corpus, codex, snap = recreated
    served = _serve(origin, corpus)
    served.write_bytes(served.read_bytes() + b"\0")
    _reader(codex, origin.base_url)
    o = _check(root, corpus, codex, snap)
    assert o.result == chk.INCONCLUSIVE and o.detail["origin"] == "changed", o.why
    assert "not the file the copy holds" in o.why


def test_a_run_the_origin_no_longer_serves_is_inconclusive_with_its_reason(recreated, origin):
    root, corpus, codex, snap = recreated            # nothing served: 404
    mo = _reader(codex, origin.base_url)
    o = _check(root, corpus, codex, snap)
    assert o.result == chk.INCONCLUSIVE and o.detail["origin"] == "unreachable", o.why
    assert f"no longer serves run {RUN} (404" in o.why
    assert mo.requests == 0 and len(_ranges(origin)) <= 2, "asked once, then not again"


def test_an_origin_that_does_not_answer_is_inconclusive_a_deferral(recreated):
    root, corpus, codex, snap = recreated
    _reader(codex, "http://127.0.0.1:9")             # nothing listens there
    o = _check(root, corpus, codex, snap)
    assert o.result == chk.INCONCLUSIVE and o.detail["origin"] == "unreachable", o.why
    assert "the origin did not answer" in o.why
    assert not o.attestable


def test_the_bound_counts_the_bytes_fetched(recreated, origin, monkeypatch):
    """A bound of three blocks: the seed's first units are taken while their
    blocks fit, and the origin is asked for no more than the bound."""
    root, corpus, codex, snap = recreated
    _serve(origin, corpus)
    ix = bz2_blocks.build(corpus / REL, louise._member_origin(codex).lines, threads=1)
    bound = 4 + sum(ix.byte_range(k, k)[1] - ix.byte_range(k, k)[0] for k in range(3))
    monkeypatch.setattr(chk, "ORIGIN_BYTES", bound)
    mo = _reader(codex, origin.base_url)
    o = _check(root, corpus, codex, snap)
    assert o.result == chk.PASS, o.why
    assert mo.bytes_read <= bound and o.detail["origin_bytes"] <= bound
    assert sum(b - a + 1 for a, b in _ranges(origin)) <= bound + 1
    assert 0 < o.detail["compared"] < FILLERS + 5, "a share, drawn by the seed"


def test_the_kept_copy_given_as_the_origin_stays_kept_copy(recreated, origin):
    root, corpus, codex, snap = recreated
    _serve(origin, corpus)
    _reader(codex, origin.base_url)
    o = _check(root, corpus, codex, snap, origin_else=False, origin_dir=str(corpus))
    assert o.result == chk.INCONCLUSIVE and o.detail["origin"] == "kept copy", o.why
    assert _ranges(origin) == [], "the origin is not asked"


def test_only_a_dated_one_stream_dump_naming_its_run_is_asked_by_its_blocks(recreated):
    root, _corpus, codex, _snap = recreated
    assert isinstance(block_origin.for_codex(codex), block_origin.BlockOrigin)
    live = col.read(root, "wikidata")
    assert block_origin.for_codex(live) is None, "the parser-2 Codex names no run"
    from app.plugins.wikipedia.source import WikipediaSource
    assert getattr(WikipediaSource, "origin_lines", None) is None, "a multistream dump has its own index"


def test_a_stop_during_the_index_build_exits_130_and_the_next_check_resumes(recreated, origin, monkeypatch,
                                                                           capsys):
    """SIGTERM or SIGINT while the copy's index is built (louise._graceful_stop's
    event): the build writes its state at its next step and the command exits
    130 (invariant 2, 07 §7.7)."""
    import contextlib
    import threading
    root, corpus, codex, snap = recreated
    _serve(origin, corpus)
    _reader(codex, origin.base_url)

    @contextlib.contextmanager
    def signalled():
        e = threading.Event()
        e.set()
        yield e
    monkeypatch.setattr(louise, "_graceful_stop", signalled)
    rc = louise.main(["hold", "check", "wikidata.next", "--root", str(root), "--checks", "records-vs-origin",
                      "--seed", "00" * 32, "--origin-else", str(corpus), "--json"])
    assert rc == 130
    assert "stopped" in capsys.readouterr().err
    assert bz2_blocks.progress(corpus / REL) is not None, "its state written: the next pass resumes there"


def test_the_nightly_check_re_parses_the_copy_while_the_index_is_not_whole(recreated, origin, monkeypatch, capsys):
    """Its time spent before the copy's index is whole (a resumable build),
    the nightly check re-parses the kept copy as before and says why; the
    next one resumes the build and asks the origin."""
    root, corpus, codex, snap = recreated
    _serve(origin, corpus)
    _reader(codex, origin.base_url)
    monkeypatch.setattr(block_origin, "NIGHTLY_BUILD_S", 0.0)
    o = _check(root, corpus, codex, snap)
    assert o.detail["origin"] == "kept copy", o.why
    assert "block index is built to" in capsys.readouterr().err
    assert bz2_blocks.progress(corpus / REL) is not None
    monkeypatch.setattr(block_origin, "NIGHTLY_BUILD_S", 3600.0)
    o = _check(root, corpus, codex, snap)
    assert o.result == chk.PASS and o.detail["origin"] == "reachable", o.why
    assert bz2_blocks.progress(corpus / REL) is None, "the build finished and its state is gone"
