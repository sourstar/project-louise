"""THE READER of historical forms: the readings a reference source holds
(app/refs/readings.py), applied to the COPY of a hit's text the reranker
reads and the COPY the chat model reads, when the hit's record states a
year a reading's period holds (operator, 2026-11-18: "This annotation will
be used to replace the ancient expressions before the results are passed to
the reranker and before they are processed by the chat model for the AI
summary ... If there is no publication year, or the year does not apply,
the annotation is not used"; architecture's ruling the same day;
docs/notes/historical-languages.md, "Proposal: a reading annotation").

THE YEAR is the record's own (invariant 11, "A collection holds only the
information contained in its corpus"): each plugin DECLARES, in order, the
record fields that state it (`publication_year_fields`); the first one the
record states is used; a plugin that declares none is never read. gutenberg:
the work's first publication (`first_published`, PG's own marc520 summary)
first, the print year of the edition PG transcribed (`date`) as the
fallback (operator, 2026-11-18: "work's first-publication year is the
default, and print year of the edition Project Gutenberg transcribed is the
fallback"); never PG's release date (`extra.issued`), which no field
declared here reads.

THE READING, per word of the copy (TEI P5's orig/reg, readings.py): a
`replace` pair (an attested modern form, `wikisource-readings`) puts `reg`
in the word's place, its case kept; else a `gloss` (`tlio`) keeps the word
and adds its lemma — `abondava [abbondare]` for the reranker, `abondava
[abbondare: <definition>]` for the chat model (the ruling, point 4). The hit
the page, the PDF and the API show is never touched.

THE VERSION, which a rerank's result depends on, is each source's
`ref_version` and a digest of its synced table (`readings.table_digest`),
computed once per sync and kept by the refs.db path and the sync time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .refs.readings import GLOSS, REPLACE, Reading, table_digest

#: the sources the reader reads, in precedence order (a replacement first)
SOURCES = (("wikisource-readings", "wikisource_reading"), ("tlio", "tlio_reading"))
RERANK, CHAT = "rerank", "chat"
#: the definition the chat copy carries, at most
GLOSS_CHARS = 120
#: a word, as the readings' forms are cut (accents and an elision kept)
_WORD = re.compile(r"[^\W\d_]+['’]?")


def year_fields(plugin: str | None) -> tuple[str, ...]:
    """The record fields the plugin declares to state its publication year."""
    from . import collections as collections_mod
    src = collections_mod.PLUGIN_REGISTRY.get(plugin or "")
    return tuple(getattr(src, "publication_year_fields", ()) or ())


class Year(int):
    """A record's year, and what states it, as the badge and moved_by show it
    ("its first publication (1605)", "its author's dates (1265–1321)")."""
    by: str = ""

    def __new__(cls, value: int, by: str = "") -> "Year":
        y = super().__new__(cls, value)
        y.by = by
        return y


def year_of(fields: tuple[str, ...], facts) -> Year | None:
    """The first declared field the record's facts state (app/ask/works.py
    `EditionFacts`), in the ruled order (architecture, 2026-11-18):
    `first_published` — never for a translation, whose first publication is
    the original's in another language; `lived` — the creators' midpoint,
    the translator's for a translation; `date` — the edition PG transcribed."""
    if facts is None:
        return None
    translated = bool(getattr(facts, "translated", False))
    for f in fields:
        if f == "first_published" and facts.first_published and not translated:
            return Year(facts.first_published, f"its first publication ({facts.first_published})")
        if f == "lived" and getattr(facts, "lived", None):
            who = "translator's" if translated else "author's"
            return Year(facts.lived, f"its {who} dates ({facts.lived_span})")
        if f == "date" and facts.year:
            return Year(facts.year, f"its edition ({facts.year})")
    return None


def collection_of(hit: dict[str, Any]) -> str:
    return str(hit.get("source_collection") or str(hit.get("chunk_id", "")).split(":", 1)[0])


#: THE YEAR INDEX (architecture, 2026-11-18: years_warm 10.9 ms and
#: years_cold 1,326 ms at depth 50 on the node's gutenberg, over the bound):
#: per collection snapshot, {uid: year} of every record stating one, built
#: OFF the query path (`warm`, at the web process's start and for each new
#: snapshot), so a rerank's years are dictionary lookups. Keyed on the SHARD
#: tree (physical identity) and the snapshot object the search reads (kept
#: in the value, so its id is never reused); `complete` once every part is in
#: — then a uid absent has no year, with nothing looked up.
_year_index: dict[tuple[str, int], dict[str, Any]] = {}
_index_lock = __import__("threading").Lock()
#: the collections by name, re-read at most this often (`collections.by_name`
#: reads every collection file: a search must not, per rerank)
_COLLECTIONS_TTL_S = 30.0
_collections: dict[str, tuple[float, Any]] = {}


def _collection(name: str):
    import time
    from . import collections as collections_mod
    now = time.monotonic()
    got = _collections.get(name)
    if got is not None and now - got[0] < _COLLECTIONS_TTL_S:
        return got[1]
    col = collections_mod.by_name(name)
    _collections[name] = (now, col)
    return col


