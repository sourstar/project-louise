"""The EUR-Lex reader of an act without Formex (app/plugins/eurlex/rendition.py),
C11 of docs/notes/eurlex-review-2026-11-21.md, on real Cellar items trimmed to
a few elements (tests/fixtures/eurlex/rendition/, README: which, and how):
  1. a QUOTED article heading (an amending act's replacement text) is the
     quoting article's text, not an article — in a list item's table cell,
     nested in the article's own element, or after a quoted one;
  2. the annex headings that put the number first (et, hu, lt, lv), ro's
     "ANEXĂ", legacy Maltese "L-Artikolu"/"L-ANNESS", and an inserted
     article's mark in every language ("14a. cikk", "14.o-A", "Член 14а");
  3. a heading in a table cell (a correlation table's "Articolul 12") is not
     an article;
  4. the footnotes go to the record's notes, with the section that calls
     them, and each numbered recital is a section — as formex.py reads them."""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from app.plugins.eurlex import rendition

FIX = Path(__file__).resolve().parent / "fixtures" / "eurlex"
REND = FIX / "rendition"
NOTE = types.SimpleNamespace(expression_title=None, date_document=None, date_publication=None)


def _read(name: str, data: bytes | None = None):
    path = REND / name
    return rendition.parse_html({name: data if data is not None else path.read_bytes()}, [name],
                                lang="xx", root="ACT", note=NOTE)


def _sections(act) -> dict[str, str]:
    return dict(act.rendered_sections)


# ---- 1. quoted headings ---------------------------------------------------------------------

def test_quoted_articles_in_an_amending_act_s_list_items_are_its_article_s_text():
    """32018R0825 en (CONVEX XHTML): 12 "articles" against 5 in its Formex —
    "‘Article 32a" and, without a quote mark, "Article 32b" of the replacement
    text, each in a list item's table cell. The trimmed act keeps 4 of its 5."""
    act = _read("32018R0825en.html")
    assert [a.num for a in act.articles] == ["Article 1", "Article 2", "Article 3", "Article 5"]
    art2 = _sections(act)["Article 2"]
    assert "‘Article 32a\nReport\n1. The Commission shall" in art2
    assert "\nArticle 32b\nExercise of the delegation\n" in art2


def test_a_quoted_annex_title_is_the_annex_s_text_not_part_of_its_heading():
    """The annex was labelled "ANNEX ‘ANNEX Ia": the quoted title
    (`oj-quotation-ti`) joined to the act's own."""
    act = _read("32018R0825en.html")
    assert [a.title for a in act.annexes] == ["ANNEX"]
    assert _sections(act)["ANNEX"].startswith("‘ANNEX Ia\nILO CONVENTIONS REFERRED TO IN THIS REGULATION")


def test_a_quoted_article_nested_in_the_article_s_element_is_its_text():
    """32016D1837 en: "‘Article 3" sits in a div inside `art_1`, in no
    table; the act's own Article 3 follows. Without its quote mark it is still
    the quoting article's: nested in the open article's element."""
    act = _read("32016D1837en.html")
    assert [a.num for a in act.articles] == ["Article 1", "Article 2", "Article 3"]
    assert "‘Article 3\n1. This Decision shall expire on 31 December 2019." in _sections(act)["Article 1"]
    data = (REND / "32016D1837en.html").read_bytes().replace("‘Article 3</p>".encode(), b"Article 3</p>")
    act = _read("32016D1837en.html", data)
    assert [a.num for a in act.articles] == ["Article 1", "Article 2", "Article 3"]
    assert "\nArticle 3\n1. This Decision shall expire" in _sections(act)["Article 1"]


def test_legacy_html_a_quoted_article_s_next_heading_is_the_quotation_s():
    """32003L0051 en (legacy HTML, no markup): '"Article 42e' then 'Article
    42f', both inserted by Article 1; the act's own Article 2 after them."""
    act = _read("32003L0051en.html")
    assert [a.num for a in act.articles] == ["Article 1", "Article 2"]
    art1 = _sections(act)["Article 1"]
    assert '"Article 42e' in art1 and "\nArticle 42f\n" in art1


