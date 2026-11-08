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
language reads it wrong in a new way. Roman numerals and fractions are left
as written.

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


def verbalize(text: str, lang: str | None) -> str:
    """`text` with its numbers and dates in words of `lang`; as it is for a
    language this module does not know (None included)."""
    if not lang or lang not in _LANG or not any(c.isdigit() for c in text):
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
