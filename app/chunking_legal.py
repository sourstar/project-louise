"""The `legal_article` chunker (2026-11-08; docs/notes/legal-chunking-retrieval.md):
one chunk per article, a longer article split at its OWN structure, and no
chunk ever longer than the encoder's window — NOTHING IS TRUNCATED, NOTHING
DROPPED (operator, 2026-11-08: a chunk truncated at the window "is not
admissible, it would break the meaning of important parts").

The studies it follows (the note gives the numbers and the citations):
  * the unit is the article — every statute benchmark labels at article
    level (BSARD, COLIEE Task 3, JuriFindIT, GerLayQA) — and chunking by
    the document's own hierarchy beats fixed-size windows on legislation
    (NitiBench, EMNLP 2025; StrucChunk 2026; Lima 2024);
  * an article past the window is split at the next unit down its
    hierarchy, as the drafting manuals number them, packed back up to the
    window so no piece is needlessly short:
      1. the PARAGRAPH: an Italian comma and an EU paragraph `1.`, `2-bis.`
         (Circolare 20 aprile 2001; Joint Practical Guide), a German Absatz
         `(1)`, `(2a)` (Handbuch der Rechtsförmlichkeit), a French
         numbered part `I. —`, `II. –` (Guide de légistique);
      2. the POINT: `a)`, `(a)`, `aa)`, `1°`, `(i)`;
      3. the SENTENCE, at a full stop, semicolon or colon followed by a
         capital or a digit, never after the abbreviations statutes use
         (`art.`, `Abs.`, `n.`, `Nr.` …);
      4. last, for a single sentence still past the window, consecutive
         token windows of the pinned tokenizer — the text continues in the
         next chunk, it is never cut off;
  * `header` (a parameter, inside `space_id`; operator 2026-11-08, piece 1
    "by design"): the record's TITLE (the act) and the section's LABEL (its
    place in the hierarchy and the article), "<title> › <label>", repeated
    at the head of every piece and counted inside the window — the headings
    G-DSR's graph fuses into each article (Louis et al., EACL 2023: BSARD
    mAP 35.3 → 47.1 from the hierarchy's headings and neighbours) written
    into the text the encoder reads, the deterministic form of contextual
    chunk headers (Anthropic, "Contextual Retrieval", 2024: −35 % failures);
  * `outline` (piece 2): one more chunk per record of two or more sections —
    the title and every section's label in order, the act's own index — so
    a question about a whole law reaches the act (Lima 2024's levels;
    G-DSR's section nodes); token-bounded like any piece, its chapter
    `outline`, after the articles so their sequence numbers stay put.

VERSION 2 (2026-11-08, from the first real Normattiva records cut by v1):
  * the header is not said twice — a section whose body begins with its
    label's last segment (Normattiva's "Art. 1. — (Delega …)", the body's
    own first line) has that line dropped from the PIECE, the header
    carrying it; the record keeps it (sections are not touched);
  * the outline is a TABLE OF CONTENTS: each partition written once, the
    articles under it indented, not every label's whole path on every
    line (legge 306/2003's outline repeated "CAPO I — DISPOSIZIONI
    GENERALI …" before each of its articles).
Version 1 cuts as it always did (invariant 3): a layer pinned to it gives
the same chunks.

BOUNDED (2026-11-20, no version: the pieces are byte for byte the same):
no encode reads more than SPAN_CHARS characters — a count and a token window
read the text in spans cut at a space between two non-spaces, which the
pinned tokenizer's pre-tokenization never crosses (`spans_are_exact`). One
encode of a 54.3 M-character text held 11.18 GB (EUR-Lex5's 32018R0688 el).

VERSION 3 (2026-11-08, München's profile of normattiva-nemo's records build:
`_count` under `_pack` was 87-92 % of every pool child's time, ~4.5 h for
341,959 records): units are PACKED LINEARLY. v2 re-encoded the whole growing
piece for every unit it added — quadratic in a text's units (a 399-comma
article: 402 encodes over 7.7 M characters). v3 counts each unit ONCE, packs
on the sum of the counts and the separator's, and holds every piece to the
bound by ONE exact count of the piece itself, giving a unit back while that
count is over (the sum can differ from the joined text's count where the
tokenizer merges across a join). So no piece is ever longer than the window,
as before, and a piece may be a little shorter than v2's where the sum
over-counts. Its cut differs from v2's, hence the version (invariant 3).

VERSION 4 (2026-11-21, P8 of docs/notes/eurlex-review-2026-11-21.md;
docs/notes/legal-chunker-v4.md): the SENTENCE level of the 24 EU languages.
v3 saw a sentence start only in a Latin-1 capital, so a paragraph past the
window in el, bg, cs, sk, sl, hr, pl, hu, ro, lt, lv or mt was never split at
its sentences and fell to `_windows`, mid-sentence; and it knew only it/de/
fr/en abbreviations, so "čl. 5", "stk. 2", "αριθ. 1093/2010" or "Úř. věst. L"
were cut after the abbreviation (the lookahead takes a digit). v4:
  * a sentence may start with any capital of the Latin, Greek and Cyrillic
    blocks the 24 languages write in — CAPITALS_V4, a FROZEN literal (never
    read off the interpreter's Unicode tables: invariant 3), pinned by its
    sha256 in tests/test_chunking_legal_v4.py;
  * no cut after the abbreviations the 24 languages' legal text writes for
    article, paragraph, point, letter, number, page, the Official Journal,
    "see", "for example", "that is" (ABBREVIATIONS_V4, each checked against
    the Interinstitutional Style Guide and real EUR-Lex text:
    docs/notes/legal-chunker-v4-abbreviations.md), nor after an initial in a
    new capital; seven short forms that are also units or words ("s",
    seconds; "т", tonnes; "σ"; "vol", "% vol."; "cap", "kap", "Kap") only
    before a number (ABBREVIATIONS_V4_BEFORE_NUMBER);
  * a Greek or Bulgarian point, "α)", "(β)", "а)", is a point (POINT_V4):
    v3's POINT knew ASCII letters only, so such a paragraph skipped its
    points for its sentences.
IDENTICAL TO v3 BY CONSTRUCTION wherever a text holds none of them: v4's
sentence candidates are v3's plus those before a new capital, each decided
by the text around it alone, and v4 refuses a candidate wherever v3 did,
adding refusals only after the new abbreviations; its point marks are v3's
plus the Greek and Cyrillic ones; levels 0 and 3 are v3's. So v4 cuts a
text differently from v3 only where it holds a new capital, a new
abbreviation or a Greek or Cyrillic point mark, and only in a section or
outline past the window (levels 1 and 2 are reached nowhere else). Version
3 cuts as it always did; `CHUNK_VERSION` stays 3 — the shared legal space's
— until the space moves (the note: what moving it takes).

The TITLE is the record's (09 §9.5), passed by every caller that cuts
(`layers.chunks_for_document(..., title=)`, guarded by
tests/test_chunking_legal.py): invariant 3's "cleaned text" is
`[title, sections]`. A layer that asks for the header or the outline and is
given no title is refused, never cut otherwise.

The window is `max_tokens` tokens of the tokenizer the layer pins by the
sha256 of its tokenizer.json (as `wikidata-text`, app/plugins/wikidata/
chunker.py, which loads it); the recipe sets it to the space's `ctx` less
the document instruction's tokens, so what the encoder receives fits.

THE STANDARD, NOT ADOPTED WHOLE: LangChain's RecursiveCharacterTextSplitter
is the same recursion over separators (Chroma's evaluation found it
competitive); here the separators are the statutes' own numbering and the
bound is in the encoder's tokens, which it does not count. Late chunking
(Günther et al. 2024) is the measured alternative for long articles
(+0.5 to +6.5 nDCG@10) and needs the whole article in one window; it is
written down in the note, not built.

A pure function of (sections, version, params) and the pinned tokenizer
(invariant 3). Chunk ids `<uid>:<seq>:<version>`.
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import Any, Iterator, Sequence

#: the version a layer that names none is cut at, and the shared legal
#: space's (SHARED_LAYER): moving it moves that space (legal-chunker-v4.md).
#: 4 since 2026-11-23 (operator, M1 = "A": the four legal collections move to
#: S4 in one window, before EUR-Lex5's restart). It is what a NEW legal
#: collection's Codex declares; a held one keeps the version its Codex pins
#: (every Codex layer names one), and one whose space moved would be refused
#: at start by its space pin, never re-cut in place (invariant 3)
CHUNK_VERSION = 4
VERSIONS = (1, 2, 3, 4)
NAME = "legal_article"

#: 1. a paragraph's number at the start of a line, in the four systems
PARAGRAPH = re.compile(
    r"^(?:\d+(?:-?[a-z]+)?\.(?:\s|$)"                     # 1.  2-bis.  3bis.   (IT, EU)
    r"|\(\d+[a-z]?\)\s"                                   # (1)  (2a)          (DE)
    r"|[IVXLC]+\.\s*[-–—]\s?)")                           # I. —  II. –        (FR)
#: 2. a point's mark at the start of a line
POINT = re.compile(
    r"^(?:[a-z]{1,2}\)\s"                                 # a)  aa)            (IT, DE, FR)
    r"|\([a-z]{1,4}\)\s"                                  # (a)  (iv)          (EU)
    r"|\d+°\s?"                                           # 1°                 (FR)
    r"|\d+\)\s)")                                         # 1)                 (IT numero)
#: 2, from version 4: the points of the Greek and the Bulgarian acts too —
#: "α)", "στ)", "(β)", "а)", "(б)" (the Joint Practical Guide's "(a)" in their
#: alphabets), their small letters by code point, U+03B1-U+03C9 and
#: U+0430-U+044F: v3 read such a point as none and went on to its sentences
POINT_V4 = re.compile(
    r"^(?:[a-z]{1,2}\)\s|\([a-z]{1,4}\)\s|\d+°\s?|\d+\)\s"       # v3's, as they are
    "|[α-ωа-я]{1,2}\\)\\s"                    # α)  στ)  а)
    "|\\([α-ωа-я]{1,4}\\)\\s)")               # (β)  (б)
#: 3. a sentence ends at . ; : followed by space and a capital, a digit or a
#: quote — not after the abbreviations legal text is full of
_SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-ZÀ-ÖØ-Þ0-9«\"„(])")
_ABBREVIATIONS = re.compile(
    r"(?:\b(?:art|artt|Art|Abs|Nr|n|nn|lett|co|comma|cod|c|cfr|Buchst|Satz|S|vgl|ggf|bzw|z\.B|"
    r"al|Al|para|paras|No|no|p|pp|ch|chap|Dir|Reg|Rn|Rdnr|Ziff|lit|ss|seq|etc|ca|ex|op|cit|"
    r"L|R|D|A|LO|Bd|Hrsg|St|Cass|Cost|gg|v|vs)\.|\b[A-Z]\.)$")

# ---- version 4: the sentence level of the 24 EU languages (2026-11-21) -------
#
# THE CAPITALS, FROZEN. Every letter of Unicode category Lu or Lt in the
# blocks the 24 official languages write in — Basic Latin, Latin-1
# Supplement, Latin Extended-A and -B, Greek and Coptic (its Coptic letters
# U+03E2-U+03EF left out), Cyrillic — one line a block. UAX #29's sentence
# rules (Unicode's own segmentation) take a sentence start the same way, an
# `Upper` after a full stop and a space; this is that class over these
# blocks, written out ONCE from Python 3.12's tables (Unicode 15.0; 3.13's
# 15.1 gives the same 403 letters) and never read from the interpreter's
# again: `str.isupper()` at import would move a cut with the Unicode database
# (invariant 3). Its sha256 is pinned in tests/test_chunking_legal_v4.py; the
# script that wrote it is in that commit's message. A letter is wider than a
# language's alphabet on purpose: a sentence opening on a name or a quotation
# in another of these scripts is a sentence start too, and a text holding
# none of them cuts as v3 whatever the class holds.
CAPITALS_V4 = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞ"
    "ĀĂĄĆĈĊČĎĐĒĔĖĘĚĜĞĠĢĤĦĨĪĬĮİĲĴĶĹĻĽĿŁŃŅŇŊŌŎŐŒŔŖŘŚŜŞŠŢŤŦŨŪŬŮŰŲŴŶŸŹŻŽ"
    "ƁƂƄƆƇƉƊƋƎƏƐƑƓƔƖƗƘƜƝƟƠƢƤƦƧƩƬƮƯƱƲƳƵƷƸƼǄǅǇǈǊǋǍǏǑǓǕǗǙǛǞǠǢǤǦǨǪǬǮǱǲǴǶǷǸǺǼǾȀȂȄȆȈȊȌȎȐȒȔȖȘȚȜȞȠȢȤȦȨȪȬȮȰȲȺȻȽȾ"
    "ɁɃɄɅɆɈɊɌɎ"
    "ͰͲͶͿΆΈΉΊΌΎΏΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩΪΫϏϒϓϔϘϚϜϞϠϴϷϹϺϽϾϿ"
    "ЀЁЂЃЄЅІЇЈЉЊЋЌЍЎЏАБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯѠѢѤѦѨѪѬѮѰѲѴѶѸѺѼѾҀҊҌҎҐҒҔҖҘҚҜҞҠҢҤҦҨҪҬҮҰҲҴҶҸҺҼҾ"
    "ӀӁӃӅӇӉӋӍӐӒӔӖӘӚӜӞӠӢӤӦӨӪӬӮӰӲӴӶӸӺӼӾ")


def _v3_capital(c: str) -> bool:
    """In v3's class `A-ZÀ-ÖØ-Þ`: compared by code point, no table read."""
    return "A" <= c <= "Z" or "À" <= c <= "Ö" or "Ø" <= c <= "Þ"


