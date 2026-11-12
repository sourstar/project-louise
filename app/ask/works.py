"""One hit per work (05 §5.3, amended 2026-10-09;
docs/notes/one-language-per-work.md).

Gutenberg holds a work and its translations as separate books, and a search
used to show each edition that matched as its own hit. Keeping ONE language
per work costs recall whichever language is kept — measured over 451
queries, every such filter lost the work the reader was after on 57–83 of
them, because the edition that matches a query cannot be predicted from its
language. What wins (50 queries better, 0 worse) is to rank as before and
then SHOW EACH WORK ONCE, at the position of its best-ranked edition, as the
edition in the reader's language when one of the work's editions in it was
found, else English, else the best-ranked one; the other editions are listed
under it, so nothing is lost.

WHAT A WORK IS comes from claims, never from a guess at search time: the
`same-work` and `translation-of` claims of the annotations collections this
peer holds and may use about the result's collection (02 §2.10, 05 §5.5 —
a link the origin does not state is an annotation), read from the claim
index `app/hold/annotations.py` keeps per snapshot. For gutenberg those are
the books' own "Wikipedia page about this book" lines joined by Wikidata
sitelinks (`tools/annotations_from_works.py`) — and from the relations the
collection's OWN origin states that its plugin names as work relations
(`WORK_RELATIONS`: gutenberg's `bf:otherEdition`, Project Gutenberg's
"several editions of this eBook" notes; 2026-11-12, operator decision 1 A,
which moved those notes from claims to relations), read from the edges the
index maintainer keeps in the manifest (app/ask/traverse.py
`index_edges`), between records held here. No claim and no such edge, no
grouping: an edition neither names is shown as it ranked.

The ranked list is unchanged up to the grouping — its scores, its order,
the reranker's work. This is presentation, after the reranker and before
the cut to `limit`, so a page of ten shows ten works.

COST per search: the annotations collections held are listed once; for one
that applies, one walk of its in-memory claim index from the candidates'
uids (bounded by their works' sizes) and one indexed manifest query for the
titles and languages of the editions found. A plugin with work relations
adds one indexed query per step of the walk (`idx_relations_from`,
`idx_relations_to`), over the step's new uids. A collection no annotations
collection is about and whose plugin names no work relation pays the
listing alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.ask.part_cache import PartCache

#: the claim types that say two records are one work
WORK_TYPES = ("same-work", "translation-of")
ENGLISH = "en"


@dataclass(frozen=True)
class Edition:
    """A record of a work, as the target collection holds it."""
    uid: str
    title: str | None
    lang: str | None
    held: bool
    #: its length where the work's claims state it (`subject_tokens` /
    #: `object_tokens` in their evidence: wikipedia-articles-cache, 2026-11-10);
    #: None where they do not (gutenberg's)
    size: int | None = None
    #: the edition the claims are drawn around (every sized claim's
    #: `object`): their producer's choice, which settles a tie of lengths
    #: between two non-English editions exactly as it did
    centre: bool = False


def largest(members: Sequence[Edition]) -> Edition | None:
    """THE EDITION SHOWN WHERE THE CLAIMS STATE THE LENGTHS (operator,
    2026-11-10, wikipedia: "English by default, but if another language's
    version is BIGGER, that one is taken instead"): the largest; English on
    a tie or where English's length is unknown (the claims' producer,
    tools/annotations_from_wikipedia.py, orders them so); then the claims'
    centre. None
    where fewer than two of the work's editions have a stated length — the
    rule is the annotation's, and gutenberg's claims state none."""
    sized = [e for e in members if e.size is not None]
    if len(sized) < 2:
        return None
    big = max(sized, key=lambda e: (e.size or 0))
    eng = next((e for e in members if (e.lang or "").lower() == ENGLISH), None)
    if eng is not None and (eng.size is None or eng.size <= 0 or eng.size >= (big.size or 0)):
        return eng
    ties = sorted((e for e in sized if e.size == big.size), key=lambda e: (not e.centre, e.uid))
    return ties[0]


def preference(show_in: str | None, query_lang: str | None,
               english: str = ENGLISH) -> tuple[str, ...]:
    """The languages a work is shown in, first choice first: the Ask page's
    "show results in", then the language the query is written in, then
    English. Duplicates and unknowns dropped."""
    out: list[str] = []
    for code in (show_in, query_lang, english):
        code = (code or "").strip().lower()
        if code and code not in ("original", "und") and code not in out:
            out.append(code)
    return tuple(out)


# --------------------------------------------------------------------------
# which edition a work shows (operator, 2026-10-22, verbatim: "If there are
# more editions: 1. always prefer the unabridged ones (even before criteria
# 2). 2. the newer editions carrying corrections over the older ones of the
# same printing company")

@dataclass(frozen=True)
class EditionFacts:
    """What a record's own metadata says about the edition it is."""
    abridged: bool = False
    #: ebook numbers the origin names as a better edition of this one (PG's
    #: "A somewhat higher quality eBook is available as #49652")
    better_as: tuple[str, ...] = ()
    publisher: str = ""
    year: int | None = None


