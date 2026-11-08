"""Speech for the programs that read Louise aloud (app/speech.py; operator,
2026-11-06, 2026-11-07): nothing in square brackets read, in whatever shape
the answer's language writes its citations; one voice for every language;
the model kept loaded while sentences come."""
import time

import pytest
from fastapi.testclient import TestClient

from app import api, speech
from tests.deposit.test_deposit_identity import node  # noqa: F401

PHILIPPE = "localai://voice-profiles/1c834741"
PROFILES = [{"name": "Valentina", "language": "it-IT", "voice": "localai://voice-profiles/db55",
             "created_at": "2026-10-16T22:20:25Z"},
            {"name": "Philippe", "language": "it-IT", "voice": PHILIPPE, "created_at": "2026-10-06T17:11:09Z"}]


@pytest.mark.parametrize("text, said", [
    ("Dante wrote it [3].", "Dante wrote it."),
    ("Dante [3] [4] [5] and Petrarch [7].", "Dante and Petrarch."),
    ("It is [3]-[5] there.", "It is there."),
    ("See [3,4] and [6-8].", "See and."),
    ("A record [gutenberg:1001], quoted.", "A record, quoted."),
    ("Chapter [IV] and a list [a] are not read either.", "Chapter and a list are not read either."),
    # French answers (operator 2026-11-07: "[7] the number is pronounced")
    ("Selon la loi [7], c'est vrai.", "Selon la loi, c'est vrai."),
    ("C'est vrai [7 ; 8].", "C'est vrai."),
    ("C'est vrai [7 et 8] et [9, 10 et 11].", "C'est vrai et."),
    ("C'est vrai [ 7 ].", "C'est vrai."),
    ("C'est vrai\u202f[7] !", "C'est vrai!"),
    ("C'est vrai [réf. 7] [source 8] [n° 9].", "C'est vrai."),
    ("C'est vrai \\[7\\].", "C'est vrai."),
    ("C'est vrai 【7】【8】.", "C'est vrai."),
    ("La legge [art. 3] resta.", "La legge resta."),
    ("Selon [Dupont, 1998, p. 7 ; voir aussi 9], c'est vrai.", "Selon, c'est vrai."),
    ("Avant [7]–[9], [10] ; après.", "Avant; après."),
])
def test_the_citations_are_not_read(text, said):
    assert speech.speakable(text) == said


def test_a_citation_before_a_bracket_or_a_parenthesis_gets_a_space_even_across_chunks():
    """Open WebUI reads "[3][4]" and "[3](…)" as links and speaks "3"."""
    assert speech.spaced_citations("Dante [3][4][5].") == "Dante [3] [4] [5]."
    assert speech.spaced_citations("[4] then", tail="Dante [3]") == " [4] then"
    assert speech.spaced_citations("Dante [3](art. 2).") == "Dante [3] (art. 2)."
    assert speech.spaced_citations("(art. 2)", tail="Dante [3]") == " (art. 2)"
    assert speech.spaced_citations("vrai [7][réf. 8] [7 ; 8](x)") == "vrai [7] [réf. 8] [7 ; 8] (x)"
    assert speech.spaced_citations("f. 7][8]", tail="vrai [ré") == "f. 7] [8]", "a citation split in two"
    assert speech.spaced_citations("[link][ref] and a[1]") == "[link][ref] and a[1]"
    assert speech.spaced_citations("a list (b) [Dante](x)") == "a list (b) [Dante](x)", "a link stays a link"


def test_a_stream_spaces_what_its_chunks_split():
    """The answer's stream through spaced_citations chunk by chunk, with the
    tail /v1 keeps, says what the whole text spaced at once says."""
    text = "Vrai [réf. 7][8] et [9](art. 2) ; [Dante](x) [10][11]."
    for size in (1, 2, 3, 5, 8):
        tail, out = "", []
        for i in range(0, len(text), size):
            v = speech.spaced_citations(text[i:i + size], tail)
            tail = (tail + v)[-speech.TAIL:]
            out.append(v)
        assert "".join(out) == speech.spaced_citations(text), size


@pytest.mark.parametrize("text, lang", [
    ("La Divina Commedia è stata scritta da Dante, ed è il poema della lingua italiana.", "it"),
    ("The Divine Comedy was written by Dante in the early fourteenth century.", "en"),
    ("Die Göttliche Komödie ist das Hauptwerk von Dante und wird in der Schule gelesen.", "de"),
    ("Dante.", None),
])
def test_the_language_is_told_by_its_commonest_words(text, lang):
    assert speech.language(text) == lang


def test_one_voice_for_every_language():
    """Operator, 2026-11-07: "Use the same omnivoice philippe voice for all the
    languages" — French had no profile and got the model's own voice, a
    different speaker at every sentence."""
    assert speech.voice_for(PHILIPPE, PROFILES) == PHILIPPE
    assert speech.voice_for("localai://voice-profiles/db55", PROFILES) == "localai://voice-profiles/db55"
    assert speech.voice_for(None, PROFILES) == PHILIPPE, "none asked: the first profile recorded"
    assert speech.voice_for("alloy", PROFILES) == "alloy", "a model's named voice is kept"
    assert speech.voice_for(None, []) is None, "no profile: the model's own"


