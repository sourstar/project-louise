"""`records-vs-origin` REACHES WIKIPEDIA'S DUMP LAYER (03 §3.2 amended
2026-11-17; app/plugins/wikipedia/weigh.py, app/hold/check.py).

On the node (2026-11-17) the check drew 2 records, both from the archive
layer: a dump-layer record's unit was weighed as the whole files its pieces
name — the run's multistream dump, 26 GB for enwiki, and page_props.sql.gz
— far over the 256 MiB bound, so it was taken only when the seed ranked it
first, and ~30 M records were never compared with their origin. Weighed as
a verifier reads them — a page's stream by Range, the run's index and
page_props read whole but ONCE a check, an archive article's row groups
beside its file's footer — they are reached under the default bound.

The fixture is the node's proportions scaled down: one wiki, an archive
part and dump-layer parts, the run's dump several times the bound, a stream
a small part of it, and the bound (`ORIGIN_BYTES`) scaled with them."""
from __future__ import annotations

import random
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app import collections as collections_mod
from app import db, discovery, louise, plugin_io
from app.hold import check as chk
from app.hold import origin_run
from app.hold import records as rec
from app.hold import snapshot as sn
from app.plugins.wikipedia import archive, locate, stream_index
from app.plugins.wikipedia import source as wsrc
from tests.hold import wiki_runs
from tests.hold.conftest import PLATFORM
from tests.test_wikipedia_page_origin import write_run

RUN = wiki_runs.FIRST
SID = "2026-10"
WORDS = [f"w{i:04d}" for i in range(4000)]
#: the archive's articles (page ids the dump also lists: read from the archive only)
ARCHIVE_PAGES = (2, 3, 4, 5)
#: the dump layer: 20 streams of 3 pages
STREAMS = [[(100 + 3 * s + k, f"Page {100 + 3 * s + k}", None) for k in range(3)] for s in range(20)]


def _text(pid: int) -> str:
    rnd = random.Random(pid)                     # incompressible enough: a stream of ~10 KB
    return " ".join(rnd.choice(WORDS) for _ in range(1500))


def _archive(corpus: Path) -> Path:
    d = corpus / "data" / "tr"
    d.mkdir(parents=True, exist_ok=True)
    rows = [(pid, seq) for pid in ARCHIVE_PAGES for seq in range(2)]
    t = pa.table({"id": [f"{p}_{s}" for p, s in rows],
                  "url": [f"https://tr.wikipedia.org/wiki/A{p}" for p, _ in rows],
                  "title": [f"A{p}" for p, _ in rows],
                  "text": [f"article {p} paragraph {s}" for p, s in rows],
                  "embedding": [np.ones(4, dtype=np.float32).tolist() for _ in rows]})
    path = d / "000.parquet"
    pq.write_table(t, path, row_group_size=2)    # one article a row group
    return path


def build(data_dir: Path, config_dir: Path, *, props_padding: int = 0):
    """wikipedia at parser 6 — parts of 4 records: the archive's 4 articles in
    part 0, the dump's 60 pages after — frozen as SID. `props_padding` rows
    of page_props for pages no record holds make the run's page_props as
    heavy as wanted (enwiki's is 458 MB)."""
    for cache in (wsrc._stream_cache, archive._groups, archive._layouts):
        cache.clear()
    root, codex = wiki_runs.node_collection(data_dir, config_dir, "wikipedia", shard_records=4,
                                            archives=(wsrc.ARCHIVE_ID,))
    corpus = root / "wikipedia" / "origin"
    streams = [[(pid, title, _text(pid)) for pid, title, _ in s] for s in STREAMS]
    props = [(pid, "wikibase_item", f"Q{pid}") for s in STREAMS for pid, _, _ in s]
    # incompressible, as a real table is: gzip must not fold the padding away
    props += [(10_000 + i, "page_image_free", random.Random(i).getrandbits(800).to_bytes(100, "big").hex())
              for i in range(props_padding)]
    write_run(corpus, RUN, streams, props=props)
    _archive(corpus)
    c = collections_mod.by_name("wikipedia")
    con = db.connect(c)
    try:
        discovery.sync_collection(con, c)
    finally:
        con.close()
    rec.build(root, "wikipedia", corpus_dir=corpus, snapshot_id=SID, records_per_shard=4, log=lambda *_: None)
    snap = sn.create(root, "wikipedia", snapshot_id=SID, log=lambda *_: None)
    return root, corpus, codex, snap


