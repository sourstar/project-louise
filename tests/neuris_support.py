"""The NeuRIS test corpus (tests/fixtures/neuris/README.md): the portal's
LegalDocML.de fixtures, manifestation zips built from them, and the API's
JSON in the openapi's shape, its values read from the XML beside it."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from app.plugins.neuris import ldml

FIX = Path(__file__).parent / "fixtures" / "neuris"
HANDMADE = FIX / "handmade" / "regelungstext-1.xml"

S1016 = "eli/bund/bgbl-1/2000/s1016/2023-04-26/10/deu"
S1234 = ("eli/bund/bgbl-1/2020/s1234/2020-01-01/1/deu", "eli/bund/bgbl-1/2020/s1234/2021-01-01/1/deu",
         "eli/bund/bgbl-1/2020/s1234/2022-01-01/1/deu")
S1234_FUTURE = "eli/bund/bgbl-1/2020/s1234/2920-01-01/1/deu"
N130 = "eli/bund/bgbl-1/2025/130/2025-05-05/1/deu"
N999 = "eli/bund/bgbl-1/2025/999/2025-01-01/1/deu"
S101 = "eli/bund/bgbl-1/1991/s101/1991-01-01/1/deu"


def manifestations() -> list[tuple[str, str, Path]]:
    """`(expression ELI, generation date, directory)` of every fixture."""
    out = []
    for main in sorted(FIX.glob("eli/**/regelungstext-1.xml")):
        d = main.parent
        out.append(("/".join(d.relative_to(FIX).parts[:8]), d.name, d))
    return out


def directory(expression: str) -> tuple[str, Path]:
    for e, generated, d in manifestations():
        if e == expression:
            return generated, d
    raise KeyError(expression)


def zip_bytes(expression: str, *, files: dict[str, bytes] | None = None, reverse: bool = False,
              compression: int = zipfile.ZIP_DEFLATED, date_time=(2026, 11, 8, 12, 0, 0),
              drop: tuple[str, ...] = ()) -> bytes:
    """The manifestation zip as the portal builds it: one member per file of
    the manifestation, named by its ELI path (the S3 key)."""
    generated, d = directory(expression)
    members = files if files is not None else {p.name: p.read_bytes() for p in sorted(d.iterdir())}
    names = sorted(n for n in members if n not in drop)
    if reverse:
        names.reverse()
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for n in names:
            info = zipfile.ZipInfo(f"{expression}/{generated}/{n}", date_time=date_time)
            info.compress_type = compression
            zf.writestr(info, members[n])
    return buf.getvalue()


def api_json(expression: str, *, main: bytes | None = None, **over) -> dict:
    """The per-expression answer (`LegislationExpressionSchema`), its values
    from the XML: names from the preface, dates from the FRBR and the
    lifecycle, validity from `ris:inkraft`/`ris:ausserkraft`."""
    generated, d = directory(expression)
    r = ldml.parse_main(main if main is not None else (d / "regelungstext-1.xml").read_bytes())
    work = "/".join(expression.split("/")[:5])
    inl = {i["refersTo"]: i["text"] for i in r.preface.get("inlines", [])}
    ris_abk = (r.ris.get("abkuerzung") or {}).get("value", "")
    ausf = next((e.get("date") for e in r.lifecycle if e.get("refersTo") == "ausfertigung"), None)
    start, end = r.ris.get("inkraft"), r.ris.get("ausserkraft")
    base = f"/v1/legislation/{expression}/{generated}"
    out = {
        "@context": "https://schema.org", "@type": "Legislation", "@id": f"/v1/legislation/{expression}",
        "name": r.act.doc_title, "abbreviation": inl.get("amtliche-abkuerzung") or ris_abk,
        "risAbbreviation": ris_abk, "alternateName": r.preface.get("short_title"),
        "exampleOfWork": {"@type": "Legislation", "@id": f"/v1/legislation/{work}",
                          "legislationIdentifier": work, "legislationDate": ausf,
                          "datePublished": (r.frbr.get("work", {}).get("FRBRdate") or {}).get("date"),
                          "isPartOf": {"@type": "PublicationIssue",
                                       "name": f"BGBl I {expression.split('/')[3]}, {expression.split('/')[4]}"}},
        "legislationIdentifier": expression,
        "temporalCoverage": f"{start or '..'}/{end or '..'}",
        "legislationLegalForce": "InForce",
        "hasPart": [{"@type": "Legislation", "@id": f"{base}/regelungstext-1.xml#{a['eId']}", "eId": a["eId"],
                     "name": a.get("num", ""), "headline": a.get("heading", ""),
                     "temporalCoverage": f"{start or '..'}/..", "partType": "article",
                     **({"revision": a["doknr"]} if a.get("doknr") else {})} for a in r.articles],
        "encoding": [
            {"@type": "LegislationObject", "@id": f"{base}/regelungstext-1/html",
             "contentUrl": f"{base}/regelungstext-1.html", "encodingFormat": "text/html", "inLanguage": "de"},
            {"@type": "LegislationObject", "@id": f"{base}/regelungstext-1/xml",
             "contentUrl": f"{base}/regelungstext-1.xml", "encodingFormat": "application/xml", "inLanguage": "de"},
            {"@type": "LegislationObject", "@id": f"{base}/zip",
             "contentUrl": f"{base}.zip", "encodingFormat": "application/zip", "inLanguage": "de"}],
    }
    out.update(over)
    return {k: v for k, v in out.items() if v is not None}


def listing_item(expression: str, **over) -> dict:
    """The listing's item (`LegislationExpressionSearchSchema`): the answer
    without the parts and `risAbbreviation`."""
    a = api_json(expression, **over)
    return {k: v for k, v in a.items() if k not in ("hasPart", "risAbbreviation", "@context")}


def lay_out(root: Path, expression: str, *, data: bytes | None = None, api: dict | bytes | None = None,
            generated: str | None = None) -> Path:
    """The pair as acquisition stores it: `<eli>/<generated>.zip` and `.json`."""
    gen = generated or directory(expression)[0]
    zpath = root / expression / f"{gen}.zip"
    zpath.parent.mkdir(parents=True, exist_ok=True)
    zpath.write_bytes(data if data is not None else zip_bytes(expression))
    body = api if api is not None else api_json(expression)
    zpath.with_suffix(".json").write_bytes(body if isinstance(body, bytes)
                                           else json.dumps(body, ensure_ascii=False).encode("utf-8"))
    return zpath


def corpus(root: Path, expressions=None) -> Path:
    for e, _, _ in manifestations():
        if expressions is None or e in expressions:
            lay_out(root, e)
    return root


def text_words(xml: bytes, part: str) -> list[str]:
    """Every word of the body (`body`) or the annex's main body (`mainBody`)."""
    root = ET.fromstring(xml)
    el = next(e for e in root.iter() if e.tag == ldml.LDML + part)
    return " ".join(el.itertext()).split()
