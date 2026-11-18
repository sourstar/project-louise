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
                                         {"gutenberg:2": f(lived=(1500, 1608), lived_span="1500–1608"),
                                          "gutenberg:3": f(year=1880)}])
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
                        lambda c, uids, **k: asked.append(list(uids)) or
                        {"gutenberg:2": f(lived=(1500, 1608), lived_span="1500–1608")})
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


def _aut(name, b, d, role="aut"):
    return {"name": name, "role": role, "birth": str(b), "death": str(d)}


def test_the_century_with_the_authors_dates():
    # Dante, "early 14th century": 1300-1333 ∩ 1265-1321 = 1300-1321 -> 1310
    y = _year(_rec(date="1880", summary="It was written in the early 14th century.",
                   creators=[_aut("Dante", 1265, 1321)]))
    assert y == 1310 and y.by == "its summary's century and its author's dates (early 14th, 1265–1321)"
    # Serao, late 19th: 1867-1899 ∩ 1856-1927 -> 1883
    assert _year(_rec(date="1910", summary="written in the late 19th century",
                      creators=[_aut("Serao", 1856, 1927)])) == 1883


def test_a_century_outside_the_authors_dates_is_the_editions_and_rejected():
    y = _year(_rec(date="1840", summary="It was likely written in the early 19th century.",
                   creators=[_aut("Giannone", 1676, 1748)]))
    assert y == 1712
    assert y.by == "its author's dates (1676–1748) (its summary's 'early 19th century' is outside them)"
    assert _year(_rec(summary="likely written in the late 19th century", creators=[_aut("Sanudo", 1466, 1536)])) == 1501
    assert _year(_rec(summary="written in the early 19th century", creators=[_aut("Ariosto", 1474, 1533)])) == 1503


def test_an_anthology_or_a_record_without_dates_takes_the_century_alone():
    y = _year(_rec(date="1890", summary="Poems written in the 14th century.",
                   creators=[_aut("A", 1265, 1321), _aut("B", 1480, 1530)]))
    assert y == 1349 and y.by == "its summary's century (14th)"
    y = _year(_rec(date="1890", summary="A text written in the mid fifteenth century."))
    assert y == 1450 and y.by == "its summary's century (mid fifteenth)"


def test_the_century_forms_parsed():
    for text, span in [("written in the early 14th century", (1300, 1333)),
                       ("composed in the mid-15th century", (1434, 1466)),
                       ("published in the late nineteenth century", (1867, 1899)),
                       ("written in the 14th–15th centuries", (1300, 1499)),
                       ("printed in the 16th century", (1500, 1599)), ("in 1605", None)]:
        assert works.stated_century([text])[0] == span, text


def test_gutenberg_declares_its_works_year_only():
    assert reading.year_fields("gutenberg") == ("first_published", "lived")


def test_the_works_year_never_the_editions_nor_the_release_date():
    rec = {"title": "Don Chisciotte", "date": "1851",
           "extra": {"issued": "2004-01-01", "marc": {"520": ["The novel was first published in 1605."]}}}
    assert _year(rec) == 1605
    rec["extra"]["marc"] = {}
    assert _year(rec) is None                                   # never 1851, never 2004


def test_a_death_year_alone_or_authors_two_centuries_apart_give_no_year():
    assert _year(_rec(date="1880", creators=[{"name": "X", "role": "aut", "birth": "", "death": "1321"}])) is None
    assert _year(_rec(date="1890", creators=[_aut("A", 1265, 1321), _aut("B", 1480, 1530)])) is None


def test_a_translation_keeps_its_works_year_and_is_never_read(tmp_path):
    plato = _year(_rec(date="1950", creators=[_aut("Plato", -428, -348), _aut("Tr", 1900, 1980, "trl")]))
    assert plato == -388 and plato.translated and plato.by == "its author's dates (-428–-348)"
    gulliver = _year(_rec(date="1842", summary="It was first published in 1726.",
                          creators=[_aut("Swift", 1667, 1745), _aut("Barbieri", 1774, 1853, "trl")]))
    assert gulliver == 1726 and gulliver.translated
    st = _store(tmp_path, ROWS)
    found = reading.lookup(["abondava", "huomo"], st)
    for y in (plato, gulliver):
        assert reading.apply("l'huomo abondava", y, found, reading.RERANK) == ("l'huomo abondava", [])
        assert reading.apply("l'huomo abondava", y, found, reading.CHAT) == ("l'huomo abondava", [])


