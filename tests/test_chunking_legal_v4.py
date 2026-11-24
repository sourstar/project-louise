"""`legal_article` VERSION 4 (2026-11-21, P8 of the EUR-Lex review;
docs/notes/legal-chunker-v4.md): the sentence level of the 24 EU languages —
a sentence start in any capital of their Latin, Greek and Cyrillic blocks (a
FROZEN literal, pinned here by its sha256), no cut after their abbreviations
— and version 3 byte for byte what it was. v4 cuts as v3 wherever a text
holds none of the new capitals and none of the new abbreviations, by
construction; held here on the four legal plugins' real fixtures (it, fr,
de, en) and on texts built from them. Its abbreviations, checked against the
Interinstitutional Style Guide and real EUR-Lex text
(docs/notes/legal-chunker-v4-abbreviations.md), are held on real sentences
of the 24 languages (tests/fixtures/eurlex/sentences/)."""
from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from pathlib import Path

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from app import chunking_legal as cl
from app.plugins.wikidata import chunker as wd

SHA = "sha256:" + "ab" * 32


@pytest.fixture(scope="module")
def word():
    """One token per whitespace-separated word, as every legal test cuts."""
    tok = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    wd.register(SHA, tok)
    yield tok
    wd._tokenizers.pop(SHA, None)


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory, word):
    """Every real document of the four legal plugins' fixtures, read by its
    plugin's own reader: Normattiva (it), LEGI (fr), NeuRIS (de), EUR-Lex (en)."""
    from tests import legal_fixture_texts as lft
    return lft.every(tmp_path_factory.mktemp("legal"))


#: the windows the goldens cut at: the shared layer's (header and outline on),
#: then ever smaller ones, so that the paragraph, point, sentence and window
#: levels are all reached on real text
WINDOWS = ((8175, True), (256, True), (64, False), (40, False), (32, False))


def _digest(docs, version: int) -> tuple[int, str]:
    h = hashlib.sha256()
    n = 0
    for mt, header in WINDOWS:
        params = {"tokenizer": SHA, "max_tokens": mt, "header": header, "outline": header}
        for _lang, uid, title, sections in docs:
            for c in cl.chunk_legal_article(uid, 1, sections, version, params, title=title):
                h.update(f"{c['id']}\x1f{c['chapter']}\x1f{c['text']}\x1e".encode())
                n += 1
    return n, h.hexdigest()


# ---- versions 1-3: byte for byte what they were --------------------------------------------------

#: computed on the chunker BEFORE version 4 was written (2026-11-21, 3da4f9c7)
GOLDEN = {1: (11774, "727627012785d35dd2e5011eacca2216d347b3c691a2ac37d2abcbf02b908aa7"),
          2: (11748, "0d747a74aabdb978d85438c3695093d325a918df3a0e9e8ea95e68ae1ba208d3"),
          3: (11748, "ab941617b40d2bca729c5287bfec1a4ee986e4682c3ee4f6d4231c611bfdbe63")}


@pytest.mark.parametrize("version", [1, 2, 3])
def test_versions_1_to_3_cut_every_fixture_as_before_v4(fixtures, version):
    """Invariant 3: a layer pinned at 3 keeps cutting at 3 — every chunk's
    id, chapter and text over the four plugins' fixtures and five windows."""
    assert _digest(fixtures, version) == GOLDEN[version]


def test_the_shared_layer_is_4_since_the_space_moved():
    """Moving it moves the shared legal space (legal-chunker-v4.md): the
    operator's M1 = "A", 2026-11-23. Every version stays readable."""
    assert cl.CHUNK_VERSION == 4 and cl.SHARED_LAYER["chunker"]["version"] == 4
    assert cl.VERSIONS == (1, 2, 3, 4)


# ---- the frozen class -----------------------------------------------------------------------------

CAPITALS_SHA256 = "e8fe079e237598a7850b3ce4562eba20c9e7f4353e2f9163081aba11b5119af0"


def test_the_capitals_are_a_frozen_literal_pinned_by_its_sha256():
    """Never built from the interpreter's Unicode tables (invariant 3)."""
    assert hashlib.sha256(cl.CAPITALS_V4.encode("utf-8")).hexdigest() == CAPITALS_SHA256
    assert len(cl.CAPITALS_V4) == len(set(cl.CAPITALS_V4)) == 403


def test_the_capitals_are_capitals_of_the_named_blocks_and_hold_v3_s():
    blocks = [(0x41, 0x5A), (0xC0, 0xDE), (0x100, 0x17F), (0x180, 0x24F), (0x370, 0x3FF), (0x400, 0x4FF)]
    for c in cl.CAPITALS_V4:
        assert unicodedata.category(c) in ("Lu", "Lt"), f"{c!r} U+{ord(c):04X}"
        assert any(lo <= ord(c) <= hi for lo, hi in blocks) and not 0x3E2 <= ord(c) <= 0x3EF
    v3 = [chr(c) for c in range(0x41, 0x5B)] + [chr(c) for c in range(0xC0, 0xDF) if c != 0xD7]
    assert set(v3) <= set(cl.CAPITALS_V4), "every candidate of v3 is one of v4's"
    assert not set(v3) & set(cl.NEW_CAPITALS_V4)
    named = ("ČŠŽŘĎŤŇŁŚŹŻŐŰĀĒĪŪȘȚĊĠĦ" "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ" "ΆΈΉΊΌΎΏΪΫ"
             + "".join(chr(c) for c in range(0x410, 0x430)) + "".join(chr(c) for c in range(0x400, 0x410)))
    assert set(named) <= set(cl.NEW_CAPITALS_V4)


#: the three patterns v4 cuts by: a change to any of them is a new version
#: (the abbreviations and the before-number forms as checked against the Style
#: Guide and real text, 2026-11-21: legal-chunker-v4-abbreviations.md)
PATTERNS_SHA256 = {"end": "06a5996d0b0c7e07e87c264f693bb0871ec413c09ff0f98f1cb3edfb7751f1b3",
                   "abbreviations": "1ac075b0ede2cc1be77dcd972e50397db00361784306d03a788f6d7350b915b4",
                   "before_number": "681359da2986a57fddc46dee1c58c2d2a4a47f57cec4e6299c5fa29063561fc2",
                   "point": "02ff0919c338a8c13312f35c99c148add689b3f5a5846af683b73391d01a7a8e",
                   "ordinal": "6b50b36cdf62cfe3590cc7ec9c99feac63fd994cbc6e391a97a306d4969971a2",
                   "after_ordinal": "5de53560b3d10c4d7cdc98b98777d59f8d0b7755002a9190c6fb2dddc96cadfb"}