def _snapshot_of(col):
    from .ask.results import _root
    from .hold import collection as col_mod
    from .share import seeder
    tree = col_mod.shard_dir_of(_root(), col.name)
    return tree, seeder.cached_snapshot(tree)


def _index_for(tree, snap) -> dict[str, Any]:
    key = (str(tree), id(snap))
    with _index_lock:
        got = _year_index.get(key)
        if got is None:
            # one snapshot per tree is current: an older one's index goes
            for k in [k for k in _year_index if k[0] == str(tree)]:
                del _year_index[k]
            got = _year_index[key] = {"snap": snap, "years": {}, "complete": False}
    return got


def index_years(col, deadline: float | None = None) -> dict[str, Any] | None:
    """Build the collection's year index (a warm's work, never a query's):
    every part's edition facts — the part's facts file where it has one,
    its records read otherwise — to {uid: year}. Resumable within a
    process: a part done stays done; past `deadline` it stops, incomplete."""
    import time
    from .ask import works
    from .hold import shard as shard_mod
    from .pack import records as pack_records
    fields = year_fields(getattr(col, "plugin", None))
    if not fields:
        return None
    tree, snap = _snapshot_of(col)
    if snap is None:
        return None
    idx = _index_for(tree, snap)
    if idx["complete"]:
        return idx
    store = works.facts_dir(None, col.name, tree)
    done = idx.setdefault("parts", set())
    for s in snap.shards:
        if deadline is not None and time.monotonic() > deadline:
            return idx
        if (s.slice, s.n) in done:
            continue
        facts = works.part_facts(tree, store, s)
        if facts is None:                           # no facts file yet: the part read once, here
            try:
                facts = {r["uid"]: works.edition_facts(r)
                         for r in pack_records.iter_records(Path(tree) / shard_mod.shard_path(s.slice, s.n))}
            except (OSError, ValueError, KeyError):
                continue                            # unreadable now: not done, tried next time
        years = idx["years"]
        for u, f in facts.items():
            y = year_of(fields, f)
            if y:
                years[u] = y
        done.add((s.slice, s.n))
    idx["complete"] = len(done) >= len(snap.shards)
    return idx


def years(hits: list[dict[str, Any]], collection=None) -> dict[int, int]:
    """{index of a hit: its record's year} for the hits whose record states
    one its plugin declares. `collection` where every hit is of one; by each
    hit's own collection otherwise. From the year index (`index_years`);
    the records it does not hold yet are read in ONE `facts_for` for the
    head, and remembered. Never raises: no year is no reading."""
    from .ask import works
    groups: dict[str, list[int]] = {}
    for i, h in enumerate(hits):
        groups.setdefault(collection.name if collection is not None else collection_of(h), []).append(i)
    out: dict[int, int] = {}
    for name, idx in groups.items():
        try:
            col = collection if collection is not None and collection.name == name else _collection(name)
            fields = year_fields(getattr(col, "plugin", None)) if col is not None else ()
            if not fields:
                continue
            tree, snap = _snapshot_of(col)
            index = _index_for(tree, snap) if snap is not None else {"years": {}, "complete": False}
            known = index["years"]
            uids = {i: works._uid(hits[i]) for i in idx}
            missing = [] if index["complete"] else list(dict.fromkeys(u for u in uids.values() if u not in known))
            if missing:
                facts = works.facts_for(col, missing)
                for u in missing:
                    # 0: read, and states no year — remembered too, never asked again
                    known[u] = year_of(fields, facts.get(u)) or Year(0)
        except Exception:   # noqa: BLE001 — a year is a courtesy, never the search's failure
            continue
        for i, u in uids.items():
            y = known.get(u)
            if y:
                out[i] = y
    return out


@dataclass
class Lookup:
    """The readings of the words asked, by form; and the version they are of."""
    by_form: dict[str, list[Reading]] = field(default_factory=dict)
    version: str = ""


_versions: dict[tuple, str] = {}
_versions_lock = __import__("threading").Lock()
#: the warm's bound: past it the digest's query is interrupted (SQLite's
#: progress handler) and the first query computes it itself, once
WARM_BOUND_S = 60.0
#: SQLite virtual-machine steps between two looks at the bound
WARM_CHECK_STEPS = 10_000


def _version(store, name: str, table: str, deadline: float | None = None) -> str:
    """`<source> <ref_version> <digest of its synced table>`, computed once a
    sync — by the warm (`warm`), or by the first query that needs it before
    the warm is done. Keyed on the refs.db FILE (physical identity) and the
    sync: a new sync is a new digest."""
    row = store.con.execute("SELECT ref_version, synced_at FROM ref_meta WHERE source_id=?", (name,)).fetchone()
    if row is None:
        return ""
    path = store.con.execute("PRAGMA database_list").fetchone()[2]
    key = (path, name, row[0], row[1])
    with _versions_lock:
        got = _versions.get(key)
    if got is not None:
        return got
    if deadline is not None:
        import time
        store.con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, WARM_CHECK_STEPS)
    try:
        v = f"{name} {row[0]} {table_digest(store.con, table)}"
    finally:
        if deadline is not None:
            store.con.set_progress_handler(None, 0)
    with _versions_lock:
        _versions[key] = v
    return v


