"""Normattiva parser 9 (2026-11-14): an article INSERTED by an amending act,
whose whole text Normattiva writes inside <heading> between "((…))", is
labelled by its number and rubric only, its text once, in the body. The
shapes are München's, from the collection-wide AKN scan (389 distinct
articles): D.Lgs. 502/1992 art. 3-septies (the body in the heading, no
paragraph), D.P.R. 597/1973 art. 14 (a long heading AND paragraphs), D.P.R.
1092/1973 art. 162 (a heading with no parenthesised rubric). Parser 8 is kept
for a collection whose Codex names it, and `line_moved_since` names, off a
parser-8 record line, exactly the lines parser 9 writes otherwise."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app import plugin_io
from app.plugins.base import DiscoverState
from app.plugins.normattiva import SOURCE, akn, rendition
from app.plugins.normattiva import source as nsrc

V8 = SOURCE.at_parser_version(8)

SEPTIES_TEXT = ("1. Si definiscono prestazioni sociosanitarie tutte le attivita' atte a soddisfare, "
                "mediante un processo assistenziale unitario, bisogni di salute della persona che "
                "richiedono unitariamente prestazioni sanitarie e azioni di protezione sociale in "
                "grado di garantire, anche nel lungo periodo, la continuita' tra le azioni di cura e "
                "quelle di riabilitazione. 2. Le prestazioni sociosanitarie comprendono: a) "
                "prestazioni sanitarie a rilevanza sociale.")
PAYMENT_TEXT = ("Speciali modalita' di pagamento. Con decreto del Ministro del tesoro, di concerto con "
                "il Ministro per la funzione pubblica, sono stabilite le modalita' per il pagamento "
                "delle pensioni ai titolari residenti all'estero, anche mediante accreditamento in "
                "conto corrente presso istituti di credito convenzionati, e le relative garanzie per "
                "l'amministrazione, sentito il consiglio di amministrazione dell'Istituto.")
assert len(SEPTIES_TEXT) > rendition.HEADING_BODY_CHARS < len(PAYMENT_TEXT)


def _act(articles: str) -> bytes:
    return f"""<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
<act name="multivigente"><meta><identification source=""><FRBRWork>
<FRBRthis value="/akn/it/act/decreto.legislativo/stato/1992-12-30/502/!main"/>
<FRBRalias name="urn:nir" value="urn:nir:stato:decreto.legislativo:1992-12-30;502"/>
<FRBRalias name="eli" value="eli/id/1992/12/30/092G0549/CONSOLIDATED/20250101"/>
</FRBRWork></identification><publication date="1992-12-30" number="305"/></meta>
<preface><p><docType>DECRETO LEGISLATIVO</docType> <docDate date="1992-12-30">30 dicembre 1992</docDate>,
n. <docNumber>502</docNumber> <docTitle>Riordino della disciplina in materia sanitaria.</docTitle></p></preface>
<body><chapter eId="chp_I"><num>-</num><heading>TITOLO I Ordinamento</heading>
{articles}
</chapter></body></act></akomaNtoso>""".encode()


SEPTIES = (f'<article eId="art_3-septies"><num>Art. 3-septies.</num>'
           f'<heading>(((Integrazione sociosanitaria) {SEPTIES_TEXT}))</heading></article>')
FOURTEEN = (f'<article eId="art_14"><num>Art. 14.</num>'
            f'<heading>(((Redditi fondiari) {SEPTIES_TEXT}))</heading>'
            f'<paragraph><num>2.</num><content><p>Il comma secondo resta.</p></content></paragraph></article>')
PAYMENT = (f'<article eId="art_162"><num>Art. 162.</num><heading>(({PAYMENT_TEXT}))</heading>'
           f'</article>')
RUBRIC = ('<article eId="art_5-bis"><num>Art. 5-bis.</num><heading>((Rubrica nuova))</heading>'
          '<paragraph><num>1.</num><content><p>Testo del comma.</p></content></paragraph></article>')
LONG_RUBRIC = ('<article eId="art_9"><num>Art. 9.</num><heading>(Disposizioni transitorie e finali '
               + "relative al personale " * 16 + ')</heading>'
               '<paragraph><num>1.</num><content><p>Testo.</p></content></paragraph></article>')
REPEALED = ('<article eId="art_6"><num>Art. 6.</num><heading>((Vecchia rubrica))</heading><paragraph><content>'
            '<p>((ARTICOLO ABROGATO DAL D.LGS. 1 GENNAIO 2021, N. 1))</p></content></paragraph></article>')
ALL = SEPTIES + FOURTEEN + PAYMENT + RUBRIC + LONG_RUBRIC + REPEALED


def _sections(xml: str, version: int) -> dict[str, tuple[str, str]]:
    act = akn.parse(_act(xml))
    return {a.num: s for a, s in zip(act.articles, rendition.sections(act, version))}


def test_the_registered_parser_is_9_and_still_carries_8():
    assert SOURCE.PARSER_VERSION == 9 and isinstance(V8, nsrc.NormattivaSourceV8) and V8.PARSER_VERSION == 8
    assert SOURCE.at_parser_version(9) is SOURCE and SOURCE.at_parser_version(7) is None


def test_an_inserted_article_written_in_its_heading_is_labelled_by_number_and_rubric():
    """3-septies: parser 8's label was the whole article (a 1,024-token
    header on every piece); parser 9's is its number and rubric, and the
    text is in the body once."""
    label, body = _sections(SEPTIES, 9)["Art. 3-septies."]
    assert label == "TITOLO I Ordinamento › Art. 3-septies. — (Integrazione sociosanitaria)"
    assert body == f"Art. 3-septies. — (Integrazione sociosanitaria) {SEPTIES_TEXT}"
    assert body.count("Si definiscono") == 1 and "Si definiscono" not in label
    label8, body8 = _sections(SEPTIES, 8)["Art. 3-septies."]
    assert body8 == body and label8.endswith(body), "parser 8: the article in the label too"


def test_a_long_heading_with_paragraphs_keeps_both_in_the_body():
    """597/1973 art. 14: the heading's text and the paragraph after it."""
    label, body = _sections(FOURTEEN, 9)["Art. 14."]
    assert label == "TITOLO I Ordinamento › Art. 14. — (Redditi fondiari)"
    assert body.split("\n") == [f"Art. 14. — (Redditi fondiari) {SEPTIES_TEXT}", "2. Il comma secondo resta."]


