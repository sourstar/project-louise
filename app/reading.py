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


def year_of(fields: tuple[str, ...], facts) -> int | None:
    """The first declared field the record's facts state (app/ask/works.py
    `EditionFacts`: `first_published` the work's, `date` the edition's)."""
    if facts is None:
        return None
    for f in fields:
        v = facts.first_published if f == "first_published" else facts.year if f == "date" else None
        if v:
            return int(v)
    return None


def collection_of(hit: dict[str, Any]) -> str:
    return str(hit.get("source_collection") or str(hit.get("chunk_id", "")).split(":", 1)[0])


def years(hits: list[dict[str, Any]], collection=None) -> dict[int, int]:
    """{index of a hit: its record's year} for the hits whose record states
    one its plugin declares. `collection` where every hit is of one; by each
    hit's own collection otherwise. Never raises: no year is no reading."""
    from . import collections as collections_mod
    from .ask import works
    groups: dict[str, list[int]] = {}
    for i, h in enumerate(hits):
        groups.setdefault(collection.name if collection is not None else collection_of(h), []).append(i)
    out: dict[int, int] = {}
    for name, idx in groups.items():
        try:
            col = collection if collection is not None and collection.name == name else collections_mod.by_name(name)
            fields = year_fields(getattr(col, "plugin", None)) if col is not None else ()
            if not fields:
                continue
            facts = works.facts_for(col, list(dict.fromkeys(works._uid(hits[i]) for i in idx)))
        except Exception:   # noqa: BLE001 — a year is a courtesy, never the search's failure
            continue
        for i in idx:
            y = year_of(fields, facts.get(works._uid(hits[i])))
            if y:
                out[i] = y
    return out


@dataclass
class Lookup:
    """The readings of the words asked, by form; and the version they are of."""
    by_form: dict[str, list[Reading]] = field(default_factory=dict)
    version: str = ""


_versions: dict[tuple, str] = {}


def _version(store, name: str, table: str) -> str:
    row = store.con.execute("SELECT ref_version, synced_at FROM ref_meta WHERE source_id=?", (name,)).fetchone()
    if row is None:
        return ""
    # keyed on the refs.db FILE (physical identity) and the sync: a new sync is a new digest
    path = store.con.execute("PRAGMA database_list").fetchone()[2]
    key = (path, name, row[0], row[1])
    if key not in _versions:
        _versions[key] = f"{name} {row[0]} {table_digest(store.con, table)}"
    return _versions[key]


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
