"""LegalDocML.de as NeuRIS serves it → what the record keeps: the act read by
Normattiva's Akoma Ntoso reader (../normattiva/akn.py), and everything the
profile adds, read from the origin's own tree.

LegalDocML.de is an Akoma Ntoso PROFILE: the same elements — `act`,
`meta/identification` with FRBR Work / Expression / Manifestation,
`lifecycle`, `body` with `section`s and `article`s, `paragraph`s with their
`num` and `content` — under its own namespace,
`http://rechtsinformationen.bund.de/schema/norm/0.1`, where akn.py reads
OASIS's `…/akn/3.0`. So the structure and the text are akn.py's, unchanged:
the tree is handed to it with the namespace renamed (`_as_akn`), and it
returns the articles, their blocks, the preface, the preamble, the
conclusions and every `<ref>`. Nothing of akn.py is rewritten.

What akn.py does not know, read here from the ORIGINAL tree — never from the
renamed one, whose XML would carry a namespace the origin did not write:

  * the whole `meta` block, lossless (`node`): identification, lifecycle,
    `temporalData`, the editorial `notes`, and every `proprietary` block —
    NeuRIS's `ris:legalDocML.de_metadaten` (`abkuerzung`, `inkraft`,
    `ausserkraft`, `vollzitat`, `standangabe`, `bedingtesInkrafttreten`,
    `verweise`, `stand`, `doknr`, `textnachweis-geltung-ab`,
    `gegenstandslos`; norm-metadata.xsd 0.1) and the
    `regtxt:legalDocML.de_metadaten` (`typ`, `form`) — structured where the
    schema names the field, kept as a tree where it does not;
  * the periods: each `temporalGroup`'s interval resolved through the
    lifecycle's `eventRef` dates, and each article's `period` with it — the
    `Fassungen` an article has;
  * the preface's `shortTitle`, its `inline`s by their `refersTo`, its dates;
  * each article's own ids (`GUID`, `eId`, `refersTo`) and its `doknr`, the
    revision id every expression holding that unchanged article shares;
  * the `attachments`' `documentRef`s — the annexes are separate FILES
    (`anlage-regelungstext-N.xml`), each read by `parse_annex`.

An annex is an Akoma Ntoso `doc`, not an `act`, so akn.parse does not take
it; its `mainBody` is walked by akn.py's own block walker, and its title is
the docTitle's inlines (`anlageregelungstext-num`, `-bezug`).

Whitespace is normalised as akn.py normalises it (`akn.clean`); XML comments
are an editor's, not the act's, and are not read (invariant 11 (a)).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from ..normattiva import akn

LDML = "{http://rechtsinformationen.bund.de/schema/norm/0.1}"
RIS = "{http://rechtsinformationen.bund.de/schema/norm-metadata/0.1}"
REGTXT = "{http://MetadatenRegelungstext.LegalDocML.de/1.9/}"
_AKN_NS = akn.A[1:-1]
_LDML_NS = LDML[1:-1]


class LdmlError(ValueError):
    """The bytes are not a LegalDocML.de document this module can read."""


def local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def node(el) -> dict:
    """An element, lossless: its name, its attributes (local names), its own
    text and the text after each child (`tail`), and its children. Empty
    parts are left out."""
    d: dict = {"tag": local(el.tag)}
    if el.attrib:
        d["attrs"] = {local(k): v for k, v in el.attrib.items()}
    text = akn.clean(el.text or "")
    if text:
        d["text"] = text
    kids = [c for c in el if isinstance(c.tag, str)]
    if kids:
        d["children"] = [node(c) for c in kids]
    tail = akn.clean(el.tail or "")
    if tail:
        d["tail"] = tail
    return d


def text_of(el) -> str:
    return akn.clean(" ".join(el.itertext())).replace("\n", " ") if el is not None else ""


def _parse_xml(xml: bytes):
    try:
        return ET.fromstring(xml)
    except ET.ParseError as e:
        raise LdmlError(f"not well-formed XML: {e}") from e


def _as_akn(el):
    """A copy of the tree under OASIS's namespace, for akn.py."""
    out = copy.deepcopy(el)
    pre = "{" + _LDML_NS + "}"
    for e in out.iter():
        if isinstance(e.tag, str) and e.tag.startswith(pre):
            e.tag = "{" + _AKN_NS + "}" + e.tag[len(pre):]
    return out