def test_a_slow_batch_is_bounded_and_gives_no_year(monkeypatch, capsys):
    import time
    from app.ask import works as works_mod
    col = _fake_collection(monkeypatch, [{}])
    monkeypatch.setattr(reading, "BATCH_BOUND_S", 0.05)
    monkeypatch.setattr(reading, "_batch_logged", {})
    monkeypatch.setattr(works_mod, "facts_for", lambda c, uids, **k: time.sleep(0.5) or {})
    assert reading.years([{"chunk_id": "gutenberg:1:0:1"}], col) == {}
    assert "[gutenberg-it warn:reading] years not read within" in capsys.readouterr().out


def test_a_words_readings_are_cached_within_a_version(tmp_path):
    st = _store(tmp_path, ROWS)
    reading._word_cache.clear(); reading._word_cache_version["v"] = ""
    a = reading.lookup(["huomo", "casa"], st)
    assert "huomo" in a.by_form and "casa" not in a.by_form and "casa" in reading._word_cache
    st.con.execute("DELETE FROM wikisource_reading")          # unchanged version: the cache answers
    assert reading.lookup(["huomo"], st).by_form["huomo"][0].reg == "uomo"


def test_every_century_form_the_nodes_summaries_use():
    # the forms München's count found on the node's 1,112 Italian records, 2026-11-18
    for text, span in [("written in the 14th and 15th centuries", (1300, 1499)),
                       ("written in the sixteenth century", (1500, 1599)),
                       ("written in the early 14th-century.", (1300, 1333)),
                       ("published in the early twentieth-century", (1900, 1933)),
                       ("composed in the late nineteenth-century", (1867, 1899)),
                       ("written in the early 14th centuries", (1300, 1333)),
                       ("a text from the first-century", (0, 99)),
                       # adjectival, the work's kind and not its date: none (architecture, 2026-11-18)
                       ("a nineteenth-century novel", None), ("an early 14th-century poem written", None)]:
        assert works.stated_century([text])[0] == span, text


