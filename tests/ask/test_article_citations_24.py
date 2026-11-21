"""EUR-Lex's act-and-article citation grammar in the 24 EU languages
(app/refs/eurlex.py, 2026-11-21). Until then it read en fr de it es pt nl, so
a question in cs, sk, pl, sl, hr, hu, ro, bg, el, lt, lv, et, fi, sv, da, mt
or ga citing an article of an act pinned nothing, though EUR-Lex5 holds the
act in all 24.

EVIDENCE, NOT MEMORY. Every text below is VERBATIM from the Official
Journal, in its language: the item Cellar serves for the act
(`publications.europa.eu/resource/celex/<CELEX>`, Accept
`application/xhtml+xml`, Accept-Language the 3-letter code; fetched
2026-11-21 — the EUR-Lex front end answered a WAF challenge, Cellar is the
repository behind it). The same nine provisions in each language, so the
expected act and article come from the provision (PROVISIONS, read in the
English text), never from the grammar's own answer. The fixtures under
tests/fixtures/eurlex/ hold an act in few of these languages: their cs, et,
hu, lt, lv, mt items cite acts but no article of one
(`test_the_fixtures_name_their_acts_in_their_languages`); the one article
cited by number, in 32004R0726ro.html, is a row below.
"""
from __future__ import annotations

import collections
import re

import pytest

from app.ask import articles
from app.plugins.eurlex import source as src
from app.refs import eurlex as eref
from tests.ask.test_article_citations import _collection
from tests.test_eurlex import ACT

#: provision → (the act it is in and where, as English reads it, the cited act's CELEX, the article)
PROVISIONS = {
    "93(2)": ("32016R0679 Art. 93(2): 'Article 5 of Regulation (EU) No 182/2011'", "32011R0182", "5"),
    "93(3)": ("32016R0679 Art. 93(3): 'Article 8 of Regulation (EU) No 182/2011'", "32011R0182", "8"),
    "rec172": ("32016R0679 recital 172: 'Article 28(2) of Regulation (EC) No 45/2001'", "32001R0045", "28"),
    "4(25)": ("32016R0679 Art. 4(25): 'point (b) of Article 1(1) of Directive (EU) 2015/1535'", "32015L1535", "1"),
    "25(6)": ("32016R0679 Art. 45(9) and recital 103: 'Article 25(6) of Directive 95/46/EC'", "31995L0046", "25"),
    "825rec21": ("32018R0825 recital 21: 'Article 7(2a) of Regulation (EU) 2016/1036'", "32016R1036", "7"),
    "825art4": ("32018R0825 Art. 4: 'Article 5(9) of Regulation (EU) 2016/1036'", "32016R1036", "5"),
    "1773rec18": ("32023R1773 recital 18: 'Article 16 of Commission Implementing Regulation (EU) 2023/1070'",
                  "32023R1070", "16"),
    "1773art5": ("32023R1773 Art. 5: 'Article 170(1) of Delegated Regulation (EU) 2015/2446'", "32015R2446", "170"),
    # the fixtures' own (tests/fixtures/eurlex/README.md): the one citation of an article of another act by
    # number their non-English items hold
    "726rec1": ("tests/fixtures/eurlex/rendition/32004R0726ro.html recital 1: 'Article 71 of Council Regulation "
                "(EEC) No 2309/93'", "31993R2309", "71"),
}