@pytest.fixture
def server(monkeypatch):
    calls, pins = [], []

    class R:
        def __init__(self, status, content=b"", ctype="audio/wav", data=None):
            self.status_code, self.content, self.headers, self._data = status, content, {"content-type": ctype}, data

        def json(self):
            return self._data

    monkeypatch.setattr(speech.httpc, "get", lambda url, **kw: R(200, data={"data": PROFILES}))
    monkeypatch.setattr(speech.httpc, "post", lambda url, json, **kw: calls.append((url, json)) or R(200, b"RIFF"))
    monkeypatch.setattr(speech.model_pins, "hold", lambda url, model, holder: pins.append(("hold", model, holder)) or True)
    monkeypatch.setattr(speech.model_pins, "release", lambda holder: pins.append(("release", holder)))
    monkeypatch.setattr(speech, "_profiles", (0.0, []))
    monkeypatch.setattr(speech, "IDLE_S", 0.2)
    return calls, pins


def test_a_sentence_is_relayed_clean_in_its_language_s_voice_and_the_model_stays_pinned(server):
    calls, pins = server
    for text in ("Buonasera, la Divina Commedia è di Dante [3][4].", "E il poema della lingua italiana [5]."):
        got = speech.synthesize({"model": "omnivoice-cpp", "input": text, "voice": PHILIPPE}, url="http://ai:8080")
        assert got == (200, b"RIFF", "audio/wav")
    assert [c[0] for c in calls] == ["http://ai:8080/v1/audio/speech"] * 2
    assert calls[0][1] == {"model": "omnivoice-cpp", "input": "Buonasera, la Divina Commedia è di Dante.",
                           "voice": PHILIPPE, "language": "it"}
    english = speech.synthesize({"model": "omnivoice-cpp", "input": "The poem was written by Dante [1].",
                                 "voice": PHILIPPE}, url="http://ai:8080")
    assert english[0] == 200 and calls[-1][1]["voice"] == PHILIPPE and calls[-1][1]["language"] == "en"
    for text in ("La Divine Comédie est le poème de Dante [7 ; 8].", "Elle est écrite en toscan [9]."):
        speech.synthesize({"model": "omnivoice-cpp", "input": text}, url="http://ai:8080")
    assert [c[1]["voice"] for c in calls[-2:]] == [PHILIPPE, PHILIPPE], "the same voice, sentence after sentence"
    assert calls[-2][1]["input"] == "La Divine Comédie est le poème de Dante."
    assert pins == [("hold", "omnivoice-cpp", "speech")] * 5, "pinned from the first sentence, not released between"
    time.sleep(0.5)
    assert pins[-1] == ("release", "speech"), "released once the sentences stop"


def test_a_text_that_is_only_citations_is_refused(server):
    calls, _ = server
    assert speech.synthesize({"model": "m", "input": "[3][4]"}, url="http://ai")[0] == 400 and calls == []


def test_the_route_answers_audio(node, server):  # noqa: F811
    r = TestClient(api.app).post("/v1/audio/speech", json={"model": "omnivoice-cpp", "input": "Dante [1].",
                                                            "voice": PHILIPPE})
    assert r.status_code == 200 and r.content == b"RIFF" and r.headers["content-type"] == "audio/wav"


@pytest.mark.parametrize("text, said", [
    ("La Gallia fu conquistata da G. Cesare nel 58 a.C. Poi tornò.",
     "La Gallia fu conquistata da G Cesare nel 58 a.C. Poi tornò."),
    ("J. R. R. Tolkien wrote it.", "J R R Tolkien wrote it."),
    ("Il Prof. Rossi e il Dott. Bianchi.", "Il Prof Rossi e il Dott Bianchi."),
    ("art. 5 della legge n. 3", "art. 5 della legge n. 3"),
    ("Fine. Inizio [3].", "Fine. Inizio."),
    ("e.g. Rome", "e.g. Rome"),
])
def test_an_initial_or_a_title_before_a_name_loses_its_stop(text, said):
    """Operator, 2026-11-08: "G. Cesare" read with a long pause after "G.":
    the model reads a full stop as a sentence's end."""
    assert speech.speakable(text) == said


def test_the_route_says_the_synthesis_time_and_the_clip_length(node, server, monkeypatch):  # noqa: F811
    """`Server-Timing` (W3C) on /v1/audio/speech: the model server's call and
    the clip's length, to measure the reading's start and its pauses."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 12000)

    class R:
        status_code, content, headers = 200, buf.getvalue(), {"content-type": "audio/wav"}
    monkeypatch.setattr(speech.httpc, "post", lambda url, json, **kw: R())
    r = TestClient(api.app).post("/v1/audio/speech", json={"model": "omnivoice-cpp", "input": "Dante wrote it.",
                                                            "voice": PHILIPPE})
    timing = dict(part.strip().split(";dur=") for part in r.headers["server-timing"].split(","))
    assert r.status_code == 200 and float(timing["synth"]) >= 0 and float(timing["audio"]) == 500.0
