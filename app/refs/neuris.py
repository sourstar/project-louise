"""German federal law as a REFERENCE SOURCE (06 §6.5; docs/notes/legal-fr-de-eu.md
§3.4): the tables a citation resolves against, the `jurabk` namespace a
neuris record writes its abbreviations under, and the jurisdiction `de` of
the shared `eli` owner (app/refs/eli.py routes `eli:de:…` here).

Three tables, synced from the per-expression JSON acquisition stores beside
each manifestation (the plugin's corpus, mounted under `<ref_root>/neuris`;
app/acquire/neuris_listing.py) — the API's own answer, so the sync opens no
zip and parses no XML:

  * `gesetz`, one row per Work, keyed by its canonical id
    (`eli:de:bund/bgbl-1/2000/s1016`), with the origin's ELI, its name and
    short title, its abbreviations, its Ausfertigung and Verkündung dates
    and its Gazette reference;
  * `fassung`, one row per expression, keyed by the record's uid, with its
    Work, its ELI, its stated validity, its force status and the
    manifestation held — the link `fassung.work → gesetz.key` is what
    `expand` follows, so a citation pins the act and its versions as facts,
    never ranked (invariant 4);
  * `abkuerzung`, one row per (abbreviation, Work): the API's
    `abbreviation` (the official one where it exists, else the
    ris-abbreviation) and its `risAbbreviation`. One abbreviation may name
    two Works (the portal's test data has `RisBWahlGV` twice), so the row's
    key is the pair and a lookup by `abk` reads every Work it names.

THE KEYS (05 §5.2, `key_lookup`): `eli:de:<path>` — the Work's
(`gesetz.key`) or an expression's (`fassung.eli`) — and `jurabk:<abbr>`,
the abbreviation VERBATIM, spaces included (`jurabk:SGB 5`): a canonical id
is the origin's own identifier verbatim and the specification fixes nothing
more, so nothing is normalised here, in the record or in the resolver.

THE RESOLVER (`CitationResolver`): "§ 823 Abs. 1 BGB", "Art. 1 GG", "§ 5
Abs. 2 Satz 1 Nr. 3 StGB", "§§ 1 bis 3 BGB" → the act the abbreviation
names, the provision kept on the entity's label. The abbreviations are the
synced table's (memoised on the reference data's version, as Normattiva's
acts index is), never typed by hand. It runs on EVERY chunk at indexing
(app/entities.py), so it is cheap where there is nothing to find: a text
without "§" and without "Art" costs two substring searches, and the
pattern is tried only where one of them stands (`str.find`, in C).
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path

from ..plugins.base import (ArticleCitation, ArticleNotes,
    Entity,
    RefColumn,
    RefLink,
    RefLookup,
    RefRecordBatch,
    RefSchema,
    RefSyncState,
    RefTable,
)

SOURCE_ID = "neuris-refs"
NAMESPACES = ("jurabk",)
ELI_JURISDICTION = "de"

GESETZ = RefTable(
    name="gesetz",
    columns=(
        RefColumn("key", "TEXT"),            # eli:de:bund/bgbl-1/2000/s1016 — the canonical id
        RefColumn("eli", "TEXT"),            # eli/bund/bgbl-1/2000/s1016 — the origin's own
        RefColumn("name", "TEXT"),           # Amtliche Langüberschrift
        RefColumn("alternate_name", "TEXT"), # Amtliche Kurzüberschrift
        RefColumn("abbreviation", "TEXT"),
        RefColumn("ris_abbreviation", "TEXT"),
        RefColumn("date", "TEXT"),           # legislationDate (Ausfertigung)
        RefColumn("published", "TEXT"),      # datePublished (Verkündung)
        RefColumn("gazette", "TEXT"),        # "BGBl I 2003, 1760"
    ),
    primary_key="key",
    indexes=("eli",),
)

FASSUNG = RefTable(
    name="fassung",
    columns=(
        RefColumn("uid", "TEXT"),            # neuris:<expression native id>
        RefColumn("work", "TEXT"),           # gesetz.key
        RefColumn("eli", "TEXT"),            # eli/bund/…/2023-04-26/10/deu
        RefColumn("valid_from", "TEXT"),     # the stated start (temporalCoverage)
        RefColumn("valid_to", "TEXT"),       # the stated end, where stated
        RefColumn("legal_force", "TEXT"),    # InForce | NotInForce | PartiallyInForce, on the fetch day
        RefColumn("manifestation", "TEXT"),  # <eli>/<generated>.zip, the origin unit
    ),
    primary_key="uid",
    indexes=("eli",),
)

ABKUERZUNG = RefTable(
    name="abkuerzung",
    columns=(
        RefColumn("id", "TEXT"),             # "<abk>\x1f<work>"
        RefColumn("abk", "TEXT"),            # verbatim: "SGB 5", "BGB"
        RefColumn("kind", "TEXT"),           # abbreviation | risAbbreviation
        RefColumn("work", "TEXT"),           # gesetz.key
        RefColumn("name", "TEXT"),           # the act's name, for the fact's label
    ),
    primary_key="id",
    indexes=("abk",),
)

LINKS = (RefLink(name="fassungen", from_table="fassung", from_column="work",
                 to_table="gesetz", to_column="key"),)

_BATCH = 2_000


def _expressions(root: Path) -> dict[str, Path]:
    """`{expression ELI: its latest JSON}` under the corpus root — the
    record's own choice (the latest generation date, plugins/neuris)."""
    from ..plugins import walk
    from ..plugins.neuris.source import split_path
    latest: dict[str, tuple[str, Path]] = {}
    for jpath in walk.files(root / "eli", "*.json"):
        got = split_path(jpath.with_suffix(".zip"), root)
        if got is None:
            continue
        expression, generated = got
        held = latest.get(expression)
        if held is None or generated > held[0]:
            latest[expression] = (generated, jpath)
    return {e: p for e, (_, p) in latest.items()}