def test_v4_s_patterns_are_pinned():
    got = {"end": cl._SENTENCE_END_V4.pattern, "abbreviations": cl._ABBREVIATIONS_V4.pattern,
           "before_number": cl._ABBREVIATIONS_V4_NUMBER.pattern, "point": cl.POINT_V4.pattern,
           "ordinal": cl._ORDINAL_V4.pattern, "after_ordinal": cl._AFTER_ORDINAL_V4.pattern}
    assert {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in got.items()} == PATTERNS_SHA256


def test_the_abbreviations_are_the_24_languages_and_new_to_v3():
    langs = [lang for lang, _forms in cl.ABBREVIATIONS_V4]
    assert langs == sorted(langs) and len(langs) == 24 and set(langs) == set(
        "bg cs da de el en es et fi fr ga hr hu it lt lv mt nl pl pt ro sk sl sv".split())
    for f in (*cl.ABBREVIATION_FORMS_V4, *cl.ABBREVIATIONS_V4_BEFORE_NUMBER):
        assert not cl._ABBREVIATIONS.search(f + "."), f"{f!r}: v3 knows it already"
        assert len(f) + 2 < cl._V4_LOOKBACK
    assert len("Buchst.") + 1 < cl._V4_LOOKBACK, "v3's longest form within the look back too"
    assert not set(cl.ABBREVIATION_FORMS_V4) & set(cl.ABBREVIATIONS_V4_BEFORE_NUMBER)


def test_no_form_that_ends_a_sentence_elsewhere_is_refused_before_a_capital():
    """The union of the 24 lists applies to every language: a form that is a
    word or a unit in one is refused before a number at most. "i" (German
    "i. V. m.", in no EU German text) is a Danish and Swedish preposition that
    ends sentences, "kap" a Hungarian verb, "vol" the "% vol." of alcoholic
    strength, "cap" an English word, "sal" Spanish salt, "por" Hungarian
    powder; the Bulgarian year "г." is also the gram, the Lithuanian year "m."
    the metre (legal-chunker-v4-abbreviations.md)."""
    words = {"i", "kap", "Kap", "vol", "cap", "sal", "por", "г", "m"}
    assert not words & set(cl.ABBREVIATION_FORMS_V4)
    assert {"kap", "Kap", "vol", "cap"} <= set(cl.ABBREVIATIONS_V4_BEFORE_NUMBER)
    (p,) = [x for x in _paragraphs("32019L0790da.html") if "som de indgår i. Udgivere" in x]
    cut = cl._sentences(p, 4)
    assert cut[0].endswith("den pressepublikation, som de indgår i.")
    assert cut[1].startswith("Udgivere af pressepublikationer bør derfor")
    # written for the test: each such form ends a sentence before a capital, a
    # number follows it in a reference
    assert cl._sentences("A sorozat is kap. A monográfiák is.", 4) == ["A sorozat is kap.", "A monográfiák is."]
    assert cl._sentences("The strength is 12 % vol. The wine is red.", 4) == \
        ["The strength is 12 % vol.", "The wine is red."]
    assert cl._sentences("The fee cap. The rate.", 4) == ["The fee cap.", "The rate."]
    assert cl._sentences("Se kap. 5 og vol. 34, cap. 6.", 4) == ["Se kap. 5 og vol. 34, cap. 6."]


# ---- identity with v3 where nothing new stands -------------------------------------------------------

_NEW_FORMS = re.compile("(?<![^\\s(\\[«\"„“‘'/])(?:" + "|".join(
    re.escape(f).replace("\\ ", "\\s") for f in sorted({*cl.ABBREVIATION_FORMS_V4, *cl.ABBREVIATIONS_V4_BEFORE_NUMBER},
                                                       key=lambda f: (-len(f), f))) + ")\\.")


#: a Greek or Cyrillic small letter anywhere: what a point mark of v4's alone needs
_GREEK_CYRILLIC_SMALL = re.compile("[α-ωа-я]")
#: an ordinal its next word continues (`AFTER_ORDINAL_V4`): "vom 18. Dezember"
_NEW_ORDINAL = re.compile("(?<![0-9.,])[0-9]{1,4}\\.\\s+(?:" + "|".join(
    re.escape(f) for f in sorted(cl.AFTER_ORDINAL_V4, key=lambda f: (-len(f), f))) + ")")


def _holds_something_new(text: str) -> bool:
    """A new capital anywhere, a new abbreviation as a token, or a Greek or
    Cyrillic small letter (a point mark of v4's): read off the text alone,
    nothing of the cut's machinery."""
    return (any(c in cl.NEW_CAPITALS_V4 for c in text) or _NEW_FORMS.search(text) is not None
            or _GREEK_CYRILLIC_SMALL.search(text) is not None or _NEW_ORDINAL.search(text) is not None)


def test_v4_s_sentences_are_v3_s_in_every_fixture_text_that_holds_nothing_new(fixtures):
    bodies = [b for _l, _u, _t, sections in fixtures for _label, b in sections if b.strip()]
    plain = [b for b in bodies if not _holds_something_new(b)]
    assert len(plain) > 0.8 * len(bodies), "the fixtures are mostly plain it/fr/de/en"
    assert sum(len(cl._sentences(b, 3)) for b in plain) > 2000, "and full of sentences"
    for b in plain:
        assert cl._sentences(b, 4) == cl._sentences(b, 3)


def _sentence_pool(fixtures) -> list[str]:
    out = []
    for _l, _u, _t, sections in fixtures:
        for _label, b in sections:
            out += cl._sentences(b, 3)
    return out


def test_v4_cuts_as_v3_on_texts_made_of_fixture_sentences_without_anything_new(fixtures, word):
    """By construction, at every level: random texts of real it/fr/de/en
    sentences, paragraph marks and line breaks, split by v3 and v4."""
    pool = [s for s in _sentence_pool(fixtures) if not _holds_something_new(s)]
    rng = random.Random(20261121)
    seps = [" ", " ", " ", "\n", "\n1. ", "\n(2) ", "\na) ", ";\n"]
    for _ in range(300):
        text = "".join(rng.choice(pool) + rng.choice(seps) for _ in range(rng.randint(2, 60)))
        if _holds_something_new(text):
            continue
        assert cl._sentences(text, 4) == cl._sentences(text, 3)
        for budget in (32, 64, 200):
            assert cl.split(word, text, budget, version=4) == cl.split(word, text, budget, version=3)


