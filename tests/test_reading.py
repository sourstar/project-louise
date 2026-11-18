"""The reader of historical forms (app/reading.py; architecture's ruling and
the operator's words, 2026-11-18): the record's own year by the plugin's
declared fields, the work's first publication before the edition's print
year and never PG's release date; replace with a modern form, gloss with a
lemma; the shown hit untouched."""
from __future__ import annotations

import sqlite3

from app import historical, reading
from app.ask import works
from app.plugins.base import RefSyncState
from app.refs import readings, store as ref_store


def test_gutenberg_declares_the_works_year_first_then_the_editions():
    assert reading.year_fields("gutenberg") == ("first_published", "lived", "date")
    assert reading.year_fields("normattiva") == ()        # operator: "normattiva language is too recent"
    assert reading.year_fields("pubmed") == ()          # declares none: nothing is applied


def test_the_year_is_the_works_then_the_editions_and_never_the_release_date():
    rec = {"title": "Don Chisciotte", "date": "1851",
           "extra": {"issued": "2004-01-01", "marc": {"520": ["The novel was first published in 1605."]}}}
    fields = reading.year_fields("gutenberg")
    assert reading.year_of(fields, works.edition_facts(rec)) == 1605
    rec["extra"]["marc"] = {}
    assert reading.year_of(fields, works.edition_facts(rec)) == 1851
    del rec["date"]
    assert reading.year_of(fields, works.edition_facts(rec)) is None      # never 2004
    assert reading.year_of((), works.edition_facts({"date": "1851"})) is None


def _store(tmp_path, rows):
    con = ref_store.connect(tmp_path / "refs.db")

    class Src:
        def __init__(self, name, table):
            self.source_id, self.ref_version, self.table = name, 1, readings.table(table)

        def schema(self):
            from app.plugins.base import RefSchema
            return RefSchema(source_id=self.source_id, tables=(self.table,))

        def sync(self, state):
            from app.plugins.base import RefRecordBatch
            got = tuple(r for r in rows if r["_src"] == self.source_id)
            yield RefRecordBatch(table=self.table.name, rows=got)

    srcs = [Src(name, table) for name, table in reading.SOURCES]
    for src in srcs:
        ref_store.sync_source(con, src, RefSyncState(ref_root=tmp_path))
    con.row_factory = sqlite3.Row
    return ref_store.RefStore(con, [s.schema() for s in srcs])


def row(src, orig, kind, reg="", lemma="", gloss="", lo=1400, hi=1899):
    return {"_src": src, "id": readings.row_id(orig, "it", lo, hi) + lemma, "orig": orig, "reg": reg,
            "kind": kind, "lemma": lemma, "gloss": gloss, "lang": "it", "period_from": lo,
            "period_to": hi, "method": "test 1"}


ROWS = [row("wikisource-readings", "huomo", readings.REPLACE, reg="uomo"),
        row("tlio", "abondava", readings.GLOSS, lemma="abbondare", gloss="Essere presente in grande quantità",
            lo=1100, hi=1400),
        row("tlio", "huomo", readings.GLOSS, lemma="uomo", gloss="Essere umano", lo=1100, hi=1400)]


def test_a_replacement_keeps_case_and_a_gloss_differs_for_the_reranker_and_the_model(tmp_path):
    st = _store(tmp_path, ROWS)
    found = reading.lookup(reading.words_of(["L'Huomo abondava"]), st)
    assert "wikisource-readings 1 " in found.version and "tlio 1 " in found.version
    # 1350: before the spelling pair's period (1400-1899), so TLIO's glosses only
    text, used = reading.apply("L'Huomo abondava", 1350, found, reading.RERANK)
    assert text == "L'Huomo [uomo] abondava [abbondare]"
    text, _ = reading.apply("L'Huomo abondava", 1350, found, reading.CHAT)
    assert text == "L'Huomo [uomo: Essere umano] abondava [abbondare: Essere presente in grande quantità]"
    # 1400: both hold, and the replacement wins over the gloss
    text, _ = reading.apply("L'Huomo abondava", 1400, found, reading.RERANK)
    assert text == "L'Uomo abondava [abbondare]"
    text, used = reading.apply("L'Huomo abondava", 1600, found, reading.RERANK)
    assert text == "L'Uomo abondava" and [u.source for u in used] == ["wikisource-readings"]
    assert reading.apply("L'Huomo abondava", 1950, found, reading.RERANK) == ("L'Huomo abondava", [])
    assert reading.apply("L'Huomo", 1600, found, reading.RERANK, lang="fr") == ("L'Huomo", [])


def test_the_version_follows_the_synced_table(tmp_path):
    st = _store(tmp_path, ROWS)
    v1 = reading.lookup(["huomo"], st).version
    st2 = _store(tmp_path / "b", ROWS[:1] + [row("tlio", "abondava", readings.GLOSS, lemma="abbondare",
                                                  gloss="altro", lo=1100, hi=1400)])
    assert reading.lookup(["huomo"], st2).version != v1


