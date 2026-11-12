"""09 §9.14's relations parts, written by `hold records build` and by the
light `hold records relations` (RULE 1) for a collection already built:
one part beside each record part, the plugin's edges over the part's record
lines, sorted and framed, `snapshot` the one that first listed the edge."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app import louise
from app.hold import collection as col, records as rec, relations as rel, shard as shard_mod, snapshot as sn
from app.hold.codex import CodexError
from app.pack import records as pack_records
from app.plugins.base import Edge
from app.plugins.normattiva import SOURCE
from app.plugins.normattiva import source as nsrc
from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed, fake_query_embed
from tests.hold.test_template import recipe
from tests.test_normattiva import L306, M306, SARD_MEMBERS, _doc, _zip

QUIET = {"log": lambda *_: None}


@pytest.fixture
def origin(tmp_path):
    nsrc._zip_cache.clear()
    corpus = tmp_path / "origin"
    _zip(corpus / "akn/M/leggi-costituzionali.zip", dict(SARD_MEMBERS))
    _zip(corpus / "akn/M/codici.zip", {M306: base64.b64encode(L306)})
    return corpus


def _create(root: Path, name: str = "normattiva", **over) -> None:
    body = (Path(nsrc.__file__).parent / "TERMS.txt").read_bytes()
    col.create_from_recipe(root, "normattiva", recipe(license={
        "class": "yellow", "terms_sha256": "sha256:" + hashlib.sha256(body).hexdigest()}, **over),
        platform_image=PLATFORM, embed=fake_embed, embed_query=fake_query_embed,
        name=name, key=KEY, now=NOW)
    assert louise.main(["hold", "collection", "ack", name, "--root", str(root), "--yes"]) == 0


def _built(root: Path, origin: Path, name: str = "normattiva", snapshot_id: str = "2026-08", **kw):
    _create(root, name)
    rec.build(root, name, corpus_dir=origin, snapshot_id=snapshot_id, **QUIET, **kw)
    return root / name / "shard"


def _relations(shard: Path) -> dict[str, bytes]:
    return {p.relative_to(shard).as_posix(): p.read_bytes()
            for p in sorted((shard / "relations").rglob("*.jsonl.zst"))} if (shard / "relations").is_dir() else {}


def _records(shard: Path) -> dict[str, bytes]:
    return {p.relative_to(shard).as_posix(): p.read_bytes() for p in sorted((shard / "records").rglob("*.jsonl.zst"))}


def _lines(path: Path) -> list[bytes]:
    index = pack_records.read_index(path)
    return [line + b"\n" for k in range(len(index.frames)) for line in pack_records.read_frame(path, index, k)]


def _expected(origin: Path, uids: list[str], snapshot_id: str) -> list[bytes]:
    """The part's lines from the PARSED documents, written out by hand."""
    triples = set()
    for member in [M306, *SARD_MEMBERS]:
        doc = _doc(origin, member)
        if doc.uid in uids:
            triples |= {(doc.uid, e.type, e.to) for e in SOURCE.relations(doc)}
    want = sorted(triples, key=lambda t: (t[0].encode(), t[1].encode(), t[2].encode()))
    return [json.dumps({"from": f, "to": to, "type": ty, "snapshot": snapshot_id}, ensure_ascii=False,
                       separators=(",", ":")).encode() + b"\n" for f, ty, to in want]