def test_v4_s_reading_of_v3_s_abbreviations_is_v3_s(fixtures):
    """`after_abbreviation_v4` reads `_ABBREVIATIONS` with pos/endpos, a
    bounded look back: the same decision as v3's slice, candidate by candidate."""
    rng = random.Random(4)
    pool = _sentence_pool(fixtures)
    tails = ["art.", "Abs.", "n.", "Nr.", "z.B.", "Buchst.", "Part.", "A.", "xA.", "c.p.c.", "(art.", "-Art.",
             "pp.", "vs.", "etc.", "Hrsg.", "L.", "fine."]
    for _ in range(400):
        text = " ".join(rng.choice(pool + tails) for _ in range(rng.randint(2, 40)))
        start = 0
        for m in cl._SENTENCE_END.finditer(text):
            v3 = bool(cl._ABBREVIATIONS.search(text[start:m.start()]))
            lo = max(start, m.start() - cl._V4_LOOKBACK)
            assert bool(cl._ABBREVIATIONS.search(text, lo, m.start())) == v3
            if not v3:
                start = m.end()


def test_the_it_fr_de_en_fixtures_give_v3_s_chunk_texts_at_the_shared_window(fixtures):
    """The shared layer and a 256-token one: every fixture document's chunk
    texts are v3's, only the ids' version moves."""
    for mt, header in WINDOWS[:2]:
        params = {"tokenizer": SHA, "max_tokens": mt, "header": header, "outline": header}
        for _lang, uid, title, sections in fixtures:
            v3 = list(cl.chunk_legal_article(uid, 1, sections, 3, params, title=title))
            v4 = list(cl.chunk_legal_article(uid, 1, sections, 4, params, title=title))
            assert [(c["chapter"], c["text"]) for c in v4] == [(c["chapter"], c["text"]) for c in v3]
            assert [c["id"] for c in v4] == [c["id"][:-1] + "4" for c in v3]


def test_at_a_forced_window_only_a_new_abbreviation_moves_a_fixture_s_cut(fixtures):
    """At 64, 40 and 32 tokens nearly every paragraph is cut at its
    sentences. One document's cut moves, and at a new abbreviation: legge
    306/2003's note "Il D.Lgs. 2 gennaio 2018, n. 1 ha disposto …", which v3
    cut after "D.Lgs." (and NeuRIS's "BGBl. I 2020", which v3 cut after
    "BGBl.", moves a sentence and no chunk)."""
    for mt, header in WINDOWS[2:]:
        params = {"tokenizer": SHA, "max_tokens": mt, "header": header, "outline": header}
        moved = []
        for _lang, uid, title, sections in fixtures:
            v3 = [c["text"] for c in cl.chunk_legal_article(uid, 1, sections, 3, params, title=title)]
            v4 = [c["text"] for c in cl.chunk_legal_article(uid, 1, sections, 4, params, title=title)]
            if v3 != v4:
                moved.append(uid)
                assert any(_holds_something_new(b) for _l, b in sections)
        assert moved == ["normattiva:legge-2003-306-vigente"], mt
    note = next(b for _l, u, _t, s in fixtures if u == "normattiva:legge-2003-306-vigente"
                for _x, b in s if "Il D.Lgs. 2 gennaio 2018" in b)
    assert any(s.endswith("Il D.Lgs.") for s in cl._sentences(note, 3)), "v3: cut after the abbreviation"
    assert not any(s.endswith("D.Lgs.") for s in cl._sentences(note, 4))


# ---- the 24 languages' sentences: REAL text. Each a paragraph of a Cellar item trimmed to
# whole lines (tests/fixtures/eurlex/sentences/, its README: every kept byte as served), as
# the EUR-Lex plugin's rendition reader reads it — the text a record's section holds ---------

SENTENCES = Path(__file__).parent / "fixtures" / "eurlex" / "sentences"


def _paragraphs(name: str) -> list[str]:
    """The fixture's lines of text as `rendition.parse_html` reads them."""
    from types import SimpleNamespace

    from app.plugins.eurlex import rendition
    note = SimpleNamespace(expression_title=None, date_document=None, date_publication=None)
    act = rendition.parse_html({name: (SENTENCES / name).read_bytes()}, [name], lang="", root="", note=note)
    # the sections and the notes: the reader files footnotes in the notes
    # (C11, 2026-11-21), which the text index cuts as the sections are cut
    return ([line for _label, body in act.rendered_sections for line in body.split("\n")]
            + [line for n in act.notes for line in str(n.get("text") or "").split("\n")])


