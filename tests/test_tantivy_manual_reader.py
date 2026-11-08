"""The worker's text-index and catalog Index handles reload their reader only
when asked (app/fts.py `manual_reader`; docs/notes/STATUS.md, 2026-11-08).

tantivy-py opens an Index with a reader on `OnCommitWithDelay`: a watcher
thread that opens a new searcher over every segment after each commit. The
worker keeps one Index per (collection, group) and one per catalog for its
life and commits all day, and every read it makes calls `reload()` first —
so the watcher re-opened pubmed's 40 M-document segments after each batch
for nothing (the node's memray: 73.45 GB allocated under `tantivy.Index`
in 81 s, 6–7.5 GB per rdf commit under the catalog's). And one Index per
key, whatever the number of batches: the cache never misses.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

tantivy = pytest.importorskip("tantivy")

from app import catalog, fts
from app import collections as collections_mod


def _coll(tmp_path: Path) -> collections_mod.Collection:
    return collections_mod.Collection(
        name="pubmed", plugin="pubmed", profile=None, embed_mode_default=None,
        corpus_dir=tmp_path / "corpus", manifest_path=tmp_path / "state" / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb")


def _entries(books, lang="en"):
    return [(lang, b, [{"id": f"pubmed:{b}:0:1", "seq": 0, "chapter": "Abstract",
                        "text": f"platelet aggregation study {b}"}]) for b in books]


@pytest.fixture
def opened(monkeypatch):
    """Every tantivy.Index constructed, by path."""
    got: list[str] = []
    real = tantivy.Index

    def counted(*a, **k):
        got.append(str(k.get("path")))
        return real(*a, **k)
    monkeypatch.setattr(tantivy, "Index", counted)
    yield got
    fts.close_all()
    catalog.close_all()


def test_one_index_per_group_whatever_the_batches(tmp_path, opened):
    c = _coll(tmp_path)
    for k in range(40):
        fts.index_books(_entries(range(k * 10, k * 10 + 10)) + _entries([90_000 + k], lang="fr"), c)
    assert sorted(opened) == sorted([str(c.fts_dir / "en"), str(c.fts_dir / "fr")]), \
        "one Index per (collection, group), opened once"
    assert sorted(k for k in fts._indexes if k[0] == str(c.fts_dir)) == \
        [(str(c.fts_dir), "en"), (str(c.fts_dir), "fr")]


def test_the_text_index_reader_reloads_only_when_asked(tmp_path, opened):
    c = _coll(tmp_path)
    fts.index_books(_entries(range(5)), c)
    idx = fts.get_index("en", c)
    idx.reload()
    before = idx.searcher().num_docs
    fts.index_books(_entries(range(5, 12)), c)
    time.sleep(1.2)                    # OnCommitWithDelay's watcher polls every 500 ms
    assert idx.searcher().num_docs == before, "no reload behind the worker's back"
    idx.reload()
    assert idx.searcher().num_docs == before + 7, "an asked reload sees the commit"


def test_the_catalog_writer_reader_reloads_only_when_asked(tmp_path, opened):
    c = _coll(tmp_path)
    catalog.write(c, [(1, "Platelets", "Doe J", "Blood", "en")])
    idx = catalog._indexes[str(catalog.path_for(c))]
    idx.reload()
    before = idx.searcher().num_docs
    for k in range(2, 12):
        catalog.write(c, [(k, f"Title {k}", "Doe J", "Blood", "en")])
    time.sleep(1.2)
    assert idx.searcher().num_docs == before, "no reload behind the writer's back"
    idx.reload()
    assert idx.searcher().num_docs == before + 10
    assert opened.count(str(catalog.path_for(c))) == 1, "the writer's Index opened once"
    assert catalog.count(c) == 11, "a reader's own handle reloads and sees every entry"
