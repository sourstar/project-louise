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

import contextvars
from contextlib import contextmanager

#: THE READER RUNS UNDER QUALITY ONLY (operator, 2026-11-18: "Keep the word
#: replacement under quality search only"; architecture: the one flag is the
#: request's Quality/Speed choice). Each path that reranks or answers sets it
#: from its request (`quality`); a path that carries no choice keeps the Ask
#: path's default, Quality. Under Speed: no year, no table lookup, no copy,
#: no moved_by.
_QUALITY: contextvars.ContextVar[bool] = contextvars.ContextVar("louise_reading_quality", default=True)


@contextmanager
def quality(on: bool):
    """The block's reader: on under Quality (`on`), off under Speed."""
    token = _QUALITY.set(bool(on))
    try:
        yield
    finally:
        _QUALITY.reset(token)


def quality_on() -> bool:
    return _QUALITY.get()


def under(on: bool, fn, *args, **kwargs):
    """`fn(*args, **kwargs)` with the reader set: for a call that runs in a
    worker thread, where the caller's context is not the thread's."""
    with quality(on):
        return fn(*args, **kwargs)


#: the sources the reader reads, in precedence order (a replacement first)
SOURCES = (("wikisource-readings", "wikisource_reading"), ("tlio", "tlio_reading"))
RERANK, CHAT = "rerank", "chat"
#: the definition the chat copy carries, at most
GLOSS_CHARS = 120
#: the glosses the chat copy of one text carries, at most, each form at its
#: first occurrence only (the node, 2026-11-18, TLIO: Inferno XXI with 35
#: glosses made the model answer "the sources do not contain it" where the
#: plain text answered; summary agreement ON 2.125 against OFF 2.75)
CHAT_GLOSSES = 5
#: a word, as the readings' forms are cut (accents and an elision kept)
_WORD = re.compile(r"[^\W\d_]+['’]?")


def year_fields(plugin: str | None) -> tuple[str, ...]:
    """The record fields the plugin declares to state its publication year."""
    from . import collections as collections_mod
    src = collections_mod.PLUGIN_REGISTRY.get(plugin or "")
    return tuple(getattr(src, "publication_year_fields", ()) or ())


class Year(int):
    """A record's year — its WORK's — and what states it, as the badge and
    moved_by show it ("its first publication (1605)", "its author's dates
    (1265–1321)"); `translated`: the record's text is a translation, which
    the glossary never reads (architecture, 2026-11-18)."""
    by: str = ""
    translated: bool = False

    def __new__(cls, value: int, by: str = "", translated: bool = False) -> "Year":
        y = super().__new__(cls, value)
        y.by, y.translated = by, translated
        return y


def _mid(span: tuple[int, int]) -> int:
    return (span[0] + span[1]) // 2


def _short(century_text: str) -> str:
    """"written in the early 14th century" -> "early 14th"."""
    return re.sub(r"(?i)\s*centur(?:y|ies)$", "", re.sub(r"(?i)^the\s+", "", century_text.strip()))


def year_of(fields: tuple[str, ...], facts) -> Year | None:
    """The WORK's year the record's facts give (app/ask/works.py
    `EditionFacts`), translations included (architecture, 2026-11-18, on the
    operator's "Publication date (not the first) is totally unreliable, do
    not consider it at all, same for translations"): `first_published` — a
    stated year; `lived` — the AUTHORS' lifetime L with the summary's century
    C, used together: L∩C non-empty, its midpoint; no overlap, C is the
    edition's century, rejected, and L's midpoint; L alone, its midpoint; C
    alone (no dates, an anthology), its midpoint. The translators' dates and
    the edition's year are never used: none, and nothing is applied."""
    if facts is None:
        return None
    translated = bool(getattr(facts, "translated", False))
    # a year read from PG's machine-written summary says so (architecture, 2026-11-18)
    auto = ", by PG's automatic summary" if getattr(facts, "summary_auto", False) else ""
    for f in fields:
        if f == "first_published" and facts.first_published:
            return Year(facts.first_published, f"its first publication ({facts.first_published}){auto}", translated)
        if f == "lived":
            life = getattr(facts, "lived", None)
            cent = getattr(facts, "century", None)
            if life and cent:
                lo, hi = max(life[0], cent[0]), min(life[1], cent[1])
                if lo <= hi:
                    return Year(_mid((lo, hi)), f"its summary's century and its author's dates "
                                                f"({_short(facts.century_text)}, {facts.lived_span}){auto}", translated)
                return Year(_mid(life), f"its author's dates ({facts.lived_span}) (its summary's "
                                        f"'{_short(facts.century_text)} century' is outside them)", translated)
            if life:
                return Year(_mid(life), f"its author's dates ({facts.lived_span})", translated)
            if cent:
                return Year(_mid(cent), f"its summary's century ({_short(facts.century_text)}){auto}", translated)
    return None


