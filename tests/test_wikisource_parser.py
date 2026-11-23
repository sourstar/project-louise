"""tests/test_wikisource_parser.py — the Wikisource parser's core
(app/plugins/wikisource/: dump, proofread, work, render;
docs/notes/wikisource-design.md §1, §3): what a work is, its order, the
transclusion of proofread scan pages, the header, and markup to words."""
from __future__ import annotations

import pytest

from app.plugins.wikisource import dump, proofread, render, work

NS = "http://www.mediawiki.org/xml/export-0.11/"


def _page(pid, ns, title, text, *, rev=None, model="wikitext", sha1="abc", redirect=None):
    red = f'<redirect title="{redirect}" />' if redirect else ""
    return (f"<page><title>{title}</title><ns>{ns}</ns><id>{pid}</id>{red}<revision><id>{rev or pid * 10}</id>"
            f"<model>{model}</model><format>text/x-wiki</format>"
            f'<text bytes="{len(text)}" xml:space="preserve">{text}</text><sha1>{sha1}</sha1></revision></page>')


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- the dump

def test_a_stream_gives_every_page_with_what_a_record_names_it_by():
    data = (f'<mediawiki xmlns="{NS}">' + _page(7, 108, "Pagina:Vita.djvu/5", _esc("<noinclude/>x"),
                                                   model="proofread-page", sha1="q1")
            + _page(8, 0, "Vita", "testo", sha1="q2") + _page(9, 0, "Vecchio", "", redirect="Vita")).encode()
    got = list(dump.pages_of_stream(data))
    assert [(p.id, p.ns, p.title, p.rev_id, p.sha1) for p in got] == [
        (7, 108, "Pagina:Vita.djvu/5", 70, "q1"), (8, 0, "Vita", 80, "q2"), (9, 0, "Vecchio", 90, "abc")]
    assert got[0].is_scan and not got[1].is_scan and got[2].redirect == "Vita"


def test_the_header_stream_holds_no_page_and_names_the_namespaces():
    head = ('<mediawiki><siteinfo><namespaces><namespace key="0" case="first-letter" />'
            '<namespace key="102" case="first-letter">Autore</namespace>'
            '<namespace key="108" case="first-letter">Pagina</namespace></namespaces></siteinfo>')
    assert list(dump.pages_of_stream(head.encode())) == []
    assert dump.namespaces(head) == {0: "", 102: "Autore", 108: "Pagina"}


# ---------------------------------------------------------------- proofread pages

def test_a_scan_page_splits_into_header_body_footer_and_its_level():
    sp = proofread.parts('<noinclude><pagequality level="4" user="X" />{{RigaIntestazione}}</noinclude>'
                         "Nel mezzo del cammin<noinclude>{{nota}}</noinclude>")
    assert (sp.level, sp.body, sp.footer) == (4, "Nel mezzo del cammin", "{{nota}}")
    assert proofread.parts("just text") == proofread.ScanPage(None, "", "just text", "")


@pytest.mark.parametrize("attrs,last,want", [
    ('from=3 to=5', None, [3, 4, 5]),
    ('from="2" to="10" step="4"', None, [2, 6, 10]),
    ('from=3 to=5 include="9,11-12" exclude="4"', None, [3, 5, 9, 11, 12]),
    ('from=7', 9, [7, 8, 9]),
])
def test_the_pages_a_tag_reads(attrs, last, want):
    tag = proofread.tags(f'<pages index="Vita.djvu" {attrs} />')[0]
    assert tag.index == "Vita.djvu" and proofread.numbers(tag, last) == want


def _scans(levels):
    return {f"Pagina:Vita.djvu/{n}": f'<noinclude><pagequality level="{lv}" user="u" /></noinclude>{body}'
                                     f"<noinclude>footer {n}</noinclude>"
            for n, (lv, body) in levels.items()}


def test_a_transclusion_reads_the_bodies_in_order_never_a_header_or_footer():
    scans = _scans({3: (4, "uno"), 4: (3, "due"), 5: (1, "tre")})
    got = proofread.expand('Prima\n<pages index="Vita.djvu" from=3 to=5 />\nDopo', "Pagina", scans.get)
    assert got.text == "Prima\nuno\ndue\ntre\nDopo"
    assert got.used == [("Pagina:Vita.djvu/3", 4), ("Pagina:Vita.djvu/4", 3), ("Pagina:Vita.djvu/5", 1)]
    assert "footer" not in got.text and got.issues == [] and got.indexes == ["Vita.djvu"]