#: the capitals v3 did not see: an initial in one of them is an abbreviation
NEW_CAPITALS_V4 = "".join(c for c in CAPITALS_V4 if not _v3_capital(c))

# THE ABBREVIATIONS, by language: each form as written before its full stop
# (an inner space is any white space), what it stands for, and WHERE IT IS
# WRITTEN SO. CHECKED (2026-11-21) against the Interinstitutional Style Guide
# in each language (style-guide.europa.eu, the edition of 18.11.2026: § 3.1
# the Official Journal, § 3.2.2-3.2.3 references to acts and their
# subdivisions, § 2.7, § 5.9, Part Four and Annex A3), and against real text:
# 143 Cellar items (six acts in the 24 languages), the fixtures under
# tests/fixtures/ (EUR-Lex, Normattiva, LEGI, NeuRIS). "§" below is the
# language's Guide section, "×n" the occurrences before a digit or a capital
# in the Cellar items; the per-language tables, every form removed and why:
# docs/notes/legal-chunker-v4-abbreviations.md. A form neither the Guide nor
# the text writes with a full stop is not here: EU acts write the
# subdivisions in full in el, bg, de, es, fr, it, nl, pt, ro, hr, sl, lt,
# lv, mt, ga, hu (each Guide's § 3.2.3; el § 10.10(c): abbreviating them "is
# expressly forbidden"); only cs (čl., odst., písm.: § 10.8), sk (ods.,
# písm.), pl (ust.) and da (stk.) abbreviate them. Estonian writes its
# abbreviations without a full stop (§ 3.1 "EÜT L 32, 6.2.1968, lk 6"; "nr",
# "lk" ×195 bare, none dotted), so it has none here.
# LEFT OUT ON PURPOSE: a form that ends a sentence as often as it continues
# one — "etc." and "et seq." and their counterparts (atd., itd., и др., κ.λπ.,
# stb., ff., suiv., segg., köv. …), "lpp."/"o." after a page number,
# "Sb."/"Z. z." after an act's number — where taking it would join two
# sentences; a form that is a word or a unit elsewhere ("sal", Spanish salt;
# "por", Hungarian powder; "i", a Danish and Swedish preposition that ends a
# sentence: "som de indgår i."; the year "г." is also the Bulgarian gram, the
# Lithuanian year "m." the metre, so neither is here, while the Polish year
# "r." and the Lithuanian day "d." are: a date before a capital continues its
# sentence in 18 of 21 and 81 of 84 cases); and every form v3 already knows
# (`_ABBREVIATIONS`: art., Nr., p., lit., v. …).
ABBREVIATIONS_V4: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bg", ("стр",                           # страница, page: "ОВ L 32, 6.2.1968 г., стр. 6" (§ 3.1; ×89)
            "бр",                            # брой, issue: "бр. 1—2" (§ 10.1.8)
            "вж", "Вж",                      # виж, see (§ 2.7, § 3.2.1, § 5.9.1)
            "срв",                           # сравни, cf. (§ 10.4.2, Annex A3)
            "напр",                          # например (§ 3.1, § 10.1.1)
            "т.е", "т.нар")),                # тоест, така наречен (§ 10.1.1, § 10.4.2)
    ("cs", ("čl", "Čl",                      # článek (§ 3.2.3, § 10.8; ×392, ×425)
            "odst",                          # odstavec (§ 3.2.3, § 10.8; ×822)
            "písm",                          # písmeno (§ 3.2.3, § 10.8)
            "č",                             # číslo: "nařízení (EU) č. 127/2010" (§ 3.2.2; ×103)
            "věst",                          # "Úř. věst. L 32, 6.2.1968, s. 6" (§ 3.1; ×89)
            "např", "Např",                  # například (Annex A3)
            "tj", "tzv",                     # to jest, takzvaný (§ 3.1, § 3.2.4)
            "srov")),                        # srovnej, cf. (§ 3.3.1)
    ("da", ("stk", "Stk",                    # stykke: "artikel 1, stk. 2" (§ 3.2.3; ×1134), "Stk. 1 berører" (×10)
            "nr",                            # nummer: "forordning (EU) nr. 127/2010" (§ 3.2.2; ×118)
            "pr",                            # per: "indført pr. 1. januar 1968" (§ 3.1)
            "jf", "f.eks", "dvs", "bl.a")),  # jævnfør, for eksempel, det vil sige, blandt andet
    ("de", ("ABl",                           # Amtsblatt: "ABl. L 32 vom 6.2.1968, S. 6" (§ 3.1; ×90)
            "BGBl",                          # Bundesgesetzblatt: "BGBl. I 2020, 1234" (NeuRIS fixture)
            "z",                             # "z. B." spaced (§ 3.2.1, § 10.1; ×16); v3 knows "z.B."
            "d.h", "d. h",                   # das heißt (§ 3.1, § 3.2.1)
            "u. a")),                        # unter anderem: "u. a. Bedingungen" (§ 10.1; 32016R1036)
    ("el", ("αριθ",                          # αριθμός: "κανονισμός (ΕΕ) αριθ. 127/2010" (§ 3.2.2; ×112)
            "σελ",                           # σελίδα, page: "σ." is the Guide's (§ 3.1), "σελ." Union texts
                                             # write too ("όπως και η συντομογραφία σελ.", Annex A3)
            "βλ", "Βλ",                      # βλέπε, see (§ 2.7, § 5.9.1, § 10.1.2)
            "π.χ", "δηλ")),                  # παραδείγματος χάριν, δηλαδή (Annex A3)
    ("en", ("e.g", "i.e",                    # (§ 10.8; fixtures)
            "Rev",                           # revision: "UN/ECE Regulation 49 Rev. 06" (fixtures)
            "Vol")),                         # volume: "Vol. 34, Issue 3" (§ 5.9.4)
    ("es", ("párr", "arts", "p. ej")),       # párrafo, artículos, por ejemplo (Annex A3)
    ("et", ()),
    ("fi", ("ks", "Ks",                      # katso, see (§ 2.7, § 3.2.1, § 5.9.1)
            "vrt", "Vrt",                    # vertaa, cf. (§ 10.8)
            "esim", "Esim")),                # esimerkiksi (§ 3.1, § 3.2.1, § 3.2.2)
    ("fr", ("cf",)),                         # confer (Part Four, Annexe B; LEGI fixture)
    ("ga", ("Uimh",                          # Uimhir: "Rialachán (AE) Uimh. 1234/2010" (§ 3.2.2; ×97)
            "lch")),                         # leathanach: "IO L 32, 6.2.1968, lch. 6" (§ 3.1; ×79)
                                             # (and "e.g.", § 10.5, which is en's)
    ("hr", ("br",                            # broj: "Uredba (EU) br. 127/2010" (§ 3.2.2; ×103)
            "str",                           # stranica: "SL L 32, 6.2.1968., str. 6." (§ 3.1; ×90)
            "npr", "tj")),                   # na primjer, to jest (§ 3.1, § 10.1.12)
    ("hu", ("sz",                            # szám: "29. sz. (1930)" (§ 5.9.3, § 10.1.8; ×8)
            "pl", "vö", "ún")),              # például, vesd össze, úgynevezett (§ 3.2.1, § 10.3)
    ("it", ("pag",                           # pagina: "GU L 32 del 6.2.1968, pag. 6" (§ 3.1; ×93)
            "pagg",                          # pagine (§ 5.9.3, Annex A3)
            "D.Lgs")),                       # decreto legislativo: "Il D.Lgs. 2 gennaio 2018" (Normattiva fixture)
    ("lt", ("žr", "Žr",                      # žiūrėk, see (§ 2.7, § 5.9.1)
            "pvz", "Pvz",                    # pavyzdžiui (§ 10.1.1, Annex A3)
            "plg",                           # palygink, cf. (§ 10.1.6, Annex A3)
            "t. y",                          # tai yra (§ 3.2.2, § 10.1.1)
            "str",                           # straipsnis (Annex A3)
            "d")),                           # diena, the date's day: "2016 m. balandžio 27 d. Europos
                                             # Parlamento ir Tarybos reglamentas" (§ 3.2.2, § 10.1.1;
                                             # 81 of 84 continue their sentence)
    ("lv", ("sk", "Sk",                      # skatīt, see (§ 2.7, § 3.2.1, § 5.9.1)
            "piem",                          # piemēram (§ 10.1.1, Annex A3)
            "t.i", "t. i",                   # tas ir: "t. i." (§ 10.6, § 10.7), "t.i.," (31992L0043 lv ×3)
            "Lpp")),                         # lappuse, in the legacy header "L 206, 22/07/1992 Lpp. 0007"
    ("mt", ("eż",                            # eżempju (§ 10.1.1, Annex A3); paragrafu is "para." (v3)
            "Vol")),                         # volum (§ 5.9.4)
    ("nl", ("nr",                            # nummer: "Verordening (EU) nr. 127/2010" (§ 3.2.2; ×111)
            "blz",                           # bladzijde: "PB L 32 van 6.2.1968, blz. 6" (§ 3.1; ×92)
            "bv",                            # bijvoorbeeld, "afgekort als bv. (en niet bijv.)" (§ 10.4.1, § 3.1)
            "d.w.z", "o.a")),                # dat wil zeggen, onder andere (§ 3.2.2, § 10.1)
    ("pl", ("ust", "Ust",                    # ustęp: "art. 1 ust. 2 lit. a)" (§ 3.2.3; ×1135, ×12)
            "str",                           # strona, in the special edition: "Dz.U. C 75 …, str. 12." (31992L0043)
            "poz",                           # pozycja: "Dz.U. 2020 poz. 1024" (Annex A3)
            "Dz",                            # "Dz. U." spaced (§ 9.5.3); "Dz.U." is v3's
            "ds",                            # do spraw: "Grupę Roboczą ds. Ochrony" (§ 10.3.1; ×80)
            "zob", "Zob",                    # zobacz, see (§ 2.7, § 3.2.1)
            "np", "tj", "tzn", "m.in",       # na przykład, to jest, to znaczy, między innymi (§ 10.3.1)
            "r")),                           # rok, the date's year: "do dnia 9 czerwca 2020 r. Komisja dokonuje"
                                             # (§ 3.1; ×18 of 21 continue, as "(dotychczas nieopublikowane)")
    ("pt", ("cf",)),                         # confira (§ 10.4.12, Annex A3)
    ("ro", ("nr",                            # numărul: "Regulamentul (UE) nr. 127/2010" (§ 3.2.2; ×104)
            "alin",                          # alineatul (Annex A3)
            "cf")),                          # confer (§ 10.1.1)
    ("sk", ("ods",                           # odsek: "článok 1 ods. 2 písm. a)" (§ 3.2.3; ×798)
            "písm",                          # písmeno (§ 3.2.3)
            "č",                             # číslo: "nariadenie (ES) č. 1292/96" (§ 3.2.2; ×104)
            "napr", "tzv", "t. j", "resp")), # napríklad, takzvaný, to jest, respektíve (§ 10.1.4, Annex A3)
    ("sl", ("št",                            # številka: "Uredba (EU) št. 127/2010" (§ 3.2.2; ×104)
            "str",                           # stran: "UL L 32, 6.2.1968, str. 6" (§ 3.1; ×93)
            "npr", "tj", "t. i")),           # na primer, to je, tako imenovani (§ 3.1, § 3.2.3, § 10.1.7)
    ("sv", ("s.k", "dvs", "bl.a")),          # så kallad, det vill säga, bland annat (§ 10.4.2, § 10.4.10)
)
#: forms that are a unit or a word elsewhere, refused as a cut only where a
#: NUMBER follows: "s. 6" is the page in cs, da, fi, pl, sk, sv (§ 3.1; "10 s.
#: The" is ten seconds), "т. 19" a Bulgarian точка (§ 5.9.3; "т" tonnes), "σ. 6"
#: the Greek page (§ 3.1), "vol. 34" a volume in es, fr, it, pt, ro (§ 5.9.4;
#: "% vol." the alcoholic strength ends a sentence), "cap. 6" a Romanian
#: capitol (§ 3.1; es, pt Annex A3; "cap" an English word), "kap." a Danish,
#: Swedish (Annex A3) and Maltese (§ 10.1.1) chapter, "Kap." the Maltese
#: (Annex A3) — "kap" ends a Hungarian sentence ("… t kap. A …"), "Kap" is a
#: German cape
ABBREVIATIONS_V4_BEFORE_NUMBER: tuple[str, ...] = ("s", "т", "σ", "vol", "cap", "kap", "Kap")