class NeurisRefSource:
    source_id = SOURCE_ID
    ref_version = 1
    protocol_version = 1
    namespaces = NAMESPACES
    eli_jurisdiction = ELI_JURISDICTION
    #: a pinned provision's note, in German (app/ask/articles.py)
    article_notes = ArticleNotes("geltende Fassung", "Fassung vom {day}", "letzte geltende Fassung")

    def key_lookup(self, canonical: str) -> tuple[str, str, str] | None:
        """`(table, column, value)` holding a canonical id a record's `keys`
        carry, or a reader names: `jurabk:<abbr>` verbatim; `eli:de:<work>`
        as the Work's key, `eli:de:<expression>` as the expression's ELI."""
        ns, _, native = canonical.partition(":")
        ns = ns.strip().lower()
        if ns == "jurabk":
            return (ABKUERZUNG.name, "abk", native) if native else None
        if ns == "eli":
            from . import eli as eli_refs
            if eli_refs.jurisdiction_of(canonical) != ELI_JURISDICTION:
                return None
            path = eli_refs.path_of(canonical).strip("/")
            if path.count("/") >= 6:                 # bund/agent/year/nat/pit/version/lang
                return FASSUNG.name, "eli", "eli/" + path
            return GESETZ.name, "key", f"eli:{ELI_JURISDICTION}:{path}"
        return None

    def schema(self) -> RefSchema:
        return RefSchema(source_id=self.source_id, tables=(GESETZ, FASSUNG, ABKUERZUNG), links=LINKS)

    def article_citations(self, query: str, refs: RefLookup | None = None,
                          facts=()) -> list[ArticleCitation]:
        return article_citations(query, refs, facts)

    def article_of_label(self, label: str | None) -> str | None:
        return article_of_label(label)

    def input_mtime(self, state: RefSyncState) -> float:
        """The newest stored JSON: a pass that fetched one re-syncs
        (refs/store.py `sync_source`)."""
        from ..plugins import walk
        root = Path(state.ref_root) / "neuris"
        return max((os.stat(p).st_mtime for p in walk.files(root / "eli", "*.json")), default=0.0)

    def sync(self, state: RefSyncState) -> Iterator[RefRecordBatch]:
        from ..plugins.neuris.source import coverage, eli_key, native_id_of, work_of
        root = Path(state.ref_root) / "neuris"
        if not root.is_dir():
            return
        works: dict[str, dict] = {}
        abks: dict[str, dict] = {}
        versions: list[dict] = []
        for expression, jpath in sorted(_expressions(root).items()):
            try:
                api = json.loads(jpath.read_bytes())
                nid = native_id_of(expression)
            except (OSError, ValueError):
                continue                    # unreadable, or outside the id's range: no record either
            if not isinstance(api, dict):
                continue
            ex = api.get("exampleOfWork") if isinstance(api.get("exampleOfWork"), dict) else {}
            part = ex.get("isPartOf") if isinstance(ex.get("isPartOf"), dict) else {}
            work_eli = str(ex.get("legislationIdentifier") or work_of(expression))
            key = eli_key(work_eli)
            if key is None:
                continue
            name = str(api.get("name") or "")
            abk = str(api.get("abbreviation") or "").strip()
            ris_abk = str(api.get("risAbbreviation") or "").strip()
            # the latest expression's word on the Work stands (sorted by ELI:
            # the point in time, then the version)
            works[key] = {"key": key, "eli": work_eli, "name": name,
                          "alternate_name": api.get("alternateName"), "abbreviation": abk,
                          "ris_abbreviation": ris_abk, "date": ex.get("legislationDate"),
                          "published": ex.get("datePublished"), "gazette": part.get("name")}
            for kind, a in (("abbreviation", abk), ("risAbbreviation", ris_abk)):
                if a:
                    abks.setdefault(f"{a}\x1f{key}", {"id": f"{a}\x1f{key}", "abk": a, "kind": kind,
                                                       "work": key, "name": name})
            start, end = coverage(api.get("temporalCoverage"))
            versions.append({"uid": f"neuris:{nid}", "work": key, "eli": expression,
                             "valid_from": start, "valid_to": end,
                             "legal_force": api.get("legislationLegalForce"),
                             "manifestation": jpath.with_suffix(".zip").relative_to(root).as_posix()})
            if len(versions) >= _BATCH:
                yield RefRecordBatch(table=FASSUNG.name, rows=tuple(versions))
                versions = []
        if works:
            yield RefRecordBatch(table=GESETZ.name, rows=tuple(works.values()))
        if abks:
            yield RefRecordBatch(table=ABKUERZUNG.name, rows=tuple(abks.values()))
        if versions:
            yield RefRecordBatch(table=FASSUNG.name, rows=tuple(versions))


