"""A cited ARTICLE, pinned by key (05 §5.2 facts-by-key; invariant 4: a
pinned block, never a ranked hit). Operator, 2026-10-13: "art. 12 della
legge sull'emigrazione testo unico 13 novembre 1919, n. 2205" found the act
and ranked other acts' articles above its own; the article was never read.

ONE ENGINE, A GRAMMAR PER LEGAL REFERENCE SOURCE (docs/notes/legal-chunking-
retrieval.md R1, operator's default A, 2026-11-08). Every registered
reference source that answers `article_citations(query, refs, facts)`
(app/plugins/base.py `LegalReferenceSource`; absent = no citations) says,
in its own grammar, which articles the query cites — `ArticleCitation`:
the Work's canonical ids and the article — and how its collections' chunk
labels name an article (`article_of_label`): Normattiva ("art. 2043 c.c.",
"art. 5 della legge 241/1990"), LEGI ("article L121-1 du code de la
consommation"), NeuRIS ("§ 823 Abs. 1 BGB"), EUR-Lex ("Article 5 of
Regulation (EU) 2016/679"). Then, for every citation, in every held
collection that declares validity:
1. the Work, by its key in the collection's own key table
   (`validity_works`): the first of the citation's `keys` held — a
   primary-key lookup each — else every one of its `also` held;
2. the version: the one in force on the query's date (`as_of`), else the
   one shown (the last text);
3. the article: that version's chunk whose label names it, read from the
   index stores as the reader reads it (app/reader.py);
4. a REPEALED article — one its source's `article_repealed` says is
   (Normattiva's "ARTICOLO ABROGATO …"; absent = never) — also the last
   version in which it still had its text, with that version's dates: what
   the reader who cites a repealed article is almost always asking for.

ONE LANGUAGE (R1 follow-up b, 2026-11-12). A Work held in several languages
(EUR-Lex: one Work per act per language, all under the act's `celex:` key)
is pinned ONCE: in the query's language (the language plan's `query_lang`)
where held, else the first of the source's `language_preference` held, else
the first in name order — never once per language held. A key whose Works
are all in one language (Normattiva, LEGI, NeuRIS) is pinned as before.

The note a fact's label ends with is the source's words (`article_notes`,
app/plugins/base.py `ArticleNotes`: "testo vigente", "texte en vigueur",
"geltende Fassung", "text in force"); the fact's field KEYS (`testo`,
`in vigore dal`, `fino al`, `versione`, `chunk`, `modificato da`) are the
API answer's (UI, the Open WebUI pipe, MCP) and do not change.

A citation's key that a collection does not hold pins nothing there: the
four key spaces (`urn:`, `legi:`, `eli:de:`/`jurabk:`, `celex:`) never meet.

Bounded: MAX_ARTICLES articles a query. A collection that cannot be read is
skipped, never fatal: the search answers without the pin."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from app.plugins.base import ArticleCitation, ArticleNotes
# Normattiva's grammar, moved to its reference source unchanged; the names
# stay importable here
from app.refs.normattiva import FORMS, label_token, token  # noqa: F401

#: articles pinned per query, whatever the query cites
MAX_ARTICLES = 4
#: characters of one article's text in the answer's context: the chat runs a
#: 16,384-token context and the ranked passages take 24,000 characters
#: (retrieval.ANSWER_SOURCE_CHARS); a code's article runs past 10,000 (p99 of
#: the codes' articles, 2,943 tokens, review §3.5). Cut, and the cut said.
MAX_CHARS = 4_000
_DEFAULT_NOTES = ArticleNotes()


@dataclass(frozen=True)
class Cited:
    """A citation and the source that cited it: its label reader, its
    repealed-article detector, its note's words, its language preference."""
    citation: ArticleCitation
    read: Callable[[str | None], str | None]
    source: Any = None

    @property
    def article(self) -> str:
        return self.citation.article

    def repealed(self, text: str) -> bool:
        return repealed(self.source, text)

    @property
    def notes(self) -> ArticleNotes:
        return getattr(self.source, "article_notes", None) or _DEFAULT_NOTES

    @property
    def languages(self) -> tuple[str, ...]:
        return tuple(getattr(self.source, "language_preference", None) or ())


