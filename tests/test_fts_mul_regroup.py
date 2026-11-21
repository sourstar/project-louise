"""P6 of the EUR-Lex review (2026-11-21): app/fts.py `index_books` deleted a
record's rows by book_id only in the groups its NEW chunks go to. A `mul`
record (one section per declared language, each in that language's group)
that lost a section language kept its rows in that group; one left with no
chunk at all kept them in every group. The groups a `mul` record can be in
are known (`fts.section_languages`, `fts.groups_for_lang`): it is deleted in
each of those its new chunks do not reach — bounded by the declared
languages, never a read of the index."""
from __future__ import annotations

from tests.conftest import *  # noqa: F401,F403 — the suite's fixtures
from tests.test_fts_batching import _discovered

from app import fts


def _chunk(i, lang, text):
    return {"id": f"w:7:{i}:1", "chapter": lang, "text": text, "tokens": 3}


def _held(c, groups, ids=(7,)):
    return {g: fts.books_held(g, list(ids), c) for g in groups}


def test_a_mul_record_that_loses_a_language_leaves_its_group(tmp_path, monkeypatch, isolated_config):
    c, _con = _discovered(tmp_path)
    monkeypatch.setattr(fts, "section_languages", lambda collection: ("en", "de"))
    de, en, other = fts.group_for("de"), fts.group_for("en"), fts.group_for("mul")
    fts.index_books([("mul", 7, [_chunk(0, "en", "Munich city"), _chunk(1, "de", "München Stadt")]),
                     ("mul", 8, [_chunk(0, "de", "Berlin Stadt")])], c)
    assert _held(c, (en, de)) == {en: 1, de: 1}
    fts.index_books([("mul", 7, [_chunk(0, "en", "Munich city")])], c)     # the German label is gone
    assert _held(c, (en, de)) == {en: 1, de: 0}, "its German rows went with its German section"
    assert _held(c, (de,), ids=(8,)) == {de: 1}, "another record's rows untouched"
    fts.index_books([("mul", 7, [])], c)                                    # no chunk at all
    assert _held(c, (en, de, other)) == {en: 0, de: 0, other: 0}, "every group let go of it"
    # and it comes back where its sections are
    fts.index_books([("mul", 7, [_chunk(0, "de", "München")])], c)
    assert _held(c, (en, de)) == {en: 0, de: 1}


def test_the_deletes_are_bounded_by_the_declared_languages_and_read_nothing(tmp_path, monkeypatch,
                                                                              isolated_config):
    """A COST test: the writes of a batch of `mul` records open only the
    groups the declared languages name (and `other`), and only those held on
    disk; no searcher is opened, whatever the index holds."""
    c, _con = _discovered(tmp_path)
    monkeypatch.setattr(fts, "section_languages", lambda collection: ("en", "de"))
    fts.index_books([("mul", 7, [_chunk(0, "en", "Munich city")])], c)    # en exists on disk, de not
    opened: list[str] = []
    real = fts.get_index

    def watched(group, collection=None):
        opened.append(group)
        idx = real(group, collection)
        return idx
    monkeypatch.setattr(fts, "get_index", watched)
    import tantivy
    real_searcher = tantivy.Index.searcher
    searched: list = []
    monkeypatch.setattr(tantivy.Index, "searcher", lambda self: searched.append(1) or real_searcher(self))
    fts.index_books([("mul", i, [_chunk(0, "en", f"record {i}")]) for i in range(100, 400)], c)
    assert set(opened) <= set(fts.groups_for_lang(c, "mul")), opened
    assert fts.group_for("de") not in opened, "a group with no index on disk is never created"
    assert searched == [], "the write path reads no index"
    # a record of one language is written as before: its own group only
    opened.clear()
    fts.index_books([("en", 500, [_chunk(0, "en", "plain")])], c)
    assert opened == [fts.group_for("en")]
