"""eurlex parser 3 (2026-11-14, München's measures from the node):
  - Cellar's NON-INFERRED TREE kept, scoped to the language (rdf.py): 26,607
    assertions over 1,698 Italian acts were in the tree and not in the notice;
  - an act WITHOUT Formex read from its XHTML, HTML or PDF (rendition.py):
    13,374 of 43,623 Italian works in force had no Formex and were left out.
Fixtures: tests/fixtures/eurlex/{rdf,xhtml,html,pdf} (README: fetched from
Cellar on 2026-11-13)."""
from __future__ import annotations

import json
import random
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app import plugin_io
from app.acquire import cellar
from app.plugins.base import DiscoverState
from app.plugins.eurlex import SOURCE, notice, rdf
from app.plugins.eurlex import source as src

FIX = Path(__file__).resolve().parent / "fixtures" / "eurlex"
RDFS = sorted((FIX / "rdf").glob("*.rdf"))
ALL_LANGS = sorted(notice.LANGUAGES.values())


def _count(tree: dict) -> int:
    return (sum(len(v) for props in tree["nodes"].values() for v in props.values())
            + sum(len(v) for a in tree["axioms"] for v in a.values()))


# ---- the tree ---------------------------------------------------------------------------------

@pytest.mark.parametrize("path", RDFS, ids=lambda p: p.stem)
def test_the_tree_is_read_whole_and_canonical_whatever_cellar_s_order(path):
    """Cellar's serialisation order is not stable (München: the dump's and
    Cellar's files differ by sha256, same graph): the canonical form is one."""
    data = path.read_bytes()
    tree = rdf.read(data)
    root = ET.fromstring(data)
    triples = sum(len(list(d)) for d in root)
    assert _count(tree) == triples, "every property element is one value held"
    shuffled = list(root)
    random.Random(7).shuffle(shuffled)
    for d in list(root):
        root.remove(d)
    for d in shuffled:
        root.append(d)
    assert rdf.read(ET.tostring(root)) == tree


@pytest.mark.parametrize("path", RDFS, ids=lambda p: p.stem)
def test_across_the_24_languages_no_assertion_of_the_tree_is_lost(path):
    """The scope moves another language's expression to its own slice; the
    union of the 24 slices is the whole tree (invariant 11)."""
    tree = rdf.read(path.read_bytes())
    seen_nodes: dict[str, dict] = {}
    seen_axioms: list = []
    for lang in ALL_LANGS:
        s = rdf.scope(tree, lang)
        seen_nodes.update(s["nodes"])
        seen_axioms += [a for a in s["axioms"] if a not in seen_axioms]
    assert seen_nodes == tree["nodes"]
    assert sorted(map(json.dumps, seen_axioms)) == sorted(map(json.dumps, tree["axioms"]))


@pytest.mark.parametrize("path", RDFS, ids=lambda p: p.stem)
def test_the_italian_slice_holds_no_other_language_s_expression(path):
    s = rdf.scope(rdf.read(path.read_bytes()), "ITA")
    langs = {m.group(1) for u in s["nodes"] for m in [re.search(r"\.([A-Z]{3})(\.|$)", u)] if m}
    assert langs <= {"ITA"}, langs
    assert s["language"] == "ITA" and s["nodes"]


def test_a_form_that_is_not_cellar_s_flat_one_is_refused():
    nested = (b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:x="http://x/">'
              b'<rdf:Description rdf:about="http://a"><x:p><rdf:Description rdf:about="http://b"/></x:p>'
              b'</rdf:Description></rdf:RDF>')
    with pytest.raises(rdf.RdfError):
        rdf.read(nested)
    with pytest.raises(rdf.RdfError):
        rdf.read(b"<notrdf/>")


# ---- the acts without Formex -------------------------------------------------------------------

def _unit_from(root: Path, kind: str, celex: str) -> Path:
    unit = root / src.unit_rel("it", celex)
    unit.mkdir(parents=True)
    for p in (FIX / kind / celex).iterdir():
        (unit / p.name).write_bytes(p.read_bytes())
    (unit / rdf.TREE_FILE).write_bytes(rdf.dumps({"nodes": {}, "axioms": [], "language": "ITA"}))
    return unit


@pytest.fixture
def renditions(tmp_path) -> Path:
    root = tmp_path / "eurlex"
    for kind, celex in (("xhtml", "32010D0699"), ("html", "31988D0540"),
                        ("pdf", "32004D0411"), ("pdf", "31962R0031")):
        _unit_from(root, kind, celex)
    return root


def _doc(root: Path, celex: str):
    refs = [r for r in SOURCE.discover(DiscoverState(corpus_root=root)) if not r.error]
    ref = next(r for r in refs if r.locator.endswith(src.unit_rel("it", celex)))
    return plugin_io.canonical_doc(SOURCE, {"native_id": ref.native_id, "path": ref.locator})