def repealed(source, text: str) -> bool:
    """The source's own `article_repealed(text)`; absent = never."""
    ask = getattr(source, "article_repealed", None)
    return bool(ask(text)) if ask is not None else False


def citations(query: str, facts: Iterable = (), refs=None) -> list[Cited]:
    """What every legal reference source says the query cites, in registry
    order. A source whose grammar fails cites nothing; the others still do."""
    from app import refs as refs_mod
    facts = list(facts)
    out: list[Cited] = []
    for source in refs_mod.REFS_REGISTRY.values():
        ask = getattr(source, "article_citations", None)
        read = getattr(source, "article_of_label", None)
        if ask is None or read is None:
            continue
        try:
            got = ask(query, refs, facts)
        except Exception:                    # noqa: BLE001 — never fatal to the search
            continue
        out += [Cited(c, read, source) for c in got if c.article]
    return out


def _held(con, key: str) -> bool:
    return con.execute("SELECT 1 FROM validity_works WHERE key=? LIMIT 1", (key,)).fetchone() is not None


def _keys(con, c: ArticleCitation) -> list[str]:
    """The Work's keys this collection holds for one citation: the first of
    `keys` held, else every one of `also` held."""
    first = next((k for k in c.keys if _held(con, k)), None)
    if first is not None:
        return [first]
    return [k for k in c.also if _held(con, k)]


def _versions(con, work: str) -> list[dict]:
    rows = con.execute("SELECT uid, native_id, valid_from, valid_to, has_text, shown FROM validity "
                       "WHERE work=? ORDER BY valid_from", (work,)).fetchall()
    return [dict(r) for r in rows]


def _chosen(versions: list[dict], as_of: int | None) -> dict | None:
    if as_of is None:
        return next((v for v in versions if v["shown"]), versions[-1] if versions else None)
    live = [v for v in versions if v["valid_from"] <= as_of
            and (v["valid_to"] is None or as_of < v["valid_to"])]
    return live[-1] if live else None


def _article(collection, native_id: int, want: Cited | str) -> dict | None:
    """The version's first chunk whose label names the article. `want` a
    bare token reads labels by Normattiva's grammar."""
    from app import reader
    read, tok = (label_token, want) if isinstance(want, str) else (want.read, want.article)
    doc = reader.document(collection, int(native_id))
    for ch in (doc or {}).get("chunks") or []:
        if read(ch.get("chapter")) == tok:
            return ch
    return None


def _one_language(found: list[tuple], query_lang: str | None, prefer: tuple[str, ...]) -> list[tuple]:
    """Of one key's Works (`found`: (work, versions, version, title, lang)),
    those of ONE language when they are in several: the query's, else the
    first of `prefer` held, else the first in name order. A Work with no
    language is kept."""
    langs = sorted({(f[4] or "").lower() for f in found} - {""})
    if len(langs) <= 1:
        return found
    q = (query_lang or "").lower()
    pick = q if q in langs else next((x for x in prefer if x in langs), langs[0])
    return [f for f in found if (f[4] or "").lower() in (pick, "")]


def _day(n) -> str | None:
    from app import validity
    return validity.day_text(n) if n else None


#: amending statements pinned with an article (piece 3): the latest ones
MAX_CHANGES = 12