# ---- meta -------------------------------------------------------------------------

def frbr_values(ident) -> dict:
    """`{work|expression|manifestation: {FRBRthis: …, FRBRuri: …, FRBRdate:
    {date, name}, FRBRalias: {name: value}, …}}` — every child's value by its
    element name; the lossless form is in `meta`."""
    out: dict = {}
    if ident is None:
        return out
    for level, key in (("FRBRWork", "work"), ("FRBRExpression", "expression"),
                       ("FRBRManifestation", "manifestation")):
        el = ident.find(LDML + level)
        if el is None:
            continue
        d: dict = {}
        for ch in el:
            t = local(ch.tag)
            if not t:
                continue
            if t == "FRBRalias":
                d.setdefault("FRBRalias", {})[ch.get("name") or ""] = ch.get("value") or ""
            elif t == "FRBRdate":
                d["FRBRdate"] = {"date": ch.get("date") or "", "name": ch.get("name") or ""}
            elif t == "FRBRauthor":
                d["FRBRauthor"] = ch.get("href") or ""
            elif t == "FRBRlanguage":
                d["FRBRlanguage"] = ch.get("language") or ""
            else:
                d[t] = ch.get("value") if ch.get("value") is not None else text_of(ch)
        out[key] = d
    return out


def lifecycle_events(meta) -> list[dict]:
    lc = meta.find(LDML + "lifecycle") if meta is not None else None
    return [{local(k): v for k, v in e.attrib.items()} for e in (lc if lc is not None else [])
            if isinstance(e.tag, str)]


def periods(meta) -> dict[str, dict]:
    """`{temporalGroup eId: {start, end}}` — each group's interval, its ends
    the lifecycle events it names (`#meta-n1_lebzykl-n1_ereignis-n2`),
    resolved to their dates; an end the group does not name is open (None)."""
    if meta is None:
        return {}
    dates = {e.get("eId"): e.get("date") for e in lifecycle_events(meta)}
    td = meta.find(LDML + "temporalData")
    out: dict[str, dict] = {}
    for g in (td if td is not None else []):
        if local(g.tag) != "temporalGroup":
            continue
        ivs = [iv for iv in g if local(iv.tag) == "timeInterval"]
        iv = ivs[0] if ivs else None
        start = (iv.get("start") or "").lstrip("#") if iv is not None else ""
        end = (iv.get("end") or "").lstrip("#") if iv is not None else ""
        out[g.get("eId") or ""] = {"start": dates.get(start) if start else None,
                                   "end": dates.get(end) if end else None}
    return out


def ris_metadata(meta) -> tuple[dict, dict, list]:
    """`(ris, regtxt, other)` from the `proprietary` blocks: NeuRIS's fields
    by the schema's names, the Regelungstext metadata, and every other block
    as a tree. A ris element the schema does not name is kept under
    `ris["other"]`."""
    ris: dict = {}
    regtxt: dict = {}
    other: list = []
    if meta is None:
        return ris, regtxt, other
    for prop in meta.findall(LDML + "proprietary"):
        for block in prop:
            if not isinstance(block.tag, str):
                continue
            if block.tag == RIS + "legalDocML.de_metadaten":
                for e in block:
                    if not isinstance(e.tag, str):
                        continue
                    t = local(e.tag)
                    if t == "abkuerzung":
                        ris["abkuerzung"] = {"value": text_of(e), "refersTo": e.get("refersTo") or ""}
                    elif t in ("inkraft", "ausserkraft", "textnachweis-geltung-ab"):
                        ris[t] = e.get("date") or ""
                    elif t in ("vollzitat", "stand"):
                        ris[t] = text_of(e)
                    elif t == "standangabe":
                        ris.setdefault("standangabe", []).append(
                            {"type": e.get("type") or "", "text": text_of(e)})
                    elif t in ("bedingtesInkrafttreten", "gegenstandslos"):
                        ris[t] = True
                    elif t == "doknr":
                        ris.setdefault("doknr", []).append(
                            {"source": e.get("source") or "", "value": text_of(e)})
                    elif t == "verweise":
                        ris["verweise"] = [verweis(v) for v in e if local(v.tag) == "verweis"]
                    else:
                        ris.setdefault("other", []).append(node(e))
            elif block.tag == REGTXT + "legalDocML.de_metadaten":
                for e in block:
                    if isinstance(e.tag, str):
                        regtxt[local(e.tag)] = text_of(e)
            else:
                other.append(node(block))
    return ris, regtxt, other


