"""The French texts as a REFERENCE SOURCE (06 §6.5; legal-fr-de-eu.md §2.5):
the lookup tables a citation resolves against, and the namespaces — `legi`
(LEGIARTI, LEGITEXT, LEGISCTA), `jorf` (JORFTEXT, the originals' ids a LEGI
text carries as its CID) and `nor` (the NOR) — that let a `legi` record write
its own ids into `keys` (05 §5.2). It declares `eli_jurisdiction = "fr"`
for the `eli` owner (app/refs/eli.py), and holds no `eli:fr:` id: LEGI's
files carry no ELI (JORF, which does, is scope not yet taken), so such a key
is routed here and resolves to nothing, said so (`key_lookup` → None).

Three tables, synced from the plugin's own effective state (app/plugins/legi:
the latest baseline, the increments after it, the deletion lists — its
discovery, so the two can never disagree on what is held), each file's
fields read from its bytes with the patterns its head needs, never a full
parse:
  * `texte` — one row per TEXTE_VERSION file, keyed by its LEGITEXT id: the
    CID, nature, number, NOR, dates, titles, état, autorité, ministère, and
    two columns DERIVED from the title (`sigle`, `mots`), never typed by
    hand — what a code's abbreviation is matched against;
  * `article` — one row per article version, keyed by its LEGIARTI id: the
    text's CID, `num` as the origin writes it and `num_norm` (`norm_num`),
    the dates, the état, the Work key, the record's uid and its origin;
  * `section` — one row per SECTION_TA file.
The link `article.cid → texte.cid` is what `expand` follows: a cited text
pins its articles as facts, never ranked (invariant 4).

THE RESOLVER (`CitationResolver`) reads, in any case and with or without
accents (§2.5):
  * code articles — `article L. 121-1 du code de la consommation`,
    `art. L121-1 C. consom.`, `C. civ., art. 1240`, `art. 1240 C. civ.`,
    `art. 39 octies A du CGI`; prefixes L., R., D., A., LO, R*, D*; `1er`,
    bis, ter …; ranges `L. 121-1 à L. 121-7` (every number between two
    whose stems agree, at most 50); sub-parts (I, 2°, a), alinéa) read and
    kept on the label;
  * the code named by its title (`du code de la consommation`), by its
    initials (`CGI`, `CSP`, `CESEDA`, `LPF`: the first letter of the title
    and of each word that is not a particle), or abbreviated (`C. civ.`,
    `C. pr. civ.`: each abbreviation a prefix of the title's words in order,
    as many as there are) — all from the codes' own titles in `texte`. A
    name two codes share resolves to the one in force, else to none;
  * acts — `loi n° 78-17 du 6 janvier 1978`, `loi n°2016-1321`, `loi du 29
    juillet 1881`, `loi organique n° …`, `décret n° 2019-1 du 3 janvier
    2019`, `ordonnance n° 2016-131`, `décret-loi`, `arrêté du 21 juin 2016`
    (by nature and number, and its date where given; by nature and date
    alone only when one text answers), and an article OF an act (`article 5
    de la loi n° 78-17`).
A cited article resolves to every version the table holds of it, as facts
(the date asked chooses among them), never ranked.

IT RUNS ON EVERY CHUNK during indexing (app/entities.py), whatever the
collection, so it costs nothing where it cannot match: no `texte` row (a
node without LEGI) returns at once; then every match holds a DIGIT RUN, and
the patterns are tried only from a trigger word (art., loi, décret,
ordonnance, arrêté) found in the 48 characters before one — normattiva's
digit-anchored scan (app/refs/normattiva.py), so prose without digits costs
one C-level search.
"""
from __future__ import annotations

import html
import re
import threading
import unicodedata
from pathlib import Path
from typing import Iterator

from ..plugins.base import (ArticleCitation, ArticleNotes, Entity, RefColumn, RefLink, RefLookup, RefRecordBatch, RefSchema,
                            RefSyncState, RefTable)

SOURCE_ID = "legi-refs"
NAMESPACES = ("legi", "jorf", "nor")
ELI_JURISDICTION = "fr"

