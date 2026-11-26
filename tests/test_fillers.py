"""The phrases that cover the wait (app/fillers.py; docs/notes/hands-free-voice.md
§7, Decision 5A, operator 2026-11-26: "when a query has been answered make
one or two phrases for the next cycle … prepare the phrases in the main 5
languages and per voice. Each time you regenerate the used ones"): the set
per language, one short and one long clip ready per voice and language, a
clip played once and its kind remade fresh, never during a question."""
from __future__ import annotations

import shutil
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api, config, derived, fillers, model_load, speech, users
from app.rage import dispatch

RECORDED = "localai://voice-profiles/aaaa1111"
SYNTH_IT = "localai://voice-profiles/5e1e0001"
SYNTH_EN = "localai://voice-profiles/5e1e0002"
TABLE = (f'[speech]\ntts_model = "omnivoice-cpp"\ntts_voice = "{RECORDED}"\n'
         f'synthetic_voices = "it={SYNTH_IT}, en={SYNTH_EN}"\n')
ADMIN = {"X-Admin-Token": "sekrit"}
ENGINE = "louise-sycl-f16-llama-cpp-d531d5d804f1-stack-7c301a7ba437"
OTHER = "louise-sycl-f16-llama-cpp-d658549772ca-stack-7c301a7ba437"


def _profile(voice: str, name: str, created: str, updated: str = "2026-11-25T06:33:12Z") -> dict:
    """One entry of GET /api/voice-profiles, as the node's server lists it."""
    return {"id": voice.rsplit("/", 1)[1], "name": name, "voice": voice, "created_at": created,
            "updated_at": updated, "audio": {"duration_ms": 10_400, "size_bytes": 500_000, "sample_rate": 24000}}


class Node:
    """The model server and the node's settings, as the pool meets them."""

    def __init__(self, monkeypatch, tmp_path: Path, config_dir: Path):
        (config_dir / "platform.toml").write_text(TABLE, encoding="utf-8")
        monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
        monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
        self.held = [_profile(RECORDED, "Philippe", "2026-10-06"), _profile(SYNTH_IT, "Synthetic it", "2026-11-16"),
                     _profile(SYNTH_EN, "Synthetic en", "2026-11-16")]
        self.calls: list[dict] = []
        self.status = 200
        self.gate: threading.Event | None = None
        self.engine: list[str | None] = [ENGINE]
        #: the engine a synthesis loads the model under, where none is loaded
        self.on_load: str | None = None
        self.kept: list[str] = []
        self.lines: list[tuple[str, str]] = []
        node = self

        class R:
            def __init__(self, status: int, content: bytes = b"", data=None):
                self.status_code, self.content, self._data = status, content, data
                self.headers = {"content-type": "audio/wav" if status == 200 else "application/json"}

            def json(self):
                return self._data

        def post(url, json, **kw):
            if node.gate is not None:
                node.gate.wait(10)
            node.calls.append(json)
            if node.engine[0] is None and node.on_load:
                node.engine[0] = node.on_load
            return R(node.status, b"RIFF-" + json["input"].encode("utf-8") if node.status == 200 else b"{}")
        monkeypatch.setattr(speech.httpc, "get", lambda url, **kw: R(200, data={"data": node.held}))
        monkeypatch.setattr(speech.httpc, "post", post)
        monkeypatch.setattr(speech, "_profiles", (0.0, []))
        monkeypatch.setattr(speech, "PROFILES_TTL_S", 0.0)
        monkeypatch.setattr(speech, "IDLE_S", 0.2)
        monkeypatch.setattr(model_load, "backend_of", lambda url, model, max_age_s=None: node.engine[0])
        # the kept clips of app/speech.py are never asked, never written (`keep=False`)
        monkeypatch.setattr(speech, "_clip_get", lambda key: node.kept.append("get"))
        monkeypatch.setattr(speech, "_clip_put", lambda key, data, ctype: node.kept.append("put"))
        from app import model_pins
        monkeypatch.setattr(model_pins, "node_event", lambda level, text: node.lines.append((level, text)))
        # THE PINS ARE FAKED, as tests/test_speech.py's: speech arms a release IDLE_S after each synthesis, and
        # a real `model_pins.release` firing after this test's teardown resolves the REAL root (2026-11-26:
        # /data/state/.model-pins.lock was made so) — the timer keeps the function it was armed with
        monkeypatch.setattr(model_pins, "hold", lambda url, model, holder: True)
        monkeypatch.setattr(model_pins, "release", lambda holder, path=None: None)
        monkeypatch.setattr(fillers, "QUIET_S", 0.0)
        monkeypatch.setattr(fillers, "POLL_S", 0.01)

    def filled(self) -> None:
        assert fillers.wait_idle(10), "the pool's worker did not finish"