#: an abridgement, by the edition's own words — never "unabridged"
_ABRIDGED = re.compile(r"(?<!un)(?<!un-)\b(?:abridged|abridg(?:e)?ment|condensed|retold|adapted\s+for\s+"
                       r"(?:young|children|schools)|gek(?:ü|ue)rzt|abr(?:é|e)g(?:é|e)|ridott[oa]|resumid[oa])\b",
                       re.IGNORECASE)
_BETTER = re.compile(r"higher\s+quality\s+e-?book\s+is\s+available\s+as\s+#\s*(\d+)", re.IGNORECASE)
_YEAR = re.compile(r"\b(1[4-9]\d\d|20\d\d)\b")


def edition_facts(record: dict[str, Any]) -> EditionFacts:
    """From the record: its title and the origin's summary (marc520) for an
    abridgement; its description for a better edition the origin names;
    marc260 for the printing company and the physical edition's year, else
    the record's `date`."""
    extra = record.get("extra") or {}
    marc = extra.get("marc") or {}
    words = " ".join([str(record.get("title") or ""), *(str(x) for x in marc.get("520") or [])])
    imprint = " ".join(str(x) for x in marc.get("260") or [])
    publisher = ""
    if ":" in imprint:
        publisher = re.split(r"[,;]|\b\d{4}\b", imprint.split(":", 1)[1])[0]
    publisher = re.sub(r"[^\w ]", "", publisher.lower()).strip()
    year = _YEAR.search(imprint) or _YEAR.search(str(record.get("date") or ""))
    return EditionFacts(abridged=bool(_ABRIDGED.search(words)),
                        better_as=tuple(_BETTER.findall(str(extra.get("description") or ""))),
                        publisher=publisher, year=int(year.group(1)) if year else None)


def edition_order(candidates: Sequence[tuple[int, str, str | None]], facts: dict[str, EditionFacts],
                  pref_index) -> list[int]:
    """The candidates (rank index, uid, language) best first: an unabridged
    edition before an abridged one, ALWAYS — before the reader's language
    too, which comes second; then an edition the origin names a better one
    of loses to it, and of one printing company the newer edition wins (its
    corrections); then the best-ranked. Facts unknown (a record not held
    here) count as unabridged and uncorrected."""
    nums = {u: u.split(":", 1)[-1] for _, u, _ in candidates}
    found = set(nums.values())

    def worse_than_another(u: str) -> int:
        f = facts.get(u) or EditionFacts()
        if any(n in found for n in f.better_as):
            return 1
        if f.publisher and f.year is not None:
            for _, v, _ in candidates:
                g = facts.get(v)
                if v != u and g and g.publisher == f.publisher and g.year is not None and g.year > f.year:
                    return 1
        return 0
    return sorted(range(len(candidates)),
                  key=lambda k: ((facts.get(candidates[k][1]) or EditionFacts()).abridged,
                                 pref_index(candidates[k][2]), worse_than_another(candidates[k][1]),
                                 candidates[k][0]))


def _uid(hit: dict[str, Any]) -> str:
    from app.retrieval import record_of
    return record_of(hit)


#: the fields a passage of the edition shown brings to the hit it heads; the
#: rest — its place, its scores, the reranker's verdict — stays the hit's
_PASSAGE = ("chunk_id", "book_id", "text", "chapter", "lang", "source")