def test_legacy_html_a_quotation_ends_at_its_closing_mark_or_at_a_lower_number():
    """31997L0011 en: "'Article 3" closes with a backtick ("…indents.`;"),
    "'Article 4" is followed by the act's own "Article 2" (numbered below
    it); its ANNEX quotes "'ANNEX I", and "ANNEX II" is the quotation's."""
    act = _read("31997L0011en.html")
    assert [a.num for a in act.articles] == ["Article 1", "Article 2"]
    assert [a.title for a in act.annexes] == ["ANNEX"]
    annex = _sections(act)["ANNEX"]
    assert annex.startswith("'ANNEX I\n") and annex.endswith("\nANNEX II")


def test_the_quote_tracker_never_holds_a_heading_numbered_at_or_below_the_quotation_s():
    lines = [rendition._Line("", t) for t in (
        "Article 1", "the following Article is inserted:", "'Article 7", "text", "Article 8",
        "Article 2", "text of Article 2", "ANNEX", "'ANNEX I", "ANNEX II", "ANNEX III")]
    _, articles, annexes = rendition._sections(lines)
    assert [a.num for a in articles] == ["Article 1", "Article 2"]
    assert [a.title for a in annexes] == ["ANNEX"]


# ---- 2. headings in the 24 languages --------------------------------------------------------

#: each language's article 1, its inserted article 14a, annex I and annex Ia,
#: as 02016R1036-20180608's consolidated XHTML states them (fetched from
#: Cellar on 2026-11-21, one per language; NBSP as norm reads it)
HEADINGS = {
    "bg": ("Член 1", "Член 14а", "ПРИЛОЖЕНИЕ I", "ПРИЛОЖЕНИЕ Iа"),
    "cs": ("Článek 1", "Článek 14a", "PŘÍLOHA I", "PŘÍLOHA Ia"),
    "da": ("Artikel 1", "Artikel 14a", "BILAG I", "BILAG Ia"),
    "de": ("Artikel 1", "Artikel 14a", "ANHANG I", "ANHANG Ia"),
    "el": ("Άρθρο 1", "Άρθρο 14α", "ΠΑΡΑΡΤΗΜΑ I", "ΠΑΡΑΡΤΗΜΑ Ια"),
    "en": ("Article 1", "Article 14a", "ANNEX I", "ANNEX Ia"),
    "es": ("Artículo 1", "Artículo 14 bis", "ANEXO I", "ANEXO I BIS"),
    "et": ("Artikkel 1", "Artikkel 14a", "I LISA", "Ia LISA"),
    "fi": ("1 artikla", "14 a artikla", "LIITE I", "LIITE I a"),
    "fr": ("Article premier", "Article 14 bis", "ANNEXE I", "ANNEXE I bis"),
    "ga": ("Airteagal 1", "Airteagal 14a", "IARSCRÍBHINN I", "IARSCRÍBHINN Ia"),
    "hr": ("Članak 1.", "Članak 14.a", "PRILOG I.", "PRILOG I.a"),
    "hu": ("1. cikk", "14a. cikk", "I. MELLÉKLET", "Ia. MELLÉKLET"),
    "it": ("Articolo 1", "Articolo 14 bis", "ALLEGATO I", "ALLEGATO I bis"),
    "lt": ("1 straipsnis", "14a straipsnis", "I PRIEDAS", "Ia PRIEDAS"),
    "lv": ("1. pants", "14.a pants", "I PIELIKUMS", "Ia PIELIKUMS"),
    "mt": ("Artikolu 1", "Artikolu 14a", "ANNESS I", "ANNESS Ia"),
    "nl": ("Artikel 1", "Artikel 14 bis", "BIJLAGE I", "BIJLAGE I bis"),
    "pl": ("Artykuł 1", "Artykuł 14a", "ZAŁĄCZNIK I", "ZAŁĄCZNIK Ia"),
    "pt": ("Artigo 1.o", "Artigo 14.o-A", "ANEXO I", "ANEXO I-A"),
    "ro": ("Articolul 1", "Articolul 14a", "ANEXA I", "ANEXA Ia"),
    "sk": ("Článok 1", "Článok 14a", "PRÍLOHA I", "PRÍLOHA Ia"),
    "sl": ("Člen 1", "Člen 14a", "PRILOGA I", "PRILOGA Ia"),
    "sv": ("Artikel 1", "Artikel 14a", "BILAGA I", "BILAGA Ia"),
}