#: per language: (fixture, does v3 cut it otherwise, the paragraph's sentences as v4
#: cuts them) — the GDPR's (32016R0679) recital 1 or 17, a sentence end before a
#: capital v3 saw or not; its footnote 6, Regulation No 45/2001 and its OJ page, one
#: sentence however many full stops; and where a new form stands elsewhere, that
#: paragraph: Danish "Stk.", German "z. B." and "u. a." (32016R1036), Hungarian "sz."
#: (02016R1036-20180608), Polish "r.", Lithuanian "d."
GOLDEN_V4 = {
    "bg": [
        ("32016R0679bg.html", True, [
            "Защитата на физическите лица във връзка с обработването на лични данни е основно право.",
            "Член 8, параграф 1 от Хартата на основните права на Европейския съюз („Хартата“) и член 16, параграф 1 от Договора за функционирането на Европейския съюз (ДФЕС) предвиждат, че всеки има право на защита на личните му данни.",
        ]),
        ("32016R0679bg.html", True, [
            "(6) Регламент (ЕО) № 45/2001 на Европейския парламент и на Съвета от 18 декември 2000 г. относно защитата на лицата по отношение на обработката на лични данни от институции и органи на Общността и за свободното движение на такива данни (ОВ L 8, 12.1.2001 г., стр. 1).",
        ]),
    ],
    "cs": [
        ("32016R0679cs.html", True, [
            "Ochrana fyzických osob v souvislosti se zpracováním osobních údajů je základním právem.",
            "Ustanovení čl. 8 odst. 1 Listiny základních práv Evropské unie (dále jen „Listina“) a čl. 16 odst. 1 Smlouvy o fungování Evropské unie (dále jen „Smlouva o fungování EU“) přiznávají každému právo na ochranu osobních údajů, které se jej týkají.",
        ]),
        ("32016R0679cs.html", True, [
            "(6) Nařízení Evropského parlamentu a Rady (ES) č. 45/2001 ze dne 18. prosince 2000 o ochraně fyzických osob v souvislosti se zpracováním osobních údajů orgány a institucemi Společenství a o volném pohybu těchto údajů (Úř. věst. L 8, 12.1.2001, s. 1).",
        ]),
    ],
    "da": [
        ("32016R0679da.html", True, [
            "Beskyttelse af fysiske personer i forbindelse med behandling af personoplysninger er en grundlæggende rettighed.",
            "I artikel 8, stk. 1, i Den Europæiske Unions charter om grundlæggende rettigheder (»chartret«) og i artikel 16, stk. 1, i traktaten om Den Europæiske Unions funktionsmåde (TEUF) fastsættes det, at enhver har ret til beskyttelse af personoplysninger, der vedrører den pågældende.",
        ]),
        ("32016R0679da.html", True, [
            "3.",
            "Stk. 1 berører ikke medlemsstaternes generelle aftaleret, som f.eks. bestemmelser om gyldighed, indgåelse eller virkning af en kontrakt, når der er tale om et barn.",
        ]),
        ("32016R0679da.html", True, [
            "(6) Europa-Parlamentets og Rådets forordning (EF) nr. 45/2001 af 18. december 2000 om beskyttelse af fysiske personer i forbindelse med behandling af personoplysninger i fællesskabsinstitutionerne og -organerne og om fri udveksling af sådanne oplysninger (EFT L 8 af 12.1.2001, s. 1).",
        ]),
    ],
    "de": [
        # "vom 18. Dezember 2000": a date's day (v3: cut before the month)
        ("32016R0679de.html", True, [
            "(6) Verordnung (EG) Nr. 45/2001 des Europäischen Parlaments und des Rates vom 18. Dezember 2000 zum Schutz natürlicher Personen bei der Verarbeitung personenbezogener Daten durch die Organe und Einrichtungen der Gemeinschaft und zum freien Datenverkehr (ABl. L 8 vom 12.1.2001, S. 1).",
        ]),
        ("32016R0679de.html", False, [
            "Der Schutz natürlicher Personen bei der Verarbeitung personenbezogener Daten ist ein Grundrecht.",
            "Gemäß Artikel 8 Absatz 1 der Charta der Grundrechte der Europäischen Union (im Folgenden „Charta“) sowie Artikel 16 Absatz 1 des Vertrags über die Arbeitsweise der Europäischen Union (AEUV) hat jede Person das Recht auf Schutz der sie betreffenden personenbezogenen Daten.",
        ]),
        ("32016R0679de.html", True, [
            "Ist nach Völkerrecht das Recht eines Mitgliedstaats anwendbar, z. B. in einer diplomatischen oder konsularischen Vertretung eines Mitgliedstaats, so sollte die Verordnung auch auf einen nicht in der Union niedergelassenen Verantwortlichen Anwendung finden.",
        ]),
        ("32016R1036de.html", True, [
            "Es sollten die Bedingungen festgesetzt werden, unter denen vorläufige Zölle eingeführt werden können, u. a. Bedingungen wonach vorläufige Zölle frühestens 60 Tage und spätestens neun Monate nach der Einleitung des Verfahrens eingeführt werden können.",
            "Aus Verwaltungsgründen sollte ferner vorgesehen werden, dass diese Zölle in allen Fällen von der Kommission entweder direkt für einen Zeitraum von neun Monaten oder in zwei Phasen von sechs und drei Monaten eingeführt werden können.",
        ]),
    ],
    "el": [
        ("32016R0679el.html", True, [
            "Ο κανονισμός (EΚ) αριθ. 45/2001 του Ευρωπαϊκού Κοινοβουλίου και του Συμβουλίου (6) εφαρμόζεται στην επεξεργασία δεδομένων προσωπικού χαρακτήρα από τα θεσμικά και λοιπά όργανα, οργανισμούς και υπηρεσίες της Ένωσης.",
            "Ο κανονισμός (EΚ) αριθ. 45/2001 και άλλες νομικές πράξεις της Ένωσης οι οποίες είναι εφαρμοστέες σε μια τέτοια επεξεργασία δεδομένων προσωπικού χαρακτήρα θα πρέπει να προσαρμοστούν στις αρχές και τους κανόνες που θεσπίζονται στον παρόντα κανονισμό και να εφαρμόζονται υπό το πρίσμα του παρόντος κανονισμού.",
            "Για να εξασφαλιστεί ένα ισχυρό και συνεκτικό πλαίσιο προστασίας των δεδομένων στην Ένωση, μετά την έκδοσή του παρόντος κανονισμού θα πρέπει να ακολουθήσουν οι αναγκαίες προσαρμογές του κανονισμού (ΕΚ) αριθ. 45/2001, ώστε να καταστεί δυνατή η εφαρμογή ταυτόχρονα με τον παρόντα κανονισμό.",
        ]),
        ("32016R0679el.html", True, [
            "(6) Κανονισμός (ΕΚ) αριθ. 45/2001 του Ευρωπαϊκού Κοινοβουλίου και του Συμβουλίου, της 18ης Δεκεμβρίου 2000, σχετικά με την προστασία των φυσικών προσώπων έναντι της επεξεργασίας δεδομένων προσωπικού χαρακτήρα από τα όργανα και τους οργανισμούς της Κοινότητας και σχετικά με την ελεύθερη κυκλοφορία των δεδομένων αυτών (ΕΕ L 8 της 12.1.2001, σ. 1).",
        ]),
    ],
    "en": [
        ("32016R0679en.html", False, [
            "The protection of natural persons in relation to the processing of personal data is a fundamental right.",
            "Article 8(1) of the Charter of Fundamental Rights of the European Union (the ‘Charter’) and Article 16(1) of the Treaty on the Functioning of the European Union (TFEU) provide that everyone has the right to the protection of personal data concerning him or her.",
        ]),
        ("32016R0679en.html", False, [
            "(6) Regulation (EC) No 45/2001 of the European Parliament and of the Council of 18 December 2000 on the protection of individuals with regard to the processing of personal data by the Community institutions and bodies and on the free movement of such data (OJ L 8, 12.1.2001, p. 1).",
        ]),
    ],
    "es": [
        ("32016R0679es.html", False, [
            "La protección de las personas físicas en relación con el tratamiento de datos personales es un derecho fundamental.",
            "El artículo 8, apartado 1, de la Carta de los Derechos Fundamentales de la Unión Europea («la Carta») y el artículo 16, apartado 1, del Tratado de Funcionamiento de la Unión Europea (TFUE) establecen que toda persona tiene derecho a la protección de los datos de carácter personal que le conciernan.",
        ]),
        ("32016R0679es.html", False, [
            "(6) Reglamento (CE) n.o 45/2001 del Parlamento Europeo y del Consejo, de 18 de diciembre de 2000, relativo a la protección de las personas físicas en lo que respecta al tratamiento de datos personales por las instituciones y los organismos comunitarios y a la libre circulación de estos datos (DO L 8 de 12.1.2001, p. 1).",
        ]),
    ],
    "et": [
        ("32016R0679et.html", False, [
            "Füüsiliste isikute kaitse isikuandmete töötlemisel on põhiõigus.",
            "Euroopa Liidu põhiõiguste harta („harta“) artikli 8 lõikes 1 ja Euroopa Liidu toimimise lepingu („ELi toimimise leping“) artikli 16 lõikes 1 on sätestatud, et igaühel on õigus oma isikuandmete kaitsele.",
        ]),
        ("32016R0679et.html", False, [
            "(6) Euroopa Parlamendi ja nõukogu 18. detsembri 2000. aasta määrus (EÜ) nr 45/2001 füüsiliste isikute kaitse kohta isikuandmete töötlemisel ühenduse institutsioonides ja asutustes ning selliste andmete vaba liikumise kohta (EÜT L 8, 12.1.2001, lk 1).",
        ]),
    ],
    "fi": [
        ("32016R0679fi.html", False, [
            "Luonnollisten henkilöiden suojelu henkilötietojen käsittelyn yhteydessä on perusoikeus.",
            "Euroopan unionin perusoikeuskirjan, jäljempänä ’perusoikeuskirja’, 8 artiklan 1 kohdan ja Euroopan unionin toiminnasta tehdyn sopimuksen (SEUT) 16 artiklan 1 kohdan mukaan jokaisella on oikeus henkilötietojensa suojaan.",
        ]),
        ("32016R0679fi.html", True, [
            "(6) Euroopan parlamentin ja neuvoston asetus (EY) N:o 45/2001, annettu 18 päivänä joulukuuta 2000, yksilöiden suojelusta yhteisöjen toimielinten ja elinten suorittamassa henkilötietojen käsittelyssä ja näiden tietojen vapaasta liikkuvuudesta (EYVL L 8, 12.1.2001, s. 1).",
        ]),
    ],
    "fr": [
        ("32016R0679fr.html", False, [
            "La protection des personnes physiques à l'égard du traitement des données à caractère personnel est un droit fondamental.",
            "L'article 8, paragraphe 1, de la Charte des droits fondamentaux de l'Union européenne (ci-après dénommée «Charte») et l'article 16, paragraphe 1, du traité sur le fonctionnement de l'Union européenne disposent que toute personne a droit à la protection des données à caractère personnel la concernant.",
        ]),
        ("32016R0679fr.html", False, [
            "(6) Règlement (CE) no 45/2001 du Parlement européen et du Conseil du 18 décembre 2000 relatif à la protection des personnes physiques à l'égard du traitement des données à caractère personnel par les institutions et organes communautaires et à la libre circulation de ces données (JO L 8 du 12.1.2001, p. 1).",
        ]),
    ],
    "ga": [
        ("32016R0679ga.html", True, [
            "Tá feidhm ag Rialachán (CE) Uimh 45/2001 ó Pharlaimint na hEorpa agus ón gComhairle (6) maidir le sonraí pearsanta a phróiseáil ag institiúidí, comhlachtaí, oifigí agus gníomhaireachtaí an Aontais.",
            "Ba cheart Rialachán (CE) Uimh. 45/2001 agus na gníomhartha dlí eile de chuid an Aontais is infheidhme maidir leis an bpróiseáil sin ar shonraí pearsanta a chur in oiriúint do na prionsabail agus do na rialacha a bhunaítear sa Rialachán seo agus a chur i bhfeidhm i bhfianaise an Rialacháin seo.",
            "D'fhonn creat láidir, comhtháite cosanta sonraí a chur ar fáil san Aontas, ba cheart na hoiriúnuithe riachtanacha ar Rialachán (CE) Uimh. 45/2001 a dhéanamh tar éis an Rialachán seo a ghlacadh, d'fhonn go bhféadfar iad a chur i bhfeidhm i gcomhthráth leis an Rialachán seo.",
        ]),
        ("32016R0679ga.html", True, [
            "(6) Rialachán (CE) Uimh.45/2001 ó Pharlaimint na hEorpa agus ón gComhairle an 18 Nollaig 2000 maidir le daoine aonair a chosaint i ndáil le sonraí pearsanta a phróiseáil ag institiúidí agus ag comhlachtaí an Chomhphobail agus maidir le saorghluaiseacht sonraí den sórt sin (IO L 8, 12.1.2001, lch. 1).",
        ]),
    ],
    "hr": [
        # "Člankom 8. stavkom 1. Povelje": an ordinal its act continues (v3: four pieces)
        ("32016R0679hr.html", True, [
            "Zaštita pojedinaca s obzirom na obradu osobnih podataka temeljno je pravo.",
            "Člankom 8. stavkom 1. Povelje Europske unije o temeljnim pravima („Povelja”) te člankom 16. stavkom 1. Ugovora o funkcioniranju Europske unije (UFEU) utvrđuje se da svatko ima pravo na zaštitu svojih osobnih podataka.",
        ]),
        ("32016R0679hr.html", True, [
            "Uredba (EZ) br. 45/2001 Europskog parlamenta i Vijeća (6) primjenjuje se na obradu osobnih podataka koju obavljaju institucije, tijela, uredi i agencije Unije.",
            "Uredba (EZ) br. 45/2001 i drugi pravni akti Unije primjenjivi na takvu obradu osobnih podataka trebali bi se prilagoditi načelima i pravilima iz ove Uredbe i primjenjivati s obzirom na ovu Uredbu.",
            "Kako bi se osigurao čvrst i usklađen okvir za zaštitu podataka u Uniji, nakon donošenja ove Uredbe trebale bi uslijediti potrebne prilagodbe Uredbe (EZ) br. 45/2001 kako bi se omogućila istovremena primjena obiju uredaba.",
        ]),
        ("32016R0679hr.html", True, [
            "(6) Uredba (EZ) br. 45/2001 Europskog parlamenta i Vijeća od 18. prosinca 2000. o zaštiti pojedinaca u vezi s obradom osobnih podataka u institucijama i tijelima Zajednice i o slobodnom kretanju takvih podataka (SL L 8, 12.1.2001., str. 1.).",
        ]),
    ],
    "hu": [
        ("32016R0679hu.html", False, [
            "A természetes személyek személyes adataik kezelésével összefüggő védelme alapvető jog.",
            "Az Európai Unió Alapjogi Chartája (Charta) 8. cikkének (1) bekezdése és az Európai Unió működéséről szóló szerződés (EUMSZ) 16. cikkének (1) bekezdése rögzíti, hogy mindenkinek joga van a rá vonatkozó személyes adatok védelméhez.",
        ]),
        ("32016R0679hu.html", False, [
            "(6) Az Európai Parlament és a Tanács 45/2001/EK rendelete (2000. december 18.) a személyes adatok közösségi intézmények és szervek által történő feldolgozása tekintetében az egyének védelméről, valamint az ilyen adatok szabad áramlásáról (HL L 8., 2001.1.12., 1. o.).",
        ]),
        ("02016R1036-20180608hu.html", True, [
            "1.",
            "Egyezmény a kényszer- vagy kötelező munkáról, 29. sz. (1930)",
        ]),
    ],
    "it": [
        ("32016R0679it.html", False, [
            "La protezione delle persone fisiche con riguardo al trattamento dei dati di carattere personale è un diritto fondamentale.",
            "L'articolo 8, paragrafo 1, della Carta dei diritti fondamentali dell'Unione europea («Carta») e l'articolo 16, paragrafo 1, del trattato sul funzionamento dell'Unione europea («TFUE») stabiliscono che ogni persona ha diritto alla protezione dei dati di carattere personale che la riguardano.",
        ]),
        ("32016R0679it.html", True, [
            "(6) Regolamento (CE) n. 45/2001 del Parlamento europeo e del Consiglio, del 18 dicembre 2000, concernente la tutela delle persone fisiche in relazione al trattamento dei dati personali da parte delle istituzioni e degli organismi comunitari, nonché la libera circolazione di tali dati (GU L 8 del 12.1.2001, pag. 1).",
        ]),
    ],
    "lt": [
        ("32016R0679lt.html", False, [
            "fizinių asmenų apsauga tvarkant asmens duomenis yra pagrindinė teisė.",
            "Europos Sąjungos pagrindinių teisių chartijos (toliau – Chartija) 8 straipsnio 1 dalyje ir Sutarties dėl Europos Sąjungos veikimo (toliau – SESV) 16 straipsnio 1 dalyje numatyta, kad kiekvienas asmuo turi teisę į savo asmens duomenų apsaugą;",
        ]),
        ("32016R0679lt.html", True, [
            "(6) 2000 m. gruodžio 18 d. Europos Parlamento ir Tarybos reglamentas (EB) Nr. 45/2001 dėl asmenų apsaugos Bendrijos institucijoms ir įstaigoms tvarkant asmens duomenis ir laisvo tokių duomenų judėjimo (OL L 8, 2001 1 12, p. 1).",
        ]),
    ],
    "lv": [
        ("32016R0679lv.html", False, [
            "Fizisku personu aizsardzība attiecībā uz personas datu aizsardzību ir pamattiesības.",
            "Eiropas Savienības Pamattiesību hartas (“harta”) 8. panta 1. punkts un Līguma par Eiropas Savienības darbību (LESD) 16. panta 1. punkts paredz, ka ikvienai personai ir tiesības uz savu personas datu aizsardzību.",
        ]),
        ("32016R0679lv.html", False, [
            "(6) Eiropas Parlamenta un Padomes Regula (EK) Nr. 45/2001 (2000. gada 18. decembris) par fizisku personu aizsardzību attiecībā uz personas datu apstrādi Kopienas iestādēs un struktūrās un par šādu datu brīvu apriti (OV L 8, 12.1.2001., 1. lpp.).",
        ]),
    ],
    "mt": [
        ("32016R0679mt.html", False, [
            "Il-protezzjoni tal-persuni fiżiċi fir-rigward tal-ipproċessar ta' data personali hija dritt fundamentali.",
            "L-Artikolu 8(1) tal-Karta tad-Drittijiet Fundamentali tal-Unjoni Ewropea (il-“Karta”) u l-Artikolu 16(1) tat-Trattat dwar il-Funzjonament tal-Unjoni Ewropea (TFUE) jipprevedu li kull persuna għandha dritt għall-protezzjoni tad-data personali li tirrigwardaha.",
        ]),
        ("32016R0679mt.html", False, [
            "(6) Ir-Regolament (KE) Nru 45/2001 tal-Parlament Ewropew u tal-Kunsill tat-18 ta' Diċembru 2000 dwar il-protezzjoni tal-individwu fir-rigward tal-ipproċessar ta' data personali mill-istituzzjonijiet u l-korpi tal-Komunità u dwar il-moviment liberu ta' dik id-data (ĠU L 8, 12.1.2001, p. 1).",
        ]),
    ],
    "nl": [
        ("32016R0679nl.html", True, [
            "Verordening (EG) nr. 45/2001 van het Europees Parlement en de Raad (6) is van toepassing op de verwerking van persoonsgegevens door de instellingen, organen en instanties van de Unie.",
            "Verordening (EG) nr. 45/2001 en andere rechtshandelingen van de Unie die van toepassing zijn op een dergelijke verwerking van persoonsgegevens, moeten aan de beginselen en regels van de onderhavige verordening worden aangepast en in het licht van de onderhavige verordening worden toegepast.",
            "Om de Unie een sterk en coherent kader inzake gegevensbescherming ter beschikking te stellen, moet Verordening (EG) nr. 45/2001 waar nodig worden aangepast zodra de onderhavige verordening is vastgesteld, opdat deze op hetzelfde tijdstip als de onderhavige verordening van toepassing kan worden.",
        ]),
        ("32016R0679nl.html", True, [
            "(6) Verordening (EG) nr. 45/2001 van het Europees Parlement en de Raad van 18 december 2000 betreffende de bescherming van natuurlijke personen in verband met de verwerking van persoonsgegevens door de communautaire instellingen en organen en betreffende het vrije verkeer van die gegevens (PB L 8 van 12.1.2001, blz. 1).",
        ]),
    ],
    "pl": [
        ("32016R0679pl.html", True, [
            "Ochrona osób fizycznych w związku z przetwarzaniem danych osobowych jest jednym z praw podstawowych.",
            "Art. 8 ust. 1 Karty praw podstawowych Unii Europejskiej (zwanej dalej „Kartą praw podstawowych”) oraz art. 16 ust. 1 Traktatu o funkcjonowaniu Unii Europejskiej (TFUE) stanowią, że każda osoba ma prawo do ochrony danych osobowych jej dotyczących.",
        ]),
        ("32016R0679pl.html", True, [
            "(6) Rozporządzenie (WE) nr 45/2001 Parlamentu Europejskiego i Rady z dnia 18 grudnia 2000 r. o ochronie osób fizycznych w związku z przetwarzaniem danych osobowych przez instytucje i organy wspólnotowe i o swobodnym przepływie takich danych (Dz.U. L 8 z 12.1.2001, s. 1).",
        ]),
        ("32016R0679pl.html", True, [
            "Zgodnie z art. 28 ust. 2 rozporządzenia (WE) nr 45/2001 przeprowadzono konsultacje z Europejskim Inspektorem Ochrony Danych, który wydał opinię w dniu 7 marca 2012 r. (17).",
        ]),
    ],
    "pt": [
        ("32016R0679pt.html", False, [
            "A proteção das pessoas singulares relativamente ao tratamento de dados pessoais é um direito fundamental.",
            "O artigo 8.o, n.o 1, da Carta dos Direitos Fundamentais da União Europeia («Carta») e o artigo 16.o, n.o 1, do Tratado sobre o Funcionamento da União Europeia (TFUE) estabelecem que todas as pessoas têm direito à proteção dos dados de caráter pessoal que lhes digam respeito.",
        ]),
        ("32016R0679pt.html", False, [
            "(6) Regulamento (CE) n.o 45/2001 do Parlamento Europeu e do Conselho, de 18 de dezembro de 2000, relativo à proteção das pessoas singulares no que diz respeito ao tratamento de dados pessoais pelas instituições e pelos órgãos comunitários e à livre circulação desses dados (JO L 8 de 12.1.2001, p. 1).",
        ]),
    ],
    "ro": [
        ("32016R0679ro.html", True, [
            "Regulamentul (CE) nr. 45/2001 al Parlamentului European și al Consiliului (6) se aplică prelucrării de date cu caracter personal de către instituțiile, organele, oficiile și agențiile Uniunii.",
            "Regulamentul (CE) nr. 45/2001 și alte acte juridice ale Uniunii aplicabile unei asemenea prelucrări a datelor cu caracter personal ar trebui adaptate la principiile și normele stabilite în prezentul regulament și aplicate în conformitate cu prezentul regulament.",
            "În vederea asigurării unui cadru solid și coerent în materie de protecție a datelor în Uniune, ar trebui ca după adoptarea prezentului regulament să se aducă Regulamentului (CE) nr. 45/2001 adaptările necesare, astfel încât acestea să poată fi aplicate odată cu prezentul regulament.",
        ]),
        ("32016R0679ro.html", True, [
            "(6) Regulamentul (CE) nr. 45/2001 al Parlamentului European și al Consiliului din 18 decembrie 2000 privind protecția persoanelor fizice cu privire la prelucrarea datelor cu caracter personal de către instituțiile și organele comunitare și privind libera circulație a acestor date (JO L 8, 12.1.2001, p. 1).",
        ]),
    ],
    "sk": [
        ("32016R0679sk.html", True, [
            "Ochrana fyzických osôb v súvislosti so spracúvaním osobných údajov patrí medzi základné práva.",
            "V článku 8 ods. 1 Charty základných práv Európskej únie (ďalej len „Charta“) a v článku 16 ods. 1 Zmluvy o fungovaní Európskej únie (ZFEÚ) sa stanovuje, že každý má právo na ochranu osobných údajov, ktoré sa ho týkajú.",
        ]),
        ("32016R0679sk.html", True, [
            "(6) Nariadenie Európskeho parlamentu a Rady (ES) č. 45/2001 z 18. decembra 2000 o ochrane jednotlivcov so zreteľom na spracovanie osobných údajov inštitúciami a orgánmi spoločenstva a o voľnom pohybe takýchto údajov (Ú. v. ES L 8, 12.1.2001, s. 1).",
        ]),
    ],
    "sl": [
        ("32016R0679sl.html", True, [
            "Uredba (ES) št. 45/2001 Evropskega parlamenta in Sveta (6) se uporablja za obdelavo osebnih podatkov s strani institucij, organov, uradov in agencij Unije.",
            "Uredbo (ES) št. 45/2001 in druge pravne akte Unije, ki se uporabljajo za tako obdelavo osebnih podatkov, bi bilo treba prilagoditi načelom in pravilom, določenim v tej uredbi, in jih uporabljati ob upoštevanju te uredbe.",
            "Za zagotovitev trdnega in usklajenega okvira varstva podatkov v Uniji bi bilo treba po sprejetju te uredbe prilagoditi tudi Uredbo (ES) št. 45/2001, da bi se lahko začela uporabljati sočasno s to uredbo.",
        ]),
        ("32016R0679sl.html", True, [
            "(6) Uredba (ES) št. 45/2001 Evropskega parlamenta in Sveta z dne 18. decembra 2000 o varstvu posameznikov pri obdelavi osebnih podatkov v institucijah in organih Skupnosti in o prostem pretoku takih podatkov (UL L 8, 12.1.2001, str. 1).",
        ]),
    ],
    "sv": [
        ("32016R0679sv.html", False, [
            "Skyddet för fysiska personer vid behandling av personuppgifter är en grundläggande rättighet.",
            "Artikel 8.1 i Europeiska unionens stadga om de grundläggande rättigheterna (nedan kallad stadgan) och artikel 16.1 i fördraget om Europeiska unionens funktionssätt (EUF-fördraget) föreskriver att var och en har rätt till skydd av de personuppgifter som rör honom eller henne.",
        ]),
        ("32016R0679sv.html", True, [
            "(6) Europaparlamentets och rådets förordning (EG) nr 45/2001 av den 18 december 2000 om skydd för enskilda då gemenskapsinstitutionerna och gemenskapsorganen behandlar personuppgifter och om den fria rörligheten för sådana uppgifter (EGT L 8, 12.1.2001, s. 1).",
        ]),
    ],
}


