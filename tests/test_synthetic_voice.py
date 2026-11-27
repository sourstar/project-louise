"""A language's synthetic voice, made by one command (app/synthetic_voice.py).

Operator, 2026-11-27: "Add a spanish voice", and the rule of the same day:
"Anything requiring a manual edit because not covered by the API goes back to
architecture for implementation or fix". On 2026-11-24 the it, en, fr and de
voices were made by hand calls to the model server (München,
/root/measure/synthetic-voices/): a clip read with NO voice, its profile
POSTed, the line set. `louise node speech synthetic <lang>` does the same,
with the clip heard back before it becomes a voice.
"""
from __future__ import annotations

import io
import json
import types
import wave
from contextlib import nullcontext

import pytest

from app import httpc, louise, platform_config, speech, synthetic_voice as sv, transcribe, voice
from app.transcribe import Answer

SERVER = "http://localai:8080"
TTS, STT = "louise-tts-omnivoice", "parakeet-cpp-multilingual"
IT = speech.PROFILE_PREFIX + "edcdce3a"


def _wav(seconds: float, rate: int = 24000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * channels * int(seconds * rate))
    return buf.getvalue()


class Server:
    """The model server as the 11-24 calls met it."""

    def __init__(self, monkeypatch, tmp_path):
        self.profiles = [{"id": "edcdce3a", "name": "Synthetic it C", "voice": IT}]
        self.speech: list[dict] = []
        self.posted: list[tuple[dict, dict]] = []
        self.heard: list[str] = []           # what each transcription answers, in turn
        self.clips: list[bytes] = []         # what each synthesis answers, in turn
        self.pings: list[str] = []
        cfg = tmp_path / "config"
        cfg.mkdir()
        (cfg / "platform.toml").write_text(
            f'[speech]\ntts_model = "{TTS}"\nstt_model = "{STT}"\nsynthetic_voices = "it={IT}"\n',
            encoding="utf-8")
        self.file = cfg / "platform.toml"
        monkeypatch.setenv("CONFIG_DIR", str(cfg))
        monkeypatch.setattr(voice, "server", lambda: SERVER)
        monkeypatch.setattr(httpc, "get", self.get)
        monkeypatch.setattr(httpc, "post", self.post)
        monkeypatch.setattr(transcribe, "transcribe", self.transcribe)
        monkeypatch.setattr(speech, "pinned", lambda url, models, holder="speech": nullcontext())
        monkeypatch.setattr(speech, "ping", self.ping)
        self.loads: list[tuple[str, str]] = []
        self.refuse_load = False
        from app import model_load, resident

        def warm(url, model, kind, role):
            def go():
                if self.refuse_load:
                    raise model_load.LoadRefused(f"{model}: no room could be made", model=model)
                self.loads.append((model, kind))
            return go
        monkeypatch.setattr(resident, "_speech_warm", warm)
        self.oom_at: int | None = None     # the draw (1-based) the card refuses
        self.draws = 0

    def get(self, url, timeout=None, **kw):
        assert url == f"{SERVER}/api/voice-profiles"
        return types.SimpleNamespace(status_code=200, json=lambda: {"data": list(self.profiles)})

    def post(self, url, json=None, data=None, files=None, timeout=None, **kw):
        if url == f"{SERVER}/v1/audio/speech":
            self.draws += 1
            if self.draws == self.oom_at:
                return types.SimpleNamespace(status_code=500, content=b"", headers={}, text=(
                    'rpc error: code = Unavailable desc = error reading from server: EOF '
                    '(level_zero backend failed with error: 39 (UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY))'))
            self.speech.append(json)
            clip = self.clips.pop(0) if self.clips else _wav(10.0)
            return types.SimpleNamespace(status_code=200, content=clip, text="", headers={})
        assert url == f"{SERVER}/api/voice-profiles"
        self.posted.append((data, files))
        n = len(self.posted)
        made = {"id": f"new{n}", "name": data["name"], "voice": f"{speech.PROFILE_PREFIX}new{n}",
                "language": data["language"]}
        self.profiles.append(made)
        return types.SimpleNamespace(status_code=201, json=lambda: made, text="")

    def transcribe(self, audio, filename, content_type, fields, url=None, deadline_s=None):
        assert dict(fields)["model"] == STT and url == SERVER
        text = self.heard.pop(0) if self.heard else sv.TEXTS[dict(fields)["language"]]
        return Answer(200, json.dumps({"text": text}).encode(), "application/json", "skipped")

    def ping(self, url, model, voice_uri, timeout=60.0):
        assert (url, model) == (SERVER, TTS)
        self.pings.append(voice_uri)
        return True

    def line(self) -> dict[str, str]:
        return platform_config.synthetic_voices(platform_config._load_one(self.file).speech.synthetic_voices)


@pytest.fixture
def server(monkeypatch, tmp_path):
    return Server(monkeypatch, tmp_path)