# ---- the citation resolver -------------------------------------------------------------

#: a provision's further divisions after its number, as German citations
#: write them: Abs./Absatz, S./Satz, Nr./Nummer, Buchst./lit., Halbs., Alt.,
#: Var., UAbs.; ranges and lists (bis, und, -, –, ff., f.)
_UNIT = (r"(?:Abs\.|Absatz|S\.|Satz|Nr\.|Nummer|Halbs\.|Halbsatz|Alt\.|Alternative|Var\.|"
         r"UAbs\.|Unterabs\.|Unterabsatz)\s*\d+[a-z]?")
_LETTER = r"(?:Buchst\.|Buchstabe|lit\.)\s*[a-z]{1,2}\b"
_RANGE = r"(?:bis|und|-|–|,)\s*\d+[a-z]?"
_TAIL = rf"(?:\s*,?\s*(?:{_UNIT}|{_LETTER}|{_RANGE}|ff\.|f\.))*"
_CITE = re.compile(rf"(?P<mark>§§?|Art\.|Artikel)\s*(?P<num>\d+[a-z]?)\b(?P<tail>{_TAIL})")
#: between the provision and the act: "des", "der", … at most one
_LINK = re.compile(r"\s+(?:(?:des|der|dem|den|im|in)\s+)?")
_PUNCT = ",.;:)]"
#: an abbreviation is at most this many words ("SGB 5", "KakaoV 2003")
MAX_WORDS = 3


def citations(text: str, abbreviations) -> list[dict]:
    """`{abk, provision, at}` for every provision a text cites by an
    abbreviation in `abbreviations` (a container of the synced forms),
    in order. The pattern is tried only where "§" or "Art" stands."""
    if "§" not in text and "Art" not in text:
        return []
    starts: list[int] = []
    for mark in ("§", "Art"):
        i = text.find(mark)
        while i >= 0:
            starts.append(i)
            i = text.find(mark, i + 1)
    out: list[dict] = []
    done = -1
    for s in sorted(set(starts)):
        if s < done:
            continue
        m = _CITE.match(text, s)
        if m is None:
            continue
        link = _LINK.match(text, m.end())
        if link is None:
            continue
        words = text[link.end():link.end() + 120].split()[:MAX_WORDS]
        for n in range(len(words), 0, -1):
            cand = " ".join(words[:n])
            for c in (cand, cand.rstrip(_PUNCT)):
                if c and c in abbreviations:
                    provision = " ".join((m.group("mark") + " " + m.group("num") + m.group("tail")).split())
                    out.append({"abk": c, "provision": provision, "at": s})
                    done = m.end()
                    break
            else:
                continue
            break
    return out