@pytest.fixture
def node(isolated_config, config_dir, tmp_path, monkeypatch):
    fillers.reset()
    n = Node(monkeypatch, tmp_path, config_dir)
    yield n
    if n.gate is not None:
        n.gate.set()                                 # nothing left waiting on the fake model
    fillers.reset()
    with speech._lock:                               # no release armed by this test outlives it
        for t in speech._timers.values():
            t.cancel()
        speech._timers.clear()


def _pool_files(tmp_path: Path) -> list[Path]:
    return sorted(p for p in (tmp_path / "cache" / fillers.DERIVED_KIND).rglob("*") if p.is_file())


# ---- the set ---------------------------------------------------------------------------------

def test_the_set_holds_the_five_main_languages_in_two_kinds_and_claims_nothing():
    from app import web
    assert fillers.LANGUAGES == tuple(code for code, *_ in web.RESULT_LANGUAGES[:5]), \
        "the five main languages: the first five of the result languages"
    got = fillers.load()
    assert tuple(got) == fillers.LANGUAGES
    for lang, kinds in got.items():
        assert tuple(kinds) == fillers.KINDS
        for kind, texts in kinds.items():
            assert fillers.PER_KIND[0] <= len(texts) <= fillers.PER_KIND[1], (lang, kind)
            for t in texts:
                low = t.casefold()
                assert not any(w in low for w in ("found", "trovat", "trouvé", "gefunden", "encontr",
                                                  "here it is", "ecco", "voilà", "voici")), t
                assert speech.language(t) in (None, lang), (t, "read in its own language's voice")
                assert speech.speakable(t) == t, (t, "said as written")
    assert fillers.texts("it-IT") == got["it"]["short"] + got["it"]["long"], "the echo guard's: every text"
    assert fillers.texts("fr", "long") == got["fr"]["long"]
    assert fillers.texts("pt") == () and fillers.texts(None) == ()
    assert "Vediamo…" in fillers.texts("it", "short") and "Let me check." in fillers.texts("en", "short")


@pytest.mark.parametrize("lang, edit, says", [
    ("en", lambda s: s.replace('"Still looking, one moment."', '"Found it, here it is."'), "claims a result"),
    ("it", lambda s: s.replace('"Un attimo."', '"Ecco."'), "claims a result"),
    ("it", lambda s: s.replace('"Sto ancora cercando, un momento."',
                               '"Ich suche noch, einen Moment."'), "reads as de"),
    ("fr", lambda s: s.replace('"Un instant.",\n', ""), None),          # still 4: fine
    ("fr", lambda s: s.replace('"Un instant.",\n', "").replace('"Je regarde.",\n', ""), "from 4 to 6"),
    ("de", lambda s: s.replace('"Hmm, ich gehe die Quellen durch."', '"Einen Moment."'), "both short and long"),
    ("es", lambda s: s.replace('"Lo miro."', '"Un momento, sigo buscando en las fuentes."'), "words"),
    ("en", lambda s: s.replace('"Let me look."', '"Let me look [3]."'), "read as written"),
    ("en", lambda s: s.replace("long = [", "longer = ["), "the kinds are"),
    ("es", lambda s: s.replace('"A ver…",', '"A ver…",\n    "A ver…",'), "twice"),
])
def test_a_set_that_breaks_a_rule_is_refused(tmp_path, lang, edit, says):
    d = tmp_path / "set"
    shutil.copytree(fillers.SET_DIR, d)
    f = d / f"{lang}.toml"
    f.write_text(edit(f.read_text(encoding="utf-8")), encoding="utf-8")
    if says is None:
        fillers.load(d)
        return
    with pytest.raises(fillers.SetError, match=says):
        fillers.load(d)


