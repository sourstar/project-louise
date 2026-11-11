"""Numbers and dates written out in the text's language before it is read
aloud (operator, 2026-11-08, choice A: "numbers and dates are pronounced in
Italian anyway" — one voice for every language, recorded in Italian, and the
model reads digits as that voice learned them).

`num2words` (LGPL-2.1, pure Python) writes the words: cardinals, ordinals and
English years. What it does not know is decided here, per language, from what
the text itself writes:

- a number's separators: "1.234" is a thousand in Italian and a decimal in
  English; a group separator is taken only before exactly three digits, so
  "3.5" in an Italian answer (a model writing English figures) is three
  point five, never thirty-five;
- a date: day, month and year as the language writes them ("12/03/2024" and
  ISO "2024-03-12"; "12 marzo 2024", "March 12, 2024", "12. März 2024"), its
  day an ordinal where the language says it so (English, German, "primo",
  "premier", "1º");
- a year: four digits from 1100 to 2099 standing alone, in English and
  German as they say years ("nineteen fourteen", "neunzehnhundertvierzehn",
  num2words' year form), elsewhere the cardinal the language uses;
- a time, "14:30": the hour and the minutes as the language joins them;
- an ordinal ("1º", "2ª", "1st", "1er", "2e"), a percentage, an era ("58
  a.C.", "44 BC", "av. J.-C.", "v. Chr.");
- a run of more than six digits with no separator is an identifier (a PMID,
  a phone number), read digit by digit.

A text in no language `speech.language` knows is left as it is: guessing a
language reads it wrong in a new way. Fractions are left as written.

ROMAN NUMERALS (operator, 2026-11-12: "dates in roman format are
mispronounced") are read as the language reads them, where the text shows
what they count (`_roman`):

- a century: "XIV secolo", "sec. XIV", "siglo XIV", "XIXe siècle", "XIV
  century": Italian, French, German and English say the ordinal
  ("quattordicesimo secolo", "dix-neuvième siècle"); Spanish and Portuguese
  say the ordinal up to the tenth and the cardinal after it ("siglo
  catorce");
- a ruler or a pope after a name ("Luigi XIV", "Pio IX", "Elisabetta II",
  "Louis XIV", "Felipe II", "Henry VIII", "Ludwig XIV."): the ordinal, "the
  fourteenth" in English, "der Vierzehnte" in German, the cardinal in French
  ("Louis quatorze", "premier" for one), Spanish and Portuguese past the
  tenth; feminine in Italian, Spanish and Portuguese after a name or noun
  ending in -a ("Elisabetta seconda"). In English, a numbered part ("World
  War II", "Chapter IV", "Vatican II") is the cardinal;
- an article before it and a word after ("la II guerra mondiale"): the
  ordinal, in the article's gender;
- the French ordinal suffix ("Ier", "IIe", "XIXᵉ");
- four letters or more worth 1000 or more ("MCMXC"): a year.

Everything else stays as written. A single I, V or X counts only after a
name, and never "I" in English (the pronoun). Neither does a word of
capitals ("DI", "MIX" in a title written in capitals), nor an abbreviation
that happens to be one ("DC", "CD", "MD", "CV", "XL").

THE STANDARD ALTERNATIVE (CLAUDE.md, "Do not reinvent the wheel"): text
normalisation as a weighted finite-state grammar — NVIDIA NeMo's
text_processing (Sparrowhawk's model, the one Google's TTS uses) covers
dates, times, measures and money in a dozen languages. It pays once the
voice reads tables, measures and money often; it needs pynini (OpenFst), a
compiled dependency far heavier than this.
"""
from __future__ import annotations

import re

from num2words import num2words

