"""The EUR-Lex reader of an act without Formex (app/plugins/eurlex/rendition.py),
C11 of docs/notes/eurlex-review-2026-11-21.md, on real Cellar items trimmed to
a few elements (tests/fixtures/eurlex/rendition/, README: which, and how):
  1. a QUOTED article heading (an amending act's replacement text) is the
     quoting article's text, not an article — in a list item's table cell,
     nested in the article's own element, or after a quoted one;
  3. a heading in a table cell (a correlation table's "Articolul 12") is not
     an article."""
from __future__ import annotations

import re
import types
from pathlib import Path

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