TEXTE = RefTable(
    name="texte",
    columns=(
        RefColumn("id", "TEXT"),            # LEGITEXT…, the TEXTE_VERSION file's id
        RefColumn("cid", "TEXT"),           # the text's CID: its LEGITEXT for a code, its JORFTEXT for an act
        RefColumn("nature", "TEXT"),        # CODE, LOI, LOI_ORGANIQUE, DECRET, ORDONNANCE, ARRETE, …
        RefColumn("num", "TEXT"),           # 78-17
        RefColumn("nor", "TEXT"),
        RefColumn("date_texte", "TEXT"),
        RefColumn("date_publi", "TEXT"),
        RefColumn("titre", "TEXT"),
        RefColumn("titrefull", "TEXT"),
        RefColumn("etat", "TEXT"),
        RefColumn("date_debut", "TEXT"),
        RefColumn("date_fin", "TEXT"),
        RefColumn("autorite", "TEXT"),
        RefColumn("ministere", "TEXT"),
        RefColumn("sigle", "TEXT"),         # derived from the title: CGI, CSP, LPF
        RefColumn("mots", "TEXT"),          # derived: the title's words, folded, particles out
        RefColumn("uid", "TEXT"),
        RefColumn("member", "TEXT"),        # <tarball>#<member>
    ),
    primary_key="id",
    indexes=("cid", "nor"),
)

ARTICLE = RefTable(
    name="article",
    columns=(
        RefColumn("id", "TEXT"),            # LEGIARTI…
        RefColumn("cid", "TEXT"),
        RefColumn("num", "TEXT"),           # as the origin writes it: L121-1
        RefColumn("num_norm", "TEXT"),      # norm_num: l121-1
        RefColumn("etat", "TEXT"),
        RefColumn("date_debut", "TEXT"),
        RefColumn("date_fin", "TEXT"),
        RefColumn("work", "TEXT"),          # the smallest LEGIARTI of its chain
        RefColumn("uid", "TEXT"),
        RefColumn("member", "TEXT"),
    ),
    primary_key="id",
    indexes=("num_norm",),
)

SECTION = RefTable(
    name="section",
    columns=(
        RefColumn("id", "TEXT"),            # LEGISCTA…
        RefColumn("cid", "TEXT"),
        RefColumn("titre", "TEXT"),
        RefColumn("uid", "TEXT"),
        RefColumn("member", "TEXT"),
    ),
    primary_key="id",
)

LINK = RefLink(name="articles", from_table="article", from_column="cid",
               to_table="texte", to_column="cid")

_BATCH = 2_000