def test_a_build_writes_the_edges_beside_the_part_as_9_14_fixes(isolated_config, tmp_path, origin):
    root = tmp_path / "collections"
    shard = _built(root, origin)
    rec.write_validation(root, "normattiva")
    snap = sn.create(root, "normattiva", snapshot_id="2026-08", **QUIET)
    assert snap.relations, "the fixture acts state edges"
    listed = {(r.slice, r.n): r for r in snap.relations}
    total = 0
    for s in snap.shards:
        uids = [r["uid"] for r in pack_records.iter_records(shard / s.path)]
        want = _expected(origin, uids, "2026-08")
        path = shard / shard_mod.relations_path(s.slice, s.n)
        if not want:
            assert not path.exists() and (s.slice, s.n) not in listed, "a part only where an edge is stated"
            continue
        assert _lines(path) == want
        assert list(json.loads(want[0])) == ["from", "to", "type", "snapshot"]
        assert set(pack_records.read_index(path).uids) == {json.loads(x)["from"] for x in want}, \
            "the index's uids keyed by `from`"
        r = listed[(s.slice, s.n)]
        assert (r.edges, r.bytes, r.root) == (len(want), path.stat().st_size,
                                              shard_mod.pieces_root(path.read_bytes()))
        total += len(want)
    assert total == sum(r.edges for r in snap.relations)
    m306 = _doc(origin, M306).uid
    assert any(json.loads(x)["from"] == m306 for p in _relations(shard) for x in _lines(shard / p))


def test_records_from_held_lines_give_byte_identical_relations(isolated_config, tmp_path, origin):
    root = tmp_path / "collections"
    shard = _built(root, origin)
    rec.write_validation(root, "normattiva")
    sn.create(root, "normattiva", snapshot_id="2026-08", **QUIET)
    from app.hold import template
    # a re-creation differing only by its slicing (02 §2.7): its parts cut the same here
    _create(root, "normattiva-b", slicing=dict(template.default_slicing(SOURCE), shard_records=29_999))
    rec.build(root, "normattiva-b", corpus_dir=origin, snapshot_id="2026-08",
              records_from="normattiva", **QUIET)
    other = root / "normattiva-b" / "shard"
    assert _records(other) == _records(shard)
    assert _relations(other) == _relations(shard) and _relations(shard)


def test_records_from_admits_a_facets_difference_none_parsed(isolated_config, tmp_path, origin, monkeypatch):
    """02 §2.7 amended 2026-11-12 (RULE 1): a re-creation that gains the
    legal facets (legal-chunking-retrieval.md R2) takes its predecessor's
    lines — the facets write no record byte — and parses none; the two
    collections are two (`facets` is inside `collection_id`)."""
    root = tmp_path / "collections"
    body = (Path(nsrc.__file__).parent / "TERMS.txt").read_bytes()
    lic = {"class": "yellow", "terms_sha256": "sha256:" + hashlib.sha256(body).hexdigest()}
    old = col.create_from_recipe(root, "normattiva", recipe(license=lic, facets=[]), platform_image=PLATFORM,
                                 embed=fake_embed, embed_query=fake_query_embed, name="normattiva",
                                 key=KEY, now=NOW)
    assert louise.main(["hold", "collection", "ack", "normattiva", "--root", str(root), "--yes"]) == 0
    rec.build(root, "normattiva", corpus_dir=origin, snapshot_id="2026-08", **QUIET)
    rec.write_validation(root, "normattiva")
    sn.create(root, "normattiva", snapshot_id="2026-08", **QUIET)
    shard = root / "normattiva" / "shard"
    _create(root, "normattiva-f")
    _name, new = col.resolve(root, "normattiva-f")
    assert old.facets == () and [f.field for f in new.facets] == [
        "date", "extra.collection", "extra.doc_type", "extra.in_force"]
    assert old.collection_id != new.collection_id
    from app.hold.seed import record_differences
    assert record_differences(old, new) == [], "facets decide no record byte (2026-11-11)"
    monkeypatch.setattr(type(SOURCE), "parse", lambda *a, **k: pytest.fail("a held line was parsed again"))
    said = []
    rec.build(root, "normattiva-f", corpus_dir=origin, snapshot_id="2026-08", records_from="normattiva",
              log=said.append)
    assert _records(root / "normattiva-f" / "shard") == _records(shard) and _records(shard), \
        "byte-identical records"
    assert any("none parsed" in m for m in said)