def verweis(v) -> dict:
    """One `ris:verweis`: its attributes (`sourceEId`, `targetWorkEli1`,
    `targetWorkEli2`) and its fields by name (`typ`, `bezugsnorm1Jurabk`,
    `bezugsnorm1Einzelnormbezeichnung`, …), each as written."""
    d: dict = {local(k): val for k, val in v.attrib.items()}
    for f in v:
        if isinstance(f.tag, str):
            d.setdefault(local(f.tag), text_of(f))
    return d


# ---- the main text ---------------------------------------------------------------------

@dataclass
class Regelungstext:
    act: akn.Act                         # structure and text, read by akn.py
    act_name: str
    meta: dict                           # <meta>, lossless (its own ids and every child)
    frbr: dict
    lifecycle: list
    periods: dict
    ris: dict
    regtxt: dict
    proprietary_other: list
    notes: list                          # the meta's editorial notes, text and placement
    articles: list                       # each article's own ids and period
    preface: dict
    attachment_refs: list
    analysis: list = field(default_factory=list)


def _preface(act_el) -> dict:
    pre = act_el.find(LDML + "preface")
    if pre is None:
        return {}
    out: dict = {"text": text_of(pre)}
    lt = pre.find(".//" + LDML + "longTitle")
    if lt is not None:
        out["long_title"] = text_of(lt)
    st = pre.find(".//" + LDML + "shortTitle")
    if st is not None:
        out["short_title"] = text_of(st)
    inl = [{"refersTo": e.get("refersTo") or "", "name": e.get("name") or "", "text": text_of(e)}
           for e in pre.iter(LDML + "inline")]
    if inl:
        out["inlines"] = inl
    dates = [{"refersTo": e.get("refersTo") or "", "date": e.get("date") or ""}
             for e in pre.iter(LDML + "date")]
    if dates:
        out["dates"] = dates
    return out


def _articles(act_el, doknr: dict[str, str], spans: dict) -> list[dict]:
    """Each `article` of the body, in reading order: its ids, its number and
    heading as written, its period resolved, its doknr. Matched to akn.py's
    articles by eId."""
    body = act_el.find(LDML + "body")
    out = []
    for a in (body.iter(LDML + "article") if body is not None else ()):
        eid = a.get("eId") or ""
        period = (a.get("period") or "").lstrip("#")
        num = a.find(LDML + "num")
        head = a.find(LDML + "heading")
        d = {"eId": eid, "GUID": a.get("GUID") or "", "num": text_of(num), "heading": text_of(head),
             "refersTo": a.get("refersTo") or "", "period": period}
        if period and period in spans:
            d["valid"] = spans[period]
        # by the article's eId; else by a source naming it under its
        # partition (`hauptteil-n1_abschnitt-n1_art-z1`), where only one does
        tails = [v for s, v in doknr.items() if s.endswith("_" + eid)]
        rev = doknr.get(eid) or (tails[0] if len(tails) == 1 else None)
        if rev:
            d["doknr"] = rev
        out.append({k: v for k, v in d.items() if v not in ("", None)})
    return out


def parse_main(xml: bytes) -> Regelungstext:
    root = _parse_xml(xml)
    act_el = root.find(LDML + "act")
    if act_el is None:
        raise LdmlError(f"no LegalDocML.de <act> under <{local(root.tag)}>")
    try:
        act = akn.parse(ET.tostring(_as_akn(root)))
    except akn.AknError as e:
        raise LdmlError(str(e)) from e
    meta = act_el.find(LDML + "meta")
    ris, regtxt, other = ris_metadata(meta)
    spans = periods(meta)
    doknr = {str(d["source"]).lstrip("#"): d["value"] for d in ris.get("doknr", [])}
    notes_el = meta.find(LDML + "notes") if meta is not None else None
    notes = [{**{local(k): v for k, v in n.attrib.items()}, "text": text_of(n)}
             for n in (notes_el if notes_el is not None else []) if local(n.tag) == "note"]
    refs = [{local(k): v for k, v in d.attrib.items()}
            for d in act_el.iter(LDML + "documentRef")]
    analysis = []
    if meta is not None:
        for an in meta.findall(LDML + "analysis"):
            analysis.append(node(an))
    return Regelungstext(
        act=act, act_name=act_el.get("name") or "",
        meta=node(meta) if meta is not None else {},
        frbr=frbr_values(meta.find(LDML + "identification") if meta is not None else None),
        lifecycle=lifecycle_events(meta), periods=spans, ris=ris, regtxt=regtxt,
        proprietary_other=other, notes=notes, articles=_articles(act_el, doknr, spans),
        preface=_preface(act_el), attachment_refs=refs, analysis=analysis)


