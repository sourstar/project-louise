"""The node's voice (app/voice.py; operator, 2026-11-07): the speech models
are the node's, in platform.toml's `[speech]`, resolved by one function and
written by `louise node speech set`; a model unset is a voice the Ask page
does not offer — no button, nothing asked — and the reason is the
operator's, with the command that supplies it."""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import api, gpu_models, louise, platform_config, speech, voice
from app.platform_config import SpeechConfig

OTHER = """# the node
[node]
name = "nas"   # its label

[ask]
latency_budget_ms = 8000
"""


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def no_server(monkeypatch):
    """The model server's `/v1/models`: `listed` names, or unreachable (None)."""
    state = {"listed": ["whisper-large-v3", "silero-vad"]}

    class R:
        status_code = 200

        def json(self):
            return {"data": [{"id": m} for m in state["listed"]]}

    def get(url, **kw):
        assert url.endswith("/v1/models"), url
        if state["listed"] is None:
            raise ConnectionError("refused")
        return R()
    monkeypatch.setattr(voice.httpc, "get", get)
    return state


def run(capsys, *argv) -> tuple[int, dict | None, str]:
    code = louise.main(list(argv))
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


# ---- the one resolution ------------------------------------------------------

def test_the_speech_models_are_read_from_the_platform_file(cfgdir):
    assert voice.settings() == SpeechConfig(), "no file: none set"
    (cfgdir / "platform.toml").write_text(
        '[speech]\nstt_model = "whisper-large-v3"\nvad_model = " silero-vad "\n'
        'tts_model = "omnivoice-cpp"\ntts_voice = "localai://voice-profiles/1c834741"\n', encoding="utf-8")
    assert voice.settings() == SpeechConfig(stt_model="whisper-large-v3", vad_model="silero-vad",
                                            tts_model="omnivoice-cpp",
                                            tts_voice="localai://voice-profiles/1c834741")
    assert voice.settings() == platform_config.load().speech


@pytest.mark.parametrize("table, says", [
    ('[speech]\nstt = "whisper"\n', "unknown key(s) stt"),
    ('[speech]\nstt_model = ""\n', "a non-empty string"),
    ('[speech]\ntts_model = 3\n', "a non-empty string"),
    ('speech = "whisper"\n', "[speech] is a table"),
])
def test_a_wrong_speech_table_is_refused_at_load(cfgdir, table, says):
    (cfgdir / "platform.toml").write_text(table, encoding="utf-8")
    with pytest.raises(platform_config.schedule_mod.ScheduleError, match=re.escape(says)):
        platform_config.load()


def test_a_voice_not_set_is_not_offered_and_the_operator_is_told_how(cfgdir):
    got = voice.offered()
    assert got == {
        "input": {"available": False, "trimmed": False,
                  "why": "no speech-to-text model is set on this node: `louise node speech set --stt <model>`"},
        "output": {"available": False,
                   "why": "no text-to-speech model is set on this node: `louise node speech set --tts <model>`"}}
    (cfgdir / "platform.toml").write_text('[speech]\nstt_model = "w"\nvad_model = "v"\n', encoding="utf-8")
    got = voice.offered()
    assert got["input"] == {"available": True, "why": None, "trimmed": True}
    assert got["output"]["available"] is False
    (cfgdir / "platform.toml").write_text("[speech\n", encoding="utf-8")
    got = voice.offered()
    assert not got["input"]["available"] and "platform.toml does not load" in got["input"]["why"]
    assert voice.settings_or_none() == SpeechConfig()


def test_the_page_asks_the_node_what_it_offers(cfgdir):
    (cfgdir / "platform.toml").write_text('[speech]\ntts_model = "omnivoice-cpp"\n', encoding="utf-8")
    r = TestClient(api.app).get("/ask/voice")
    assert r.status_code == 200 and r.json() == voice.offered()
    assert r.json()["output"]["available"] and not r.json()["input"]["available"]


# ---- `louise node speech` --------------------------------------------------