def test_every_language_has_real_goldens():
    assert sorted(GOLDEN_V4) == sorted(lang for lang, _forms in cl.ABBREVIATIONS_V4)
    for lang, golden in GOLDEN_V4.items():
        assert any(len(want) > 1 for _n, _m, want in golden), f"{lang}: a sentence end"
        assert any(len(want) == 1 or moved for _n, moved, want in golden), f"{lang}: a refused cut"


@pytest.mark.parametrize("lang", sorted(GOLDEN_V4))
def test_v4_cuts_real_text_at_its_sentence_ends_and_never_after_an_abbreviation(lang, word):
    for name, moved, want in GOLDEN_V4[lang]:
        text = " ".join(want)
        assert text in _paragraphs(name), f"{name}: a paragraph of the fixture, verbatim"
        assert cl._sentences(text, 4) == want
        assert (cl._sentences(text, 3) != want) == moved
    # through the chunker's own split, at a window of the longest sentence: every
    # piece whole sentences, the text whole
    want = max((w for _n, _m, w in GOLDEN_V4[lang]), key=len)
    text = " ".join(want)
    pieces = cl.split(word, text, max(len(s.split()) for s in want), version=4)
    runs = {" ".join(want[i:j]) for i in range(len(want)) for j in range(i + 1, len(want) + 1)}
    assert len(pieces) > 1 and all(p in runs for p in pieces) and " ".join(pieces) == text


