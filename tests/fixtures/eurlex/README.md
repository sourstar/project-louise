# EUR-Lex fixtures — provenance

Real files, byte for byte as their repositories hold them (sha256 below).
EU law: © European Union, CC BY 4.0 (texts), CC0 (metadata) — Decision
2011/833/EU, EUR-Lex legal notice. None was fetched from europa.eu (not
reachable from the session that took them, 2026-11-08); each comes from a
repository that holds the Publications Office's files.

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
```