def test_set_writes_the_table_and_keeps_every_other_line(cfgdir, capsys, no_server):
    (cfgdir / "platform.toml").write_text(OTHER, encoding="utf-8")
    code, out, _ = run(capsys, "node", "speech", "set", "--stt", "whisper-large-v3", "--vad", "silero-vad")
    assert code == 0
    assert out["changed"] == ["stt_model", "vad_model"]
    assert out["speech"] == {"stt_model": "whisper-large-v3", "vad_model": "silero-vad",
                             "tts_model": None, "tts_voice": None}
    assert out["offered"]["input"]["available"] and not out["offered"]["output"]["available"]
    assert out["listed"] == {"silero-vad": True, "whisper-large-v3": True}
    text = (cfgdir / "platform.toml").read_text(encoding="utf-8")
    assert text.startswith(OTHER), "every other line byte for byte"
    assert voice.settings() == SpeechConfig(stt_model="whisper-large-v3", vad_model="silero-vad")
    # again, in place: the table where it stood, once
    code, out, _ = run(capsys, "node", "speech", "set", "--tts", "omnivoice-cpp", "--voice", 'Phi "lippe"')
    assert code == 0 and out["changed"] == ["tts_model", "tts_voice"]
    text = (cfgdir / "platform.toml").read_text(encoding="utf-8")
    assert text.count("[speech]") == 1 and text.startswith(OTHER)
    assert voice.settings().tts_voice == 'Phi "lippe"'
    assert out["listed"] == {"omnivoice-cpp": False, "silero-vad": True, "whisper-large-v3": True}
    assert out["listed_note"] == "not listed by the model server: omnivoice-cpp"


def test_an_empty_value_removes_a_model_and_the_last_one_the_table(cfgdir, capsys, no_server):
    (cfgdir / "platform.toml").write_text(OTHER, encoding="utf-8")
    run(capsys, "node", "speech", "set", "--stt", "whisper-large-v3", "--tts", "omnivoice-cpp")
    code, out, _ = run(capsys, "node", "speech", "set", "--stt", "")
    assert code == 0 and out["changed"] == ["stt_model"]
    assert not out["offered"]["input"]["available"], "no model: the microphone is no longer offered"
    code, out, _ = run(capsys, "node", "speech", "set", "--tts", "")
    assert (cfgdir / "platform.toml").read_text(encoding="utf-8") == OTHER, "the file as it was"
    code, out, _ = run(capsys, "node", "speech", "set", "--tts", "")
    assert code == 0 and out["changed"] == [], "nothing to change: nothing written"


def test_set_with_nothing_to_set_and_a_file_that_would_not_load_are_refused(cfgdir, capsys, no_server):
    code, out, err = run(capsys, "node", "speech", "set")
    assert code == 2 and out is None and "nothing to set" in err
    # the table written another way, as a dotted key: the node reads it, a
    # second [speech] would not load — refused, the file untouched
    (cfgdir / "platform.toml").write_text('speech.stt_model = "a"\n' + OTHER, encoding="utf-8")
    assert voice.settings().stt_model == "a"
    before = (cfgdir / "platform.toml").read_text(encoding="utf-8")
    code, out, err = run(capsys, "node", "speech", "set", "--tts", "omnivoice-cpp")
    assert code == 2 and "Nothing was changed" in err
    assert (cfgdir / "platform.toml").read_text(encoding="utf-8") == before
    assert not list(cfgdir.glob("*.tmp")), "no temporary file left"
    (cfgdir / "platform.toml").write_text("[speech]\nstt = 1\n", encoding="utf-8")
    code, out, err = run(capsys, "node", "speech", "show")
    assert code == 2 and "unknown key(s) stt" in err


def test_show_says_where_the_models_run_and_what_the_page_offers(cfgdir, capsys, no_server, monkeypatch):
    monkeypatch.setattr(voice.config, "CHAT_URL", "http://ai:8080/")
    (cfgdir / "platform.toml").write_text('[speech]\nstt_model = "whisper-large-v3"\n', encoding="utf-8")
    code, out, _ = run(capsys, "node", "speech", "show")
    assert code == 0
    assert out["server"] == "http://ai:8080" and out["file"] == str(cfgdir / "platform.toml")
    assert out["speech"]["stt_model"] == "whisper-large-v3" and out["listed"] == {"whisper-large-v3": True}
    assert out["offered"] == voice.offered() and "https" in out["note"]
    no_server["listed"] = None
    code, out, _ = run(capsys, "node", "speech", "show")
    assert out["listed"] is None and out["listed_note"] == \
        "the model server at http://ai:8080 did not answer GET /v1/models"


def test_rewrite_puts_the_table_where_it_stood():
    text = '[a]\nx = 1\n\n[speech]   # old\nstt_model = "old"\n# a comment\n\n[b]\ny = 2\n'
    got = voice.rewrite(text, {"stt_model": "new", "vad_model": None, "tts_model": None, "tts_voice": None})
    assert got.startswith('[a]\nx = 1\n\n[speech]') and got.endswith('stt_model = "new"\n\n[b]\ny = 2\n')
    assert "old" not in got
    assert voice.rewrite("[a]\nx = 1", {"tts_model": "t"}).startswith("[a]\nx = 1\n\n[speech]")
    assert voice.rewrite(text, {}) == "[a]\nx = 1\n\n[b]\ny = 2\n"


def test_the_route_of_the_command_tree_answers_show(cfgdir, monkeypatch, no_server):
    from app import api_commands
    path, _ = api_commands.resolve(["node", "speech", "show"])
    assert path == ("node", "speech", "show")
    path, _ = api_commands.resolve(["node", "speech", "set"])
    assert path == ("node", "speech", "set")


