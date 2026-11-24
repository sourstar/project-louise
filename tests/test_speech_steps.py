"""Speed against quality is the node's dial (operator, 2026-11-24:
"controlling quality over speed is useful").

`[speech] tts_steps` is how many steps the text-to-speech model decodes a
sentence in (OmniVoice's own: 32; half the steps, about half the time —
docs/notes/omnivoice-on-our-backend.md §11). It is desired state in
platform.toml (invariant 10), written by `louise node speech set --steps N`,
read at every sentence — so it holds from the next one, no restart — and
sent to the model server as `params.flow_steps` (app/speech.py). A request's
own steps are an admin's only: a guest could otherwise ask the card for
1,024 a sentence. A model that has no steps is asked again without them."""
from __future__ import annotations

import re

import pytest

from app import platform_config, speech, voice
from app.platform_config import SpeechConfig
from tests.test_voice import OTHER, cfgdir, no_server, run, two_voices  # noqa: F401 — the fixtures
from tests.test_voice_roles import IT, node  # noqa: F401 — a node with synthetic voices

ScheduleError = platform_config.schedule_mod.ScheduleError


# ---- the setting -------------------------------------------------------------

def test_the_steps_are_read_as_a_number_or_its_digits(cfgdir):
    assert voice.settings().tts_steps is None, "no file: the model's own steps"
    for written, read in (("16", "16"), ('"24"', "24"), ("1024", "1024"), ('" 08 "', "8")):
        (cfgdir / "platform.toml").write_text(
            f'[speech]\ntts_model = "omnivoice-cpp"\ntts_steps = {written}\n', encoding="utf-8")
        assert voice.settings() == SpeechConfig(tts_model="omnivoice-cpp", tts_steps=read)


@pytest.mark.parametrize("written", ["0", "1025", "-4", '"fast"', "true", "1.5", '""', "[16]"])
def test_a_step_count_that_is_no_whole_number_in_range_is_refused_at_load(cfgdir, written):
    (cfgdir / "platform.toml").write_text(f"[speech]\ntts_steps = {written}\n", encoding="utf-8")
    with pytest.raises(ScheduleError, match=re.escape("speech.tts_steps is the number of steps")):
        platform_config.load()


def test_set_writes_the_steps_and_show_says_what_louise_cannot_see(cfgdir, capsys, no_server):
    path = cfgdir / "platform.toml"
    path.write_text(OTHER, encoding="utf-8")
    code, out, _ = run(capsys, "node", "speech", "set", "--tts", "omnivoice-cpp", "--steps", "16")
    assert code == 0 and out["changed"] == ["tts_model", "tts_steps"]
    assert out["speech"]["tts_steps"] == "16"
    text = path.read_text(encoding="utf-8")
    assert text.startswith(OTHER) and "tts_steps = 16\n" in text, "a number in the file, every other line kept"
    assert set(out["listed"]) == {"omnivoice-cpp"}, "a step count is no model to look for on the server"
    assert "params.flow_steps" in out["steps_note"] and "omnivoice-cpp" in out["steps_note"]
    code, out, _ = run(capsys, "node", "speech", "show")
    assert out["speech"]["tts_steps"] == "16" and out["steps_note"] == voice.STEPS_NOTE
    # a wrong one is refused by name, the file untouched
    for bad in ("0", "many", "2000"):
        code, out, err = run(capsys, "node", "speech", "set", "--steps", bad)
        assert code == 2 and out is None and "speech.tts_steps" in err
        assert path.read_text(encoding="utf-8") == text and not list(cfgdir.glob("*.tmp"))
    # an empty value gives the model its own steps again
    code, out, _ = run(capsys, "node", "speech", "set", "--steps", "")
    assert code == 0 and out["changed"] == ["tts_steps"] and "steps_note" not in out
    assert "tts_steps" not in path.read_text(encoding="utf-8") and voice.settings().tts_steps is None


def test_the_command_line_is_the_only_place_that_asks(cfgdir):
    """RULE 2: a choice for the operator lives in the command line. The
    System page's Models panel writes `[speech]`'s models, not this."""
    from app import web
    assert "tts_steps" in platform_config.SPEECH_KEYS and voice.FLAGS["tts_steps"] == "--steps"
    assert "tts_steps" not in [field for field, _, _ in web.MODEL_FIELDS]


# ---- the relay ---------------------------------------------------------------

def test_every_sentence_carries_the_nodes_steps_from_the_next_one_on(two_voices, cfgdir):
    calls, path = two_voices, cfgdir / "platform.toml"
    speech.synthesize({"input": "Dante."}, url="http://ai")
    assert "params" not in calls[-1], "no steps set: the request as it always was"
    voice.write(path, {"tts_steps": "16"})
    speech.synthesize({"input": "Dante."}, url="http://ai")
    assert calls[-1]["params"] == {"flow_steps": "16"}, "a string: what the model server's params hold"
    voice.write(path, {"tts_steps": "8"})          # the dial turned while the node runs
    speech.synthesize({"input": "Dante."}, url="http://ai")
    assert calls[-1]["params"] == {"flow_steps": "8"}
    voice.write(path, {"tts_steps": ""})
    speech.synthesize({"input": "Dante.", "params": {"seed": "0"}}, url="http://ai")
    assert calls[-1]["params"] == {"seed": "0"}, "the request's other parameters untouched"


