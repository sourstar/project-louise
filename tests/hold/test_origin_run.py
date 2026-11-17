"""09 §9.4 `origin_run` (*amended 2026-11-17*, operator: B): a snapshot of a
dated-dump origin names the run its records were built from; the field is
optional and additive within version 2, absent everywhere else, and every
reader accepts it — the file's loader, the lazy loader, a peer's pull
(`share fetch` parses with `_load`) and the cheap tail reader the loader of
collections asks (app/hold/origin_run.py). An UNKNOWN key is refused (09
§9.18): the loader used to drop it, so the object hashed otherwise than its
file."""
import json

import pytest

from app.hold import origin_run as orun
from app.hold import snapshot as sn
from app.hold.codex import CodexError, canonical_json


def _snap(run=None, **over) -> sn.Snapshot:
    shard = sn.Shard(slice="lang=tr", n=1, root="sha256:" + "a" * 64,
                     records_root="sha256:" + "b" * 64, first_id="1", last_id="9",
                     records=9, chunks=12, chunks_root="sha256:" + "c" * 64, tokens=12 * 512,
                     bytes=1000)
    base = {"collection_id": "sha256:" + "d" * 64, "snapshot_id": "2026-11",
            "previous": "sha256:" + "e" * 64, "shards": (shard,),
            "delta": sn.Delta(("wikipedia:1",), ("wikipedia:2",),
                              (sn.Tombstone("wikipedia:3", "withdrawn"),)),
            "files": {"validation.json": {"root": "sha256:" + "f" * 64, "bytes": 10}},
            "origin_run": run}
    base.update(over)
    return sn.Snapshot(**base)


def _written(tmp_path, snap: sn.Snapshot):
    p = tmp_path / "shard" / "snapshots" / f"{snap.snapshot_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(snap.canonical())
    return p


def test_the_run_is_written_where_there_is_one_and_absent_otherwise():
    assert json.loads(_snap("20261101").canonical())["origin_run"] == "20261101"
    assert "origin_run" not in json.loads(_snap().canonical()), \
        "absent for other origins — never null, a third shape"
    assert _snap("20261101").hash != _snap().hash, "the run is part of what is hashed and signed"


@pytest.mark.parametrize("bad", ["", " 20261101", 20261101])
def test_a_run_that_is_not_a_string_is_refused_on_write(bad):
    with pytest.raises(CodexError, match="origin_run"):
        _snap(bad).canonical()


def test_every_loader_reads_it_and_the_hash_is_the_files(tmp_path):
    snap = _snap("20261101")
    p = _written(tmp_path, snap)
    lazy = sn.load(p)
    assert isinstance(lazy.delta, sn.LazyDelta), "the lazy path, as every peer's file is read"
    assert lazy.origin_run == "20261101" and lazy.hash == snap.hash
    whole = sn._load(p.read_bytes())          # `share fetch` parses a pulled snapshot so
    assert whole.origin_run == "20261101"
    assert whole.hash == snap.hash, "re-serialised, it is the same bytes: a peer's chain links"
    older = sn._load(_snap().canonical())
    assert older.origin_run is None and older.hash == _snap().hash


def test_an_unknown_key_is_refused_never_dropped(tmp_path):
    d = json.loads(_snap("20261101").canonical())
    d["expires"] = "2027-01-01"
    raw = canonical_json(d)
    with pytest.raises(CodexError, match="unknown field.*expires"):
        sn._load(raw)
    p = tmp_path / "x.json"
    p.write_bytes(raw)
    with pytest.raises(CodexError, match="unknown field.*expires"):
        sn.load(p)


def test_a_null_run_is_refused_on_read():
    d = json.loads(_snap().canonical())
    d["origin_run"] = None
    with pytest.raises(CodexError, match="origin_run"):
        sn._load(canonical_json(d))


def test_a_version_1_snapshot_with_a_figures_key_is_still_refused():
    d = json.loads(_snap(version=1).canonical())
    d["figures"] = []
    with pytest.raises(CodexError, match="unknown field.*figures"):
        sn._load(canonical_json(d))


def test_the_tail_reader_gives_the_run_without_reading_the_delta(tmp_path, monkeypatch):
    many = tuple(f"wikipedia:{i}" for i in range(20_000))
    snap = _snap("20261101", delta=sn.Delta(many, (), ()))
    p = _written(tmp_path, snap)
    monkeypatch.setattr(orun, "_whole", lambda p: pytest.fail("a canonical file is read from its tail"))
    assert orun.snapshot_run(p) == "20261101"
    q = _written(tmp_path / "b", _snap(snapshot_id="2026-10"))
    assert orun.snapshot_run(q) is None


def test_the_tail_reader_reads_a_file_once_per_identity(tmp_path, monkeypatch):
    """A cost test: the loader of collections asks on every round."""
    p = _written(tmp_path, _snap("20261101"))
    reads = []
    real = orun._tail_run
    monkeypatch.setattr(orun, "_tail_run", lambda path: reads.append(path) or real(path))
    for _ in range(50):
        assert orun.snapshot_run(p) == "20261101"
    assert len(reads) == 1
    p.write_bytes(_snap("20261201").canonical())      # another file at the path
    assert orun.snapshot_run(p) == "20261201" and len(reads) == 2


def test_a_file_not_in_canonical_form_is_read_whole(tmp_path):
    p = tmp_path / "pretty.json"
    p.write_text(json.dumps(json.loads(_snap("20261101").canonical()), indent=2))
    assert orun.snapshot_run(p) == "20261101"