@pytest.mark.parametrize("lang", sorted(HEADINGS))
def test_every_language_s_article_and_annex_headings_are_read_by_their_words(lang):
    """The legacy HTML and the PDF carry no class: the words decide. Before
    C11 the inserted article was missed in bg, el, fi, hu, lt, lv, pt and the
    annexes in et, hu, lt, lv."""
    article, inserted, annex, annex_a = HEADINGS[lang]
    assert rendition._ARTICLE.match(article) and rendition._ARTICLE.match(inserted)
    assert rendition._ANNEX.match(annex) and rendition._ANNEX.match(annex_a)


@pytest.mark.parametrize("heading", ["L-Artikolu 1", "Artikolu 14a"])
def test_legacy_maltese_articles_carry_their_article(heading):
    assert rendition._ARTICLE.match(heading)


@pytest.mark.parametrize("heading", ["ANEXĂ", "L-ANNESS I", "MELLÉKLET", "LISA", "PRIEDAS", "PIELIKUMS",
                                     "BILAGA 1"])
def test_a_single_annex_and_the_other_legacy_forms_are_annexes(heading):
    assert rendition._ANNEX.match(heading)


@pytest.mark.parametrize("line", ["Article 5 of", "Article 5 to", "the Article 5", "I. melléklet szerint",
                                  "Annex I"])
def test_text_that_only_names_an_article_or_an_annex_is_no_heading(line):
    assert not rendition._ARTICLE.match(line) and not rendition._ANNEX.match(line)


@pytest.mark.parametrize("name, arts, annexes", [
    ("31992L0043et.html", ["Artikkel 1", "Artikkel 24"], ["I LISA", "II LISA"]),
    ("31992L0043hu.html", ["1. cikk", "24. cikk"], ["I. MELLÉKLET", "II. MELLÉKLET"]),
    ("31992L0043lt.html", ["1 straipsnis", "24 straipsnis"], ["I PRIEDAS", "II PRIEDAS"]),
    ("31992L0043lv.html", ["1. pants", "24. pants"], ["I PIELIKUMS", "II PIELIKUMS"]),
    ("31992L0043mt.html", ["L-Artikolu 1", "L-Artikolu 24"], ["L-ANNESS I", "L-ANNESS II"]),
    ("32004R0726ro.html", ["Articolul 1", "Articolul 90"], ["ANEXĂ"]),
], ids=lambda x: x if isinstance(x, str) else None)
def test_legacy_html_annexes_are_their_own_sections_not_the_last_article_s(name, arts, annexes):
    """31992L0043 lt/lv/et/hu: "Article 24" held ~54K characters, Annexes I–VI
    in it; mt found no article at all; 32004R0726 ro no annex."""
    act = _read(name)
    assert [a.num for a in act.articles] == arts
    assert [a.title for a in act.annexes] == annexes
    last = _sections(act)[arts[-1]]
    assert not any(a in last for a in annexes)


# ---- 3. a heading in a table cell -----------------------------------------------------------

def test_a_correlation_table_s_cells_are_the_annex_s_text():
    """02016R1036-20180608 ro (consolidated XHTML): "ANEXA II › Articolul 12",
    "ANEXA II › Articolul 23"… — the correlation table's cells — were
    articles, most of them empty. The consolidated text's classes now say
    what an article is (`title-article-norm`), and a cell never heads one."""
    act = _read("02016R1036-20180608ro.html")
    assert [a.num for a in act.articles] == ["Articolul 1", "Articolul 14a", "Articolul 23a", "Articolul 24",
                                             "Articolul 25"]
    assert act.articles[1].title == "Platoul continental sau zona economică exclusivă"
    assert [a.title for a in act.annexes] == ["ANEXA I", "ANEXA II"]
    assert not any("›" in label for label, _ in act.rendered_sections)
    assert "Articolul 12\nArticolul 12\nArticolul 22a\nArticolul 23" in _sections(act)["ANEXA II"]


def test_without_classes_a_cell_still_heads_nothing():
    """The same act read by its words alone (its classes taken off): the
    headings are found by `_ARTICLE`, the cells are still the annex's."""
    data = re.sub(rb' class="(title-article-norm|stitle-article-norm|title-annex-1)"', b"",
                  (REND / "02016R1036-20180608ro.html").read_bytes())
    act = _read("02016R1036-20180608ro.html", data)
    assert [a.num for a in act.articles] == ["Articolul 1", "Articolul 14a", "Articolul 23a", "Articolul 24",
                                             "Articolul 25"]
    assert [a.title for a in act.annexes] == ["ANEXA I", "ANEXA II"]


