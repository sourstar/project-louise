"""tools/measure/legal_chunker_drift.py — what `legal_article` v4 changes on
a collection's own documents, per language, cut as the text index stage
cuts them: on a fixture collection of LEGI's real files, one French article
past the window and one Greek one (its manifest row's language set to `el`:
a multilingual collection's row says it)."""
from __future__ import annotations

import json

import pytest

from app import collections as collections_mod, cpu_limit, db, discovery, parallel, worker
from tests.legi_fixtures import REAL, article, member, tarball
from tools.measure import legal_chunker_drift as drift

BASE = "Freemium_legi_global_20250713-140000.tar.gz"

#: ~9,000 words a text: past the shared layer's 8,175-token window (one
#: token a word, conftest's stand-in for Nemotron's tokenizer), no paragraph
#: or point marks, so both are cut at the sentence level
GREEK = " ".join(f"Η Επιτροπή εξετάζει το ζήτημα του κανονισμού και των μέτρων αυτού{'.' if i % 2 else ';'}"
                 for i in range(800))
FRENCH = " ".join("La Commission examine la question du règlement et de ses mesures." for _ in range(800))


@pytest.fixture
def collection(tmp_path, isolated_config):
    root = tmp_path / "legi"
    tarball(root / BASE, {**REAL, member("article", "LEGIARTI000000000201"): article("LEGIARTI000000000201", text=GREEK),
                          member("article", "LEGIARTI000000000202"): article("LEGIARTI000000000202", text=FRENCH)})
    c = collections_mod.Collection(
        name="legi", plugin="legi", profile=None, embed_mode_default=None,
        corpus_dir=root, manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb")
    con = db.connect(c)
    try:
        discovery.sync_collection(con, c)
        while True:              # the rdf stage writes each row's language, as on the node
            row = con.execute("SELECT * FROM documents WHERE rdf_done=0 AND error IS NULL LIMIT 1").fetchone()
            if row is None:
                break
            worker.stage_rdf(con, row, c)
            if con.in_transaction:
                con.execute("COMMIT")
        con.execute("UPDATE documents SET lang='el' WHERE path LIKE '%LEGIARTI000000000201%'")
        con.commit()
    finally:
        con.close()
    return c


def test_it_counts_per_language_what_v4_changes_and_where_v3_fell_to_windows(collection):
    got = drift.measure(collection, to_version=4, window=3, examples=2, log=lambda m: None)
    el, fr = got["languages"]["el"], got["languages"]["fr"]
    assert el["documents"] == 1 and el["documents_differ"] == 1
    assert el["windowed"] > 0 and el["documents_windowed"] == 1, "v3 saw no Greek sentence: token windows"
    assert el["windowed_to"] == 0 and el["documents_windowed_to"] == 0, "v4 cuts it at its sentences"
    assert el["chunks_to_embed"] > 0 and el["chunks_dropped"] > 0
    assert fr["documents"] >= 5 and fr["documents_differ"] == 0 and fr["chunks_to_embed"] == 0
    assert fr["windowed"] == fr["windowed_to"] == 0, "French sentences: v3 and v4 alike"
    assert fr["chunks"] == fr["chunks_to"] > fr["documents"], "the long French article is cut, the same way"
    assert got["totals"]["documents"] == el["documents"] + fr["documents"] and got["totals"]["errors"] == 0
    (ex,) = got["examples"]
    assert ex["lang"] == "el" and ex["current"] != ex["to"] and "Επιτροπή" in ex["to"]


def test_a_window_is_cut_by_the_stage_s_own_path_and_skips_what_the_stage_skips(collection):
    con = db.connect(collection)
    try:
        con.execute("UPDATE documents SET error='parked' WHERE lang='el'")
        con.commit()
        top = con.execute("SELECT MAX(rowid) FROM documents").fetchone()[0]
    finally:
        con.close()
    got = drift.cut_window(collection, 0, top, 4, 5)
    assert "el" not in got["langs"] and got["skipped"]["out of scope or errored"] == 1
    assert got["langs"]["fr"]["documents_differ"] == 0


def test_the_pass_runs_on_the_parse_pool_at_the_profile_s_cores(collection, monkeypatch):
    seen = {}

    def each_part(fn, tasks, *, limit=None):
        seen.update(fn=fn, limit=limit, tasks=len(tasks))
        return (fn(*t) for t in tasks)
    monkeypatch.setattr(parallel, "each_part", each_part)
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.Limit(6, "day"))
    drift.measure(collection, window=2, log=lambda m: None)
    assert seen["fn"] is drift.cut_window and seen["limit"] == 6 and seen["tasks"] > 1
    drift.measure(collection, window=2, processes=2, log=lambda m: None)
    assert seen["limit"] == 2, "fewer when asked, never more"
    monkeypatch.setattr(cpu_limit, "current", lambda: cpu_limit.Limit(0, "quiet"))
    with pytest.raises(SystemExit, match="no core"):
        drift.measure(collection, log=lambda m: None)


def test_the_command_prints_a_line_a_language_then_the_totals(collection, monkeypatch, capsys):
    monkeypatch.setattr(collections_mod, "by_name", lambda name: collection if name == "legi" else None)
    assert drift.main(["legi", "--examples", "1"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [x["lang"] for x in lines[:2]] == ["el", "fr"]
    assert lines[2]["totals"]["documents_differ"] == 1 and "example" in lines[3]
    assert drift.main(["nope"]) == 2 and drift.main(["legi", "--to", "9"]) == 2
