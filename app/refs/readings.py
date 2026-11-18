"""READINGS of historical forms, one table per origin (architecture,
2026-11-18, through München: "a REFERENCE SOURCE (06 §6.5), not an
annotations collection — a glossary is facts about the language's words,
keyed by form"; docs/notes/historical-languages.md, "Proposal: a reading
annotation for historical Italian").

A row is one pair, in the vocabulary of TEI P5's `<choice>`: `orig`, the
form as the old text prints it, and `reg`, its regularised (modern) form
(TEI P5 §3.5.2 "Regularization and Normalization": `<orig>`/`<reg>`). Kind
`replace` carries a `reg`; kind `gloss` has none — only the `lemma` and the
`gloss` (a definition) an origin gives, read beside the form, since a lemma
is a citation form and put in the sentence's place it would break it
(*abondava* → *abbondare*). A pair holds for the print years
`period_from`..`period_to` (inclusive); the reader (app/historical.py)
applies it to a text whose record's own year falls in that span, and to
nothing else.

The primary key is the pair's identity, `orig`, `lang` and the period, so a
re-sync writes the same row again (invariant 2); `orig` is indexed, so the
reader's lookup of a text's words is a SEARCH, never a scan.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..plugins.base import RefColumn, RefTable

REPLACE, GLOSS = "replace", "gloss"

COLUMNS = (
    RefColumn("id", "TEXT"),
    RefColumn("orig", "TEXT"),
    RefColumn("reg", "TEXT"),
    RefColumn("kind", "TEXT"),
    RefColumn("lemma", "TEXT"),
    RefColumn("gloss", "TEXT"),
    RefColumn("lang", "TEXT"),
    RefColumn("period_from", "INTEGER"),
    RefColumn("period_to", "INTEGER"),
    RefColumn("method", "TEXT"),
)


def table(name: str) -> RefTable:
    return RefTable(name=name, columns=COLUMNS, primary_key="id", indexes=("orig",))


def row_id(orig: str, lang: str, period_from: int, period_to: int) -> str:
    return f"{lang}|{orig}|{period_from}|{period_to}"


@dataclass(frozen=True)
class Reading:
    orig: str
    reg: str | None
    kind: str
    lemma: str | None
    gloss: str | None
    lang: str
    period_from: int
    period_to: int
    method: str
    source: str

    def applies(self, year: int) -> bool:
        return self.period_from <= year <= self.period_to


def table_digest(con, name: str) -> str:
    """A digest of a synced table's rows, in key order: with the source's
    `ref_version` it is the reading's version (the ruling: "the version is
    ref_version plus a digest of the synced table"). Computed once per sync
    by the reader (it keys its cache on the sync time), never per query."""
    h = hashlib.sha256()
    for r in con.execute(f"SELECT id, reg, kind, lemma, gloss, period_from, period_to, method "
                         f"FROM {name} ORDER BY id"):
        h.update(repr(tuple(r)).encode("utf-8"))
    return h.hexdigest()[:16]