@pytest.fixture
def wiki(isolated_config, data_dir, config_dir, monkeypatch):
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    monkeypatch.setenv("LOUISE_PLATFORM_IMAGE", PLATFORM)
    return build(data_dir, config_dir)


def _files(corpus: Path) -> dict[str, Path]:
    d = corpus / "trwiki" / RUN
    base = f"trwiki-{RUN}-pages-articles-multistream"
    return {"dump": d / f"{base}.xml.bz2", "index": d / f"{base}-index.txt.bz2",
            "props": d / f"trwiki-{RUN}-page_props.sql.gz", "archive": corpus / "data" / "tr" / "000.parquet"}


def _check(root, codex, snap, corpus, *, seed: bytes, origin_unit=None, **kw) -> chk.Outcome:
    run = origin_run.run_of(codex, snap)
    return chk.run(root, "wikipedia", codex, snap, seed=seed, checks=["records-vs-origin"], sample="1",
                   reparse=louise._reparse_from(codex, "wikipedia", corpus, run=run),
                   origin_unit=origin_unit or louise._origin_unit_from(codex, "wikipedia", corpus, run=run),
                   **kw).outcome("records-vs-origin")


def _whole_files(codex, corpus, run):
    """BEFORE 2026-11-17: a unit was the whole files its pieces name."""
    plugin = collections_mod.PLUGIN_REGISTRY[codex.plugin]
    return lambda origin: plugin_io.origin_unit(plugin, origin, corpus, run=run)


def _dump_compared(o: chk.Outcome) -> bool:
    return "dump 0 of" not in o.why and " dump " in o.why


SEEDS = [bytes([i]) * 32 for i in range(12)]


def _seeds_ranking_first(root, codex, snap, corpus) -> list[bytes]:
    """Two seeds, by the ONE sampling rule over the units the records point
    into: one that ranks an archive unit first, one a dump-layer unit."""
    from app.hold import codex as cx
    from app.pack import records as pack_records
    weigh = louise._origin_unit_from(codex, "wikipedia", corpus, run=RUN)
    units = {}
    for s in snap.shards:
        for r in pack_records.iter_records(root / "wikipedia" / "shard" / s.path):
            got = weigh(r["origin"])
            units[got.unit] = got.group[0]
    out: dict[str, bytes] = {}
    for i in range(256):
        seed = bytes([i]) * 32
        out.setdefault(units[cx.sample(seed, len(units), iter(sorted(units)))[0]], seed)
        if len(out) == 2:
            return [out["archive"], out["dump"]]
    raise AssertionError(out)


def test_the_fixture_has_an_archive_part_and_dump_layer_parts(wiki):
    root, _corpus, _codex, snap = wiki
    origins = {}
    from app.pack import records as pack_records
    for s in snap.shards:
        origins[s.n] = [r["origin"] for r in pack_records.iter_records(root / "wikipedia" / "shard" / s.path)]
    first, *rest = sorted(origins)
    assert all(".parquet#" in o for o in origins[first]), "the first part: the archive's articles"
    assert all(o.startswith("trwiki/pages-articles-multistream.xml.bz2#") for n in rest
               for o in origins[n]), "every other part: the dump layer, by page (parser 6)"
    assert sum(len(v) for v in origins.values()) == 4 + 60