#: what each language writes and says around a number
_LANG: dict[str, dict] = {
    "it": {"dec": ",", "group": ".", "point": "virgola", "pct": "per cento",
           "months": "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre",
           "eras": {"a.C.": "avanti Cristo", "a. C.": "avanti Cristo", "d.C.": "dopo Cristo", "d. C.": "dopo Cristo"},
           "first_day": True, "ordinal_day": False, "fem": "a", "time": "{h} e {m}", "hour": "{h}"},
    "en": {"dec": ".", "group": ",", "point": "point", "pct": "percent",
           "months": "January February March April May June July August September October November December",
           "eras": {"BC": "BC", "AD": "AD", "BCE": "BCE", "CE": "CE"},
           "first_day": False, "ordinal_day": True, "fem": None, "time": "{h} {m}", "hour": "{h} o'clock"},
    "fr": {"dec": ",", "group": ".   ", "point": "virgule", "pct": "pour cent",
           "months": "janvier février mars avril mai juin juillet août septembre octobre novembre décembre",
           "eras": {"av. J.-C.": "avant Jésus-Christ", "apr. J.-C.": "après Jésus-Christ"},
           "first_day": True, "ordinal_day": False, "fem": None, "time": "{h} heures {m}",
           "hour": "{h} heures"},
    "de": {"dec": ",", "group": ".", "point": "Komma", "pct": "Prozent",
           "months": "Januar Februar März April Mai Juni Juli August September Oktober November Dezember",
           "eras": {"v. Chr.": "vor Christus", "n. Chr.": "nach Christus"},
           "first_day": False, "ordinal_day": True, "fem": None, "time": "{h} Uhr {m}", "hour": "{h} Uhr"},
    "es": {"dec": ",", "group": ".", "point": "coma", "pct": "por ciento",
           "months": "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre",
           "eras": {"a. C.": "antes de Cristo", "a.C.": "antes de Cristo", "d. C.": "después de Cristo",
                    "d.C.": "después de Cristo"},
           "first_day": False, "ordinal_day": False, "fem": "a", "time": "{h} y {m}", "hour": "{h}"},
    "pt": {"dec": ",", "group": ".", "point": "vírgula", "pct": "por cento",
           "months": "janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro",
           "eras": {"a.C.": "antes de Cristo", "d.C.": "depois de Cristo"},
           "first_day": True, "ordinal_day": False, "fem": "a", "time": "{h} e {m}", "hour": "{h}"},
}
for _spec in _LANG.values():
    _spec["month_list"] = _spec["months"].split()

#: more digits than this with no separator: an identifier, read digit by digit
ID_DIGITS = 6


def _words(n: int, lang: str, to: str = "cardinal") -> str:
    """num2words' words, without the commas English puts in a cardinal (a
    comma is a pause to the voice)."""
    try:
        return num2words(n, lang=lang, to=to).replace(",", "")
    except (NotImplementedError, OverflowError, ValueError):
        return num2words(n, lang=lang).replace(",", "")


def _ordinal(n: int, lang: str, feminine: bool = False) -> str:
    w = _words(n, lang, "ordinal")
    fem = _LANG[lang]["fem"]
    if feminine and fem and w.endswith("o"):
        w = w[:-1] + fem
    return w


def _year(n: int, lang: str) -> str:
    return _words(n, lang, "year") if lang in ("en", "de") else _words(n, lang)


def _day(d: int, lang: str) -> str:
    spec = _LANG[lang]
    if spec["ordinal_day"] or (d == 1 and spec["first_day"]):
        return _ordinal(d, lang)
    return _words(d, lang)


def _date(d: int, m: int, y: int | None, lang: str) -> str | None:
    if not (1 <= d <= 31 and 1 <= m <= 12):
        return None
    spec = _LANG[lang]
    month = spec["month_list"][m - 1]
    tail = f" {_year(y, lang)}" if y is not None else ""
    if lang == "en":
        return f"{month} {_day(d, lang)}{tail}"
    if lang in ("es", "pt"):
        return f"{_day(d, lang)} de {month}" + (f" de {_year(y, lang)}" if y is not None else "")
    return f"{_day(d, lang)} {month}{tail}"


