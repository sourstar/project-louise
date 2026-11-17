"""tests/test_wikipedia_coverage.py — `louise dev wikipedia-coverage`
(app/wikipedia_coverage.py; 2026-11-17): the pages of a wikipedia
collection's run against the rows its manifest holds, per wiki, read-only.

The fixture is tests/test_wikipedia_titles.py's: layer 1 holds pages 10, 20,
30, 40 and 90, the dump layer the rows of its DUMP_ROWS and UNTITLED; the
run's redirects 11, 21, 31, 41, the loop's and the chain's, and page 60 have
no row. Here the
archive's row of page 40 is missing too: a page the archive covers, which the
dump pass skips, held by neither layer."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app import wikipedia_coverage as wc
from app.plugins.base import DiscoverState
from app.plugins.wikipedia import SOURCE, archive
from app.plugins.wikipedia import source as wsrc
from tests import test_wikipedia_titles as twt

#: the run's articles no row holds: the dump layer's left out, and page 40
MISSING = {11, 21, 31, 41, 60, 70, 71, 80, 81, 82, 83, 40}


@pytest.fixture
def wiki(tmp_path):
    wsrc._stream_cache.clear()
    archive._groups.clear()
    archive._layouts.clear()
    corpus = tmp_path / "wikipedia"
    twt.an_archive(corpus)
    twt.a_run(corpus, twt.RUN_PAGES + [(90, "Category:Novels", "A category.", None)])
    refs = [r for r in SOURCE.discover(DiscoverState(corpus_root=corpus, snapshot=twt.RUN)) if r.scope == "document"]
    state = tmp_path / "state"
    state.mkdir()
    m = twt.a_manifest(state / "manifest.sqlite", refs)
    con = sqlite3.connect(m)
    con.execute("DELETE FROM documents WHERE native_id = '40'")       # the archive's row, gone
    con.execute("INSERT INTO documents(uid, source, native_id, path, title) VALUES (?,?,?,?,?)",
                ("wikipedia:99", "wikipedia", "99", "x", "Gone since"))   # a row the run does not list
    con.commit()
    con.close()
    return SimpleNamespace(name="wikipedia", plugin="wikipedia", corpus_dir=corpus, manifest_path=m,
                           snapshot=twt.RUN, state=state, tmp=tmp_path)


def files(d):
    return sorted(str(p.relative_to(d)) for p in d.rglob("*"))


def test_a_page_missing_from_both_layers_is_reported_and_a_covered_page_is_not(wiki):
    before = files(wiki.tmp)
    out = wc.coverage(wiki, samples=50, log=lambda m: None)
    en = out["wikis"]["enwiki"]
    assert en["run"] == twt.RUN and en["articles_in_run"] == len(twt.RUN_PAGES), "the category is not an article"
    assert en["rows"] == {"archive": 5, "dump": 6, "both": 0}, "layer 1: 10, 20, 30, 90 and 99; 91 untitled"
    assert en["missing"] == len(MISSING) and {s["page_id"] for s in en["sample"]} == MISSING
    covered = {s["page_id"]: s["archive_covers"] for s in en["sample"]}
    assert covered[40] is True and en["missing_covered_by_archive"] == 1, \
        "the archive covers 40, the dump pass skips it, and no archive row holds it: lost from both layers"
    assert not any(v for k, v in covered.items() if k != 40) and en["missing_not_covered"] == len(MISSING) - 1
    assert {s["title"] for s in en["sample"] if s["page_id"] == 31} == {"Journey to the Centre of the Earth"}
    assert 10 not in covered and 30 not in covered, "covered pages with their archive row: held"
    assert en["rows_not_in_run"] == 1, "page 99: a row the run does not list"
    assert files(wiki.tmp) == before, "nothing written: not even the archive's id cache"


def test_the_sample_is_bounded_and_the_same_every_time(wiki, monkeypatch):
    from app import config
    a = wc.coverage(wiki, samples=3, log=lambda m: None)["wikis"]["enwiki"]
    monkeypatch.setattr(config, "PARSE_PROCESSES", 2)
    monkeypatch.setattr(wc, "TASK_ROWS", 3)
    b = wc.coverage(wiki, samples=3, log=lambda m: None)["wikis"]["enwiki"]
    assert len(a["sample"]) == 3 and a == b, "the pool's passes find what one process finds"
    assert {s["page_id"] for s in a["sample"]} <= MISSING


def test_a_wiki_without_its_runs_index_says_so(wiki):
    twt.wt.index_path(twt.wt.run_dumps(wiki.corpus_dir, twt.RUN)["en"]).unlink()
    out = wc.coverage(wiki, log=lambda m: None)
    assert "not on disk" in out["wikis"]["enwiki"]["error"]


def test_the_command_prints_the_check(wiki, monkeypatch, capsys):
    from app import collections as collections_mod
    from app import louise
    monkeypatch.setattr(collections_mod, "by_name", lambda name: wiki if name == "wikipedia" else None)
    assert louise.main(["dev", "wikipedia-coverage", "wikipedia", "--lang", "en", "--samples", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["wikis"]["enwiki"]["missing"] == len(MISSING) and len(out["wikis"]["enwiki"]["sample"]) == 2


def test_the_manifests_pass_reads_by_rowid_windows(tmp_path):
    """By the primary key, never a sort (CLAUDE.md's selection-query rules)."""
    from app import db
    m = tmp_path / "m.db"
    sqlite3.connect(m).executescript(db.SCHEMA)
    con = sqlite3.connect(m)
    plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + wc.HELD_SQL, (0, 10, 5)))
    con.close()
    assert "USING INTEGER PRIMARY KEY" in plan and "TEMP B-TREE" not in plan, plan
