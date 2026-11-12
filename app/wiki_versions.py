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

import json
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


#: claims rendered per batch by `claim_lines`
LINES_BATCH = 200_000


def claim_lines(m: dict, plugin: str = "wikipedia") -> Iterator[bytes]:
    """`claims(m, plugin)` as the claims file holds them — each line
    `json.dumps(claim, ensure_ascii=False, sort_keys=True)` and a newline —
    in batches of lines, BYTE FOR BYTE (tests/hold/test_annotation_methods.py).
    Rendered from one template whose constant pieces ARE `json.dumps`'s and
    whose variable ones are integers and the language codes, also rendered by
    `json.dumps` once each: 12.3 M claims cost ~5.7 µs each through dicts and
    `json.dumps` (70 s, one core), about a fifth of that here (2026-11-12, the
    method the worker re-runs, app/hold/annotation_methods.py)."""
    from .plugins.wikipedia import ids
    dumps = (lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True))
    lang_of = {o: dumps(code) for code, o in ids.LANG_ORDINAL.items()}
    head = (f'{{"confidence": {dumps(CONFIDENCE)}, "contributor": {dumps(CONTRIBUTOR)}, '
            f'"evidence": {{"object_tokens": ')
    tail = f', "type": {dumps("same-work")}}}\n'
    ref = dumps(f"{plugin}:")[:-1]                  # the opening quote and "<plugin>:"
    method = dumps(METHOD)
    starts = m["item_start"]
    sizes = starts[1:] - starts[:-1]
    multi = np.nonzero(sizes >= 2)[0]
    if not len(multi):
        return
    # every non-shown member with its item's centre, in `claims`' order
    lo = starts[multi]
    n = sizes[multi] - 1
    owner = np.repeat(np.arange(len(multi)), n)
    first = np.cumsum(n) - n
    member = np.repeat(lo + 1, n) + (np.arange(len(owner)) - first[owner])
    centre = lo[owner]
    nid, cen = m["mem_nid"][member], m["mem_nid"][centre]
    lang_s, lang_c = _ordinals(nid), _ordinals(cen)
    toks, toks_c = m["mem_tokens"][member], m["mem_tokens"][centre]
    qids = m["item_qid"][multi][owner]
    for a in range(0, len(member), LINES_BATCH):
        b = slice(a, a + LINES_BATCH)
        yield "".join(
            f'{head}{tc}, "subject_tokens": {t}, "wikidata": "Q{q}"}}, "method": {method}, '
            f'"object": {ref}{c}", "object_lang": {lang_of[lc]}, "subject": {ref}{s_}", '
            f'"subject_lang": {lang_of[ls]}{tail}'
            for s_, c, ls, lc, t, tc, q in zip(nid[b].tolist(), cen[b].tolist(), lang_s[b].tolist(),
                                               lang_c[b].tolist(), toks[b].tolist(), toks_c[b].tolist(),
                                               qids[b].tolist())).encode("utf-8")


def _ordinals(nid: np.ndarray) -> np.ndarray:
    """`ids.decode_native_id`'s language, as its ordinal, vectorised."""
    from .plugins.wikipedia import ids
    rest = np.where(nid >= ids.DUMP_BASE, nid - ids.DUMP_BASE, nid)
    return rest // ids.LANG_BASE


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
