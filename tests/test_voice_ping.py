"""A voice's encoding is made before a reader waits for it (app/voice_ping.py).

Operator, 2026-11-25: "upon voice swap seng a ping to make the model
re-encode", then, narrowing it: "The only voice ping purpose is to do the
re-encode for voices without it cached to avoid the extra delay upon the
first usage".

The speech engine keeps a clip's encoding beside the clip, bound to the clip
and to the engine's build; a voice with none pays it in its first sentence
(0.8 to 3.2 s on the node). A ping is one short synthesis in the voice, sent
ONLY to a voice that has no encoding under this engine with this clip: a
profile new to the node, a clip replaced in place, every set voice once after
the engine changed. It is not a warm and is not sent because a voice was
selected.
"""
from __future__ import annotations

import types

import httpx
import pytest

from app import httpc, model_load, node_state, speech, users, voice, voice_ping
from app.platform_config import SpeechConfig

SERVER = "http://localai:8080"
TTS = "louise-tts-omnivoice"
ENGINE = "louise-sycl-f16-llama-cpp-d531d5d804f1-stack-7c301a7ba437"
NEXT = "louise-sycl-f16-llama-cpp-d658549772ca-stack-7c301a7ba437"
PHILIPPE = speech.PROFILE_PREFIX + "1c834741"
IT, EN, ANNA = (speech.PROFILE_PREFIX + n for n in ("edcdce3a", "aaaa0001", "bbbb0002"))


def _profile(voice_uri: str, name: str, *, created: str, size: int = 1_331_940, ms: int = 27_748,
             updated: str = "2026-11-25T06:33:12Z") -> dict:
    """One entry of GET /api/voice-profiles, as the node's server lists it."""
    return {"id": voice_uri.rsplit("/", 1)[1], "name": name, "voice": voice_uri, "created_at": created,
            "updated_at": updated, "audio": {"duration_ms": ms, "size_bytes": size, "sample_rate": 24000}}


class Node:
    """The model server and the node's settings, as a look meets them."""

    def __init__(self, monkeypatch):
        self.profiles = [_profile(PHILIPPE, "Philippe", created="2026-10-06"),
                         _profile(IT, "Synthetic it C", created="2026-11-25", size=500_000, ms=10_400),
                         _profile(EN, "Synthetic en", created="2026-11-16", size=480_000, ms=10_000)]
        self.settings = SpeechConfig(tts_model=TTS, tts_voice=PHILIPPE, synthetic_voices=f"it={IT}, en={EN}")
        self.loaded, self.engine = {TTS}, ENGINE
        self.accounts: list[str] = []
        self.pings: list[str] = []
        self.answers = True
        self.said: list[tuple[str, str]] = []
        monkeypatch.setattr(voice, "settings_or_none", lambda: self.settings)
        monkeypatch.setattr(voice, "server", lambda: SERVER)
        monkeypatch.setattr(speech, "profiles", lambda url: self.profiles)
        monkeypatch.setattr(model_load, "loaded_models", lambda server, max_age_s=None: frozenset(self.loaded))
        monkeypatch.setattr(model_load, "backend_of",
                            lambda url, model, max_age_s=None: self.engine if model in self.loaded else None)
        monkeypatch.setattr(users, "load", lambda: [types.SimpleNamespace(name=f"u{i}", voice=v, revoked=None)
                                                    for i, v in enumerate(self.accounts)])
        monkeypatch.setattr(speech, "ping", self.ping)

    def ping(self, url, model, voice_uri, timeout=60.0):
        assert (url, model) == (SERVER, TTS)
        self.pings.append(voice_uri)
        return self.answers

    def look(self, **kw):
        self.pings.clear()
        got = voice_ping.look(say=lambda level, text: self.said.append((level, text)), **kw)
        return got, list(self.pings)


@pytest.fixture
def node(monkeypatch):
    return Node(monkeypatch)


