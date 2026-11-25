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
        'tts_model = "omnivoice-cpp"\ntts_voice = "localai://voice-profiles/aaaa1111"\n', encoding="utf-8")
    assert voice.settings() == SpeechConfig(stt_model="whisper-large-v3", vad_model="silero-vad",
                                            tts_model="omnivoice-cpp",
                                            tts_voice="localai://voice-profiles/aaaa1111")
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


def test_the_page_asks_the_node_what_it_offers(cfgdir, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    (cfgdir / "platform.toml").write_text('[speech]\ntts_model = "omnivoice-cpp"\n', encoding="utf-8")
    r = TestClient(api.app).get("/ask/voice", headers={"X-Admin-Token": "sekrit"})
    assert r.status_code == 200 and r.json() == voice.offered()
    assert r.json()["output"]["available"] and not r.json()["input"]["available"]


# ---- `louise node speech` --------------------------------------------------

def test_set_writes_the_table_and_keeps_every_other_line(cfgdir, capsys, no_server):
    (cfgdir / "platform.toml").write_text(OTHER, encoding="utf-8")
    code, out, _ = run(capsys, "node", "speech", "set", "--stt", "whisper-large-v3", "--vad", "silero-vad")
    assert code == 0
    assert out["changed"] == ["stt_model", "vad_model"]
    assert out["speech"] == {"stt_model": "whisper-large-v3", "vad_model": "silero-vad",
                             "tts_model": None, "tts_voice": None, "synthetic_voices": None, "tts_steps": None}
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
        '[speech]\ntts_model = "omnivoice-cpp"\ntts_voice = "localai://voice-profiles/aaaa1111"\n', encoding="utf-8")
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
    # too few words to tell its language: the engine's auto, never the profile's own tag
    assert calls[-1] == {"input": "Dante wrote it.", "model": "omnivoice-cpp",
                         "voice": "localai://voice-profiles/aaaa1111", "language": speech.AUTO}
    assert pins == ["omnivoice-cpp"], "the node's model pinned while it speaks"
    speech.synthesize({"input": "Dante.", "model": "kokoro", "voice": "alloy"}, url="http://ai")
    assert (calls[-1]["model"], calls[-1]["voice"]) == ("kokoro", "alloy"), "a client's own choice is kept"
    (cfgdir / "platform.toml").write_text('[speech]\ntts_model = "omnivoice-cpp"\n', encoding="utf-8")
    speech.synthesize({"input": "Dante."}, url="http://ai")
    assert calls[-1]["voice"] == "localai://voice-profiles/first", "no voice set: the first profile, as before"


# ---- the Ask page ------------------------------------------------------------

def page(config_dir, table: str, admin: bool = True) -> str:
    """The Ask page as an admin sees it (a recorded voice is an admin's,
    2026-11-16), or as a guest."""
    from app import config
    (config_dir / "platform.toml").write_text(table, encoding="utf-8")
    was = config.ADMIN_TOKEN
    config.ADMIN_TOKEN = "sekrit"
    try:
        return TestClient(api.app).get("/ask", headers={"X-Admin-Token": "sekrit"} if admin else {}).text
    finally:
        config.ADMIN_TOKEN = was


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


# ---- the reader's voice (operator, 2026-11-11: a configuration tab to choose the voice) --

VOICE_A = "localai://voice-profiles/aaaa1111"
VOICE_B = "localai://voice-profiles/bbbb2222"