def test_every_act_without_formex_is_discovered_dated_by_its_notice(renditions):
    refs = list(SOURCE.discover(DiscoverState(corpus_root=renditions)))
    assert not [r for r in refs if r.error], [r.error for r in refs if r.error]
    assert len(refs) == 4


def test_a_unit_without_its_tree_is_not_yet_a_document(renditions):
    (renditions / src.unit_rel("it", "31988D0540") / rdf.TREE_FILE).unlink()
    assert len(list(SOURCE.discover(DiscoverState(corpus_root=renditions)))) == 3


def test_the_xhtml_act_is_read_in_articles_and_annexes(renditions):
    doc = _doc(renditions, "32010D0699")
    labels = [l for l, _ in doc.sections]
    assert doc.extra["form"] == "xhtml" and doc.language == "it"
    assert labels[:3] == ["Preamble", "Articolo 1", "Articolo 2"]
    assert "Articolo 13 — Entrata in vigore" in labels[-3] and labels[-2:] == ["ALLEGATO I", "ALLEGATO II"]
    assert sum(1 for l in labels if l.startswith("Progetto di DECISIONE")) == 14, \
        "the annexed draft's 13 articles and its heading, under its title"


def test_the_legacy_html_act_is_read_by_its_headings_words(renditions):
    doc = _doc(renditions, "31988D0540")
    assert doc.extra["form"] == "html"
    assert [l for l, _ in doc.sections] == ["Preamble", "Articolo 1", "Articolo 2", "Articolo 3", "Articolo 4"]
    metas = doc.extra["formex"]["head"]["31988D0540it.html"]["meta"]
    assert any(m.get("name") == "DC.source" and "L 297" in m.get("content", "") for m in metas), \
        "the Dublin Core head kept: the OJ source"


def test_the_pdf_act_is_read_from_its_text_layer(renditions):
    doc = _doc(renditions, "32004D0411")
    assert doc.extra["form"] == "pdf" and [l for l, _ in doc.sections][:2] == ["Preamble", "Articolo 1"]
    assert len(doc.sections) == 8
    info = doc.extra["formex"]["pdf"]["l_15120040430it00500053.pdf"]
    assert info["pages"] == 4 and info["Producer"].startswith("Acrobat Distiller")


def test_a_scan_is_a_record_without_text_and_says_so(renditions):
    doc = _doc(renditions, "31962R0031")
    assert doc.sections == [] and doc.extra["form"] == "pdf"
    assert any(i.code == "no-text-layer" for i in doc.issues)


class _Words(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.words, self.attrs, self._skip = set(), [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        self.attrs += [(tag, k, v or "") for k, v in attrs]

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.words |= set(re.findall(r"\w+", data))


@pytest.mark.parametrize("kind, celex", [("xhtml", "32010D0699"), ("html", "31988D0540")])
def test_no_word_and_no_attribute_of_an_html_item_is_lost(renditions, kind, celex):
    doc = _doc(renditions, celex)
    held = set(re.findall(r"\w+", "\n".join(f"{l}\n{b}" for l, b in doc.sections)
                          + doc.title + json.dumps(doc.extra, ensure_ascii=False)))
    blob = json.dumps(doc.extra, ensure_ascii=False)
    for p in (FIX / kind / celex).iterdir():
        if p.name == "notice.xml":
            continue
        w = _Words()
        w.feed(p.read_text(encoding="utf-8", errors="replace"))
        assert not (w.words - held), sorted(w.words - held)[:20]
        missing = [a for a in w.attrs if json.dumps(a[2], ensure_ascii=False) not in blob]
        assert not missing, missing[:10]


# ---- the fetch ----------------------------------------------------------------------------------

def test_the_listing_asks_for_every_form_not_formex_alone():
    q = cellar.count_query(1988, ("it",))
    assert all(f'"{f}"' in q for f in cellar.FORMS)


def test_the_best_form_the_notice_lists_is_taken():
    for kind, celex, want in (("xhtml", "32010D0699", "xhtml"), ("html", "31988D0540", "html"),
                              ("pdf", "32004D0411", "pdf")):
        note = notice.parse((FIX / kind / celex / "notice.xml").read_bytes())
        assert cellar.form_of(note) == want, celex


def test_a_unit_on_disk_as_recent_as_the_listing_keeps_its_bytes(tmp_path):
    """A unit fetched before parser 3 gains its tree alone: its notice is as
    recent as the listing says and its items match the notice."""
    dest = tmp_path / "u"
    dest.mkdir()
    for p in (FIX / "html" / "31988D0540").iterdir():
        (dest / p.name).write_bytes(p.read_bytes())
    note = notice.parse((dest / "notice.xml").read_bytes())
    held = cellar._epoch(note.last_modified)
    kept = cellar._current_unit(dest, held, None)
    assert kept is not None and set(kept[1]) == {"31988D0540it.html"}
    assert cellar._current_unit(dest, held + 1, None) is None, "the origin changed since: fetched again"
    (dest / "31988D0540it.html").write_bytes(b"short")
    assert cellar._current_unit(dest, held, None) is None, "an item of another size: fetched again"
