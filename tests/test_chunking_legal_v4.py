"""`legal_article` VERSION 4 (2026-11-21, P8 of the EUR-Lex review;
docs/notes/legal-chunker-v4.md): the sentence level of the 24 EU languages —
a sentence start in any capital of their Latin, Greek and Cyrillic blocks (a
FROZEN literal, pinned here by its sha256), no cut after their abbreviations
— and version 3 byte for byte what it was. v4 cuts as v3 wherever a text
holds none of the new capitals and none of the new abbreviations, by
construction; held here on the four legal plugins' real fixtures (it, fr,
de, en) and on texts built from them."""
from __future__ import annotations

import hashlib
import random
import re
import unicodedata

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


def test_the_shared_layer_stays_at_3_until_the_space_moves():
    """Moving it moves the shared legal space (legal-chunker-v4.md): not here."""
    assert cl.CHUNK_VERSION == 3 and cl.SHARED_LAYER["chunker"]["version"] == 3
    assert 4 in cl.VERSIONS


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
PATTERNS_SHA256 = {"end": "06a5996d0b0c7e07e87c264f693bb0871ec413c09ff0f98f1cb3edfb7751f1b3",
                   "abbreviations": "f724ac973e24a06f2116449920eb965cc3a3bf45f2cb03ae4766e0e74bdc2e12",
                   "before_number": "48e404b0401d4225c53d7f98908bf63737c8d4cc5565e15c31838b8fbdd696a5",
                   "point": "02ff0919c338a8c13312f35c99c148add689b3f5a5846af683b73391d01a7a8e"}


def test_v4_s_patterns_are_pinned():
    got = {"end": cl._SENTENCE_END_V4.pattern, "abbreviations": cl._ABBREVIATIONS_V4.pattern,
           "before_number": cl._ABBREVIATIONS_V4_NUMBER.pattern, "point": cl.POINT_V4.pattern}
    assert {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in got.items()} == PATTERNS_SHA256


def test_the_abbreviations_are_the_24_languages_and_new_to_v3():
    langs = [lang for lang, _forms in cl.ABBREVIATIONS_V4]
    assert langs == sorted(langs) and len(langs) == 24 and set(langs) == set(
        "bg cs da de el en es et fi fr ga hr hu it lt lv mt nl pl pt ro sk sl sv".split())
    for f in (*cl.ABBREVIATION_FORMS_V4, *cl.ABBREVIATIONS_V4_BEFORE_NUMBER):
        assert not cl._ABBREVIATIONS.search(f + "."), f"{f!r}: v3 knows it already"
        assert len(f) + 2 < cl._V4_LOOKBACK
    assert len("Buchst.") + 1 < cl._V4_LOOKBACK, "v3's longest form within the look back too"


# ---- identity with v3 where nothing new stands -------------------------------------------------------

_NEW_FORMS = re.compile("(?<![^\\s(\\[«\"„“‘'/])(?:" + "|".join(
    re.escape(f).replace("\\ ", "\\s") for f in sorted({*cl.ABBREVIATION_FORMS_V4, *cl.ABBREVIATIONS_V4_BEFORE_NUMBER},
                                                       key=lambda f: (-len(f), f))) + ")\\.")


#: a Greek or Cyrillic small letter anywhere: what a point mark of v4's alone needs
_GREEK_CYRILLIC_SMALL = re.compile("[α-ωа-я]")


def _holds_something_new(text: str) -> bool:
    """A new capital anywhere, a new abbreviation as a token, or a Greek or
    Cyrillic small letter (a point mark of v4's): read off the text alone,
    nothing of the cut's machinery."""
    return (any(c in cl.NEW_CAPITALS_V4 for c in text) or _NEW_FORMS.search(text) is not None
            or _GREEK_CYRILLIC_SMALL.search(text) is not None)


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


# ---- the 24 languages' sentences (written for the test in each language's drafting
# conventions — no fixture holds a body in these languages; EUR-Lex's are English) -------------------