def test_a_kept_clip_is_one_made_at_the_same_steps(two_voices):
    out = {"input": "Dante.", "model": "omnivoice-cpp"}
    at_16 = speech._clip_key("http://ai", out | {"params": {"flow_steps": "16"}})
    assert at_16 != speech._clip_key("http://ai", out) != speech._clip_key(
        "http://ai", out | {"params": {"flow_steps": "32"}}) != at_16


def test_an_admins_own_steps_are_kept_over_the_nodes(two_voices, cfgdir):
    calls = two_voices
    voice.write(cfgdir / "platform.toml", {"tts_steps": "16"})
    speech.synthesize({"input": "Dante.", "params": {"flow_steps": "4", "seed": "0"}}, url="http://ai")
    assert calls[-1]["params"] == {"flow_steps": "4", "seed": "0"}


def test_a_reader_who_is_not_an_admin_cannot_set_the_steps(node):
    calls, _held, config_dir = node
    asked = {"input": IT, "params": {"flow_steps": "1024", "seed": "0"}}
    speech.synthesize(dict(asked), url="http://ai", admin=False)
    assert calls[-1]["params"] == {"seed": "0"}, "no dial set: the model's own steps, never the reader's"
    speech.synthesize({"input": IT, "params": {"flow_steps": "1024"}}, url="http://ai", admin=False)
    assert "params" not in calls[-1]
    voice.write(config_dir / "platform.toml", {"tts_steps": "16"})
    speech.synthesize(dict(asked), url="http://ai", admin=False)
    assert calls[-1]["params"] == {"seed": "0", "flow_steps": "16"}, "the node's, for every reader"


def test_params_that_are_no_table_go_as_sent_and_nothing_is_invented():
    assert speech.with_steps("flow_steps=4", "16", True) == ("flow_steps=4", False)
    assert speech.with_steps(None, None, True) == (None, False)
    assert speech.with_steps({}, None, False) == (None, False)
    assert speech.with_steps({"seed": 0}, "16", True) == ({"seed": 0, "flow_steps": "16"}, True)


def test_a_model_with_no_steps_is_asked_again_without_them_and_the_log_is_told_once(
        two_voices, cfgdir, monkeypatch):
    calls = two_voices
    voice.write(cfgdir / "platform.toml", {"tts_steps": "16"})
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(speech.model_pins, "node_event", lambda level, text: said.append((level, text)))
    monkeypatch.setattr(speech, "_steps_said", set())
    answer = {"status": 500}

    class Answer:
        headers = {"content-type": "application/json"}

        def __init__(self, status: int, content: bytes):
            self.status_code, self.content = status, content
            if status == 200:
                self.headers = {"content-type": "audio/wav"}

    def post(url, json, **kw):
        calls.append(json)
        if "flow_steps" in (json.get("params") or {}):
            # llama.cpp's own words for a model without steps, as LocalAI relays them
            return Answer(answer["status"], b'{"error":{"message":"rpc error: invalid TTS input: the model may '
                                            b'not support \\"speed\\", \\"loudness\\", \\"cfg_scale\\" or '
                                            b'\\"flow_steps\\", see the server log"}}')
        return Answer(200, b"RIFF")
    monkeypatch.setattr(speech.httpc, "post", post)

    status, audio, _ = speech.synthesize({"input": "Dante.", "params": {"seed": "0"}}, url="http://ai")
    assert status == 200 and audio == b"RIFF", "the sentence is read"
    assert [c.get("params") for c in calls[-2:]] == [{"seed": "0", "flow_steps": "16"}, {"seed": "0"}]
    speech.synthesize({"input": "Virgil."}, url="http://ai")
    assert [c.get("params") for c in calls[-2:]] == [{"flow_steps": "16"}, None]
    assert len(said) == 1 and said[0][0] == "warn", "one line, not one a sentence"
    assert "tts_steps = 16" in said[0][1] and '--steps ""' in said[0][1] and "omnivoice-cpp" in said[0][1]
    # an admin's own steps refused: the server's answer goes back, nothing is asked twice
    before = len(calls)
    status, _, _ = speech.synthesize({"input": "Ovid.", "params": {"flow_steps": "4"}}, url="http://ai")
    assert status == 500 and len(calls) == before + 1 and len(said) == 1


def test_another_failure_is_not_the_dials(two_voices, cfgdir, monkeypatch):
    calls = two_voices
    voice.write(cfgdir / "platform.toml", {"tts_steps": "16"})

    class Busy:
        status_code, content, headers = 503, b'{"error":{"message":"busy"}}', {"content-type": "application/json"}
    monkeypatch.setattr(speech.httpc, "post", lambda url, json, **kw: calls.append(json) or Busy())
    monkeypatch.setattr(speech.model_pins, "node_event", lambda *a: pytest.fail("nothing to say"))
    before = len(calls)
    assert speech.synthesize({"input": "Dante."}, url="http://ai")[0] == 503
    assert len(calls) == before + 1, "asked once: the answer does not name the steps"