#: (language, the citation as that language's Official Journal writes it, the provision)
ROWS: tuple[tuple[str, str, str], ...] = (
    # en
    ('en', 'Article 5 of Regulation (EU) No 182/2011', '93(2)'),
    ('en', 'Article 8 of Regulation (EU) No 182/2011', '93(3)'),
    ('en', 'Article 28(2) of Regulation (EC) No 45/2001', 'rec172'),
    ('en', 'Article 1(1) of Directive (EU) 2015/1535', '4(25)'),
    ('en', 'Article 25(6) of Directive 95/46/EC', '25(6)'),
    ('en', 'Article 7(2a) of Regulation (EU) 2016/1036', '825rec21'),
    ('en', 'Article 5(9) of Regulation (EU) 2016/1036', '825art4'),
    ('en', 'Article 16 of Commission Implementing Regulation (EU) 2023/1070', '1773rec18'),
    ('en', 'Article 170(1) of Delegated Regulation (EU) 2015/2446', '1773art5'),
    # fr
    ('fr', 'article 5 du règlement (UE) no 182/2011', '93(2)'),
    ('fr', 'article 8 du règlement (UE) no 182/2011', '93(3)'),
    ('fr', 'article 28, paragraphe 2, du règlement (CE) no 45/2001', 'rec172'),
    ('fr', 'article 1er, paragraphe 1, point b), de la directive (UE) 2015/1535', '4(25)'),
    ('fr', 'article 25, paragraphe 6, de la directive 95/46/CE', '25(6)'),
    ('fr', 'article 7, paragraphe 2 bis, du règlement (UE) 2016/1036', '825rec21'),
    ('fr', 'article 5, paragraphe 9, du règlement (UE) 2016/1036', '825art4'),
    ('fr', 'article 16 du règlement d’exécution (UE) 2023/1070', '1773rec18'),
    ('fr', 'article 170, paragraphe 1, du règlement délégué (UE) 2015/2446', '1773art5'),
    # de
    ('de', 'Artikel 5 der Verordnung (EU) Nr. 182/2011', '93(2)'),
    ('de', 'Artikel 8 der Verordnung (EU) Nr. 182/2011', '93(3)'),
    ('de', 'Artikel 28 Absatz 2 der Verordnung (EG) Nr. 45/2001', 'rec172'),
    ('de', 'Artikels 1 Nummer 1 Buchstabe b der Richtlinie (EU) 2015/1535', '4(25)'),
    ('de', 'Artikel 25 Absatz 6 der Richtlinie 95/46/EG', '25(6)'),
    ('de', 'Artikel 7 Absatz 2a der Verordnung (EU) 2016/1036', '825rec21'),
    ('de', 'Artikel 5 Absatz 9 der Verordnung (EU) 2016/1036', '825art4'),
    ('de', 'Artikel 16 der Durchführungsverordnung (EU) 2023/1070', '1773rec18'),
    ('de', 'Artikel 170 Absatz 1 der delegierten Verordnung (EU) 2015/2446', '1773art5'),
    # it
    ('it', 'articolo 5 del regolamento (UE) n. 182/2011', '93(2)'),
    ('it', 'articolo 8 del regolamento (UE) n. 182/2011', '93(3)'),
    ('it', 'articolo 28, paragrafo 2, del regolamento (CE) n. 45/2001', 'rec172'),
    ('it', 'articolo 1, paragrafo 1, lettera b), della direttiva (UE) 2015/1535', '4(25)'),
    ('it', 'articolo 25, paragrafo 6, della direttiva 95/46/CE', '25(6)'),
    ('it', 'articolo 7, paragrafo 2 bis, del regolamento (UE) 2016/1036', '825rec21'),
    ('it', 'articolo 5, paragrafo 9, del regolamento (UE) 2016/1036', '825art4'),
    ('it', 'articolo 16 del regolamento di esecuzione (UE) 2023/1070', '1773rec18'),
    ('it', 'articolo 170, paragrafo 1, del regolamento delegato (UE) 2015/2446', '1773art5'),
    # es
    ('es', 'artículo 5 del Reglamento (UE) n.o 182/2011', '93(2)'),
    ('es', 'artículo 8 del Reglamento (UE) n.o 182/2011', '93(3)'),
    ('es', 'artículo 28, apartado 2, del Reglamento (CE) n.o 45/2001', 'rec172'),
    ('es', 'artículo 1, apartado 1, letra b), de la Directiva (UE) 2015/1535', '4(25)'),
    ('es', 'artículo 25, apartado 6, de la Directiva 95/46/CE', '25(6)'),
    ('es', 'artículo 7, apartado 2 bis, del Reglamento (UE) 2016/1036', '825rec21'),
    ('es', 'artículo 5, apartado 9, del Reglamento (UE) 2016/1036', '825art4'),
    ('es', 'artículo 16 del Reglamento de Ejecución (UE) 2023/1070', '1773rec18'),
    ('es', 'artículo 170, apartado 1, del Reglamento Delegado (UE) 2015/2446', '1773art5'),
    # pt
    ('pt', 'artigo 5.o do Regulamento (UE) n.o 182/2011', '93(2)'),
    ('pt', 'artigo 8.o do Regulamento (UE) n.o 182/2011', '93(3)'),
    ('pt', 'artigo 28.o, n.o 2, do Regulamento (CE) n.o 45/2001', 'rec172'),
    ('pt', 'artigo 1.o, n.o 1, alínea b), da Diretiva (UE) 2015/1535', '4(25)'),
    ('pt', 'artigo 25.o, n.o 6, da Diretiva 95/46/CE', '25(6)'),
    ('pt', 'artigo 7.o, n.o 2-A, do Regulamento (UE) 2016/1036', '825rec21'),
    ('pt', 'artigo 5.o, n.o 9, do Regulamento (UE) 2016/1036', '825art4'),
    ('pt', 'artigo 16.o do Regulamento de Execução (UE) 2023/1070', '1773rec18'),
    ('pt', 'artigo 170.o, n.o 1, do Regulamento Delegado (UE) 2015/2446', '1773art5'),
    # nl
    ('nl', 'artikel 5 van Verordening (EU) nr. 182/2011', '93(2)'),
    ('nl', 'artikel 8 van Verordening (EU) nr. 182/2011', '93(3)'),
    ('nl', 'artikel 28, lid 2, van Verordening (EG) nr. 45/2001', 'rec172'),
    ('nl', 'artikel 1, lid 1, punt b), van Richtlijn (EU) 2015/1535', '4(25)'),
    ('nl', 'artikel 25, lid 6, van Richtlijn 95/46/EG', '25(6)'),
    ('nl', 'artikel 7, lid 2 bis, van Verordening (EU) 2016/1036', '825rec21'),
    ('nl', 'artikel 5, lid 9, van Verordening (EU) 2016/1036', '825art4'),
    ('nl', 'artikel 16 van Uitvoeringsverordening (EU) 2023/1070', '1773rec18'),
    ('nl', 'artikel 170, lid 1, van Gedelegeerde Verordening (EU) 2015/2446', '1773art5'),
    # da
    ('da', 'artikel 5 i forordning (EU) nr. 182/2011', '93(2)'),
    ('da', 'artikel 8 i forordning (EU) nr. 182/2011', '93(3)'),
    ('da', 'artikel 28, stk. 2, i forordning (EF) nr. 45/2001', 'rec172'),
    ('da', 'artikel 1, stk. 1, litra b), i Europa-Parlamentets og Rådets direktiv (EU) 2015/1535', '4(25)'),
    ('da', 'artikel 25, stk. 6, i direktiv 95/46/EF', '25(6)'),
    ('da', 'artikel 7, stk. 2a, i forordning (EU) 2016/1036', '825rec21'),
    ('da', 'artikel 5, stk. 9, i forordning (EU) 2016/1036', '825art4'),
    ('da', 'artikel 16 i Kommissionens gennemførelsesforordning (EU) 2023/1070', '1773rec18'),
    ('da', 'artikel 170, stk. 1, i delegeret forordning (EU) 2015/2446', '1773art5'),
    # sv
    ('sv', 'artikel 5 i förordning (EU) nr 182/2011', '93(2)'),
    ('sv', 'artikel 8 i förordning (EU) nr 182/2011', '93(3)'),
    ('sv', 'artikel 28.2 i förordning (EG) nr 45/2001', 'rec172'),
    ('sv', 'artikel 1.1 b i Europaparlamentets och rådets direktiv (EU) 2015/1535', '4(25)'),
    ('sv', 'artikel 25.6 eller 26.4 i direktiv 95/46/EG', '25(6)'),
    ('sv', 'artikel 7.2a i förordning (EU) 2016/1036', '825rec21'),
    ('sv', 'artikel 5.9 i förordning (EU) 2016/1036', '825art4'),
    ('sv', 'artikel 16 i kommissionens genomförandeförordning (EU) 2023/1070', '1773rec18'),
    ('sv', 'artikel 170.1 i delegerad förordning (EU) 2015/2446', '1773art5'),
    # cs
    ('cs', 'článek 5 nařízení (EU) č. 182/2011', '93(2)'),
    ('cs', 'článek 8 nařízení (EU) č. 182/2011', '93(3)'),
    ('cs', 'čl. 28 odst. 2 nařízení (ES) č. 45/2001', 'rec172'),
    ('cs', 'čl. 1 odst. 1 písm. b) směrnice (EU) 2015/1535', '4(25)'),
    ('cs', 'čl. 25 odst. 6 směrnice 95/46/ES', '25(6)'),
    ('cs', 'čl. 7 odst. 2a nařízení (EU) 2016/1036', '825rec21'),
    ('cs', 'čl. 5 odst. 9 nařízení (EU) 2016/1036', '825art4'),
    ('cs', 'článku 16 prováděcího nařízení Komise (EU) 2023/1070', '1773rec18'),
    ('cs', 'čl. 170 odst. 1 nařízení v přenesené pravomoci (EU) 2015/2446', '1773art5'),
    # sk
    ('sk', 'článok 5 nariadenia (EÚ) č. 182/2011', '93(2)'),
    ('sk', 'článok 8 nariadenia (EÚ) č. 182/2011', '93(3)'),
    ('sk', 'článkom 28 ods. 2 nariadenia (ES) č. 45/2001', 'rec172'),
    ('sk', 'článku 1 bode 1 písm. b) smernice Európskeho parlamentu a Rady (EÚ) 2015/1535', '4(25)'),
    ('sk', 'článku 25 ods. 6 smernice 95/46/ES', '25(6)'),
    ('sk', 'článku 7 ods. 2a nariadenia (EÚ) 2016/1036', '825rec21'),
    ('sk', 'článku 5 ods. 9 nariadenia (EÚ) 2016/1036', '825art4'),
    ('sk', 'článku 16 vykonávacieho nariadenia Komisie (EÚ) 2023/1070', '1773rec18'),
    ('sk', 'článkom 170 ods. 1 delegovaného nariadenia (EÚ) 2015/2446', '1773art5'),
    # pl
    ('pl', 'art. 5 rozporządzenia (UE) nr 182/2011', '93(2)'),
    ('pl', 'art. 8 rozporządzenia (UE) nr 182/2011', '93(3)'),
    ('pl', 'art. 28 ust. 2 rozporządzenia (WE) nr 45/2001', 'rec172'),
    ('pl', 'art. 1 ust. 1 lit. b) dyrektywy Parlamentu Europejskiego i Rady (UE) 2015/1535', '4(25)'),
    ('pl', 'art. 25 ust. 6 dyrektywy 95/46/WE', '25(6)'),
    ('pl', 'art. 7 ust. 2a rozporządzenia (UE) 2016/1036', '825rec21'),
    ('pl', 'art. 5 ust. 9 rozporządzenia (UE) nr 2016/1036', '825art4'),
    ('pl', 'art. 16 rozporządzenia wykonawczego Komisji (UE) 2023/1070', '1773rec18'),
    ('pl', 'art. 170 ust. 1 rozporządzenia delegowanego (UE) 2015/2446', '1773art5'),
    # sl
    ('sl', 'člen 5 Uredbe (EU) št. 182/2011', '93(2)'),
    ('sl', 'člen 8 Uredbe (EU) št. 182/2011', '93(3)'),
    ('sl', 'členom 28(2) Uredbe (ES) št. 45/2001', 'rec172'),
    ('sl', 'člena 1(1) Direktive (EU) 2015/1535', '4(25)'),
    ('sl', 'člena 25(6) Direktive 95/46/ES', '25(6)'),
    ('sl', 'členu 7(2a) Uredbe (EU) 2016/1036', '825rec21'),
    ('sl', 'členom 5(9) Uredbe (EU) 2016/1036', '825art4'),
    ('sl', 'člena 16 Izvedbene uredbe Komisije (EU) 2023/1070', '1773rec18'),
    ('sl', 'členom 170(1) Delegirane uredbe (EU) 2015/2446', '1773art5'),
    # hr
    ('hr', 'članak 5. Uredbe (EU) br. 182/2011', '93(2)'),
    ('hr', 'članak 8. Uredbe (EU) br. 182/2011', '93(3)'),
    ('hr', 'člankom 28. stavkom 2. Uredbe (EZ) br. 45/2001', 'rec172'),
    ('hr', 'člankom 1. stavkom 1. točkom 2. Direktive 2015/1535', '4(25)'),
    ('hr', 'članka 25. stavka 6. Direktive 95/46/EZ', '25(6)'),
    ('hr', 'članku 7. stavku 2.a Uredbe (EU) 2016/1036', '825rec21'),
    ('hr', 'člankom 5. stavkom 9. Uredbe (EU) 2016/1036', '825art4'),
    ('hr', 'članku 16. Provedbene uredbe Komisije (EU) 2023/1070', '1773rec18'),
    ('hr', 'člankom 170. stavkom 1. Delegirane uredbe (EU) 2015/2446', '1773art5'),
    # bg
    ('bg', 'член 5 от Регламент (ЕС) № 182/2011', '93(2)'),
    ('bg', 'член 8 от Регламент (ЕС) № 182/2011', '93(3)'),
    ('bg', 'член 28, параграф 2 от Регламент (ЕО) № 45/2001', 'rec172'),
    ('bg', 'член 1, параграф 1, точка б) от Директива (ЕС) 2015/1535', '4(25)'),
    ('bg', 'член 25, параграф 6 от Директива 95/46/ЕО', '25(6)'),
    ('bg', 'член 7, параграф 2а от Регламент (ЕС) 2016/1036', '825rec21'),
    ('bg', 'член 5, параграф 9 от Регламент (ЕО) 2016/1036', '825art4'),
    ('bg', 'член 16 от Регламент за изпълнение (ЕС) 2023/1070', '1773rec18'),
    ('bg', 'член 170, параграф 1 от Делегиран регламент (ЕС) 2015/2446', '1773art5'),
    # el
    ('el', 'άρθρο 5 του κανονισμού (ΕΕ) αριθ. 182/2011', '93(2)'),
    ('el', 'άρθρο 8 του κανονισμού (ΕΕ) αριθ. 182/2011', '93(3)'),
    ('el', 'άρθρο 28 παράγραφος 2 του κανονισμού (ΕΚ) αριθ. 45/2001', 'rec172'),
    ('el', 'άρθρου 1 παράγραφος 1 στοιχείο β) της οδηγίας (ΕΕ) 2015/1535', '4(25)'),
    ('el', 'άρθρου 25 παράγραφος 6 της οδηγίας 95/46/ΕΚ', '25(6)'),
    ('el', 'άρθρο 7 παράγραφος 2α του κανονισμού (ΕΕ) 2016/1036', '825rec21'),
    ('el', 'άρθρο 5 παράγραφος 9 του κανονισμού (ΕΕ) 2016/1036', '825art4'),
    ('el', 'άρθρο 16 του εκτελεστικού κανονισμού (ΕΕ) 2023/1070', '1773rec18'),
    ('el', 'άρθρο 170 παράγραφος 1 του κατ’ εξουσιοδότηση κανονισμού (ΕΕ) 2015/2446', '1773art5'),
    # ro
    ('ro', 'articolul 5 din Regulamentul (UE) nr. 182/2011', '93(2)'),
    ('ro', 'articolul 8 din Regulamentul (UE) nr. 182/2011', '93(3)'),
    ('ro', 'articolul 28 alineatul (2) din Regulamentul (CE) nr. 45/2001', 'rec172'),
    ('ro', 'articolului 25 alineatul (6) din Directiva 95/46/CE', '25(6)'),
    ('ro', 'articolul 7 alineatul (2a) din Regulamentul (UE) 2016/1036', '825rec21'),
    ('ro', 'articolului 5 alineatul (9) din Regulamentul (UE) 2016/1036', '825art4'),
    ('ro', 'articolul 16 din Regulamentul de punere în aplicare (UE) 2023/1070', '1773rec18'),
    ('ro', 'articolul 170 alineatul (1) din Regulamentul delegat (UE) 2015/2446', '1773art5'),
    ('ro', 'Articolul 71 din Regulamentul (CEE) nr. 2309/93 al Consiliului din 22 iulie 1993', '726rec1'),
    # mt
    ('mt', 'Artikolu 5 tar-Regolament (UE) Nru 182/2011', '93(2)'),
    ('mt', 'Artikolu 8 tar-Regolament (UE) Nru 182/2011', '93(3)'),
    ('mt', 'Artikolu 28(2) tar-Regolament (KE) Nru 45/2001', 'rec172'),
    ('mt', 'Artikolu 1(1) tad-Direttiva (UE) 2015/1535', '4(25)'),
    ('mt', 'Artikolu 25(6) tad-Direttiva 95/46/KE', '25(6)'),
    ('mt', 'Artikolu 7(2a) tar-Regolament (UE) 2016/1036', '825rec21'),
    ('mt', 'Artikolu 5(9) tar-Regolament (UE) 2016/1036', '825art4'),
    ('mt', 'Artikolu 16 tar-Regolament ta’ Implimentazzjoni tal-Kummissjoni (UE) 2023/1070', '1773rec18'),
    ('mt', 'Artikolu 170(1) tar-Regolament Delegat (UE) 2015/2446', '1773art5'),
    # ga
    ('ga', 'Airteagal 5 de Rialachán (AE) Uimh. 182/2011', '93(2)'),
    ('ga', 'Airteagal 8 de Rialachán (AE) Uimh. 182/2011', '93(3)'),
    ('ga', 'Airteagal 28(2) de Rialachán (CE) Uimh. 45/2001', 'rec172'),
    ('ga', 'Airteagal 1(1) de Threoir (AE) 2015/1535', '4(25)'),
    ('ga', 'Airteagal 25(6) de Threoir 95/46/CE', '25(6)'),
    ('ga', 'Airteagal 7(2a) de Rialachán (AE) 2016/1036', '825rec21'),
    ('ga', 'Airteagal 5(9) de Rialachán (AE) 2016/1036', '825art4'),
    ('ga', 'Airteagal 16 de Rialachán Cur Chun Feidhme (AE) 2023/1070', '1773rec18'),
    ('ga', 'Airteagal 170(1) de Rialachán Tarmligthe (AE) 2015/2446', '1773art5'),
    # hu
    ('hu', 'a 182/2011/EU rendelet 5. cikkét', '93(2)'),
    ('hu', 'a 45/2001/EK rendelet 28. cikkének', 'rec172'),
    ('hu', 'az (EU) 2015/1535 európai parlamenti és tanácsi irányelv (19) 1. cikke', '4(25)'),
    ('hu', 'a 95/46/EK irányelv 25. cikk', '25(6)'),
    ('hu', '(EU) 2016/1036 rendelet 7. cikkének', '825rec21'),
    ('hu', 'az (EU) 2016/1036 rendelet 5. cikkének', '825art4'),
    ('hu', 'az (EU) 2023/1070 bizottsági végrehajtási rendelet (7) 16. cikkében', '1773rec18'),
    ('hu', 'az (EU) 2015/2446 felhatalmazáson alapuló rendelet 170. cikkének', '1773art5'),
    # fi
    ('fi', 'asetuksen (EU) N:o 182/2011 5 artiklaa', '93(2)'),
    ('fi', 'asetuksen (EU) N:o 182/2011 8 artiklaa', '93(3)'),
    ('fi', 'asetuksen (EY) N:o 45/2001 28 artiklan', 'rec172'),
    ('fi', 'direktiivin (EU) 2015/1535 (19) 1 artiklan', '4(25)'),
    ('fi', 'direktiivin 95/46/EY 25 artiklan', '25(6)'),
    ('fi', 'asetuksen (EU) 2016/1036 7 artiklan', '825rec21'),
    ('fi', 'asetuksen (EU) 2016/1036 5 artiklan', '825art4'),
    ('fi', 'asetuksen (EU) 2023/1070 (7) 16 artiklassa', '1773rec18'),
    ('fi', 'asetuksen (EU) 2015/2446 170 artiklan', '1773art5'),
    # et
    ('et', 'määruse (EL) nr 182/2011 artiklit 5', '93(2)'),
    ('et', 'määruse (EL) nr 182/2011 artiklit 8', '93(3)'),
    ('et', 'määruse (EÜ) nr 45/2001 artikli 28', 'rec172'),
    ('et', 'direktiivi (EL) 2015/1535 (19) artikli 1', '4(25)'),
    ('et', 'direktiivi 95/46/EÜ artikli 25', '25(6)'),
    ('et', 'Määruse (EL) 2016/1036 artikli 7', '825rec21'),
    ('et', 'määruse (EL) 2016/1036 artikli 5', '825art4'),
    ('et', 'määruse (EL) 2023/1070 (7) artiklis 16', '1773rec18'),
    ('et', 'määruse (EL) 2015/2446 artikli 170', '1773art5'),
    # lv
    ('lv', 'Regulas (ES) Nr. 182/2011 5. pantu', '93(2)'),
    ('lv', 'Regulas (ES) Nr. 182/2011 8. pantu', '93(3)'),
    ('lv', 'Regulas (EK) Nr. 45/2001 28. panta', 'rec172'),
    ('lv', 'Direktīvas (ES) 2015/1535 (19) 1. panta', '4(25)'),
    ('lv', 'Direktīvas 95/46/EK 25. panta', '25(6)'),
    ('lv', 'Regulas (ES) 2016/1036 7. panta', '825rec21'),
    ('lv', 'Regulas (ES) 2016/1036 5. panta', '825art4'),
    ('lv', 'regulas (ES) 2023/1070 (7) 16. pantā', '1773rec18'),
    ('lv', 'regulas (ES) 2015/2446 170. panta', '1773art5'),
    # lt
    ('lt', 'Reglamento (ES) Nr. 182/2011 5 straipsnis', '93(2)'),
    ('lt', 'Reglamento (ES) Nr. 182/2011 8 straipsnis', '93(3)'),
    ('lt', 'Reglamento (EB) Nr. 45/2001 28 straipsnio', 'rec172'),
    ('lt', 'direktyvos (ES) 2015/1535 (19) 1 straipsnio', '4(25)'),
    ('lt', 'Direktyvos 95/46/EB 25 straipsnio', '25(6)'),
    ('lt', 'Reglamento (ES) 2016/1036 7 straipsnio', '825rec21'),
    ('lt', 'Reglamento (ES) 2016/1036 5 straipsnio', '825art4'),
    ('lt', 'reglamento (ES) 2023/1070 (7) 16 straipsnyje', '1773rec18'),
    ('lt', 'reglamento (ES) 2015/2446 170 straipsnio', '1773art5'),
)