# ---- 4. footnotes and recitals --------------------------------------------------------------

def test_footnotes_are_the_record_s_notes_with_the_section_that_calls_them():
    """32016R0679 en: all 21 footnotes were in "Article 99". As formex.Collect
    does for a NOTE: out of the text, into `notes`, with its caller's section
    (the footnote's back link to its call); a quoted act's own footnote
    ("(*4)") with the article quoting it."""
    act = _read("32018R0825en.html")
    called = {n["text"].split(")")[0] + ")": n["section"] for n in act.notes}
    assert called == {"(1)": "Preamble", "(2)": "Whereas (1)", "(3)": "Whereas (1)", "(*4)": "Article 2"}
    assert act.notes[1]["@"] == {"id": "ntr1-L_2018143EN.01000101-E0001"}
    text = "\n".join(t for _, t in act.rendered_sections)
    assert "Position of the European Parliament of 16 April 2014" not in text
    assert all(n["text"] not in text for n in act.notes)


def test_a_consolidated_text_s_footnotes_are_notes_too():
    act = _read("02016R1036-20180608ro.html")
    assert [(n["section"], n["text"]) for n in act.notes] == [
        ("Articolul 23a — Exercitarea delegării de competențe", "( 7 ) JO L 123, 12.5.2016, p. 1.")]
    assert "JO L 123" not in _sections(act)["ANEXA II"]


def test_each_numbered_recital_is_a_section_labelled_as_formex_labels_it():
    """32016R0679 en: its 173 recitals were one "Preamble" section; Formex
    gives one each, "Whereas (1)" … The enacting formula after them is the
    preamble's."""
    act = _read("32018R0825en.html")
    labels = [label for label, _ in act.rendered_sections]
    assert labels[:4] == ["Preamble", "Whereas (1)", "Whereas (26)", "Article 1"]
    assert act.recitals == [(label, _sections(act)[label]) for label in ("Whereas (1)", "Whereas (26)")]
    assert _sections(act)["Whereas (26)"] == \
        "(26) Regulations (EU) 2016/1036 and (EU) 2016/1037 should therefore be amended accordingly,"
    preamble = _sections(act)["Preamble"]
    assert preamble.endswith("Whereas:\nHAVE ADOPTED THIS REGULATION:")


def test_the_old_xhtml_s_recitals_are_its_table_rows():
    """fmx2xhtml 3.2 (32010D0699 it, 2012): a recital is a table row, "(1)"
    its first cell; "HA ADOTTATO LA PRESENTE DECISIONE:" after them."""
    xhtml = FIX / "xhtml" / "32010D0699"
    names = sorted(p.name for p in xhtml.glob("*.html"))
    act = rendition.parse_html({n: (xhtml / n).read_bytes() for n in names}, names, lang="it", root="ACT",
                               note=NOTE)
    assert [label for label, _ in act.recitals] == [f"considerando quanto segue ({i})" for i in (1, 2, 3)]
    assert act.recitals[0][1].startswith("(1) L’articolo 70 dell’accordo euromediterraneo")
    assert dict(act.rendered_sections)["Preamble"].endswith("HA ADOTTATO LA PRESENTE DECISIONE:")


def test_legacy_html_recitals_run_to_the_next_and_the_enacting_formula_is_the_preamble_s():
    """32004R0726 ro (legacy HTML): "(1) …" paragraphs after "întrucât:";
    the formula and the title heading before Article 1 go back to the preamble."""
    act = _read("32004R0726ro.html")
    assert [label for label, _ in act.recitals] == ["întrucât (1)", "întrucât (2)"]
    assert "ADOPTĂ" not in act.recitals[-1][1]
    assert _sections(act)["Preamble"].endswith("ADOPTĂ PREZENTUL REGULAMENT:\nTITLUL I\n"
                                               "DEFINIȚII ȘI DOMENIU DE APLICARE")


def test_a_pdf_s_recitals_stay_in_its_preamble():
    lines = [rendition._Line("", t) for t in ("considerando quanto segue:", "(1) Ai sensi della direttiva",
                                               "(2) La Commissione", "Articolo 1", "testo")]
    sections, _articles, _annexes = rendition._sections(lines)
    assert [label for label, _ in sections] == ["Preamble", "Articolo 1"]
