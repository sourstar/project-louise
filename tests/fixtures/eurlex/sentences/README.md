# Real sentences in the 24 languages — provenance

For `tests/test_chunking_legal_v4.py`: the goldens of `legal_article` v4's
sentence level, one real paragraph per case, in every EU language
(docs/notes/legal-chunker-v4-abbreviations.md). Before these, the repository
held no body text in bg, el, es, fi, fr, ga, hr, nl, pl, pt, sk, sl or sv,
and only titles in most of the others.

Fetched from Cellar on 2026-11-21, as `../README.md` says: content
negotiation on `https://publications.europa.eu/resource/celex/<CELEX>` with
`Accept: application/xhtml+xml` and `Accept-Language: <3-letter code>`
(answered 303), then the item over https. Then TRIMMED to whole lines: the
head up to `<body>`, the `<p>` elements the tests read, the closing tags —
every kept byte as Cellar served it (CONVEX XHTML, one element a line; the
result is not well formed, the plugin's reader does not need it to be).
Reuse: © European Union, CC BY 4.0, Commission Decision 2011/833/EU.

`<CELEX><lang>.html`, from the item named (cellar
`<uuid>.<expression>.<manifestation>/DOC_n`, the sha256 of the item as
served, its size):

| file | item |
|---|---|
| `32016R0679<lang>.html`, the 24 | the GDPR: recital 1, recital 17, footnote 6 (and, where said, more) |
| `32016R0679bg.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0001.03/DOC_1` (`f46af448…`, 1,116,211 bytes) |
| `32016R0679cs.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0002.03/DOC_1` (`a1c44265…`, 809,143 bytes) |
| `32016R0679da.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0003.03/DOC_1` (`cbf44600…`, 806,795 bytes); also Article 8(3), "Stk. 1 berører …" |
| `32016R0679de.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0004.03/DOC_1` (`219040a0…`, 862,613 bytes); also recital 25, "z. B." |
| `32016R0679el.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0005.03/DOC_1` (`daf6524b…`, 1,173,959 bytes) |
| `32016R0679en.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0006.03/DOC_1` (`962539af…`, 806,864 bytes) |
| `32016R0679es.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0023.03/DOC_1` (`7e6f4309…`, 831,331 bytes) |
| `32016R0679et.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0007.03/DOC_1` (`bddb41e1…`, 783,432 bytes) |
| `32016R0679fi.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0008.03/DOC_1` (`00f162b7…`, 834,832 bytes) |
| `32016R0679fr.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0009.03/DOC_1` (`27a2248c…`, 876,858 bytes) |
| `32016R0679ga.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0010.03/DOC_1` (`7913fb84…`, 878,714 bytes) |
| `32016R0679hr.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0011.03/DOC_1` (`a6a85238…`, 781,723 bytes) |
| `32016R0679hu.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0012.03/DOC_1` (`e0ab0287…`, 848,842 bytes) |
| `32016R0679it.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0013.03/DOC_1` (`52dbf00e…`, 823,981 bytes) |
| `32016R0679lt.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0015.03/DOC_1` (`34dab912…`, 804,576 bytes) |
| `32016R0679lv.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0014.03/DOC_1` (`ee20f9f4…`, 808,492 bytes) |
| `32016R0679mt.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0016.03/DOC_1` (`905b18fd…`, 879,632 bytes) |
| `32016R0679nl.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0017.03/DOC_1` (`47da4f06…`, 836,229 bytes) |
| `32016R0679pl.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0018.03/DOC_1` (`4b44ea95…`, 840,814 bytes); also recital 139, "ds.", and recital 172, "r. (17)" |
| `32016R0679pt.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0019.03/DOC_1` (`0ebcc5c1…`, 871,148 bytes) |
| `32016R0679ro.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0020.03/DOC_1` (`bb84c6c7…`, 873,690 bytes) |
| `32016R0679sk.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0021.03/DOC_1` (`8bfbd4a3…`, 814,865 bytes) |
| `32016R0679sl.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0022.03/DOC_1` (`f658fc91…`, 785,549 bytes) |
| `32016R0679sv.html` | `3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0024.03/DOC_1` (`b02b4aae…`, 828,689 bytes) |
| `32016R1036de.html` | `e0dcca38-3e84-11e6-af30-01aa75ed71a1.0004.03/DOC_1` (`4bbfd60c…`, 266,415 bytes): recital 13, "u. a." |
| `02016R1036-20180608hu.html` | `49a6dee7-e726-4a1b-b598-ad79213c2001.0008.02/DOC_1` (`8fd5e15b…`, 213,831 bytes): the ILO convention "29. sz. (1930)" of Annex Ia |
| `32019L0790da.html` | `214471fe-786e-11e9-9f05-01aa75ed71a1.0003.03/DOC_1` (`e1764e81…`, 278,963 bytes): recital 59, "som de indgår i. Udgivere" |

## sha256

```
5bc63ea884a8fb1f6d3a9ceafc46c04d3d9547fee2ce50642cc1b3beeb3727b5  02016R1036-20180608hu.html
d93a0cea18e2a5904e89747703c8c10ead6f82529728b1466b84cbe5bdb5f18b  32016R0679bg.html
c2619d9926e09988788e9388049e77cb942d79ace3f340bf91c60cf79669ebec  32016R0679cs.html
6fd9c4ea6c53e1a643b56c4b0e1a4e9cd0fd786095919e38321ac371ad08f39d  32016R0679da.html
ac082081e5c9fe8ffc6dc9d677838fda23192b134ea7aa8e72daa3de73fd38b3  32016R0679de.html
cfe34b0cd6c7f1863bc5ada9f0cd458019ecc552875b8ce51da097af75e27f8e  32016R0679el.html
f95e5fbfb34daf681248c0f75e3ff9a6f6ff894802347ab34d5d3f959b013502  32016R0679en.html
bb42c4ee7650bfb16039dffaa6f4f8d5b1456560b5d2493ff8ee05bb172683ae  32016R0679es.html
fe7fba1358fe66148fe1c226f0859fd6608cba2b0a043bd48971f99dcb74f5d2  32016R0679et.html
bbd528c3a14596ce49f8ba39d7d9b7e80319facd5e4ccb2b5b30eefa585a343b  32016R0679fi.html
3f472b33723dcb0c94528a5e7b887dd7cab80207c3fb93659428393fc059a846  32016R0679fr.html
be4be00243b78c04a03d93b53859caca66ddf25c006b3f77edd4bbd21ea61323  32016R0679ga.html
b586055e5c61b429a54c8d192dd8f1418204a0188118e59ec2ba93905e31ab2f  32016R0679hr.html
f5a9d2f6aeebc4e97d1c553924b69029de299c615fc60f28546a6e02346e65a4  32016R0679hu.html
26bdb0b2fea82a45f4d90e200eaa4a1a3419936cf9cb5b5837b5bbf9e0e11151  32016R0679it.html
536e9e7bf4a680cbae4481aaea8c75bc4503424fc4f96807fddc0cabf5aa3067  32016R0679lt.html
f28504fee4c7dd79df59d7a0b7293f1be4a9cbd3e57aa266d0e958a21993fd04  32016R0679lv.html
4c2d555daee03a7c7961e345e29a02af27c44c1de5b9bf4dfb87caf251516754  32016R0679mt.html
eeca49344bd041f086cf2f79c99626203985907b5840b603737f030862fe561e  32016R0679nl.html
660451e777cf834174458695486dfb0dc378e8662de51668e603e7358bdc2984  32016R0679pl.html
7212d0903630e3bea4be02d1132dba1e9aab744f5f45e45a1375f1a5a321f9d0  32016R0679pt.html
630ea865c6adfe639f93f04a50010e43466b4e522ac7e5da37a734f9f8142b34  32016R0679ro.html
2d5ac1a08e0985a465f2f15c0f2a7995ba83dda73cf8b345b25747da73dced28  32016R0679sk.html
776b9513e32525fe0ecff81c755e3c09ddc3119f5ebacec427cae53a259cdd12  32016R0679sl.html
671156f778a94fb0f8851ed334cdcea7163cb4c448dc9d65f7d8f881b7f2db2d  32016R0679sv.html
bde343c3fc9a92a5d969877a3f9750f0d85598bff5f1295ebc6ba35792f9dff8  32016R1036de.html
32918cccf458f31404c67c6b0eec626243a7c6bb953d83c76138c839a5b9fb2d  32019L0790da.html
```
