"""A small it.wikisource multistream run for the Wikisource tests: the
header stream with <siteinfo>, page streams of a few pages each, and the
index `offset:page_id:title` — the shape Wikimedia publishes."""
from __future__ import annotations

import bz2
from pathlib import Path

NS = "http://www.mediawiki.org/xml/export-0.11/"
SITEINFO = (f'<mediawiki xmlns="{NS}" version="0.11" xml:lang="it"><siteinfo><sitename>Wikisource</sitename>'
            '<dbname>itwikisource</dbname><namespaces>'
            '<namespace key="0" case="first-letter" /><namespace key="14" case="first-letter">Categoria</namespace>'
            '<namespace key="102" case="first-letter">Autore</namespace>'
            '<namespace key="108" case="first-letter">Pagina</namespace>'
            '<namespace key="110" case="first-letter">Indice</namespace></namespaces></siteinfo>\n')


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def page(pid, ns, title, text, *, model="wikitext", sha1=None, redirect=None):
    red = f'<redirect title="{redirect}" />' if redirect else ""
    return (f"  <page>\n    <title>{title}</title>\n    <ns>{ns}</ns>\n    <id>{pid}</id>\n{red}"
            f"    <revision>\n      <id>{pid * 10}</id>\n      <model>{model}</model>\n"
            f"      <format>text/x-wiki</format>\n"
            f'      <text bytes="{len(text)}" xml:space="preserve">{esc(text)}</text>\n'
            f"      <sha1>{sha1 or f's{pid}'}</sha1>\n    </revision>\n  </page>\n")


def scan(n, level, body):
    return f'<noinclude><pagequality level="{level}" user="Tizio" />{{{{RigaIntestazione}}}}</noinclude>{body}' \
           f"<noinclude>{{{{nota a piè}}}}</noinclude>"


PAGES = [
    page(11, 110, "Indice:Vita.djvu", "{{:MediaWiki:Proofreadpage_index_template|Autore=Benvenuto Cellini|Anno=1829}}",
         model="proofread-index"),
    page(21, 108, "Pagina:Vita.djvu/5", scan(5, 4, "Io Benvenuto"), model="proofread-page"),
    page(22, 108, "Pagina:Vita.djvu/6", scan(6, 3, "nacqui in Fiorenza."), model="proofread-page"),
    page(23, 108, "Pagina:Vita.djvu/7", scan(7, 4, "Libro secondo."), model="proofread-page"),
    page(31, 0, "Vita", "{{Intestazione\n| Nome e cognome dell'autore = Benvenuto Cellini\n| Titolo = Vita\n"
                        "| Anno di pubblicazione = 1728\n}}\n{{Indice sommario|nome=Libro primo|titolo=Libro primo}}\n"
                        "{{Indice sommario|nome=Libro secondo|titolo=Libro secondo}}\n[[Categoria:Autobiografie]]"),
    page(32, 0, "Vita/Libro primo", '<pages index="Vita.djvu" from=5 to=6 />'),
    page(33, 0, "Vita/Libro secondo", '<pages index="Vita.djvu" from=7 />'),
    page(41, 102, "Autore:Benvenuto Cellini", "{{Autore\n| Nome = Benvenuto\n| Cognome = Cellini\n"
                                             "| Anno di nascita = 1500\n| Anno di morte = 1571\n}}"),
    page(51, 0, "Cellini", "", redirect="Autore:Benvenuto Cellini"),
]


def write_run(root: Path, *, run: str = "20261101", per_stream: int = 3, pages=PAGES) -> Path:
    """`<root>/itwikisource/<run>/itwikisource-<run>-pages-articles-multistream.xml.bz2`
    and its index; returns the dump."""
    d = root / "itwikisource" / run
    d.mkdir(parents=True, exist_ok=True)
    dump = d / f"itwikisource-{run}-pages-articles-multistream.xml.bz2"
    index = d / f"itwikisource-{run}-pages-articles-multistream-index.txt.bz2"
    blob, lines = bytearray(bz2.compress(SITEINFO.encode())), []
    for i in range(0, len(pages), per_stream):
        at = len(blob)
        group = pages[i:i + per_stream]
        for p in group:
            pid = p.split("<id>", 1)[1].split("</id>", 1)[0]
            title = p.split("<title>", 1)[1].split("</title>", 1)[0]
            lines.append(f"{at}:{pid}:{title}")
        blob += bz2.compress("".join(group).encode())
    blob += bz2.compress(b"</mediawiki>\n")
    dump.write_bytes(bytes(blob))
    index.write_bytes(bz2.compress(("\n".join(lines) + "\n").encode()))
    return dump
