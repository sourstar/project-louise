"""NeuRIS parser 4 (2026-11-15), from München's raw read of three real
expressions: the portal writes every FRBR URI under its host
(`https://www.ris.bund.de/eli/…`), and in its test phase writes 1001-01-01
for every lifecycle date, no ris:inkraft and no top-level temporalCoverage.
Parser 3 flagged every record `expression-eli-mismatch` and `no-stated-start`,
and the validity index read every version of a law as current. Parser 4 strips
the host, reads the placeholder as no day, and gives an unstated version a
PROVISIONAL start (operator: "we add a 'draft' flag for the in force date, and
we use the publishing date as draft"), said on every result."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from app import validity
from app.plugins.base import DiscoverState
from app.plugins.neuris import SOURCE
from app.plugins.neuris import source as nsrc
from tests import neuris_support as ns

V3 = SOURCE.at_parser_version(3)
HOST = "https://www.ris.bund.de/"
VALIDITY = validity.Validity(from_key="extra.valid_from", work_key="extra.eli_work")


def _as_the_portal_writes_it(xml: bytes, *, date_name: bytes = b"ausfertigung") -> bytes:
    """The fixture the way the real portal writes it (München, 2026-11-15):
    FRBR URIs under the host, every lifecycle date the placeholder, no
    ris:inkraft, the expression dated by its Ausfertigung."""
    out = re.sub(rb'(<akn:FRBR(?:this|uri)\b[^>]*\bvalue=")(eli/)', rb"\1" + HOST.encode() + rb"\2", xml)
    out = re.sub(rb'(<akn:eventRef\b[^>]*\bdate=")\d{4}-\d{2}-\d{2}"', rb'\g<1>1001-01-01"', out)
    out = re.sub(rb"<ris:(?:inkraft|ausserkraft)\b[^>]*?(?:/>|>[^<]*</ris:(?:inkraft|ausserkraft)>)", b"", out)
    expr = re.search(rb"<akn:FRBRExpression\b.*?</akn:FRBRExpression>", out, re.S)
    fixed = re.sub(rb'(<akn:FRBRdate\b[^>]*\bname=")[^"]*"', rb"\g<1>" + date_name + b'"', expr.group(0))
    return out[:expr.start()] + fixed + out[expr.end():]


def _doc(plugin, root: Path, expression: str, *, xml_edit=None, **api):
    _, d = ns.directory(expression)
    files = {p.name: p.read_bytes() for p in sorted(d.iterdir())}
    if xml_edit is not None:
        files["regelungstext-1.xml"] = xml_edit(files["regelungstext-1.xml"])
    body = ns.api_json(expression, main=files["regelungstext-1.xml"])
    for k, v in api.items():
        if k == "datePublished":
            body["exampleOfWork"] = {**body["exampleOfWork"], "datePublished": v}
        else:
            body[k] = v
    body = {k: v for k, v in body.items() if v is not None}
    ns.lay_out(root, expression, data=ns.zip_bytes(expression, files=files), api=body)
    [ref] = list(plugin.discover(DiscoverState(corpus_root=root)))
    return plugin.parse(plugin.fetch(ref))


PIT = ns.S1016.split("/")[5]                 # 2023-04-26


def test_the_registered_parser_is_5_and_still_carries_4_3_and_2():
    assert SOURCE.PARSER_VERSION == 5
    assert [SOURCE.at_parser_version(v).PARSER_VERSION for v in (5, 4, 3, 2)] == [5, 4, 3, 2]
    assert SOURCE.at_parser_version(5) is SOURCE


def test_parser_4_as_deployed_starts_at_the_eli_unmarked(tmp_path):
    four = SOURCE.at_parser_version(4)
    doc = _doc(four, tmp_path / "a", ns.S1016, xml_edit=_as_the_portal_writes_it,
               temporalCoverage=None, datePublished="2023-04-28")
    assert doc.extra["valid_from"] == PIT and "valid_from_provisional" not in doc.extra
    assert "start-from-eli" in [i.code for i in doc.issues]


def test_a_frbr_uri_under_the_portal_s_host_is_the_same_expression(tmp_path):
    doc = _doc(SOURCE, tmp_path / "a", ns.S1016, xml_edit=_as_the_portal_writes_it, temporalCoverage=None)
    assert "expression-eli-mismatch" not in [i.code for i in doc.issues]
    assert doc.extra["eli_expression"] == ns.S1016 and doc.extra["eli_work"].startswith("eli/bund/")
    assert HOST in json.dumps(doc.extra["frbr"]), "the XML's own URIs kept as written"
    old = _doc(V3, tmp_path / "b", ns.S1016, xml_edit=_as_the_portal_writes_it, temporalCoverage=None)
    assert "expression-eli-mismatch" in [i.code for i in old.issues], "parser 3 read it as another expression"
    assert doc.keys == old.keys, "the Work's key was already read under any host"


def test_a_first_version_starts_provisionally_at_the_act_s_publication(tmp_path):
    doc = _doc(SOURCE, tmp_path / "a", ns.S1016, xml_edit=_as_the_portal_writes_it,
               temporalCoverage=None, datePublished="2023-04-28")
    assert (doc.extra["valid_from"], doc.extra["valid_from_provisional"]) == ("2023-04-28", "publication")
    assert [i.code for i in doc.issues if i.code.startswith(("start", "no-stated"))] == ["start-provisional"]
    meta = validity.of_doc(doc, VALIDITY)
    assert meta["from"] == 20230428 and meta["start_basis"] == "publication"


def test_an_amended_version_starts_provisionally_at_the_amendment_s_signing(tmp_path):
    doc = _doc(SOURCE, tmp_path / "a", ns.S1016,
               xml_edit=lambda b: _as_the_portal_writes_it(b, date_name=b"ausfertigung-aenderung"),
               temporalCoverage=None, datePublished="2000-05-30")
    assert (doc.extra["valid_from"], doc.extra["valid_from_provisional"]) == (PIT, "signing")


def test_a_placeholder_publication_is_no_day_either(tmp_path):
    doc = _doc(SOURCE, tmp_path / "a", ns.S1016, xml_edit=_as_the_portal_writes_it,
               temporalCoverage="1001-01-01/..", datePublished="1001-01-01")
    assert (doc.extra["valid_from"], doc.extra["valid_from_provisional"]) == (PIT, "signing")


def test_a_stated_start_replaces_the_provisional_one(tmp_path):
    doc = _doc(SOURCE, tmp_path / "a", ns.S1016, xml_edit=_as_the_portal_writes_it,
               temporalCoverage="2023-05-01/..", datePublished="2023-04-28")
    assert doc.extra["valid_from"] == "2023-05-01" and "valid_from_provisional" not in doc.extra
    assert not [i for i in doc.issues if i.code in ("start-provisional", "no-stated-start")]
    assert validity.of_doc(doc, VALIDITY)["start_basis"] is None


def test_every_result_says_a_start_is_provisional(tmp_path):
    """validity.describe, what a result's `validity` is (05 §5.3)."""
    from app import db
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    validity.record(con, "neuris:1", 1, {"from": 20230428, "work": "w", "has_text": True,
                                          "in_force": None, "start_basis": "publication"})
    validity.record(con, "neuris:2", 2, {"from": 20240101, "work": "w2", "has_text": True, "in_force": None})
    got = validity.describe(con, [1, 2])
    assert got[1]["provisional"] == "publication" and got[1]["from"] == "2023-04-28"
    assert "provisional" not in got[2], "a stated start says nothing more"


def test_the_issue_is_declared():
    assert "start-provisional" in nsrc.ISSUES and "valid_from_provisional" in nsrc.EXTRA_KEYS
    assert nsrc.relative_eli(HOST + "eli/bund/x") == "eli/bund/x" and nsrc.relative_eli("eli/bund/x") == "eli/bund/x"