def test_the_index_from_a_child_is_installed_with_its_years_sources(monkeypatch):
    from app.ask import works as works_mod
    f = works_mod.EditionFacts
    col = _fake_collection(monkeypatch, [{}])
    monkeypatch.setattr(reading, "INDEX_IN_CHILD", True)

    class Ex:
        def __init__(self, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def submit(self, fn, name, bound):
            class F:
                def result(self, timeout=None):
                    return ({"gutenberg:1": (1310, "its author's dates (1265–1321)", False),
                             "gutenberg:2": (1726, "its first publication (1726)", True)}, True)
            return F()
    import concurrent.futures as cf
    monkeypatch.setattr(cf, "ProcessPoolExecutor", Ex)
    reading._index_off_process(col, 10)
    got = reading.years([{"chunk_id": "gutenberg:1:0:1"}, {"chunk_id": "gutenberg:2:0:1"}], col)
    assert got == {0: 1310, 1: 1726} and got[1].translated and got[0].by.startswith("its author's")




def test_a_translation_is_marked_by_its_own_corpus_only():
    from app import historical
    assert historical.is_translation({"extra": {"creators_detail": [{"role": "trl"}]}})
    assert historical.is_translation({"title": "Commentario de le piu notabili cose, in Italiana tradotto"})
    assert historical.is_translation({"title": "Traduzione di Elia Morpurgo de' Discorsi Ebraici"})
    assert historical.is_translation({"extra": {"marc": {"520": ["An Italian translation of the novel."]}}})
    # Lammermoor (42881): Scott, no trl, no statement — read as an original, as ruled
    lammermoor = {"title": "La promessa sposa di Lammermoor", "creators": ["Scott, Walter"],
                  "extra": {"creators_detail": [{"role": "cre", "name": "Scott, Walter"}],
                            "marc": {"520": ["... by Walter Scott is a historical novel published in 1819."]}}}
    assert not historical.is_translation(lammermoor)


AUTO = " (This is an automatically generated summary.)"


def test_a_volume_marker_never_separates_a_work():
    k = works.volume_key("Opera nova amorosa", ["a1"])
    for title in ["Opera nova amorosa, Vol. 1", "Opera nova amorosa Volume III", "Opera nova amorosa. Tomo II",
                  "Opera nova amorosa, Parte 2", "Opera nova amorosa - Libro IV", "Opera nova amorosa, Book 3",
                  "Opera nova amorosa 3"]:
        assert works.volume_key(title, ["a1"]) == k, title
    assert works.volume_key("Opera nova amorosa, Vol. 1", ["a2"]) != k
    assert works.volume_key("Opera nova amorosa", []) == ""


def _vol(uid, n, century, title="Opera nova amorosa", summary_extra=""):
    rec = {"title": f"{title}, Vol. {n}",
           "extra": {"marc": {"520": [f"A poem written in the {century} century.{summary_extra}{AUTO}"]},
                     "creators_detail": [{"name": "Notturno", "role": "cre", "agent_id": "2/agents/1"}]}}
    f = works.edition_facts(rec)
    return uid, f, _year(rec)


def test_the_volumes_placed_by_the_century_alone_share_its_majority():
    entries = [_vol("g:30738", 1, "early 16th"), _vol("g:31285", 2, "early 16th"), _vol("g:31818", 3, "early 17th")]
    assert entries[2][2] == 1616
    shared, stats = reading.share_volumes(entries)
    assert {u: int(y) for u, y in shared.items()} == {"g:30738": 1516, "g:31285": 1516, "g:31818": 1516}
    assert shared["g:31818"].by == "its work's volumes (early 16th, 2 of 3 volumes), by PG's automatic summary"
    assert stats == {"groups": 1, "changed": 1, "ties": 0}


def test_a_tie_takes_the_earliest_and_a_dated_volume_keeps_its_own_year():
    a, b = _vol("g:1", 1, "early 17th"), _vol("g:2", 2, "early 16th")
    shared, stats = reading.share_volumes([a, b])
    assert int(shared["g:1"]) == 1516 and stats["ties"] == 1
    rec = {"title": "Opera nova amorosa, Vol. 3", "extra": {"marc": {"520": ["It was published in 1530." + AUTO]},
           "creators_detail": [{"name": "Notturno", "role": "cre", "agent_id": "2/agents/1"}]}}
    dated = ("g:3", works.edition_facts(rec), _year(rec))
    shared, _ = reading.share_volumes([a, b, dated])
    assert "g:3" not in shared


def test_a_year_from_pgs_automatic_summary_says_so():
    y = _year(_rec(summary="The novel was first published in 1605." + AUTO))
    assert y.by == "its first publication (1605), by PG's automatic summary"
    y = _year(_rec(summary="written in the early 14th century." + AUTO))
    assert y.by == "its summary's century (early 14th), by PG's automatic summary"
    assert _year(_rec(summary="first published in 1605.")).by == "its first publication (1605)"


def test_the_shown_first_publication_says_when_pgs_automatic_summary_states_it(monkeypatch):
    from types import SimpleNamespace
    f = works.edition_facts({"extra": {"marc": {"520": ["It was first published in 1605." + AUTO]}}})
    monkeypatch.setattr(works, "facts_for", lambda col, uids, root=None: {"gutenberg:1": f})
    hits = [{"chunk_id": "gutenberg:1:0:1"}]
    works.attach_first_published(SimpleNamespace(name="g", plugin="gutenberg", codex=object()), hits)
    assert hits[0]["first_published"] == 1605 and hits[0]["first_published_by"] == "PG's automatic summary"



def test_a_century_is_the_works_only_in_a_writing_or_publication_context():
    # the node's examples, 2026-11-18
    for text in ["Mulazzi's work is a historical novel set in the 16th century.",                    # 17850
                 "Set in 17th-century Netherlands, the novel follows a tulip grower.",               # 68090
                 "A historical account set in the 14th century, it was written later.",              # 58247
                 "It is a study of music in Italy during the late 18th and early 19th centuries."]:  # 43181
        assert works.stated_century([text])[0] is None, text
    assert works.stated_century(["The commentary was likely written in the early 20th century."])[0] == (1900, 1933)
    assert works.stated_century(["It was likely written in the late 19th century."])[0] == (1867, 1899)
    assert works.stated_century(["The play was first performed in the early 16th century."])[0] == (1500, 1533)
    assert works.century_statement(["It was published in the 16th century."])[2] == "published"


def _life_rec(summary, birth, death, date=None):
    rec = {"extra": {"marc": {"520": [summary]},
                     "creators_detail": [{"name": "A", "role": "aut", "birth": str(birth), "death": str(death)}]}}
    if date:
        rec["date"] = date
    return rec


def test_the_report_of_a_summary_date_outside_the_authors_life():
    giannone = _life_rec("It was likely written in the early 19th century.", 1676, 1748, "1820")
    assert works.summary_date_outside_life(giannone) is None            # the edition's own year inside: not reported
    giannone.pop("date")
    assert "written" in works.summary_date_outside_life(giannone)
    pitre = _life_rec("A collection of tales written in the early 18th century.", 1841, 1916)
    assert works.summary_date_outside_life(pitre) == ("the summary says 'early 18th century' (written); "
                                                      "the author lived 1841–1916")
    posthumous = _life_rec("The diary was published in the late 19th century.", 1700, 1760)
    assert works.summary_date_outside_life(posthumous) is None          # a posthumous publication
    setting = _life_rec("A novel set in the 14th century.", 1850, 1900)
    assert works.summary_date_outside_life(setting) is None             # its setting, never its date
    assert works.summary_date_outside_life(_life_rec("written in the 14th century", 1265, 1321)) is None


def test_gutenberg_declares_the_summary_date_issue():
    from app.plugins.gutenberg import source
    assert works.SUMMARY_DATE_OUTSIDE_LIFE in source.ISSUES



def test_a_writing_verb_governs_during_and_produced_and_crafted_are_writing():
    # München's before/after on the node, 2026-11-18
    assert works.stated_century(["A novel written during the late 19th century."])[0] == (1867, 1899)   # 32193
    assert works.stated_century(["The poem was composed in verse during the early 16th century."])[0] == (1500, 1533)  # 31285
    assert works.stated_century(["It was likely composed during the 16th century."])[0] == (1500, 1599)   # 6938
    assert works.stated_century(["A work likely penned during the mid 18th century."])[0] == (1734, 1766)
    assert works.stated_century(["The text was produced in the early 19th century."])[0] == (1800, 1833)
    assert works.stated_century(["A treatise produced during the 17th century."])[0] == (1600, 1699)    # 27608
    assert works.stated_century(["It was crafted in the late 19th century."])[0] == (1867, 1899)       # 37900
    # setting, no writing verb governing: none
    assert works.stated_century(["A study of music in Italy during the late 18th century."])[0] is None  # 43181
    assert works.stated_century(["It describes a trial held in early 20th-century Italy."])[0] is None   # 25211
    assert works.stated_century(["A novel that challenged 19th-century morality."])[0] is None           # 69113



def test_the_front_matter_marks_a_translation_and_the_body_never():
    from app import historical
    lammermoor = {"title": "La promessa sposa di Lammermoor", "extra": {"creators_detail": [{"role": "cre"}]},
                  "sections": [["Front matter", "LA PROMESSA SPOSA DI LAMMERMOOR. Translated by Gaetano Barbieri."],
                               ["Capitolo I", "Testo."]]}                                              # 42881
    assert historical.is_translation(lammermoor)
    assert historical.is_translation({"sections": [["Front matter", "I DIALOGHI DI LUCIANO VOLGARIZZATI"]]})
    assert historical.is_translation({"sections": [["Front matter", "PRIMA VERSIONE ITALIANA DI G. B."]]})  # 67846
    assert historical.is_translation({"sections": [["Front matter", "VOLGARIZZAMENTO ANTICO TOSCANO"]]})  # 60864
    assert historical.is_translation({"sections": [["Front matter", "tradotto dal francese da un anonimo"]]})
    assert historical.is_translation({"sections": [["Front matter", "recato in italiano da L. P."]]})
    # 45334: a figure, not a translation
    assert not historical.is_translation({"sections": [["Front matter", "una storia tradotta dal regno dei sogni"]]})
    # no Front matter section: the first 5,000 characters only; a phrase deep in the body is never read
    deep = {"sections": [["Capitolo I", "x " * 3000], ["Capitolo XX", "Translated by nobody, said the knight."]]}
    assert not historical.is_translation(deep)
    early = {"sections": [["Capitolo I", "Translated by Ugo Foscolo. " + "x " * 3000]]}
    assert historical.is_translation(early)


def test_a_translation_by_its_front_matter_is_never_read():
    rec = {"title": "La promessa sposa di Lammermoor", "date": "1830",
           "extra": {"marc": {"520": ["... by Walter Scott is a historical novel published in 1819."]},
                     "creators_detail": [{"name": "Scott, Walter", "role": "cre", "birth": "1771", "death": "1832"}]},
           "sections": [["Front matter", "Translated by Gaetano Barbieri."]]}
    y = _year(rec)
    assert y == 1819 and y.translated