def test_a_page_is_weighed_by_its_stream_and_its_run_s_shared_files_once(wiki):
    _root, corpus, codex, _snap = wiki
    f = _files(corpus)
    weigh = louise._origin_unit_from(codex, "wikipedia", corpus, run=RUN)
    got = weigh("trwiki/pages-articles-multistream.xml.bz2#104|trwiki/page_props.sql.gz#104")
    start, end = stream_index.stream_of(f["index"], 104)
    assert got.unit == f"trwiki/{RUN}/{f['dump'].name}#{start}::{end}" and got.size == end - start
    assert got.size < f["dump"].stat().st_size / 10, "a stream, never the whole dump"
    assert dict(got.shared) == {f"trwiki/{RUN}/{f['index'].name}": f["index"].stat().st_size,
                                f"trwiki/{RUN}/{f['props'].name}": f["props"].stat().st_size}
    assert got.group == ("dump", "trwiki")
    # the pages of one stream are one unit; the last stream runs to the end of the file
    assert weigh("trwiki/pages-articles-multistream.xml.bz2#105|trwiki/page_props.sql.gz#105").unit == got.unit
    last = weigh("trwiki/pages-articles-multistream.xml.bz2#159")
    assert last.unit.endswith("::0") and last.size == f["dump"].stat().st_size - stream_index.stream_of(
        f["index"], 159)[0]
    # parser 5's byte range: the bytes named, no index
    old = weigh(f"trwiki/{RUN}/{f['dump'].name}#{start}::{end}")
    assert old.unit == got.unit and old.shared == ()
    # an archive article: its row group's columns read, beside the file's footer
    a = weigh(f"{wsrc._archive_prefix()}data/tr/000.parquet#2::4")
    md = pq.read_metadata(f["archive"])
    assert a.unit == "data/tr/000.parquet#rg1" and a.group == ("archive", "trwiki")
    assert a.size == sum(md.row_group(1).column(i).total_compressed_size for i in range(md.num_columns)
                         if md.row_group(1).column(i).path_in_schema in archive.READ_COLUMNS)
    assert dict(a.shared) == {"data/tr/000.parquet#footer": md.serialized_size + 8}
    # what is not there is unweighed, and its records are reported as not there
    assert weigh("trwiki/pages-articles-multistream.xml.bz2#99999").size is None


def test_a_shared_page_props_and_index_count_once(wiki):
    """Every record of the run reads the same page_props and index: read once
    a check, never once a record or once a unit."""
    root, corpus, codex, snap = wiki
    f = _files(corpus)
    o = _check(root, codex, snap, corpus, seed=SEEDS[0], origin_bytes=1 << 40)
    assert o.result == chk.PASS, o.why
    assert o.detail["compared"] == o.detail["records"] == 64
    md = pq.read_metadata(f["archive"])
    archive_read = md.serialized_size + 8 + sum(
        md.row_group(g).column(i).total_compressed_size for g in range(md.num_row_groups)
        for i in range(md.num_columns) if md.row_group(g).column(i).path_in_schema in archive.READ_COLUMNS)
    # the streams tile the dump (no siteinfo header in the fixture): the dump once, the index once,
    # page_props once, the archive's columns and footer once
    want = f["dump"].stat().st_size + f["index"].stat().st_size + f["props"].stat().st_size + archive_read
    assert o.detail["origin_bytes"] == want
    assert o.detail["origin_units"] == o.detail["origin_units_sampled"] == 20 + 4