def collapse(hits: Sequence[dict[str, Any]], work_of: dict[str, str],
             members: dict[str, Sequence[Edition]], prefer: Sequence[str],
             facts: dict[str, EditionFacts] | None = None,
             passages=None, limit: int | None = None) -> list[dict[str, Any]]:
    """`hits` in ranked order, one per record; `work_of` maps a record's uid
    to its work's key, `members` a work's key to every edition of it known.
    Each work keeps ONE hit, at the position of its best-ranked edition:
    the first of the work's hits by `edition_order` — unabridged first, then
    the first language of `prefer`, then the corrected edition, then the
    best-ranked (`facts`, per uid, what its record says; with none, the
    language and the rank decide, as before). That hit carries `editions`, every
    other edition of the work — `{uid, title, lang, held, rank, chunk_id}`,
    `rank` and `chunk_id` where the edition was among the hits — those in
    the preferred languages first. A hit of no work is untouched.

    WHERE THE CLAIMS STATE THE LENGTHS (`largest`), the work is shown as its
    largest edition instead, at the same place with the same scores: that
    edition's passage where it was among the hits, else the passage of it
    `passages` finds for the question (one batch, for the works among the
    first `limit` shown — the rest are cut). The hit says so (`shown_as`,
    invariant 4(2)); with no passage found, the rule above."""
    by_work: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for i, h in enumerate(hits):
        w = work_of.get(_uid(h))
        if w is not None:
            by_work.setdefault(w, []).append((i, h))
    if not by_work:
        return list(hits)
    lang_of = {e.uid: e.lang for es in members.values() for e in es}

    def lang(h: dict[str, Any]) -> str:
        return (h.get("lang") or lang_of.get(_uid(h)) or "").lower()

    def pref_index(code: str | None) -> int:
        code = (code or "").lower()
        return prefer.index(code) if code in prefer else len(prefer)

    # pass 1: each work's place, and the largest editions with no passage yet
    slots: list[tuple[dict[str, Any], str | None, Edition | None]] = []
    wanted: list[Edition] = []
    for i, h in enumerate(hits):
        w = work_of.get(_uid(h))
        if w is None:
            slots.append((h, None, None))
            continue
        found = by_work[w]
        if found[0][0] != i:
            continue                       # a later edition of a work already shown
        shown = largest(members.get(w, ()))
        if (shown is not None and shown.uid not in {_uid(x) for _j, x in found}
                and (limit is None or len(slots) < limit)):
            wanted.append(shown)
        slots.append((h, w, shown))
    got = _safe(passages, wanted) if wanted and passages is not None else {}

    # pass 2: the heads
    out: list[dict[str, Any]] = []
    for h, w, shown in slots:
        if w is None:
            out.append(h)
            continue
        found = by_work[w]
        matched = {_uid(x): x for _j, x in found}
        p = None
        if shown is not None:
            p = matched.get(shown.uid) or got.get(shown.uid)
        if p is not None:
            head = dict(h)
            head.update({k: p[k] for k in _PASSAGE if k in p})
            head["shown_as"] = {"edition": shown.uid, "rule": SHOWN_RULE}
            if shown.uid not in matched:
                head["shown_as"]["passage"] = "its best match for the question"
        else:
            order = edition_order([(j, _uid(x), lang(x)) for j, x in found], facts or {}, pref_index)
            head = dict(found[order[0]][1])
        rank_of = {_uid(x): (j + 1, x.get("chunk_id")) for j, x in found}
        editions = []
        for e in members.get(w, ()):
            if e.uid == _uid(head):
                continue
            rank, chunk = rank_of.get(e.uid, (None, None))
            editions.append({"uid": e.uid, "title": e.title, "lang": e.lang, "held": e.held,
                             "rank": rank, "chunk_id": chunk})
        editions.sort(key=lambda e: (pref_index(e["lang"]),
                                     e["rank"] if e["rank"] is not None else float("inf"), e["uid"]))
        head["editions"] = editions
        out.append(head)
    return out


SHOWN_RULE = "the largest edition, English on a tie"


def _safe(passages, editions: list[Edition]) -> dict[str, dict[str, Any]]:
    try:
        return passages(editions) or {}
    except Exception:   # noqa: BLE001 — the editions that matched stay shown
        return {}


# --------------------------------------------------------------------------
# the work groups, from the claims held here

def _usable(collection, annotations: Iterable | None) -> list:
    """The annotations collections held here, usable, about `collection`."""
    codex = getattr(collection, "codex", None)
    if codex is None:
        return []
    from app.ask import annotations as facts
    held = facts._held(annotations)
    ids = _ids_of(collection)
    return [a for a in held if facts.target_among(a.codex, ids) is not None
            and facts._withheld(a) is None]