def _number(raw: str, lang: str) -> str:
    """One written number, its separators read as the language writes them."""
    spec = _LANG[lang]
    groups = "".join(re.escape(c) for c in spec["group"])
    dec = re.escape(spec["dec"])
    other_dec = "." if spec["dec"] == "," else ","
    m = re.fullmatch(rf"(\d{{1,3}}(?:[{groups}]\d{{3}})+)(?:{dec}(\d+))?", raw)
    if m:                                       # 1.234 / 1.234,5 (it), 1,234.5 (en)
        whole, frac = int(re.sub(r"\D", "", m.group(1))), m.group(2)
    else:
        m = re.fullmatch(rf"(\d+)(?:(?:{dec}|{re.escape(other_dec)})(\d+))?", raw)
        if not m:
            return raw
        if m.group(2) is None and len(m.group(1)) > ID_DIGITS:
            return " ".join(_words(int(c), lang) for c in m.group(1))
        whole, frac = int(m.group(1)), m.group(2)
    out = _words(whole, lang)
    if frac is not None:
        tail = _words(int(frac), lang) if not frac.startswith("0") else \
            " ".join(_words(int(c), lang) for c in frac)
        out += f" {spec['point']} {tail}"
    return out


_ROMAN = r"(?=[MDCLXVI])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_ROMAN_VALUES = {"M": 1000, "D": 500, "C": 100, "L": 50, "X": 10, "V": 5, "I": 1}
#: abbreviations that are valid numerals
_NOT_ROMAN = frozenset("DC CD MD CV MC CM CC DL MM MI DI LI CI XL MIX DIV CIV LIV MIL".split())
_CENTURY = {"it": r"secol[oi]|sec\.", "fr": r"siècles?", "es": r"siglos?", "pt": r"séculos?",
            "en": r"century|centuries", "de": r"Jahrhunderts?"}
#: words before which a number counts a part, said as a cardinal in English
_EN_PARTS = frozenset("War Part Chapter Volume Book Act Phase Type Class Grade Level Article Title Section "
                      "Appendix Vatican Stage Series Step Annex Schedule Canto Psalm Round".split())
#: the century's plural, after which one letter is an article ("I secoli")
_PLURAL = {"it": "secoli", "fr": "siècles", "es": "siglos", "pt": "séculos", "en": "centuries",
           "de": "jahrhunderte"}
#: the German articles after which an ordinal takes -n
_DE_OBLIQUE = frozenset("im am vom zum beim dem den des einem eines".split())
#: feminine nouns that do not end in -a
_FEMININE = frozenset("parte classe fase sezione appendice serie legge edizione".split())
_ARTICLES = {
    "it": ({"il", "lo", "del", "nel", "al", "dal", "sul", "dello", "nello", "allo", "dallo", "sullo"},
           {"la", "della", "nella", "alla", "dalla", "sulla"}),
    "es": ({"el", "del", "al"}, {"la"}),
    "pt": ({"o", "do", "no", "ao"}, {"a", "da", "na"}),
}


def roman(s: str) -> int | None:
    """A Roman numeral's value (I–MMMCMXCIX), None for anything else."""
    if not s or not re.fullmatch(_ROMAN, s):
        return None
    vals = [_ROMAN_VALUES[c] for c in s]
    return sum(-v if i + 1 < len(vals) and v < vals[i + 1] else v for i, v in enumerate(vals))


def _counted(n: int, lang: str, feminine: bool = False, regnal: bool = False) -> str:
    """A number that counts a century or a ruler, as `lang` says it."""
    if lang in ("es", "pt") and n > 10:
        return _words(n, lang)
    if lang == "fr" and regnal:
        return "premier" if n == 1 else _words(n, lang)
    if lang == "en" and regnal:
        return f"the {_ordinal(n, lang)}"
    if lang == "de" and regnal:
        return f"der {_ordinal(n, lang)}"
    return _ordinal(n, lang, feminine)


