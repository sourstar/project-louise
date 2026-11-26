"""A recorded voice is the admin's only (operator, 2026-11-16: "limit the
cloned voices to the admin account … For the users account allow only
synthetic voices according to the language spoken"). An admin — the admin
token, or a user of role admin — chooses among the node's recorded voices;
every other reader hears the synthetic voice the node names for the
answer's language (`[speech] synthetic_voices`), chooses none, and is never
read to in a recorded voice, whatever the request or the account names."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api, config, platform_config, speech, users, voice

RECORDED = "localai://voice-profiles/aaaa1111"
SYNTH_IT = "localai://voice-profiles/5e1e0001"
SYNTH_EN = "localai://voice-profiles/5e1e0002"
TABLE = (f'[speech]\ntts_model = "omnivoice-cpp"\ntts_voice = "{RECORDED}"\n'
         f'synthetic_voices = "it={SYNTH_IT}, en-GB={SYNTH_EN}"\n')
ADMIN = {"X-Admin-Token": "sekrit"}
IT = "La guerra è finita e il poeta scrive della sua città con amore."
EN = "The war is over and the poet writes about the city that he loved."
FR = "La guerre est finie et le poète écrit sur la ville qui est la sienne pour toujours."


@pytest.fixture
def node(isolated_config, config_dir, monkeypatch):
    (config_dir / "platform.toml").write_text(TABLE, encoding="utf-8")
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    calls: list[dict] = []
    held = [{"name": "Recorded", "voice": RECORDED, "created_at": "2026-10-06"},
            {"name": "Synthetic it", "voice": SYNTH_IT, "created_at": "2026-11-16"},
            {"name": "Synthetic en", "voice": SYNTH_EN, "created_at": "2026-11-16"}]

    class R:
        status_code, content, headers = 200, b"RIFF", {"content-type": "audio/wav"}

        def json(self):
            return {"data": held}
    monkeypatch.setattr(speech.httpc, "get", lambda url, **kw: R())
    monkeypatch.setattr(speech.httpc, "post", lambda url, json, **kw: calls.append(json) or R())
    monkeypatch.setattr(speech.model_pins, "hold", lambda url, model, holder: True)
    monkeypatch.setattr(speech.model_pins, "release", lambda holder: None)
    monkeypatch.setattr(speech, "_profiles", (0.0, []))
    monkeypatch.setattr(speech, "_clip_get", lambda key: None)
    monkeypatch.setattr(speech, "_clip_put", lambda key, data, ctype: None)
    return calls, held, config_dir


def test_the_synthetic_voices_are_read_by_language_and_a_wrong_entry_is_refused(node, config_dir):
    assert voice.synthetic() == {"it": SYNTH_IT, "en": SYNTH_EN}, "a language by its primary tag"
    for bad, says in (('"it"', "not language=voice"), ('"it=a, it=b"', "given twice"),
                      ('"italiano=a"', "not language=voice"), ('"it="', "not language=voice")):
        (config_dir / "platform.toml").write_text(f"[speech]\nsynthetic_voices = {bad}\n", encoding="utf-8")
        with pytest.raises(platform_config.schedule_mod.ScheduleError, match=says):
            platform_config.load()


def test_a_reader_who_is_not_an_admin_hears_the_synthetic_voice_of_the_language(node):
    calls, _, _ = node
    for text, want in ((IT, SYNTH_IT), (EN, SYNTH_EN), (FR, SYNTH_IT)):
        assert speech.synthesize({"input": text, "voice": RECORDED}, url="http://ai", admin=False)[0] == 200
        assert calls[-1]["voice"] == want, (text, "the language's voice, the first named for one with none")
    speech.synthesize_timed({"input": IT}, url="http://ai", account_voice=RECORDED, admin=False)
    assert calls[-1]["voice"] == SYNTH_IT, "an account's recorded voice is not used either"
    # THE SENTENCE'S OWN LANGUAGE, NOT THE REQUEST'S TAG (operator, 2026-11-25: "the language/country
    # tag must be aligned to the language to speak, otherwise the voice has always a thick italian
    # accent"): the page sends the language the answer was ASKED in; an English sentence is English
    speech.synthesize({"input": EN, "language": "it-IT"}, url="http://ai", admin=False)
    assert calls[-1]["voice"] == SYNTH_EN and calls[-1]["language"] == "en", "voice and tag: the sentence's"
    # the tag is what is left for a sentence too short to tell — as a LANGUAGE, never with its country
    speech.synthesize({"input": "Dante, 1321.", "language": "it-IT"}, url="http://ai", admin=False)
    assert calls[-1]["voice"] == SYNTH_IT and calls[-1]["language"] == "it"
    speech.synthesize({"input": "Dante, 1321.", "language": "en_GB"}, url="http://ai", admin=False)
    assert calls[-1]["voice"] == SYNTH_EN and calls[-1]["language"] == "en"
    # an admin: as before, the voice asked for, else the node's
    speech.synthesize({"input": IT, "voice": RECORDED}, url="http://ai")
    assert calls[-1]["voice"] == RECORDED
    assert all(c["voice"] != RECORDED for c in calls[:-1])


def test_a_german_sentence_with_few_common_words_is_read_in_the_german_voice(node, config_dir):
    """München, 2026-11-24, #721's first hour live: this sentence had no
    language (twenty words a language were known, "das" the only one here),
    so the first voice named read it — the Italian speaker. The page's
    `context` still names the language of a sentence too short to tell."""
    calls, held, _ = node
    synth_de = "localai://voice-profiles/5e1e0003"
    held.append({"name": "Synthetic de", "voice": synth_de, "created_at": "2026-11-24"})
    (config_dir / "platform.toml").write_text(
        TABLE.replace(f"en-GB={SYNTH_EN}", f"en-GB={SYNTH_EN}, de={synth_de}"), encoding="utf-8")
    german = "Das Dekret tritt am Tag nach seiner Veröffentlichung im Amtsblatt in Kraft."
    assert speech.synthesize({"input": german}, url="http://ai", admin=False)[0] == 200
    assert calls[-1]["voice"] == synth_de and calls[-1]["language"] == "de"
    speech.synthesize({"input": "Goethe, 1749.", "context": german}, url="http://ai", admin=False)
    assert calls[-1]["voice"] == synth_de, "too short to tell: the answer it is part of says"
    speech.synthesize({"input": "Goethe, 1749."}, url="http://ai", admin=False)
    assert calls[-1]["voice"] == SYNTH_IT, "nothing to tell it by: the first named, as designed"


def test_the_steps_of_a_synthesis_are_the_nodes_and_go_in_every_request(node, config_dir):
    """Operator, 2026-11-25: "add the steps to the admin settings". `[speech]
    tts_steps` is `params.flow_steps` of the request to the model, for an
    admin and for every other reader, over whatever the client sent; unset,
    no such parameter is sent and the model's own default stands."""
    calls, _, _ = node
    speech.synthesize({"input": IT}, url="http://ai", admin=False)
    assert "params" not in calls[-1], "unset: the model's own default, nothing sent"
    (config_dir / "platform.toml").write_text(TABLE + 'tts_steps = "24"\n', encoding="utf-8")
    speech.synthesize({"input": IT}, url="http://ai", admin=False)
    assert calls[-1]["params"] == {"flow_steps": "24"}
    speech.synthesize({"input": IT, "voice": RECORDED, "params": {"flow_steps": "64", "seed": "7"}}, url="http://ai")
    assert calls[-1]["params"] == {"flow_steps": "24", "seed": "7"}, "the node's steps, the client's other parameters kept"
    assert voice.settings().tts_steps == "24" and platform_config.tts_steps("24") == 24