def test_the_model_reads_the_glossed_copy_and_the_shown_hit_is_untouched(monkeypatch, tmp_path):
    st = _store(tmp_path, ROWS)
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {0: 1554})
    monkeypatch.setattr(reading, "lookup", lambda words, store=None: _real_lookup(words, st))
    hits = [{"text": "L'huomo che habbia", "lang": "it", "title": "Rime", "uid": "gutenberg:1"},
            {"text": "Un testo moderno", "lang": "it", "title": "Oggi", "uid": "gutenberg:2"}]
    result = {"hits": hits}
    out, reads = historical.prepare(result, "chi era?")
    assert result["hits"][0]["text"] == "L'huomo che habbia"     # the shown hit: untouched
    assert out["hits"][0]["text"].startswith("L'uomo")
    assert "glossary" in out["hits"][0]["reading"] and "reading" not in out["hits"][1]
    assert reads[0].why == "year" and reads[0].glossary and "glossary" in reads[0].badge()
    # B's own h- list did not also run on it: the glossary's pairs took its place
    assert historical.H_RULE not in reads[0].rules


_real_lookup = reading.lookup


def test_the_warm_computes_the_versions_off_the_query_path_and_a_query_reuses_them(tmp_path):
    st = _store(tmp_path, ROWS)
    reading._versions.clear()
    got = reading.warm(store=st)
    assert set(got) == {"wikisource-readings", "tlio"}
    calls = []
    real = reading.table_digest
    try:
        reading.table_digest = lambda con, name: calls.append(name) or real(con, name)
        assert reading.lookup(["huomo"], st).version == "; ".join(got[n] for n, _t in reading.SOURCES)
        assert calls == []                       # the query computed nothing
    finally:
        reading.table_digest = real


def test_a_warm_past_its_bound_leaves_the_digest_to_the_first_query(tmp_path, monkeypatch):
    st = _store(tmp_path, ROWS)
    monkeypatch.setattr(reading, "WARM_CHECK_STEPS", 1)
    reading._versions.clear()
    assert reading.warm(bound_s=-1.0, store=st) == {}          # interrupted at once
    assert reading.lookup(["huomo"], st).version                # the query computes it, once


def test_a_normattiva_hit_is_never_read_and_never_moved(monkeypatch):
    from types import SimpleNamespace
    from app import collections as collections_mod
    from app.ask import works as works_mod
    col = SimpleNamespace(name="normattiva-it", plugin="normattiva", codex=object())
    monkeypatch.setattr(collections_mod, "by_name", lambda name: col)
    called = []
    monkeypatch.setattr(works_mod, "facts_for", lambda *a, **k: called.append(a) or {})
    hits = [{"chunk_id": "normattiva:1865-03-20;2248:0:1", "source_collection": "normattiva-it",
             "text": "Le leggi sono pubblicate"}]
    assert reading.years(hits) == {} and called == []         # no fields declared: its facts never read


def _fake_collection(monkeypatch, facts_by_part):
    """A gutenberg collection whose snapshot has these parts' facts."""
    from types import SimpleNamespace
    from app.ask import works as works_mod
    shards = [SimpleNamespace(slice="main", n=i) for i in range(len(facts_by_part))]
    snap = SimpleNamespace(shards=shards)
    col = SimpleNamespace(name="gutenberg-it", plugin="gutenberg", codex=object())
    monkeypatch.setattr(reading, "_snapshot_of", lambda c: ("/tree/gutenberg-it", snap))
    monkeypatch.setattr(works_mod, "part_facts", lambda tree, store, s: facts_by_part[s.n])
    monkeypatch.setattr(works_mod, "facts_dir", lambda root, name, tree: "/store")
    reading._year_index.clear()
    return col


def test_the_year_index_answers_a_rerank_with_nothing_looked_up(monkeypatch):
    from app.ask import works as works_mod
    f = works_mod.EditionFacts
    col = _fake_collection(monkeypatch, [{"gutenberg:1": f(first_published=1605, year=1851)},
                                         {"gutenberg:2": f(year=1554), "gutenberg:3": f()}])
    idx = reading.index_years(col)
    assert idx["complete"] and idx["years"] == {"gutenberg:1": 1605, "gutenberg:2": 1554}
    monkeypatch.setattr(works_mod, "facts_for", lambda *a, **k: (_ for _ in ()).throw(AssertionError("looked up")))
    hits = [{"chunk_id": f"gutenberg:{i}:0:1"} for i in (1, 2, 3, 9)]
    assert reading.years(hits, col) == {0: 1605, 1: 1554}