def _ids_of(collection) -> tuple[str, ...]:
    """`collection`'s id and its records twin's (`annotations.answers_for`):
    its state directory is its manifest's."""
    from app.hold import carry
    path = getattr(collection, "manifest_path", None)
    twin = carry.records_twin_of(Path(path).parent) if path else None
    return tuple(dict.fromkeys(i for i in (collection.codex.collection_id, twin) if i))


def work_relations(collection) -> tuple[str, ...]:
    """The relation types `collection`'s plugin says make two of its records
    one work (`WORK_RELATIONS`, the plugin's own: 09 §9.14 has the plugin
    register its types); () for a plugin that names none."""
    from app.collections import PLUGIN_REGISTRY
    plugin = PLUGIN_REGISTRY.get(getattr(collection, "plugin", None) or "")
    return tuple(getattr(plugin, "WORK_RELATIONS", ()) or ())


class _Edges:
    """The collection's own edges of `types` between records it holds, read
    from the manifest's `relations` table (app/ask/traverse.py), both
    directions, one indexed query per direction per batch of uids. A
    manifest with no `relations` table yet (no snapshot indexed) has none."""

    BATCH = 500

    def __init__(self, collection, types: Sequence[str]):
        from app import db
        self.types = tuple(types)
        self.con = None
        if not self.types:
            return
        con = db.read_only_connect(collection)   # no DDL on a search's path
        if con is None:
            return
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='relations'"
                       ).fetchone() is None:
            con.close()
            return
        self.con = con

    def __call__(self, uids: Sequence[str]) -> list[tuple[str, str]]:
        if self.con is None or not uids:
            return []
        ts = ",".join("?" * len(self.types))
        out: list[tuple[str, str]] = []
        uids = list(dict.fromkeys(uids))
        for i in range(0, len(uids), self.BATCH):
            batch = uids[i:i + self.BATCH]
            us = ",".join("?" * len(batch))
            for sql in (EDGES_OUT.format(us=us, ts=ts), EDGES_IN.format(us=us, ts=ts)):
                out += [(r[0], r[1]) for r in self.con.execute(sql, [*batch, *self.types])]
        return out

    def close(self) -> None:
        if self.con is not None:
            self.con.close()
            self.con = None


#: a step of the walk: the edges out of the step's uids and into them, each
#: joined to the documents at both ends — an edition this peer does not hold
#: joins nothing (as the claims never named one)
EDGES_OUT = ("SELECT r.from_uid, r.to_id FROM relations r"
             " JOIN documents t ON t.uid = r.to_id"
             " WHERE r.from_uid IN ({us}) AND r.type IN ({ts})")
EDGES_IN = ("SELECT r.from_uid, r.to_id FROM relations r"
            " JOIN documents f ON f.uid = r.from_uid"
            " WHERE r.to_id IN ({us}) AND r.type IN ({ts})")


def work_groups(collection, uids: Sequence[str], *, root: Path | None = None,
                annotations: Iterable | None = None,
                ) -> tuple[dict[str, str], dict[str, list[Edition]]]:
    """(work_of, members) for the records `uids` of `collection`, from the
    work claims (`WORK_TYPES`) of the usable annotations collections about
    it whose `from_snapshot` the answer's snapshot has reached, and from its
    own work relations (`work_relations`) between records held here. A work
    is a connected set of records under those claims and edges, keyed by its
    smallest uid; records neither names are in no work."""
    if not uids:
        return {}, {}
    own = work_relations(collection)
    usable = _usable(collection, annotations)
    if not usable and not own:
        return {}, {}
    indexes = _claim_indexes(collection, usable, root) if usable else []
    if not indexes and not own:
        return {}, {}
    edges = _Edges(collection, own)
    try:
        return _walk(collection, uids, indexes, edges)
    finally:
        edges.close()