def test_a_readings_first_sentence_is_made_at_half_the_steps(node, config_dir):
    """Operator, 2026-11-26: the first sentence at half the steps, the rest
    at full. Sentence 0 of a reading only — half the node's steps, or half the
    model's own default where the node sets none (his words: "the half of the
    samples"); a request outside a reading, and every later sentence, keep them."""
    calls, _, _ = node
    speech.synthesize_timed({"input": IT}, url="http://ai", reading="r0", n=0, admin=False)
    assert calls[-1]["params"] == {"flow_steps": "16"}, "unset: half the model's own 32"
    speech.synthesize_timed({"input": IT + " Due."}, url="http://ai", reading="r0", n=1, admin=False)
    assert calls[-1]["params"] == {"flow_steps": "20"}, "unset: the ramp still climbs to the model's own"
    speech.synthesize_timed({"input": IT + " Cinque."}, url="http://ai", reading="r0", n=4, admin=False)
    assert "params" not in calls[-1], "unset and ramped up: the model's own default, nothing sent"
    (config_dir / "platform.toml").write_text(TABLE + 'tts_steps = "24"\n', encoding="utf-8")
    speech.synthesize_timed({"input": IT + " Tre."}, url="http://ai", reading="r2", n=0, admin=False)
    assert calls[-1]["params"] == {"flow_steps": "12"}
    speech.synthesize_timed({"input": IT + " Tre."}, url="http://ai", reading="r2", n=1, admin=False)
    assert calls[-1]["params"] == {"flow_steps": "16"}, "the same words later in a reading: made again, ramped"
    speech.synthesize_timed({"input": IT + " Tre."}, url="http://ai", reading="r2", n=9, admin=False)
    assert calls[-1]["params"] == {"flow_steps": "24"}, "and at full once ramped up"
    speech.synthesize({"input": IT + " Quattro."}, url="http://ai", admin=False)
    assert calls[-1]["params"] == {"flow_steps": "24"}, "outside a reading: full"
    assert speech.first_steps(4) == 4 and speech.first_steps(5) == 4, "never under the lowest accepted"
    assert speech.first_steps(None) == 16
    # operator, 2026-11-26: "increase the encoding at steps of 4 (16, 20, 24, 28, 32)"
    assert [speech.reading_steps(n, 32) for n in range(6)] == [16, 20, 24, 28, 32, 32]
    assert [speech.reading_steps(n, None) for n in range(6)] == [16, 20, 24, 28, 32, 32]
    assert [speech.reading_steps(n, 24) for n in range(4)] == [12, 16, 20, 24]
    _, _, _, timing = speech.synthesize_timed({"input": IT + " Sei."}, url="http://ai", reading="r3", n=2,
                                              admin=False)
    assert ("steps", 20.0) in timing, "the steps a clip was made at, in its Server-Timing"


