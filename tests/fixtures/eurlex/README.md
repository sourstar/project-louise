# EUR-Lex fixtures — provenance

Real files, byte for byte as their repositories hold them (sha256 below).
EU law: © European Union, CC BY 4.0 (texts), CC0 (metadata) — Decision
2011/833/EU, EUR-Lex legal notice. `formex/` and `notices/` were not
fetched from europa.eu (not reachable from the session that took them,
2026-11-08); each comes from a repository that holds the Publications
Office's files. `rdf/`, `xhtml/`, `html/` and `pdf/` were fetched from
Cellar on 2026-11-13 from the node, as the next section says.

## Fetched from Cellar, 2026-11-13 (`rdf/`, `xhtml/`, `html/`, `pdf/`)

`https://publications.europa.eu/resource/celex/<CELEX>`, by content
negotiation; items from the URI each `MANIFESTATION_HAS_ITEM` names, saved
under its `TECHMD/STREAM_NAME` (every size equal to its `STREAM_SIZE`).

| path | Accept | what |
|---|---|---|
| `rdf/<CELEX>.rdf` | `application/rdf+xml;notice=non-inferred-tree` | the work's NON-INFERRED TREE in RDF/XML, all languages: the graph the EUR-Lex Data Dump's metadata archive (`LEG_MTD_<date>.zip`, `<cellar uuid>/tree_non_inferred.rdf`) holds. For 31990R0048 the dump's file (dump of 2026-11-08) and this one are the same graph — 2,115 triples, isomorphic, identical sorted lines — in another order, so the sha256 differs: Cellar's serialisation order is not stable. `31990R0048`, `32016R1036`, `02016R1036-20180608` (a consolidation), `32019L0790` |
| `xhtml/32010D0699/` | `application/xml;notice=branch`, Accept-Language `ita` | an Italian act in force WITHOUT a `fmx4` manifestation: its branch notice (manifestations `print`, `pdfa1a`, `xhtml`) and the two items of its `xhtml` manifestation. `Accept: application/xhtml+xml` on the CELEX answers 300 with the two `DOC_n` items |
| `html/31988D0540/` | same | an act whose only text is the LEGACY `html` manifestation: branch notice and `31988D0540it.html`. `Accept: text/html` answers 200 with the item |
| `pdf/32004D0411/` | same | an act held only as `pdf` (with a text layer): branch notice and `l_15120040430it00500053.pdf`. `Accept: application/pdf` answers 200 |
| `pdf/31962R0031/` | same | an act held only as a SCANNED `pdf` (no text layer, 2 pages): branch notice and `p_04519620614it13851386.pdf` |

In the 2026-11-08 dump, 13,374 of the 43,623 Italian works in force have no
Formex (12,360 html/xhtml + pdf, 786 html only, 228 pdf only — 5 of them
scans); these four acts stand for them.

## Formex (`formex/<CELEX>/`)