def _claim_indexes(collection, usable, root: Path | None) -> list:
    """The claim indexes of the `usable` annotations collections whose
    `from_snapshot` the answer's snapshot has reached."""
    from app.hold import annotations as ann
    from app.hold import collection as col
    from app.share import seeder
    from app.ask.results import _root
    root = Path(root) if root is not None else _root()
    try:
        snap = seeder.cached_snapshot(col.shard_dir_of(root, collection.name))
    except Exception:   # noqa: BLE001 — no snapshot named: no claim applies (05 §5.3)
        snap = None
    read = snap.snapshot_id if snap is not None else None
    from app.ask.annotations import target_among
    ids = _ids_of(collection)
    return [ann.index_of(col.shard_dir_of(root, a.name)) for a in usable
            if ann.applies_from(target_among(a.codex, ids).from_snapshot, read)]


def _walk(collection, uids: Sequence[str], indexes: list, edges: "_Edges",
          ) -> tuple[dict[str, str], dict[str, list[Edition]]]:
    """The candidates' works, whole, step by step: each step's new uids
    through every claim index and through the collection's own edges."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def join(x: str, y: str) -> None:
        a, b = find(x), find(y)
        if a != b:
            parent[max(a, b)] = min(a, b)

    seen: set[str] = set()
    sizes: dict[str, int] = {}
    centres: set[str] = set()
    step = list(dict.fromkeys(uids))
    while step:                     # the candidates' works, whole: a star of claims
        step = [u for u in dict.fromkeys(step) if u not in seen]   # reaches every
        seen.update(step)                                          # edition through its centre
        nxt: list[str] = []
        for u in step:
            for idx in indexes:
                for cl in idx.by_target.get(u, ()):
                    c = cl.claim
                    if c["type"] not in WORK_TYPES:
                        continue
                    ev = c.get("evidence") or {}
                    for end, key in ((c["subject"], "subject_tokens"), (c["object"], "object_tokens")):
                        if isinstance(ev.get(key), int):
                            sizes[end] = ev[key]
                    if isinstance(ev.get("object_tokens"), int):
                        centres.add(c["object"])
                    join(c["subject"], c["object"])
                    nxt += [o for o in (c["subject"], c["object"]) if o not in seen]
        for a, b in edges(step):
            join(a, b)
            nxt += [o for o in (a, b) if o not in seen]
        step = nxt
    groups: dict[str, list[str]] = {}
    for u in parent:
        groups.setdefault(find(u), []).append(u)
    groups = {k: v for k, v in groups.items() if len(v) >= 2}
    if not groups:
        return {}, {}
    from app.ask.annotations import _target_rows
    rows = _target_rows(collection, [u for v in groups.values() for u in v])
    work_of = {u: k for k, v in groups.items() for u in v}
    members = {k: [Edition(u, (rows.get(u) or {}).get("title"), (rows.get(u) or {}).get("lang"),
                           u in rows, sizes.get(u), u in centres) for u in sorted(v)]
               for k, v in groups.items()}
    return work_of, members


def best_passage(collection, plan, edition: Edition) -> dict[str, Any] | None:
    """`edition`'s passage that best matches the question: a text search
    inside that one record (the `keys` filter) with the plan's variant for
    its language, else its first passage; None where none is held."""
    from app import fts
    try:
        book_id = int(edition.uid.split(":", 1)[1])
    except (IndexError, ValueError):
        return None
    lang = (edition.lang or "").lower()
    group = fts.group_for(lang) if lang else None
    if group is None:
        return None
    try:
        queries = plan.variants(group, [lang]) if plan is not None else []
    except Exception:   # noqa: BLE001 — the query as typed then
        queries = []
    for q in queries or [getattr(plan, "core", "")]:
        if q:
            got = fts.search_records(group, q, 1, collection=collection, book_ids=[book_id])
            if got:
                return got[0]
    chunks = fts.book_chunks(group, book_id, collection)
    return min(chunks, key=lambda c: str(c.get("chunk_id"))) if chunks else None


#: the passages of the largest editions not among the hits, searched side by
#: side (tantivy releases the GIL; retrieval.FTS_THREADS' reasoning)
PASSAGE_THREADS = 8


def best_passages(collection, plan, editions: Sequence[Edition]) -> dict[str, dict[str, Any]]:
    """`best_passage` of each of `editions`, in parallel; uid → passage."""
    from concurrent.futures import ThreadPoolExecutor
    from app import cancel
    if not editions:
        return {}
    one = lambda e: (e.uid, best_passage(collection, plan, e))   # noqa: E731
    with ThreadPoolExecutor(max_workers=min(len(editions), PASSAGE_THREADS)) as ex:
        return {u: p for u, p in ex.map(cancel.carry(one), editions) if p is not None}


def one_hit_per_work(hits: list[dict[str, Any]], collection, prefer: Sequence[str], *,
                     root: Path | None = None, annotations: Iterable | None = None,
                     plan=None, limit: int | None = None) -> list[dict[str, Any]]:
    """`collapse` with the work groups held here. A grouping that cannot be
    read returns the list as it ranked: a guess about what a book IS never
    costs a reader a hit (and never the search)."""
    if not hits or getattr(collection, "codex", None) is None:
        return hits
    try:
        work_of, members = work_groups(collection, [_uid(h) for h in hits], root=root,
                                       annotations=annotations)
    except Exception:   # noqa: BLE001
        return hits
    if not work_of:
        return hits
    try:
        facts = _facts_of(collection, hits, work_of, root=root)
    except Exception:   # noqa: BLE001 — no facts: the language and the rank decide, as before
        facts = {}
    return collapse(hits, work_of, members, prefer, facts,
                    passages=lambda es: best_passages(collection, plan, es), limit=limit)


def _facts_of(collection, hits: Sequence[dict[str, Any]], work_of: dict[str, str], *,
              root: Path | None = None) -> dict[str, EditionFacts]:
    """`edition_facts` for the hits of a work found more than once — the
    only ones a choice is made between: a dict lookup in the part's facts
    file (`part_facts`) once the part has one, else one record read, as
    the plates of a hit are (app/ask/figures.py), while the file is built
    off the search's thread."""
    from collections import Counter
    from app.ask import figures as figures_mod
    from app.ask.results import _root
    from app.hold import collection as col
    from app.share import seeder
    uids = [_uid(h) for h in hits]
    many = {w for w, n in Counter(work_of[u] for u in uids if u in work_of).items() if n >= 2}
    want = [u for u in dict.fromkeys(uids) if work_of.get(u) in many]
    if not want:
        return {}
    root = Path(root) if root is not None else _root()
    tree = col.shard_dir_of(root, collection.name)
    snap = seeder.cached_snapshot(tree)
    if snap is None:
        return {}
    order = figures_mod._order(collection.codex)
    store = facts_dir(root, collection.name, tree)
    out: dict[str, EditionFacts] = {}
    for u in want:
        if ":" not in u:
            continue
        for shard in snap.lookup_order(u.split(":", 1)[1], order):   # the true part first
            part = part_facts(tree, store, shard)
            if part is None:
                f = _facts_read(tree, shard, u)
            else:
                f = part.get(u)
            if f is not None:
                out[u] = f
                break
    return out