GOLDEN_V4 = {
    "el": ["Τα κράτη μέλη εφαρμόζουν το άρθ. 5 παρ. 2 του κανονισμού (ΕΕ) αριθ. 1093/2010.",
           "Η Επιτροπή ενημερώνεται χωρίς καθυστέρηση.",
           "Βλ. επίσης σ. 12 της ΕΕ L 119."],
    "bg": ["Държавите членки прилагат чл. 5, ал. 2 от Регламент (ЕС) № 1093/2010.",
           "Съветът се информира незабавно.",
           "Вж. също стр. 3 и т. 4."],
    "cs": ["Členské státy uplatňují čl. 5 odst. 2 písm. a) nařízení (EU) č. 1093/2010.",
           "Členské státy o tom informují Komisi.",
           "Viz Úř. věst. L 119, 4.5.2016, s. 1."],
    "pl": ["Państwa członkowskie stosują art. 5 ust. 2 lit. a) rozporządzenia (UE) nr 1093/2010.",
           "Środki te zgłasza się Komisji.",
           "Zob. Dz.U. L 119 z 4.5.2016, s. 1."],
    "hu": ["A tagállamok alkalmazzák a 2011. évi CXII. tv. 5. §-át és az 1093/2010/EU rendelet 5. cikkét.",
           "Őket a Bizottság tájékoztatja.",
           "Vö. HL L 119., 2016.5.4., 1. o."],
    "ro": ["Statele membre aplică art. 5 alin. (2) lit. (a) din Regulamentul (UE) nr. 1093/2010.",
           "Ținând seama de acestea, Comisia este informată.",
           "A se vedea JO L 119, 4.5.2016, p. 1."],
    "lt": ["Valstybės narės taiko Reglamento (ES) Nr. 1093/2010 5 str. 2 dalį.",
           "Šios priemonės pranešamos Komisijai.",
           "Žr. OL L 119, 2016 5 4, p. 1."],
    "lv": ["Dalībvalstis piemēro Regulas (ES) Nr. 1093/2010 5. panta 2. punktu.",
           "Šos pasākumus paziņo Komisijai, sk. 7. pantu.",
           "Ēdināšanas pakalpojumiem to nepiemēro."],
    "mt": ["L-Istati Membri għandhom japplikaw l-Art. 5, par. 2, tar-Regolament (UE) Nru 1093/2010.",
           "Żmien twil ma għandux jgħaddi.",
           "Ara wkoll ĠU L 119, 4.5.2016, p. 1."],
    "sk": ["Členské štáty uplatňujú čl. 5 ods. 2 nariadenia (EÚ) č. 1093/2010.",
           "Ďalšie opatrenia sa oznamujú Komisii.",
           "Pozri Ú. v. EÚ L 119, 4.5.2016, s. 1."],
    "sl": ["Države članice uporabljajo člen 5(2) Uredbe (EU) št. 1093/2010, glej tudi odst. 3 in tč. (a).",
           "Člen 7 se ne uporablja.",
           "Glej UL L 119, 4.5.2016, str. 1."],
    "hr": ["Države članice primjenjuju članak 5. stavak 2. točku (a) Uredbe (EU) br. 1093/2010, "
           "vidi i čl. 7 st. 3.",
           "Što se tiče rokova, primjenjuje se članak 9.",
           "Vidjeti SL L 119, 4.5.2016., str. 1."],
    "et": ["Liikmesriigid kohaldavad määruse (EL) nr 1093/2010 artikli 5 lõiget 2.",
           "Šveitsi suhtes kohaldatakse artiklit 7.",
           "Vt ELT L 119, 4.5.2016, lk 1."],
    "fi": ["Jäsenvaltioiden on sovellettava asetuksen (EU) N:o 1093/2010 5 artiklan 2 kohtaa, ks. myös s. 12.",
           "Äänestys toimitetaan kirjallisesti.",
           "Ks. EUVL L 119, 4.5.2016, s. 1."],
    "ga": ["Cuirfidh na Ballstáit Airt. 5 de Rialachán (AE) Uimh. 1093/2010 i bhfeidhm.",
           "Ní bheidh feidhm ag Airteagal 7.",
           "Féach IO L 119, 4.5.2016, lch. 1."],
}


@pytest.mark.parametrize("lang", sorted(GOLDEN_V4))
def test_v4_cuts_each_language_at_its_sentence_ends_and_never_after_an_abbreviation(lang, word):
    want = GOLDEN_V4[lang]
    text = " ".join(want)
    assert cl._sentences(text, 4) == want
    # through the chunker's own split, at a window of the longest sentence:
    # every piece is whole sentences, the text whole
    pieces = cl.split(word, text, max(len(s.split()) for s in want), version=4)
    runs = {" ".join(want[i:j]) for i in range(len(want)) for j in range(i + 1, len(want) + 1)}
    assert len(pieces) > 1 and all(p in runs for p in pieces) and " ".join(pieces) == text


def test_v3_saw_neither_the_capitals_nor_the_abbreviations():
    """What v4 changes, on two of them: v3 cut Greek after "άρθ.", "παρ.",
    "αριθ." and "σ." (a digit follows) and never before "Η" or "Β"."""
    el = " ".join(GOLDEN_V4["el"])
    assert cl._sentences(el, 3) == ["Τα κράτη μέλη εφαρμόζουν το άρθ.", "5 παρ.", "2 του κανονισμού (ΕΕ) αριθ.",
                                    "1093/2010. Η Επιτροπή ενημερώνεται χωρίς καθυστέρηση. Βλ. επίσης σ.",
                                    "12 της ΕΕ L 119."]
    cs = " ".join(GOLDEN_V4["cs"])
    assert cl._sentences(cs, 3) == ["Členské státy uplatňují čl.", "5 odst.", "2 písm. a) nařízení (EU) č.",
                                    "1093/2010. Členské státy o tom informují Komisi.", "Viz Úř. věst.",
                                    "L 119, 4.5.2016, s.", "1."]


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