def test_each_set_voice_is_pinged_once_and_then_never_again(node):
    got, pinged = node.look()
    assert pinged == [PHILIPPE, IT, EN], "the node's voice, then each language's synthetic voice"
    assert got["engine"] == ENGINE and got["pinged"] == pinged and got["failed"] == []
    with node_state.connect() as c:
        rec = voice_ping.read(c)
    assert rec[PHILIPPE]["engine"] == ENGINE and rec[PHILIPPE]["clip"] == "1331940/27748/2026-11-25T06:33:12Z"
    assert any("Philippe has its encoding under " + ENGINE in text for _, text in node.said)
    assert node.look()[1] == [], "it is not a warm: a voice with its encoding is not spoken to again"
    assert node.look()[1] == []


def test_another_engine_makes_every_kept_encoding_unreadable_so_each_voice_is_pinged_once_more(node):
    node.look()
    node.engine = NEXT                    # the speech model loaded on the next engine
    assert node.look()[1] == [PHILIPPE, IT, EN]
    assert node.look()[1] == []
    node.engine = ENGINE                  # and a rollback is another build again
    assert node.look()[1] == [PHILIPPE, IT, EN]


def test_a_clip_replaced_in_place_under_the_same_id_is_a_new_clip(node):
    # done twice on the node (Philippe's 11 s clip, then his original 27.75 s one): the id does
    # not change, the listing's size, length and updated_at do
    node.look()
    node.profiles[0] = _profile(PHILIPPE, "Philippe", created="2026-10-06", size=528_044, ms=11_000,
                                updated="2026-11-26T09:00:00Z")
    assert node.look()[1] == [PHILIPPE], "that voice alone"
    assert node.look()[1] == []


def test_a_voice_is_not_pinged_because_it_was_selected(node):
    # operator: "The only voice ping purpose is to do the re-encode for voices without it cached"
    node.look()
    node.accounts = [PHILIPPE]            # an admin keeps a voice that already has its encoding
    assert node.look()[1] == []
    node.settings = SpeechConfig(tts_model=TTS, tts_voice=IT, synthetic_voices=f"en={EN}")
    assert node.look()[1] == [], "nor because the node's voice was changed to one it has"


def test_a_profile_new_to_the_node_is_pinged_when_it_is_first_set_or_kept(node):
    node.look()
    node.profiles.append(_profile(ANNA, "Anna", created="2026-11-26", size=600_000, ms=12_500))
    assert node.look()[1] == [], "recorded, used by nobody: nobody's first sentence waits for it"
    node.accounts = [ANNA]                # an admin's account keeps it
    assert node.look()[1] == [ANNA]
    assert node.look()[1] == []


def test_the_voices_looked_at_are_the_profiles_the_node_is_set_to_use(node):
    profiles = node.profiles
    # no voice named: the one a sentence with none asked gets, the first recorded
    s = SpeechConfig(tts_model=TTS, tts_voice=None, synthetic_voices=f"it={IT}")
    assert voice_ping.wanted(s, [], profiles) == [PHILIPPE, IT]
    # a model's own named voice has no clip; a profile the server no longer holds is nobody's;
    # a voice named twice is pinged once
    s = SpeechConfig(tts_model=TTS, tts_voice="alloy", synthetic_voices=f"it={IT}, de={speech.PROFILE_PREFIX}gone")
    assert voice_ping.wanted(s, [IT, EN, EN], profiles) == [IT, EN]


def test_a_ping_never_loads_the_speech_model(node):
    node.loaded = set()
    got, pinged = node.look()
    assert pinged == [] and "not loaded" in got["why"]
    node.settings = SpeechConfig()        # and a node that reads nothing aloud pings nothing
    assert node.look()[1] == []


def test_a_ping_that_fails_is_not_recorded_and_is_asked_again_only_later(node):
    node.answers = False
    got, pinged = node.look(now=1000.0)
    assert pinged == [PHILIPPE, IT, EN] and got["failed"] == pinged and got["pinged"] == []
    assert any(level == "warn" and "did not answer 200" in text for level, text in node.said)
    node.answers = True
    assert node.look(now=1000.0 + voice_ping.RETRY_AFTER_S - 1)[1] == [], "not every minute, for ever"
    assert node.look(now=1000.0 + voice_ping.RETRY_AFTER_S + 1)[1] == [PHILIPPE, IT, EN]
    assert node.look(now=5000.0)[1] == []