CENTURY_ALONE = "its summary's century ("


def share_volumes(entries) -> tuple[dict[str, "Year"], dict[str, int]]:
    """THE VOLUMES OF ONE WORK SHARE ONE YEAR (architecture, 2026-11-18,
    option A narrowed): among the records placed by the summary's century
    ALONE, those of one `volume_group` (the authors' PG agent ids and the
    title without its volume markers) take the MAJORITY century among them,
    a tie the earliest; a record placed by its first publication or by its
    author's dates keeps its own year. `entries`: (uid, facts, Year).
    Returns ({uid: its new Year}, {"groups", "changed", "ties"})."""
    from .ask.works import stated_century  # noqa: F401 — the century spans are the facts'
    groups: dict[str, list[tuple[str, Any, Year]]] = {}
    for uid, f, y in entries:
        g = getattr(f, "volume_group", "") if f is not None else ""
        if g and y is not None and y.by.startswith(CENTURY_ALONE) and getattr(f, "century", None):
            groups.setdefault(g, []).append((uid, f, y))
    out: dict[str, Year] = {}
    stats = {"groups": 0, "changed": 0, "ties": 0}
    for members in groups.values():
        if len(members) < 2:
            continue
        stats["groups"] += 1
        votes: dict[tuple, int] = {}
        text_of: dict[tuple, str] = {}
        for _u, f, _y in members:
            key = tuple(f.century)
            votes[key] = votes.get(key, 0) + 1
            text_of.setdefault(key, f.century_text)
        top = max(votes.values())
        best = sorted(k for k, v in votes.items() if v == top)
        if len(best) > 1:
            stats["ties"] += 1
        span = best[0]                                       # a tie: the earliest
        for uid, f, y in members:
            auto = ", by PG's automatic summary" if getattr(f, "summary_auto", False) else ""
            new = Year(_mid(span), f"its work's volumes ({_short(text_of[span])}, {votes[span]} of "
                                   f"{len(members)} volumes){auto}", y.translated)
            if int(new) != int(y):
                stats["changed"] += 1
            out[uid] = new
    return out, stats


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
        seen = idx.setdefault("facts", {})
        for u, f in facts.items():
            y = year_of(fields, f)
            if y:
                years[u] = y
                if getattr(f, "volume_group", ""):
                    seen[u] = f                              # for the volumes' shared year, below
        done.add((s.slice, s.n))
    idx["complete"] = len(done) >= len(snap.shards)
    if idx["complete"]:
        shared, idx["volumes"] = share_volumes((u, f, idx["years"].get(u)) for u, f in seen.items())
        idx["years"].update(shared)
        idx.pop("facts", None)
    return idx


#: the bound of a query's own batch while the index is building (architecture,
#: 2026-11-18): measured on the node, 1.2 s cold (the index: 110 s the first
#: build after a FACTS_VERSION change, 0.5 s after)
BATCH_BOUND_S = 5.0
_batch_pool = None
_batch_logged: dict[str, float] = {}


def _say(collection: str, msg: str, every_s: float = 60.0) -> None:
    import time
    now = time.monotonic()
    if now - _batch_logged.get(collection + msg[:20], float("-inf")) < every_s:
        return
    _batch_logged[collection + msg[:20]] = now
    print(f"{time.strftime('%H:%M:%S')} [{collection} {msg}", flush=True)


