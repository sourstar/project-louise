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


def test_the_claims_are_a_star_around_the_version_shown():
    rows = [(7, EN, 500, nid("en", 7)), (7, IT, 100, nid("it", 7)), (7, DE, 300, nid("de", 7)),
            (8, EN, 100, nid("en", 8)), (8, DE, 900, nid("de", 8)),
            (9, FR, 10, nid("fr", 9))]                                     # one version: no claim
    qid, lang, tok, n = (np.array(c, dtype=np.int64) for c in zip(*rows))
    out = list(wiki_versions.claims(wiki_versions.arrays(qid, lang, tok, n)))
    pairs = [(c["subject"], c["object"]) for c in out]
    assert pairs == [(f"wikipedia:{nid('de', 7)}", f"wikipedia:{nid('en', 7)}"),
                     (f"wikipedia:{nid('it', 7)}", f"wikipedia:{nid('en', 7)}"),
                     (f"wikipedia:{nid('en', 8)}", f"wikipedia:{nid('de', 8)}")], \
        "n-1 claims per article, every one against the version shown (the largest)"
    c = out[2]
    assert c["type"] == "same-work" and c["subject_lang"] == "en" and c["object_lang"] == "de"
    assert c["evidence"] == {"wikidata": "Q8", "subject_tokens": 100, "object_tokens": 900}
    assert c["confidence"] == "1" and c["method"] == {"kind": "wikidata"}


def test_nothing_in_search_or_the_worker_groups_wikipedia_by_itself():
    """Operator, 2026-11-10: "I don't want to silently hijack the searches of
    the other peers": the grouping is an annotations collection's, never the
    wikipedia collection's own logic."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app"
    users = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")
                   if "wiki_versions" in p.read_text(encoding="utf-8") and p.name != "wiki_versions.py")
    assert users == [], users