#: (language, what it is, the text): a number run that is no act — the OJ's page, a formula — and the
#: sentence of 32016R0679 Art. 2(3) naming Regulation (EC) No 45/2001 and citing the GDPR's own Article 98
NEGATIVES: tuple[tuple[str, str, str], ...] = (
    ('en', 'page', '4.5.2016 EN Official Journal of the European Union L 119/1'),
    ('en', 'formula', 'hall be used: CF4 emissions [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('en', 'this-act',
     'Regulation (EC) No 45/2001 and other Union legal acts applicable to such processing of personal data shall be '
     'adapted to the principles and rules of this Regulation in accordance with Article 98. 4.'),
    ('fr', 'page', "4.5.2016 FR Journal officiel de l'Union européenne L 119/1"),
    ('fr', 'formula', 'sions de PFC: CF4 emissions [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('fr', 'this-act',
     "Le règlement (CE) no 45/2001 et les autres actes juridiques de l'Union applicables audit traitement des "
     'données à caractère personnel sont adaptés aux principes et aux règles du présent règlement conformément à '
     "l'article 98. 4."),
    ('de', 'page', '4.5.2016 DE Amtsblatt der Europäischen Union L 119/1'),
    ('de', 'formula', 'zu verwenden: CF4 emissions [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('de', 'this-act',
     'Die Verordnung (EG) Nr. 45/2001 und sonstige Rechtsakte der Union, die diese Verarbeitung personenbezogener '
     'Daten regeln, werden im Einklang mit Artikel 98 an die Grundsätze und Vorschriften der vorliegenden Verordnung '
     'angepasst.'),
    ('it', 'page', "4.5.2016 IT Gazzetta ufficiale dell'Unione europea L 119/1"),
    ('it', 'formula', 'seguenti: emissioni di CF4 [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('it', 'this-act',
     "Il regolamento (CE) n. 45/2001 e gli altri atti giuridici dell'Unione applicabili a tale trattamento di dati "
     "personali devono essere adeguati ai principi e alle norme del presente regolamento conformemente all'articolo "
     '98. 4.'),
    ('es', 'page', '4.5.2016 ES Diario Oficial de la Unión Europea L 119/1'),
    ('es', 'formula', 's ecuaciones: CF4 emissions [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('es', 'this-act',
     'El Reglamento (CE) n.o 45/2001 y otros actos jurídicos de la Unión aplicables a dicho tratamiento de datos de '
     'carácter personal se adaptarán a los principios y normas del presente Reglamento de conformidad con su '
     'artículo 98. 4.'),
    ('pt', 'page', '4.5.2016 PT Jornal Oficial da União Europeia L 119/1'),
    ('pt', 'formula', 's equações: Emissões de CF4 [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('pt', 'this-act',
     'O Regulamento (CE) n.o 45/2001, bem como outros atos jurídicos da União aplicáveis ao tratamento de dados '
     'pessoais, são adaptados aos princípios e regras do presente regulamento nos termos previstos no artigo 98.o. '
     '4.'),
    ('nl', 'page', '4.5.2016 NL Publicatieblad van de Europese Unie L 119/1'),
    ('nl', 'formula', 'ngen gebruikt: CF4-emissies [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('nl', 'this-act',
     'Verordening (EG) nr. 45/2001 en andere rechtshandelingen van de Unie die van toepassing zijn op een dergelijke '
     'verwerking van persoonsgegevens worden overeenkomstig artikel 98 aan de beginselen en regels van de '
     'onderhavige verordening aangepast. 4.'),
    ('da', 'page', '4.5.2016 DA Den Europæiske Unions Tidende L 119/1'),
    ('da', 'formula', '-emissioner: CF4-emissioner [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('da', 'this-act',
     'Forordning (EF) nr. 45/2001 og andre EU-retsakter, der finder anvendelse på sådan behandling af '
     'personoplysninger, tilpasses til principperne og bestemmelserne i nærværende forordning i overensstemmelse med '
     'artikel 98. 4.'),
    ('sv', 'page', '4.5.2016 SV Europeiska unionens officiella tidning L 119/1'),
    ('sv', 'formula', 'rkarbonutsläpp: CF4-utsläpp [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('sv', 'this-act',
     'Förordning (EG) nr 45/2001 och de av unionens övriga rättsakter som är tillämpliga på sådan behandling av '
     'personuppgifter ska anpassas till principerna och bestämmelserna i denna förordning i enlighet med artikel 98. '
     '4.'),
    ('cs', 'page', '4.5.2016 CS Úřední věstník Evropské unie L 119/1'),
    ('cs', 'formula', '= AEM × (SEFCF4/1 000) × PrAl'),
    ('cs', 'this-act',
     'Nařízení (ES) č. 45/2001 a další právní akty Unie týkající se takového zpracování osobních údajů jsou '
     'uzpůsobeny zásadám a pravidlům tohoto nařízení podle článku 98. 4.'),
    ('sk', 'page', '4.5.2016 SK Úradný vestník Európskej únie L 119/1'),
    ('sk', 'formula', '= AEM × (SEFCF4/1 000) × PrAl'),
    ('sk', 'this-act',
     'Nariadenie (ES) č. 45/2001 a ostatné právne akty Únie uplatniteľné na takéto spracúvanie osobných údajov sa '
     'upravia podľa zásad a pravidiel tohto nariadenia v súlade s článkom 98. 4.'),
    ('pl', 'page', '4.5.2016 PL Dziennik Urzędowy Unii Europejskiej L 119/1'),
    ('pl', 'formula', 'pujące równania: emisje CF4 [t] = AEM × (SEFCF4/1 000) × PrA'),
    ('pl', 'this-act',
     'Rozporządzenie (WE) nr 45/2001 oraz inne unijne akty prawne mające zastosowanie do takiego przetwarzania '
     'danych osobowych zostają dostosowane do zasad i przepisów niniejszego rozporządzenia zgodnie z art. 98. 4.'),
    ('sl', 'page', '4.5.2016 SL Uradni list Evropske unije L 119/1'),
    ('sl', 'formula', 'slednji enačbi: emisije CF4 [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('sl', 'this-act',
     'Uredba (ES) št. 45/2001 in drugi pravni akti Unije, ki se uporabljajo za tako obdelavo osebnih podatkov, se '
     'prilagodijo načelom in pravilom iz te uredbe v skladu s členom 98. 4.'),
    ('hr', 'page', '4.5.2016 HR Službeni list Europske unije L 119/1'),
    ('hr', 'formula', 'deće jednadžbe: emisije CF4 [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('hr', 'this-act',
     'Uredba (EZ) br. 45/2001 i drugi pravni akti Unije primjenjivi na takvu obradu osobnih podataka moraju se '
     'prilagoditi načelima i pravilima ove Uredbe u skladu s člankom 98. 4.'),
    ('bg', 'page', '4.5.2016 BG Официален вестник на Европейския съюз L 119/1'),
    ('bg', 'formula', 'ните формули: Емисии на CF4 [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('bg', 'this-act',
     'Регламент (ЕО) № 45/2001 и другите правни актове на Съюза, приложими към подобна обработка на лични данни, се '
     'адаптират към принципите и правилата на настоящия регламент в съответствие с член 98. 4.'),
    ('el', 'page', '4.5.2016 EL Επίσημη Εφημερίδα της Ευρωπαϊκής Ένωσης L 119/1'),
    ('el', 'formula', 'θες εξισώσεις: εκπομπές CF4 [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('el', 'this-act',
     'Ο κανονισμός (EΚ) αριθ. 45/2001 και άλλες νομικές πράξεις της Ένωσης εφαρμοστέες σε μια τέτοια επεξεργασία '
     'δεδομένων προσωπικού χαρακτήρα προσαρμόζονται στις αρχές και τους κανόνες του παρόντος κανονισμού σύμφωνα με '
     'το άρθρο 98. 4.'),
    ('ro', 'page', '4.5.2016 RO Jurnalul Oficial al Uniunii Europene L 119/1'),
    ('ro', 'formula', 'le ecuații: Emisiile de CF4 [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('ro', 'this-act',
     'Regulamentul (CE) nr. 45/2001 și alte acte juridice ale Uniunii aplicabile unei asemenea prelucrări a datelor '
     'cu caracter personal se adaptează la principiile și normele din prezentul regulament în conformitate cu '
     'articolul 98.'),
    ('mt', 'page', '4.5.2016 MT Il-Ġurnal Uffiċjali tal-Unjoni Ewropea L 119/1'),
    ('mt', 'formula', 'jiet ta’ PFC: CF4 emissions [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('mt', 'this-act',
     'Ir-Regolament (KE) Nru 45/2001 u atti legali oħrajn tal-Unjoni applikabbli għal tali pproċessar tad-data '
     "personali għandhom jiġu adattati għall-prinċipji u r-regoli ta' dan ir-Regolament skont l-Artikolu 98. 4."),
    ('ga', 'page', '15.9.2023 GA Iris Oifigiúil an Aontais Eorpaigh L 228/94'),
    ('ga', 'formula', 'a chinneadh: CF4 emissions [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('ga', 'this-act',
     'Maidir le Rialachán (CE) Uimh. 45/2001 agus le gníomhartha dlí eile de chuid an Aontais atá infheidhme maidir '
     'leis an bpróiseáil sin, déanfar iad a chur in oiriúint do phrionsabail agus do rialacha an Rialacháin seo i '
     'gcomhréir le hAirteagal 98. 4.'),
    ('hu', 'page', '2016.5.4. HU Az Európai Unió Hivatalos Lapja L 119/1'),
    ('hu', 'formula', 'használni: CF4-kibocsátások [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('hu', 'this-act',
     'A 45/2001/EK rendeletet, valamint a személyes adatok ilyen kezelésére vonatkozó egyéb uniós jogi aktusokat a '
     '98. cikkel összhangban hozzá kell igazítani az e rendeletben foglalt elvekhez és szabályokhoz.'),
    ('fi', 'page', '4.5.2016 FI Euroopan unionin virallinen lehti L 119/1'),
    ('fi', 'formula', 'ia yhtälöitä: CF4 emissions [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('fi', 'this-act',
     'Asetus (EY) N:o 45/2001 ja muut tällaiseen henkilötietojen käsittelyyn sovellettavat unionin säädökset on '
     'mukautettava tämän asetuksen periaatteiden ja sääntöjen mukaisiksi 98 artiklaa noudattaen. 4.'),
    ('et', 'page', '4.5.2016 ET Euroopa Liidu Teataja L 119/1'),
    ('et', 'formula', '= AEM × (SEFCF4/1 000) × PrAl'),
    ('et', 'this-act',
     'Määrust (EÜ) nr 45/2001 ja muid sellisele isikuandmete töötlemisele kohaldatavaid liidu õigusakte tuleb '
     'kooskõlas artikliga 98 kohandada vastavalt käesoleva määruse põhimõtetele ja normidele. 4.'),
    ('lv', 'page', '4.5.2016 LV Eiropas Savienības Oficiālais Vēstnesis L 119/1'),
    ('lv', 'formula', 'vienādojumus: CF4 emisijas [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('lv', 'this-act',
     'Regulu (EK) Nr. 45/2001 un citus Savienības tiesību aktus, ko piemēro šādai personas datu apstrādei, pielāgo '
     'šīs regulas principiem un noteikumiem saskaņā ar 98. pantu. 4.'),
    ('lt', 'page', '2016 5 4 LT Europos Sąjungos oficialusis leidinys L 119/1'),
    ('lt', 'formula', 'ygtys: išmetamas CF4 kiekis [t] = AEM × (SEFCF4/1 000) × PrAl'),
    ('lt', 'this-act',
     'Reglamentas (EB) Nr. 45/2001 ir kiti Sąjungos teisės aktai, taikytini tokiam asmens duomenų tvarkymui, pagal '
     '98 straipsnį pritaikomi pagal šio reglamento principus ir taisykles. 4.'),
)