def test_a_language_missing_from_the_set_is_refused(tmp_path):
    d = tmp_path / "set"
    shutil.copytree(fillers.SET_DIR, d)
    (d / "es.toml").unlink()
    with pytest.raises(fillers.SetError, match="does not read"):
        fillers.load(d)


# ---- the pool --------------------------------------------------------------------------------

def test_a_lazy_fill_answers_at_once_and_makes_one_short_and_one_long_in_the_background(node, tmp_path):
    node.gate = threading.Event()                    # the model takes its time
    t0 = time.monotonic()
    first = fillers.ready("it", admin=True)
    assert time.monotonic() - t0 < 1.0 and first["ready"] == [], "at once, with what is ready: nothing yet"
    assert first["lang"] == "it" and first["voice"] == fillers.opaque(fillers.voice_identity(RECORDED, node.held))
    assert fillers.ready("it")["ready"] == [], "asked again while it is made: still nothing"
    node.gate.set()
    node.filled()
    assert len(node.calls) == 2, "one short, one long — the second ask queued nothing more"
    got = fillers.ready("it")
    assert [r["kind"] for r in got["ready"]] == ["short", "long"]
    assert all(r["url"] == f"/ask/voice/fillers/{r['id']}" for r in got["ready"])
    short, long_ = node.calls
    assert short["input"] in fillers.texts("it", "short") and long_["input"] in fillers.texts("it", "long")
    assert short["voice"] == RECORDED and short["language"] == "it" and short["model"] == "omnivoice-cpp"
    fillers.ready("it")
    node.filled()
    assert len(node.calls) == 2, "a full pool makes nothing"
    assert node.kept == [], "never asked of, never kept among, speech's kept clips"
    assert not (tmp_path / "cache" / "speech-clips").exists()
    # another language is another pool, in the same voice for an admin
    fillers.ready("en")
    node.filled()
    assert len(node.calls) == 4 and {c["language"] for c in node.calls[2:]} == {"en"}
    assert len([p for p in _pool_files(tmp_path) if p.suffix == ".json"]) == 4


def test_a_used_clip_goes_and_exactly_its_kind_is_remade_of_another_text(node, tmp_path):
    fillers.ready("it")
    node.filled()
    for _ in range(12):
        got = {r["kind"]: r["id"] for r in fillers.ready("it")["ready"]}
        short, long_ = (fillers._find(got[k])[1] for k in fillers.KINDS)
        n = len(node.calls)
        assert fillers.used([got["short"]]) == 1
        assert fillers.clip(got["short"]) is None, "played once: gone"
        node.filled()
        assert len(node.calls) == n + 1, "the used kind only"
        again = {r["kind"]: r["id"] for r in fillers.ready("it")["ready"]}
        assert again["long"] == got["long"] and again["short"] != got["short"]
        fresh = fillers._find(again["short"])[1]
        assert fresh["text"] != short["text"], "never the text just used"
        assert fresh["text"] != long_["text"], "never the other ready one's"
    # both played: both remade, each of another text
    got = {r["kind"]: fillers._find(r["id"])[1] for r in fillers.ready("it")["ready"]}
    n = len(node.calls)
    assert fillers.used([m["id"] for m in got.values()] + ["not-an-id", "0" * 32]) == 2
    node.filled()
    assert len(node.calls) == n + 2
    after = {r["kind"]: fillers._find(r["id"])[1]["text"] for r in fillers.ready("it")["ready"]}
    assert all(after[k] != got[k]["text"] for k in fillers.KINDS)
    assert node.kept == []
    assert len([p for p in _pool_files(tmp_path) if p.suffix == ".json" and p.name != fillers.USED]) == 2