_V4_DIGITS = "0123456789"
#: what may stand before an abbreviation of v4: white space or an opening
#: mark, or the start of the text (a negative lookbehind: it holds there too).
#: Written out, so no word-character table is read (v3's `\b` reads Python's)
_V4_LEFT = "(?<![^\\s(\\[«\"„“‘'/])"


def _alternation(forms) -> str:
    """The forms as one alternation, longest first, an inner space any white space."""
    return "|".join(re.escape(f).replace("\\ ", "\\s")
                    for f in sorted(set(forms), key=lambda f: (-len(f), f)))


#: every form of v4 once, sorted (the table above names the languages)
ABBREVIATION_FORMS_V4 = tuple(sorted({f for _lang, forms in ABBREVIATIONS_V4 for f in forms}))
_SENTENCE_END_V4 = re.compile("(?<=[.;:])\\s+(?=[" + CAPITALS_V4 + "0-9«\"„(])")
_ABBREVIATIONS_V4 = re.compile(_V4_LEFT + "(?:" + _alternation(ABBREVIATION_FORMS_V4)
                               + "|[" + NEW_CAPITALS_V4 + "])\\.$")
_ABBREVIATIONS_V4_NUMBER = re.compile(_V4_LEFT + "(?:" + _alternation(ABBREVIATIONS_V4_BEFORE_NUMBER) + ")\\.$")
#: AN ORDINAL INSIDE THE SENTENCE (2026-11-21, measured on 32016R0679 and
#: 32013R0575 from Cellar in de, hr and sl): a number's full stop before a
#: capital ends no sentence where the number is an ordinal that the next
#: word continues. German writes a date's day so: of the 488 such candidates
#: of 32013R0575 de, ~470 are "vom 8. Dezember 1986"; Croatian an article's or
#: paragraph's number before the act it is of: ~240 of 388 are "člankom 81.
#: Uredbe", "stavka 2. Direktive", "članka 114. UFEU-a". Refused before THOSE
#: words only: the rest of Croatian's are sentence starts ("stavka 3.
#: Komisija …"), and Slovenian ends its sentences there ("iz člena 8. V
#: primeru …", 96 of 96). The act nouns in the cases that follow a number
#: (genitive, dative-locative, instrumental), never the nominative a
#: sentence opens with; refusing a cut only joins two sentences, cutting one
#: breaks it
_ORDINAL_V4 = re.compile("(?<![0-9.,])[0-9]{1,4}\\.$")
AFTER_ORDINAL_V4: tuple[str, ...] = (
    # de: the months (and the Austrian forms)
    "Januar", "Jänner", "Februar", "Feber", "März", "April", "Mai", "Juni", "Juli", "August",
    "September", "Oktober", "November", "Dezember",
    # hr: regulation, directive, decision, charter, treaty, annex, protocol, convention
    "Uredbe", "Uredbi", "Uredbom", "Direktive", "Direktivi", "Direktivom", "Odluke", "Odluci", "Odlukom",
    "Povelje", "Povelji", "Poveljom", "Ugovora", "Ugovoru", "Ugovorom", "Priloga", "Prilogu", "Prilogom",
    "Protokola", "Protokolu", "Protokolom", "Konvencije", "Konvenciji", "Konvencijom",
    # hr: the Treaties' acronyms, inflected with a hyphen ("UFEU-a", "UEU-u")
    "UFEU-", "UEU-")
