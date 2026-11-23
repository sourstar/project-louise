# legal_article v4's abbreviations, checked (2026-11-21)

**Status:** the per-language lists of v4 (`ABBREVIATIONS_V4`,
`ABBREVIATIONS_V4_BEFORE_NUMBER`, app/chunking_legal.py) were written from memory
(legal-chunker-v4.md §2). They are now checked against the Interinstitutional Style
Guide and real EUR-Lex text, and corrected. v1–v3 are untouched (their golden digests
pass), `CHUNK_VERSION` stays 3, nothing on the node moves. Of the 210 forms the 24
lists held, 102 are confirmed and 108 left them (nine of those, `vol`, `cap` and `kap`
in en, es, fr, it, pt, da and sv, for the before-number list); 17 are added. The union
every language is cut with went from 169 forms to 101.

## 1. Sources

- **The Guide**, edition of 18.11.2026, every language: the whole PDF
  (`https://style-guide.europa.eu/o/opportal-service/isg?resource=pdf-web/ISG_<lang>_4web.pdf`)
  and the sections as served (`…?resource=<lang>/<identifier>.html`; the page is
  `https://style-guide.europa.eu/<lang>/content/-/isg/topic?identifier=<identifier>`).
  Read: § 2.7, § 3.1 (the OJ), § 3.2.1–3.2.4 (references to acts and their
  subdivisions), § 3.3.1, § 5.9.1–5.9.4, § 9.4, all of Part Four (its identifiers
  differ by language) and Annex A3. Every page was reachable; earlier the domain did
  not resolve from the agent's network, which is why the lists were written blind.
- **Real text**: 143 Cellar items, 32016R0679 (GDPR), 31992L0043 (no Irish),
  32019L0790, 32016R1036, 02016R1036-20180608 and 32024R0903 in the 24 languages,
  fetched by content negotiation as tests/fixtures/eurlex/README.md does (48 MB,
  outside the repository). Read as the plugin reads them (`formex.norm`).
- **The repository's fixtures**: everything under tests/fixtures/eurlex/ and the
  bodies of Normattiva, LEGI and NeuRIS (tests/legal_fixture_texts.py). They hold body
  text in English and Italian, one Czech and one German corrigendum, and otherwise
  only the titles of about ten works in all 24 languages (notices, RDF). `rendition/`
  is not on this branch; it is on `worktree-agent-a1d5a0f7eace2bad6` (et, hu, lt, lv,
  mt, ro, trimmed), and was counted from there.

"×n" below: occurrences of the form with its full stop **before a digit or a capital**
in the Cellar items, the candidates it refuses. "0" means none in the items, the
fixtures and the language's Guide.

## 2. The rule applied

