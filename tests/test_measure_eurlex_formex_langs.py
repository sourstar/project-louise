"""tools/measure/eurlex_formex_langs.py counts why a language's Formex
units are refused — the wrapper count and the language its <LG.DOC> states —
as the unit builder decides it (München, 2026-11-16: hr refused 1,232)."""
import zipfile

from tests.acquire.test_eurlex_dump import FORMEX, _dump
from tools.measure import eurlex_formex_langs as m


def test_each_work_is_counted_by_its_wrappers_and_the_language_stated(tmp_path):
    dump = _dump(tmp_path / "dump")
    wrapper = next(p for p in FORMEX[2].iterdir() if p.name.endswith(".doc.xml"))
    other = wrapper.read_bytes().replace(b"<LG.DOC>IT</LG.DOC>", b"<LG.DOC>EN</LG.DOC>")
    assert other != wrapper.read_bytes()
    with zipfile.ZipFile(dump / "datadump-it" / "LEG_IT_FMX_2.zip", "w") as z:
        z.writestr(f"aaaa/fmx4/{wrapper.name}", other)
        z.writestr("bbbb/fmx4/A.doc.xml", b"<LG.DOC>IT</LG.DOC>")
        z.writestr("bbbb/fmx4/B.doc.xml", b"<LG.DOC>IT</LG.DOC>")
        z.writestr("cccc/fmx4/X.000101.fmx.xml", b"")
    got = m.tally(dump, "it")
    assert got["works"] == 4
    assert got["kinds"] == {"1/it": 1, "1/en": 1, "2/-": 1, "0/-": 1}
    assert got["examples"]["2/-"] == [{"work": "bbbb", "wrappers": ["A.doc.xml", "B.doc.xml"]}]
    assert "1/it" not in got["examples"], "what is built is not an example of a refusal"
