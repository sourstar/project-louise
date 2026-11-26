"""Cellar's DOUBLED member names (2026-11-26, 32015D1735 hr): its corrected
package holds the body as `L_2015252HR.01004901.xml---L_2015252HR.01005202.tif.xml`
and the annex as `…01005201.xml---…01005203.tif.xml`, while the wrapper names
`…01004901.xml` and `…01005201.xml`. The act's text is there; the reader
refused it ("the wrapper names the body …, which is not among the items")."""
from __future__ import annotations

from app.plugins.eurlex import formex


def test_a_wanted_name_reads_the_one_member_doubled_from_it():
    files = {"A.xml---B.tif.xml": b"<ACT/>", "C.xml": b"<ANNEX/>"}
    got, aliases = formex.doubled_names(files, ["A.xml", "C.xml", "D.xml"])
    assert got["A.xml"] == b"<ACT/>" and aliases == {"A.xml": "A.xml---B.tif.xml"}
    assert "D.xml" not in got and set(files) <= set(got)


def test_a_held_name_or_two_doubled_members_resolve_nothing():
    files = {"A.xml": b"1", "A.xml---x.xml": b"2", "E.xml---1.xml": b"3", "E.xml---2.xml": b"4"}
    got, aliases = formex.doubled_names(files, ["A.xml", "E.xml"])
    assert got["A.xml"] == b"1" and "E.xml" not in got and aliases == {}


def test_the_doubled_member_is_read_as_the_body_and_not_kept_again():
    """Architecture's review of 023245a0: the member read as the body was
    also kept whole under `other_items` — the act's text held twice."""
    from tests.acquire.test_eurlex_dump import FORMEX, _items
    files = _items(FORMEX[2])
    body = "L_1972160IT.01000101.xml"
    doubled = body + "---L_1972160IT.01000102.tif.xml"
    files[doubled] = files.pop(body)
    act = formex.parse(files)
    assert act.meta["doubled_names"] == {body: doubled}
    assert doubled not in act.meta.get("other_items", {}) and body not in act.meta.get("other_items", {})
    assert act.body_file == body and act.title
