"""tools/measure/bz2_blocks_bench.py: the synthetic dump, its index and the
loopback comparison, at a few hundred entities (the measurement itself runs
at ~300,000; docs/notes/wikidata-entity-origin.md)."""
import json

from tools.measure import bz2_blocks_bench as bench


def test_the_bench_makes_a_dump_indexes_it_and_compares_its_blocks(tmp_path, capsys, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "CACHE_DIR", None)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", None)
    dump = tmp_path / bench.REL
    assert bench.main(["dump", str(dump), "400", "--level", "1"]) == 0
    made = json.loads(capsys.readouterr().out)
    assert made["entities"] == 400 and made["compressed"] == dump.stat().st_size and made["ratio"] > 3
    assert bench.main(["index", str(dump), "--threads", "2"]) == 0
    ix = json.loads(capsys.readouterr().out)
    assert ix["units"] == 400 and ix["framed"] is None and ix["blocks"] > 10
    assert bench.main(["compare", str(tmp_path), "50"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["all_same"] and got["fetched"] == got["unit_bytes"] + 4 and got["requests"] == got["units"] + 1
