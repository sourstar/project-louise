"""Numbers and dates said in the text's language (app/speech_numbers.py;
operator, 2026-11-08, choice A: "numbers and dates are pronounced in Italian
anyway" — the one voice reads digits as it learned them). Each case is the
sentence the voice is given."""
from __future__ import annotations

import pytest

from app import speech
from app.speech_numbers import verbalize


@pytest.mark.parametrize("lang, text, said", [
    # Italian: thousands with a dot, decimals with a comma, eras, ordinals, dates, times
    ("it", "Cesare conquistò la Gallia nel 58 a.C. Poi morì il 15 marzo 44 a.C.",
     "Cesare conquistò la Gallia nel cinquantotto avanti Cristo. Poi morì il quindici marzo "
     "quarantaquattro avanti Cristo."),
    ("it", "Nel 1914 c'erano 1.234 soldati, il 3,5% del totale; il 2° posto e la 1ª volta.",
     "Nel millenovecentoquattordici c'erano milleduecentotrentaquattro soldati, il tre virgola "
     "cinque per cento del totale; il secondo posto e la prima volta."),
    ("it", "Il 01/01/2024 alle 14:30.", "Il primo gennaio duemilaventiquattro alle quattordici e trenta."),
    ("it", "Valore 3.5 e 12.000 abitanti.", "Valore tre virgola cinque e dodicimila abitanti."),
    ("it", "PMID 12345678.", "PMID uno due tre quattro cinque sei sette otto."),
    # English: years as years, commas in thousands, month-first dates, ordinals
    ("en", "In 1914 there were 1,234 soldiers, 3.5% of the total; the 2nd place.",
     "In nineteen fourteen there were one thousand two hundred and thirty-four soldiers, three point "
     "five percent of the total; the second place."),
    ("en", "On March 12, 2024 and 2024-03-12.",
     "On March twelfth, twenty twenty-four and March twelfth twenty twenty-four."),
    ("en", "Caesar died in 44 BC at 9:05.", "Caesar died in forty-four BC at nine oh five."),
    ("en", "The war ended on 11 November 1918.", "The war ended on the eleventh of November nineteen eighteen."),
    # French, German, Spanish, Portuguese
    ("fr", "Le 1er mars 2024, 1 234 soldats et 3,5 %. César mourut en 44 av. J.-C.",
     "Le premier mars deux mille vingt-quatre, mille deux cent trente-quatre soldats et trois virgule "
     "cinq pour cent. César mourut en quarante-quatre avant Jésus-Christ."),
    ("de", "Im Jahr 1914, um 14:30, waren es 1.234 Soldaten.",
     "Im Jahr neunzehnhundertvierzehn, um vierzehn Uhr dreißig, waren es "
     "eintausendzweihundertvierunddreißig Soldaten."),
    ("es", "El 12/03/2024 había 1.234 soldados; el 2º lugar y la 1ª vez.",
     "El doce de marzo de dos mil veinticuatro había mil doscientos treinta y cuatro soldados; el "
     "segundo lugar y la primera vez."),
    ("pt", "Em 1500, 3,5% das pessoas.", "Em mil e quinhentos, três vírgula cinco por cento das pessoas."),
])
def test_numbers_and_dates_are_said_in_the_texts_language(lang, text, said):
    assert verbalize(text, lang) == said


def test_a_language_it_does_not_know_is_left_as_written():
    assert verbalize("Nothing 1914 changes.", None) == "Nothing 1914 changes."
    assert verbalize("Ничего 1914.", "ru") == "Ничего 1914."
    assert verbalize("Nessun numero qui.", "it") == "Nessun numero qui."


def test_a_letter_after_a_number_stays_a_word_and_a_word_with_digits_is_left():
    assert verbalize("un film 3D e l'H2O", "it") == "un film tre D e l'H2O"


def test_the_speech_route_sends_the_words(monkeypatch):
    sent = []

    class R:
        status_code, content, headers = 200, b"RIFF", {"content-type": "audio/wav"}
    monkeypatch.setattr(speech.httpc, "get", lambda url, **kw: R())
    monkeypatch.setattr(speech.httpc, "post", lambda url, json, **kw: sent.append(json) or R())
    monkeypatch.setattr(speech, "_profiles", (0.0, []))
    speech.synthesize({"input": "The war began in 1914 [3].", "language": "en"}, url="http://ai")
    speech.synthesize({"input": "La guerra iniziò nel 1914 [3].", "language": "it-IT"}, url="http://ai")
    assert [s["input"] for s in sent] == ["The war began in nineteen fourteen.",
                                          "La guerra iniziò nel millenovecentoquattordici."]


@pytest.mark.parametrize("lang, text, said", [
    # operator, 2026-11-12: "dates in roman format are mispronounced"
    ("it", "Dante visse nel XIV secolo, tra il XIII e il XIV secolo; sec. XV; XIV-XV secolo.",
     "Dante visse nel quattordicesimo secolo, tra il tredicesimo e il quattordicesimo secolo; sec. "
     "quindicesimo; quattordicesimo-quindicesimo secolo."),
    ("it", "Luigi XIV e Pio IX; Elisabetta II regna. Carlo V fu imperatore.",
     "Luigi quattordicesimo e Pio nono; Elisabetta seconda regna. Carlo quinto fu imperatore."),
    ("it", "la II guerra mondiale e il I secolo a.C.", "la seconda guerra mondiale e il primo secolo avanti Cristo."),
    ("it", "Nel V secolo a.C. Atene fiorì.", "Nel quinto secolo avanti Cristo. Atene fiorì."),
    ("en", "Louis XIV built it; World War II ended; Henry VIII; the XIV century; MCMXC.",
     "Louis the fourteenth built it; World War two ended; Henry the eighth; the fourteenth century; nineteen ninety."),
    ("fr", "Louis XIV vécut au XVIIe siècle; François Ier; au Ve siècle.",
     "Louis quatorze vécut au dix-septième siècle; François premier; au cinquième siècle."),
    ("es", "Felipe II y Alfonso XIII; el siglo XIV; la II Guerra Mundial.",
     "Felipe segundo y Alfonso trece; el siglo catorce; la segunda Guerra Mundial."),
    ("de", "Ludwig XIV. regierte im XVII. Jahrhundert.", "Ludwig der vierzehnte regierte im siebzehnten Jahrhundert."),
    ("pt", "o século XIV e D. Pedro II", "o século catorze e D. Pedro segundo"),
])
def test_roman_numerals_are_said_as_the_language_says_them(lang, text, said):
    assert verbalize(text, lang) == said


@pytest.mark.parametrize("lang, text", [
    ("it", "LA STORIA DI ROMA, Washington DC, taglia XL, I Promessi Sposi, I secoli bui, CD e MD."),
    ("en", "When I left, Elizabeth I and a CV."),
    ("fr", "Le siècle des Lumières; Ce matin; De Gaulle."),
])
def test_what_only_looks_like_a_roman_numeral_is_left_as_written(lang, text):
    assert verbalize(text, lang) == text