def test_the_clip_key_tells_half_steps_from_full():
    """The kept clip's key holds the steps: a half-step first sentence is
    never served for the same words at full steps."""
    out = {"model": "m", "voice": "v", "input": IT}
    half = speech._clip_key("http://ai", {**out, "params": {"flow_steps": "16"}}, "engine-1")
    full = speech._clip_key("http://ai", {**out, "params": {"flow_steps": "32"}}, "engine-1")
    assert half != full and full == speech._clip_key("http://ai", {**out, "params": {"flow_steps": "32"}}, "engine-1")


def _wav(ms: int, rate: int = 24000) -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x01\x00" * (rate * ms // 1000))
    return buf.getvalue()


def test_a_quotation_in_another_language_is_spoken_in_it(node, monkeypatch):
    """Operator, 2026-11-26: "words in quote marks that are in a different
    language and the pronunciation is not in that language". The quotation,
    where it reads clearly as another language, is its own segment in its
    language; the rest in the sentence's; one clip, joined."""
    calls, _, _ = node

    class W:
        status_code, headers = 200, {"content-type": "audio/wav"}
        content = _wav(500)
    monkeypatch.setattr(speech.httpc, "post", lambda url, json, **kw: calls.append(json) or W())
    s = 'Il motto dei moschettieri era "Un pour tous, tous pour un", scritto da Dumas nel romanzo.'
    status, content, ctype, timing = speech.synthesize_timed({"input": s}, url="http://ai", reading="q1", n=0)
    sent = calls[-3:]
    assert status == 200 and [c["language"] for c in sent] == ["it", "fr", "it"]
    assert sent[1]["input"].startswith('"Un pour tous')
    assert all(c["params"] == {"flow_steps": "16"} for c in sent), "each at the piece's steps (n=0: 16)"
    assert ("segments", 3.0) in timing and ("steps", 16.0) in timing
    assert 1500 <= speech.audio_ms(content) <= 1700, "three clips of 500 ms and two gaps"
    # one word, or the sentence's own language: the sentence goes whole
    before = len(calls)
    speech.synthesize_timed({"input": 'Disse «addio» e partì da casa per sempre.'}, url="http://ai")
    speech.synthesize_timed({"input": 'Disse «resta con me, amico mio» e partì da casa.'}, url="http://ai")
    assert len(calls) == before + 2