def _pairs(text: str) -> list[tuple[str, str]]:
    return [(c.keys[0].removeprefix("celex:"), c.article) for c in eref.article_citations(text)]


@pytest.mark.parametrize("lang, text, provision", ROWS, ids=[f"{r[0]}-{r[2]}" for r in ROWS])
def test_a_real_citation_names_its_act_and_its_article(lang, text, provision):
    _where, celex, article = PROVISIONS[provision]
    assert _pairs(text)[:1] == [(celex, article)], (lang, text)
    [c] = [c for c in eref.citations(text) if c["celex"] == celex]
    assert c["art"], "the article stays on the entity's label"


def test_every_language_has_its_forms_its_source_and_five_real_citations():
    langs = {f.lang for f in eref._FORMS}
    assert langs == set(src.LANGUAGES) and len(eref._FORMS) == 24
    assert all(f.source and re.search(r"3\d{4}[RLD]\d{4}", f.source) for f in eref._FORMS), \
        "each language says where its forms were read"
    counts = collections.Counter(r[0] for r in ROWS)
    assert set(counts) == langs and min(counts.values()) >= 5, counts
    assert {n[0] for n in NEGATIVES} == langs and len(NEGATIVES) == 3 * 24


@pytest.mark.parametrize("lang, kind, text", NEGATIVES, ids=[f"{n[0]}-{n[1]}" for n in NEGATIVES])
def test_no_act_or_no_article_of_a_named_act_pins_nothing(lang, kind, text):
    """A number run that is no act — the Official Journal's page "L 119/1",
    a formula's "CF4/1 000" — names nothing; an article of the act the text
    is in ("Article 98" of the GDPR, in the sentence naming Regulation (EC)
    No 45/2001) is no article of the act it names."""
    assert eref.article_citations(text) == [], text
    if kind == "this-act":
        assert eref.citations(text) == [{"celex": "32001R0045", "art": None}], text
    else:
        assert eref.citations(text) == [], text


