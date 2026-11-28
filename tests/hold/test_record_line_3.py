"""09 §9.5's third record shape (2026-11-28, operator: "keep the record, mark
it as degraded, mark the parts as missing and carry on"; "B"): line 2 with
`origin_gaps`, what the origin did not give for the record — `[]` when whole,
one `{part, why}` per part, sorted by part, outside `content_hash`."""
import json

import pytest

from app.hold import check as chk
from app.pack import records as pack_records

GAP = {"part": "annex-ii.fmx.xml", "why": "absent"}


def _record(uid="eurlex:32006R1828", gaps=None, **kw) -> dict:
    r = {"uid": uid, "native_id": uid.rsplit(":", 1)[-1], "lang": "bg",
         "title": "Регламент", "creators": [], "date": "2006",
         "subjects": [], "keys": [], "sections": [["Член 1", "текст"]],
         "extra": {}, "chunks": 1, "origin": "bg/32006R1828", "content_hash": "ab" * 16,
         "origin_hash": "cd" * 16, **kw}
    if gaps is not None:
        r["origin_gaps"] = gaps
    return r


def test_shape_3_is_shape_2_with_the_gaps_before_origin():
    keys = list(pack_records.RECORD_KEYS_3)
    assert keys[keys.index("origin_gaps") + 1] == "origin"
    assert [k for k in keys if k != "origin_gaps"] == list(pack_records.RECORD_KEYS_2)
    line = json.loads(pack_records.canonical_line(_record(), record_line=3))
    assert list(line) == keys
    assert line["origin_gaps"] == [] and line["figures"] == [], "[] when whole, never null"


def test_lines_1_and_2_never_carry_the_key():
    for line_no in (1, 2):
        line = json.loads(pack_records.canonical_line(_record(gaps=[GAP]), record_line=line_no))
        assert "origin_gaps" not in line


def test_the_gaps_are_said_in_one_shape_and_order():
    gaps = [{"part": "z.pdf", "why": "unreadable"}, {"part": "a.xml", "why": "another's: eurlex:32006R1829"}]
    line = json.loads(pack_records.canonical_line(_record(gaps=gaps), record_line=3))
    assert [g["part"] for g in line["origin_gaps"]] == ["a.xml", "z.pdf"]
    for bad in ([{"part": "a"}], [{"part": "a", "why": "lost"}], [{"part": "", "why": "absent"}],
                [GAP, GAP], "absent", [{"part": "a", "why": "another's: "}]):
        with pytest.raises(pack_records.ShardFormatError, match="origin_gaps"):
            pack_records.canonical_line(_record(gaps=bad), record_line=3)


def test_the_gaps_are_outside_the_content_hash():
    """What the record holds is unchanged; what it lacks is said."""
    assert pack_records.content_hash("T", [("I", "x")]) == pack_records.content_hash("T", [("I", "x")])
    a = json.loads(pack_records.canonical_line(_record(), record_line=3))
    b = json.loads(pack_records.canonical_line(_record(gaps=[GAP]), record_line=3))
    assert a["content_hash"] == b["content_hash"]


@pytest.mark.parametrize("suffix", [".jsonl.zst", ".parquet"])
def test_a_shape_3_part_round_trips_through_the_reader(tmp_path, suffix):
    path = tmp_path / f"00001{suffix}"
    pack_records.write_shard(path, [_record("eurlex:1", [GAP]), _record("eurlex:2")], record_line=3)
    got = list(pack_records.iter_records(path))
    assert [r["origin_gaps"] for r in got] == [[GAP], []]


def test_the_records_check_refuses_a_line_3_record_that_does_not_say_its_gaps():
    assert chk._gaps_as_said({"origin_gaps": []})
    assert chk._gaps_as_said({"origin_gaps": [GAP]})
    assert not chk._gaps_as_said({}), "a line-3 record without the key"
    assert not chk._gaps_as_said({"origin_gaps": [{"part": "b", "why": "absent"},
                                                  {"part": "a", "why": "absent"}]}), "unsorted"
    assert not chk._gaps_as_said({"origin_gaps": [{"part": "a", "why": "lost"}]})


