"""One hit per work (05 §5.3, amended 2026-10-09; app/ask/works.py).

The measurement (docs/notes/one-language-per-work.md, 451 queries): every
rule that keeps ONE language per work lost the work the reader wanted on
57–83 queries; showing each work once, at its best edition's position and
in the query's language where the work has a book in it, was better on 50
and worse on none. Work groups come from held claims (02 §2.10), never from
a guess at search time; nothing is dropped — the other editions are listed
under the hit."""
from types import SimpleNamespace

from app import collections as collections_mod, retrieval
from app.ask import works
from tests.ask.test_annotation_facts import setup


def hit(uid: str, lang: str, seq: int = 0) -> dict:
    return {"chunk_id": f"{uid}:{seq}:1", "book_id": int(uid.split(":")[1]), "lang": lang,
            "text": uid}


E = works.Edition
DQ = [E("g:1", "Don Quijote", "es", True), E("g:2", "Don Quixote", "en", True),
      E("g:3", "Don Quichotte", "fr", True), E("g:4", "Don Quijote II", "es", False)]
WORK_OF = {e.uid: "g:1" for e in DQ}
MEMBERS = {"g:1": DQ}


def test_a_work_is_shown_once_where_its_best_edition_ranked_in_the_preferred_language():
    hits = [hit("g:9", "de"), hit("g:2", "en"), hit("g:8", "en"), hit("g:3", "fr"), hit("g:1", "es")]
    out = works.collapse(hits, WORK_OF, MEMBERS, works.preference(None, "fr"))
    assert [retrieval.record_of(h) for h in out] == ["g:9", "g:3", "g:8"], \
        "the work at rank 2, its best edition's place, shown in French; nothing else moves"
    ed = out[1]["editions"]
    assert [e["uid"] for e in ed] == ["g:2", "g:1", "g:4"], "English next (the fallback), then by rank"
    assert ed[0] == {"uid": "g:2", "title": "Don Quixote", "lang": "en", "held": True,
                     "rank": 2, "chunk_id": "g:2:0:1"}
    assert ed[2]["rank"] is None and ed[2]["held"] is False, "an edition not found is still listed"
    assert "editions" not in out[0], "a record of no work is untouched"


def test_the_picker_comes_first_then_the_query_s_language_then_english_then_the_best_ranked():
    assert works.preference("de", "fr") == ("de", "fr", "en")
    assert works.preference(None, None) == ("en",)
    assert works.preference("original", "EN") == ("en",)
    hits = [hit("g:3", "fr"), hit("g:2", "en"), hit("g:1", "es")]
    shown = lambda prefer: retrieval.record_of(works.collapse(hits, WORK_OF, MEMBERS, prefer)[0])
    assert shown(works.preference("es", "fr")) == "g:1"
    assert shown(works.preference(None, "it")) == "g:2", "no Italian edition: English"
    assert shown(()) == "g:3", "no preference met: the best-ranked"


def test_a_search_fills_its_page_with_distinct_works(monkeypatch):
    """The grouping runs after the reranker and BEFORE the cut to k: a page
    of three is three works."""
    hits = [hit("g:2", "en"), hit("g:3", "fr"), hit("g:7", "en"), hit("g:1", "es"), hit("g:8", "en")]
    monkeypatch.setattr(retrieval.refs, "get_store", lambda: None)
    monkeypatch.setattr(retrieval.refs.RESOLVER, "resolve", lambda q, s: [])
    monkeypatch.setattr(retrieval, "_search_one_collection",
                        lambda con, c, q, k, mode, tr, ids, plan=None, depth=0: {
                            "catalog": [], "merged": [dict(x) for x in hits], "translations": {},
                            "timings": {}, "scope": {}})
    monkeypatch.setattr(retrieval, "_attach_metadata_and_enqueue", lambda con, c, h: None)
    monkeypatch.setattr(retrieval, "_rerank_list", lambda q, h, r, b, d: (h, False))
    monkeypatch.setattr(works, "work_groups", lambda c, uids, **kw: (WORK_OF, MEMBERS))
    c = SimpleNamespace(name="g", plugin="g", codex=object())
    out = retrieval.search(None, "q", k=3, collection=c, translate=False, show_in="fr")
    assert [retrieval.record_of(h) for h in out["hits"]] == ["g:3", "g:7", "g:8"]
    assert out["query_lang"] is None