_AFTER_ORDINAL_V4 = re.compile("(?:" + _alternation(AFTER_ORDINAL_V4) + ")(?=[\\s,;:.)\\-]|$|(?<=-))")
#: how far back from a candidate an abbreviation is looked for: past the
#: longest form of either version (tests/test_chunking_legal_v4.py), so the
#: look is bounded and a long run of refused candidates stays linear
_V4_LOOKBACK = 64


#: Nemotron-3-Embed-1B-BF16's tokenizer.json at revision c0c9fea9 (17,077,678
#: bytes; München, 2026-11-08), the encoder of the shared legal space
NEMOTRON_TOKENIZER = "sha256:797410dfb649a5b9ba92bc4fef7dbf4022d00e73de6867c4ac199a8846439421"
#: the space's ctx 8,191 (the model's context_size less one) less "passage: "
#: and a margin of 16 for the join and the special tokens
SHARED_MAX_TOKENS = 8175
#: THE SHARED LEGAL LAYER (docs/notes/legal-chunking-retrieval.md; operator
#: 2026-11-08, pieces 1 and 2 by design): the one definition the three legal
#: plugins declare as their `default_layers` and their recipes state byte for
#: byte (tests/test_chunking_legal.py) — the chunker is inside `space_id`
SHARED_LAYER = {"rule": "default",
                "chunker": {"id": NAME, "version": CHUNK_VERSION,
                            "params": {"tokenizer": NEMOTRON_TOKENIZER, "max_tokens": SHARED_MAX_TOKENS,
                                       "header": True, "outline": True}},
                "input": {"form": "text"}}


