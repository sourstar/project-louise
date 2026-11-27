"""What a document's work IS, kept beside it (app/work_kind.py;
docs/notes/fact-fiction-search.md, operator 2026-11-27): computed by the rdf
stage, recomputed by the index maintainer in rowid windows for another rules
version, read for the candidates alone, and the filter's verdict."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import collections as collections_mod, db, discovery, work_kind

REAL = Path(__file__).parent / "fixtures" / "gutenberg-real"


def _collection(tmp_path, ids=(2600, 5, 151)):
    corpus = tmp_path / "corpus_k"
    for n in ids:
        d = corpus / str(n)
        shutil.copytree(REAL / str(n), d)
        (d / f"pg{n}.txt.utf8").write_text("A text of the book.\n", encoding="utf-8")
    return collections_mod.Collection(name="gutenberg", plugin="gutenberg", profile=None, embed_mode_default=None,
                                      corpus_dir=corpus, manifest_path=tmp_path / "m" / "manifest.db",
                                      fts_dir=tmp_path / "m" / "fts", lance_dir=tmp_path / "m" / "lancedb")


def _staged(tmp_path, ids=(2600, 5, 151)):
    from app import worker
    c = _collection(tmp_path, ids)
    con = db.connect(c)
    discovery.sync_collection(con, c)
    for row in con.execute("SELECT * FROM documents ORDER BY uid").fetchall():
        worker.stage_rdf(con, row, c)
    if con.in_transaction:
        con.execute("COMMIT")                # as the worker commits around its stage
    return c, con


def test_the_rdf_stage_writes_the_kind_its_basis_and_the_version(isolated_config, tmp_path):
    c, con = _staged(tmp_path)
    got = {int(r[0]): (r[1], r[2], r[3]) for r in con.execute(
        "SELECT native_id, work_kind, work_kind_basis, work_kind_v FROM documents")}
    assert got[2600] == ("literary", "by its subject headings", work_kind.RULES_VERSION)
    assert got[151] == ("literary", "by its Gutenberg category", work_kind.RULES_VERSION)
    assert got[5] == (None, None, work_kind.RULES_VERSION), "the Constitution: a fact, no kind"


def test_the_maintainer_recomputes_another_versions_rows_in_windows_and_then_costs_nothing(
        isolated_config, tmp_path, monkeypatch):
    c, con = _staged(tmp_path)
    con.execute("UPDATE documents SET work_kind=NULL, work_kind_basis=NULL, work_kind_v=NULL")
    out = work_kind.maintain(con, c, window=1)
    assert out["windows"] == out["top"] and out["complete"], out
    assert con.execute("SELECT count(*) FROM documents WHERE work_kind_v IS NOT ?",
                       (work_kind.RULES_VERSION,)).fetchone()[0] == 0
    assert con.execute("SELECT work_kind FROM documents WHERE native_id='2600'").fetchone()[0] == "literary"
    assert work_kind.maintain(con, c) is None, "done for this version: one control read, no window"
    # a rules bump: recomputed by itself, no command
    monkeypatch.setattr(work_kind, "RULES_VERSION", work_kind.RULES_VERSION + 1)
    again = work_kind.maintain(con, c)
    assert again["complete"] and again["written"] == 3
    assert json.loads(db.get_control(con, work_kind.CURSOR_KEY))["v"] == work_kind.RULES_VERSION


def test_the_maintainer_stops_at_its_budget_and_resumes_at_its_cursor(isolated_config, tmp_path, monkeypatch):
    c, con = _staged(tmp_path)
    con.execute("UPDATE documents SET work_kind_v=NULL")
    calls = iter([True, False])
    out = work_kind.maintain(con, c, window=1, may_continue=lambda: next(calls, False))
    assert out["windows"] == 1 and not out["complete"]
    rest = work_kind.maintain(con, c, window=1)
    assert rest["complete"] and rest["cursor"] == rest["top"]


def test_the_window_query_is_a_rowid_range_never_a_scan(isolated_config, tmp_path):
    """scheduling.md §6: a window is a bounded range of the table's own b-tree."""
    c, con = _staged(tmp_path)
    plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + work_kind.WINDOW_SQL, (0, 5000, 1)))
    assert "USING INTEGER PRIMARY KEY" in plan and "TEMP B-TREE" not in plan, plan
    ids = ",".join("?" * 3)
    q = (f"SELECT native_id, work_kind, work_kind_basis FROM documents WHERE source=? "
         f"AND native_id IN ({ids}) AND work_kind IS NOT NULL")
    plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + q, ["gutenberg", "1", "2", "3"]))
    assert "USING INDEX" in plan and "SCAN" not in plan, plan


def test_a_collection_whose_records_carry_no_kind_is_never_walked():
    assert work_kind.maintain(None, SimpleNamespace(plugin="wikipedia")) is None
    assert work_kind.row_values(SimpleNamespace(plugin="eurlex"), "Fiction", "") == (None, None, work_kind.RULES_VERSION)


@pytest.mark.parametrize("asked,kind,keep", [
    ("facts", "literary", False), ("facts", "opinion", True), ("facts", None, True),
    ("literature", "literary", True), ("literature", "opinion", False), ("literature", None, False),
    ("all", "literary", True), ("auto", "literary", True)])
def test_what_each_kind_of_search_keeps(asked, kind, keep):
    assert work_kind.keep(asked, kind) is keep


def test_the_filter_reads_the_candidates_kinds_and_says_what_it_left_out(isolated_config, tmp_path):
    c, con = _staged(tmp_path)
    cands = [{"book_id": 2600}, {"book_id": 5}, {"book_id": 151}]
    kept, left = work_kind.filter_candidates(con, c, cands, "facts")
    assert [h["book_id"] for h in kept] == [5]
    assert left == {"count": 2, "by_basis": {"by its subject headings": 1, "by its Gutenberg category": 1}}
    kept, left = work_kind.filter_candidates(con, c, cands, "literature")
    assert [h["book_id"] for h in kept] == [2600, 151] and left["count"] == 1
    assert work_kind.filter_candidates(con, c, cands, "all") == (cands, {"count": 0, "by_basis": {}})
    other = SimpleNamespace(plugin="eurlex")
    assert work_kind.filter_candidates(None, other, cands, "facts")[0] == cands, "no kind: facts"
    kept, left = work_kind.filter_candidates(None, other, cands, "literature")
    assert kept == [] and left["count"] == 3
