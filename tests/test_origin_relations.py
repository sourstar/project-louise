"""The links an ORIGIN states are its collection's relations (02 §2.10,
05 §5.5; operator 2026-11-12, decision 1 A): gutenberg's catalogue notes
(several editions, a higher-quality edition, the book's Wikipedia page) and
PubMed's CommentsCorrections and bibliography, read from what the record
already keeps — so `hold records relations` writes them with no re-parse."""
from pathlib import Path

from app.hold import relations as rel
from app.plugins.base import CanonicalDoc, Edge
from app.plugins.gutenberg.source import GutenbergSource
from app.plugins.pubmed.source import PubmedSource, PubmedSourceV8


def doc(uid: str, extra: dict) -> CanonicalDoc:
    return CanonicalDoc(uid=uid, title="", creators=[], language="en", date=None, subjects=[],
                        keys=[], extra=extra, sections=[], raw_hash="")


# ---- gutenberg -----------------------------------------------------------

DESCRIPTION = (
    "Wikipedia page about this book: https://en.wikipedia.org/wiki/Pride_and_Prejudice_(novel))."
    "\n\nProject Gutenberg has several editions of this eBook: #1342, #42671 and #20686."
    "\n\nA somewhat higher quality eBook is available as #42671."
    "\n\nSee also #555 in a note that is not the editions note.")


def test_gutenberg_states_its_catalogues_edition_and_wikipedia_links():
    got = list(GutenbergSource().relations(doc("gutenberg:1342", {"description": DESCRIPTION})))
    assert got == [Edge("gutenberg:20686", "bf:otherEdition"), Edge("gutenberg:42671", "bf:otherEdition"),
                   Edge("https://en.wikipedia.org/wiki/Pride_and_Prejudice_(novel)", "foaf:isPrimaryTopicOf")], \
        "its own number and a number outside the note are no edition; one edge per (type, to); " \
        "the URL less the prose's ')' and '.', its own '(novel)' kept"


def test_a_book_with_no_note_states_nothing():
    assert list(GutenbergSource().relations(doc("gutenberg:1", {}))) == []
    assert list(GutenbergSource().relations(doc("gutenberg:1", {"description": "A note."}))) == []


# ---- pubmed --------------------------------------------------------------

EXTRA = {
    "comments_corrections": [
        {"ref_type": "RetractionIn", "source": "J Widget Med. 2027;19:1", "pmid": "99999999",
         "pmid_version": "1"},
        {"ref_type": "CommentOn", "source": "A notice named by text alone"},
        {"ref_type": "Cites", "pmid": "11111111"},
        {"ref_type": "ErratumIn", "pmid": "1"}],              # itself
    "references": [
        {"citation": "Someone et al. 1999.", "pmid": "11111111",
         "ids": {"pubmed": "11111111", "doi": "10.1/x"}, "list_title": "References"},
        {"citation": "A DOI only.", "ids": {"doi": "10.2/y"}},
        {"citation": "A PMC id only.", "ids": {"pmc": "PMC7777777"}},
        {"citation": "Nested 2001.", "list_title": "References › Supplementary"}]}


def test_pubmed_states_its_citations_comments_and_corrections():
    want = [Edge("pubmed:99999999", "pubmed:RetractionIn"), Edge("pubmed:11111111", "pubmed:cites"),
            Edge("doi:10.2/y", "pubmed:cites"), Edge("pmc:PMC7777777", "pubmed:cites")]
    for plugin in (PubmedSource(), PubmedSourceV8()):        # both parsers a Codex may name
        assert list(plugin.relations(doc("pubmed:1", EXTRA))) == want, \
            "NLM's RefType verbatim; Cites is the bibliography's statement, once; a text-only " \
            "notice, a reference with no id and the record itself are no edge"


# ---- through a record line: nothing re-parsed -----------------------------

def test_the_edges_come_from_the_record_line_a_build_wrote(isolated_config, tmp_path, corpus_dir):
    """The note survives into the record line (`extra.description`), and the
    relations pass reads it there: the parts a gutenberg build writes."""
    from tests.hold.test_build_and_snapshot import _build, _collection
    from tests.hold.test_relations_build import _lines, _relations
    import json
    rdf = corpus_dir / "10001" / "pg10001.rdf"
    rdf.write_text(rdf.read_text("utf-8").replace(
        "<dcterms:title>A Study in Fixtures</dcterms:title>",
        "<dcterms:title>A Study in Fixtures</dcterms:title>\n"
        "    <dcterms:description>Project Gutenberg has several editions of this eBook: "
        "#10001 and #10002.</dcterms:description>"), "utf-8")
    root = _collection(tmp_path)
    _build(root, corpus_dir, records_per_shard=2, snapshot_id="2026-08")
    shard = root / "gutenberg" / "shard"
    parts = _relations(shard)
    assert parts, "a plugin with relations writes its parts beside the record parts"
    edges = [json.loads(line) for p in parts for line in _lines(shard / p)]
    assert edges == [{"from": "gutenberg:10001", "to": "gutenberg:10002", "type": "bf:otherEdition",
                      "snapshot": "2026-08"}]
    assert rel.plugin_of(type("C", (), {"plugin": "gutenberg"})()) is not None


def test_a_gutenberg_built_before_relations_gets_them_without_a_rebuild(
        isolated_config, tmp_path, corpus_dir, monkeypatch):
    """The node's path (RULE 1): gutenberg built and frozen with no relations;
    `hold records relations --snapshot <next>` writes the edges, no record
    part touched, and the next snapshot lists them."""
    from app import louise
    from app.hold import snapshot as sn
    from tests.hold.test_build_and_snapshot import _build, _collection
    from tests.hold.test_relations_build import QUIET, _records, _relations
    rdf = corpus_dir / "10002" / "pg10002.rdf"
    text = rdf.read_text("utf-8")
    title = text[text.index("<dcterms:title>"):text.index("</dcterms:title>") + len("</dcterms:title>")]
    rdf.write_text(text.replace(title, title + "\n    <dcterms:description>A somewhat higher quality "
                                "eBook is available as #10003.</dcterms:description>"), "utf-8")
    root = _collection(tmp_path)
    with monkeypatch.context() as m:                      # built before this feature
        m.setattr(rel, "plugin_of", lambda codex: None)
        _build(root, corpus_dir, records_per_shard=2, snapshot_id="2026-08")
        first = sn.create(root, "gutenberg", snapshot_id="2026-08", **QUIET)
    shard = root / "gutenberg" / "shard"
    assert first.relations == () and not _relations(shard)
    records = _records(shard)
    assert louise.main(["hold", "records", "relations", "gutenberg", "--root", str(root),
                        "--snapshot", "2026-09"]) == 0
    assert _records(shard) == records, "no record part touched"
    snap = sn.create(root, "gutenberg", snapshot_id="2026-09", **QUIET)
    assert snap.relations and snap.delta.added == () and snap.delta.changed == ()
    from app.ask import traverse
    got = traverse.traverse("gutenberg:10002", depth=1, root=root, collection="gutenberg")["edges"]
    assert {(e["from"], e["type"], e["to"]) for e in got} == {
        ("gutenberg:10002", "bf:otherEdition", "gutenberg:10003")}