class LegalChunkerError(ValueError):
    """The layer's params are not usable."""


def _tokenizer(params: dict) -> Any:
    from app.plugins.wikidata import chunker as wd     # the one loader of a pinned tokenizer
    return wd.tokenizer_for(params.get("tokenizer"))


# THE BOUNDED ENCODE (2026-11-20, the second OOM hold; München's
# tools/measure/parse_peak.py): EUR-Lex5's 32018R0688 el, 128 MB, 54.3 M
# characters, its `chunks` rose 11.18 GB (83x its bytes; ~206 bytes a
# character) — and 32021R2017 el 5.18 GB for 23.6 M characters (~219 a
# character): the peak follows the TEXT, because `split` counted a whole
# section in one encode and `_windows` encoded a whole over-long sentence and
# listed every offset. Now no encode reads more than SPAN_CHARS: a text is
# read in spans cut at a plain space whose neighbours are not white space,
# and the spans' counts summed, their offsets shifted — the SAME numbers, by
# construction, for a tokenizer whose pre-tokenization is local there
# (`spans_are_exact`): no normalizer, its split a regex with no lookbehind
# that no match crosses at such a space (the pinned Nemotron-3-Embed-1B
# tokenizer: every alternative but `\s+(?!\S)` stops before a space, and
# that one cannot end at a space followed by a non-space), then ByteLevel
# without its own regex, and no added token holding a space; or a plain
# whitespace split. Any other tokenizer is encoded whole, as before. No
# chunker version moves: the pieces are byte for byte the pieces
# (invariant 3; tests/test_chunking_legal_spans.py against the pinned
# tokenizer, tools/measure/legal_chunk_identity.py on the node's documents).
# Standardised alternative, not taken: the tokenizers library's own batch
# or streaming encode has no bounded-memory count; a tokenizer-agnostic
# bound would need it.
SPAN_CHARS = 200_000
_SAFE_CUT = re.compile(r"(?<=\S) (?=\S)")
#: the pre-tokenizers whose pre-tokens never cross a space between two
#: non-spaces, and so whose encoding is the spans' (`spans_are_exact`)
_EXACT_SPLIT = ("[^\\r\\n\\p{L}\\p{N}]?[\\p{Lu}\\p{Lt}\\p{Lm}\\p{Lo}\\p{M}]*[\\p{Ll}\\p{Lm}\\p{Lo}\\p{M}]+|"
                "[^\\r\\n\\p{L}\\p{N}]?[\\p{Lu}\\p{Lt}\\p{Lm}\\p{Lo}\\p{M}]+[\\p{Ll}\\p{Lm}\\p{Lo}\\p{M}]*|"
                "\\p{N}| ?[^\\s\\p{L}\\p{N}]+[\\r\\n/]*|\\s*[\\r\\n]+|\\s+(?!\\S)|\\s+")