def test_before_the_index_is_complete_the_missing_records_are_read_in_one_batch(monkeypatch):
    from app.ask import works as works_mod
    f = works_mod.EditionFacts
    col = _fake_collection(monkeypatch, [{}])
    asked = []
    monkeypatch.setattr(works_mod, "facts_for",
                        lambda c, uids, **k: asked.append(list(uids)) or {"gutenberg:2": f(year=1554)})
    hits = [{"chunk_id": f"gutenberg:{i}:0:1"} for i in (1, 2, 2)]
    assert reading.years(hits, col) == {1: 1554, 2: 1554}
    assert asked == [["gutenberg:1", "gutenberg:2"]]          # one batch, each record once
    assert reading.years(hits, col) == {1: 1554, 2: 1554}
    assert asked[1:] == []                                    # both remembered, the yearless one too


def test_the_collection_is_not_reread_per_rerank(monkeypatch):
    from types import SimpleNamespace
    from app import collections as collections_mod
    calls = []
    monkeypatch.setattr(collections_mod, "by_name",
                        lambda n: calls.append(n) or SimpleNamespace(name=n, plugin="pubmed"))
    reading._collections.clear()
    for _ in range(5):
        reading.years([{"chunk_id": "pubmed:1:0:1", "source_collection": "pubmed"}])
    assert calls == ["pubmed"]


def test_a_translations_first_publication_is_the_originals_and_never_used():
    rec = {"title": "Viaggi di Gulliver", "date": "1842",
           "extra": {"marc": {"520": ["Gulliver's Travels was first published in 1726."]},
                     "creators_detail": [{"name": "Swift, Jonathan", "role": "aut"},
                                         {"name": "Barbieri, Gaetano", "role": "trl"}]}}
    f = works.edition_facts(rec)
    assert f.translated and f.first_published == 1726
    assert reading.year_of(reading.year_fields("gutenberg"), f) == 1842        # never 1726
    del rec["date"]
    assert reading.year_of(reading.year_fields("gutenberg"), works.edition_facts(rec)) is None



def _rec(**kw):
    rec = {"title": "t", "extra": {"marc": {}, "creators_detail": []}}
    if "date" in kw:
        rec["date"] = kw.pop("date")
    if "summary" in kw:
        rec["extra"]["marc"]["520"] = [kw.pop("summary")]
    rec["extra"]["creators_detail"] = kw.pop("creators", [])
    return rec


def _year(rec):
    return reading.year_of(reading.year_fields("gutenberg"), works.edition_facts(rec))


def test_dante_by_his_dates_reaches_the_tlio_glosses(tmp_path):
    y = _year(_rec(date="1880", creators=[{"name": "Dante Alighieri", "role": "aut", "birth": "1265",
                                           "death": "1321"}]))
    assert y == 1293 and y.by == "its author's dates (1265–1321)"
    st = _store(tmp_path, ROWS)
    found = reading.lookup(["abondava"], st)
    assert reading.apply("abondava", y, found, reading.RERANK)[0] == "abondava [abbondare]"


def test_a_translation_takes_its_translators_dates_never_the_originals_year():
    y = _year(_rec(date="1842", summary="It was first published in 1726.",
                   creators=[{"name": "Swift", "role": "aut", "birth": "1667", "death": "1745"},
                             {"name": "Barbieri", "role": "trl", "birth": "1774", "death": "1853"}]))
    assert y == 1813 and y.by == "its translator's dates (1774–1853)"


def test_a_death_year_alone_gives_the_editions_year():
    y = _year(_rec(date="1880", creators=[{"name": "X", "role": "aut", "birth": "", "death": "1321"}]))
    assert y == 1880 and y.by == "its edition (1880)"


def test_two_authors_two_hundred_years_apart_give_the_editions_year():
    y = _year(_rec(date="1890", creators=[{"name": "A", "role": "aut", "birth": "1265", "death": "1321"},
                                          {"name": "B", "role": "aut", "birth": "1480", "death": "1530"},
                                          {"name": "E", "role": "edt", "birth": "1850", "death": "1900"}]))
    assert y == 1890


def test_two_authors_of_one_time_give_the_mean_and_editors_never_count():
    y = _year(_rec(date="1890", creators=[{"name": "A", "role": "aut", "birth": "1300", "death": "1360"},
                                          {"name": "B", "role": "cre", "birth": "1310", "death": "1370"},
                                          {"name": "E", "role": "edt", "birth": "1850", "death": "1900"}]))
    assert y == 1335 and y.by == "its author's dates (1300–1360, 1310–1370)"


def test_a_stated_first_publication_wins_and_shows_itself():
    y = _year(_rec(date="1880", summary="Published in 1350.",
                   creators=[{"name": "A", "role": "aut", "birth": "1300", "death": "1360"}]))
    assert y == 1350 and y.by == "its first publication (1350)"


def test_one_definition_of_a_stated_first_publication_for_both_readers():
    from app import historical
    for text, year in [("first published in 1350", 1350), ("Published in 1605.", 1605),
                       ("originally published in 2005", 2005), ("written in the 14th century", None)]:
        rec = {"extra": {"marc": {"520": [text]}}}
        assert works.edition_facts(rec).first_published == year
        assert historical.record_first_published(rec) == year