#: the rule `edition_facts` applies; a change to it is a new file name, so a
#: facts file never outlives the rule that wrote it (invariant 3)
FACTS_VERSION = 1


def _decode(v) -> EditionFacts:
    return EditionFacts(bool(v[0]), tuple(v[1]), str(v[2]), v[3])


def _make(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for r in records:
        f = edition_facts(r)
        out[str(r["uid"])] = [f.abridged, list(f.better_as), f.publisher, f.year]
    return out


#: one file per part root (app/ask/part_cache.py): gutenberg's parts are
#: zstd frames, 173 ms of a 229 ms choice was decompressing them (München,
#: 2026-10-22); a lookup once the part has a file
CACHE = PartCache("edition-facts", FACTS_VERSION, decode=_decode)


def facts_dir(root: Path, name: str, tree: Path) -> Path:
    return CACHE.store(tree)


def part_facts(tree: Path, store: Path, shard) -> dict[str, EditionFacts] | None:
    """Every record's `edition_facts` of one part, or None while it has no
    file (then it is being built, off the search's thread)."""
    return CACHE.get(tree, store, shard, _make)


def build_part_facts(tree: Path, store: Path, shard) -> Path | None:
    return CACHE.build(tree, store, shard, _make)


def _facts_read(tree: Path, shard, uid: str) -> EditionFacts | None:
    """One record's facts while its part has no file yet: its three fields
    (`records.read_fields`, by column from a Parquet part), never its text."""
    from app.hold import shard as shard_mod
    from app.pack import records as pack_records
    path = Path(tree) / shard_mod.shard_path(shard.slice, shard.n)
    try:
        return edition_facts(pack_records.read_fields(path, uid, ("title", "date", "extra")))
    except (KeyError, OSError, ValueError):
        return None
