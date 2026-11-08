"""LEGI's fixtures (tests/fixtures/legi/README.md): the real files, and the
HAND-MADE variants built from them here — each one a real file with named
fields replaced, so its every other byte is the origin's form."""
from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "legi"

#: real (Legilibre/legi-php @ a028bac0, files_exemples/)
ART_ABROGE = (FIX / "LEGIARTI000006398122.xml").read_bytes()      # ABROGE 1952-06-29 → 2001-01-01
ART_MODIFIE = (FIX / "LEGIARTI000019325262.xml").read_bytes()     # MODIFIE 2004-10-26 → 2007-11-09
SECTION = (FIX / "LEGISCTA000006135575.xml").read_bytes()
STRUCT = (FIX / "struct-LEGITEXT000006070666.xml").read_bytes()
VERSION = (FIX / "version-LEGITEXT000006070666.xml").read_bytes()

CMMI = "LEGITEXT000006070666"           # Code des instruments monétaires et des médailles
CASF = "LEGITEXT000006074069"           # Code de l'action sociale et des familles


def _pairs(ident: str) -> str:
    """DILA's folders from an id: `LEGIARTI000006398122` → `00/00/06/39/81`."""
    digits = ident[8:]
    return "/".join(digits[i:i + 2] for i in range(0, 10, 2))


def member(kind: str, ident: str, text: str = CMMI, folder: str = "code_en_vigueur") -> str:
    base = (f"legi/global/code_et_TNC_en_vigueur/{folder}/LEGI/TEXT/{_pairs(text)}/{text}/")
    if kind == "article":
        return f"{base}article/LEGI/ARTI/{_pairs(ident)}/{ident}.xml"
    if kind == "section_ta":
        return f"{base}section_ta/LEGI/SCTA/{_pairs(ident)}/{ident}.xml"
    if kind == "texte_version":
        return f"{base}texte/version/{ident}.xml"
    return f"{base}texte/struct/{ident}.xml"


#: the real files, at the places the baseline holds them
REAL = {
    member("article", "LEGIARTI000006398122"): ART_ABROGE,
    member("article", "LEGIARTI000019325262", CASF): ART_MODIFIE,
    member("section_ta", "LEGISCTA000006135575"): SECTION,
    member("textelr", CMMI): STRUCT,
    member("texte_version", CMMI): VERSION,
}


