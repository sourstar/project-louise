"""A small it.wikisource multistream run for the Wikisource tests: the
header stream with <siteinfo>, page streams of a few pages each, and the
index `offset:page_id:title` — the shape Wikimedia publishes — with the
run's `categorylinks`, `linktarget` and `page_props` SQL dumps beside it,
written as dumps.wikimedia.org writes them since 20261001 (MariaDB's header
and CREATE TABLE as itwikisource-20261101's, then one tuple a line) or, with
`layout="statement"`, as the runs before (one INSERT a line)."""
from __future__ import annotations

import bz2
import gzip
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


#: (page, category title as linktarget keys it, its lt_id, cl_sortkey_prefix):
#: the work page's own, a subpage's (a template's: not in its text), a scan
#: page's (ProofreadPage's quality tracking), the author's
LINKS = [(31, "Autobiografie", 1001, "Vita"), (31, "Testi_del_XVI_secolo", 1002, "Vita"),
         (32, "Testi_in_cui_è_citato_Dante_Alighieri", 1003, ""), (21, "Pagine_SAL_100%", 1004, ""),
         (41, "Autori_del_XVI_secolo", 1005, "Cellini, Benvenuto")]
#: (page, property, value): the work's Wikidata item and the author's
PROPS = [(31, "wikibase_item", "Q900031"), (31, "defaultsort", "Vita"), (32, "wikibase_item", "Q900032"),
         (41, "wikibase_item", "Q900041"), (41, "page_image_free", "Cellini.jpg")]
#: linktarget's other rows (not categories): read, never packed
OTHER_TARGETS = [(7, 0, "Vita"), (8, 102, "Benvenuto_Cellini")]

_HEAD = b"""/*M!999999\\- enable the sandbox mode */ 
-- MariaDB dump 10.19  Distrib 10.11.19-MariaDB, for debian-linux-gnu (x86_64)
--
-- Host: dbstore1007.eqiad.wmnet    Database: itwikisource
-- ------------------------------------------------------
-- Server version\t10.11.13-MariaDB-log

/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET TIME_ZONE='+00:00' */;

--
-- Table structure for table `%(t)s`
--

DROP TABLE IF EXISTS `%(t)s`;
CREATE TABLE `%(t)s` (
%(cols)s
) ENGINE=InnoDB DEFAULT CHARSET=binary ROW_FORMAT=COMPRESSED;

--
-- Dumping data for table `%(t)s`
--

/*!40000 ALTER TABLE `%(t)s` DISABLE KEYS */;
"""
_TAIL = b"/*!40000 ALTER TABLE `%(t)s` ENABLE KEYS */;\n"
CREATE = {
    "categorylinks": ("  `cl_from` int(8) unsigned NOT NULL DEFAULT 0,\n"
                      "  `cl_sortkey` varbinary(230) NOT NULL DEFAULT '',\n"
                      "  `cl_timestamp` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),\n"
                      "  `cl_sortkey_prefix` varbinary(255) NOT NULL DEFAULT '',\n"
                      "  `cl_type` enum('page','subcat','file') NOT NULL DEFAULT 'page',\n"
                      "  `cl_collation_id` smallint(5) unsigned NOT NULL DEFAULT 0,\n"
                      "  `cl_target_id` bigint(20) unsigned NOT NULL,\n"
                      "  PRIMARY KEY (`cl_from`,`cl_target_id`),\n"
                      "  KEY `cl_sortkey_id` (`cl_target_id`,`cl_type`,`cl_sortkey`,`cl_from`)"),
    "linktarget": ("  `lt_id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,\n"
                   "  `lt_namespace` int(11) NOT NULL,\n"
                   "  `lt_title` varbinary(255) NOT NULL,\n"
                   "  PRIMARY KEY (`lt_id`),\n"
                   "  UNIQUE KEY `lt_namespace_title` (`lt_namespace`,`lt_title`)"),
    "page_props": ("  `pp_page` int(10) unsigned NOT NULL,\n"
                   "  `pp_propname` varbinary(60) NOT NULL DEFAULT '',\n"
                   "  `pp_value` blob NOT NULL,\n"
                   "  `pp_sortkey` float DEFAULT NULL,\n"
                   "  PRIMARY KEY (`pp_page`,`pp_propname`)"),
}


def q(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def sql(table: str, tuples: list[str], *, layout: str = "lines", per_statement: int = 2,
        create: str | None = None) -> bytes:
    """A table's dump: the header, its INSERT statements of `per_statement`
    tuples each in `layout`, the footer."""
    body = ""
    for i in range(0, len(tuples), per_statement):
        part = tuples[i:i + per_statement]
        if layout == "lines":
            body += f"INSERT INTO `{table}` VALUES\n" + ",\n".join(part) + ";\n"
        else:
            body += f"INSERT INTO `{table}` VALUES " + ",".join(part) + ";\n"
    cols = (create or CREATE[table]).encode()
    return (_HEAD % {b"t": table.encode(), b"cols": cols}) + body.encode() + (_TAIL % {b"t": table.encode()})


def write_tables(dump: Path, *, links=LINKS, props=PROPS, other_targets=OTHER_TARGETS, layout: str = "lines",
                 per_statement: int = 2, timestamp: str = "2025-02-17 16:21:49") -> None:
    """The run's three tables beside `dump`, each in its primary key's order
    — linktarget in (namespace, title) order, as the dump writes it."""
    prefix = dump.name.split("-pages-articles")[0]
    cl = [f"({p},{q((pre + chr(10) + t).upper() if pre else t.upper())},{q(timestamp)},{q(pre)},'page',1,{lt})"
          for p, t, lt, pre in sorted(links, key=lambda r: (r[0], r[2]))]
    targets = {(lt, 14, t) for _p, t, lt, _pre in links} | set(other_targets)
    lt_rows = [f"({i},{ns},{q(t)})" for i, ns, t in sorted(targets, key=lambda r: (r[1], r[2]))]
    pp = [f"({p},{q(k)},{q(v)},NULL)" for p, k, v in sorted(props, key=lambda r: (r[0], r[1]))]
    for table, rows in (("categorylinks", cl), ("linktarget", lt_rows), ("page_props", pp)):
        (dump.parent / f"{prefix}-{table}.sql.gz").write_bytes(
            gzip.compress(sql(table, rows, layout=layout, per_statement=per_statement)))


def write_run(root: Path, *, run: str = "20261101", per_stream: int = 3, pages=PAGES, tables: bool = True,
              **table_kw) -> Path:
    """`<root>/itwikisource/<run>/itwikisource-<run>-pages-articles-multistream.xml.bz2`,
    its index and (`tables`) the run's three tables; returns the dump."""
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
    if tables:
        write_tables(dump, **table_kw)
    return dump