@pytest.fixture
def two_voices(cfgdir, monkeypatch):
    (cfgdir / "platform.toml").write_text(
        f'[speech]\ntts_model = "omnivoice-cpp"\ntts_voice = "{VOICE_A}"\n', encoding="utf-8")
    calls = []

    class R:
        status_code, content, headers = 200, b"RIFF", {"content-type": "audio/wav"}

        def json(self):
            return {"data": [{"name": "Voice B", "language": "it-IT", "voice": VOICE_B,
                              "created_at": "2026-10-16T22:20:25Z"},
                             {"name": "Voice A", "language": "it-IT", "voice": VOICE_A,
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
    assert [v["name"] for v in got["voices"]] == ["Voice A", "Voice B"], "in the order they were recorded"
    assert got["default"] == VOICE_A, "none chosen: the node's own"
    from app import config
    config.ADMIN_TOKEN, was = "sekrit", config.ADMIN_TOKEN
    try:
        assert TestClient(api.app).get("/ask/voices", headers={"X-Admin-Token": "sekrit"}).json() == got
    finally:
        config.ADMIN_TOKEN = was


def test_the_readers_voice_speaks_and_one_no_longer_held_falls_back_to_the_nodes(two_voices):
    calls = two_voices
    assert speech.synthesize({"input": "Dante.", "voice": VOICE_B}, url="http://ai")[0] == 200
    assert calls[-1]["voice"] == VOICE_B, "the reader's choice"
    speech.synthesize({"input": "Dante.", "voice": "localai://voice-profiles/gone"}, url="http://ai")
    assert calls[-1]["voice"] == VOICE_A, "a voice the server no longer holds: the node's, not an error"
    speech.synthesize({"input": "Dante."}, url="http://ai")
    assert calls[-1]["voice"] == VOICE_A


def test_no_answer_read_aloud_no_voice_to_choose(cfgdir):
    assert voice.voices() == {"voices": [], "default": None, "account": None}


def test_the_voice_choice_is_on_the_page_only_where_the_answer_is_read(isolated_config, config_dir):
    assert 'id="voice-pick"' not in page(config_dir, "")
    html = page(config_dir, '[speech]\ntts_model = "omnivoice-cpp"\n')
    settings = html[html.index('<details class="settings">'):]
    assert 'id="voice-pick"' in settings[:settings.index("</details>")], "under the Ask page's Settings"


# ---- the account's voice (operator, 2026-11-11: "Shall a user account can choose it?";
#      2026-11-16: a recorded voice is an admin's, so an ADMIN account)

def test_an_account_keeps_its_voice_and_every_client_of_it_hears_it(two_voices, monkeypatch):
    from app import config, users
    calls = two_voices
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    _, token = users.add("ada", role="admin")
    c, h = TestClient(api.app), {"Authorization": f"Bearer {token}"}
    assert c.get("/ask/voices", headers=h).json()["account"] == {"name": "ada", "voice": None}
    r = c.post("/ask/voice", headers=h, json={"voice": VOICE_B})
    assert r.status_code == 200 and r.json()["account"] == {"name": "ada", "voice": VOICE_B}
    assert f'voice = "{VOICE_B}"' in users.path().read_text(), "kept with the account, in users.toml"
    assert c.post("/v1/audio/speech", headers=h, json={"input": "Dante."}).status_code == 200
    assert calls[-1]["voice"] == VOICE_B, "a client naming no voice hears the account's"
    c.post("/v1/audio/speech", headers=h, json={"input": "Dante.", "voice": VOICE_A})
    assert calls[-1]["voice"] == VOICE_A, "a request naming one is read in it"
    assert c.post("/ask/voice", headers=h, json={"voice": "localai://voice-profiles/nope"}).status_code == 400
    assert users.voice_of("ada") == VOICE_B, "a voice the node does not offer is refused, nothing kept"
    assert c.post("/ask/voice", headers=h, json={"voice": None}).json()["account"]["voice"] is None
    c.post("/v1/audio/speech", headers=h, json={"input": "Dante."})
    assert calls[-1]["voice"] == VOICE_A, "none chosen: the node's"


def test_no_account_no_voice_kept_by_the_node_and_one_since_removed_is_the_nodes(two_voices, monkeypatch):
    from app import config, users
    calls = two_voices
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    c = TestClient(api.app)
    assert c.get("/ask/voices").json()["account"] is None, "a guest: the browser keeps it"
    assert c.post("/ask/voice", json={"voice": VOICE_B}).status_code == 409
    assert c.post("/ask/voice", headers={"X-Admin-Token": "sekrit"}, json={"voice": VOICE_B}).status_code == 409, \
        "the admin token is no account"
    _, token = users.add("ada", role="admin")
    users.set_voice("ada", "localai://voice-profiles/gone")
    h = {"Authorization": f"Bearer {token}"}
    assert c.get("/ask/voices", headers=h).json()["account"] == {"name": "ada", "voice": None}
    c.post("/v1/audio/speech", headers=h, json={"input": "Dante."})
    assert calls[-1]["voice"] == VOICE_A, "a voice the server no longer holds: the node's, never an error"


# ---- the microphone's report (operator, 2026-11-11: "when the phone is connected to the car the mic of louise does not work")

def test_a_recordings_report_is_one_line_in_the_nodes_log(isolated_config, monkeypatch):
    from app import model_pins
    lines = []
    monkeypatch.setattr(model_pins, "node_event", lambda level, text: lines.append((level, text)))
    c = TestClient(api.app)
    r = c.post("/ask/voice/report", json={"outcome": "silent", "label": "Car kit", "rate": 16000, "ctx_rate": 16000,
                                          "frames": 750, "peak": 0, "heard": False, "audio": "AAAA", "token": "x"})
    assert r.status_code == 204
    (level, text), = lines
    assert level == "warn" and text.startswith("voice: microphone silent (guest) — ")
    said = json.loads(text.split(" — ", 1)[1])
    assert said == {"label": "Car kit", "rate": 16000, "ctx_rate": 16000, "frames": 750, "peak": 0, "heard": False}, \
        "the known keys only: never the audio, never anything else a client sends"
    c.post("/ask/voice/report", json={"outcome": "heard", "chars": 28, "mode": "recorder", "parts": 7,
                                      "mime": "audio/webm;codecs=opus", "ctx_was": "running", "decode_fails": 0})
    assert lines[-1][0] == "info", "a question heard is no warning"
    assert json.loads(lines[-1][1].split(" — ", 1)[1]) == {
        "chars": 28, "mode": "recorder", "parts": 7, "mime": "audio/webm;codecs=opus", "ctx_was": "running",
        "decode_fails": 0}, "which way recorded, and what"
    assert c.post("/ask/voice/report", content=b"x" * 5000).status_code == 413
    assert c.post("/ask/voice/report", json=[1]).status_code == 400
    assert len(lines) == 2


def test_every_field_the_page_reports_is_one_the_node_keeps():
    """The node keeps the known keys only; a field the page adds and the node
    drops is a report that silently says less than it was built to."""
    from pathlib import Path
    from app import web
    js = (Path(web.__file__).parent / "static" / "voice.js").read_text(encoding="utf-8")
    block = js[js.index("function micInfo(r)"):js.index("function report(")]
    sent = set(re.findall(r"(\w+):", block.split("return {", 1)[1]))
    sent |= {"stop", "vad", "chars"}            # added where the clip is written down
    assert sent <= set(web.VOICE_REPORT_KEYS), sorted(sent - set(web.VOICE_REPORT_KEYS))


# ---- a reading's times in the node's log (operator, on a phone, 2026-11-25: "the voice starts very late
# after the answer begins" — and a phone has no console to read `louiseVoice.timeline()` from)

READING = {"first_word": 2400, "last_word": 9100, "started": [2450], "taps": [], "by_itself": True, "blocked": False,
           "count": 7, "lang": "it", "ua": "Mozilla/5.0 (Linux; Android 15) Chrome/142 Mobile",
           "sentences": [{"chars": 62, "cut": 3100, "sent": 3110, "got": 6000, "synth": 2510, "audio": 4100,
                          "turn": 0, "node": 2700, "cache": None,
                          "play": 6050, "end": 10150, "text": "Dante nacque a Firenze."},
                         {"chars": 80, "cut": 3900, "sent": 6010, "got": 8700, "synth": 2600, "audio": 5200,
                          "play": 11400, "end": 16600}]}


def test_a_readings_times_are_one_line_saying_where_the_wait_went(isolated_config, monkeypatch):
    from app import model_pins
    lines = []
    monkeypatch.setattr(model_pins, "node_event", lambda level, text: lines.append((level, text)))
    c = TestClient(api.app)
    assert c.post("/ask/voice/timeline", json=READING).status_code == 204
    (level, text), = lines
    assert level == "info" and text.startswith("voice: reading (guest, it) — ")
    assert "the answer's first word 2.4 s after Ask" in text and "the reading started at 2.5 s (by itself)" in text
    assert "FIRST SOUND at 6.0 s, 3.6 s after the first word" in text
    assert ("sentence 1 (62 chars): cut 0.7 s after the first word, asked for 0.0 s later, here 2.9 s after that "
            "(the node made it in 2.5 s, 4.1 s of sound), played 0.1 s after it arrived") in text
    assert "the longest silence between two sentences 1.2 s" in text and "7 sentence(s)" in text
    assert "Dante" not in text and "Firenze" not in text, "times only: never a word of the answer"
    numbers = json.loads(text.split(" — ")[2])
    assert numbers["first_word"] == 2400 and numbers["sentences"][0] == {
        "chars": 62, "cut": 3100, "sent": 3110, "got": 6000, "synth": 2510, "audio": 4100, "turn": 0, "node": 2700,
        "cache": None, "play": 6050, "end": 10150}
    assert text.endswith("Chrome/142 Mobile")


def test_a_reading_that_played_nothing_says_so(isolated_config, monkeypatch):
    from app import model_pins
    lines = []
    monkeypatch.setattr(model_pins, "node_event", lambda level, text: lines.append((level, text)))
    c = TestClient(api.app)
    silent = {**READING, "blocked": True, "by_itself": False, "taps": [5000], "started": [5000],
              "sentences": [{"chars": 62, "cut": 3100, "sent": 3110, "got": 6000, "synth": 2510, "audio": 4100}]}
    assert c.post("/ask/voice/timeline", json=silent).status_code == 204
    text = lines[-1][1]
    assert "THE BROWSER DID NOT LET THE PAGE PLAY: nothing was heard until a tap" in text and "(a tap)" in text
    tried = {**READING, "blocked": True}            # it tried: the page's `play` time is the attempt, not a sound
    assert c.post("/ask/voice/timeline", json=tried).status_code == 204
    assert "THE BROWSER DID NOT LET THE PAGE PLAY (tried at 6.0 s)" in lines[-1][1] and "FIRST SOUND" not in lines[-1][1]
    quiet = {**READING, "sentences": [{"chars": 62, "cut": 3100, "sent": 3110}]}
    assert c.post("/ask/voice/timeline", json=quiet).status_code == 204
    assert "NO SOUND WAS PLAYED" in lines[-1][1]
    assert c.post("/ask/voice/timeline", json={"sentences": "x" * 10, "first_word": "soon", "ua": 7}).status_code == 204
    assert "the answer's first word ? after Ask" in lines[-1][1], "what is not a number is not a time"
    assert c.post("/ask/voice/timeline", content=b"x" * 9000).status_code == 413
    assert c.post("/ask/voice/timeline", json=[1]).status_code == 400


def test_the_page_sends_a_readings_times_once_when_it_ends():
    from pathlib import Path
    from app import web
    js = (Path(web.__file__).parent / "static" / "voice.js").read_text(encoding="utf-8")
    block = js[js.index("function reportReading(r)"):js.index("function stopReading()")]
    assert "r.reported" in block and "'/ask/voice/timeline'" in block and "keepalive: true" in block
    stop = js[js.index("function stopReading()"):js.index("function play(r, url)")]
    assert stop.index("reportReading(r)") < stop.index("reading = null"), "before the reading is let go"
    sent = set(re.findall(r"(\w+):", block.split("const body = {", 1)[1].split("};", 1)[0]))
    assert sent <= {"first_word", "last_word", "started", "taps", "sentences", "count", "by_itself", "blocked",
                    "lang", "ua"}, sent
    assert "text" not in sent and "answer" not in sent


def test_the_line_says_where_the_node_spent_a_sentences_wait(isolated_config, monkeypatch):
    # the Bug session, 2026-11-25: "the delay before the first sound is much more than 3 s … make sure the
    # line also gives the node's side: when the request arrived versus when synthesis started"
    from app import web
    def first(**t):
        row = {"chars": 62, "cut": 3100, "sent": 3110, "got": 14_110, "synth": 2500, "audio": 4100, "turn": 0,
               "node": 2540, "play": 14_150, "end": 18_250, **t}
        return web.reading_said({**READING, "sentences": [row]})[0]
    assert "the node made it in 2.5 s; 8.5 s between the page and the node" in first(), \
        "eleven seconds from asked-for to here and the node had it for 2.5: the rest was on the way"
    assert "the node made it in 2.5 s after 6.0 s waiting its turn" in first(turn=6000, node=8540)
    assert "the node made it in 2.5 s and 8.4 s elsewhere on the node (a thread, a lookup)" in first(node=10_900)
    assert "answered from the node's kept clips" in first(cache=1, synth=None, node=12, got=3200, play=3250)
    assert "elsewhere" not in first() and "waiting its turn" not in first()


def test_the_speech_route_says_how_long_the_node_had_the_request(isolated_config, monkeypatch):
    from app import speech
    monkeypatch.setattr(speech, "synthesize_timed",
                        lambda body, **kw: (200, b"RIFF", "audio/wav", [("turn", 0.0), ("synth", 12.0)]))
    r = TestClient(api.app).post("/v1/audio/speech", json={"input": "Dante."})
    assert r.status_code == 200
    parts = dict(p.strip().split(";dur=") for p in r.headers["server-timing"].split(","))
    assert set(parts) == {"turn", "synth", "node"} and float(parts["node"]) >= 0.0


# ---- the microphone is shown recording when it gives sound (operator, on the car's hands-free, 2026-11-25:
# "most of the time only the last part of the spoken sentence is written down")

def test_the_microphone_turns_red_at_its_first_sound_not_when_it_is_opened():
    from pathlib import Path
    from app import web
    js = (Path(web.__file__).parent / "static" / "voice.js").read_text(encoding="utf-8")
    start = js[js.index("async function start()"):js.index("function wake(r, how)")]
    assert "micState('rec'" not in start, "opening the microphone no longer shows it recording"
    assert ("micState('wake', 'the microphone is connecting (a car or a headset takes a few seconds) — "
            "speak when it turns red')") in start
    assert "const WAKE_MAX_MS = SILENT_MS;" in js, \
        "his car takes about five seconds: the wait ends at the first sound or with the silence rule, not at three"
    assert "setTimeout(() => wake(r, 'timeout'), WAKE_MAX_MS)" in start, "never waited for without a bound"
    hear = js[js.index("function hear(r, buf, rate)"):js.index("// THE SECOND WAY TO RECORD")]
    assert "if (top > 0) { r.live = true; wake(r, 'sound'); } else r.zero_ms += ms;" in hear, \
        "a microphone that is on is never exactly zero; the zeros before it are counted"
    wake = js[js.index("function wake(r, how)"):js.index("function release(r)")]
    assert "if (r.shown) return;" in wake and "micState('rec'" in wake and "r.wake_ms" in wake
    assert "clearTimeout(r.waking)" in js[js.index("function release(r)"):js.index("async function stop()")]
    html = (Path(web.__file__).parent / "templates" / "ask.html").read_text(encoding="utf-8")
    assert "#mic.waking{" in html


def test_the_recordings_report_carries_how_long_the_microphone_took_to_give_sound(isolated_config, monkeypatch):
    from app import model_pins, web
    lines = []
    monkeypatch.setattr(model_pins, "node_event", lambda level, text: lines.append((level, text)))
    c = TestClient(api.app)
    c.post("/ask/voice/report", json={"outcome": "heard", "label": "Car hands-free", "first_ms": 180, "wake_ms": 2350,
                                      "zero_ms": 2170, "woke": "sound", "chars": 9})
    said = json.loads(lines[-1][1].split(" — ", 1)[1])
    assert said == {"label": "Car hands-free", "chars": 9, "first_ms": 180, "wake_ms": 2350, "zero_ms": 2170,
                    "woke": "sound"}, "the car's number: 2.2 s of zeros before the first sound"
    assert {"first_ms", "wake_ms", "zero_ms", "woke"} <= set(web.VOICE_REPORT_KEYS)
