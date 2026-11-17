"""The re-creation of wikipedia at parser 6 (the operator's "Choice B",
2026-11-17; app/hold/line_carry.py): its parts carried from parser 5's,
each line's `origin` rewritten and every other byte taken — held here to a
build by the book, line for line, part for part and snapshot for snapshot;
`records-vs-origin` passing on the new form; the vectors' reconcile answered
from the carry's proof; and `wikipedia-articles` following the new
collection, which the cascade then declares."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app import collections as collections_mod
from app import db, discovery, louise
from app.hold import annotations as ann
from app.hold import annotations_export, cascade, line_carry
from app.hold import carry as carry_mod
from app.hold import check as chk
from app.hold import records as rec
from app.hold import snapshot as sn
from app.pack import records as pack_records
from app.plugins.wikipedia import archive
from app.plugins.wikipedia import source as wsrc
from app.plugins.wikipedia.ids import native_id_of
from tests.hold import wiki_runs
from tests.hold.conftest import KEY, PLATFORM
from tests.test_wikipedia_page_origin import write_run

RUN = wiki_runs.FIRST
SID = "2026-10"
STREAMS = [[(5, "Ankara", "'''Ankara''' is the capital."), (6, "Bursa", "Bursa is green.")],
           [(9, "Rize", "Rize grows tea."), (11, "Van", "#REDIRECT [[Van Gölü]]")],
           [(12, "Muş", "Muş."), (13, "İzmir", "İzmir is on the coast.")],
           [(15, "Kars", "Kars is cold.")]]
PROPS = [(5, "wikibase_item", "Q3640"), (9, "wikibase_item", "Q483078"), (9, "page_image_free", "Rize.jpg"),
         (13, "wikibase_item", "Q35997"), (15, "disambiguation", "")]


def uid(pid: int) -> str:
    return f"wikipedia:{native_id_of('tr', pid)}"


def _scan(name: str) -> None:
    c = collections_mod.by_name(name)
    con = db.connect(c)
    try:
        discovery.sync_collection(con, c)
    finally:
        con.close()


def _lines(root: Path, name: str, snap) -> dict[str, bytes]:
    out = {}
    tree = root / name / "shard"
    for s in snap.shards:
        idx = pack_records.read_index(tree / s.path)
        for k in range(len(idx.frames)):
            for line in pack_records.read_frame(tree / s.path, idx, k):
                out[json.loads(line)["uid"]] = line
    return out


@pytest.fixture
def node(isolated_config, data_dir, config_dir, monkeypatch):
    """wikipedia at parser 5, built and frozen as 2026-10, as the node holds
    it; `wikipedia.next`, the same recipe at parser 6, created and empty."""
    for cache in (wsrc._stream_cache, archive._groups, archive._layouts):
        cache.clear()
    monkeypatch.setenv("LOUISE_PLATFORM_IMAGE", PLATFORM)
    root, w5 = wiki_runs.node_collection(data_dir, config_dir, "wikipedia", parser=5, shard_records=3)
    corpus = root / "wikipedia" / "origin"
    write_run(corpus, RUN, STREAMS, props=PROPS)
    _scan("wikipedia")
    rec.build(root, "wikipedia", corpus_dir=corpus, snapshot_id=SID, records_per_shard=3, log=lambda *_: None)
    sn.create(root, "wikipedia", snapshot_id=SID, log=lambda *_: None)
    _r, w6 = wiki_runs.node_collection(data_dir, config_dir, "wikipedia.next", shard_records=3)
    assert (w5.parser.version, w6.parser.version) == (5, 6)
    return root, corpus, w5, w6


def test_the_space_does_not_move(node):
    """Invariant 1: the parser is no part of `space_id` (09 §9.3), so every
    vector of parser 5's collection is parser 6's."""
    _root, _corpus, w5, w6 = node
    assert w5.collection_id != w6.collection_id
    assert w5.space_id == w6.space_id


def test_the_carry_is_a_build_by_the_book_line_for_line_part_for_part(node, monkeypatch):
    root, corpus, _w5, w6 = node
    old = sn.load(root / "wikipedia" / "shard" / "snapshots" / f"{SID}.json")
    assert len(old.shards) == 3 and all("trwiki/20261001/" in o.decode()
                                        for o in _lines(root, "wikipedia", old).values())
    # BY THE BOOK: parser 6 parses every page of the same corpus
    rec.build(root, "wikipedia.next", corpus_dir=corpus, snapshot_id=SID, records_per_shard=3,
              log=lambda *_: None)
    book = sn.create(root, "wikipedia.next", snapshot_id=SID, log=lambda *_: None)
    tree = root / "wikipedia.next" / "shard"
    book_parts = {s.path: (tree / s.path).read_bytes() for s in book.shards}
    book_raw = (tree / "snapshots" / f"{SID}.json").read_bytes()
    book_lines = _lines(root, "wikipedia.next", book)
    # the target as the re-creation finds it: no records, no build, no snapshot
    def wipe() -> None:
        shutil.rmtree(tree / "records")
        shutil.rmtree(tree / "snapshots")
        shutil.rmtree(root / "wikipedia.next" / "state" / "build")
    wipe()
    parsed: list[str] = []
    from app.pack import build as pack_build
    # THE BUILD'S OWN ROUTE, `records build --records-from`: parser 5's lines
    # taken with their origin rewritten (`carried_origin`), none parsed
    with monkeypatch.context() as m:
        m.setattr(pack_build, "_record_of", lambda *a: parsed.append(a) or pytest.fail("parsed"))
        rec.build(root, "wikipedia.next", corpus_dir=corpus, snapshot_id=SID, records_per_shard=3,
                  records_from="wikipedia", log=lambda *_: None)
    built = sn.create(root, "wikipedia.next", snapshot_id=SID, log=lambda *_: None)
    assert {s.path: (tree / s.path).read_bytes() for s in built.shards} == book_parts and not parsed
    wipe()
    with monkeypatch.context() as m:
        m.setattr(pack_build, "_record_of", lambda *a: parsed.append(a) or pytest.fail("parsed"))
        got = line_carry.carry(root, "wikipedia.next", "wikipedia", log=lambda *_: None)
    assert (got.parts, got.carried, got.present, got.records) == (3, 3, 0, 7)
    assert got.rewritten == 7 and not parsed
    mine = sn.load(tree / "snapshots" / f"{SID}.json")
    assert {s.path: (tree / s.path).read_bytes() for s in mine.shards} == book_parts, "part for part"
    assert _lines(root, "wikipedia.next", mine) == book_lines, "line for line"
    assert (tree / "snapshots" / f"{SID}.json").read_bytes() == book_raw, "the snapshot, byte for byte"
    # and each line is parser 5's but for its origin, which names the page
    for u, line in _lines(root, "wikipedia", old).items():
        a, b = json.loads(line), json.loads(book_lines[u])
        assert {k: v for k, v in a.items() if k != "origin"} == {k: v for k, v in b.items() if k != "origin"}
        assert b["origin"].startswith("trwiki/pages-articles-multistream.xml.bz2#")
    assert json.loads(book_lines[uid(9)])["origin"] == \
        "trwiki/pages-articles-multistream.xml.bz2#9|trwiki/page_props.sql.gz#9"
    marker = json.loads((root / "wikipedia.next" / "state" / line_carry.MARKER).read_text())
    assert marker["from_snapshot"] == {"id": SID, "hash": old.hash} and marker["done"]

    # RECORDS-VS-ORIGIN on the new form, in the run the snapshot names
    from app.hold import origin_run
    assert origin_run.run_of(w6, mine) == RUN
    for kw in ({"reparse": louise._reparse_from(w6, "wikipedia.next", corpus, run=RUN)},
               {"reparse": louise._reparse_from(w6, "wikipedia.next", corpus, run=RUN),
                "reparse_many": louise._reparse_many_from(w6, "wikipedia.next", corpus, run=RUN)}):
        o = chk.run(root, "wikipedia.next", w6, mine, seed=b"\0" * 32, checks=["records-vs-origin"],
                    sample="1", **kw).outcome("records-vs-origin")
        assert o.result == chk.PASS, o.why
        assert o.detail["compared"] == 7 and o.detail["failed"] == []


def test_the_vectors_are_reconciled_from_the_carrys_proof(node, monkeypatch):
    root, _corpus, w5, w6 = node
    carry_mod.carry(root, "wikipedia", "wikipedia.next", rebuilt=True, apply=True, copy=True, log=lambda *_: None)
    line_carry.carry(root, "wikipedia.next", "wikipedia", log=lambda *_: None)
    monkeypatch.setattr(carry_mod, "_content_table", lambda *a: pytest.fail("both snapshots read again"))
    got = carry_mod.reconcile(root, "wikipedia", "wikipedia.next", log=lambda *_: None)
    assert got["by"] == "line-carry" and got["reset"] == 0 and got["kept"] == got["records_after"] == 7
    # a target whose snapshot is not the carried one: the proof does not hold
    p = root / "wikipedia.next" / "state" / line_carry.MARKER
    body = json.loads(p.read_text())
    p.write_text(json.dumps({**body, "to_snapshot": {"id": SID, "hash": "sha256:" + "0" * 64}}))
    assert line_carry.proven(root, "wikipedia", "wikipedia.next", w5, w6) is None


def test_a_stopped_carry_resumes_to_the_same_bytes(node):
    import threading
    root, _corpus, _w5, _w6 = node
    stop = threading.Event()
    seen: list[str] = []

    def log(m: str) -> None:
        seen.append(m)
    from app import parallel
    orig = parallel.each_part

    def each_part(fn, tasks, **kw):
        for i, r in enumerate(orig(fn, tasks, **kw)):
            if i == 0:
                stop.set()
            yield r
    import app.parallel as par
    par.each_part, keep = each_part, orig
    try:
        got = line_carry.carry(root, "wikipedia.next", "wikipedia", stop=stop, log=log)
    finally:
        par.each_part = keep
    assert got.interrupted and got.carried == 1
    tree = root / "wikipedia.next" / "shard"
    assert not (tree / "snapshots").exists() or not list((tree / "snapshots").glob("*.json"))
    assert not list(tree.rglob("*.carry")), "no half-written part left"
    again = line_carry.carry(root, "wikipedia.next", "wikipedia", log=lambda *_: None)
    assert (again.present, again.carried) == (1, 2) and not again.interrupted
    snap = sn.load(tree / "snapshots" / f"{SID}.json")
    other = sn.load(root / "wikipedia" / "shard" / "snapshots" / f"{SID}.json")
    assert [(s.records_root, s.records, s.chunks_root) for s in snap.shards] == \
        [(s.records_root, s.records, s.chunks_root) for s in other.shards]


def test_what_the_carry_refuses(node, data_dir, config_dir):
    root, _corpus, _w5, _w6 = node
    # anything else that decides record bytes: nothing carried
    _r, _other = wiki_runs.node_collection(data_dir, config_dir, "wikipedia.other", shard_records=4)
    with pytest.raises(line_carry.CarryRefused, match="differ in slicing"):
        line_carry.carry(root, "wikipedia.other", "wikipedia", log=lambda *_: None)
    with pytest.raises(line_carry.CarryRefused, match="one collection"):
        line_carry.carry(root, "wikipedia", "wikipedia", log=lambda *_: None)
    # parts not cut as a build by the book cuts them: a short part inside a slice
    snap_p = root / "wikipedia" / "shard" / "snapshots" / f"{SID}.json"
    raw = json.loads(snap_p.read_bytes())
    raw["shards"][0]["records"] = 2
    snap_p.write_bytes(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode())
    with pytest.raises(line_carry.CarryRefused, match="not the count cut"):
        line_carry.carry(root, "wikipedia.next", "wikipedia", log=lambda *_: None)
    assert not (root / "wikipedia.next" / "state" / "build").exists(), "nothing written"


def test_the_dry_run_sizes_it_and_writes_nothing(node):
    root, _corpus, _w5, _w6 = node
    got = line_carry.carry(root, "wikipedia.next", "wikipedia", dry_run=True, log=lambda *_: None)
    assert got.parts == 3 and got.records == 7 and got.carried == 0 and got.snapshot["from"] == SID
    assert not (root / "wikipedia.next" / "state" / "build").exists()


def test_a_line_is_rewritten_in_its_bytes_as_canonical_line_writes_it():
    """The byte rewrite against the decode-and-encode it stands for, on
    lines whose strings hold quotes, escapes, `,"origin":"` and non-ASCII."""
    tricky = {"uid": "wikipedia:1000000000009", "native_id": "1000000000009", "lang": "tr",
              "title": 'A "quoted" title ,"origin":"x"', "creators": [], "date": None, "subjects": ["Ş"],
              "keys": [], "sections": [["", 'body \\ "q"   ,"origin":"fake"']],
              "extra": {"wikitext": ',"origin":"trwiki/1/2#3::4","content_hash":"' + "a" * 32 + '"'},
              "origin": "trwiki/20261001/trwiki-20261001-pages-articles-multistream.xml.bz2#1::2",
              "content_hash": "c" * 32, "origin_hash": "d" * 32}
    line = pack_records.canonical_line(tricky)
    carry = lambda o, n: wsrc.WikipediaSource().carried_origin(o, n, 5)
    new, u, ch, moved = line_carry.rewrite_line(line, carry)
    want = pack_records.canonical_line({**tricky, "origin": "trwiki/pages-articles-multistream.xml.bz2#9"})
    assert new + b"\n" == want and u == tricky["uid"] and ch == "c" * 32 and moved
    # a line out of the fixed shape takes the slower road to the same bytes
    odd = {**tricky, "origin_hash": None}
    new2, *_ = line_carry.rewrite_line(pack_records.canonical_line(odd), carry)
    assert new2 + b"\n" == pack_records.canonical_line({**odd, "origin": "trwiki/pages-articles-multistream.xml.bz2#9"})


def test_wikipedia_articles_follows_the_new_collection_and_the_cascade_sees_it(node, tmp_path):
    root, _corpus, w5, w6 = node
    claims = b"".join(json.dumps({
        "type": "same-work", "subject": uid(a), "object": uid(b), "subject_lang": "tr", "object_lang": "tr",
        "method": {"kind": "wikidata"}, "evidence": {"wikidata": "Q1"}, "confidence": "1",
        "contributor": "a test annotator"}).encode() + b"\n" for a, b in ((5, 6), (9, 13)))
    _a5, _ = ann.create(root, "wikipedia-articles", target_ref="wikipedia", from_snapshot=SID, claims=claims,
                       platform_image=PLATFORM, snapshot_id=SID, key=KEY, now="2026-11-17T00:00:00Z",
                       log=lambda *_: None)
    carry_mod.carry(root, "wikipedia", "wikipedia.next", rebuilt=True, apply=True, copy=True, log=lambda *_: None)
    line_carry.carry(root, "wikipedia.next", "wikipedia", log=lambda *_: None)
    out = tmp_path / "claims.jsonl"
    got = annotations_export.export(root, "wikipedia-articles", out, log=lambda *_: None)
    assert got["claims"] == 2 and got["targets"][0]["collection_id"] == w5.collection_id
    a6, snap6 = ann.create(root, "wikipedia-articles.next", target_ref="wikipedia.next", from_snapshot=SID,
                           claims=out, platform_image=PLATFORM, snapshot_id=SID, key=KEY,
                           now="2026-11-17T00:00:00Z", log=lambda *_: None)
    assert [t.collection_id for t in ann.cx.targets_of(a6)] == [w6.collection_id]
    held5 = {r["uid"]: ann.claim_of(r) for s in sn.load(
        root / "wikipedia-articles" / "shard" / "snapshots" / f"{SID}.json").shards
        for r in pack_records.iter_records(root / "wikipedia-articles" / "shard" / s.path)}
    held6 = {r["uid"]: ann.claim_of(r) for s in snap6.shards
             for r in pack_records.iter_records(root / "wikipedia-articles.next" / "shard" / s.path)}
    assert held6 == held5, "every claim carried, pinned to the new collection's snapshot as it was"
    assert all(c["target_snapshot"] == SID for c in held6.values())
    cascade.reset()
    decl = cascade.declarations(root)
    assert decl.sources["wikipedia-articles.next"] == {"wikipedia.next": "annotates"}
    assert "wikipedia-articles.next" in decl.dependents("wikipedia.next")
    assert decl.declaring(w6.collection_id) == ["wikipedia-articles.next"]