def test_v3_saw_neither_the_capitals_nor_the_abbreviations():
    """What v4 changes, on real text: v3 cut the Czech and the Greek footnote
    after "č.", "věst.", "s.", "αριθ." and "σ." (a digit or a capital follows),
    and never cut the Bulgarian recital before "Член" (a capital it did not see)."""
    def v3_cuts_after(lang: str, i: int) -> list[str]:
        _name, _moved, want = GOLDEN_V4[lang][i]
        return [s.rsplit(" ", 1)[-1] for s in cl._sentences(" ".join(want), 3)[:-1]]
    assert v3_cuts_after("cs", 1) == ["č.", "věst.", "s."]
    assert v3_cuts_after("el", 1) == ["αριθ.", "σ."]
    assert v3_cuts_after("bg", 0) == []


def test_a_polish_committee_do_spraw_is_no_sentence_end():
    """"Grupa Robocza ds. Ochrony Osób Fizycznych" (do spraw): Polish names
    every committee so (Guide pl § 10.3.1); v3 cut after "ds."."""
    (p,) = [x for x in _paragraphs("32016R0679pl.html") if "Grupę Roboczą ds." in x]
    whole = ("Powinna ona zastąpić Grupę Roboczą ds. Ochrony Osób Fizycznych w zakresie Przetwarzania "
             "Danych Osobowych, powołaną na mocy dyrektywy 95/46/WE.")
    assert whole in cl._sentences(p, 4) and len(cl._sentences(p, 4)) == 8
    assert whole not in cl._sentences(p, 3) and len(cl._sentences(p, 3)) == 9