- A form stays where the language's Guide or real legal text writes it with a full
  stop. Where neither does, it goes: EU acts write the subdivisions in full in every
  language but cs, sk, pl and da (each Guide's § 3.2.3); the Greek Guide § 10.10(c)
  says abbreviating them "απαγορεύεται ρητά" (is expressly forbidden).
- The union of the 24 lists cuts every language. A form that is a word or a unit in
  any of them is refused before a number only, or not at all.
- Forms that end a sentence as often as they continue one stay out, as before ("etc.",
  "et seq."), now **measured** for the dates: a Polish year "r." before a capital
  continues its sentence in 18 of 21 cases and a Lithuanian day "d." in 81 of 84, so
  both are in; the Bulgarian year "г." is also the gram and the Lithuanian year "m." the
  metre, so both stay out.

## 3. Per language

| lang | form | verdict | source |
|---|---|---|---|
| bg | стр. | confirmed | § 3.1 "ОВ L 32, 6.2.1968 г., стр. 6"; ×89 |
| bg | бр. | confirmed | § 10.1.8 "бр. 1—2" |
| bg | вж., Вж. | confirmed | § 2.7 "Вж. точка 2.2.", § 3.2.1, § 5.9.1 |
| bg | напр. | confirmed | § 3.1, § 10.1.1 |
| bg | т.е., т.нар. | confirmed | § 10.1.1, § 10.4.2; т.е. 6 in the acts |
| bg | чл., Чл., ал. | removed, not written | § 3.2.3 "член 1, параграф 2, буква а)"; 0 (the Guide's only "чл." is "чл.-кор.") |
| bg | Напр. | removed | 0 |
| bg | срв. | added | § 10.4.2 "срв. (сравни)", Annex A3 |
| cs | čl., Čl. | confirmed | § 3.2.3 "čl. 1 odst. 2 písm. a)", § 10.8; ×392, ×425 |
| cs | odst., písm. | confirmed | § 3.2.3, § 10.8; odst. ×822 |
| cs | č. | confirmed | § 3.2.2 "nařízení Komise (EU) č. 127/2010"; ×103 |
| cs | věst. | confirmed | § 3.1 "Úř. věst. L 32, 6.2.1968, s. 6"; ×89 |
| cs | např., Např., tj., tzv. | confirmed | Annex A3, § 3.1, § 3.2.4 |
| cs | pododst., příl. | **wrong** | § 10.8: "zkracují se článek na čl., odstavec na odst. a písmeno na písm. Ostatní názvy se nezkracují"; 0 |
| cs | resp. | removed | 0 in cs (it stays for sk) |
| cs | srov. | added | § 3.3.1 "srov. bod 5a) níže" |
| da | stk. | confirmed | § 3.2.3 "artikel 1, stk. 2, litra a)"; ×1134 |
| da | nr. | confirmed | § 3.2.2 "forordning (EU) nr. 127/2010"; ×118 |
| da | jf., f.eks., dvs., bl.a. | confirmed | Annex A3, § 3.2.1, § 3.1; 88, 103, 11, 39 in the acts |
| da | pkt. | **wrong** | Annex A3: "pkt. punktum (punkt forkortes aldrig)", and § 3.2.3 writes "andet punktum"; 0 |
| da | kap. | moved before a number | Annex A3 "kap. kapitel"; "kap." ends a Hungarian sentence ("… kap. A …", Guide hu) |
| da | Jf. | removed | 0 |
| da | Stk. | added | ×10: "Stk. 1 berører ikke …" (GDPR Art. 8(3)), sentence-initial |
| da | pr. | added | § 3.1 "indført pr. 1. januar 1968" ×5 |
| de | ABl. | confirmed | § 3.1 "ABl. L 32 vom 6.2.1968, S. 6"; ×90 |
| de | BGBl. | confirmed | NeuRIS fixture "BGBl. I 2020, 1234"; Guide de Annex A4 |
| de | z. (z. B.) | confirmed | § 3.2.1, § 10.1; ×16 |
| de | d.h. | confirmed | 2 in the acts |
| de | GVBl., RGBl., BAnz., UAbs., Unterabs., Anh., Anl., Abschn., Kap., Halbs., Alt., Fn., Rz., i.V.m., u.a., sog., insb., Vgl. | removed, not written | § 3.2.3 "Artikel 1 Absatz 2 Buchstabe a"; 0 in the acts, the Guide and the NeuRIS fixtures |
| de | i. | **wrong** | 0 for "i. V. m."; "i" is a Danish and Swedish preposition that ends sentences: 32019L0790 da recital 59 "… som de indgår i. Udgivere …" |
| de | ff. | removed, et-seq. class | § 10.7 "§ 17 ff."; ends a reference, as "etc." |
| de | d. h., u. a. | added (spaced) | § 3.1, § 3.2.1 "d. h. das Kürzel"; § 10.1; 32016R1036 recital 13 "u. a. Bedingungen" |
| el | αριθ. | confirmed | § 3.2.2 "κανονισμός (ΕΕ) αριθ. 127/2010"; ×112 |
| el | σελ. | confirmed | Annex A3: "όπως και η συντομογραφία σελ." (in Union texts; § 3.1 writes σ.) |
| el | βλ., Βλ., π.χ., δηλ. | confirmed | § 2.7 "Βλ. σημείο 2.2.", § 5.9.1, § 10.1.2; Annex A3 |
| el | άρθ., άρθρ., Άρθ., παρ., υποπαρ., εδ., περ., στοιχ., κεφ. | **wrong** | § 10.10(c): EU acts never abbreviate subdivisions ("να μη γράφεται π.χ. «άρθρ.», «§» και «παράγρ.»"); § 3.2.3 "άρθρο 1 παράγραφος 2 στοιχείο α)"; 0 |
| en | e.g., i.e. | confirmed | § 10.8; fixtures 78, 13 |
| en | cf., Cf., subpara., sect. | removed, not written | 0 (§ 3.2.3 "Article 1(2), point (a)") |
| en | vol. | moved before a number | "% vol." (alcoholic strength) ends sentences: fixture L_2011304EN.01006001.xml |
| en | Rev. | added | fixture L_2017349EN.01005801.xml "UN/ECE Regulation 49 Rev. 06" ×9 |
| en | Vol. | added | § 5.9.4 "Vol. 34, Issue 3", Annex A3 |
| es | párr., arts., p. ej. | confirmed | Annex A3 |
| es | apdo., apdos., núm., núms., pág., págs., ap., cf. | removed, not written | § 3.2.3 "artículo 1, apartado 2, letra a)", § 3.1 "DO L 32 de 6.2.1968, p. 6"; 0 |
| es | cap., vol. | moved before a number | Annex A3, § 5.9.4; "cap" an English word |
| et | (none) | confirmed | § 3.1 "EÜT L 32, 6.2.1968, lk 6"; "lk" ×91 and "nr" ×104 bare, none dotted |
| fi | ks., Ks., vrt., Vrt., esim. | confirmed | § 2.7, § 3.2.1, § 5.9.1, § 10.8, § 3.1 |
| fi | Esim. | added | § 3.2.1, § 3.2.2 (sentence-initial) |
| fr | cf. | confirmed | Part Four, Annexe B "confer (cf.)"; LEGI fixture "(cf. le chapitre …)" |
| fr | paragr., sect. | removed, not written | § 3.2.3 "l'article 1er, paragraphe 2, point a)"; 0 |
| fr | vol. | moved before a number | § 5.9.4 "vol. 34"; "vol" a French word (theft, flight) |
| ga | Uimh., lch. | confirmed | § 3.2.2 "Rialachán (AE) Uimh. 1234/2010", § 3.1 "IO L 32, 6.2.1968, lch. 6"; ×97, ×79 |
| ga | Airt. | removed, not written | § 3.2.3 "Airteagal 1(2)"; 0 |
| ga | m.sh. | removed, not written | § 10.5 uses "e.g." (en's form); 0 |
| hr | br., str. | confirmed | § 3.2.2 "Uredba (EU) br. 127/2010", § 3.1 "SL L 32, 6.2.1968., str. 6."; ×103, ×90 |
| hr | npr., tj. | confirmed | § 10.1.12, § 3.1 |
| hr | čl., Čl., podst. | removed, not written | § 3.2.3 "članak 1. stavak 2. točka (a)"; 0 |
| hr | Npr. | removed | § 10.8: "Na početku rečenice bolje je umjesto kratica Npr., Br. … ispisati cijelu riječ" |
| hu | sz., pl., vö., ún. | confirmed | § 5.9.3, § 10.1.8 ("29. sz. (1930)" ×8); § 3.2.1; § 10.3; § 10.1.11 |
| hu | bek., tv. | removed, not written | § 3.2.3 "1. cikke (2) bekezdésének"; 0 |
| hu | ld., Ld. | **wrong** | § 10.3: "A „vesd össze” kifejezést általában rövidítjük (vö.), a „lásd” szót azonban nem"; 0 |
| hu | Vö. | removed | 0 |
| it | pag., pagg. | confirmed | § 3.1 "GU L 32 del 6.2.1968, pag. 6" ×93; § 5.9.3, Annex A3 |
| it | D.Lgs. | confirmed | Normattiva fixture, legge 306/2003: "Il D.Lgs. 2 gennaio 2018" |
| it | par., sez., cap., d.lgs., D.lgs., d.Lgs., d.l. | removed, not written | § 3.2.3 "l'articolo 1, paragrafo 2, lettera a)"; 0 in the acts and the Normattiva fixtures |
| it | vol. | moved before a number | § 5.9.4 |
| lt | žr., Žr., pvz., Pvz., t. y., str. | confirmed | § 2.7, § 5.9.1; § 10.1.1; § 3.2.2; Annex A3 ("str. straipsnis") |
| lt | psl. | removed, not written | the page is "p." (§ 3.1 "OL L 32, 1968 2 6, p. 6"); 0 |
| lt | plg. | added | § 10.1.6, § 10.3.9, Annex A3 (palygink, cf.) |
| lt | d. | added | § 3.2.2 "2010 m. vasario 5 d. Komisijos reglamentas"; ×85: of the 84 after a day's number, 81 continue the sentence |
| lv | sk., Sk., piem., t.i. | confirmed | § 2.7, § 3.2.1, § 5.9.1, § 10.1.1; t.i. 3 in 31992L0043 |
| lv | apakšp. | removed, not written | § 3.2.3 "a) apakšpunkts"; 0 |
| lv | Piem. | removed | 0 |
| lv | t. i. | added (spaced) | § 10.6, § 10.7 |
| lv | Lpp. | added | the legacy header "Oficiālais Vēstnesis L 206 , 22/07/1992 Lpp. 0007" (31992L0043lv, rendition/) |
| mt | eż. | confirmed | § 10.1.1, Annex A3 |
| mt | par., subpar. | **wrong** | § 10.1.1: "para. (paragrafu), kap. (kapitolu), eż. (eżempju) u p. (paġna)" — "para." is v3's; § 3.2.3 "il-paragrafu" in full; 0 |
| mt | Vol., kap., Kap. | added (kap., Kap. before a number) | § 5.9.4; § 10.1.1; Annex A3 |
| nl | nr., blz., d.w.z., o.a. | confirmed | § 3.2.2 ×111, § 3.1 ×92, § 3.2.2, § 10.1 |
| nl | bijv. | **wrong** | § 10.4.1: "“bijvoorbeeld” wordt bij voorkeur afgekort als “bv.” (en niet “bijv.”)" |
| nl | jo., zgn. | removed, not written | 0 |
| nl | bv. | added | § 10.4.1, § 3.1, § 3.2.1, Annex A3 |
| pl | ust., Ust. | confirmed | § 3.2.3 "art. 1 ust. 2 lit. a)"; ×1135, ×12 |
| pl | str. | confirmed | 31992L0043 pl footnotes "Dz.U. C 75 z 20.3.1991, str. 12." ×7 (the Guide's is "s.") |
| pl | poz., Dz., zob., Zob., np., tj., tzn., m.in. | confirmed | Annex A3 "Dz.U. 2020 poz. 1024"; § 9.5.3 "Dz. U. L 166"; § 2.7, § 3.2.1; § 10.3.1 |
| pl | nr | **never with a full stop** | § 10.3.1, rule 1 (no full stop where the abbreviation ends with the word's last letter); "nr 45/2001" ×105 bare, 0 dotted |
| pl | ds. | added | § 10.3.1 "ds. (do spraw)"; ×80 "Grupę Roboczą ds. Ochrony …" |
| pl | r. | added | § 3.1, § 3.2.2; ×22: of the 21 after a year before a capital or a bracket, 18 continue ("do dnia 9 czerwca 2020 r. Komisja dokonuje …", "2014 r. (dotychczas nieopublikowane …)") |
| pt | cf. | confirmed | § 10.4.12, Annex A3 |
| pt | alín., arts., pág. | removed, not written | § 3.2.3 "o artigo 1.o, n.o 2, alínea a)", § 3.1 "p. 6"; 0 |
| pt | cap., vol. | moved before a number | Annex A3, § 5.9.4 |
| ro | nr., cf., alin. | confirmed | § 3.2.2 ×104; § 10.1.1; Annex A3 |
| ro | pct., par., subpar., subparagr. | removed, not written | § 3.2.3 "articolul 1 alineatul (2) litera (a)", "al doilea paragraf"; 0 |
| ro | cap., vol. | before a number | § 3.1 "(Ediția specială în limba română, cap. 6, vol. 3, p. 33)" |
| sk | ods., písm., č. | confirmed | § 3.2.3 "článok 1 ods. 2 písm. a)" ×798; § 3.2.2 ×104 |
| sk | napr., tzv., t. j., resp. | confirmed | § 10.1.4, Annex A3, § 10.1.5; 6, 1, 5, 3 in the acts |
| sk | čl., Čl., podods., príl., Napr. | removed, not written | § 3.2.3 writes "článok" in full; 0 |
| sl | št., str. | confirmed | § 3.2.2 ×104, § 3.1 ×93 |
| sl | npr., tj., t. i. | confirmed | § 3.2.3, § 3.1, § 10.1.7 |
| sl | čl., Čl., odst., pododst., tč., Ur. l. | removed, not written | § 3.2.3 "člen 1(2)(a)"; 0 |
| sv | s.k., dvs., bl.a. | confirmed | § 10.4.2, § 10.4.10; dvs. 7, bl.a. 13 in the acts |
| sv | jfr, Jfr | **never with a full stop** | the Guide writes "jfr Celex nedan" (Bilaga C); 0 dotted |
| sv | kap. | moved before a number | Annex A3 |

Before a number only (`ABBREVIATIONS_V4_BEFORE_NUMBER`):

| form | verdict | source |
|---|---|---|
| s. | confirmed | the page in cs, da, fi, pl, sk, sv (§ 3.1); ×84–105 each; "10 s." is seconds |
| т. | confirmed | § 5.9.3 "EU:C:2005:446, т. 19", Annex A3; "т" is tonnes |
| σ. | confirmed | § 3.1 "ΕΕ L 32 της 6.2.1968, σ. 6"; ×93 |
| st. | removed | Croatian writes "stavak" (§ 3.2.3); 0 |
| ν. | removed | the Greek national "ν." (§ 10.10(c)); 0 in EU text |
| l. | removed | 0 in the acts and the Normattiva fixtures ("L." is v3's) |
| vol., cap., kap., Kap. | moved here | above |

## 4. Looked at, not added

- "sal." (lv, § 10.1.1, cf.): "sal" is Spanish salt, which ends sentences in food law.
  "por." (pl, § 10.3.1, cf.): Hungarian "por", powder.
- Case-law reports (§ 5.9.3: Slg., Randnr., Rec., Racc., Colet., Jurispr., Sml., Kok.,
  Rink., ZOdl., Zb., Sb. rozh.): none in the six acts; Dutch "Kok" is also a name.
- sv "kl.", "fr.o.m.", "t.o.m." (§ 10.6) before a time or a date: none in the acts.
- hu "o." before the page in the legacy header ("22/07/1992 o. 0007"): the same "o."
  ends every Hungarian OJ footnote ("1. o.").
- ga "Ich." (×1, a typo of "lch."), hr "sv." (§ 3.1, a special edition's volume),
  botanical authors ("Brid.", "Jur.", "spp.").

## 5. Not abbreviations, and left as v3 had them

An ordinal before a capital is a cut (legal-chunker-v4.md §2): Croatian "Člankom 8.
stavkom 1. | Povelje" cuts GDPR recital 1 into four, German "vom 18. | Dezember 2000"
its footnote 6, Croatian "podtočka ii. | Uredbe" (§ 3.2.3). A colon before a capital is
one too (Slovenian "(v nadaljnjem besedilu: | Listina)"). The drift tool counts them;
the abbreviation lists do not decide them.

## 6. The tests

tests/test_chunking_legal_v4.py holds real paragraphs of every language
(tests/fixtures/eurlex/sentences/, 27 Cellar items trimmed to whole lines, README
there): the GDPR's recital 1 or 17 (a sentence end) and footnote 6 (one sentence,
several full stops) per language, Danish "Stk.", German "z. B." and "u. a.",
Hungarian "sz.", Polish "ds." and "r.", Lithuanian "d.", English "Rev.", and the
Danish "i." that now ends its sentence. Each is checked verbatim against the fixture as
the plugin's rendition reader reads it, with whether v3 cut it otherwise.