def test_a_clip_another_engine_made_is_never_served_and_is_remade(node, tmp_path):
    fillers.ready("it")
    node.filled()
    old = {r["kind"]: r["id"] for r in fillers.ready("it")["ready"]}
    # the speech model evicted: a clip listed under a known engine is still served by its id, but nothing is
    # listed (whatever is kept could be another engine's)
    node.engine[0], node.on_load = None, ENGINE
    assert fillers.clip(old["short"]) is not None
    assert fillers.ready("it")["ready"] == []
    node.filled()
    assert len(node.calls) == 3, "the engine not known: one clip made, which loaded the model and said which"
    assert {r["id"] for r in fillers.ready("it")["ready"]} == set(old.values()), "and its pool had it: dropped"
    node.on_load = None
    # the engine swapped: the old engine's clips are not found, and are made again under the new one
    node.engine[0] = OTHER
    assert fillers.ready("it")["ready"] == []
    node.filled()
    assert len(node.calls) == 5
    new = {r["kind"]: r["id"] for r in fillers.ready("it")["ready"]}
    assert set(new.values()).isdisjoint(old.values())
    assert all(fillers._find(i)[1]["engine"] == OTHER for i in new.values())
    assert fillers.clip(old["long"]) is None, "another engine's: not served"
    assert fillers._find(old["long"]) is None, "and gone"
    assert fillers.clip(new["long"]) == (b"RIFF-" + fillers._find(new["long"])[1]["text"].encode(), "audio/wav")


def test_nothing_is_synthesized_while_a_question_runs(node, monkeypatch):
    monkeypatch.setattr(fillers, "QUIET_S", 0.2)
    with ExitStack() as held:
        held.enter_context(dispatch.question(linger=0.0))
        assert dispatch.quiet_s() == 0.0
        fillers.ready("it")
        time.sleep(0.5)
        assert node.calls == [], "a search, an answer or a reading is running: the pool waits"
    t0 = time.monotonic()
    node.filled()
    assert len(node.calls) == 2 and time.monotonic() - t0 >= 0.2, "made once the process was quiet"
    # a job that stays deferred past its bound is dropped, not failed: the next ask asks again
    monkeypatch.setattr(fillers, "DEFER_MAX_S", 0.2)
    ids = [r["id"] for r in fillers.ready("it")["ready"]]
    with ExitStack() as held:
        held.enter_context(dispatch.question(linger=0.0))
        assert fillers.used(ids) == 2
        node.filled()
        assert len(node.calls) == 2 and not [ln for ln in node.lines if ln[0] != "info"], "deferred, never failed"
    monkeypatch.setattr(fillers, "DEFER_MAX_S", 600.0)
    fillers.ready("it")
    node.filled()
    assert len(node.calls) == 4


def test_quiet_counts_from_the_end_of_the_last_step_of_a_question():
    assert dispatch.quiet_s() == float("inf"), "none has run"
    with dispatch.question(linger=0.0):
        with dispatch.interactive(dispatch.Level.RERANK):
            assert dispatch.quiet_s() == 0.0
        assert dispatch.quiet_s() == 0.0, "the question still runs"
    time.sleep(0.05)
    assert 0.04 <= dispatch.quiet_s() < 5.0