def test_a_plugin_without_relations_writes_none(isolated_config, tmp_path, corpus_dir):
    from tests.hold.test_build_and_snapshot import _build, _collection
    root = _collection(tmp_path)
    _build(root, corpus_dir, records_per_shard=2)
    snap = sn.create(root, "gutenberg", snapshot_id="2026-08", **QUIET)
    assert not (root / "gutenberg" / "shard" / "relations").exists()
    assert snap.relations == ()
    con = sqlite3.connect(root / "gutenberg" / "state" / "build" / ".build" / "build.db")
    assert con.execute("SELECT value FROM meta WHERE key=?", (rel.META_KEY,)).fetchone() is None
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='relations_parts'").fetchone()


def test_snapshot_create_refuses_edges_written_for_another_snapshot(isolated_config, tmp_path, origin):
    root = tmp_path / "collections"
    _built(root, origin)
    rec.write_validation(root, "normattiva")
    with pytest.raises(CodexError, match="written for snapshot 2026-08"):
        sn.create(root, "normattiva", snapshot_id="2026-09", **QUIET)


def test_the_light_command_writes_what_a_build_would_and_touches_no_record(
        isolated_config, tmp_path, origin, monkeypatch):
    """RULE 1: a collection built before relations existed (normattiva,
    341,959 records) gets its relations parts without a re-parse."""
    root = tmp_path / "collections"
    with monkeypatch.context() as m:                    # built before this feature
        m.setattr(rel, "plugin_of", lambda codex: None)
        shard = _built(root, origin)
        rec.write_validation(root, "normattiva")
        first = sn.create(root, "normattiva", snapshot_id="2026-08", **QUIET)
    assert first.relations == () and not _relations(shard)
    records = _records(shard)

    argv = ["hold", "records", "relations", "normattiva", "--root", str(root), "--snapshot", "2026-09"]
    from app import config, parallel
    monkeypatch.setattr(config, "PARSE_PROCESSES", 2)       # the node's pool, one part per task
    assert parallel.processes() == 2
    assert louise.main(argv) == 0
    assert _records(shard) == records, "no record part touched"
    ref = _built(tmp_path / "ref", origin, snapshot_id="2026-09")
    assert _relations(shard) == _relations(ref) and _relations(shard), "the parts a build writes"

    snap = sn.create(root, "normattiva", snapshot_id="2026-09", **QUIET)
    assert [s.root for s in snap.shards] == [s.root for s in first.shards]
    assert [r.path for r in snap.relations] == list(_relations(shard))
    assert snap.delta.added == () and snap.delta.changed == ()

    from app.ask import traverse
    uid = _doc(origin, M306).uid
    got = traverse.traverse(uid, depth=1, root=root, collection="normattiva")["edges"]
    assert {(e["from"], e["type"], e["to"]) for e in got} >= {
        (uid, "eli:amends", "urn:nir:stato:decreto.legislativo:2000-05-26;187"),
        (uid, "eli:cites", "akn:it/act/legge/stato/1988-08-23/400")}
    assert {e["snapshot"] for e in got} == {"2026-09"}

    # idempotent: nothing to compute, the same bytes, the same snapshot
    before = _relations(shard)
    assert louise.main(argv) == 0
    assert _relations(shard) == before
    assert sn.create(root, "normattiva", snapshot_id="2026-09", **QUIET).hash == snap.hash