#: `id(tok)` -> (tok, verdict). The entry HOLDS the tokenizer it judged: an
#: id is reused once its object is collected, and a verdict keyed on the id
#: alone was inherited by the next tokenizer at that address — an exact
#: tokenizer's, for one that is not, cutting its texts in spans (other chunks,
#: invariant 3; München, 2026-11-23: test_chunking_legal_spans.py 2 failed in
#: 6 staged runs). Held, the object cannot die while its verdict stands, and
#: the `is` check is belt and braces. A `tokenizers.Tokenizer` takes no weak
#: reference, so the cache is bounded instead (the node holds a few pinned
#: tokenizers, alive in their registry for the process anyway: plugins/
#: wikidata/chunker.py `_tokenizers`); an evicted entry is gone with its id.
_exact: OrderedDict[int, tuple[Any, bool]] = OrderedDict()
_EXACT_HELD = 32
_exact_lock = threading.Lock()


def spans_are_exact(tok: Any) -> bool:
    """Is `tok`'s encoding of a text the concatenation of its encodings of
    the text cut at a space between two non-spaces (`_SAFE_CUT`)? Read off
    the tokenizer's own declaration (`to_str`), once per tokenizer."""
    key = id(tok)
    with _exact_lock:
        hit = _exact.get(key)
    if hit is not None and hit[0] is tok:
        return hit[1]
    got = _declared_exact(tok)
    with _exact_lock:
        _exact[key] = (tok, got)
        _exact.move_to_end(key)
        while len(_exact) > _EXACT_HELD:
            _exact.popitem(last=False)
    return got


def _declared_exact(tok: Any) -> bool:
    import json
    try:
        d = json.loads(tok.to_str())
    except Exception:   # noqa: BLE001 — a tokenizer that does not say: encoded whole
        return False
    if d.get("normalizer") is not None:
        return False
    if any(" " in (a.get("content") or "") for a in d.get("added_tokens") or []):
        return False
    pre = d.get("pre_tokenizer") or {}
    if pre.get("type") == "WhitespaceSplit":
        return True
    steps = pre.get("pretokenizers") if pre.get("type") == "Sequence" else None
    if not steps or len(steps) != 2:
        return False
    split, byte = steps
    return (split.get("type") == "Split" and (split.get("pattern") or {}).get("Regex") == _EXACT_SPLIT
            and split.get("behavior") == "Isolated" and not split.get("invert")
            and byte.get("type") == "ByteLevel" and not byte.get("use_regex") and not byte.get("add_prefix_space"))


def _spans(tok: Any, text: str) -> list[tuple[int, int]]:
    """(start, end) of the spans `text` is encoded in: itself where it is
    short or the tokenizer is not span-exact; else cut at the first safe
    space at or past every SPAN_CHARS (a run with no safe space is one span)."""
    if len(text) <= SPAN_CHARS or not spans_are_exact(tok):
        return [(0, len(text))]
    out: list[tuple[int, int]] = []
    start = 0
    while len(text) - start > SPAN_CHARS:
        m = _SAFE_CUT.search(text, start + SPAN_CHARS)
        if m is None:
            break
        out.append((start, m.start()))
        start = m.start()
    out.append((start, len(text)))
    return out


#: characters given to one `encode_batch` (2026-11-25, architecture's
#: reading of EUR-Lex5.next's fts: 32018R0688 in 23 languages, 90-136 MB
#: each, 75-90 s apiece on ONE child while ten idled, the chunker encoding
#: span after span on one thread). The spans of a text, and the units a
#: piece packs, are independent texts: the tokenizers library's own
#: `encode_batch` encodes them on its own threads (Rust, rayon) and gives
#: each the encoding `encode` gives it, so the counts, offsets and pieces
#: are the same (no chunker version moves; tests/test_chunking_legal_spans.py).
#: Bounded, so a batch holds a few MB of encodings, never a whole act's.
BATCH_CHARS = 4_000_000


def _batched(tok: Any) -> bool:
    """May `tok` encode many texts in one call with each one's own
    encoding? A tokenizer that pads (`padding`) pads a batch to its longest."""
    return callable(getattr(tok, "encode_batch", None)) and getattr(tok, "padding", None) is None


def _encode_many(tok: Any, texts):
    """`tok.encode(t, add_special_tokens=False)` for each text, in order:
    in batches of up to BATCH_CHARS where the tokenizer batches."""
    if not _batched(tok):
        for t in texts:
            yield tok.encode(t, add_special_tokens=False)
        return
    group: list[str] = []
    size = 0
    for t in texts:
        group.append(t)
        size += len(t)
        if size >= BATCH_CHARS:
            yield from tok.encode_batch(group, add_special_tokens=False)
            group, size = [], 0
    if group:
        yield from tok.encode_batch(group, add_special_tokens=False)


