"""tools/measure/reading_cost.py: one JSON line per collection, the years
and the lookup timed, nothing written to the node's refs.db."""
from __future__ import annotations

import json

from app import reading
from app.refs import readings
from tools.measure import reading_cost


def test_one_line_per_collection_with_the_timings(monkeypatch, capsys, tmp_path):
    hits = [{"chunk_id": f"gutenberg:{i // 2}:{i % 2}:1", "text": "l'huomo", "lang": "it",
             "source_collection": "g"} for i in range(4)]
    monkeypatch.setattr(reading_cost, "sample_hits", lambda name, depth, seed: hits)
    monkeypatch.setattr(reading, "years", lambda recs, collection=None: {0: 1554})
    tsv = tmp_path / "w.tsv"
    cols = [c.name for c in readings.COLUMNS]
    row = {"id": "it|huomo|1400|1899", "orig": "huomo", "reg": "uomo", "kind": "replace", "lemma": "",
           "gloss": "", "lang": "it", "period_from": "1400", "period_to": "1899", "method": "t"}
    tsv.write_text("\t".join(cols) + "\n" + "\t".join(row[c] for c in cols) + "\n", encoding="utf-8")
    assert reading_cost.main(["--collection", "g", "--depth", "2", "--runs", "1", "--readings-tsv", str(tsv)]) == 0
    got = json.loads(capsys.readouterr().out.strip())
    assert got["collection"] == "g" and got["with_year"] == 1 and got["texts_changed"] == 2
    assert {"years_cold_ms", "years_warm_ms", "lookup_apply_ms"} <= set(got)
