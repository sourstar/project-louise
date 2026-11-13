"""A part is named by its slice as the snapshot names it, "/" and all
(München, 2026-11-13: legi's slices are `kind=…/year=…`; `hold check
--shards kind=article/year=1962/00001` kept the last two segments, matched
no part and said "this peer holds none of the sampled shards"; and a legi
part landed from a peer was never checked, `part_name` finding no part)."""
from types import SimpleNamespace

from app.hold import check as chk

CID = "sha256:" + "ab" * 32


def _ctx(only, slices=(("kind=article/year=1962", 1), ("kind=article/year=1962", 2), ("year=1962", 1))):
    shards = tuple(SimpleNamespace(slice=s, n=n) for s, n in slices)
    return SimpleNamespace(only_shards=only, snapshot=SimpleNamespace(shards=shards, snapshot_id="2026-11"))


def test_a_part_is_named_with_its_whole_slice():
    assert chk.shard_name("kind=article/year=1962/00001") == "kind=article/year=1962/00001"
    assert chk.shard_name(f"{CID}/kind=article/year=1962/00001") == "kind=article/year=1962/00001", \
        "an attestation's name: the collection id taken off, the slice kept whole"
    assert chk.shard_name("records/kind=article/year=1962/00001.jsonl.zst") == "kind=article/year=1962/00001"
    assert chk.shard_name("year=2023/00007") == "year=2023/00007"
    for none in ("calibration/x.safetensors", "relations/year=2023/00001.jsonl.zst", "00001", "x/1", ""):
        assert chk.shard_name(none) is None, none
    assert chk.part_name("records/kind=texte/year=2001/00003.jsonl.zst") == "kind=texte/year=2001/00003"
    assert chk.part_name("calibration/x") is None


def test_the_named_parts_are_the_ones_checked():
    got = chk._shards(_ctx(["kind=article/year=1962/00001"]), "records")
    assert [(s.slice, s.n) for s in got] == [("kind=article/year=1962", 1)], \
        "the nested slice's part, and not year=1962/00001 of another slice"
    got = chk._shards(_ctx([f"{CID}/kind=article/year=1962/00002"]), "records")
    assert [(s.slice, s.n) for s in got] == [("kind=article/year=1962", 2)]


def test_a_name_the_snapshot_does_not_list_is_said_as_such():
    why = chk._none_held(_ctx(["year=1962/00009"]))
    assert "lists no part named year=1962/00009" in why and "holds none" not in why
    assert chk._none_held(_ctx(None)) == "this peer holds none of the sampled shards"
    assert chk._none_held(_ctx(["year=1962/00001"])) == "this peer holds none of the parts asked for"


def test_a_shard_id_keeps_its_whole_slice_in_the_sharing_code():
    """04 §4.5's `<collection_id>/<space>/<slice>/<n>`: everything after the
    first two segments — the last two alone named another slice's part in
    coverage, counting, deprecate, withdraw, possession and contribute."""
    from app.share import contribute, counting, coverage, deprecate, withdraw
    sid = f"{CID}/0123456789abcdef/kind=article/year=1962/00001"
    for f in (coverage.slice_and_n, counting._sid, deprecate._sid, withdraw._sid):
        assert f(sid) == "kind=article/year=1962/00001", f
    assert coverage.slice_and_n("year=2023/00007") == "year=2023/00007"
    snap = SimpleNamespace(shards=(SimpleNamespace(slice="year=1962", n=1),
                                   SimpleNamespace(slice="kind=article/year=1962", n=1)))
    assert contribute._shard_of(snap, sid).slice == "kind=article/year=1962"