def test_an_unece_revision_is_no_sentence_end():
    """"UN/ECE Regulation 49 Rev. 06" in 32017R2400's annex (a real Formex
    fixture): v3 cut after "Rev."."""
    import xml.etree.ElementTree as ET

    from app.plugins.eurlex.formex import norm
    f = Path(__file__).parent / "fixtures" / "eurlex" / "coverage" / "32017R2400_en" / "L_2017349EN.01005801.xml"
    text = ("Fuels compatible with use by the engine declared by the manufacturer in accordance with "
            "paragraph 4.6.2. of UN/ECE Regulation 49 Rev. 06 (as applicable)")
    assert text in {norm("".join(c.itertext())) for c in ET.parse(f).iter("CELL")}
    assert cl._sentences(text, 4) == [text]
    assert cl._sentences(text, 3) == [text[:text.index(" 06 (")], "06 (as applicable)"]


@pytest.mark.parametrize("marks", [["α)", "β)", "γ)", "στ)"], ["(α)", "(β)", "(γ)", "(δ)"],
                                   ["а)", "б)", "в)", "г)"], ["(а)", "(б)", "(в)", "(г)"]])
def test_v4_cuts_a_greek_or_bulgarian_paragraph_at_its_points(marks, word):
    """The point level: "α)" and "а)" are the Greek and Bulgarian acts' own
    marks; v3 knew ASCII letters only and went on to the sentences."""
    lines = ["1. Η επεξεργασία είναι σύννομη μόνο εάν ισχύει τουλάχιστον μία από τις εξής προϋποθέσεις:"]
    lines += [f"{m} " + " ".join(f"λέξη{i}" for i in range(30)) + ";" for m in marks]
    text = "\n".join(lines)
    v4 = cl.split(word, text, 70, version=4)
    assert all(p.split("\n", 1)[0].split()[0] in [*marks, "1."] for p in v4), "every piece starts at a point"
    assert "\n".join(v4) == text and all(len(p.split()) <= 70 for p in v4)
    v3 = cl.split(word, text, 70, version=3)
    assert v3 != v4 and "\n".join(v3) != text, "v3 cut at the sentences, the lines joined by spaces"
    if not marks[0].startswith("("):          # "(α)" opens a sentence for v3 too ("(" is in its class)
        assert any(not p.startswith(tuple(marks) + ("1.",)) for p in v3), "v3 cut inside a point"


