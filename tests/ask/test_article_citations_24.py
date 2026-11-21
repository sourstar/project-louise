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
    for text in [r[1] for r in ROWS] + [n[2] for n in NEGATIVES] + [q for q, *_ in REAL_TEXT_CASES]:
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
    # 32016R0679 (en) recital 103: a list gives its last article, in every language alike
    ("Article 25(6) or Article 26(4) of Directive 95/46/EC", "31995L0046", "26"),
    ("čl. 25 odst. 6 nebo čl. 26 odst. 4 směrnice 95/46/ES", "31995L0046", "26"),
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