def test_spanish_is_made_as_on_11_24_heard_recorded_set_and_pinged(server):
    out = sv.make("es", log=lambda m: None)
    # step 1: the sentence read with NO voice: the field is absent, not empty
    (call,) = server.speech
    assert call == {"model": TTS, "input": sv.TEXTS["es"], "language": "es", "response_format": "wav"}
    assert "voice" not in call
    # step 3: the profile as POSTed on 11-24
    ((data, files),) = server.posted
    assert data["name"] == "Synthetic es" and data["language"] == "es-ES"
    assert data["transcript"] == sv.TEXTS["es"] and data["consent_confirmed"] == "true"
    assert "with no voice" in data["description"] and "no person is in it" in data["description"]
    assert files["audio"][0] == "synthetic-es.wav" and files["audio"][2] == "audio/wav"
    # step 4: the line keeps Italian, in its place, and adds Spanish
    assert server.line() == {"it": IT, "es": out["made"][0]["voice"]}
    assert list(server.line()) == ["it", "es"]
    # step 5: the new voice pinged, so its clip is encoded now
    assert server.pings == [out["made"][0]["voice"]]
    assert out["made"][0]["heard"] == 1.0 and out["discarded"] == []


def test_the_11_24_sentences_are_kept_verbatim():
    assert sv.TEXTS["de"] == ("Dies ist die synthetische Stimme von Louise für die deutsche Sprache. Ich lese "
                              "die Antworten laut vor, ruhig und deutlich, einen Satz nach dem anderen, ohne Eile.")
    assert sv.TEXTS["it"].startswith("Questa è la voce sintetica di Louise per la lingua italiana.")
    assert {k: sv.TAGS[k] for k in ("it", "en", "fr", "de")} == {
        "it": "it-IT", "en": "en-US", "fr": "fr-FR", "de": "de-DE"}


def test_a_draw_not_heard_as_the_sentence_is_never_made_a_voice(server):
    server.heard = ["Ceci est une autre langue entièrement, pas du tout la phrase."]
    out = sv.make("es", log=lambda m: None)
    assert len(server.speech) == 2 and len(server.posted) == 1
    assert out["discarded"][0]["draw"] == 1 and "heard" in out["discarded"][0]["why"]
    assert out["draws"] == 2


def test_a_clip_of_the_wrong_shape_is_discarded_before_it_is_heard(server):
    server.clips = [_wav(1.0), _wav(10.0, channels=2), _wav(10.0)]
    out = sv.make("es", log=lambda m: None)
    assert [d["why"].split(",")[0] for d in out["discarded"]] == ["1.0 s", "2 channels"]
    assert len(server.posted) == 1


def test_too_many_failed_draws_stop_with_nothing_set(server):
    server.heard = ["no"] * 10
    with pytest.raises(sv.SyntheticError, match="3 draws"):
        sv.make("es", log=lambda m: None)
    assert server.posted == [] and server.line() == {"it": IT} and server.pings == []


def test_candidates_are_designed_as_on_11_24_and_none_set_until_one_is_used(server):
    out = sv.make("es", candidates=3, log=lambda m: None)
    assert [m["name"] for m in out["made"]] == ["Synthetic es A", "Synthetic es B", "Synthetic es C"]
    # each candidate a DESIGN, in `instructions`, with no voice (München, 11-27:
    # three undesigned draws were one speaker)
    assert [c.get("instructions") for c in server.speech] == [
        "male, elderly, low pitch", "male, elderly, very low pitch", "male, middle-aged, low pitch"]
    assert all("voice" not in c for c in server.speech)
    assert [m["design"] for m in out["made"]] == [c["instructions"] for c in server.speech]
    assert 'designed as "male, middle-aged, low pitch"' in server.posted[2][0]["description"]
    assert server.line() == {"it": IT} and server.pings == []
    assert "--use" in out["says"]
    got = sv.use("es", "Synthetic es B")
    assert server.line() == {"it": IT, "es": out["made"][1]["voice"]}
    assert got["voice"] == out["made"][1]["voice"] and server.pings == [got["voice"]]


def test_a_name_already_held_is_refused_and_nothing_is_drawn(server):
    server.profiles.append({"id": "s", "name": "Synthetic es", "voice": speech.PROFILE_PREFIX + "s"})
    with pytest.raises(sv.SyntheticError, match="--use \"Synthetic es\""):
        sv.make("es", log=lambda m: None)
    assert server.speech == [] and server.posted == []


def test_candidates_take_the_letters_no_profile_holds(server):
    # the node's three undesigned Spanish draws of 11-27 keep A to C
    for c in "ABC":
        server.profiles.append({"id": c, "name": f"Synthetic es {c}", "voice": speech.PROFILE_PREFIX + c})
    out = sv.make("es", candidates=2, log=lambda m: None)
    assert [m["name"] for m in out["made"]] == ["Synthetic es D", "Synthetic es E"]
    assert [m["design"] for m in out["made"]] == ["male, elderly, low pitch", "male, elderly, very low pitch"]
    assert server.line() == {"it": IT}