def _roman(text: str, lang: str) -> str:
    """`text` with its Roman numerals in words where it shows what they
    count (the module's docstring); every other one as written."""
    if not re.search(r"[IVXLCDM]", text):
        return text
    tok = rf"(?<![\w.])({_ROMAN})"
    end = r"(?![\w])"

    def value(r: str) -> int | None:
        """A numeral's value, None for an abbreviation or a lone L, C, D, M
        (French "Le", "Ce", "De" with the ordinal's "e")."""
        return None if r in _NOT_ROMAN or (len(r) == 1 and r not in "IVX") else roman(r)

    # a century, before or after the century's word; a range "XIV-XV secolo"
    cent = _CENTURY.get(lang)
    if cent:
        mark = r"(?:\.|º|°|e|ème|ᵉ)?"

        def before(m: re.Match) -> str:
            a = value(m.group(2))
            b = value(m.group(3)) if m.group(3) else None
            if a is None or (m.group(3) and b is None):
                return m.group(0)
            # one letter before the plural is an article: "I secoli bui"
            if len(m.group(2)) == 1 and b is None and _PLURAL.get(lang, "") == m.group(4).lower():
                return m.group(0)
            # German declines it after a dative or genitive article: "im siebzehnten Jahrhundert"
            n = "n" if lang == "de" and (m.group(1) or "").strip().lower() in _DE_OBLIQUE else ""
            said = _counted(a, lang) + n + (f"-{_counted(b, lang)}{n}" if b is not None else "")
            return f"{m.group(1) or ''}{said} {m.group(4)}"
        text = re.sub(rf"((?<!\w)\w+\s+)?{tok}{mark}(?:\s*[-–]\s*({_ROMAN}){mark})?\s+({cent})(?!\w)",
                      before, text)

        def after(m: re.Match) -> str:
            n = value(m.group(2))
            return m.group(0) if n is None else f"{m.group(1)} {_counted(n, lang)}"
        text = re.sub(rf"(?<!\w)({cent})\s+{tok}{end}", after, text)

        # its era: "il I secolo a.C." → "il primo secolo avanti Cristo"; a stop that also ends the sentence stays
        for abbr, said in _LANG[lang]["eras"].items():
            def era(m: re.Match, said: str = said, abbr: str = abbr) -> str:
                rest = m.string[m.end():].lstrip()
                ends = abbr.endswith(".") and (not rest or rest[0].isupper())
                return f"{m.group(1)} {said}" + ("." if ends else "")
            text = re.sub(rf"(?<!\w)((?:{cent})(?:\s+[^\W\d_]+)?)\s+{re.escape(abbr)}(?!\w)", era, text)

    # the French ordinal suffix: Ier, Ire, IIe, XIXe, XIXᵉ
    if lang == "fr":
        def suffix(m: re.Match) -> str:
            n = value(m.group(1))
            if n is None:
                return m.group(0)
            if n == 1:
                return "première" if m.group(2) == "re" else "premier"
            if len(m.group(1)) == 1 and m.group(2) == "e":
                return m.group(0)       # "Ve", "Xe" alone: a century says it (above), else as written
            return _ordinal(n, lang)
        text = re.sub(rf"{tok}(er|re|ème|e|ᵉ)(?!\w)", suffix, text)

    # an article before it and a word after: "la II guerra mondiale"
    arts = _ARTICLES.get(lang)
    if arts:
        masc, fem = arts
        words = "|".join(sorted(masc | fem, key=len, reverse=True))

        def article(m: re.Match) -> str:
            n = value(m.group(2))
            if n is None:
                return m.group(0)
            return f"{m.group(1)} {_counted(n, lang, feminine=m.group(1).lower() in fem)}"
        text = re.sub(rf"(?i:\b({words}))\s+{tok}(?=\s+[^\W\d_])", article, text)

    # a ruler, a pope, or (English) a numbered part, after a name in title case
    def ruler(m: re.Match) -> str:
        name, r, stop = m.group(1), m.group(2), m.group(3) or ""
        n = value(r)
        if n is None or (lang == "en" and r == "I"):
            return m.group(0)
        if lang == "en" and name in _EN_PARTS:
            return f"{name} {_words(n, lang)}{stop}"
        fem = lang in ("it", "es", "pt") and (name.endswith("a") or name.lower() in _FEMININE)
        # German writes the ruler's stop ("Ludwig XIV."): it is the ordinal's, not the sentence's
        keep = stop if lang != "de" else ""
        return f"{name} {_counted(n, lang, feminine=fem, regnal=True)}{keep}"
    text = re.sub(rf"(?<![\w])([A-ZÀ-ÝĀ-Ž][a-zà-ÿā-ž'’-]+)\s+{tok}(\.)?{end}", ruler, text)

    # a year in numerals: four letters or more, worth a thousand or more
    return re.sub(rf"{tok}{end}", lambda m: _year(value(m.group(1)), lang)
                  if len(m.group(1)) >= 4 and (value(m.group(1)) or 0) >= 1000 else m.group(0), text)