# ---- the speech the page asks for --------------------------------------------

def test_a_speech_request_naming_no_model_or_voice_gets_the_nodes(cfgdir, monkeypatch):
    (cfgdir / "platform.toml").write_text(
        '[speech]\ntts_model = "omnivoice-cpp"\ntts_voice = "localai://voice-profiles/1c834741"\n', encoding="utf-8")
    calls, pins = [], []

    class R:
        status_code, content, headers = 200, b"RIFF", {"content-type": "audio/wav"}

        def json(self):
            return {"data": [{"voice": "localai://voice-profiles/first", "created_at": "2026-01-01"}]}
    monkeypatch.setattr(speech.httpc, "get", lambda url, **kw: R())
    monkeypatch.setattr(speech.httpc, "post", lambda url, json, **kw: calls.append(json) or R())
    monkeypatch.setattr(speech.model_pins, "hold", lambda url, model, holder: pins.append(model) or True)
    monkeypatch.setattr(speech.model_pins, "release", lambda holder: None)
    monkeypatch.setattr(speech, "_profiles", (0.0, []))
    assert speech.synthesize({"input": "Dante wrote it [3]."}, url="http://ai")[0] == 200
    assert calls[-1] == {"input": "Dante wrote it.", "model": "omnivoice-cpp",
                         "voice": "localai://voice-profiles/1c834741"}
    assert pins == ["omnivoice-cpp"], "the node's model pinned while it speaks"
    speech.synthesize({"input": "Dante.", "model": "kokoro", "voice": "alloy"}, url="http://ai")
    assert (calls[-1]["model"], calls[-1]["voice"]) == ("kokoro", "alloy"), "a client's own choice is kept"
    (cfgdir / "platform.toml").write_text('[speech]\ntts_model = "omnivoice-cpp"\n', encoding="utf-8")
    speech.synthesize({"input": "Dante."}, url="http://ai")
    assert calls[-1]["voice"] == "localai://voice-profiles/first", "no voice set: the first profile, as before"


# ---- the Ask page ------------------------------------------------------------

def page(config_dir, table: str) -> str:
    (config_dir / "platform.toml").write_text(table, encoding="utf-8")
    return TestClient(api.app).get("/ask").text


def test_the_page_has_no_button_for_a_voice_the_node_does_not_offer(isolated_config, config_dir):
    html = page(config_dir, "")
    assert 'id="mic"' not in html and 'id="speak"' not in html and "voice.js" not in html
    assert 'id="q"' in html, "the question box as before"
    html = page(config_dir, '[speech]\nstt_model = "whisper-large-v3"\n')
    assert 'id="mic"' in html and 'id="speak"' not in html and "/static/voice.js" in html
    html = page(config_dir, '[speech]\ntts_model = "omnivoice-cpp"\n')
    assert 'id="mic"' not in html and 'id="speak"' in html and "/static/voice.js" in html
    html = page(config_dir, '[speech]\nstt_model = "w"\ntts_model = "t"\nvad_model = "v"\n')
    assert 'id="mic"' in html and 'id="speak"' in html
    for forbidden in ("model", "server", "protocol"):   # RULE 2: nothing asks
        assert not re.search(rf'<(input|select)[^>]*{forbidden}', html, re.I)


def test_the_gpu_panel_counts_the_speech_models_as_louises():
    uses = gpu_models.louise_uses([], speech={"speech to text": "whisper-large-v3", "speech": "omnivoice-cpp"})
    assert uses == {"whisper-large-v3": ["speech to text"], "omnivoice-cpp": ["speech"]}


# ---- the reader's voice (operator, 2026-11-11: "choose philippe or valentina") --

PHILIPPE = "localai://voice-profiles/1c834741"
VALENTINA = "localai://voice-profiles/db55"


@pytest.fixture
def two_voices(cfgdir, monkeypatch):
    (cfgdir / "platform.toml").write_text(
        f'[speech]\ntts_model = "omnivoice-cpp"\ntts_voice = "{PHILIPPE}"\n', encoding="utf-8")
    calls = []

    class R:
        status_code, content, headers = 200, b"RIFF", {"content-type": "audio/wav"}

        def json(self):
            return {"data": [{"name": "Valentina", "language": "it-IT", "voice": VALENTINA,
                              "created_at": "2026-10-16T22:20:25Z"},
                             {"name": "Philippe", "language": "it-IT", "voice": PHILIPPE,
                              "created_at": "2026-10-06T17:11:09Z"}]}
    monkeypatch.setattr(speech.httpc, "get", lambda url, **kw: R())
    monkeypatch.setattr(speech.httpc, "post", lambda url, json, **kw: calls.append(json) or R())
    monkeypatch.setattr(speech.model_pins, "hold", lambda url, model, holder: True)
    monkeypatch.setattr(speech.model_pins, "release", lambda holder: None)
    monkeypatch.setattr(speech, "_profiles", (0.0, []))
    monkeypatch.setattr(speech, "_clip_get", lambda key: None)
    monkeypatch.setattr(speech, "_clip_put", lambda key, data, ctype: None)
    return calls