def test_the_reversed_look_back_is_the_grammar():
    """`_BEFORE` is the grammar, `_BEFORE_REV` how it is run (one anchored
    match on the reversed window): the same type word, organisation and "No"
    at every number run of every text here, at every window length."""
    for text in [r[1] for r in ROWS] + [r[1] for r in LISTS] + [n[2] for n in NEGATIVES] \
            + [q for q, *_ in REAL_TEXT_CASES]:
        for m in eref._NUMBERS.finditer(text):
            for lo in (120, 50, 20, 8):
                w = text[max(0, m.start() - lo):m.start()]
                f, r = eref._BEFORE.search(w), eref._BEFORE_REV.match(w[::-1])
                assert (f is None) == (r is None), w
                if f is not None:
                    assert f.start() == len(w) - r.end(), w
                    assert [bool(f.group(g)) for g in ("R", "L", "D", "org", "no")] == \
                        [bool(r.group(g)) for g in ("R", "L", "D", "org", "no")], w
    with pytest.raises(ValueError):
        eref._reversed(r"(?<!\d)x")


#: shapes the harvest found, each verbatim, and what they cite
REAL_TEXT_CASES = (
    # 32022R2065 (de) Art. 89(1): a range, never Article "12 bis"
    ("Die Artikel 12 bis 15 der Richtlinie 2000/31/EG werden gestrichen.", "32000L0031", "12"),
    # 32022R2065 (pl), 32018R0825 (lv): the OJ's "No" before a number the year leads
    ("art. 5 ust. 9 rozporządzenia (UE) nr 2016/1036", "32016R1036", "5"),
    ("Regulas (ES) Nr. 2016/1037 10. panta 11. punktu", "32016R1037", "10"),
    # 32016R0679 (hu) Art. 94: a second organisation after the suffix
    ("az Európai Közösségek Statisztikai Hivatala részére történő továbbításáról szóló 1101/2008/EK, Euratom "
     "európai parlamenti és tanácsi rendelet", "32008R1101", None),
    # 32022R2065 (de) recital 10: "Richtlinien" is no "Richtlinie" with an Italian "n"
    ("die Richtlinien 2001/95/EG", "32001L0095", None),
    # 32016R0679 (el) Art. 2(3): an organisation in two scripts, Latin E and Greek Κ
    ("Ο κανονισμός (EΚ) αριθ. 45/2001", "32001R0045", None),
    # 32018R0825 (bg) Art. 4: the OJ's (ЕО) on a 2016 act — the year first all the same
    ("член 5, параграф 9 от Регламент (ЕО) 2016/1036", "32016R1036", "5"),
    # 32016R0679 (fr) Art. 4(25): the first article, "1er"
    ("l'article 1er, paragraphe 1, point b), de la directive (UE) 2015/1535", "32015L1535", "1"),
    # 32016R0679 (sv) recital 103: two articles in the Swedish dotted form, the first named
    ("artikel 25.6 eller 26.4 i direktiv 95/46/EG", "31995L0046", "25"),
    # 32016R0679 (ga) recital 103: the Irish OJ's own "Regulation" for the directive, read as written
    ("Airteagal 26(4) de Rialachán 95/46/CE", "31995R0046", "26"),
    # tests/fixtures/eurlex/eurlex-dumpfirst/formex/31972R1473 (it): a heading, then the sentence — the
    # article nearest the act, never the heading's
    ("Articolo 84 All'articolo 2, ultimo comma del regolamento (CEE, Euratom, CECA) n. 259/68", "31968R0259", "2"),
    # 32016R0679 (en) recital 103: a list starts at its first article, in every language alike (LISTS)
    ("Article 25(6) or Article 26(4) of Directive 95/46/EC", "31995L0046", "25"),
    ("čl. 25 odst. 6 nebo čl. 26 odst. 4 směrnice 95/46/ES", "31995L0046", "25"),
)


def _fixture_text(path) -> str:
    import html
    raw = path.read_text(encoding="utf-8", errors="replace")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


@pytest.mark.parametrize("item, acts", [
    # the legacy HTML of 31992L0043: "Nõukogu direktiiv 92/43/EMÜ", "A Tanács 92/43/EGK irányelve", "Tarybos
    # Direktyva 92/43/EEB", "Padomes Direktīva 92/43/EEK", "Id-Direttiva tal-Kunsill 92/43/KEE", and the
    # footnote's "91/244/…" amending directive
    ("rendition/31992L0043et.html", {"31992L0043", "31991L0244"}),
    ("rendition/31992L0043hu.html", {"31992L0043", "31991L0244"}),
    ("rendition/31992L0043lt.html", {"31992L0043", "31991L0244"}),
    ("rendition/31992L0043lv.html", {"31992L0043", "31991L0244"}),
    ("rendition/31992L0043mt.html", {"31992L0043", "31991L0244"}),
    # a Czech corrigendum: "směrnice Komise (EU) 2019/1922", "směrnice Evropského parlamentu a Rady 2009/48/ES"
    ("coverage/32019L1922R01_cs/L_2019313CS.01010101.xml", {"32019L1922", "32009L0048"}),
    ("rendition/32004R0726ro.html", {"32004R0726", "31993R2309"}),
])
def test_the_fixtures_name_their_acts_in_their_languages(item, acts):
    from tests.test_eurlex import FIX
    got = {c["celex"] for c in eref.citations(_fixture_text(FIX / item))}
    assert acts <= got, (item, sorted(got))


@pytest.mark.parametrize("text, celex, article", REAL_TEXT_CASES)
def test_the_shapes_the_harvest_found(text, celex, article):
    got = [c for c in eref.citations(text) if c["celex"] == celex]
    assert got, (text, eref.citations(text))
    assert _pairs(text)[:1] == ([(celex, article)] if article else []), text