def fold(text: str) -> str:
    """Lower case, accents off, apostrophes and dashes as spaces."""
    s = unicodedata.normalize("NFKD", str(text or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[’'`\-–]", " ", s)


#: the words a title's initials skip: articles, prepositions, conjunctions
PARTICLES = frozenset(("de", "du", "des", "la", "le", "les", "l", "d", "et", "a", "au", "aux",
                       "en", "entre", "pour", "sur", "dans", "par"))


def title_words(title: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(title))


def sigle(title: str) -> str:
    """`Code général des impôts` → `CGI`; `Livre des procédures fiscales` →
    `LPF`: the first word's initial, then each word's that is not a particle."""
    words = title_words(title)
    if not words:
        return ""
    return (words[0][0] + "".join(w[0] for w in words[1:] if w not in PARTICLES)).upper()


def mots(title: str) -> str:
    """The title's words after the first (`code`, `livre`), particles out."""
    return " ".join(w for w in title_words(title)[1:] if w not in PARTICLES)


def norm_num(num: str) -> str:
    """`L. 121-1` and LEGI's `L121-1` → `l121-1`; `1er` → `1`;
    `39 octies A` → `39octiesa`: one form for both sides."""
    s = str(num or "").lower().replace("–", "-")
    s = re.sub(r"(\d)\s*er\b", r"\1", s)
    s = re.sub(r"(\d)\s*(?:premier)\b", r"\1", s)
    return re.sub(r"[\s.]", "", s)


# ---- the sync ------------------------------------------------------------------------------

def _one(pattern: bytes, data: bytes) -> str:
    m = re.search(pattern, data, re.S)
    return html.unescape(m.group(1).decode("utf-8", "replace")).strip() if m else ""


def _attr(tag_bytes: bytes, name: bytes) -> str:
    m = re.search(rb"\b" + name + rb'="([^"]*)"', tag_bytes)
    return html.unescape(m.group(1).decode("utf-8", "replace")).strip() if m else ""


def _texte_tag(data: bytes) -> bytes:
    m = re.search(rb"<CONTEXTE[\s>].*?<TEXTE\s[^>]*>", data, re.S)
    return m.group(0) if m else b""


def row_of(kind: str, file_id: str, data: bytes, uid: str, member: str) -> tuple[str, dict] | None:
    """The table and row one file gives, from its bytes."""
    if kind == "article":
        texte = _texte_tag(data)
        from ..plugins.legi.source import work_key
        chain = re.findall(rb'<LIEN_ART\b[^>]*?\bid="(LEGIARTI\d+)"', data)
        num = _one(rb"<META_ARTICLE>.*?<NUM>(.*?)</NUM>", data)
        return ARTICLE.name, {
            "id": file_id, "cid": _attr(texte, b"cid"), "num": num, "num_norm": norm_num(num),
            "etat": _one(rb"<META_ARTICLE>.*?<ETAT>(.*?)</ETAT>", data),
            "date_debut": _one(rb"<META_ARTICLE>.*?<DATE_DEBUT>(.*?)</DATE_DEBUT>", data),
            "date_fin": _one(rb"<META_ARTICLE>.*?<DATE_FIN>(.*?)</DATE_FIN>", data),
            "work": work_key(file_id, [c.decode() for c in chain]), "uid": uid, "member": member}
    if kind == "texte_version":
        titre = _one(rb"<TITRE>(.*?)</TITRE>", data)
        return TEXTE.name, {
            "id": file_id, "cid": _one(rb"<CID>(.*?)</CID>", data),
            "nature": _one(rb"<META_COMMUN>.*?<NATURE>(.*?)</NATURE>", data),
            "num": _one(rb"<META_TEXTE_CHRONICLE>.*?<NUM>(.*?)</NUM>", data),
            "nor": _one(rb"<META_TEXTE_CHRONICLE>.*?<NOR>(.*?)</NOR>", data),
            "date_texte": _one(rb"<DATE_TEXTE>(.*?)</DATE_TEXTE>", data),
            "date_publi": _one(rb"<DATE_PUBLI>(.*?)</DATE_PUBLI>", data),
            "titre": titre, "titrefull": _one(rb"<TITREFULL>(.*?)</TITREFULL>", data),
            "etat": _one(rb"<META_TEXTE_VERSION>.*?<ETAT>(.*?)</ETAT>", data),
            "date_debut": _one(rb"<META_TEXTE_VERSION>.*?<DATE_DEBUT>(.*?)</DATE_DEBUT>", data),
            "date_fin": _one(rb"<META_TEXTE_VERSION>.*?<DATE_FIN>(.*?)</DATE_FIN>", data),
            "autorite": _one(rb"<AUTORITE>(.*?)</AUTORITE>", data),
            "ministere": _one(rb"<MINISTERE>(.*?)</MINISTERE>", data),
            "sigle": sigle(titre), "mots": mots(titre), "uid": uid, "member": member}
    if kind == "section_ta":
        return SECTION.name, {"id": file_id, "cid": _attr(_texte_tag(data), b"cid"),
                              "titre": _one(rb"<TITRE_TA>(.*?)</TITRE_TA>", data),
                              "uid": uid, "member": member}
    return None


class LegiRefSource:
    source_id = SOURCE_ID
    ref_version = 1
    namespaces = NAMESPACES
    eli_jurisdiction = ELI_JURISDICTION
    #: a pinned article's note, in French (app/ask/articles.py)
    article_notes = ArticleNotes("texte en vigueur", "texte au {day}", "dernier texte en vigueur")

    def key_lookup(self, canonical: str) -> tuple[str, str, str] | None:
        """`(table, column, value)` for a key a `legi` record writes: its
        LEGIARTI / LEGITEXT / LEGISCTA id (`legi:`), its text's JORFTEXT CID
        (`jorf:`), its text's NOR (`nor:`). None for any other id — an
        `eli:fr:` key among them: no LEGI file carries an ELI."""
        ns, _, value = str(canonical).partition(":")
        ns, value = ns.strip().lower(), value.strip()
        if ns == "legi":
            if value.startswith("LEGIARTI"):
                return ARTICLE.name, "id", value
            if value.startswith("LEGISCTA"):
                return SECTION.name, "id", value
            if value.startswith("LEGITEXT"):
                return TEXTE.name, "id", value
            return None
        if ns == "jorf" and value.startswith("JORFTEXT"):
            return TEXTE.name, "cid", value
        if ns == "nor" and value:
            return TEXTE.name, "nor", value
        return None

    def schema(self) -> RefSchema:
        return RefSchema(source_id=self.source_id, tables=(TEXTE, ARTICLE, SECTION), links=(LINK,))

    def article_citations(self, query: str, refs: RefLookup | None = None,
                          facts=()) -> list[ArticleCitation]:
        return article_citations(query, refs, facts)

    def article_of_label(self, label: str | None) -> str | None:
        return article_of_label(label)

    @staticmethod
    def input_mtime(state: RefSyncState) -> float:
        """The newest tarball's mtime under `<ref_root>/legi`: a new
        increment re-syncs (app/refs/store.py)."""
        from ..plugins import walk
        return max((p.stat().st_mtime for root in state.corpus_roots("legi")
                    for p in walk.files(root, "*.tar.gz")), default=0.0)

    def sync(self, state: RefSyncState) -> Iterator[RefRecordBatch]:
        """The plugin's effective state under `<ref_root>/legi` (the plugin's
        corpus, as acquisition lays it out), one row per file it holds."""
        from ..plugins.base import DiscoverState
        from ..plugins.legi import SOURCE
        from ..plugins.legi import source as lsrc
        from ..plugins.legi import xml as lx
        roots = state.corpus_roots("legi")            # by the collections held (base.RefSyncState)
        if not roots:
            return
        batches: dict[str, list[dict]] = {TEXTE.name: [], ARTICLE.name: [], SECTION.name: []}
        for root, ref in ((r, x) for r in roots for x in SOURCE.discover(DiscoverState(corpus_root=r))):
            if ref.error or ref.scope != "document":
                continue
            try:
                raw = SOURCE.fetch(ref)
            except Exception:   # noqa: BLE001 — a member that cannot be read holds no row
                continue
            tar, member = lsrc.split_locator(ref.locator)
            stem = member.rsplit("/", 1)[-1]
            file_id = stem[:-4] if stem.endswith(".xml") else stem
            kind = lx.index_fields(raw.data or b"")["kind"]
            got = row_of(kind, file_id, raw.data or b"", f"legi:{ref.native_id}",
                         f"{tar.relative_to(root).as_posix() if tar.is_relative_to(root) else tar.name}#{member}")
            if got is None:
                continue
            table, row = got
            batches[table].append(row)
            if len(batches[table]) >= _BATCH:
                yield RefRecordBatch(table=table, rows=tuple(batches[table]))
                batches[table] = []
        for table, rows in batches.items():
            if rows:
                yield RefRecordBatch(table=table, rows=tuple(rows))


# ---- the citation resolver -----------------------------------------------------------------

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre",
     "octobre", "novembre", "decembre"))}