def test_a_grouping_that_cannot_run_returns_the_list_as_ranked(monkeypatch):
    hits = [hit("g:2", "en"), hit("g:3", "fr")]
    monkeypatch.setattr(works, "work_groups",
                        lambda c, uids, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    c = SimpleNamespace(name="g", codex=object())
    assert works.one_hit_per_work(hits, c, ("en",)) == hits
    assert works.one_hit_per_work(hits, SimpleNamespace(name="g", codex=None), ("en",)) == hits


def test_the_work_groups_are_read_from_the_held_claims(tmp_path):
    """pubmed:1 (la) same-work pubmed:2, and pubmed:3 translation-of
    pubmed:1: one work of three records, joined through pubmed:1 even when
    only pubmed:2 and pubmed:3 are among the hits."""
    target, tcoll, held, codex = setup(tmp_path)
    work_of, members = works.work_groups(tcoll, ["pubmed:2", "pubmed:4"], root=tmp_path,
                                         annotations=[held])
    assert work_of == {"pubmed:1": "pubmed:1", "pubmed:2": "pubmed:1", "pubmed:3": "pubmed:1"}
    assert members["pubmed:1"] == [E("pubmed:1", "T1", "la", True), E("pubmed:2", "T2", "en", True),
                                   E("pubmed:3", "T3", "en", True)]
    hits = [hit("pubmed:2", "en"), hit("pubmed:4", "en"), hit("pubmed:1", "la")]
    out = works.one_hit_per_work(hits, tcoll, works.preference(None, "la"), root=tmp_path,
                                 annotations=[held])
    assert [retrieval.record_of(h) for h in out] == ["pubmed:1", "pubmed:4"]
    assert [e["uid"] for e in out[0]["editions"]] == ["pubmed:2", "pubmed:3"]


def test_a_withheld_annotations_copy_groups_nothing(tmp_path, monkeypatch):
    from app import copy_state
    target, tcoll, held, codex = setup(tmp_path)
    monkeypatch.setattr(copy_state, "withholds", lambda c: "quarantine: roots not yet run")
    assert works.work_groups(tcoll, ["pubmed:1"], root=tmp_path, annotations=[held]) == ({}, {})


def test_no_annotations_collection_about_it_costs_only_the_listing(tmp_path, monkeypatch):
    target, tcoll, held, codex = setup(tmp_path)
    monkeypatch.setattr(collections_mod, "load_annotations", lambda: [])
    assert works.work_groups(tcoll, ["pubmed:1"], root=tmp_path) == ({}, {})


# ---- which edition a work shows (operator, 2026-10-22) ----------------------

F = works.EditionFacts


def test_unabridged_wins_even_over_the_readers_language():
    """"1. always prefer the unabridged ones (even before criteria 2)": a
    French abridgement does not beat the English full text for a French
    reader; among unabridged editions the language decides as before."""
    hits = [hit("g:2", "en"), hit("g:3", "fr"), hit("g:1", "es")]
    facts = {"g:3": F(abridged=True)}
    out = works.collapse(hits, WORK_OF, MEMBERS, works.preference(None, "fr"), facts)
    assert retrieval.record_of(out[0]) == "g:2"
    out = works.collapse(hits, WORK_OF, MEMBERS, works.preference(None, "fr"), {})
    assert retrieval.record_of(out[0]) == "g:3", "no facts: as before"


def test_the_corrected_edition_wins_within_a_language():
    """"2. the newer editions carrying corrections over the older ones of the
    same printing company"; and PG's own "higher quality eBook" note."""
    hits = [hit("g:1", "es"), hit("g:4", "es")]
    newer = {"g:1": F(publisher="ibarra", year=1780), "g:4": F(publisher="ibarra", year=1787)}
    assert retrieval.record_of(works.collapse(hits, WORK_OF, MEMBERS, ("es",), newer)[0]) == "g:4"
    other_house = {"g:1": F(publisher="ibarra", year=1780), "g:4": F(publisher="sancha", year=1787)}
    assert retrieval.record_of(works.collapse(hits, WORK_OF, MEMBERS, ("es",), other_house)[0]) == "g:1", \
        "another printing company: the rank decides"
    named = {"g:1": F(better_as=("4",))}
    assert retrieval.record_of(works.collapse(hits, WORK_OF, MEMBERS, ("es",), named)[0]) == "g:4"


def test_edition_facts_read_the_records_own_words():
    rec = {"title": "Don Quixote (Abridged for Schools)", "date": "1890",
           "extra": {"marc": {"260": ["London : Macmillan, 1890."]},
                     "description": "A somewhat higher quality eBook is available as #49652."}}
    f = works.edition_facts(rec)
    assert f == F(abridged=True, better_as=("49652",), publisher="macmillan", year=1890)
    assert works.edition_facts({"title": "Les Misérables (Unabridged)"}).abridged is False
    assert works.edition_facts({"title": "X", "extra": {"marc": {"520": ["A condensed retelling of the epic."]}}}).abridged
    assert works.edition_facts({"title": "Plain", "date": "1901"}) == F(year=1901)


# ---- the edition facts, a dict lookup per part (München, 2026-10-22) --------

def _two_editions(tmp_path):
    target, tcoll, held, codex = setup(tmp_path)
    hits = [hit("pubmed:1", "la"), hit("pubmed:2", "en"), hit("pubmed:4", "en")]
    return tcoll, hits, {"pubmed:1": "w", "pubmed:2": "w"}


def test_a_parts_facts_are_read_once_then_looked_up(tmp_path, monkeypatch):
    """The first search that meets a part reads its two records alone and
    queues the part; the file built off the search's thread then answers
    every later search without reading a record."""
    from app.pack import records as pack_records
    tcoll, hits, work_of = _two_editions(tmp_path)
    first = works._facts_of(tcoll, hits, work_of, root=tmp_path)
    assert first == {"pubmed:1": F(year=2023), "pubmed:2": F(year=2023)}, "pubmed:4 is no choice"
    works.CACHE.queue.join()
    files = list((tmp_path / "pubmed" / "state" / "edition-facts").glob("v*.json"))
    assert [f.name.split("-")[0] for f in files] == [f"v{works.FACTS_VERSION}"]
    works.CACHE.clear()

    def no_read(*a, **k):
        raise AssertionError("a record was read")
    monkeypatch.setattr(pack_records, "read_fields", no_read)
    monkeypatch.setattr(pack_records, "iter_records", no_read)
    assert works._facts_of(tcoll, hits, work_of, root=tmp_path) == first, "from the file"
    files[0].unlink()
    assert works._facts_of(tcoll, hits, work_of, root=tmp_path) == first, "from memory"


def test_a_new_version_of_a_part_is_another_file(tmp_path):
    """Named by the part's root: a rewritten part never meets the facts of
    the old one."""
    from dataclasses import replace
    from app.share import seeder
    tcoll, hits, work_of = _two_editions(tmp_path)
    tree = tmp_path / "pubmed" / "shard"
    shard = seeder.current_snapshot(tree).shards[0]
    store = works.facts_dir(tmp_path, "pubmed", tree)
    old = works.build_part_facts(tree, store, shard)
    new = works.build_part_facts(tree, store, replace(shard, root="sha256:" + "ee" * 32))
    assert old != new and old.exists() and new.exists()
    assert works.part_facts(tree, store, shard)["pubmed:3"] == F(year=2023)


def test_the_search_path_loads_a_snapshot_once(tmp_path, monkeypatch):
    from app.hold import snapshot as sn
    from app.share import seeder
    setup(tmp_path)
    tree = tmp_path / "pubmed" / "shard"
    # a cold process: the setup's own reads went through the one shared
    # load (`seeder._shared`, 2026-11-08), which would answer this one too
    monkeypatch.setattr(seeder, "_SNAPS", type(seeder._SNAPS)())
    loads = []
    real = sn.load
    monkeypatch.setattr(sn, "load", lambda *a, **k: loads.append(1) or real(*a, **k))
    one = seeder.cached_snapshot(tree)
    assert seeder.cached_snapshot(tree) is one and len(loads) == 1
    newer = tree / "snapshots" / "2099-01.json"
    newer.write_bytes((tree / "snapshots" / f"{one.snapshot_id}.json").read_bytes())
    seeder.cached_snapshot(tree)
    assert len(loads) == 2, "a newer snapshot is loaded"


# ---- the largest edition, where the claims state the lengths ----------------
# (operator, 2026-11-10, wikipedia: "English by default, but if another
# language's version is BIGGER, that one is taken instead"; choice A: show
# its passage that best matches the question; docs/notes/wikipedia-one-version.md)

WK = [E("w:1", "Roma", "it", True, 900, True), E("w:2", "Rome", "en", True, 400),
      E("w:3", "Rom", "de", True, 300)]
WK_OF = {e.uid: "w:1" for e in WK}


def test_the_largest_edition_is_shown_english_on_a_tie_or_where_its_length_is_unknown():
    assert works.largest(WK).uid == "w:1"
    tie = [E("w:1", "Roma", "it", True, 400, True), E("w:2", "Rome", "en", True, 400)]
    assert works.largest(tie).uid == "w:2", "a tie: English"
    unknown = [E("w:1", "Roma", "it", True, 900, True), E("w:2", "Rome", "en", True, 0),
               E("w:3", "Rom", "de", True, 300)]
    assert works.largest(unknown).uid == "w:2", "English's length not known yet: English (shown_order)"
    two = [E("w:5", "A", "de", True, 700), E("w:6", "B", "fr", True, 700, True)]
    assert works.largest(two).uid == "w:6", "a tie elsewhere: the claims' centre, as their producer chose"
    assert works.largest(DQ) is None, "no lengths stated (gutenberg's claims): the rule does not apply"


def test_the_largest_edition_matched_shows_its_passage_at_the_works_best_place():
    hits = [hit("w:9", "en"), hit("w:2", "en"), hit("w:1", "it", seq=4)]
    hits[1]["rerank_score"] = 0.9
    out = works.collapse(hits, WK_OF, {"w:1": WK}, works.preference(None, "en"))
    assert [retrieval.record_of(h) for h in out] == ["w:9", "w:1"], "at rank 2, the work's best place"
    head = out[1]
    assert head["chunk_id"] == "w:1:4:1" and head["lang"] == "it" and head["text"] == "w:1"
    assert head["rerank_score"] == 0.9, "its place and scores stay the best-ranked edition's"
    assert head["shown_as"] == {"edition": "w:1", "rule": "the largest edition, English on a tie"}
    assert [(e["uid"], e["rank"]) for e in head["editions"]] == [("w:2", 2), ("w:3", None)]


def test_the_largest_edition_not_matched_shows_its_best_passage_for_the_question():
    hits = [hit("w:2", "en"), hit("w:3", "de")]
    asked = []

    def passages(es):
        asked.append([e.uid for e in es])
        return {"w:1": {"chunk_id": "w:1:7:1", "book_id": 1, "lang": "it", "text": "il passo"}}
    out = works.collapse(hits, WK_OF, {"w:1": WK}, ("en",), passages=passages)
    assert asked == [["w:1"]] and len(out) == 1, "one batch"
    assert out[0]["chunk_id"] == "w:1:7:1" and out[0]["text"] == "il passo"
    assert out[0]["shown_as"]["passage"] == "its best match for the question"
    assert [(e["uid"], e["rank"]) for e in out[0]["editions"]] == [("w:2", 1), ("w:3", 2)]


def test_no_passage_of_the_largest_edition_falls_back_to_the_editions_found():
    hits = [hit("w:3", "de"), hit("w:2", "en")]
    for passages in (None, lambda es: {}, lambda es: 1 / 0):
        out = works.collapse(hits, WK_OF, {"w:1": WK}, ("en",), passages=passages)
        assert retrieval.record_of(out[0]) == "w:2" and "shown_as" not in out[0]


def test_passages_are_searched_only_for_the_works_on_the_page():
    """The page is cut to `limit` after the grouping: a work below it costs
    no search."""
    other = [E("v:1", "Parigi", "it", True, 900, True), E("v:2", "Paris", "en", True, 400)]
    work_of = {**WK_OF, **{e.uid: "v:1" for e in other}}
    members = {"w:1": WK, "v:1": other}
    hits = [hit("w:2", "en"), hit("x:5", "en"), hit("v:2", "en")]
    asked = []
    works.collapse(hits, work_of, members, ("en",), passages=lambda es: asked.extend(e.uid for e in es),
                   limit=2)
    assert asked == ["w:1"], "Paris is third: cut"
    asked.clear()
    works.collapse(hits, work_of, members, ("en",), passages=lambda es: asked.extend(e.uid for e in es))
    assert asked == ["w:1", "v:1"]


def test_the_lengths_and_the_centre_are_read_from_the_claims(tmp_path):
    from tests.hold.test_annotations import a_claim, claims_file
    target, tcoll, held, codex = setup(tmp_path, claims=claims_file(
        a_claim("pubmed:2", "pubmed:1", evidence={"wikidata": "Q1", "subject_tokens": 40,
                                                  "object_tokens": 90}),
        a_claim("pubmed:3", "pubmed:1", evidence={"wikidata": "Q1", "subject_tokens": 10,
                                                  "object_tokens": 90})))
    _work_of, members = works.work_groups(tcoll, ["pubmed:3"], root=tmp_path, annotations=[held])
    assert members["pubmed:1"] == [E("pubmed:1", "T1", "la", True, 90, True),
                                   E("pubmed:2", "T2", "en", True, 40),
                                   E("pubmed:3", "T3", "en", True, 10)]


def test_the_best_passage_is_a_text_search_inside_that_record_else_its_first(monkeypatch):
    from app import fts
    calls = []
    monkeypatch.setattr(fts, "search_records",
                        lambda g, q, n, collection=None, book_ids=None: calls.append((g, q, book_ids))
                        or ([{"chunk_id": "w:7:3:1"}] if q == "roma antica" else []))
    monkeypatch.setattr(fts, "book_chunks", lambda g, b, c: [{"chunk_id": "w:7:2:1"}, {"chunk_id": "w:7:0:1"}])
    plan = SimpleNamespace(core="ancient rome", variants=lambda g, held: ["roma antica"])
    e = E("w:7", "Roma", "it", True, 9)
    assert works.best_passage(None, plan, e) == {"chunk_id": "w:7:3:1"}
    assert calls == [("it", "roma antica", [7])]
    plan = SimpleNamespace(core="ancient rome", variants=lambda g, held: ["nothing"])
    assert works.best_passage(None, plan, e) == {"chunk_id": "w:7:0:1"}, "no match: its first passage"
    assert works.best_passage(None, plan, E("w:x", "?", "it", True)) is None
    assert works.best_passages(None, plan, [e, E("w:x", "?", "it", True)]) == {"w:7": {"chunk_id": "w:7:0:1"}}