def _facts_bounded(col, uids: list[str]):
    """`works.facts_for` of these records, at most BATCH_BOUND_S: past it,
    None (the read goes on in its thread, its result unused), said at most
    once a minute naming the collection. Each batch read is logged —
    `[<collection> info:reading] years read for N records in M ms (index
    building)` — so the window's queries can be counted on the node."""
    import concurrent.futures as cf
    import time
    from .ask import works
    global _batch_pool
    if _batch_pool is None:
        _batch_pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="reading-batch")
    t0 = time.monotonic()
    fut = _batch_pool.submit(works.facts_for, col, uids)
    try:
        got = fut.result(timeout=BATCH_BOUND_S)
    except cf.TimeoutError:
        _say(col.name, f"warn:reading] years not read within {BATCH_BOUND_S:.0f} s for {len(uids)} records "
                       "(index building): no reading for this query")
        return None
    print(f"{time.strftime('%H:%M:%S')} [{col.name} info:reading] years read for {len(uids)} records in "
          f"{(time.monotonic() - t0) * 1000:.0f} ms (index building)", flush=True)
    return got


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
                facts = _facts_bounded(col, missing)
                if facts is None:
                    continue                        # past the bound: no year, no reading, no moved_by
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
        # a source synced to NO rows (tlio held back by its licence gate) is
        # not a table held: its version would claim a reading nothing gives
        empty = store.con.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is None
        v = "" if empty else f"{name} {row[0]} {table_digest(store.con, table)}"
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
        store = _store(store)
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
#: the index is built in a SPAWNED CHILD PROCESS, its result handed back:
#: built in the web process's own thread, the first build (271 s on the
#: node's gutenberg, 2026-11-18, reading every part's records) held the
#: interpreter lock against the searches beside it (architecture). The
#: thread here only waits on the child. False builds in-thread (tests).
INDEX_IN_CHILD = True


def _index_in_child(name: str, bound_s: float) -> tuple[dict[str, tuple[int, str, bool]], bool] | None:
    """In the child: the collection's year index, as plain values."""
    import time
    from . import collections as collections_mod
    col = collections_mod.by_name(name)
    if col is None:
        return None
    idx = index_years(col, time.monotonic() + bound_s)
    if idx is None:
        return None
    return ({u: (int(y), getattr(y, "by", ""), bool(getattr(y, "translated", False)))
             for u, y in idx["years"].items()}, bool(idx["complete"]))


def _index_off_process(col, bound_s: float) -> None:
    """The collection's year index built in a child process and installed
    here; in this thread where the child cannot run (or INDEX_IN_CHILD is
    off). A complete index costs one look."""
    import time
    tree, snap = _snapshot_of(col)
    if snap is None:
        return
    idx = _index_for(tree, snap)
    if idx["complete"]:
        return
    if not INDEX_IN_CHILD:
        index_years(col, time.monotonic() + bound_s)
        return
    import concurrent.futures as cf
    import multiprocessing
    try:
        with cf.ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as ex:
            got = ex.submit(_index_in_child, col.name, bound_s).result(timeout=bound_s + 60)
    except Exception:   # noqa: BLE001 — a child that cannot run: built here, as before
        index_years(col, time.monotonic() + bound_s)
        return
    if got is None:
        return
    years, complete = got
    idx["years"].update({u: Year(y, by, tr) for u, (y, by, tr) in years.items()})
    idx["complete"] = complete
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
            for col in held:
                if year_fields(getattr(col, "plugin", None)):
                    _index_off_process(col, bound_s)
        except Exception:   # noqa: BLE001 — a warm never fails anything
            pass
    t = threading.Thread(target=run, name="reading-years", daemon=True)
    _years_warming["thread"] = t
    t.start()


#: the readings of words already asked, by the tables' version — most of a
#: rerank's words recur across queries (and most have no reading: cached
#: too). Cleared when the version changes; bounded.
_word_cache: dict[str, list[Reading]] = {}
_word_cache_version = {"v": ""}
WORD_CACHE_MAX = 500_000


#: a MEASUREMENT's own reading tables (tools/measure, --readings-tsv): a
#: scratch refs.db built from a derived file before the node's sync; None in
#: the product, which reads the node's refs.db
STORE_OVERRIDE = None


def _store(store=None):
    if store is not None:
        return store
    if STORE_OVERRIDE is not None:
        return STORE_OVERRIDE
    from . import refs
    return refs.read_store()


def tables_held(store=None) -> str:
    """The version of the reading tables held, "" when none is synced — a
    measurement of the reader refuses to report then (it would compare the
    reader OFF with itself)."""
    return lookup([], store).version