_MONTH_RE = r"janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre"
_SUFFIXES = r"bis|ter|quater|quinquies|sexies|septies|octies|nonies|novies|decies"
#: an article number: an optional prefix (L., R., D., A., LO, R*, D*), digits
#: and dashed groups, `1er`, a Latin suffix, the CGI's capital letter
_NUM = (rf"(?:(?:L\.?\s?O|LO|L|R\*?|D\*?|A)\.?\s?)?\d+(?:\s?er)?(?:\s?[-–]\s?\d+)*"
        rf"(?:\s(?:{_SUFFIXES}))?(?:\s[A-Z](?![\w.]))?")
_SUB = r"(?:\s*,?\s*(?:[IVX]+(?![\w])|\d+\s?°|[a-z]\)|alin[ée]a\s+\d+|al\.\s*\d+))*"
_ARTICLE = re.compile(rf"(?i:art(?:icles?)?\.?)\s*(?P<a>{_NUM})(?:\s*(?:à|au|a)\s+(?P<b>{_NUM}))?(?P<sub>{_SUB})")
_NATURES = (
    (r"loi\s+organique", "LOI_ORGANIQUE"), (r"loi\s+constitutionnelle", "LOI_CONSTIT"),
    (r"d[ée]cret[-\s]loi", "DECRET_LOI"), (r"loi", "LOI"), (r"d[ée]cret", "DECRET"),
    (r"ordonnance", "ORDONNANCE"), (r"arr[êe]t[ée]", "ARRETE"),
)
_NATURE_RE = "|".join(f"(?:{p})" for p, _ in _NATURES)
_ACT_CORE = (rf"(?P<nature>{_NATURE_RE})(?:\s+n\s?[°o]\.?\s*|\s*n°\s*|\s+no\.?\s+|\s+num[ée]ro\s+|\s+)"
             rf"(?P<num>\d{{2,4}}-\d{{1,5}})?(?:\s*,?\s*du\s+(?P<day>1er|\d{{1,2}})\s+(?P<month>{_MONTH_RE})"
             rf"\s+(?P<year>\d{{4}}))?")
