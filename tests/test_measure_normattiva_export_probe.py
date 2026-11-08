"""tools/measure/normattiva_export_probe.py — does any Normattiva export
format carry the Codice civile's partitions (operator 2026-11-08, option A)?
Louise's own export client, AKN first, one job at a time, stopping at the first
refusal; the members read for partition elements and heading lines."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.acquire import export_job
from app.acquire.spec import TransportError
from tools.measure import normattiva_export_probe as probe_mod

NIR = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE NIR SYSTEM "nirstrict.dtd">
<NIR><DecretoLegislativo><articolato>
<libro><num>LIBRO QUARTO</num><rubrica>DELLE OBBLIGAZIONI</rubrica>
<titolo><num>TITOLO IX</num><rubrica>DEI FATTI ILLECITI</rubrica>
<articolo><num>Art. 2043.</num><rubrica>(Risarcimento per fatto illecito).</rubrica>
<comma>Qualunque fatto doloso o colposo ...</comma></articolo>
<articolo><num>Art. 2384-bis.</num>
<comma>((ARTICOLO NON PIU' PREVISTO A SEGUITO DELLA SOSTITUZIONE DEL LIBRO V, TITOLO V))</comma></articolo>
</titolo></libro></articolato></DecretoLegislativo></NIR>"""
HTML = (b"<html><body><h2>LIBRO QUARTO - DELLE OBBLIGAZIONI</h2><h3>TITOLO IX - DEI FATTI ILLECITI</h3>"
        b"<p>Art. 2043. (Risarcimento per fatto illecito).</p></body></html>")
FLAT = b"<akomaNtoso><doc name='CODICE CIVILE-art. 2043'><p>Art. 2043.</p></doc></akomaNtoso>"


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in members.items():
            z.writestr(n, b)
    return buf.getvalue()


def test_a_job_for_one_day_in_a_named_format():
    body = export_job.body_for({"day": "1942-03-16", "form": "V"}, vigenza_day="2026-11-08", formato="XML")
    assert body["formato"] == "XML" and body["dataVigenza"] == "2026-11-08"
    assert body["parametriRicerca"]["dataInizioEmanazione"] == body["parametriRicerca"]["dataFineEmanazione"] \
        == "1942-03-16"
    assert export_job.body_for({"year": 1990, "form": "O"}, vigenza_day=None)["formato"] == "AKN", \
        "the corpus form is unchanged"
    with pytest.raises(ValueError, match="format"):
        export_job.body_for({"day": "1942-03-16"}, vigenza_day=None, formato="PDF")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        export_job.body_for({"day": "1942-3-16"}, vigenza_day=None)


def _fake(answers: dict[str, bytes | Exception], asked: list):
    def run(api, body, dest, *, headers):
        asked.append((body["formato"], headers.get("User-Agent")))
        got = answers[body["formato"]]
        if isinstance(got, Exception):
            raise got
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(got)
        return len(got)
    return run


def test_a_refused_akn_job_stops_everything(tmp_path):
    asked: list = []
    refused = TransportError("export: check-status 409: La pagina richiesta è stata bloccata")
    report = probe_mod.probe(["AKN", "XML", "JSON"], {"day": "1942-03-16", "form": "V"}, vigenza="2026-11-08",
                             out=tmp_path, match=None, run=_fake({"AKN": refused}, asked))
    assert [f for f, _ua in asked] == ["AKN"], "nothing more is sent to the origin"
    assert asked[0][1].startswith("louise-rag/"), "Louise's own User-Agent, as the node's acquisition"
    assert "409" in report["jobs"][0]["refused"] and "AKN refused" in report["stopped"]
    assert "REFUSED" in (tmp_path / "report.txt").read_text(encoding="utf-8")


def test_each_format_one_job_in_turn_until_a_refusal_and_the_partitions_read(tmp_path):
    asked: list = []
    answers = {"AKN": _zip({"REGIO DECRETO_19420316_262/x_V347.xml": FLAT}),
               "XML": _zip({"REGIO DECRETO_19420316_262/x.xml": NIR, "OTHER_19420316_305/y.xml": NIR}),
               "JSON": TransportError("export: check-status 409"), "HTML": _zip({"a.html": HTML})}
    report = probe_mod.probe(["AKN", "XML", "JSON", "HTML"], {"day": "1942-03-16", "form": "V"},
                             vigenza="2026-11-08", out=tmp_path, match="19420316_262", run=_fake(answers, asked))
    assert [f for f, _ua in asked] == ["AKN", "XML", "JSON"], "HTML not asked after JSON's refusal"
    akn, xml = report["jobs"][0], report["jobs"][1]
    assert akn["members"][0]["heading_lines"] == 0 and akn["members"][0]["partition_elements"] == {}
    nir = xml["members"][0]
    assert nir["root"] == "NIR" and "nirstrict.dtd" in nir["doctype"]
    assert nir["partition_elements"] == {"libro": 1, "titolo": 1}
    assert nir["heading_lines"] == 2, "the repealed article's note about LIBRO V is not a heading"
    assert nir["examples"][1] == {"heading": "TITOLO IX", "before": "Art. 2043."}
    assert "heading_lines" not in xml["members"][1], "another act of the day is listed, not analysed"
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["stopped"].startswith("JSON")


def test_html_headings_are_read_and_akn_must_come_first(tmp_path):
    got = probe_mod.analyse("cc.html", HTML)
    assert got["heading_lines"] == 2 and got["examples"][0]["before"].startswith("Art. 2043")
    with pytest.raises(SystemExit, match="AKN goes first"):
        probe_mod.probe(["XML", "AKN"], {"day": "1942-03-16", "form": "V"}, vigenza="2026-11-08",
                        out=tmp_path, match=None, run=_fake({}, []))