def test_a_quotations_neighbours_keep_the_sentences_language_and_punctuation_is_no_call(node, monkeypatch):
    """Architecture's read, 2026-11-26: the lead-in and the tail were left to
    `context`, which holds the quotation ("per tutti" spoken English in the
    English voice); a "." after the guillemets was a third call; a format the
    node cannot join turned the sentence into a 502."""
    calls, _, _ = node

    class W:
        status_code, headers = 200, {"content-type": "audio/wav"}
        content = _wav(300)
    monkeypatch.setattr(speech.httpc, "post", lambda url, json, **kw: calls.append(json) or W())
    s = 'Il giudice ha detto che «the law is the law and it must be obeyed» per tutti.'
    speech.synthesize_timed({"input": s, "context": "«the law is the law and it must be obeyed»"}, url="http://ai",
                            admin=False)
    sent = calls[-3:]
    assert [c["language"] for c in sent] == ["it", "en", "it"] and "context" not in sent[2]
    assert sent[0]["voice"] == SYNTH_IT and sent[2]["voice"] == SYNTH_IT, "the sentence's voice, not the quotation's"
    before = len(calls)
    speech.synthesize_timed({"input": 'Il motto era «Un pour tous, tous pour un».', "language": "it"}, url="http://ai")
    assert len(calls) == before + 2 and calls[-1]["input"].endswith("».") , "the stop goes with the quotation"
    # nothing says the sentence's language: nothing to tell a quotation from, it goes whole
    assert speech.quote_segments('Tom «Un pour tous, tous pour un»', None) == []


def test_a_quotation_the_node_cannot_join_goes_as_one_sentence(node, monkeypatch):
    calls, _, _ = node

    class Mp3:
        status_code, headers, content = 200, {"content-type": "audio/mpeg"}, b"ID3..."
    monkeypatch.setattr(speech.httpc, "post", lambda url, json, **kw: calls.append(json) or Mp3())
    s = 'Il motto dei moschettieri era "Un pour tous, tous pour un", scritto da Dumas nel romanzo.'
    before = len(calls)
    status, content, ctype, _ = speech.synthesize_timed({"input": s, "response_format": "mp3"}, url="http://ai")
    assert status == 200 and len(calls) == before + 1, "another format: never segmented"
    before = len(calls)
    status, content, ctype, _ = speech.synthesize_timed({"input": s}, url="http://ai")
    assert status == 200 and content == b"ID3..." and calls[-1]["input"].startswith("Il motto"), \
        "clips that do not join: the sentence again, as one request, never a 502"


def test_a_kept_clip_takes_its_turn_in_order(node, monkeypatch):
    """A cache hit returns before the model's call; it still TAKES ITS TURN:
    marked done (unmarked, sentence n+1 waited TURN_ARRIVE_S for it), and
    only after n-1 is done (marked at once, n+1 overtook an n-1 still at the
    model — architecture, 2026-11-26)."""
    import threading
    import time
    turns = speech.Turns()
    monkeypatch.setattr(speech, "_TURNS", turns)
    monkeypatch.setattr(speech, "_clip_get", lambda key: (b"RIFF", "audio/wav"))
    order: list[str] = []
    entered = threading.Event()

    def sentence0():
        with turns.turn("rk", 0):
            entered.set()
            time.sleep(0.3)                     # n=0 still at the model
            order.append("0")
    t = threading.Thread(target=sentence0)
    t.start()
    entered.wait(2)
    t0 = time.monotonic()
    speech.synthesize_timed({"input": IT}, url="http://ai", reading="rk", n=1, admin=False)
    order.append("1")
    t.join()
    assert order == ["0", "1"], "the kept clip waits for the sentence before it"
    t1 = time.monotonic()
    with turns.turn("rk", 2) as waited:
        pass
    assert time.monotonic() - t1 < 0.5, "and is marked done: the next one does not wait"


def test_steps_that_are_not_a_number_in_range_are_refused_at_load(node, config_dir):
    for bad in ('"many"', '"3"', '"65"', '"2.5"', '"-8"'):
        (config_dir / "platform.toml").write_text(f"[speech]\ntts_steps = {bad}\n", encoding="utf-8")
        with pytest.raises(platform_config.schedule_mod.ScheduleError, match="tts_steps"):
            platform_config.load()
    for ok in ('"4"', '"32"', '"64"'):
        (config_dir / "platform.toml").write_text(f"[speech]\ntts_steps = {ok}\n", encoding="utf-8")
        assert platform_config.load().speech.tts_steps == ok.strip('"')