def test_a_readers_question_holds_the_card_and_the_rest_waits_for_the_next_look(node):
    asked = iter([False, True])           # a question arrives after the first ping
    got, pinged = node.look(question=lambda: next(asked, True))
    assert pinged == [PHILIPPE] and "question" in got["why"]
    assert node.look()[1] == [IT, EN]


def test_a_server_that_does_not_name_its_backends_still_has_new_clips_pinged(node):
    node.engine = None
    assert node.look()[1] == [PHILIPPE, IT, EN]
    assert node.look()[1] == []


def test_the_records_of_profiles_the_server_no_longer_holds_go(node):
    node.look()
    node.profiles = [p for p in node.profiles if p["voice"] != EN]
    node.engine = NEXT
    node.look()
    with node_state.connect() as c:
        assert set(voice_ping.read(c)) == {PHILIPPE, IT}


# ---------------- the beat ----------------

def test_the_beat_looks_at_most_once_a_minute_and_only_where_the_node_preloads(node, monkeypatch):
    runs: list = []
    start = lambda run: (runs.append(1), run())          # noqa: E731 — the look, on this thread
    assert voice_ping.beat(preloads=False, start=start) is False, "a paused node's card is the operator's"
    assert voice_ping.beat(preloads=True, question=lambda: True, start=start) is False
    assert voice_ping.beat(preloads=True, start=start) is True and node.pings == [PHILIPPE, IT, EN]
    assert voice_ping.beat(preloads=True, start=start) is False, "CHECK_EVERY_S has not passed"
    monkeypatch.setattr(voice_ping, "_looked", -1e9)
    node.engine = NEXT
    assert voice_ping.beat(preloads=True, start=start) is True and len(runs) == 2


def test_one_look_at_a_time(node, monkeypatch):
    held: list = []
    assert voice_ping.beat(preloads=True, start=held.append) is True      # started, not finished
    monkeypatch.setattr(voice_ping, "_looked", -1e9)
    assert voice_ping.beat(preloads=True, start=held.append) is False, "the first look is still out"
    held[0]()
    monkeypatch.setattr(voice_ping, "_looked", -1e9)
    assert voice_ping.beat(preloads=True, start=held.append) is True


# ---------------- the wire ----------------

def test_the_ping_is_one_short_synthesis_in_the_voice(monkeypatch):
    sent: list = []

    def post(url, json=None, timeout=None, **kw):
        sent.append((url, json))
        return httpx.Response(sent[-1][1]["status"] if "status" in json else 200,
                              request=httpx.Request("POST", url), content=b"RIFF")
    monkeypatch.setattr(httpc, "post", post)
    assert speech.ping(SERVER, TTS, PHILIPPE) is True
    assert sent == [(f"{SERVER}/v1/audio/speech", {"model": TTS, "input": "OK", "voice": PHILIPPE})]
    monkeypatch.setattr(httpc, "post", lambda url, **kw: httpx.Response(404, request=httpx.Request("POST", url)))
    assert speech.ping(SERVER, TTS, PHILIPPE) is False, "a profile the server no longer holds"
    monkeypatch.setattr(httpc, "post", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert speech.ping(SERVER, TTS, PHILIPPE) is False


def test_the_engine_is_the_backend_the_loaded_speech_model_runs_on(monkeypatch):
    # GET /system, as the node's server answers it (the P1 patch's listing)
    rows = [{"id": TTS, "backend": ENGINE, "process": {"pid": 4242}, "size_vram": 2_266 << 20},
            {"id": "louise-stt", "process": {"pid": 4243}}]
    monkeypatch.setattr(httpc, "get", lambda url, timeout=None, **kw: httpx.Response(
        200, request=httpx.Request("GET", url), json={"loaded_models": rows}))
    monkeypatch.setattr(model_load, "_fetch_system", REAL_FETCH)
    model_load.reset()
    assert model_load.backend_of(f"{SERVER}/v1/audio/speech", TTS) == ENGINE
    assert model_load.backend_of(SERVER, "louise-stt") is None, "a listing that names none for it"
    assert model_load.backend_of(SERVER, "not-loaded") is None, "only a loaded model has one"


REAL_FETCH = model_load._fetch_system