def lookup(words: Iterable[str], store=None) -> Lookup:
    """The readings of these words (lower-cased), from the synced tables —
    by key, an indexed SEARCH per chunk of 500 words not yet cached."""
    store = _store(store)
    want = sorted({w.lower() for w in words})
    out = Lookup()
    have = set(store.tables())
    present = [(name, table) for name, table in SOURCES if table in have]
    versions = [v for v in (_version(store, name, table) for name, table in present) if v]
    out.version = "; ".join(versions)
    if _word_cache_version["v"] != out.version or len(_word_cache) > WORD_CACHE_MAX:
        _word_cache.clear()
        _word_cache_version["v"] = out.version
    ask = [w for w in want if w not in _word_cache]
    got: dict[str, list[Reading]] = {w: [] for w in ask}
    for name, table in present:
        if not _version(store, name, table):
            continue
        for i in range(0, len(ask), 500):
            part = ask[i:i + 500]
            marks = ",".join("?" for _ in part)
            for r in store.con.execute(
                    f"SELECT orig, reg, kind, lemma, gloss, lang, period_from, period_to, method "
                    f"FROM {table} WHERE orig IN ({marks})", part):
                got.setdefault(r[0], []).append(
                    Reading(r[0], r[1] or None, r[2], r[3] or None, r[4] or None, r[5], int(r[6]), int(r[7]),
                            r[8], name))
    _word_cache.update(got)
    for w in want:
        rs = _word_cache.get(w)
        if rs:
            out.by_form[w] = rs
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
    if not ok:
        return None
    # a form listed under SEVERAL lemmas is ambiguous: no gloss rather than a
    # guess (the node: "fuoro", furono, glossed as fuoco "fire")
    if ok[0].kind == GLOSS and len({r.lemma for r in ok if r.kind == GLOSS}) > 1:
        return None
    return ok[0]


def apply(text: str, year: int, found: Lookup, purpose: str, lang: str = "it") -> tuple[str, list[Reading]]:
    """The copy of `text` read at `year`, and the readings it used. A
    TRANSLATION's text is returned as it is (`Year.translated`): its year is
    the work's, and the work's year does not apply to the translator's
    Italian (architecture, 2026-11-18: a 1950 Plato at -388 would fall in
    TLIO's period)."""
    used: list[Reading] = []
    if getattr(year, "translated", False):
        return text, used
    # most texts hold no form with a reading (94 of 100 on the node, 2026-11-18):
    # a set intersection decides that before any word is substituted
    if not found.by_form:
        return text, used
    low = text.lower()
    if not any(f in low for f in found.by_form):            # substring checks, in C: no form here
        return text, used

    glossed: set[str] = set()

    def one(m: re.Match) -> str:
        w = m.group(0)
        r = best(found.by_form.get(w.lower(), []), year, lang)
        if r is None:
            return w
        if r.kind == REPLACE and r.reg:
            used.append(r)
            return _keep_case(w, r.reg)
        if purpose == CHAT and r.kind == GLOSS:
            # each form once, and a few a text: a gloss is a help to read the
            # passage, never more words than the passage (CHAT_GLOSSES)
            if w.lower() in glossed or len(glossed) >= CHAT_GLOSSES:
                return w
            glossed.add(w.lower())
        used.append(r)
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



#: the plain words a node says while its reading tables are not synced
#: (architecture, 2026-11-18, under RULE 2: "a node whose reading tables are
#: not synced reads nothing, correctly, but it must SAY so")
NOT_SYNCED = ("historical reading: no tables synced (wikisource-readings, tlio); "
              "`louise dev refs-sync --only wikisource-readings` supplies them")


def status(plugins=None) -> str | None:
    """`node show`'s and `hold collection show`'s line about the reader: the
    tables held and their versions, or NOT_SYNCED; None where no collection
    the line is for declares year fields (`plugins`: their plugin names;
    None, every collection held). Never raises."""
    try:
        if plugins is None:
            from . import collections as collections_mod
            held, _bad = collections_mod.load_all(skip_unreadable=True)
            plugins = [getattr(c, "plugin", None) for c in held]
        if not any(year_fields(p) for p in plugins):
            return None
        v = tables_held()
        return f"historical reading: {v}" if v else NOT_SYNCED
    except Exception as e:   # noqa: BLE001 — a status line never fails its page
        return f"historical reading: its tables cannot be read ({type(e).__name__})"
