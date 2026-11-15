"""NeuRIS parser 3 (operator, 2026-11-14, choice A): what the portal rewrites
on every daily rebuild and says nothing about the law — a fresh random GUID on
every LegalDocML element, the HTML render's `<meta name="erzeugt">` — is the
provider's bookkeeping (invariant 11 (d)), read and hashed away. München's
comparison of the held manifestations with the bulk of 2026-11-14: 26 of 26
sampled expressions differed in bytes, none in wording; 53,862 GUIDs, 721
render timestamps. A regeneration of an unchanged law gives the same
`origin_hash` and the same record; a word, a note or an annotation the origin
changed still moves both. Parser 2 is kept for a Codex that names it."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

from app import collections as collections_mod
from app.plugins.base import DiscoverState
from app.plugins.neuris import SOURCE
from app.plugins.neuris import source as nsrc
from tests import neuris_support as ns

V2 = SOURCE.at_parser_version(2)
HTML = (b'<html><head><meta name="erzeugt" content="2026-11-12T16:00:00Z"/>'
        b'<meta name="dokumentart" content="norm"/></head><body><p>Text</p></body></html>')


def _regenerated(xml: bytes) -> bytes:
    """The same XML with a fresh GUID on every element, as each daily build writes it."""
    return re.sub(rb'GUID="[^"]*"', lambda m: f'GUID="{uuid.uuid4()}"'.encode(), xml)


def _files(expression: str, **edit) -> dict[str, bytes]:
    _, d = ns.directory(expression)
    files = {p.name: p.read_bytes() for p in sorted(d.iterdir())}
    files["regelungstext-1.html"] = HTML
    for name, fn in edit.items():
        files[name.replace("_", "-").replace("-xml", ".xml").replace("-html", ".html")] = fn(
            files[name.replace("_", "-").replace("-xml", ".xml").replace("-html", ".html")])
    return files


def _record(plugin, root: Path, files: dict[str, bytes]):
    ns.lay_out(root, ns.S1016, data=ns.zip_bytes(ns.S1016, files=files))
    [ref] = list(plugin.discover(DiscoverState(corpus_root=root)))
    raw = plugin.fetch(ref)
    return raw.raw_hash, plugin.parse(raw)


def test_parser_3_is_still_carried_beside_2():
    assert SOURCE.PARSER_VERSION >= 3 and isinstance(V2, nsrc.NeurisSourceV2) and V2.PARSER_VERSION == 2
    assert SOURCE.at_parser_version(3).PARSER_VERSION == 3 and SOURCE.at_parser_version(1) is None


def test_a_daily_rebuild_of_an_unchanged_law_is_the_same_record(tmp_path):
    held = _files(ns.S1016)
    rebuilt = _files(ns.S1016, regelungstext_1_xml=_regenerated, anlage_regelungstext_1_xml=_regenerated,
                     regelungstext_1_html=lambda b: b.replace(b"2026-11-12T16:00:00Z", b"2026-11-14T05:45:00Z"))
    assert rebuilt["regelungstext-1.xml"] != held["regelungstext-1.xml"], "every GUID is new"
    h1, d1 = _record(SOURCE, tmp_path / "a", held)
    h2, d2 = _record(SOURCE, tmp_path / "b", rebuilt)
    assert h1 == h2, "the same origin_hash"
    assert d1.sections == d2.sections and json.dumps(d1.extra, sort_keys=True) == json.dumps(d2.extra, sort_keys=True)
    # parser 2 read the bytes as fetched: every rebuild was "changed at the origin"
    assert _record(V2, tmp_path / "c", held)[0] != _record(V2, tmp_path / "d", rebuilt)[0]


def test_a_word_the_origin_changed_still_moves_the_hash_and_the_record(tmp_path):
    held = _files(ns.S1016)
    h1, d1 = _record(SOURCE, tmp_path / "a", held)
    word = d1.sections[0][1].split()[-1].strip(".").encode()        # a word of the law's own text
    assert word in held["regelungstext-1.xml"]
    changed = _files(ns.S1016, regelungstext_1_xml=lambda b: _regenerated(b.replace(word, word + b"n")))
    h2, d2 = _record(SOURCE, tmp_path / "b", changed)
    assert h1 != h2 and d1.sections != d2.sections


def test_an_annotation_or_a_note_the_origin_added_is_kept_and_moves_the_hash(tmp_path):
    """domainTerm, the editorial status, the notes: about the law, kept (invariant 11)."""
    held = _files(ns.S1016)
    annotated = _files(ns.S1016, regelungstext_1_xml=lambda b: b.replace(
        b"<akn:FRBRWork ", b'<akn:FRBRWork domainTerm="Interne Abk\xc3\xbcrzung" ', 1))
    h1, _ = _record(SOURCE, tmp_path / "a", held)
    h2, d2 = _record(SOURCE, tmp_path / "b", annotated)
    assert h1 != h2 and "Interne Abkürzung" in json.dumps(d2.extra, ensure_ascii=False)


def test_the_normalization_is_written_exactly_and_touches_nothing_else():
    xml = b'<a GUID="1" eId="x"><b GUID=\'2\' refersTo="y">GUID="in text" stays</b></a>'
    assert nsrc.normalize_member("r.xml", xml) == b'<a eId="x"><b refersTo="y">GUID="in text" stays</b></a>'
    assert nsrc.normalize_member("r.html", HTML) == HTML.replace(
        b'<meta name="erzeugt" content="2026-11-12T16:00:00Z"/>', b"")
    assert nsrc.normalize_member("bild.png", b'GUID="1" ') == b'GUID="1" ', "a binary member as fetched"
    assert "GUID" in nsrc.NORMALIZATION and "erzeugt" in nsrc.NORMALIZATION


def test_a_collection_parses_at_its_codexs_parser():
    class C(SimpleNamespace):
        plugin_instance = collections_mod.Collection.plugin_instance

    def coll(v):
        return C(plugin="neuris", source="origin",
                 codex=SimpleNamespace(parser=SimpleNamespace(id="neuris", version=v)))
    assert coll(2).plugin_instance() is V2
    assert coll(3).plugin_instance() is SOURCE.at_parser_version(3)