def test_a_synthetic_voice_no_longer_held_gives_way_and_none_held_reads_nothing(node, config_dir):
    calls, held, _ = node
    held[:] = [h for h in held if h["voice"] != SYNTH_IT]
    speech.synthesize({"input": IT}, url="http://ai", admin=False)
    assert calls[-1]["voice"] == SYNTH_EN, "the next one held"
    (config_dir / "platform.toml").write_text(
        f'[speech]\ntts_model = "omnivoice-cpp"\ntts_voice = "{RECORDED}"\n', encoding="utf-8")
    n = len(calls)
    status, body, _ = speech.synthesize({"input": IT, "voice": RECORDED}, url="http://ai", admin=False)
    assert status == 409 and b"admin only" in body and len(calls) == n, "never the recorded voice"


def test_the_routes_read_to_each_reader_by_role(node):
    calls, _, _ = node
    c = TestClient(api.app)
    _, user = users.add("bea")
    _, admin_user = users.add("ada", role="admin")
    as_user, as_admin_user = {"Authorization": f"Bearer {user}"}, {"Authorization": f"Bearer {admin_user}"}
    for h, want in ((as_user, SYNTH_IT), ({}, None), (ADMIN, RECORDED), (as_admin_user, RECORDED)):
        r = c.post("/v1/audio/speech", headers=h, json={"input": IT, "voice": RECORDED})
        if want is None:
            assert r.status_code == 401, "a guest, once a user exists: nobody"
            continue
        assert r.status_code == 200 and calls[-1]["voice"] == want, (h, calls[-1])
    users.set_voice("bea", RECORDED)                       # kept before 2026-11-16: not used
    c.post("/v1/audio/speech", headers=as_user, json={"input": EN})
    assert calls[-1]["voice"] == SYNTH_EN


def test_a_user_has_no_voice_to_choose_and_cannot_keep_one(node):
    c = TestClient(api.app)
    _, token = users.add("bea")
    h = {"Authorization": f"Bearer {token}"}
    got = c.get("/ask/voices", headers=h).json()
    assert got == {"voices": [], "default": None, "account": {"name": "bea", "voice": None},
                   "by_language": ["it", "en"]}
    assert c.post("/ask/voice", headers=h, json={"voice": RECORDED}).status_code == 403
    assert c.post("/ask/voice", headers=h, json={"voice": SYNTH_IT}).status_code == 403
    assert users.voice_of("bea") is None
    assert c.post("/ask/voice", headers=h, json={"voice": None}).status_code == 200
    names = [v["name"] for v in c.get("/ask/voices", headers=ADMIN).json()["voices"]]
    assert names == ["Recorded", "Synthetic it", "Synthetic en"], "an admin chooses among them all"


def test_a_guest_is_offered_the_answer_read_aloud_only_with_a_synthetic_voice(node, config_dir):
    c = TestClient(api.app)
    assert c.get("/ask/voice").json()["output"]["available"] is True
    assert 'id="speak"' in c.get("/ask").text
    (config_dir / "platform.toml").write_text('[speech]\ntts_model = "omnivoice-cpp"\n', encoding="utf-8")
    out = c.get("/ask/voice").json()["output"]
    assert out["available"] is False and "--synthetic" in out["why"] and "admin only" in out["why"]
    assert 'id="speak"' not in c.get("/ask").text, "no button where nothing would be read"
    assert 'id="speak"' in c.get("/ask", headers=ADMIN).text, "an admin is read to in the recorded voice"
    shown = voice.show()
    assert shown["synthetic"] == {} and shown["offered_to_readers"]["available"] is False


def test_every_reader_route_says_who_is_asking():
    """A route that forgot to say would read a recorded voice to anyone (the
    library default is the admin's, for the node's own callers)."""
    root = Path(api.__file__).parent
    watched = {"synthesize_timed", "synthesize", "voices", "offered", "choose"}
    missing = []
    for name in ("api.py", "web.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr in watched
                    and isinstance(call.func.value, ast.Name) and call.func.value.id in ("speech", "voice")
                    and not any(k.arg == "admin" for k in call.keywords)):
                missing.append(f"{name}:{call.lineno} {call.func.value.id}.{call.func.attr}")
    assert not missing, missing


def test_no_recorded_voice_is_named_in_the_repository():
    """Which voices are recorded is the node's word, in its own platform.toml
    (2026-11-16): the code, the docs and the tests name none."""
    import subprocess
    root = Path(api.__file__).resolve().parents[1]
    out = subprocess.run(["git", "grep", "-n", "-i", "-I", "-E", "voice-profiles/1c834741|tools/voices/"],
                         cwd=root, capture_output=True, text=True, check=False)
    hits = [line for line in out.stdout.splitlines() if not line.startswith("tests/test_voice_roles.py:")]
    assert not hits, hits