# ---- an annex ----------------------------------------------------------------------------

@dataclass
class Anlage:
    member: str
    title: str
    doc_name: str
    meta: dict
    frbr: dict
    preface: dict
    blocks: list[akn.Block]
    notes: list
    cites: list


_ANNEX_NUM = re.compile(r"(\d+)\.xml$")


def kind_of(xml: bytes) -> str:
    """What a LegalDocML.de member IS, by its root's first child: `act` (a
    `regelungstext`), `doc` (an annex, `anlage-regelungstext-N`),
    `collection` (the `rechtsetzungsdokument`, `<documentCollection>`, which
    names the manifestation's components) — or `other`, any root the
    grammar does not name here, kept whole rather than refused."""
    root = _parse_xml(xml)
    for tag, kind in (("act", "act"), ("doc", "doc"), ("documentCollection", "collection")):
        if root.find(LDML + tag) is not None:
            return kind
    return "other"


def parse_collection(member: str, xml: bytes) -> dict:
    """The `rechtsetzungsdokument` (norm-rechtsetzungsdokument.xsd: akomaNtoso
    / documentCollection @name / meta + collectionBody / component+): its
    name, its meta lossless with the FRBR values read, and its components,
    each lossless (the `documentRef` naming each part of the manifestation).
    Found in the zip of every expression the node probed (München,
    2026-11-08: `rechtsetzungsdokument-1.xml` beside `regelungstext-1.xml`)."""
    root = _parse_xml(xml)
    dc = root.find(LDML + "documentCollection")
    if dc is None:
        raise LdmlError(f"{member}: no <documentCollection> under <{local(root.tag)}>")
    meta = dc.find(LDML + "meta")
    body = dc.find(LDML + "collectionBody")
    return {k: v for k, v in {
        "member": member, "name": dc.get("name") or "",
        "meta": node(meta) if meta is not None else {},
        "frbr": frbr_values(meta.find(LDML + "identification") if meta is not None else None),
        "components": [node(c) for c in (body if body is not None else []) if isinstance(c.tag, str)],
    }.items() if v not in (None, "", [], {})}


def parse_annex(member: str, xml: bytes) -> Anlage:
    root = _parse_xml(xml)
    doc = root.find(LDML + "doc")
    if doc is None:
        raise LdmlError(f"{member}: no LegalDocML.de <doc> under <{local(root.tag)}>")
    meta = doc.find(LDML + "meta")
    pre = doc.find(LDML + "preface")
    inlines = [text_of(e) for e in (pre.iter(LDML + "inline") if pre is not None else ())]
    title_el = pre.find(".//" + LDML + "docTitle") if pre is not None else None
    title = " ".join(x for x in inlines if x) or text_of(title_el)
    cites: list = []
    ctx = akn._Ctx(cites, set())
    blocks: list[akn.Block] = []
    main = doc.find(LDML + "mainBody")
    if main is not None:
        akn._walk(_as_akn(main), 0, ctx, blocks)
    notes_el = meta.find(LDML + "notes") if meta is not None else None
    notes = [{**{local(k): v for k, v in n.attrib.items()}, "text": text_of(n)}
             for n in (notes_el if notes_el is not None else []) if local(n.tag) == "note"]
    return Anlage(
        member=member, title=title, doc_name=doc.get("name") or "",
        meta=node(meta) if meta is not None else {},
        frbr=frbr_values(meta.find(LDML + "identification") if meta is not None else None),
        preface=_preface(doc), blocks=blocks, notes=notes + [{"inline": n} for n in ctx.notes],
        cites=cites)


def annex_order(member: str) -> tuple[int, str]:
    m = _ANNEX_NUM.search(member)
    return (int(m.group(1)) if m else 0, member)