def verbalize(text: str, lang: str | None) -> str:
    """`text` with its numbers, dates and Roman numerals in words of `lang`;
    as it is for a language this module does not know (None included)."""
    if not lang or lang not in _LANG:
        return text
    text = _roman(text, lang)
    if not any(c.isdigit() for c in text):
        return text
    spec = _LANG[lang]
    months = "|".join(spec["month_list"])

    # dates first, while their digits are whole: ISO, then day/month/year
    def iso(m: re.Match) -> str:
        return _date(int(m.group(3)), int(m.group(2)), int(m.group(1)), lang) or m.group(0)
    text = re.sub(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", iso, text)

    def dmy(m: re.Match) -> str:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        d, mo = (b, a) if lang == "en" else (a, b)     # en: month first
        return _date(d, mo, y, lang) or m.group(0)
    text = re.sub(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", dmy, text)

    # a day before or after a month's name: "12 marzo", "March 12", "12. März", "1er mars"
    def day_month(m: re.Match) -> str:
        if lang == "en":            # "11 November" is said "the eleventh of November"
            return f"the {_ordinal(int(m.group(1)), lang)} of {m.group(2)}"
        return f"{_day(int(m.group(1)), lang)} {m.group(2)}"
    text = re.sub(rf"\b([12]?\d|3[01])(?:\.|°|º|er|st|nd|rd|th)?\s+({months})\b", day_month, text,
                  flags=re.IGNORECASE)
    if lang == "en":
        text = re.sub(rf"\b({months})\s+([12]?\d|3[01])(?:st|nd|rd|th)?\b",
                      lambda m: f"{m.group(1)} {_ordinal(int(m.group(2)), lang)}", text)

    # an era after a number: "58 a.C." → "58 avanti Cristo"; where its stop
    # also ended the sentence (a capital next, or the end), the stop stays
    for abbr, said in spec["eras"].items():
        def era(m: re.Match, said: str = said) -> str:
            rest = m.string[m.end():].lstrip()
            ends = abbr.endswith(".") and (not rest or rest[0].isupper())
            return f" {said}" + ("." if ends else "")
        text = re.sub(rf"(?<=\d)\s*{re.escape(abbr)}(?!\w)", era, text)

    # a time: "14:30" → the hour and the minutes, as the language joins them
    def clock(m: re.Match) -> str:
        h, mi = int(m.group(1)), int(m.group(2))
        if mi == 0:
            return spec["hour"].format(h=_words(h, lang))
        minutes = _words(mi, lang) if not (lang == "en" and mi < 10) else f"oh {_words(mi, lang)}"
        return spec["time"].format(h=_words(h, lang), m=minutes)
    text = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", clock, text)

    # ordinals: 1º 2ª 1° (it/es/pt), 1st (en), 1er 2e 2ème (fr)
    def ordinal(m: re.Match) -> str:
        return _ordinal(int(m.group(1)), lang, feminine=m.group(2) in ("ª", "a"))
    if lang in ("it", "es", "pt"):
        text = re.sub(r"\b(\d+)(º|°|ª)", ordinal, text)
    elif lang == "en":
        text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", ordinal, text)
    elif lang == "fr":
        text = re.sub(r"\b(\d+)(er|re|e|ème)\b", ordinal, text)

    # percentages, then years, then every number left
    groups = "".join(re.escape(c) for c in spec["group"])
    num = rf"\d{{1,3}}(?:[{groups}]\d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
    text = re.sub(rf"({num})\s?%", lambda m: f"{_number(m.group(1), lang)} {spec['pct']}", text)
    text = re.sub(r"(?<![\d.,])(1[1-9]\d\d|20\d\d)(?![\d.,]\d)", lambda m: _year(int(m.group(1)), lang), text)
    # a letter right after a number is its own word ("3D" → "tre D")
    return re.sub(rf"(?<![\w.,])({num})(?!\d)(?=([^\W\d_])?)",
                  lambda m: _number(m.group(1), lang) + (" " if m.group(2) else ""), text)