def test_a_heading_with_no_parenthesised_rubric_is_labelled_by_its_number_alone():
    """1092/1973 art. 162: "Speciali modalita' di pagamento. Con decreto …"
    runs on; no cut is guessed."""
    label, body = _sections(PAYMENT, 9)["Art. 162."]
    assert label == "TITOLO I Ordinamento › Art. 162."
    assert body == f"Art. 162. — {PAYMENT_TEXT}"


@pytest.mark.parametrize("xml, num", [(RUBRIC, "Art. 5-bis."), (LONG_RUBRIC, "Art. 9."), (REPEALED, "Art. 6.")])
def test_a_rubric_a_long_rubric_without_the_marker_and_a_repeal_are_as_at_8(xml, num):
    """The 157 long headings without "((" are rubrics with provenance notes."""
    assert _sections(xml, 9)[num] == _sections(xml, 8)[num]


def _zip(root: Path, member: str, data: bytes) -> None:
    path = root / "akn/M/decreti-legislativi.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(member, data)


def _line(plugin, root: Path) -> dict:
    ref = next(iter(plugin.discover(DiscoverState(corpus_root=root))))
    doc = plugin_io.canonical_doc(plugin, {"native_id": ref.native_id, "path": ref.locator})
    return {"uid": doc.uid, "sections": [list(s) for s in doc.sections], "extra": doc.extra}


@pytest.fixture
def root(tmp_path) -> Path:
    nsrc._zip_cache.clear()
    return tmp_path / "normattiva"


MEMBER = "DECRETO LEGISLATIVO_19921230_502/1992-12-30_092G0549_VIGENZA_2025-01-01_V9.xml"


@pytest.mark.parametrize("xml, moved", [(SEPTIES, True), (FOURTEEN, True), (PAYMENT, True), (RUBRIC, False),
                                        (LONG_RUBRIC, True), (REPEALED, False), (ALL, True),
                                        (RUBRIC + REPEALED, False)])
def test_the_parser_8_lines_parser_9_moves_are_named_off_the_line(root, xml, moved):
    """RULE 1: re-creating Normattiva on 9 takes the held parser-8 lines and
    parses again only those this names. It errs one way only: a long rubric
    without the marker is parsed again (and comes out the same); a line it
    does not name parses at 9 to the same sections."""
    _zip(root, MEMBER, _act(xml))
    eight, nine = _line(V8, root), _line(SOURCE, root)
    assert SOURCE.line_moved_since(eight, 8) is moved
    assert (eight["sections"] != nine["sections"]) <= moved, "never a moved line left unnamed"
    if not moved:
        assert eight["sections"] == nine["sections"]
    assert not SOURCE.line_moved_since(nine, 9)
    assert SOURCE.line_moved_since(eight, 7), "a version the rule does not know is parsed"


def test_a_collection_parses_at_its_codexs_parser():
    from types import SimpleNamespace

    from app import collections as collections_mod

    class C(SimpleNamespace):
        plugin_instance = collections_mod.Collection.plugin_instance

    def coll(v):
        return C(plugin="normattiva", source="origin",
                 codex=SimpleNamespace(parser=SimpleNamespace(id="normattiva", version=v)))
    assert coll(8).plugin_instance() is V8
    assert coll(9).plugin_instance() is SOURCE