def test_sections_cut_the_first_and_last_pages_and_only_one_reads_one():
    scans = _scans({1: (4, 'a<section begin="s1" />primo<section end="s1" /><section begin="s2" />secondo'
                           '<section end="s2" />'),
                    2: (4, "## s3 ##\nterzo\n## s4 ##\nquarto")})
    got = proofread.expand('<pages index="Vita.djvu" from=1 to=2 fromsection="s2" tosection="s3" />',
                           "Pagina", scans.get)
    assert got.text == "secondo\nterzo"
    only = proofread.expand('<pages index="Vita.djvu" from=1 to=1 onlysection="s1" />', "Pagina", scans.get)
    assert only.text == "primo"


def test_what_is_not_expanded_is_said_and_its_tag_kept():
    got = proofread.expand('<pages index="Foglio.jpg" from="a" to="b" /> e <pages index="Vita.djvu" from=1 '
                           'to=2 tosection="x" foo="1" />', "Pagina", _scans({1: (3, "uno")}).get)
    codes = [c for c, _ in got.issues]
    assert codes == ["pages-not-numbered", "pages-attribute-unread", "page-missing"]
    assert got.text.startswith('<pages index="Foglio.jpg"') and "uno" in got.text


# ---------------------------------------------------------------- works

def test_a_work_is_its_page_and_every_subpage_and_an_orphan_its_own():
    got = work.works(["Vita", "Vita/Capitolo I", "Vita/Capitolo II", "Rime/Sonetto/1", "Rime/Sonetto/2",
                      "Rime/Sonetto", "Lettere/1"])
    assert got == {"Vita": ["Vita", "Vita/Capitolo I", "Vita/Capitolo II"],
                   "Rime/Sonetto": ["Rime/Sonetto", "Rime/Sonetto/1", "Rime/Sonetto/2"],
                   "Lettere/1": ["Lettere/1"]}


def test_the_order_is_the_texts_own_mentions_then_the_rest_by_title_depth_first():
    texts = {"Vita": "{{Indice sommario|nome=Capitolo II|titolo=Il secondo}}\n[[/Capitolo I]]",
             "Vita/Capitolo II": "[[Vita/Capitolo II/Nota]]",
             "Vita/Capitolo I": "", "Vita/Capitolo II/Nota": "", "Vita/Appendice": "", "Vita/Aggiunta": ""}
    got = work.order("Vita", list(texts), texts.get)
    assert got == ["Vita", "Vita/Capitolo II", "Vita/Capitolo II/Nota", "Vita/Capitolo I",
                   "Vita/Aggiunta", "Vita/Appendice"]


def test_the_header_keeps_every_parameter_and_reads_its_fields():
    h = work.header("{{Intestazione\n| Nome e cognome dell'autore = Benvenuto Cellini\n| Titolo = Vita\n"
                    "| Anno di pubblicazione = 1728\n| Progetto = Letteratura\n}}\nTesto", "it")
    assert h.template == "Intestazione"
    assert h.fields == {"author": "Benvenuto Cellini", "title": "Vita", "year": "1728"}
    assert ("Progetto", "Letteratura") in h.params and len(h.params) == 4
    assert work.header("no header here", "it").template is None
    assert work.header("{{header|title=X}}", "fr").template is None, "a wiki with no header template"


# ---------------------------------------------------------------- render

def test_markup_goes_words_stay_and_notes_follow_the_section():
    text = render.to_text("{{Intestazione|Titolo=Vita}}\n{{Centrato|CAPITOLO I}}\n"
                          "Io, '''Benvenuto''', nacqui in [[Firenze|Fiorenza]]<ref>Il 3 novembre 1500.</ref>"
                          " nel [[1500]].<br>{{Sc|Fine}} {{larger|120%|grande}}[[Categoria:Autobiografie]]"
                          "<!-- nota redazionale -->")
    assert text == "CAPITOLO I\nIo, Benvenuto, nacqui in Fiorenza[1] nel 1500.\nFine grande\n\n[1] Il 3 novembre 1500."


def test_a_heading_is_its_words_on_a_line():
    assert render.to_text("== Parte prima ==\nTesto") == "Parte prima\n\nTesto"