def tarball(path: Path, members: dict[str, bytes], *, mtime: int = 1_700_000_000,
            prefix: str = "") -> Path:
    """A tarball as DILA's: gzip, regular files; an increment's under its
    dated folder (`prefix`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            ti = tarfile.TarInfo(prefix + name)
            ti.size, ti.mtime = len(data), mtime
            tf.addfile(ti, io.BytesIO(data))
    return path


# ---- HAND-MADE variants (README: "hand-made") ------------------------------------------------

def article(ident: str, *, num: str = "1", etat: str = "VIGUEUR", debut: str = "1952-06-29",
            fin: str = "2999-01-01", versions: list[tuple[str, str, str, str]] | None = None,
            text: str = "La fabrication des monnaies est exécutée par voie de régie administrative.",
            cid: str = CMMI, title: str = "Code des instruments monétaires et des médailles",
            liens: bytes | None = None) -> bytes:
    """The real ABROGE article with its id, number, état, dates, versions
    (id, état, début, fin), text, text and LIENS replaced."""
    data = ART_ABROGE
    data = data.replace(b"LEGIARTI000006398122", ident.encode())
    data = re.sub(rb"<NUM>1</NUM>", b"<NUM>" + num.encode() + b"</NUM>", data)
    data = data.replace(b"<ETAT>ABROGE</ETAT>", b"<ETAT>" + etat.encode() + b"</ETAT>")
    data = data.replace(b"<DATE_DEBUT>1952-06-29</DATE_DEBUT>", b"<DATE_DEBUT>" + debut.encode() + b"</DATE_DEBUT>")
    data = data.replace(b"<DATE_FIN>2001-01-01</DATE_FIN>", b"<DATE_FIN>" + fin.encode() + b"</DATE_FIN>")
    rows = versions if versions is not None else [(ident, etat, debut, fin)]
    vxml = b"".join(
        f'<VERSION etat="{e}"><LIEN_ART debut="{d}" etat="{e}" fin="{f}" id="{i}" num="{num}" origine="LEGI"/></VERSION>'
        .encode() for i, e, d, f in rows)
    data = re.sub(rb"<VERSIONS>.*?</VERSIONS>", b"<VERSIONS>" + vxml + b"</VERSIONS>", data, flags=re.S)
    data = re.sub(rb"<BLOC_TEXTUEL>.*?</BLOC_TEXTUEL>",
                  b"<BLOC_TEXTUEL><CONTENU><p>" + text.encode() + b"</p></CONTENU></BLOC_TEXTUEL>", data, flags=re.S)
    data = data.replace(CMMI.encode(), cid.encode())
    data = data.replace("Code des instruments monétaires et des médailles".encode(), title.encode())
    if liens is not None:
        data = re.sub(rb"<LIENS>.*?</LIENS>", b"<LIENS>" + liens + b"</LIENS>", data, flags=re.S)
    return data


def texte_version(ident: str, title: str, *, cid: str | None = None, nature: str = "CODE",
                  num: str = "", nor: str = "", date_texte: str = "2999-01-01",
                  date_publi: str = "2999-01-01", etat: str = "VIGUEUR",
                  debut: str = "1952-06-29", visas: str = "") -> bytes:
    """The real TEXTE_VERSION with its ids, title, nature, number, NOR,
    dates, état and visas replaced."""
    data = VERSION
    data = data.replace(f"<ID>{CMMI}</ID>".encode(), f"<ID>{ident}</ID>".encode())
    data = data.replace(f"<CID>{CMMI}</CID>".encode(), f"<CID>{cid or ident}</CID>".encode())
    data = data.replace(b"<NATURE>CODE</NATURE>", f"<NATURE>{nature}</NATURE>".encode())
    data = data.replace(b"<NUM/>", f"<NUM>{num}</NUM>".encode() if num else b"<NUM/>")
    data = data.replace(b"<NOR/>", f"<NOR>{nor}</NOR>".encode() if nor else b"<NOR/>")
    data = data.replace(b"<DATE_PUBLI>2999-01-01</DATE_PUBLI>", f"<DATE_PUBLI>{date_publi}</DATE_PUBLI>".encode())
    data = data.replace(b"<DATE_TEXTE>2999-01-01</DATE_TEXTE>", f"<DATE_TEXTE>{date_texte}</DATE_TEXTE>".encode())
    data = data.replace("Code des instruments monétaires et des médailles".encode(), title.encode())
    data = data.replace(b"<ETAT>VIGUEUR</ETAT>", f"<ETAT>{etat}</ETAT>".encode())
    data = data.replace(b"<DATE_DEBUT>1952-06-29</DATE_DEBUT>", f"<DATE_DEBUT>{debut}</DATE_DEBUT>".encode())
    if visas:
        data = data.replace(b"<VISAS>\n\t<CONTENU/>", f"<VISAS>\n\t<CONTENU><p>{visas}</p></CONTENU>".encode(), 1)
    return data


#: MODIFIE_MORT_NE — the real MODIFIE annexe, its état replaced (hand-made)
ART_MORT_NE = ART_MODIFIE.replace(b"<ETAT>MODIFIE</ETAT>", b"<ETAT>MODIFIE_MORT_NE</ETAT>").replace(
    b'<VERSION etat="MODIFIE">\n<LIEN_ART debut="2004-10-26" etat="MODIFIE"',
    b'<VERSION etat="MODIFIE_MORT_NE">\n<LIEN_ART debut="2004-10-26" etat="MODIFIE_MORT_NE"')
#: a DATE_DEBUT of 2222-02-22 — the real ABROGE article, its start replaced (hand-made)
ART_PLACEHOLDER = ART_ABROGE.replace(b"<DATE_DEBUT>1952-06-29</DATE_DEBUT>",
                                     b"<DATE_DEBUT>2222-02-22</DATE_DEBUT>")