def test_a_failed_synthesis_is_one_line_and_is_tried_again_at_the_next_ask_never_in_a_loop(node, monkeypatch):
    node.status = 500
    fillers.ready("it")
    node.filled()
    assert len(node.calls) == 1 and [ln[0] for ln in node.lines] == ["warn"]
    assert "speech fillers: Philippe (it) not made" in node.lines[0][1]
    fillers.ready("it")
    node.filled()
    assert len(node.calls) == 1, "not before RETRY_AFTER_S"
    monkeypatch.setattr(fillers, "RETRY_AFTER_S", 0.0)
    node.status = 200
    fillers.ready("it")
    node.filled()
    assert len(node.calls) == 3 and len(fillers.ready("it")["ready"]) == 2
    # an engine never named: nothing kept (it could be any engine's), said once
    node.engine[0] = None
    fillers.ready("fr")
    node.filled()
    assert len(node.calls) == 4 and "does not say which engine" in node.lines[-1][1]


def test_the_voice_is_known_by_its_recording_never_by_its_name(node):
    fillers.ready("it")
    node.filled()
    node.held[0] = {**node.held[0], "name": "Philippe (renamed)"}
    assert len(fillers.ready("it")["ready"]) == 2, "a new name: the same voice"
    node.held[0] = {**node.held[0], "updated_at": "2026-11-26T08:00:00Z"}
    got = fillers.ready("it")
    assert got["ready"] == [], "its clip replaced under the same id: another voice, made again"
    node.filled()
    assert len(node.calls) == 4
    assert got["voice"] == fillers.opaque(fillers.voice_identity(RECORDED, node.held))
    assert fillers.voice_identity(None, []) != fillers.voice_identity("alloy", []) == "alloy"


def test_the_pools_are_derived_data_on_the_fast_drive_and_none_without_one(node, tmp_path, monkeypatch):
    assert fillers._root() == derived.place(fillers.DERIVED_KIND, Path(config.louise_root()))
    assert fillers._root().parent.parent == tmp_path / "cache"
    fillers.ready("de")
    node.filled()
    assert _pool_files(tmp_path)
    monkeypatch.setattr(config, "CACHE_DIR", None)
    got = fillers.ready("es")
    node.filled()
    assert got["ready"] == [] and got["voice"] is not None and len(node.calls) == 2, \
        "no fast drive: nothing kept, so nothing made"


def test_another_model_set_meanwhile_makes_nothing_more_for_the_old_pool(node, config_dir):
    node.gate = threading.Event()
    fillers.ready("it")
    (config_dir / "platform.toml").write_text(TABLE.replace("omnivoice-cpp", "another-tts"), encoding="utf-8")
    node.gate.set()
    node.filled()
    assert len(node.calls) <= 1, "the model changed: the job for the old one ends"


# ---- the routes ------------------------------------------------------------------------------