def _count(tok: Any, text: str) -> int:
    # the spans are CUT AS THEY ARE ENCODED (a generator): a list of them is
    # a second copy of the text, alive for the whole count — 136 MB of an
    # act in a child whose peak the large lane's room is reckoned on
    return sum(len(e.ids) for e in _encode_many(tok, (text[a:b] for a, b in _spans(tok, text))))


def _count_many(tok: Any, texts: Sequence[str]) -> list[int]:
    """`_count` of each text, their spans encoded together — cut as they
    are encoded, a batch of them alive at a time."""
    owner: list[int] = []                # the text each span sent so far belongs to

    def parts():
        for i, t in enumerate(texts):
            for a, b in _spans(tok, t):
                owner.append(i)
                yield t[a:b]
    out = [0] * len(texts)
    for k, e in enumerate(_encode_many(tok, parts())):
        out[owner[k]] += len(e.ids)
    return out


def _token_ends(tok: Any, text: str):
    """Each token's end offset in `text`, in order, a span at a time."""
    spans = _spans(tok, text)
    for (a, _b), enc in zip(spans, _encode_many(tok, (text[a:b] for a, b in spans))):
        for _s, e in enc.offsets:
            yield a + e


def _lines_split(text: str, mark: re.Pattern) -> list[str]:
    """`text` cut before every line that starts with `mark`; the first unit
    is whatever precedes the first mark (an article's heading, an intro)."""
    units: list[str] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if cur and mark.match(line.lstrip()):
            units.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        units.append("\n".join(cur))
    return [u for u in units if u.strip()]


def _sentences(text: str, version: int = 3) -> list[str]:
    """`text` cut at its sentence ends: v3's class and abbreviations up to
    version 3 (exactly as always), the 24 languages' from 4 (`_sentences_v4`)."""
    if int(version) >= 4:
        return _sentences_v4(text)
    out: list[str] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        if _ABBREVIATIONS.search(text[start:m.start()]):
            continue
        out.append(text[start:m.start()])
        start = m.end()
    out.append(text[start:])
    return [s for s in out if s.strip()]


def after_abbreviation_v4(text: str, start: int, m: re.Match) -> bool:
    """Is the candidate `m` of `_SENTENCE_END_V4` (its white space) refused
    as a cut, the current sentence having begun at `start`? Where v3 would
    refuse it — `_ABBREVIATIONS` over `text[start:m.start()]`, read here with
    `pos`/`endpos` instead of a slice: the same match, since the pattern
    sees the character before `pos` (white space at `start`, which is no word
    character, as a slice's start is none) and every match ends at `endpos`
    within `_V4_LOOKBACK` — or after a form of v4's, or, before a number,
    after one of its short forms, or after an ordinal its next word
    continues (`_AFTER_ORDINAL_V4`)."""
    end = m.start()
    lo = max(start, end - _V4_LOOKBACK)
    if _ABBREVIATIONS.search(text, lo, end) or _ABBREVIATIONS_V4.search(text, lo, end):
        return True
    if _ORDINAL_V4.search(text, lo, end) and _AFTER_ORDINAL_V4.match(text, m.end()):
        return True                    # "vom 18. Dezember", "člankom 81. Uredbe"
    return (m.end() < len(text) and text[m.end()] in _V4_DIGITS
            and _ABBREVIATIONS_V4_NUMBER.search(text, lo, end) is not None)


def _sentences_v4(text: str) -> list[str]:
    """v4's sentences: v3's cut, and a cut before a capital of CAPITALS_V4,
    refused after an abbreviation of either version (`after_abbreviation_v4`).
    Each candidate decided by the text around it alone, so the cuts are
    v3's wherever the text holds no new capital after a candidate and no new
    abbreviation before one (tests/test_chunking_legal_v4.py)."""
    out: list[str] = []
    start = 0
    for m in _SENTENCE_END_V4.finditer(text):
        if after_abbreviation_v4(text, start, m):
            continue
        out.append(text[start:m.start()])
        start = m.end()
    out.append(text[start:])
    return [s for s in out if s.strip()]


def _windows(tok: Any, text: str, budget: int) -> list[str]:
    """Consecutive windows of `budget` tokens, cut at token boundaries, the
    whole text kept: the last resort for one sentence past the window.
    The text is encoded a span at a time: a window is cut after its
    `budget`-th token where another token follows, the last one runs to the
    text's end — as when every offset was listed first."""
    out: list[str] = []
    begin = 0
    n = 0
    # ONLY THE CUTS ARE READ (2026-11-25, architecture's reading of München's
    # profile of 32018R0688 fr: 13,717,825 Python steps over every token's
    # end, 16.9 s of 72): the tokens whose end a window is cut at are the
    # budget-th, 2·budget-th, … counted across the spans, so each span's
    # offsets are indexed at those positions, n/budget reads, not n
    cuts: list[int] = []
    spans = _spans(tok, text)
    for (a, _b), enc in zip(spans, _encode_many(tok, (text[a:b] for a, b in spans))):
        offsets = enc.offsets
        cuts.extend(a + offsets[i][1] for i in range((budget - 1 - n) % budget, len(offsets), budget))
        n += len(offsets)
    if cuts and n % budget == 0:
        cuts.pop()                      # the last token itself: no token follows it, no cut
    for c in cuts:
        out.append(text[begin:c])
        begin = c
    if n:
        out.append(text[begin:])
        begin = len(text)
    if begin < len(text) and text[begin:].strip():
        out[-1:] = [out[-1] + text[begin:]] if out else [text[begin:]]
    return [w for w in out if w.strip()] or [text]


def _windows_listed(tok: Any, text: str, budget: int) -> list[str]:
    """`_windows` as it was until 2026-11-20 — every offset listed from one
    encode of the whole text; kept as the reference the bounded one is held
    to (tests/test_chunking_legal_spans.py)."""
    enc = tok.encode(text, add_special_tokens=False)
    offsets = list(enc.offsets)
    out: list[str] = []
    i = 0
    begin = 0
    while i < len(offsets):
        j = min(i + budget, len(offsets))
        end = offsets[j - 1][1] if j < len(offsets) else len(text)
        out.append(text[begin:end])
        begin = end
        i = j
    if begin < len(text) and text[begin:].strip():
        out[-1:] = [out[-1] + text[begin:]] if out else [text[begin:]]
    return [w for w in out if w.strip()] or [text]