def test_an_edge_keeps_the_snapshot_that_first_listed_it(isolated_config, tmp_path, origin, monkeypatch):
    root = tmp_path / "collections"
    shard = _built(root, origin)
    rec.write_validation(root, "normattiva")
    sn.create(root, "normattiva", snapshot_id="2026-08", **QUIET)
    path = shard / "relations/type=LEGGE/vig=2025/00001.jsonl.zst"
    old = [json.loads(x) for x in _lines(path)]
    assert old, "legge 306/2003 states edges"

    real = nsrc.NormattivaSource.relations

    def more(self, doc):
        yield from real(self, doc)
        if doc.extra.get("active_modifications"):
            yield Edge(to="akn:it/act/legge/stato/2026-01-01/1", type="eli:cites")
    monkeypatch.setattr(nsrc.NormattivaSource, "relations", more)
    assert louise.main(["hold", "records", "relations", "normattiva", "--root", str(root),
                        "--snapshot", "2026-09"]) == 0
    new = [json.loads(x) for x in _lines(path)]
    assert {e["to"]: e["snapshot"] for e in new} == {
        **{e["to"]: "2026-08" for e in old}, "akn:it/act/legge/stato/2026-01-01/1": "2026-09"}
    assert [(e["from"].encode(), e["type"].encode(), e["to"].encode()) for e in new] == sorted(
        (e["from"].encode(), e["type"].encode(), e["to"].encode()) for e in new)
    # the version 2026-08 listed is kept beside it until 2026-09 is frozen
    assert shard_mod.superseded_name(path, "2026-08").is_file()
    snap = sn.create(root, "normattiva", snapshot_id="2026-09", **QUIET)
    assert snap.relations[0].edges == len(old) + 1


def test_a_stopped_pass_resumes_after_the_last_part(isolated_config, tmp_path, origin, monkeypatch):
    """Invariant 2: a stop finishes the part in hand, the pass says it was
    interrupted, `snapshot create` refuses the half, and a re-run finishes."""
    import threading
    root = tmp_path / "collections"
    with monkeypatch.context() as m:
        m.setattr(rel, "plugin_of", lambda codex: None)
        _built(root, origin)
    stop = threading.Event()
    stop.set()
    got = rel.backfill(root, "normattiva", snapshot_id="2026-08", stop=stop, **QUIET)
    assert got.interrupted and got.parts == 1
    rec.write_validation(root, "normattiva")
    with pytest.raises(CodexError, match="did not finish"):
        sn.create(root, "normattiva", snapshot_id="2026-08", **QUIET)
    again = rel.backfill(root, "normattiva", snapshot_id="2026-08", **QUIET)
    assert not again.interrupted and again.parts == 2, "the part done is not computed again"
    assert sn.create(root, "normattiva", snapshot_id="2026-08", **QUIET).relations


def test_a_snapshot_with_relations_passes_hold_check(isolated_config, tmp_path, origin):
    """03 §3.2: `roots`, `records` and `chunks` over a snapshot listing
    relations parts — none of them reads the relations, and none refuses."""
    from app.hold import check
    root = tmp_path / "collections"
    _built(root, origin)
    rec.write_validation(root, "normattiva")
    snap = sn.create(root, "normattiva", snapshot_id="2026-08", **QUIET)
    assert snap.relations
    _name, codex = col.resolve(root, "normattiva")
    report = check.run(root, "normattiva", codex, snap, seed=b"\x00" * 32,
                       checks=("roots", "records", "chunks"))
    assert {o.check: o.result for o in report.outcomes} == {
        "roots": check.PASS, "records": check.PASS, "chunks": check.PASS}


def test_roots_holds_the_relations_parts_to_their_roots(isolated_config, tmp_path, origin):
    """03 §3.2 `roots` covers the relations parts too (2026-11-11): a byte
    changed in one fails the check, naming that part."""
    from app.hold import check, shard as shard_mod
    root = tmp_path / "collections"
    _built(root, origin)
    rec.write_validation(root, "normattiva")
    snap = sn.create(root, "normattiva", snapshot_id="2026-08", **QUIET)
    r = snap.relations[0]
    path = col.shard_dir_of(root, "normattiva") / shard_mod.relations_path(r.slice, r.n)
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 0xFF
    path.write_bytes(bytes(raw))
    _name, codex = col.resolve(root, "normattiva")
    report = check.run(root, "normattiva", codex, snap, seed=b"\x00" * 32, checks=("roots",))
    [o] = report.outcomes
    assert o.result == check.FAIL and f"relations/{r.slice}/{r.n:05d}" in o.detail["failed"]
