"""NeuRIS parser 4 (2026-11-15), from München's raw read of three real
expressions: the portal writes every FRBR URI under its host
(`https://www.ris.bund.de/eli/…`), and in its test phase writes 1001-01-01
for every lifecycle date, no ris:inkraft and no top-level temporalCoverage.
Parser 3 flagged every record `expression-eli-mismatch` and `no-stated-start`,
and the validity index read every version of a law as current. Parser 4 strips
the host, reads the placeholder as no day, and starts an unstated version at
its ELI's point in time, saying so on the record."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app import validity
from app.plugins.base import DiscoverState
from app.plugins.neuris import SOURCE
from app.plugins.neuris import source as nsrc
from tests import neuris_support as ns

V3 = SOURCE.at_parser_version(3)
HOST = "https://www.ris.bund.de/"


def _as_the_portal_writes_it(xml: bytes) -> bytes:
    """The fixture the way the real portal writes it (München, 2026-11-15)."""
    out = re.sub(rb'(<akn:FRBR(?:this|uri)\b[^>]*\bvalue=")(eli/)', rb"\1" + HOST.encode() + rb"\2", xml)
    out = re.sub(rb'(<akn:eventRef\b[^>]*\bdate=")\d{4}-\d{2}-\d{2}"', rb'\g<1>1001-01-01"', out)
    out = re.sub(rb"<ris:(?:inkraft|ausserkraft)\b[^>]*?(?:/>|>[^<]*</ris:(?:inkraft|ausserkraft)>)", b"", out)
    return out


def _doc(plugin, root: Path, expression: str, *, xml_edit=None, **api):
    gen, d = ns.directory(expression)
    files = {p.name: p.read_bytes() for p in sorted(d.iterdir())}
    if xml_edit is not None:
        files["regelungstext-1.xml"] = xml_edit(files["regelungstext-1.xml"])
    body = ns.api_json(expression, main=files["regelungstext-1.xml"])
    body.update(api)
    body = {k: v for k, v in body.items() if v is not None}
    ns.lay_out(root, expression, data=ns.zip_bytes(expression, files=files), api=body)
    [ref] = list(plugin.discover(DiscoverState(corpus_root=root)))
    return plugin.parse(plugin.fetch(ref))


def test_the_registered_parser_is_4_and_still_carries_3_and_2():
    assert SOURCE.PARSER_VERSION == 4
    assert [SOURCE.at_parser_version(v).PARSER_VERSION for v in (4, 3, 2)] == [4, 3, 2]
    assert SOURCE.at_parser_version(4) is SOURCE


def test_a_frbr_uri_under_the_portal_s_host_is_the_same_expression(tmp_path):
    doc = _doc(SOURCE, tmp_path / "a", ns.S1016, xml_edit=_as_the_portal_writes_it, temporalCoverage=None)
    assert "expression-eli-mismatch" not in [i.code for i in doc.issues]
    assert doc.extra["eli_expression"] == ns.S1016 and doc.extra["eli_work"].startswith("eli/bund/")
    assert HOST in json.dumps(doc.extra["frbr"]), "the XML's own URIs kept as written"
    old = _doc(V3, tmp_path / "b", ns.S1016, xml_edit=_as_the_portal_writes_it, temporalCoverage=None)
    assert "expression-eli-mismatch" in [i.code for i in old.issues], "parser 3 read it as another expression"
    assert doc.keys == old.keys, "the Work's key was already read under any host"


def test_the_test_phase_placeholder_is_no_day_and_the_eli_point_in_time_starts_the_version(tmp_path):
    doc = _doc(SOURCE, tmp_path / "a", ns.S1016, xml_edit=_as_the_portal_writes_it, temporalCoverage=None)
    pit = ns.S1016.split("/")[5]
    assert doc.extra["valid_from"] == pit and "1001-01-01" not in str(doc.extra.get("valid_from"))
    [said] = [i for i in doc.issues if i.code == "start-from-eli"]
    assert pit in said.detail
    assert validity.of_doc(doc, validity.Validity(from_key="extra.valid_from", work_key="extra.eli_work"))["from"] \
        == int(pit.replace("-", "")), "it takes its place in the Work's chain"


def test_a_placeholder_coverage_is_no_stated_start_and_a_real_one_is_the_record_s(tmp_path):
    placeholder = _doc(SOURCE, tmp_path / "a", ns.S1016, xml_edit=_as_the_portal_writes_it,
                       temporalCoverage="1001-01-01/..")
    assert placeholder.extra["valid_from"] == ns.S1016.split("/")[5]
    stated = _doc(SOURCE, tmp_path / "b", ns.S1016, xml_edit=_as_the_portal_writes_it,
                  temporalCoverage="2023-05-01/..")
    assert stated.extra["valid_from"] == "2023-05-01"
    assert not [i for i in stated.issues if i.code in ("start-from-eli", "no-stated-start")]


def test_the_issue_is_declared():
    assert "start-from-eli" in nsrc.ISSUES and nsrc.relative_eli(HOST + "eli/bund/x") == "eli/bund/x"
    assert nsrc.relative_eli("eli/bund/x") == "eli/bund/x"
