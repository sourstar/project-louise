"""PubMed parser 9 (operator's choice B, 2026-11-08;
docs/notes/pubmed-other-abstracts.md): a citation with no <Abstract> takes
an OtherAbstract into `sections` as its "Abstract", the English one first.
Every other citation parses to the same sections as at parser 8, so its
`content_hash` and chunk do not move. Parser 8 is KEPT for a collection
whose Codex names it (`collections.plugin_at`): a deploy never parses the
live pubmed differently from its own records."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import collections as collections_mod
from app.hold import equivalence
from app.hold.records import content_hash
from app.plugins.base import DocRef, RawDoc
from app.plugins.pubmed import SOURCE
from app.plugins.pubmed.source import PubmedSourceV8, section_abstract


def _article(pmid: int, abstract: str | None, others: list[tuple[str, str, str]]) -> RawDoc:
    own = f"<Abstract><AbstractText>{abstract}</AbstractText></Abstract>" if abstract else ""
    other = "".join(f'<OtherAbstract Type="{t}" Language="{lang}"><AbstractText>{text}</AbstractText>'
                    f"</OtherAbstract>" for t, lang, text in others)
    xml = (f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>"
           f"<ArticleTitle>Japan's TFR plunges.</ArticleTitle>{own}</Article>{other}"
           f"</MedlineCitation></PubmedArticle>")
    return RawDoc(ref=DocRef(source_id="pubmed", native_id=str(pmid), locator=f"f.xml.gz#{pmid}"), raw_hash="x" * 32,
                  data=xml.encode())


V8 = SOURCE.at_parser_version(8)


def test_the_registered_parser_is_9_and_still_carries_8():
    assert SOURCE.PARSER_VERSION == 9 and isinstance(V8, PubmedSourceV8) and V8.PARSER_VERSION == 8
    assert SOURCE.at_parser_version(9) is SOURCE
    assert SOURCE.at_parser_version(7) is None, "only the versions it carries"


def test_a_citation_with_its_own_abstract_parses_as_at_8():
    raw = _article(1, "We measured it.", [("Publisher", "ger", "Wir haben es gemessen.")])
    nine, eight = SOURCE.parse(raw), V8.parse(raw)
    assert nine.sections == eight.sections == [("Title", "Japan's TFR plunges."), ("Abstract", "We measured it.")]
    assert content_hash(nine.title, nine.sections) == content_hash(eight.title, eight.sections)
    assert nine.extra == eight.extra, "the record is the same apart from sections"


def test_an_other_abstract_only_citation_takes_the_english_one():
    """The POPLINE case (PIP/eng) and the night's 44 plain-language summaries."""
    raw = _article(2, None, [("Publisher", "ger", "Deutsch."), ("plain-language-summary", "eng", "In short.")])
    nine, eight = SOURCE.parse(raw), V8.parse(raw)
    assert eight.sections == [("Title", "Japan's TFR plunges.")], "parser 8: the title alone"
    assert nine.sections == [("Title", "Japan's TFR plunges."), ("Abstract", "In short.")]
    assert content_hash(nine.title, nine.sections) != content_hash(eight.title, eight.sections)
    assert [o["text"] for o in nine.extra["other_abstracts"]] == ["Deutsch.", "In short."], \
        "every other abstract stays whole in extra"
    assert "other-abstract-only" in [i.code for i in nine.issues], "still flagged: the origin's shape"


def test_without_an_english_one_the_first_in_document_order():
    raw = _article(3, None, [("Publisher", "ger", "Deutsch."), ("Publisher", "fre", "Français.")])
    assert SOURCE.parse(raw).sections[1] == ("Abstract", "Deutsch.")


def test_a_citation_with_no_abstract_of_any_kind_is_the_title_alone():
    raw = _article(4, None, [])
    assert SOURCE.parse(raw).sections == V8.parse(raw).sections == [("Title", "Japan's TFR plunges.")]
    assert section_abstract({"abstract": "", "extra": {"other_abstracts": [{"text": "  "}]}}) == ""


def test_a_collection_parses_at_its_codexs_parser():
    """The live pubmed (Codex: parser 8) keeps parsing as its records were
    built; a collection created on 9 parses at 9; a version the plugin no
    longer carries gets the plugin as before."""
    class C(SimpleNamespace):
        plugin_instance = collections_mod.Collection.plugin_instance
    def coll(v):
        return C(plugin="pubmed", source="origin",
                 codex=SimpleNamespace(parser=SimpleNamespace(id="pubmed", version=v)))
    assert coll(8).plugin_instance() is V8
    assert coll(9).plugin_instance() is SOURCE
    assert coll(7).plugin_instance() is SOURCE
    assert C(plugin="pubmed", source="origin", codex=None).plugin_instance() is SOURCE