def test_the_build_writes_what_the_parse_found_missing():
    """`CanonicalDoc.origin_gaps`, at line 3 only (pack/build._record_of)."""
    import inspect
    from app.pack import build
    from app.plugins.base import CanonicalDoc
    assert "origin_gaps" in {f for f in CanonicalDoc.__dataclass_fields__}
    src = inspect.getsource(build._record_of)
    assert 'record["origin_gaps"] = records.origin_gaps(' in src and "line >= 3" in src


def test_a_codex_may_say_line_3():
    from app.hold import codex as cx
    assert 3 in cx.RECORD_LINES and 3 in cx.RECORD_LINES_WRITABLE


# ---- built end to end: a line-3 collection ---------------------------------------

@pytest.fixture
def gaps_shard(isolated_config, tmp_path, monkeypatch):
    """The figures end-to-end collection at `record_line` 3, one of its books
    parsed with a part its origin did not give."""
    import dataclasses
    from app.hold import collection as col, records as rec, snapshot as snap
    from tests.hold.conftest import KEY, NOW, PLATFORM, fake_query_embed
    from tests.hold.test_figures_end_to_end import _book, _recipe_2
    from tests.hold.test_vectors_export import fake_embed
    monkeypatch.delenv("EMBED_URL", raising=False)
    corpus = tmp_path / "corpus"
    _book(corpus, 20001, ["plate1.jpg"])
    _book(corpus, 20002, ["plate2.jpg"])
    r = _recipe_2()
    r["record_line"] = 3
    root = tmp_path / "collections"
    col.create_from_recipe(root, "gutenberg", r, platform_image=PLATFORM, embed=fake_embed,
                           embed_query=fake_query_embed, name="gaps", key=KEY, now=NOW)
    codex = col.read(root, "gaps")
    from app import collections as collections_mod
    plugin_cls = type(collections_mod.PLUGIN_REGISTRY[codex.plugin])
    real = plugin_cls.parse

    def parse(self, *a, **kw):
        doc = real(self, *a, **kw)
        if doc is not None and str(doc.uid).endswith("20002"):
            doc = dataclasses.replace(doc, origin_gaps=[{"part": "plate9.jpg", "why": "absent"}])
        return doc
    monkeypatch.setattr(plugin_cls, "parse", parse)
    rec.build(root, "gaps", corpus_dir=corpus, log=lambda *_: None, records_per_shard=10)
    rec.write_validation(root, "gaps")
    snapshot = snap.create(root, "gaps", log=lambda *_: None)
    return root, snapshot, col.read(root, "gaps")


def test_a_line_3_collection_says_its_gaps_and_passes_its_check(gaps_shard):
    from app.hold import check as chk, shard as shard_mod
    root, snapshot, codex = gaps_shard
    assert codex.record_line == 3
    got = {}
    for s in snapshot.shards:
        for r in pack_records.iter_records(root / "gaps" / "shard" / s.path):
            got[r["uid"].rsplit(":", 1)[-1]] = r
    assert got["20002"]["origin_gaps"] == [{"part": "plate9.jpg", "why": "absent"}]
    assert got["20001"]["origin_gaps"] == []
    assert got["20001"]["figures"], "line 3 carries the plates as line 2 does"
    out = chk.check_records(chk.Ctx(root=root, name="gaps", codex=codex, snapshot=snapshot,
                                    seed=b"\x05" * 32))
    assert out.result == chk.PASS, out.why


def test_an_image_no_marker_stands_for_is_an_unplaced_gap():
    """Operator, 2026-11-28: "if there is no mark there is no mark, consider
    the article degraded": no `figures` entry, a gap said in the record."""
    gaps = [{"part": "L_2006371BG.01009703.tif", "why": "unplaced"}]
    line = json.loads(pack_records.canonical_line(_record(gaps=gaps), record_line=3))
    assert line["origin_gaps"] == gaps