def warm(bound_s: float = WARM_BOUND_S, store=None) -> dict[str, str]:
    """Every synced reading table's version computed, OFF the query path
    (architecture, 2026-11-18): at the process's start and whenever a newer
    sync is seen (app/warmup.py's watcher), each under the bound. What could
    not be computed in time is left to the first query. Never raises."""
    import sqlite3
    import time
    out: dict[str, str] = {}
    try:
        if store is None:
            from . import refs
            store = refs.read_store()
        have = set(store.tables())
        deadline = time.monotonic() + bound_s
        for name, table in SOURCES:
            if table in have:
                try:
                    out[name] = _version(store, name, table, deadline)
                except sqlite3.OperationalError:     # interrupted at the bound
                    pass
    except Exception:   # noqa: BLE001 — a warm never fails anything
        pass
    if store is None:                       # the process's own warm (a test names its store)
        warm_years()
    return out


#: the year indexes' warm bound, apart from the digests'
YEARS_WARM_BOUND_S = 600.0
_years_warming = {"thread": None}


def warm_years(bound_s: float = YEARS_WARM_BOUND_S) -> None:
    """Each held collection whose plugin declares year fields: its year index
    built in ONE background thread (`index_years`), under the bound; a call
    while it runs does nothing. A complete index costs one look."""
    import threading
    import time
    t = _years_warming["thread"]
    if t is not None and t.is_alive():
        return

    def run() -> None:
        try:
            from . import collections as collections_mod
            held, _bad = collections_mod.load_all(skip_unreadable=True)
            deadline = time.monotonic() + bound_s
            for col in held:
                if year_fields(getattr(col, "plugin", None)):
                    index_years(col, deadline)
        except Exception:   # noqa: BLE001 — a warm never fails anything
            pass
    t = threading.Thread(target=run, name="reading-years", daemon=True)
    _years_warming["thread"] = t
    t.start()


def lookup(words: Iterable[str], store=None) -> Lookup:
    """The readings of these words (lower-cased), from the synced tables —
    by key, an indexed SEARCH per chunk of 500 words."""
    if store is None:
        from . import refs
        store = refs.read_store()
    want = sorted({w.lower() for w in words})
    out = Lookup()
    versions = []
    have = set(store.tables())
    for name, table in SOURCES:
        if table not in have:
            continue
        v = _version(store, name, table)
        if not v:
            continue
        versions.append(v)
        for i in range(0, len(want), 500):
            part = want[i:i + 500]
            marks = ",".join("?" for _ in part)
            for r in store.con.execute(
                    f"SELECT orig, reg, kind, lemma, gloss, lang, period_from, period_to, method "
                    f"FROM {table} WHERE orig IN ({marks})", part):
                out.by_form.setdefault(r[0], []).append(
                    Reading(r[0], r[1] or None, r[2], r[3] or None, r[4] or None, r[5], int(r[6]), int(r[7]),
                            r[8], name))
    out.version = "; ".join(versions)
    return out


def _keep_case(old: str, new: str) -> str:
    if old.isupper() and len(old) > 1:
        return new.upper()
    if old[:1].isupper():
        return new[:1].upper() + new[1:]
    return new


def best(readings: list[Reading], year: int, lang: str = "it") -> Reading | None:
    """A replacement before a gloss; within a kind, the source order; only
    readings of the record's language."""
    order = {n: k for k, (n, _t) in enumerate(SOURCES)}
    ok = [r for r in readings if r.applies(year) and r.lang == lang]
    ok.sort(key=lambda r: (0 if r.kind == REPLACE else 1, order.get(r.source, 9)))
    return ok[0] if ok else None


def apply(text: str, year: int, found: Lookup, purpose: str, lang: str = "it") -> tuple[str, list[Reading]]:
    """The copy of `text` read at `year`, and the readings it used."""
    used: list[Reading] = []

    def one(m: re.Match) -> str:
        w = m.group(0)
        r = best(found.by_form.get(w.lower(), []), year, lang)
        if r is None:
            return w
        used.append(r)
        if r.kind == REPLACE and r.reg:
            return _keep_case(w, r.reg)
        if r.kind == GLOSS and r.lemma:
            if purpose == CHAT and r.gloss:
                g = r.gloss if len(r.gloss) <= GLOSS_CHARS else r.gloss[:GLOSS_CHARS].rsplit(" ", 1)[0] + "…"
                return f"{w} [{r.lemma}: {g}]"
            return f"{w} [{r.lemma}]"
        return w
    return _WORD.sub(one, text), used


def words_of(texts: Iterable[str]) -> set[str]:
    return {w.lower() for t in texts for w in _WORD.findall(t or "")}


def label(version: str) -> str:
    """moved_by's weight name: "historical reading (<source>, <version>)"."""
    return f"historical reading ({version})"