| directory | repository @ commit | path | what |
|---|---|---|---|
| `32016R1036` | `eur-lex-rs/eur-lex` @ `6e3db12c2c08bc4d9dfb0aa82ec6dd99a0649667` | `data/32016R1036/` | Regulation (EU) 2016/1036, the OJ act: body `L_2016176EN.01002101.xml`, wrapper `.doc.xml`, annexes `L_2016176EN.01005001.xml`, `L_2016176EN.01005101.xml` (Formex 4) |
| `02016R1036-20180608` | same | `data/02016R1036-20180608/` | its consolidated version of 2018-06-08: `CL2016R1036EN0020010.0001.xml` (CONS.ACT, with the `CLG.MDFO`/`MDFC` amendment marks) and `.doc.xml` |
| `32019L0790` | same | `data/32019L0790/` | Directive (EU) 2019/790: an act with DIVISIONs (titles, chapters) |
| `32024R0903` | `AlessioNar/tulit` @ `a082efe` (EUPL-1.2 repository; the files are the EU's) | `tests/data/sources/eu/eurlex/formex/c008bcb6-e7ec-11ee-9ea8-01aa75ed71a1.0006.02/DOC_1/` | Regulation (EU) 2024/903, Formex 6 names (`….doc.fmx.xml`, `.000101.fmx.xml`), a `toc` item the wrapper names in PUBLICATION.REF |

All English: no other language's Formex file was found on GitHub. The
slicing tests place a real file under another `lang=` to check that the
language is read from the origin (`LG.DOC`) and a mismatch is refused.

## CDM notices (`notices/`)

From `op-cellar-git/eurlex-xml-format` @ `a4b891042e1cfe4d43617d994da50b02e9571228`:

| file | path | what |
|---|---|---|
| `52018PC0340_branch.xml` | `new/samples/original_notices/` | a BRANCH notice as Cellar serves it (the "current" format): NOTICE/WORK, INVERSE, the branch's EXPRESSION and its MANIFESTATIONs with their items (`TECHMD/STREAM_NAME`) — a Commission proposal, not an act |
| `EN-CELEX-02009R1223-20191218.xml` | `new/samples/` | a branch notice of a CONSOLIDATED version (`ACT_CONSOLIDATED_BASED_ON_RESOURCE_LEGAL`, the ELI of the version). Its first line is an editor's banner — `<!-- edited with XMLSpy v2019 rel. 3 sp1 … by DG OP -->` — a HAND-EDITED sample: its expressions sit inside WORK with `display="true"`, where Cellar's own notices put the branch's EXPRESSION beside WORK |
| `celex_32018R0196_object_work.xml` | `new/samples/` | an OBJECT notice of Regulation (EU) 2018/196's work, hand-edited (the same XMLSpy banner) |

## What is not here, and how the tests stand in for it

No repository found holds a notice and the Formex of the SAME act. The
end-to-end tests (`tests/test_eurlex.py`) put a unit together from the real
Formex above and the real consolidated notice re-keyed in the test to the
Formex's act (`_notice_for`: CELEX, ELI, base act and language replaced,
every other byte the Office's), and say so. The field-by-field tests run on
the files unmodified. The Formex manifestation's 300 Multiple Choices body
and the SPARQL answers are served by a local mock server in
`tests/acquire/test_cellar.py`, in the shapes legal-fr-de-eu.md §7.1 and
the SPARQL 1.1 JSON results format (W3C) describe.

## sha256

```
fee1e0b7f9561bfa834577a716f6fe30aeddba169e9ed21a4ef158ef39fe2530  formex/02016R1036-20180608/CL2016R1036EN0020010.0001.doc.xml
c042df02b1a5fa6077ec50faa183723ad7a5b37e3c16485462d103e37deec1a2  formex/02016R1036-20180608/CL2016R1036EN0020010.0001.xml
e38ef80158962aa9271f9e242051b5faade7e5eb2fa1e16ffd7d7d68e91a8187  formex/32016R1036/L_2016176EN.01002101.doc.xml
b8b1d225225ccf69855c14bd36fae9da623cc62d82f0bc43c1c594744195e8cf  formex/32016R1036/L_2016176EN.01002101.xml
1d1531e7a78387e2c12fb4f56a0637067aa2a121ca6915d67b0465126cdee52e  formex/32016R1036/L_2016176EN.01005001.xml
b21f274ebab0c0d6788d83aeb6fe13e1dda94012cae921e890035308bdcba627  formex/32016R1036/L_2016176EN.01005101.xml
61dbbc77bf1dec17150ba52284036ce391f1ae2ea91f4deced2697602d8ce11d  formex/32019L0790/L_2019130EN.01009201.doc.xml
7a7d46524b1076e0227937c026a74d87101724fc31c7ce310052ad8d79ff6bdf  formex/32019L0790/L_2019130EN.01009201.xml
f3ea062af02dd305ef0c2df5b0482ae1135cacb89bd68550d29df0f3773ca93b  formex/32024R0903/L_202400903EN.000101.fmx.xml
59974b27a2120f725e6c89d1643110993fee64cce39dc55a823357340b6c4e3b  formex/32024R0903/L_202400903EN.002601.fmx.xml
f7dda2e5b9e96d0cb268b34b37d75e6b787c253fd8203bf23f90c8750e474a2f  formex/32024R0903/L_202400903EN.doc.fmx.xml
d5fb5cf89aa31b8aabb99d1f1352199eeae17179436d894c2b5e7605137924f0  formex/32024R0903/L_202400903EN.toc.fmx.xml
4186fd60516cec188642db27955dfa00cc536c0f166cc2a7e664ec7393d4320c  notices/52018PC0340_branch.xml
93759a7e276c6e423a605cd7f463b2738f787ae4db85d46a2f0179bcd507f9e4  notices/EN-CELEX-02009R1223-20191218.xml
c4807f23b9becda7ae45a2fc01203861b23228a431f868b48b588103e130fdc2  notices/celex_32018R0196_object_work.xml
283a39314546faf63a1ea698531e311256d8b3897826f61e615cbfe88d973928  html/31988D0540/31988D0540it.html
ab8d3846bfb3780579fec1d3c1f13d506374b3e8d24db010c69f2cb552ee1276  html/31988D0540/notice.xml
0962c885ca57e5d9e8413b48f1b31586d077879f5d6c874b504f4cc31f663c60  pdf/31962R0031/notice.xml
ce3908d50e62fcf68c7836b50aaa256da143bc4c5332fa356a5da367ad57c5ad  pdf/31962R0031/p_04519620614it13851386.pdf
50b32c2216ed25675a06bb3b1e4cf754bd1b30dff74190653cf11e8c3a36590c  pdf/32004D0411/l_15120040430it00500053.pdf
9d207f441331c8337c3701c0bafdfd2d969529a7f124a6b752f4363ec1857494  pdf/32004D0411/notice.xml
f2855f459c0b62618afbeb81547269cbac5c222f5a402a8c87235a77c1b60039  rdf/02016R1036-20180608.rdf
4daaaa841d80204038a9073fe4c671aaa05811a2407739152a992f8345d2a1ed  rdf/31990R0048.rdf
8e2506879cc7aa958d346f4fa03ee031808d96a7cd4e1c8949cc8a2c3f682816  rdf/32016R1036.rdf
1af12068534ea4bcd12f35c3254a05613ea769756bb45e434a3e92f2dfaf1d1a  rdf/32019L0790.rdf
8e7a008f18b5454bbbac0a872c6168ba1b7cb3f7ac80ae20efa7e8c531a3223d  xhtml/32010D0699/L_2010306IT.01001401.doc.html
e24d253b2e692fdeb6cd426b357d012c20fa97bb30378f1e2d6a5259a888de6c  xhtml/32010D0699/L_2010306IT.01001501.doc.html
f0c413c49c5c3d3bb9cc248367a2b634ac3d06253b4b9dea9541837aceaab20a  xhtml/32010D0699/notice.xml
```