_index_lock = threading.Lock()
_index_cache: tuple | None = None       # (stamp, {abk: [rows…]})


def _stamp(refs: RefLookup):
    return tuple(sorted((r["source_id"], r["ref_version"], r["synced_at"]) for r in refs.rows("ref_meta")))


def abbreviation_index(refs: RefLookup) -> dict[str, list[dict]]:
    """`abkuerzung` by abbreviation, built once per reference-data version."""
    global _index_cache
    try:
        stamp = _stamp(refs)
    except Exception:   # noqa: BLE001 — no ref_meta: correctness over speed
        stamp = None
    with _index_lock:
        cached = _index_cache
    if stamp is not None and cached is not None and cached[0] == stamp:
        return cached[1]
    index: dict[str, list[dict]] = {}
    try:
        rows = refs.rows(ABKUERZUNG.name)
    except Exception:   # noqa: BLE001 — never synced: no abbreviations
        rows = []
    for r in rows:
        index.setdefault(r["abk"], []).append(r)
    if stamp is not None:
        with _index_lock:
            _index_cache = (stamp, index)
    return index


def invalidate_cache() -> None:
    global _index_cache
    with _index_lock:
        _index_cache = None


class CitationResolver:
    """A German citation → the act its abbreviation names (`gesetz` rows),
    as entities whose id is the Work's canonical ELI (`eli:de:…`), the
    provision on the label. `expand` is the composite's link walk."""

    def resolve(self, query: str, refs: RefLookup) -> list[Entity]:
        if "§" not in query and "Art" not in query:
            return []
        index = abbreviation_index(refs)
        if not index:
            return []
        out: list[Entity] = []
        seen: set[str] = set()
        for c in citations(query, index):
            for r in index.get(c["abk"], []):
                if r["work"] in seen:
                    continue
                seen.add(r["work"])
                out.append(Entity(entity_id=r["work"], kind=GESETZ.name, source_id=SOURCE_ID,
                                  label=f"{c['abk']} — {c['provision']}"))
        return out


# ---- the articles a query cites (legal-chunking-retrieval.md R1) ---------------------------

#: a provision's number, as `citations` spells it: "§ 823 Abs. 1" → 823, "Art. 1" → 1
_PROVISION = re.compile(r"(?P<mark>§§?|Art\.|Artikel)\s*(?P<num>\d+[a-z]?)")


def article_citations(query: str, refs: RefLookup | None, facts=()) -> list[ArticleCitation]:
    """Every provision the query cites by a synced abbreviation ("§ 823
    Abs. 1 BGB", "Art. 1 GG"): the Works the abbreviation names (their
    canonical ELI, a `neuris` record's key), then `jurabk:<abk>` (the
    record's other key). A range or a list ("§§ 1 bis 3") cites its first
    provision. Needs the synced `abkuerzung` table: what is an abbreviation
    only it knows."""
    if refs is None or ("§" not in query and "Art" not in query):
        return []
    index = abbreviation_index(refs)
    if not index:
        return []
    out: list[ArticleCitation] = []
    for c in citations(query, index):
        m = _PROVISION.match(c["provision"])
        if m is None:
            continue
        mark = "Art." if m.group("mark") in ("Art.", "Artikel") else "§"
        keys = tuple(dict.fromkeys([r["work"] for r in index.get(c["abk"], [])] + [f"jurabk:{c['abk']}"]))
        out.append(ArticleCitation(keys=keys, article=m.group("num").lower(),
                                   label=f"{mark} {m.group('num')}"))
    return out


#: a section's label: "[partition › ]§ 823 — heading", "Art 1 — heading"
_LABEL = re.compile(r"^\s*(?:§+|Art\.?|Artikel)\s*(?P<num>\d+[a-z]?)\b")


def article_of_label(label: str | None) -> str | None:
    m = _LABEL.match(str(label or "").rsplit(" › ", 1)[-1])
    return m.group("num").lower() if m else None