def test_an_article_is_the_query_s_words_and_one_spelling():
    [c] = eref.article_citations("az (EU) 2016/1036 rendelet 5. cikkének (9) bekezdése")
    assert (c.keys, c.article, c.label) == ((f"celex:{ACT}",), "5", "5. cikkének")
    [c] = eref.article_citations("άρθρο 14α του κανονισμού (ΕΕ) 2016/1036")
    assert (c.article, c.label) == ("14a", "άρθρο 14α"), "Greek α is the letter a, as the label side reads it"
    [c] = eref.article_citations("artigo 14.º-A do Regulamento (UE) 2016/1036")
    assert c.article == "14a"
    # the same act's two articles are two citations; the act once in `citations`
    got = eref.article_citations("Article 5 of Regulation (EU) 2016/1036 and Article 6 of Regulation (EU) 2016/1036")
    assert [c.article for c in got] == ["5", "6"]
    assert [c["celex"] for c in eref.citations("Article 5 of Regulation (EU) 2016/1036 and Article 6 of "
                                               "Regulation (EU) 2016/1036")] == [ACT]


def test_a_dense_text_in_any_script_stays_linear():
    """`citations` runs on every chunk at indexing: the look-back is one
    anchored match a number run, the article search skips what cannot start
    an article word."""
    import time
    dense = " ".join(r[1] for r in ROWS) * 20
    t = time.perf_counter()
    got = eref.citations(dense)
    assert time.perf_counter() - t < 2.0
    assert {c["celex"] for c in got} >= {p[1] for p in PROVISIONS.values()}


# ---- a list of articles before (or after) one act: each, in order ---------------------------------

#: the five lists of the five acts, as English reads them (the act and its articles, in the text's order):
#: a RANGE gives its two ends (`_list_articles` says why)
LIST_PROVISIONS = {
    "L1": ("32022R2065 recital 74: 'Articles 6 and 8 of Directive 2011/83/EU'", "32011L0083", ("6", "8")),
    "L2": ("32022R2065 recital 74: 'Articles 5 and 6 of Directive 2000/31/EC'", "32000L0031", ("5", "6")),
    "L3": ("32022R2065 recital 53, Art. 16: 'Articles 3 to 7 of Directive 2011/93/EU'", "32011L0093", ("3", "7")),
    "L4": ("32022R2065 Art. 89(1): 'Articles 12 to 15 of Directive 2000/31/EC'", "32000L0031", ("12", "15")),
    "L5": ("32016R0679 recital 106: 'Article 25(6) or Article 26(4) of Directive 95/46/EC'", "31995L0046", ("25", "26")),
}
#: the Irish text's own "Rialachán" for the directive (32016R0679 recital 106), read as written
_LIST_ACT = {("ga", "L5"): "31995R0046"}