def _changes(con, work: str, tok: str) -> list[dict]:
    """What changed the article and its act (app/validity.py `amendments_of`:
    the amending actions each version states, piece 3, operator 2026-11-08)
    — by any canonical id of the Work, the latest MAX_CHANGES, facts as the
    origin states them: the amending act, the day it applies from, the kind."""
    from app import validity
    keys = [r[0] for r in con.execute("SELECT key FROM validity_works WHERE work=?", (work,))]
    rows = validity.amendments_of(con, keys, tok)
    return [{"atto": (r["stating_title"] or r["amending"]).split(" — ")[0] if r["amending_is_stating"]
             else r["amending"], "dal": r["day"], "tipo": r["kind"],
             "su": f"art. {r['article']}" if r["article"] else "l'atto"} for r in rows[-MAX_CHANGES:]]


def _fact(collection, key: str, act_label: str, c: ArticleCitation, version: dict, chunk: dict,
          note: str, changes: list[dict] | None = None) -> dict:
    fields: dict[str, Any] = {"testo": _capped(str(chunk.get("text") or "")),
                              "in vigore dal": _day(version["valid_from"]),
                              "fino al": _day(version["valid_to"]) or "oggi",
                              "versione": version["uid"], "chunk": chunk.get("chunk_id")}
    if changes:
        fields["modificato da"] = changes
    return {"entity_id": f"{key}~art{c.article.replace('-', '')}", "kind": "articolo",
            "source_id": collection.name,
            "label": f"{act_label} — {c.label} ({note})",
            "fields": fields, "via": None}


def _capped(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return (text[:MAX_CHARS].rstrip() + f"\n[… testo troncato a {MAX_CHARS} di {len(text)} "
            f"caratteri: il testo intero è nel documento]")


def pin(query: str, collections: Iterable, facts: Iterable = (), *,
        as_of: int | None = None, store=None, query_lang: str | None = None) -> list[dict]:
    """The cited articles' texts as pinned facts (PinnedFact's fields, as
    dicts: the answer's `facts` list holds them so). `store`: the refs store
    the grammars that need reference data read (a code's title, a German
    abbreviation); None = only those that need none cite. `query_lang`: the
    language a Work held in several is pinned in (the plan's)."""
    from app import db, validity
    cited = citations(query, facts, store)
    if not cited:
        return []
    out: list[dict] = []
    for collection in collections:
        if validity.declared(collection) is None:
            continue
        try:
            con = db.connect(collection)
        except Exception:                    # noqa: BLE001 — the search answers without it
            continue
        try:
            for want in cited:
                c = want.citation
                for key in _keys(con, c):
                    works = [r[0] for r in con.execute(
                        "SELECT work FROM validity_works WHERE key=?", (key,))]
                    found: list[tuple] = []
                    for work in works:
                        versions = _versions(con, work)
                        v = _chosen(versions, as_of)
                        if v is None:
                            continue
                        row = con.execute("SELECT title, lang FROM documents WHERE uid=?",
                                          (v["uid"],)).fetchone()
                        found.append((work, versions, v, row[0] if row else None,
                                      row[1] if row else None))
                    notes = want.notes
                    for work, versions, v, title, _lang in _one_language(found, query_lang,
                                                                         want.languages):
                        act_label = (title or key).split(" — ")[0]
                        ch = _article(collection, v["native_id"], want)
                        changes = _changes(con, work, c.article)
                        if ch is not None:
                            note = notes.in_force if as_of is None else notes.as_of.format(day=_day(as_of))
                            out.append(_fact(collection, key, act_label, c, v, ch, note, changes))
                        if ch is None or want.repealed(str(ch.get("text") or "")):
                            for older in reversed([x for x in versions
                                                   if x["valid_from"] < v["valid_from"]]):
                                och = _article(collection, older["native_id"], want)
                                if och is not None and not want.repealed(str(och.get("text") or "")):
                                    out.append(_fact(collection, key, act_label, c, older, och,
                                                     notes.last, changes))
                                    break
                        if len(out) >= MAX_ARTICLES:
                            return out[:MAX_ARTICLES]
        except Exception:                    # noqa: BLE001 — never fatal to the search
            continue
        finally:
            con.close()
    return out[:MAX_ARTICLES]
