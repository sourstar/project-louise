"""THE ACT'S OWN WRAPPER (app/plugins/eurlex/formex.py `own_wrapper`;
München's facts, 2026-11-16). A Croatian Special Edition volume gives ONE
fmx4 manifestation whose items are the files of two acts sharing its FIN.ID
(32009L0045's expression lists 32011D0291's wrapper and body too): 1,241
Croatian works were refused as "two wrappers". The act's text is the wrapper
whose NO.CELEX is its own; the other act's files stay items of the unit
(invariant 11) and are not read. The same rule in the Data Dump's builder,
Cellar's fetch, the parser and the unit's day."""
from __future__ import annotations

import shutil
import zipfile

import pytest

from app import plugin_io
from app.acquire import eurlex_dump
from app.plugins.base import DiscoverState
from app.plugins.eurlex import SOURCE, formex
from app.plugins.eurlex import source as src
from tests.acquire.test_eurlex_dump import FORMEX, _dump, serial  # noqa: F401

OWN = "L_1972160IT.01000101.doc.xml"
#: the other act's wrapper and body, named to sort FIRST, published earlier
OTHER, OTHER_BODY = "L_1960001IT.01000301.doc.xml", "L_1960001IT.01000301.xml"


def _other_files(celex: str = "21990A0315(01)") -> dict[str, bytes]:
    wrapper = (f'<?xml version="1.0" encoding="UTF-8"?><DOC><BIB.DOC><NO.CELEX>{celex}</NO.CELEX>'
               f'<NO.DOC TYPE="OJ"><TXT>1960/L 1/03</TXT></NO.DOC></BIB.DOC>'
               f'<PUBLICATION.REF FILE="L_1960001IT.toc.xml"><COLL>L</COLL><NO.OJ>1</NO.OJ>'
               f'<DATE ISO="19600101">19600101</DATE><LG.OJ>IT</LG.OJ></PUBLICATION.REF>'
               f'<FMX><DOC.MAIN.PUB NO.SEQ="0003"><LG.DOC>IT</LG.DOC>'
               f'<REF.PHYS TYPE="DOC.XML" FILE="{OTHER_BODY}"></REF.PHYS></DOC.MAIN.PUB></FMX></DOC>')
    body = ('<?xml version="1.0" encoding="UTF-8"?><ACT><TITLE><TI><P>Accordo di un altro atto</P></TI></TITLE>'
            '<ENACTING.TERMS><ARTICLE IDENTIFIER="001"><TI.ART>Articolo 1</TI.ART>'
            '<ALINEA>Testo di un altro atto.</ALINEA></ARTICLE></ENACTING.TERMS></ACT>')
    return {OTHER: wrapper.encode(), OTHER_BODY: body.encode()}


def _own_files(*, stated: str | None = "31972R1473") -> dict[str, bytes]:
    files = {p.name: p.read_bytes() for p in FORMEX[2].iterdir()
             if p.name not in ("notice.xml", "tree.json") and not p.name.startswith("LEG_MTD")}
    if stated:
        files[OWN] = files[OWN].replace(b"<BIB.DOC>", f"<BIB.DOC><NO.CELEX>{stated}</NO.CELEX>".encode(), 1)
    return files


def test_one_wrapper_is_read_as_before_and_of_several_the_act_s_own():
    single = _own_files(stated=None)
    assert formex.own_wrapper(single, "31972R1473", single.__getitem__) == OWN
    both = {**_own_files(), **_other_files()}
    assert formex.own_wrapper(both, "31972R1473", both.__getitem__) == OWN
    assert formex.own_wrapper(both, "21990A0315(01)", both.__getitem__) == OTHER


@pytest.mark.parametrize("files, why", [
    ({**_own_files(stated=None), **_other_files()}, "none naming 31972R1473"),
    ({**_own_files(), **_other_files("31972R1473")}, "2 naming 31972R1473"),
])
def test_several_wrappers_none_or_two_of_them_the_act_s_is_refused_and_said(files, why):
    with pytest.raises(formex.FormexError, match=why):
        formex.own_wrapper(files, "31972R1473", files.__getitem__)


def test_the_act_reads_the_same_with_the_other_act_s_files_beside_it():
    alone = formex.parse(_own_files())
    mixed = formex.parse({**_own_files(), **_other_files()}, "31972R1473")
    assert (mixed.title, mixed.articles, mixed.recitals, mixed.annexes, mixed.body_file, mixed.publication_date) == \
        (alone.title, alone.articles, alone.recitals, alone.annexes, alone.body_file, alone.publication_date)
    assert mixed.wrapper_file == OWN and "altro atto" not in repr(mixed.articles)


def test_the_unit_s_day_and_id_are_its_own_act_s(tmp_path):
    """The other wrapper sorts first and was published in 1960: the unit's
    day (its native id) is still 1972's."""
    plain, mixed = tmp_path / "plain", tmp_path / "mixed"
    for root, extra in ((plain, {}), (mixed, _other_files())):
        unit = root / src.unit_rel("it", FORMEX[0])
        shutil.copytree(FORMEX[2], unit, ignore=shutil.ignore_patterns("notice.xml", "LEG_MTD*"))
        (unit / OWN).write_bytes(_own_files()[OWN])
        for name, data in extra.items():
            (unit / name).write_bytes(data)
    (a,) = SOURCE.discover(DiscoverState(corpus_root=plain))
    (b,) = SOURCE.discover(DiscoverState(corpus_root=mixed))
    assert a.error is None and b.error is None and a.native_id == b.native_id
    doc = plugin_io.canonical_doc(SOURCE, {"native_id": b.native_id, "path": b.locator})
    assert doc.sections and "altro atto" not in " ".join(body for _l, body in doc.sections)


def test_the_dump_builds_the_unit_with_every_item_the_origin_lists(tmp_path, serial, monkeypatch):  # noqa: F811
    """The tree lists both acts' files under the manifestation (as the
    Special Edition's does): the unit lands all of them, and is not refused."""
    import dataclasses
    from app.plugins.eurlex import from_tree
    from app.plugins.eurlex.notice import Item
    dump = _dump(tmp_path / "dump")
    fmx = dump / "datadump-it" / "LEG_IT_FMX.zip"
    with zipfile.ZipFile(fmx) as z:
        held = {i.filename: z.read(i) for i in z.infolist()}
    held[f"{FORMEX[1]}/fmx4/{OWN}"] = _own_files()[OWN]
    with zipfile.ZipFile(fmx, "w") as z:
        for name, data in held.items():
            z.writestr(name, data)
        for name, data in _other_files().items():
            z.writestr(f"{FORMEX[1]}/fmx4/{name}", data)
    real = from_tree.notice

    def listed_both(tree, lang3, celex=None):
        note = real(tree, lang3, celex)
        if celex != FORMEX[0] or "fmx4" not in note.items:
            return note
        extra = [Item(uri=f"http://cellar/{n}", name=n, size=None, order=None) for n in (OTHER, OTHER_BODY)]
        return dataclasses.replace(note, items={**note.items, "fmx4": note.items["fmx4"] + extra})
    monkeypatch.setattr(from_tree, "notice", listed_both)
    corpus = tmp_path / "corpus"
    got = eurlex_dump.build(dump, corpus, ["it"], log=lambda m: None)
    assert got["it"]["formex-refused"] == 0 and got["it"]["built"] == 2
    unit = corpus / src.unit_rel("it", FORMEX[0])
    assert {OTHER, OTHER_BODY, OWN} <= {p.name for p in unit.iterdir()}, "every item the origin lists, landed"