#: (language, the list as that language's Official Journal writes it, verbatim from the same Cellar items, the list)
LISTS: tuple[tuple[str, str, str], ...] = (
    # en
    ('en', 'out in Articles 6 and 8 of Directive 2011/83/EU', 'L1'),
    ('en', '2005/29/EC, Articles 5 and 6 of Directive 2000/31/EC', 'L2'),
    ('en', 'to in Articles 3 to 7 of Directive 2011/93/EU', 'L3'),
    ('en', '2000/31/EC 1. Articles 12 to 15 of Directive 2000/31/EC', 'L4'),
    ('en', 'basis of Article 25(6) or Article 26(4) of Directive 95/46/EC', 'L5'),
    # fr
    ('fr', 'énoncées aux articles 6 et 8 de la directive 2011/83/UE', 'L1'),
    ('fr', '2005/29/CE, aux articles 5 et 6 de la directive 2000/31/CE', 'L2'),
    ('fr', 'visées aux articles 3 à 7 de la directive 2011/93/UE', 'L3'),
    ('fr', '1. Les articles 12 à 15 de la directive 2000/31/CE', 'L4'),
    ('fr', "base de l'article 25, paragraphe 6, ou de l'article 26, paragraphe 4, de la directive 95/46/CE", 'L5'),
    # de
    ('de', 'gemäß Artikel 6 und 8 der Richtlinie 2011/83/EU', 'L1'),
    ('de', '2005/29/EG, Artikel 5 und 6 der Richtlinie 2000/31/EG', 'L2'),
    ('de', 'nach den Artikeln 3 bis 7 der Richtlinie 2011/93/EU', 'L3'),
    ('de', '(1) Die Artikel 12 bis 15 der Richtlinie 2000/31/EG', 'L4'),
    ('de', 'Grundlage des Artikels 25 Absatz 6 oder des Artikels 26 Absatz 4 der Richtlinie 95/46/EG', 'L5'),
    # it
    ('it', 'agli articoli 6 e 8 della direttiva 2011/83/UE', 'L1'),
    ('it', 'agli articoli 5 e 6 della direttiva 2000/31/CE', 'L2'),
    ('it', 'agli articoli da 3 a 7 della direttiva 2011/93/UE', 'L3'),
    ('it', 'Gli articoli da 12 a 15 della direttiva 2000/31/CE', 'L4'),
    ('it', "dell'articolo 25, paragrafo 6, o dell'articolo 26, paragrafo 4, della direttiva 95/46/CE", 'L5'),
    # es
    ('es', 'los artículos 6 y 8 de la Directiva 2011/83/UE', 'L1'),
    ('es', 'los artículos 5 y 6 de la Directiva 2000/31/CE', 'L2'),
    ('es', 'los artículos 3 a 7 de la Directiva 2011/93/UE', 'L3'),
    ('es', 'los artículos 12 a 15 de la Directiva 2000/31/CE', 'L4'),
    ('es', 'base del artículo 25, apartado 6, o el artículo 26, apartado 4, de la Directiva 95/46/CE', 'L5'),
    # pt
    ('pt', 'estabelecidos nos artigos 6.o e 8.o da Diretiva 2011/83/UE', 'L1'),
    ('pt', '2005/29/CE, nos artigos 5.o e 6.o da Diretiva 2000/31/CE', 'L2'),
    ('pt', 'referem os artigos 3.o a 7.o da Diretiva 2011/93/UE', 'L3'),
    ('pt', '1. Os artigos 12.o a 15.o da Diretiva 2000/31/CE', 'L4'),
    ('pt', 'base no artigo 25.o, n.o 6, ou no artigo 26.o, n.o 4, da Diretiva 95/46/CE', 'L5'),
    # nl
    ('nl', 'van de artikelen 6 en 8 van Richtlijn 2011/83/EU', 'L1'),
    ('nl', '2005/29/EG, de artikelen 5 en 6 van Richtlijn 2000/31/EG', 'L2'),
    ('nl', 'in de artikelen 3 tot en met 7 van Richtlijn 2011/93/EU', 'L3'),
    ('nl', '1. De artikelen 12 tot en met 15 van Richtlijn 2000/31/EG', 'L4'),
    ('nl', 'grond van artikel 25, lid 6, of artikel 26, lid 4, van Richtlijn 95/46/EG', 'L5'),
    # da
    ('da', 'kravene i artikel 6 og 8 i direktiv 2011/83/EU', 'L1'),
    ('da', '2005/29/EF, artikel 5 og 6 i direktiv 2000/31/EF', 'L2'),
    ('da', 'omhandlet i artikel 3-7 i Europa-Parlamentets og Rådets direktiv 2011/93/EU', 'L3'),
    ('da', '2000/31/EF 1. Artikel 12-15 i direktiv 2000/31/EF', 'L4'),
    ('da', 'grundlag af artikel 25, stk. 6, eller artikel 26, stk. 4, i direktiv 95/46/EF', 'L5'),
    # sv
    ('sv', 'anges i artiklarna 6 och 8 i direktiv 2011/83/EU', 'L1'),
    ('sv', '2005/29/EG, artiklarna 5 och 6 i direktiv 2000/31/EG', 'L2'),
    ('sv', 'avses i artiklarna 3–7 i Europaparlamentets och rådets direktiv 2011/93/EU', 'L3'),
    ('sv', '1. Artiklarna 12–15 i direktiv 2000/31/EG', 'L4'),
    ('sv', 'grundval av artikel 25.6 eller 26.4 i direktiv 95/46/EG', 'L5'),
    # cs
    ('cs', 'stanovené v článcích 6 a 8 směrnice 2011/83/EU', 'L1'),
    ('cs', '2005/29/ES, článcích 5 a 6 směrnice 2000/31/ES', 'L2'),
    ('cs', 'uvedených v článcích 3 až 7 směrnice Evropského parlamentu a Rady 2011/93/EU', 'L3'),
    ('cs', '2000/31/ES 1. Články 12 až 15 směrnice 2000/31/ES', 'L4'),
    ('cs', 'na základě čl. 25 odst. 6 nebo čl. 26 odst. 4 směrnice 95/46/ES', 'L5'),
    # sk
    ('sk', 'stanovené v článkoch 6 a 8 smernice 2011/83/EÚ', 'L1'),
    ('sk', '2005/29/ES, článkoch 5 a 6 smernice 2000/31/ES', 'L2'),
    ('sk', 'uvedených v článkoch 3 až 7 smernice Európskeho parlamentu a Rady 2011/93/EÚ', 'L3'),
    ('sk', '2000/31/ES 1. Články 12 až 15 smernice 2000/31/ES', 'L4'),
    ('sk', 'základe článku 25 ods. 6 alebo článku 26 ods. 4 smernice 95/46/ES', 'L5'),
    # pl
    ('pl', 'określonych w art. 6 i 8 dyrektywy 2011/83/UE', 'L1'),
    ('pl', '2005/29/WE, art. 5 i 6 dyrektywy 2000/31/WE', 'L2'),
    ('pl', 'których mowa w art. 3–7 dyrektywy Parlamentu Europejskiego i Rady 2011/93/UE', 'L3'),
    ('pl', 'Uchyla się art. 12–15 dyrektywy 2000/31/WE', 'L4'),
    ('pl', 'podstawie art. 25 ust. 6 lub art. 26 ust. 4 dyrektywy 95/46/WE', 'L5'),
    # sl
    ('sl', 'zahtev iz členov 6 in 8 Direktive 2011/83/EU', 'L1'),
    ('sl', '2005/29/ES, členov 5 in 6 Direktive 2000/31/ES', 'L2'),
    ('sl', 'dejanji iz členov 3 do 7 Direktive 2011/93/EU', 'L3'),
    ('sl', '2000/31/ES 1. Členi 12 do 15 Direktive 2000/31/ES', 'L4'),
    ('sl', 'podlagi člena 25(6) ali člena 26(4) Direktive 95/46/ES', 'L5'),
    # hr
    ('hr', 'utvrđene u člancima 6. i 8. Direktive 2011/83/EU', 'L1'),
    ('hr', '2005/29/EZ, člancima 5. i 6. Direktive 2000/31/EZ', 'L2'),
    ('hr', 'iz članaka od 3. do 7. Direktive 2011/93/EU', 'L3'),
    ('hr', '2000/31/EZ 1. Članci od 12. do 15. Direktive 2000/31/EZ', 'L4'),
    ('hr', 'temelju članka 25. stavka 6. i članka 26. stavka 4. Direktive 95/46/EZ', 'L5'),
    # bg
    ('bg', 'изискванията по членове 6 и 8 от Директива 2011/83/ЕС', 'L1'),
    ('bg', '2005/29/ЕО, членове 5 и 6 от Директива 2000/31/ЕО', 'L2'),
    ('bg', 'посочени в членове 3 – 7 от Директива 2011/93/ЕС', 'L3'),
    ('bg', '2000/31/ЕО 1. Членове 12—15 от Директива 2000/31/ЕО', 'L4'),
    ('bg', 'основа на член 25, параграф 6 или член 26, параграф 4 от Директива 95/46/ЕО', 'L5'),
    # el
    ('el', 'ορίζονται στα άρθρα 6 και 8 της οδηγίας 2011/83/ΕΕ', 'L1'),
    ('el', '2005/29/ΕΚ, στα άρθρα 5 και 6 της οδηγίας 2000/31/ΕΚ', 'L2'),
    ('el', 'αναφέρονται στα άρθρα 3 έως 7 της οδηγίας 2011/93/ΕΕ', 'L3'),
    ('el', '2000/31/ΕΚ 1. Τα άρθρα 12 έως 15 της οδηγίας 2000/31/ΕΚ', 'L4'),
    ('el', 'βάσει του άρθρου 25 παράγραφος 6 ή του άρθρου 26 παράγραφος 4 της οδηγίας 95/46/ΕΚ', 'L5'),
    # ro
    ('ro', 'la articolele 6 și 8 din Directiva 2011/83/UE', 'L1'),
    ('ro', 'la articolele 5 și 6 din Directiva 2000/31/CE', 'L2'),
    ('ro', 'la articolele 3-7 din Directiva 2011/93/UE', 'L3'),
    ('ro', '1. Articolele 12-15 din Directiva 2000/31/CE', 'L4'),
    ('ro', 'temeiul articolului 25 alineatul (6) sau al articolului 26 alineatul (4) din Directiva 95/46/CE', 'L5'),
    # mt
    ('mt', 'stabbiliti fl-Artikoli 6 u 8 tad-Direttiva 2011/83/UE', 'L1'),
    ('mt', '2005/29/KE, fl-Artikoli 5 u 6 tad-Direttiva 2000/31/KE', 'L2'),
    ('mt', 'msemmija fl-Artikoli 3 sa 7 tad-Direttiva 2011/93/UE', 'L3'),
    ('mt', '1. L-Artikoli 12 sa 15 tad-Direttiva 2000/31/KE', 'L4'),
    ('mt', 'abbażi tal-Artikolu 25(6) jew l-Artikolu 26 (4) tad-Direttiva 95/46/KE', 'L5'),
    # ga
    ('ga', 'in Airteagail 6 agus 8 de Threoir 2011/83/AE', 'L1'),
    ('ga', '2005/29/CE, Airteagail 5 agus 6 de Threoir 2000/31/CE', 'L2'),
    ('ga', 'in Airteagail 3 go 7 de Threoir 2011/93/AE', 'L3'),
    ('ga', 'Scriostar Airteagail 12 go 15 de Threoir 2000/31/CE', 'L4'),
    ('ga', 'bhonn Airteagal 25(6) nó Airteagal 26(4) de Rialachán 95/46/CE', 'L5'),
    # hu
    ('hu', 'kötelezettségeiknek, különösen a 2011/83/EU irányelv 6. és 8. cikkében, a', 'L1'),
    ('hu', '2005/29/EK irányelv 7. cikkében, a 2000/31/EK irányelv 5. és 6. cikkében, valamint', 'L2'),
    ('hu',
     'ismeretére vagy tudomására ad okot. A 2011/93/EU európai parlamenti és tanácsi irányelv (26) 3–7. cikkében '
     'említett', 'L3'),
    ('hu', '2000/31/EK irányelv módosításai (1) A 2000/31/EK irányelv 12–15. cikkét el', 'L4'),
    ('hu', 'működésének ellenőrzését, ideértve a 95/46/EK irányelv 25. cikk (6) vagy a 26. cikk (4)', 'L5'),
    # fi
    ('fi', 'vaatimuksia, joista säädetään direktiivin 2011/83/EU 6 ja 8 artiklassa, direktiivin', 'L1'),
    ('fi', '2005/29/EY 7 artiklassa, direktiivin 2000/31/EY 5 ja 6 artiklassa sekä', 'L2'),
    ('fi', 'parlamentin ja neuvoston direktiivin 2011/93/EU (26) 3–7 artiklassa tarkoitetuista', 'L3'),
    ('fi', 'muuttaminen 1. Kumotaan direktiivin 2000/31/EY 12–15 artikla. 2.', 'L4'),
    ('fi', 'koskevien päätösten, kuten direktiivin 95/46/EY 25 artiklan 6 kohdan tai 26 artiklan 4', 'L5'),
    # et
    ('et', 'Parlamendi ja nõukogu direktiivi 2011/83/EL artiklites 6 ja 8, direktiivi 2005/29/EÜ', 'L1'),
    ('et', '2005/29/EÜ artiklis 7, direktiivi 2000/31/EÜ artiklites 5 ja 6 ning direktiivi', 'L2'),
    ('et', 'Parlamendi ja nõukogu direktiivi 2011/93/EL (26) artiklites 3–7 osutatud süütegudega', 'L3'),
    ('et',
     'LÕPPSÄTTED Artikkel 89 Direktiivi 2000/31/EÜ muutmine 1. Direktiivi 2000/31/EÜ artiklid 12–15 jäetakse '
     'välja.', 'L4'),
    ('et', 'organisatsioonis, ning direktiivi 95/46/EÜ artikli 25 lõike 6 või artikli 26 lõike 4', 'L5'),
    # lv
    ('lv', 'Parlamenta un Padomes Direktīvas 2011/83/ES 6. un 8. pantā, Direktīvas', 'L1'),
    ('lv', '2005/29/EK 7. pantā, Direktīvas 2000/31/EK 5. un 6. pantā un', 'L2'),
    ('lv', 'Parlamenta un Padomes Direktīvas 2011/93/ES (26) 3.–7. pantā, minētajiem', 'L3'),
    ('lv', 'NOTEIKUMI 89. pants Direktīvas 2000/31/EK grozījumi 1. Direktīvas 2000/31/EK 12.–15. pantu svītro.', 'L4'),
    ('lv', 'pieņemti, pamatojoties uz Direktīvas 95/46/EK 25. panta 6. punktu vai 26. panta 4.', 'L5'),
    # lt
    ('lt', 'reikalavimus, išdėstytus Direktyvos 2011/83/ES 6 bei 8 straipsniuose, Direktyvos', 'L1'),
    ('lt', '2005/29/EB 7 straipsnyje, Direktyvos 2000/31/EB 5 bei 6 straipsniuose ir', 'L2'),
    ('lt', 'Parlamento ir Tarybos direktyvos 2011/93/ES (26) 3–7 straipsniuose, teikimą,', 'L3'),
    ('lt', 'daliniai pakeitimai 1. Direktyvos 2000/31/EB 12–15 straipsniai išbraukiami.', 'L4'),
    ('lt', 'sprendimų, priimtų remiantis Direktyvos 95/46/EB 25 straipsnio 6 dalimi arba 26 straipsnio 4', 'L5'),
)


@pytest.mark.parametrize("lang, text, key", LISTS, ids=[f"{r[0]}-{r[2]}" for r in LISTS])
def test_each_article_of_a_list_is_cited_in_order(lang, text, key):
    _where, celex, arts = LIST_PROVISIONS[key]
    celex = _LIST_ACT.get((lang, key), celex)
    assert [a for c, a in _pairs(text) if c == celex] == list(arts), (lang, text, _pairs(text))