def test_the_reader_chooses_among_the_nodes_recorded_voices(two_voices):
    got = voice.voices()
    assert [v["name"] for v in got["voices"]] == ["Philippe", "Valentina"], "in the order they were recorded"
    assert got["default"] == PHILIPPE, "none chosen: the node's own"
    assert TestClient(api.app).get("/ask/voices").json() == got


def test_the_readers_voice_speaks_and_one_no_longer_held_falls_back_to_the_nodes(two_voices):
    calls = two_voices
    assert speech.synthesize({"input": "Dante.", "voice": VALENTINA}, url="http://ai")[0] == 200
    assert calls[-1]["voice"] == VALENTINA, "the reader's choice"
    speech.synthesize({"input": "Dante.", "voice": "localai://voice-profiles/gone"}, url="http://ai")
    assert calls[-1]["voice"] == PHILIPPE, "a voice the server no longer holds: the node's, not an error"
    speech.synthesize({"input": "Dante."}, url="http://ai")
    assert calls[-1]["voice"] == PHILIPPE


def test_no_answer_read_aloud_no_voice_to_choose(cfgdir):
    assert voice.voices() == {"voices": [], "default": None, "account": None}


def test_the_voice_choice_is_on_the_page_only_where_the_answer_is_read(isolated_config, config_dir):
    assert 'id="voice-pick"' not in page(config_dir, "")
    html = page(config_dir, '[speech]\ntts_model = "omnivoice-cpp"\n')
    settings = html[html.index('<details class="settings">'):]
    assert 'id="voice-pick"' in settings[:settings.index("</details>")], "under the Ask page's Settings"


# ---- the account's voice (operator, 2026-11-11: "Shall a user account can choose it?")

def test_an_account_keeps_its_voice_and_every_client_of_it_hears_it(two_voices, monkeypatch):
    from app import config, users
    calls = two_voices
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    _, token = users.add("ada")
    c, h = TestClient(api.app), {"Authorization": f"Bearer {token}"}
    assert c.get("/ask/voices", headers=h).json()["account"] == {"name": "ada", "voice": None}
    r = c.post("/ask/voice", headers=h, json={"voice": VALENTINA})
    assert r.status_code == 200 and r.json()["account"] == {"name": "ada", "voice": VALENTINA}
    assert f'voice = "{VALENTINA}"' in users.path().read_text(), "kept with the account, in users.toml"
    assert c.post("/v1/audio/speech", headers=h, json={"input": "Dante."}).status_code == 200
    assert calls[-1]["voice"] == VALENTINA, "a client naming no voice hears the account's"
    c.post("/v1/audio/speech", headers=h, json={"input": "Dante.", "voice": PHILIPPE})
    assert calls[-1]["voice"] == PHILIPPE, "a request naming one is read in it"
    assert c.post("/ask/voice", headers=h, json={"voice": "localai://voice-profiles/nope"}).status_code == 400
    assert users.voice_of("ada") == VALENTINA, "a voice the node does not offer is refused, nothing kept"
    assert c.post("/ask/voice", headers=h, json={"voice": None}).json()["account"]["voice"] is None
    c.post("/v1/audio/speech", headers=h, json={"input": "Dante."})
    assert calls[-1]["voice"] == PHILIPPE, "none chosen: the node's"


def test_no_account_no_voice_kept_by_the_node_and_one_since_removed_is_the_nodes(two_voices, monkeypatch):
    from app import config, users
    calls = two_voices
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    c = TestClient(api.app)
    assert c.get("/ask/voices").json()["account"] is None, "a guest: the browser keeps it"
    assert c.post("/ask/voice", json={"voice": VALENTINA}).status_code == 409
    assert c.post("/ask/voice", headers={"X-Admin-Token": "sekrit"}, json={"voice": VALENTINA}).status_code == 409, \
        "the admin token is no account"
    _, token = users.add("ada")
    users.set_voice("ada", "localai://voice-profiles/gone")
    h = {"Authorization": f"Bearer {token}"}
    assert c.get("/ask/voices", headers=h).json()["account"] == {"name": "ada", "voice": None}
    c.post("/v1/audio/speech", headers=h, json={"input": "Dante."})
    assert calls[-1]["voice"] == PHILIPPE, "a voice the server no longer holds: the node's, never an error"