def test_the_routes_answer_their_shape_to_each_reader_by_role(node, tmp_path):
    c = TestClient(api.app)
    got = c.get("/ask/voice/fillers?lang=it", headers=ADMIN)
    assert got.status_code == 200 and got.json()["ready"] == [] and set(got.json()) == {"lang", "voice", "ready"}
    node.filled()
    body = c.get("/ask/voice/fillers", params={"lang": "it-IT"}, headers=ADMIN).json()
    assert body["lang"] == "it" and [set(r) for r in body["ready"]] == [{"id", "kind", "url"}] * 2
    assert {c_["voice"] for c_ in node.calls} == {RECORDED}, "an admin: their recorded voice"
    audio = c.get(body["ready"][0]["url"], headers=ADMIN)
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    assert audio.content.startswith(b"RIFF-") and audio.headers["cache-control"] == "no-store"
    assert c.get("/ask/voice/fillers/" + "f" * 32, headers=ADMIN).status_code == 404
    assert c.get("/ask/voice/fillers/not-an-id", headers=ADMIN).status_code == 404
    assert c.get("/ask/voice/fillers?lang=pt", headers=ADMIN).json() == {"lang": "pt", "voice": None, "ready": []}
    assert c.get("/ask/voice/fillers", headers=ADMIN).json()["ready"] == []
    # a reader who is not an admin: the synthetic voice of the language, never the recorded one
    _, token = users.add("bea")
    bea = {"Authorization": f"Bearer {token}"}
    assert c.get(body["ready"][0]["url"], headers=bea).status_code == 404, "the admin's voice is the admin's"
    assert c.post("/ask/voice/fillers/used", headers=bea, json={"ids": [body["ready"][0]["id"]]}).json() == \
        {"removed": 0}
    n = len(node.calls)
    for lang, want in (("it", SYNTH_IT), ("en", SYNTH_EN)):
        mine = c.get("/ask/voice/fillers", headers=bea, params={"lang": lang, "voice": RECORDED}).json()
        assert mine["voice"] == fillers.opaque(fillers.voice_identity(want, node.held))
        node.filled()
    assert [x["voice"] for x in node.calls[n:]] == [SYNTH_IT] * 2 + [SYNTH_EN] * 2, "the page's choice counts not"
    mine = c.get("/ask/voice/fillers?lang=it", headers=bea).json()["ready"]
    assert c.get(mine[0]["url"], headers=bea).status_code == 200
    assert c.post("/ask/voice/fillers/used", headers=bea, json={"ids": [mine[0]["id"]]}).json() == {"removed": 1}
    assert c.get(mine[0]["url"], headers=bea).status_code == 404
    assert c.post("/ask/voice/fillers/used", headers=bea, json={"ids": ["x"] * 17}).status_code == 422
    # once a user exists, nobody without a token
    assert c.get("/ask/voice/fillers?lang=it").status_code == 401
    assert c.get(mine[1]["url"]).status_code == 401
    assert c.post("/ask/voice/fillers/used", json={"ids": []}).status_code == 401


def test_an_admin_with_a_kept_voice_or_a_chosen_one_gets_that_voice(node):
    c = TestClient(api.app)
    _, token = users.add("ada", role="admin")
    ada = {"Authorization": f"Bearer {token}"}
    users.set_voice("ada", SYNTH_EN)
    c.get("/ask/voice/fillers?lang=it", headers=ada)
    node.filled()
    assert {x["voice"] for x in node.calls} == {SYNTH_EN}, "the account's voice"
    c.get("/ask/voice/fillers", params={"lang": "it", "voice": SYNTH_IT}, headers=ada)
    node.filled()
    assert {x["voice"] for x in node.calls[2:]} == {SYNTH_IT}, "the page's choice first, as speech reads it"
    c.get("/ask/voice/fillers", params={"lang": "it", "voice": "localai://voice-profiles/gone"}, headers=ADMIN)
    node.filled()
    assert {x["voice"] for x in node.calls[4:]} == {RECORDED}, "a voice no longer held: the node's"


def test_a_reader_with_no_synthetic_voice_is_offered_nothing(node, config_dir):
    (config_dir / "platform.toml").write_text(f'[speech]\ntts_model = "omnivoice-cpp"\ntts_voice = "{RECORDED}"\n',
                                              encoding="utf-8")
    _, token = users.add("bea")
    got = TestClient(api.app).get("/ask/voice/fillers?lang=it", headers={"Authorization": f"Bearer {token}"}).json()
    node.filled()
    assert got == {"lang": "it", "voice": None, "ready": []} and node.calls == []


def test_speech_without_keep_neither_reads_nor_writes_the_kept_clips(node):
    """The fresh synthesis the pool asks for (`keep=False`): the same words
    asked again are a NEW clip, never the one kept."""
    body = {"model": "omnivoice-cpp", "input": "Vediamo…", "voice": RECORDED, "language": "it"}
    for _ in range(2):
        assert speech.synthesize_timed(dict(body), "http://ai:8080", keep=False)[0] == 200
    assert len(node.calls) == 2 and node.kept == []
    speech.synthesize_timed(dict(body), "http://ai:8080")
    assert node.kept == ["get", "put"], "a reading still keeps its clips"