def test_every_language_has_its_five_lists_and_its_words_for_them():
    counts = collections.Counter(r[0] for r in LISTS)
    assert set(counts) == {f.lang for f in eref._FORMS} and set(counts.values()) == {5}
    assert all(f.conj for f in eref._FORMS if f.art), "each language joining articles before the act says how"
    assert all(f.num_word for f in eref._FORMS if f.after and f.lang != "et"), \
        "each language numbering an article before its word (hu fi lv lt) names the word"


@pytest.mark.parametrize("text", [
    # 32023R1773 (fr, it, nl) Annex III: a range of PARAGRAPHS is the article's own, never articles
    "établis à l’article 29, paragraphes 2 à 7 et paragraphe 10, de la directive (UE) 2018/2001",
    "di cui all’articolo 29, paragrafi da 2 a 7 e 10, della direttiva (UE) 2018/2001",
    "stgesteld in artikel 29, leden 2 tot en met 7, en lid 10, van Richtlijn (EU) 2018/2001",
    # (en) the paragraphs first
    "laid down in paragraphs 2 to 7 and 10 of Article 29 of Directive (EU) 2018/2001",
])
def test_a_list_of_paragraphs_is_one_article(text):
    assert _pairs(text) == [("32018L2001", "29")], text
    [c] = eref.citations(text)
    assert [n for _label, n in eref._list_articles(c["art"])] == ["29"], "the paragraphs' numbers are no articles"


def test_the_pin_takes_max_articles_of_a_list(tmp_path, isolated_config):
    """Every article of the list is cited; the pin bounds them downstream
    (app/ask/articles.py MAX_ARTICLES), in the query's order."""
    from tests.test_eurlex import build_corpus
    c, _ = _collection(tmp_path, "eurlex", build_corpus(tmp_path / "eurlex"))
    q = "Articles 1, 2, 3, 4, 5 and 6 of Regulation (EU) 2016/1036"
    assert [a for _c, a in _pairs(q)] == ["1", "2", "3", "4", "5", "6"]
    got = articles.pin(q, [c])
    assert [f["entity_id"] for f in got] == [f"celex:{ACT}~art{n}" for n in range(1, articles.MAX_ARTICLES + 1)]
    [f, g] = articles.pin("Articles 5 and 6 of Regulation (EU) 2016/1036", [c])
    assert (f["entity_id"], g["entity_id"]) == (f"celex:{ACT}~art5", f"celex:{ACT}~art6")
    assert f["label"].endswith("— Articles 5 (text in force)")


# ---- question forms: what a reader types and the Official Journal never writes ----------------

#: (language, a question as a reader types it, the act, the article) — WRITTEN HERE, not OJ text: none of
#: these forms stands before an article's number in the five acts' text in its language (`_QUESTION_FORMS`)
TYPED = (
    ("en", "Art. 5 of Regulation (EU) 2016/679", "32016R0679", "5"),
    ("fr", "art. 17 du règlement (UE) 2016/679", "32016R0679", "17"),
    ("de", "Art. 6 Abs. 1 der Verordnung (EU) 2016/679", "32016R0679", "6"),
    ("it", "art. 6 del regolamento (UE) 2016/679", "32016R0679", "6"),
    ("es", "art. 6 del Reglamento (UE) 2016/679", "32016R0679", "6"),
    ("pt", "art. 6.º do Regulamento (UE) 2016/679", "32016R0679", "6"),
    ("nl", "art. 6 van Verordening (EU) 2016/679", "32016R0679", "6"),
    ("da", "art. 6, stk. 1, i forordning (EU) 2016/679", "32016R0679", "6"),
    ("sv", "art. 6.1 i förordning (EU) 2016/679", "32016R0679", "6"),
    ("ro", "art. 6 alin. (1) din Regulamentul (UE) 2016/679", "32016R0679", "6"),
    ("mt", "Art. 6 tar-Regolament (UE) 2016/679", "32016R0679", "6"),
    ("sk", "čl. 6 ods. 1 nariadenia (EÚ) 2016/679", "32016R0679", "6"),
    ("hr", "čl. 6. Uredbe (EU) 2016/679", "32016R0679", "6"),
    ("sl", "čl. 6 Uredbe (EU) 2016/679", "32016R0679", "6"),
    ("sl", "6. člen Uredbe (EU) 2016/679", "32016R0679", "6"),
    ("sl", "v 6. členu Uredbe (EU) 2016/679", "32016R0679", "6"),
    ("bg", "чл. 6 от Регламент (ЕС) 2016/679", "32016R0679", "6"),
    ("el", "άρθ. 6 του κανονισμού (ΕΕ) 2016/679", "32016R0679", "6"),
    ("el", "άρθρ. 6 παρ. 1 του κανονισμού (ΕΕ) 2016/679", "32016R0679", "6"),
    ("el", "αρθρ. 6 του κανονισμου (ΕΕ) 2016/679", "32016R0679", "6"),
    ("lt", "Reglamento (ES) 2016/679 6 str.", "32016R0679", "6"),
)


@pytest.mark.parametrize("lang, text, celex, article", TYPED, ids=[f"{t[0]}-{t[1][:12]}" for t in TYPED])
def test_a_question_form_is_read_and_kept_apart_from_the_evidenced_forms(lang, text, celex, article):
    assert _pairs(text)[:1] == [(celex, article)], text
    assert any(f.lang == lang for f in eref._QUESTION_FORMS), "a question form, said so"
    for f in (f for f in eref._FORMS if f.lang == lang):
        assert not (f.art and re.search(rf"(?:{f.art})\s*\d", text, re.I)), "no evidenced form reads it"
        assert not (f.after and re.search(f.after, text, re.I)), "no evidenced form reads it"


def test_every_question_form_is_tested_and_the_evidenced_ones_hold_none():
    assert {f.lang for f in eref._QUESTION_FORMS} <= {t[0] for t in TYPED}
    assert all(f.source.startswith("the OJ writes") for f in eref._QUESTION_FORMS)
    assert not any(f.R or f.L or f.D or f.conn or f.gap for f in eref._QUESTION_FORMS), \
        "a reader's word for an article, never for an act"
    # of the abbreviations, the OJ texts write only cs "čl." and pl "art." before a number
    abbreviated = {f.lang for f in eref._FORMS if any(a in f.art for a in (r"art\.", r"čl\.", r"чл\.", r"ρθ\."))}
    assert abbreviated == {"cs", "pl"}


# ---- end to end: a Hungarian and a Greek question pin the article from their Work ----------

#: the article's heading as each language's Official Journal writes it (the
#: forms app/plugins/eurlex/rendition.py reads, from 02016R1036-20180608 in each)
_HEADING = {"hu": "{n}. cikk", "el": "Άρθρο {n}"}


def _eu_languages(root, *langs):
    """ACT in several languages: the real Formex and notice re-languaged, as
    tests/ask/test_article_citations.py `_eu_three_languages` does, and each
    article's heading in the language's own form."""
    from tests.test_eurlex import _notice_for, _unit
    for lang in langs:
        l3 = src.LANGUAGES[lang].encode()
        unit = _unit(root, lang, ACT, ACT, _notice_for(ACT).replace(b">ENG<", b">" + l3 + b"<"))
        up = lang.upper().encode()
        for p in unit.glob("*.xml"):
            if p.name == "notice.xml":
                continue
            b = p.read_bytes().replace(b"<LG.DOC>EN", b"<LG.DOC>" + up).replace(b"<LG.OJ>EN", b"<LG.OJ>" + up)
            if lang in _HEADING:
                b = re.sub(rb"<TI\.ART>Article (\d+[a-z]?)</TI\.ART>",
                           lambda m: f"<TI.ART>{_HEADING[lang].format(n=m[1].decode())}</TI.ART>".encode(), b)
            p.write_bytes(b)
    return root


#: 32018R0825 Art. 4, second paragraph, verbatim: "Article 5(9) of Regulation (EU) 2016/1036"
QUESTIONS = (
    ("hu", "az eljárás megindításáról szóló, az (EU) 2016/1036 rendelet 5. cikkének (9) bekezdése", "5. cikkének"),
    ("el", "σύμφωνα με το άρθρο 5 παράγραφος 9 του κανονισμού (ΕΕ) 2016/1036", "άρθρο 5"),
)


@pytest.mark.parametrize("lang, query, label", QUESTIONS, ids=[q[0] for q in QUESTIONS])
def test_a_hungarian_and_a_greek_question_pin_the_article_of_their_language_s_work(
        tmp_path, isolated_config, lang, query, label):
    c, con = _collection(tmp_path, "eurlex", _eu_languages(tmp_path / "eurlex", "en", "hu", "el"))
    lang_of = {r["uid"]: r["lang"] for r in con.execute("SELECT uid, lang FROM documents")}
    assert sorted(lang_of.values()) == ["el", "en", "hu"], "three languages held"
    [f] = articles.pin(query, [c], query_lang=lang)
    assert lang_of[f["fields"]["versione"]] == lang, "the query's language's Work"
    assert f["entity_id"] == f"celex:{ACT}~art5"
    heading = _HEADING[lang].format(n=5)
    assert f"{heading} — Initiation of proceedings" in f["fields"]["testo"]
    assert _HEADING[lang].format(n=6) not in f["fields"]["testo"], "the article, not the next"
    assert f["label"].endswith(f"— {label} (text in force)")
    # a language not held: the source's preference, English, once
    [g] = articles.pin(query, [c], query_lang="fi")
    assert lang_of[g["fields"]["versione"]] == "en" and g["entity_id"] == f"celex:{ACT}~art5"