def test_under_the_bound_the_dump_layer_is_compared_where_whole_files_never_were(wiki, monkeypatch):
    """THE DEFECT, reproduced and fixed: the bound at a quarter of the run's
    dump (the node: 256 MiB against enwiki's 26 GB). Weighed as whole files,
    a dump-layer record is compared only when the seed ranks its file first;
    weighed as a verifier reads it, every seed reaches the dump layer — and
    the archive's."""
    root, corpus, codex, snap = wiki
    f = _files(corpus)
    monkeypatch.setattr(chk, "ORIGIN_BYTES", f["dump"].stat().st_size // 4)
    before = [_check(root, codex, snap, corpus, seed=s, origin_unit=_whole_files(codex, corpus, RUN))
              for s in SEEDS]
    after = [_check(root, codex, snap, corpus, seed=s) for s in SEEDS]
    assert all(o.result == chk.PASS for o in before + after), [o.why for o in before + after]
    assert not all(_dump_compared(o) for o in before), "whole files: some seeds never reach the dump layer"
    for o in after:
        assert _dump_compared(o) and "NO DUMP-LAYER" not in o.why, o.why
        assert o.detail["origin_bytes"] <= chk.ORIGIN_BYTES, "within the bound, the shared pieces once"
    assert any("archive 0 of" not in o.why for o in after), "the archive's row groups are reached too"


def test_a_check_that_reached_no_dump_layer_record_says_so(isolated_config, data_dir, config_dir, monkeypatch):
    """enwiki's case: the run's page_props alone is heavier than the bound.
    A dump-layer unit is then read only when the seed ranks it first, and
    compared in part; when an archive unit comes first, the check passes on
    the archive's records — and SAYS that no dump-layer record was compared,
    and why, never silently."""
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    monkeypatch.setenv("LOUISE_PLATFORM_IMAGE", PLATFORM)
    root, corpus, codex, snap = build(data_dir, config_dir, props_padding=4000)
    f = _files(corpus)
    monkeypatch.setattr(chk, "ORIGIN_BYTES", f["props"].stat().st_size // 2)
    seeds = _seeds_ranking_first(root, codex, snap, corpus)
    outs = [_check(root, codex, snap, corpus, seed=s) for s in seeds]
    silent = [o for o in outs if o.result == chk.PASS and not _dump_compared(o)]
    assert silent and all("NO DUMP-LAYER RECORD WAS COMPARED" in o.why for o in silent)
    assert all("heavier than the bound" in o.why and "page_props.sql.gz" in o.why for o in silent)
    reached = [o for o in outs if _dump_compared(o)]
    assert reached, "and a seed that ranks a dump-layer unit first reaches it, in part"
    for o in reached:
        (part,) = o.detail["origin_units_in_part"]
        assert part["weight"] > chk.ORIGIN_BYTES and part["compared"] <= part["sampled"]
        assert "in part" in o.why and "archive 0 of" in o.why and "NO ARCHIVE-LAYER" in o.why


def test_records_check_shards_runs_the_named_parts_alone(wiki, capsys, tmp_path):
    """`hold records check --shards SLICE/NNNNN …` (07 §7.3): an operator
    verifies the parts named, in place of the seeded sample.

    The origin given is a directory of its own, not the corpus the node
    keeps: a re-parse from the kept copy is INCONCLUSIVE by rule and never a
    pass (`_is_kept_copy`, d1905f1, tests/hold/test_check.py) — this test and
    that rule were written the same day on two branches, and on main the
    test asked the kept copy for a pass."""
    import shutil
    root, corpus, _codex, snap = wiki
    names = [f"{s.slice}/{s.n:05d}" for s in snap.shards]
    want = names[3:5]
    origin = tmp_path / "the-origin"
    shutil.copytree(corpus, origin)
    rc = louise.main(["hold", "records", "check", "wikipedia", "--root", str(root), "--origin", str(origin),
                      "--seed", "00" * 32, "--json", "--shards", *want])
    assert rc == 0
    import json
    report = json.loads(capsys.readouterr().out)
    (rvo,) = [c for c in report["checks"] if c["check"] == "records-vs-origin"]
    assert rvo["result"] == "pass" and rvo["detail"]["records"] == rvo["detail"]["compared"] == 8
    # and the comma form `hold check --shards` takes
    rc = louise.main(["hold", "records", "check", "wikipedia", "--root", str(root), "--origin", str(corpus),
                      "--seed", "00" * 32, "--json", "--shards", ",".join(want)])
    report = json.loads(capsys.readouterr().out)
    assert rc == 0 and [c["detail"]["records"] for c in report["checks"]
                        if c["check"] == "records-vs-origin"] == [8]


def test_wikidata_s_one_stream_is_compared_in_part_as_before(wiki):
    """Wikidata's dump is ONE bzip2 stream (103,755,108,222 bytes): no index
    the origin publishes reaches an entity, so a verifier reads it whole and
    its unit is the file — `_compared_in_part`'s share, unchanged."""
    from app.plugins.wikidata.source import WikidataSource
    assert getattr(WikidataSource(), "origin_weigher", None) is None, \
        "weighed as its file, as before: nothing in it can be read by Range"


def test_the_weigher_reads_the_index_table_once_a_wiki_never_the_index(wiki, monkeypatch):
    """A COST test: the stream of each page comes from the run's table, one
    lookup a record; the table is ensured once a check, and the index itself
    is never read per record."""
    _root, corpus, codex, _snap = wiki
    ensured, read = [], []
    real_ensure, real_read = stream_index.ensure, stream_index.read_columns
    monkeypatch.setattr(stream_index, "ensure", lambda i: ensured.append(i) or real_ensure(i))
    monkeypatch.setattr(stream_index, "read_columns", lambda i: read.append(i) or real_read(i))
    weigh = louise._origin_unit_from(codex, "wikipedia", corpus, run=RUN)
    t0 = time.perf_counter()
    for _ in range(20):
        for s in STREAMS:
            for pid, _, _ in s:
                weigh(f"trwiki/pages-articles-multistream.xml.bz2#{pid}|trwiki/page_props.sql.gz#{pid}")
    per = (time.perf_counter() - t0) / (20 * 60)
    assert len(ensured) == 1 and len(read) <= 1, (ensured, read)
    assert per < 2e-3, f"{per * 1e6:.0f} µs a record"
    assert locate.is_page_form("trwiki/pages-articles-multistream.xml.bz2#104")
