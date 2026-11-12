"""WIKIPEDIA'S ARTICLES AS AN ANNOTATIONS COLLECTION (operator, 2026-11-10;
docs/notes/wikipedia-one-version.md). Each article — a Wikidata item — is
one work, its language versions its editions; the version shown is English
by default, another language's when it is BIGGER ("some entries are specific
to a nation, and the version in that nation's language is often bigger and
more accurate"). NOT enforced by the wikipedia collection (operator, the
same day: "I don't want to silently hijack the searches of the other
peers"): stated as `same-work` claims in the annotations collection
`wikipedia-articles-cache`, which a peer holds or not and a reader applies or not
(05 §5.3, invariant 4).

THE CLAIMS (`claims`, written by tools/annotations_from_wikipedia.py): a
STAR per item, as gutenberg's (tools/annotations_from_works.py): every other
version claimed `same-work` against the version shown, so an article of n
versions is n-1 claims and the shown one is the centre; the evidence carries
the item and both versions' lengths (the manifest's `tokens`), from which
the search shows the largest.
"""
from __future__ import annotations

from typing import Any, Iterator

import numpy as np

_ENGLISH = 0                        # app/plugins/wikipedia/ids.py LANG_ORDINAL["en"]
_UNKNOWN_FIRST = np.iinfo(np.int64).max


def shown_order(qid: np.ndarray, lang: np.ndarray, tokens: np.ndarray, nid: np.ndarray) -> np.ndarray:
    """The members of every item, grouped by item, THE SHOWN ONE FIRST: the
    largest by `tokens`; English on a tie (its ordinal is 0) and when its
    own length is not known yet; then the lower native id (the same data
    gives the same choice)."""
    eff = np.where((lang == _ENGLISH) & (tokens <= 0), _UNKNOWN_FIRST, tokens)
    return np.lexsort((nid, lang, -eff, qid))


def arrays(qid: np.ndarray, lang: np.ndarray, tokens: np.ndarray, nid: np.ndarray) -> dict[str, np.ndarray]:
    """The map's arrays from parallel per-document arrays (`qid` 0: none)."""
    keep = qid != 0
    qid, lang, tokens, nid = qid[keep], lang[keep], tokens[keep], nid[keep]
    order = shown_order(qid, lang, tokens, nid)
    mq = qid[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = mq[1:] != mq[:-1]
    by_doc = np.argsort(nid, kind="stable")
    return {"doc_nid": nid[by_doc].astype(np.int64), "doc_qid": qid[by_doc].astype(np.int64),
            "item_qid": mq[first].astype(np.int64),
            "item_start": np.append(np.nonzero(first)[0], len(order)).astype(np.int64),
            "mem_nid": nid[order].astype(np.int64), "mem_tokens": tokens[order].astype(np.int64)}


#: the run's attribution, as the claims hold it: the collection was named
#: `wikipedia-articles` then (renamed `wikipedia-articles-cache`, operator
#: 2026-11-12), and the string stays so that an update re-states the same
#: claims byte for byte rather than re-issuing 12.3 M of them
CONTRIBUTOR = "Louise reference node (wikipedia-articles, 2026-11-10)"
METHOD = {"kind": "wikidata"}
#: Wikipedia's own statement: a page's `wikibase_item` (page_props)
CONFIDENCE = "1"


def _lang(nid: int) -> str:
    from .plugins.wikipedia import ids
    return ids.decode_native_id(int(nid))[0]


def claims(m: dict, plugin: str = "wikipedia") -> Iterator[dict[str, Any]]:
    """The `same-work` claims of `arrays`' output: per item of two versions
    or more, each other version against the one shown (the centre)."""
    starts = m["item_start"]
    for k, q in enumerate(m["item_qid"]):
        lo, hi = int(starts[k]), int(starts[k + 1])
        if hi - lo < 2:
            continue
        centre, centre_t = int(m["mem_nid"][lo]), int(m["mem_tokens"][lo])
        for j in range(lo + 1, hi):
            nid, t = int(m["mem_nid"][j]), int(m["mem_tokens"][j])
            yield {"type": "same-work", "subject": f"{plugin}:{nid}", "object": f"{plugin}:{centre}",
                   "subject_lang": _lang(nid), "object_lang": _lang(centre),
                   "method": METHOD, "evidence": {"wikidata": f"Q{int(q)}", "subject_tokens": t,
                                                  "object_tokens": centre_t},
                   "confidence": CONFIDENCE, "contributor": CONTRIBUTOR}


def from_manifest(collection, con, *, qids_for=None) -> dict:
    """`arrays` for the wikipedia collection `collection` from its manifest
    (`rowid, native_id, tokens`) and its page_props items (as the smart
    embedding reads them, app/embed_select.py)."""
    from . import embed_select
    m = embed_select.read_manifest(con)
    ok = m["native_id"] >= 0
    lang, page = embed_select.decode(np.where(ok, m["native_id"], 0))
    lang = np.where(ok, lang, -1)
    langs = sorted(int(x) for x in np.unique(lang[lang >= 0]))
    by_lang = (qids_for or embed_select.load_qids)(collection, langs)
    qid = np.zeros(len(lang), dtype=np.int64)
    for o, (pages_of, qids_of) in by_lang.items():
        at = np.nonzero(lang == o)[0]
        qid[at] = embed_select.lookup(pages_of, qids_of, page[at])
    return arrays(np.where(ok, qid, 0), lang, m["tokens"], m["native_id"])