def test_designs_given_are_drawn_and_one_design_is_made_and_set(server):
    out = sv.make("es", designs=["female, middle-aged, low pitch", " male, elderly "], log=lambda m: None)
    assert [c["instructions"] for c in server.speech] == ["female, middle-aged, low pitch", "male, elderly"]
    assert server.line() == {"it": IT}
    out = sv.make("es", designs=["male, middle-aged, low pitch"], log=lambda m: None)
    assert out["made"][0]["name"] == "Synthetic es" and server.speech[-1]["instructions"] == (
        "male, middle-aged, low pitch")
    assert server.line()["es"] == out["made"][0]["voice"]
    with pytest.raises(sv.SyntheticError, match="one or the other"):
        sv.make("fr", candidates=3, designs=["male, elderly"], log=lambda m: None)


def test_a_discarded_design_is_drawn_again(server):
    server.heard = ["no"]
    out = sv.make("es", candidates=2, log=lambda m: None)
    assert [c["instructions"] for c in server.speech] == [
        "male, elderly, low pitch", "male, elderly, low pitch", "male, elderly, very low pitch"]
    assert out["discarded"][0]["design"] == "male, elderly, low pitch"


def test_the_11_24_designs_are_kept_verbatim():
    assert sv.DESIGNS == {"A": "male, elderly, low pitch", "B": "male, elderly, very low pitch",
                          "C": "male, middle-aged, low pitch", "D": "female, elderly, low pitch",
                          "E": "female, middle-aged, low pitch", "F": "male, elderly"}


def test_use_replaces_a_language_s_voice_in_its_place(server):
    server.profiles.append({"id": "x", "name": "Synthetic it D", "voice": speech.PROFILE_PREFIX + "x"})
    sv.use("it", "Synthetic it D")
    assert server.line() == {"it": speech.PROFILE_PREFIX + "x"}


def test_use_of_a_profile_the_server_does_not_hold_is_refused(server):
    with pytest.raises(sv.SyntheticError, match="holds no voice profile"):
        sv.use("es", "Synthetic es Z")
    assert server.line() == {"it": IT}


def test_a_language_with_no_sentence_says_what_supplies_it(server):
    with pytest.raises(sv.SyntheticError, match="--text"):
        sv.make("pt", log=lambda m: None)
    server.heard = ["Esta é a voz sintética de Louise para a língua portuguesa."]
    out = sv.make("pt", text="Esta é a voz sintética de Louise para a língua portuguesa.", log=lambda m: None)
    assert server.posted[0][0]["language"] == "pt-PT" and out["tag"] == "pt-PT"


def test_no_speech_to_text_model_no_voice(server):
    server.file.write_text(f'[speech]\ntts_model = "{TTS}"\n', encoding="utf-8")
    with pytest.raises(sv.SyntheticError, match="--stt"):
        sv.make("es", log=lambda m: None)
    assert server.speech == []


def test_heard_is_the_share_of_the_sentence_s_words_given_back():
    s = sv.TEXTS["es"]
    assert sv.heard(s, s.upper().replace(",", "")) == 1.0
    assert sv.heard(s, "") == 0.0
    assert sv.heard(s, s.replace("calma", "palma")) >= sv.HEARD_MIN


def test_the_command_and_its_route(server, capsys):
    a = louise.build_parser().parse_args(["node", "speech", "synthetic", "es"])
    assert a.fn(a) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["made"][0]["name"] == "Synthetic es" and "es" in server.line()
    a = louise.build_parser().parse_args(["node", "speech", "synthetic", "es"])
    assert a.fn(a) == 2                                  # held now: refused, naming --use
    assert "--use" in capsys.readouterr().err
    a = louise.build_parser().parse_args(["node", "speech", "synthetic", "fr", "--designs",
                                          "female, elderly, low pitch; male, elderly"])
    assert a.fn(a) == 0
    assert [m["design"] for m in json.loads(capsys.readouterr().out)["made"]] == [
        "female, elderly, low pitch", "male, elderly"]


def test_the_room_is_made_before_any_draw(server):
    # München, 2026-11-27: a draw on a full card killed the TTS backend
    sv.make("es", log=lambda m: None)
    assert server.loads == [(TTS, "tts"), (STT, "stt")]


def test_no_room_is_a_deferral_not_an_error(server, capsys):
    server.refuse_load = True
    with pytest.raises(sv.Deferred, match="run the same command again"):
        sv.make("es", log=lambda m: None)
    assert server.speech == [] and server.posted == []
    a = louise.build_parser().parse_args(["node", "speech", "synthetic", "es"])
    assert a.fn(a) == 3                                   # 503 at the route: run again
    assert "run the same command again" in capsys.readouterr().err


def test_a_draw_the_card_refuses_defers_and_names_what_was_made(server):
    server.oom_at = 2                     # the first candidate made, the second refused
    with pytest.raises(sv.Deferred, match="Synthetic es A.*next free letters"):
        sv.make("es", candidates=3, log=lambda m: None)
    assert [d["name"] for d, _f in server.posted] == ["Synthetic es A"]
    assert server.line() == {"it": IT}
    # run again: the rest under the next free letters, A kept
    out = sv.make("es", candidates=2, log=lambda m: None)
    assert [m["name"] for m in out["made"]] == ["Synthetic es B", "Synthetic es C"]