def test_a_short_form_is_refused_as_a_cut_only_before_a_number():
    """"s. 1" is a page; "10 s. The" is ten seconds, and a sentence end."""
    assert cl._sentences("Viz Úř. věst. L 119, s. 1. Dále", 4) == ["Viz Úř. věst. L 119, s. 1.", "Dále"]
    assert cl._sentences("Heat it for 10 s. The sample cools.", 4) == ["Heat it for 10 s.", "The sample cools."]
    assert cl._sentences("Η δοκιμή διαρκεί 10 σ. Το δείγμα ψύχεται.", 4) == \
        ["Η δοκιμή διαρκεί 10 σ.", "Το δείγμα ψύχεται."]


def test_an_initial_in_a_new_capital_is_no_sentence_end():
    assert cl._sentences("Το υπέγραψε ο Ε. Παπαδόπουλος. Ο Ł. Kowalski όχι.", 4) == \
        ["Το υπέγραψε ο Ε. Παπαδόπουλος.", "Ο Ł. Kowalski όχι."]


def test_v4_chunk_ids_carry_their_version_and_5_is_refused(word):
    params = {"tokenizer": SHA, "max_tokens": 64}
    (c,) = cl.chunk_legal_article("eurlex:1", 1, [("Άρθρο 1", "Ο παρών κανονισμός εφαρμόζεται.")], 4, params)
    assert c["id"] == "eurlex:1:0:4"
    with pytest.raises(cl.LegalChunkerError, match="versions"):
        list(cl.chunk_legal_article("eurlex:1", 1, [("Art. 1", "x")], 5, params))



def test_an_ordinal_cuts_nothing_only_before_the_words_that_continue_it():
    """Measured 2026-11-21 on 32016R0679 and 32013R0575 from Cellar: German
    dates (~470 of 488 candidates in 32013R0575 de) and Croatian ordinals
    before their act (~240 of 388) are not sentence ends; a sentence start
    after an ordinal still is, and Slovenian ends its sentences there."""
    cut = lambda t: cl._sentences(t, 4)     # noqa: E731
    assert cut("des Rates vom 8. Dezember 1986 über den Jahresabschluss.") == \
        ["des Rates vom 8. Dezember 1986 über den Jahresabschluss."]
    assert cut("Bis spätestens 25. Mai 2018 teilt er dies mit.") == ["Bis spätestens 25. Mai 2018 teilt er dies mit."]
    assert cut("in Kraft gemäß Artikel 5. Die Kommission erlässt.") == \
        ["in Kraft gemäß Artikel 5.", "Die Kommission erlässt."]
    for t in ("u skladu s člankom 81. Uredbe (EU) br. 1093/2010, posebno.",
              "definiran člankom 1. stavkom 2. Direktive 2009/65/EZ Europskog parlamenta.",
              "na odredbama članka 114. UFEU-a, koji se tumači."):
        assert cut(t) == [t], t
    assert cut("Za potrebe stavka 3. Komisija može provedbenim aktima.") == \
        ["Za potrebe stavka 3.", "Komisija može provedbenim aktima."]
    assert cut("v skladu s členom 112. Kadar je izpostavljenost preostala.") == \
        ["v skladu s členom 112.", "Kadar je izpostavljenost preostala."]
    assert cut("A sentence ends in 2016. Uredba applies.") == ["A sentence ends in 2016.", "Uredba applies."], \
        "the nominative opens a sentence"
    # v3 is v3
    assert cl._sentences("des Rates vom 8. Dezember 1986 über.", 3) == ["des Rates vom 8.", "Dezember 1986 über."]