def test_the_image_is_equivalent_for_a_codex_naming_a_carried_parser(monkeypatch):
    """`hold check`'s reference images: a Codex on parser 8 is still read by
    this image as its own (invariant 3), one on 7 is not."""
    from app import chunking
    monkeypatch.setattr(chunking, "chunk_version_for", lambda cid: 1)
    def codex(v):
        return SimpleNamespace(plugin="pubmed", parser=SimpleNamespace(id="pubmed", version=v),
                               layers=(SimpleNamespace(chunker=SimpleNamespace(id="abstract", version=1)),))
    assert equivalence.equivalent_by_versions(codex(9))
    assert equivalence.equivalent_by_versions(codex(8))
    assert not equivalence.equivalent_by_versions(codex(7))


def _line(raw: RawDoc, plugin) -> dict:
    """The record line's fields `line_moved_since` reads."""
    doc = plugin.parse(raw)
    return {"uid": doc.uid, "sections": [list(s) for s in doc.sections], "extra": doc.extra}


def test_parser_9_names_the_parser_8_lines_it_moves():
    """RULE 1: re-creating pubmed on 9 takes the live snapshot's 40 M lines
    and parses again only those parser 9 writes differently — the ~64 k
    whose only abstract is an OtherAbstract — read off the line itself."""
    other_only = _line(_article(2, None, [("PIP", "eng", "Fertility fell.")]), V8)
    own = _line(_article(1, "We measured it.", [("Publisher", "ger", "Deutsch.")]), V8)
    bare = _line(_article(4, None, []), V8)
    assert SOURCE.line_moved_since(other_only, 8)
    assert not SOURCE.line_moved_since(own, 8) and not SOURCE.line_moved_since(bare, 8)
    assert not SOURCE.line_moved_since(_line(_article(2, None, [("PIP", "eng", "x")]), SOURCE), 9)
    assert SOURCE.line_moved_since(own, 7), "a version the rule does not know is parsed"
    # and the rule is exact: a line it does not move parses to the same sections at 9
    for raw in (_article(1, "We measured it.", [("Publisher", "ger", "Deutsch.")]), _article(4, None, [])):
        assert SOURCE.parse(raw).sections == V8.parse(raw).sections


def test_a_held_line_the_new_parser_moves_is_parsed_and_the_rest_taken(monkeypatch):
    from app.pack import build as pack_build
    from app.pack import records as pack_records
    lines = {"pubmed:1": _line(_article(1, "Own.", []), V8) | {"origin_hash": "o1"},
             "pubmed:2": _line(_article(2, None, [("PIP", "eng", "Other.")]), V8) | {"origin_hash": "o2"}}
    monkeypatch.setattr(pack_records, "read_record_at", lambda path, frame, line: lines[path.rsplit("/", 1)[-1]])
    monkeypatch.setattr(pack_build, "_record_from_line", lambda coll, line: ("taken", line["uid"]))
    monkeypatch.setattr(pack_build, "_record_of", lambda coll, plugin, row: ("parsed", row["uid"]))
    monkeypatch.setattr(pack_build, "_origin_moved", lambda *a: False)

    def row(uid, parser_from):
        return {"uid": uid, pack_build.HELD_DIR: "/held",
                pack_build.HELD_AT: {"path": uid, "frame": 0, "line": 0},
                pack_build.HELD_SRC: {"uid": uid, "native_id": uid[7:], "path": "p", "lang": "en",
                                      "txt_hash": f"o{uid[7:]}"},
                **({pack_build.HELD_FROM: parser_from} if parser_from is not None else {})}
    assert pack_build.held_or_parsed(None, SOURCE, row("pubmed:1", 8)) == ("taken", "pubmed:1")
    assert pack_build.held_or_parsed(None, SOURCE, row("pubmed:2", 8)) == ("parsed", "pubmed:2")
    assert pack_build.held_or_parsed(None, SOURCE, row("pubmed:2", None)) == ("taken", "pubmed:2"), \
        "the same parser: every line taken, as before"
    assert pack_build._parser_moved(SimpleNamespace(PARSER_VERSION=3), 2, {}), \
        "a plugin that names no rule has every line parsed"


def test_records_from_admits_a_parser_change_only_where_the_plugin_names_its_lines():
    from app.hold import records as hold_records

    def codex(plugin="pubmed", pid="pubmed", v=9):
        return SimpleNamespace(plugin=plugin, parser=SimpleNamespace(id=pid, version=v))
    assert hold_records.parser_carried(codex(v=8), codex(), ["parser", "toolchain.platform_image"]) == 8
    assert hold_records.parser_carried(codex(v=8), codex(), ["slicing"]) is None, "no parser change"
    assert hold_records.parser_carried(codex(v=8), codex(v=10), ["parser"]) is None, \
        "the target's Codex must name the parser this image runs"
    assert hold_records.parser_carried(codex("gutenberg", "gutenberg", 9),
                                       codex("gutenberg", "gutenberg", 11), ["parser"]) is None, \
        "a plugin naming no moved lines: refused, parsed by the book"