def _pack_linear(tok: Any, units: Sequence[str], budget: int, sep: str) -> list[tuple[str, int]]:
    """v3: `(piece, its exact token count)`, consecutive units joined while
    the sum of their counts (and the separators') fits; each piece of several
    units then held to the bound by its own exact count, a unit given back
    while it is over. One count per unit and about one per piece: linear in
    the text. A single unit past the budget is the caller's."""
    counts = _count_many(tok, units)
    sep_n = _count(tok, sep) if sep else 0
    out: list[tuple[str, int]] = []
    i, n = 0, len(units)
    while i < n:
        j, est = i + 1, counts[i]
        while j < n and est + sep_n + counts[j] <= budget:
            est += sep_n + counts[j]
            j += 1
        exact = counts[i] if j == i + 1 else _count(tok, sep.join(units[i:j]))
        while j - i > 1 and exact > budget:
            j -= 1
            exact = counts[i] if j == i + 1 else _count(tok, sep.join(units[i:j]))
        out.append((sep.join(units[i:j]), exact))
        i = j
    return out


def _pack(tok: Any, units: Sequence[str], budget: int, sep: str) -> list[str]:
    """Consecutive units joined while they fit: no piece shorter than it
    need be, none longer than the budget (a unit past it is the caller's)."""
    pieces: list[str] = []
    buf = ""
    for u in units:
        cand = f"{buf}{sep}{u}" if buf else u
        if buf and _count(tok, cand) > budget:
            pieces.append(buf)
            buf = u
        else:
            buf = cand
    if buf:
        pieces.append(buf)
    return pieces


def split(tok: Any, text: str, budget: int, level: int = 0, *, version: int = CHUNK_VERSION,
          count: int | None = None) -> list[str]:
    """`text` as pieces of at most `budget` tokens, cut at the highest level
    of its structure that makes them fit; joined, the pieces are the text.
    `version` 3 packs linearly (`_pack_linear`), 1 and 2 as they always did;
    4 packs as 3 and cuts its sentences in the 24 languages (`_sentences_v4`);
    `count`: the text's token count where the caller has it already."""
    count = count if count is not None else _count(tok, text)
    if count <= budget:
        return [text]
    if level == 0:
        units, sep = _lines_split(text, PARAGRAPH), "\n"
    elif level == 1:
        units, sep = _lines_split(text, POINT_V4 if int(version) >= 4 else POINT), "\n"
    elif level == 2:
        units, sep = _sentences(text, version), " "
    else:
        return _windows(tok, text, budget)
    if len(units) <= 1:
        return split(tok, text, budget, level + 1, version=version, count=count)
    out: list[str] = []
    if int(version) >= 3:
        for piece, n in _pack_linear(tok, units, budget, sep):
            out.extend(split(tok, piece, budget, level + 1, version=version, count=n) if n > budget
                       else [piece])
        return out
    for piece in _pack(tok, units, budget, sep):
        out.extend(split(tok, piece, budget, level + 1, version=version) if _count(tok, piece) > budget
                   else [piece])
    return out


OUTLINE = "outline"


def check(params: Any) -> tuple[int, bool, bool]:
    if not isinstance(params, dict):
        raise LegalChunkerError("params is a table")
    mt = params.get("max_tokens")
    if not isinstance(mt, int) or isinstance(mt, bool) or mt < 16:
        raise LegalChunkerError(f"max_tokens is an integer of at least 16, got {mt!r}")
    flags = []
    for k in ("header", "outline"):
        v = params.get(k, False)
        if not isinstance(v, bool):
            raise LegalChunkerError(f"{k} is true or false, got {v!r}")
        flags.append(v)
    unknown = sorted(set(params) - {"tokenizer", "max_tokens", "header", "outline"})
    if unknown:
        raise LegalChunkerError(f"unknown params {unknown}: they would enter space_id and cut nothing")
    return mt, flags[0], flags[1]


def header_of(title: str, label: str) -> str:
    """"<title> › <label>", either alone where the other is empty."""
    return " › ".join(x for x in ((title or "").strip(), (label or "").strip()) if x)


def body_after_head(label: str, text: str) -> str:
    """v2: the body without a first line that only repeats the label's last
    segment, which the header already says. A body that IS that line alone
    is kept whole: a chunk is never the header with nothing under it."""
    last = (label or "").split(" › ")[-1].strip()
    first, sep, rest = text.partition("\n")
    if last and sep and first.strip() == last and rest.strip():
        return rest.strip("\n")
    return text


def toc(labels: Sequence[str]) -> str:
    """v2's outline: the labels as a table of contents — a partition
    written once where it begins, each level below it indented two spaces."""
    out: list[str] = []
    prev: list[str] = []
    for label in labels:
        path = [x.strip() for x in label.split(" › ") if x.strip()]
        same = 0
        while same < min(len(prev), len(path) - 1) and prev[same] == path[same]:
            same += 1
        for depth in range(same, len(path)):
            out.append("  " * depth + path[depth])
        prev = path
    return "\n".join(out)


def _budget(tok: Any, head: str, max_tokens: int, uid: str) -> int:
    budget = max_tokens - (_count(tok, head) if head else 0)
    if budget < 32:
        raise LegalChunkerError(f"{uid}: the header {head.strip()!r} leaves {budget} tokens of {max_tokens}")
    return budget


def chunk_legal_article(uid: str, native_id, sections: Sequence[tuple[str, str]],
                        version=None, params=None, *, title: str | None = None) -> Iterator[dict]:
    from app.chunking import _mk
    max_tokens, header, outline = check(params or {})
    if (header or outline) and title is None:
        raise LegalChunkerError(f"{uid}: this layer writes the record's title into its chunks "
                                f"(header/outline) and the caller passed none")
    tok = _tokenizer(params or {})
    version = CHUNK_VERSION if version is None else version
    if version not in VERSIONS:
        raise LegalChunkerError(f"{uid}: legal_article has versions {list(VERSIONS)}, not {version!r}")
    seq = 0
    for label, body in sections:
        text = (body or "").strip()
        if not text:
            continue
        head = header_of(title or "", label) if header else ""
        if head and int(version) >= 2:
            text = body_after_head(label, text)
        head = f"{head}\n" if head else ""
        for piece in split(tok, text, _budget(tok, head, max_tokens, uid), version=int(version)):
            yield _mk(uid, native_id, seq, label or "", head + piece, version)
            seq += 1
    labels = list(dict.fromkeys(l.strip() for l, _b in sections if (l or "").strip()))
    if outline and len(labels) >= 2:
        head = f"{title.strip()}\n" if (title or "").strip() else ""
        index = toc(labels) if int(version) >= 2 else "\n".join(labels)
        for piece in split(tok, index, _budget(tok, head, max_tokens, uid), version=int(version)):
            yield _mk(uid, native_id, seq, OUTLINE, head + piece, version)
            seq += 1