_ACT = re.compile(_ACT_CORE, re.IGNORECASE)
_ACT_AFTER = re.compile(rf"\s*,?\s*(?:de\s+la\s+|du\s+|de\s+l['’]\s*|de\s+|des\s+)?{_ACT_CORE}", re.IGNORECASE)
_TRIGGER = re.compile(r"(?<![\w])(?:art(?:icles?)?\.?|loi|d[ée]crets?|ordonnances?|arr[êe]t[ée]s?)(?![\w])",
                      re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
#: how far before its first digit a citation's trigger can stand
_BACK = 48
#: a code abbreviated: `C. civ.`, `C. pr. civ.`, `C. consom.`, `Code civ.`
_ABBR = re.compile(r"(?:C|Code)\.?\s+((?:[a-zà-ÿ]{2,}\.\s*){1,4})")
_SIGLE = re.compile(r"\b([A-Z]{2,7})\b")
_LEAD = re.compile(r"^\s*,?\s*(?:du\s+|de\s+la\s+|de\s+l['’]\s*|des\s+|de\s+)?", re.IGNORECASE)


def _date(day: str, month: str, year: str) -> str:
    d = 1 if day.lower().startswith("1er") else int(day)
    return f"{int(year):04d}-{_MONTHS[fold(month).strip()]:02d}-{d:02d}"


def _nature(text: str) -> str | None:
    for pattern, nature in _NATURES:
        if re.fullmatch(pattern, text.strip(), re.IGNORECASE):
            return nature
    return None


def _act_of(m: re.Match) -> dict | None:
    nature = _nature(m.group("nature"))
    num, day = m.group("num"), m.group("day")
    if nature is None or not (num or day):
        return None
    return {"nature": nature, "num": num,
            "date": _date(day, m.group("month"), m.group("year")) if day else None}


def citations(text: str) -> list[dict]:
    """What a text cites, in order: `{kind: "article", a, b, sub, start,
    end}` (the code or act after or before it is the caller's to find) or
    `{kind: "act", nature, num, date, start, end}`."""
    out: list[dict] = []
    pos = 0
    for d in _DIGITS.finditer(text):
        if d.start() < pos:
            continue
        lo = max(pos, d.start() - _BACK)
        for t in _TRIGGER.finditer(text, lo, d.start()):
            word = t.group(0).lower()
            if word.startswith("art"):
                m = _ARTICLE.match(text, t.start())
                if m is not None and m.start("a") <= d.start() < m.end():
                    out.append({"kind": "article", "a": m.group("a"), "b": m.group("b"),
                                "sub": " ".join(m.group("sub").split()), "start": m.start(), "end": m.end()})
                    pos = m.end()
                    break
            else:
                m = _ACT.match(text, t.start())
                if m is not None and m.end() > d.start():
                    act = _act_of(m)
                    if act is not None:
                        out.append({"kind": "act", **act, "start": m.start(), "end": m.end()})
                        pos = m.end()
                        break
    return out


_index_lock = threading.Lock()
_index_cache: tuple | None = None


def _stamp(refs: RefLookup):
    return tuple(sorted((r["source_id"], r["ref_version"], r["synced_at"]) for r in refs.rows("ref_meta")))


def _index(refs: RefLookup) -> dict:
    """The texts, keyed for a citation: the codes (title words, initials,
    the état), the acts by (nature, number) and (nature, date) — built
    once per reference-data version, as the gene resolver's is."""
    global _index_cache
    try:
        stamp = _stamp(refs)
    except Exception:   # noqa: BLE001 — no ref_meta: correctness over speed
        stamp = None
    with _index_lock:
        cached = _index_cache
    if stamp is not None and cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        rows = refs.rows(TEXTE.name)
    except Exception:   # noqa: BLE001 — never synced: no texts
        rows = []
    codes, by_num, by_date = [], {}, {}
    for r in rows:
        if r["nature"] == "CODE":
            codes.append(r)
        if r["num"]:
            by_num.setdefault((r["nature"], r["num"]), []).append(r)
        if r["date_texte"]:
            by_date.setdefault((r["nature"], r["date_texte"]), []).append(r)
    index = {"codes": codes, "by_num": by_num, "by_date": by_date,
             "titles": sorted(((fold(r["titre"]).split(), r) for r in codes if r["titre"]),
                              key=lambda t: -len(t[0]))}
    if stamp is not None:
        with _index_lock:
            _index_cache = (stamp, index)
    return index


def invalidate_cache() -> None:
    global _index_cache
    with _index_lock:
        _index_cache = None


def _one_code(rows: list[dict]) -> list[dict]:
    """A name two codes share is the one in force, else none (strict:
    over-resolving pins the wrong law as a fact)."""
    if len({r["cid"] for r in rows}) <= 1:
        return rows[:1]
    live = [r for r in rows if r["etat"] == "VIGUEUR"]
    return live if len({r["cid"] for r in live}) == 1 else []


def code_at(index: dict, text: str) -> tuple[list[dict], int]:
    """The code a text STARTS by naming, and how many characters name it:
    its title, its initials or its abbreviation."""
    lead = _LEAD.match(text)
    start = lead.end() if lead else 0
    rest = text[start:start + 120]
    words = re.findall(r"[a-z0-9]+", fold(rest))
    for title, row in index["titles"]:                        # the longest title first
        if words[:len(title)] == title:
            # the characters the title spans, to the end of its last word
            n, seen = 0, 0
            for m in re.finditer(r"[^\W_]+", rest):
                seen += 1
                n = m.end()
                if seen == len(title):
                    break
            same = [r for t, r in index["titles"] if t == title]
            return _one_code(same), start + n
    m = _ABBR.match(rest)
    if m:
        parts = [p.strip(" .") for p in m.group(1).split(".") if p.strip(" .")]
        parts = [fold(p).strip() for p in parts]
        hits = [r for r in index["codes"] if len(r["mots"].split()) == len(parts)
                and all(w.startswith(p) for w, p in zip(r["mots"].split(), parts))]
        if hits:
            return _one_code(hits), start + m.end()
    m = _SIGLE.match(rest)
    if m:
        hits = [r for r in index["codes"] if r["sigle"] == m.group(1)]
        if hits:
            return _one_code(hits), start + m.end()
    return [], 0


def code_before(index: dict, text: str) -> list[dict]:
    """The code a text ENDS by naming, followed by a comma: `C. civ., art.`"""
    tail = text[-60:]
    for m in re.finditer(r"(?:(?<=\s)|^)(?=(?:C\.|Code|[A-Z]{2,7}\b))", tail):
        rows, n = code_at(index, tail[m.start():])
        if rows and re.fullmatch(r"\s*,\s*", tail[m.start() + n:]):
            return rows
    return []


def _nums(a: str, b: str | None) -> list[str]:
    """The normalised numbers a citation names: one, or every number of a
    range whose two ends share their stem (at most 50), else its two ends."""
    na = norm_num(a)
    if not b:
        return [na]
    nb = norm_num(b)
    ma, mb = re.fullmatch(r"(.*?)(\d+)", na), re.fullmatch(r"(.*?)(\d+)", nb)
    if ma and mb and ma.group(1) == mb.group(1) and 0 <= int(mb.group(2)) - int(ma.group(2)) <= 50:
        return [f"{ma.group(1)}{k}" for k in range(int(ma.group(2)), int(mb.group(2)) + 1)]
    return [na, nb]


def _acts(index: dict, act: dict) -> list[dict]:
    if act["num"]:
        nums = [act["num"]]
        y, _, n = act["num"].partition("-")
        if len(y) == 4 and int(y) < 2000:
            nums.append(f"{y[2:]}-{n}")              # two-digit years until 1999
        rows = [r for k in nums for r in index["by_num"].get((act["nature"], k), [])]
        if act["date"]:
            rows = [r for r in rows if r["date_texte"] == act["date"]] or rows
        return rows
    rows = index["by_date"].get((act["nature"], act["date"]), [])
    return rows if len({r["cid"] for r in rows}) == 1 else []


def _cited(query: str, refs: RefLookup, index: dict) -> Iterator[tuple[dict, dict, list[dict]]]:
    """What a query cites, resolved: `(texte row, citation, [])` for an act
    cited, `(texte row, citation, [article rows])` for an article of a code
    or act — the one walk the resolver and the article pin share."""
    skip_to = 0
    for c in citations(query):
        if c["start"] < skip_to:
            continue
        if c["kind"] == "act":
            for r in _acts(index, c):
                yield r, c, []
            continue
        after = query[c["end"]:c["end"] + 160]
        texts: list[dict] = []
        m = _ACT_AFTER.match(after)
        if m is not None and (act := _act_of(m)) is not None:
            texts = _acts(index, act)       # the act itself is its own citation, pinned too
        if not texts:
            texts, n = code_at(index, after)
            if texts:
                skip_to = c["end"] + n
        if not texts:
            texts = code_before(index, query[max(0, c["start"] - 60):c["start"]])
        for t in texts:
            for num in _nums(c["a"], c["b"]):
                rows = refs.rows(ARTICLE.name, {"cid": t["cid"], "num_norm": num})
                if not rows and re.search(r"[a-z]$", num) and re.search(r"\d[a-z]$", num):
                    # a capital read as the CGI's letter that was the next word
                    rows = refs.rows(ARTICLE.name, {"cid": t["cid"], "num_norm": num[:-1]})
                yield t, c, rows


def _held(refs: RefLookup | None) -> dict | None:
    if refs is None:
        return None
    index = _index(refs)
    if not index["codes"] and not index["by_num"] and not index["by_date"]:
        return None                         # no LEGI here: nothing to resolve against
    return index


def article_citations(query: str, refs: RefLookup | None, facts=()) -> list[ArticleCitation]:
    """Every article the query cites, one citation per Work (the article's
    chain, `article.work`): its versions' `legi:LEGIARTI…` ids as the keys —
    a `legi` record's own key (app/plugins/legi/source.py). Needs the synced
    `texte`/`article` tables: a code is named by its title, initials or
    abbreviation, which only they hold."""
    index = _held(refs)
    if index is None:
        return []
    out: list[ArticleCitation] = []
    for _t, _c, rows in _cited(query, refs, index):
        works: dict[str, list[dict]] = {}
        for r in rows:
            works.setdefault(str(r.get("work") or r["id"]), []).append(r)
        for group in works.values():
            num = group[0]["num"]
            out.append(ArticleCitation(keys=tuple(f"legi:{r['id']}" for r in group),
                                       article=norm_num(num), label=f"article {num}"))
    return out


#: a `legi` article record's one section: "Article L121-1"
_ARTICLE_LABEL = re.compile(r"^\s*Article\s+(?P<num>\S.*?)\s*$")


def article_of_label(label: str | None) -> str | None:
    m = _ARTICLE_LABEL.match(str(label or "").rsplit(" › ", 1)[-1])
    return norm_num(m.group("num")) if m else None


class CitationResolver:
    """Citation text → the texts and article versions named (`texte`,
    `article` rows), entities `legi:<id>`; `expand` follows the declared
    link like any resolver's (app/refs/resolver.py's composite)."""

    def resolve(self, query: str, refs: RefLookup) -> list[Entity]:
        index = _held(refs)
        if index is None:
            return []
        out: list[Entity] = []
        seen: set[str] = set()

        def add(eid: str, kind: str, label: str) -> None:
            if eid not in seen:
                seen.add(eid)
                out.append(Entity(entity_id=eid, kind=kind, source_id=SOURCE_ID, label=label))
        for t, c, rows in _cited(query, refs, index):
            if c["kind"] == "act":
                add(f"legi:{t['id']}", TEXTE.name, t["titre"] or t["id"])
                continue
            for r in rows:
                add(f"legi:{r['id']}", ARTICLE.name,
                    f"{t['titre']} — art. {r['num']} ({r['date_debut']} → {r['date_fin']}, "
                    f"{r['etat']})" + (f" {c['sub']}" if c["sub"] else ""))
        return out
