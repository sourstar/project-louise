"""Wikipedia: one version per article in search (operator, 2026-11-10;
docs/notes/wikipedia-one-version.md, app/wiki_versions.py). The version
shown is the largest, English on a tie or when its length is unknown; the
operator chose A: the shown version with its best passage."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import numpy as np

from app import config, wiki_versions
from app.plugins.wikipedia import ids

EN, DE, FR, IT = (ids.LANG_ORDINAL[c] for c in ("en", "de", "fr", "it"))


def nid(lang: str, page: int) -> int:
    return ids.native_id_of(lang, page)


def test_the_largest_version_is_shown_english_on_a_tie_or_an_unknown_length():
    # item 1: it bigger than en -> it; item 2: en = de -> en; item 3: en unknown, fr known -> en;
    # item 4: no English, fr 50, it 80 -> it
    rows = [(1, EN, 100, nid("en", 1)), (1, IT, 200, nid("it", 1)),
            (2, EN, 100, nid("en", 2)), (2, DE, 100, nid("de", 2)),
            (3, EN, 0, nid("en", 3)), (3, FR, 300, nid("fr", 3)),
            (4, FR, 50, nid("fr", 4)), (4, IT, 80, nid("it", 4)),
            (0, DE, 999, nid("de", 9))]                                   # no item: not in the map
    qid, lang, tok, n = (np.array(c, dtype=np.int64) for c in zip(*rows))
    m = wiki_versions.arrays(qid, lang, tok, n)
    shown = {int(q): int(m["mem_nid"][m["item_start"][i]]) for i, q in enumerate(m["item_qid"])}
    assert shown == {1: nid("it", 1), 2: nid("en", 2), 3: nid("en", 3), 4: nid("it", 4)}
    assert nid("de", 9) not in set(m["doc_nid"].tolist())
    assert list(m["doc_nid"]) == sorted(m["doc_nid"])


def _hit(n: int, score: float) -> dict:
    return {"chunk_id": f"wikipedia:{n}:1:1", "book_id": n, "text": f"text of {n}", "score": score,
            "lang": ids.decode_native_id(n)[0], "reranked_score": score}


def _with_map(monkeypatch, rows):
    qid, lang, tok, n = (np.array(c, dtype=np.int64) for c in zip(*rows))
    m = wiki_versions.arrays(qid, lang, tok, n)
    monkeypatch.setattr(wiki_versions, "load", lambda collection: m)


WP = SimpleNamespace(plugin="wikipedia")


def test_each_article_once_at_its_best_rank_in_the_shown_version(monkeypatch):
    _with_map(monkeypatch, [(7, EN, 500, nid("en", 7)), (7, IT, 100, nid("it", 7)),
                            (8, EN, 100, nid("en", 8)), (8, DE, 900, nid("de", 8))])
    asked = []

    def passage(collection, plan, n):
        asked.append(n)
        return {"chunk_id": f"wikipedia:{n}:4:1", "book_id": n, "text": "the German passage", "lang": "de"}
    other = {"chunk_id": "wikipedia:1:1:1", "book_id": 1, "text": "no item", "score": 0.5}
    hits = [_hit(nid("it", 7), 0.9), other, _hit(nid("en", 7), 0.8), _hit(nid("en", 8), 0.7)]
    out = wiki_versions.one_version(hits, WP, None, passage=passage)
    assert [h["chunk_id"] for h in out] == [f"wikipedia:{nid('en', 7)}:1:1", "wikipedia:1:1:1",
                                            f"wikipedia:{nid('de', 8)}:4:1"]
    assert out[0]["score"] == 0.9, "the article keeps its best rank's place and scores"
    assert out[0]["version_shown"] == "matched"
    assert [(e["lang"], e["rank"]) for e in out[0]["editions"]] == [("it", 1)]
    assert out[2]["text"] == "the German passage" and out[2]["book_id"] == nid("de", 8)
    assert out[2]["version_shown"] == "shown version, its best passage"
    assert [(e["lang"], e["rank"]) for e in out[2]["editions"]] == [("en", 4)], "the matched version listed"
    assert asked == [nid("de", 8)], "a passage looked up only where the shown version did not match"


def test_no_map_or_no_passage_leaves_the_matched_version(monkeypatch):
    monkeypatch.setattr(wiki_versions, "load", lambda collection: None)
    hits = [_hit(nid("it", 7), 0.9), _hit(nid("en", 7), 0.8)]
    assert wiki_versions.one_version(hits, WP, None) == hits, "no map yet: as ranked"
    _with_map(monkeypatch, [(8, EN, 100, nid("en", 8)), (8, DE, 900, nid("de", 8))])
    out = wiki_versions.one_version([_hit(nid("en", 8), 0.7)], WP, None, passage=lambda c, p, n: None)
    assert out[0]["book_id"] == nid("en", 8) and out[0]["version_shown"].startswith("matched")


def test_the_map_is_built_once_per_signature_and_read_memory_mapped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path / "cache"))
    manifest = tmp_path / "manifest.db"
    con = sqlite3.connect(manifest)
    con.execute("CREATE TABLE documents(native_id TEXT, tokens INTEGER)")
    for lang, page, tokens in (("en", 7, 500), ("it", 7, 100), ("de", 8, 900), ("en", 8, 100), ("fr", 9, 10)):
        con.execute("INSERT INTO documents VALUES(?,?)", (str(nid(lang, page)), tokens))
    con.commit()
    coll = SimpleNamespace(plugin="wikipedia", manifest_path=manifest, name="wikipedia")
    item = {7: 7007, 8: 8008}

    def qids_for(collection, langs):
        return {o: (np.array(sorted(item)), np.array([item[p] for p in sorted(item)])) for o in langs}
    out = wiki_versions.build(coll, con, signature="s1", qids_for=qids_for)
    assert out["items"] == 2 and out["with_item"] == 4 and out["documents"] == 5
    assert str(wiki_versions.place(coll)).startswith(str(tmp_path / "cache"))
    assert wiki_versions.build(coll, con, signature="s1", qids_for=qids_for) == {"skipped": "up to date"}
    m = wiki_versions.load(coll)
    assert isinstance(m["doc_nid"], np.memmap)
    assert wiki_versions._members(m, 8008) == [nid("de", 8), nid("en", 8)]
    assert wiki_versions._item_of(m, nid("fr", 9)) is None
