"""PARKING, LOUISE'S HALF (docs/notes/parking-step-2.md §4 A and B, §7;
operator, 2026-11-25: "Do parking."): a model LocalAI says it can park gives
its room back by moving its weights to host memory, and comes back in the
time of a copy; a parked model is listed and is NOT on the card; the keeper
writes the host's floor; "the device has no room now" is a deferral.

Built against the server part's exact answers (staged
0027-model-park.patch): `POST /backend/park {"model"}` — 200 `{"message":
"model parked", "parked": {size_host, size_vram_needed, since}}`, 409
`model_busy`, 412 `cannot_park` / `parking_off` / `host_memory_floor`, 404
`model_not_loaded`, 500 `model_stopped`; `GET /system` — `"parks": true`,
`"state": "loaded" | "parked"`, `"parked": {…}`; the runtime setting
`park_host_floor_bytes`; HTTP 503 `Retry-After: 2` `{"error": {"type":
"no_room_on_device"}}`.

ON A SERVER WITHOUT THE PATCH NOTHING CHANGES: no row says `parks`, no park
is asked, no floor is written — the first tests of each part say so."""
from __future__ import annotations

import time
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest

from app import (config, gpu, httpc, localai_reclaimer, model_evict, model_load, model_pins, model_use, node_state,
                 platform_config, resident)
from tests.test_model_evict import _in_set, _known, _used  # noqa: F401

REAL_FETCH = model_load._fetch_system
S = "http://localai:8080"
MIB = 1 << 20
GIB = 1 << 30
#: what a parked process still holds on the card (bge-m3: 321 MiB, measured)
RESIDUE = 300
SECRET = "sk-park-secret"


def _refusal(status: int, kind: str, model: str = "m"):
    body = {"error": {"message": f'model "{model}": {kind}', "code": status, "type": kind}}
    return SimpleNamespace(status_code=status, json=lambda: body, headers={})


class Runner:
    """LocalAI WITH the park patch, as Louise meets it: `/system` (a model
    that parks says so, a parked one what it holds and what its return
    takes), `/backend/park`, `/backend/shutdown`, `/backend/load`, the
    settings read whole and written whole, each model's pin, and the GPU
    probe, whose count follows what is on the card."""

    def __init__(self, models_mib: dict[str, int], *, parks=(), extra_mib: int = 0, cpu=None,
                 patched: bool = True, floor: int | None = 0, answers=None):
        self.models = dict(models_mib)          # MiB on the card when loaded
        self.parks = set(parks) if patched else set()
        self.parked: dict[str, int] = {}        # model -> MiB of weights in host memory
        self.extra = extra_mib
        self.cpu = dict(cpu or {})
        self.patched = patched
        self.settings = {"memory_reclaimer_threshold": 0.95, "memory_reclaimer_enabled": True,
                         "api_keys": [SECRET], "p2p_token": SECRET}
        if patched and floor is not None:
            self.settings["park_host_floor_bytes"] = floor
        self.answers = dict(answers or {})      # model -> the park's scripted refusal
        self.calls: Counter = Counter()
        self.asked: list[tuple[str, str]] = []  # (verb, model) in order
        self.posted: list[dict] = []            # the settings bodies written

    def on_card(self, m: str) -> int:
        return RESIDUE if m in self.parked else self.models[m]

    def get(self, url, **kw):
        if url.endswith("/system"):
            self.calls["system"] += 1
            rows = []
            for i, m in enumerate(sorted(self.models)):
                row = {"id": m, "size_vram": self.on_card(m) * MIB,
                       "process": {"pid": 10 + i, "cpu_percent": self.cpu.get(m, 0.0)}}
                if m in self.parks:
                    row.update(parks=True, state="parked" if m in self.parked else "loaded")
                    if m in self.parked:
                        row["parked"] = {"size_host": self.parked[m] * MIB,
                                         "size_vram_needed": self.parked[m] * MIB,
                                         "since": "2026-11-25T21:00:00Z"}
                rows.append(row)
            return SimpleNamespace(status_code=200, json=lambda: {"loaded_models": rows})
        if url.endswith("/api/settings"):
            self.calls["settings"] += 1
            body = dict(self.settings)
            return SimpleNamespace(status_code=200, json=lambda: body)
        if "/api/models/config-json/" in url:
            m = url.rsplit("/", 1)[1]
            return SimpleNamespace(status_code=200, json=lambda: {"name": m, "pinned": False})
        raise AssertionError(f"unexpected GET {url}")

    def put(self, url, **kw):
        return SimpleNamespace(status_code=200)

    def post(self, url, json=None, **kw):
        if url.endswith("/api/settings"):
            self.posted.append(dict(json))
            known = set(self.settings) | ({"park_host_floor_bytes"} if self.patched else set())
            self.settings.update({k: v for k, v in json.items() if k in known})
            return SimpleNamespace(status_code=200, json=lambda: {"success": True})
        m = json["model"]
        if url.endswith("/backend/park"):
            self.asked.append(("park", m))
            if not self.patched:
                return SimpleNamespace(status_code=404, headers={},
                                       json=lambda: {"error": {"code": 404, "message": "Resource not found", "type": ""}})
            if m in self.answers:
                status, kind = self.answers[m]
                if kind == "model_stopped":
                    self.models.pop(m, None)
                return _refusal(status, kind, m)
            if m not in self.models:
                return _refusal(404, "model_not_loaded", m)
            weights = self.models[m] - RESIDUE
            self.parked[m] = weights
            return SimpleNamespace(status_code=200, headers={}, json=lambda: {
                "message": "model parked",
                "parked": {"size_host": weights * MIB, "size_vram_needed": weights * MIB,
                           "since": "2026-11-25T21:00:00Z"}})
        if url.endswith("/backend/shutdown"):
            self.asked.append(("shutdown", m))
            self.models.pop(m, None)
            self.parked.pop(m, None)
            return SimpleNamespace(status_code=200)
        if url.endswith("/backend/load"):
            self.asked.append(("load", m))
            self.parked.pop(m, None)
            return SimpleNamespace(status_code=200, json=lambda: {"loaded": [m]})
        raise AssertionError(f"unexpected POST {url}")

    def gpu(self, url):
        used = (sum(self.on_card(m) for m in self.models) + self.extra) * MIB
        return {"devices": [{"card": "card1", "pdev": "0000:03:00.0", "driver": "xe", "vendor_id": "0x8086",
                             "device_id": "0xe223", "vendor": "Intel", "name": "Arc Pro B70",
                             "vram_total": 32_656 * MIB, "vram_used": used,
                             "processes": [{"pid": 7, "label": "x", "command": "llama-server",
                                            "container": "localai", "vram": used, "host": 0}]}]}


@pytest.fixture
def node(monkeypatch, tmp_path, isolated_config):
    def make(models_mib, **kw) -> Runner:
        r = Runner(models_mib, **kw)
        monkeypatch.setattr(model_load, "_fetch_system", REAL_FETCH)
        monkeypatch.setattr(httpc, "get", r.get)
        monkeypatch.setattr(httpc, "post", r.post)
        monkeypatch.setattr(httpc, "put", r.put)
        monkeypatch.setattr(config, "GPU_PROBE_URL", "http://gpu-probe:8080")
        monkeypatch.setattr(gpu, "_get_json", r.gpu)
        return r
    monkeypatch.setattr(model_pins, "_path", lambda: tmp_path / "state" / "model-pins.json")
    monkeypatch.setattr(model_evict, "ROOM_WAIT_S", 0.0)
    recorded: list = []
    monkeypatch.setattr(model_load, "_record", recorded.append)     # no cold-start line from a thread
    said: list[str] = []
    resident.reset()
    resident.set_log(lambda level, text: said.append(text))
    model_load.reset()
    model_use.reset()
    node_state.set_worker_state("running")
    make.said, make.recorded = said, recorded
    yield make
    resident.reset()
    resident.set_log(model_pins.node_event)
    model_load.reset()


def _verbs(r: Runner, verb: str) -> list[str]:
    return [m for v, m in r.asked if v == verb]


# ---- the listing ------------------------------------------------------------------

def test_a_server_without_the_patch_lists_no_model_that_parks(node):
    node({"vega-bge-m3": 900, "louise-chat": 7_000}, patched=False)
    assert model_load.loaded_models(S) == {"vega-bge-m3", "louise-chat"}
    assert model_load.parks(S) == frozenset() and model_load.parked(S) == {}


def test_a_parked_model_is_listed_and_nothing_is_learnt_from_what_is_left_of_it(node):
    """Its `size_vram` is what its process still holds on the card (321 MiB
    for bge-m3): never its size, never its weights at rest, never steady."""
    r = node({"vega-bge-m3": 900, "louise-chat": 7_000}, parks=["vega-bge-m3"])
    assert model_load.loaded_models(S, 0.0) and model_load.parks(S) == {"vega-bge-m3"}
    assert model_load.parked(S) == {} and model_load.size_vram(S, "vega-bge-m3") == 900 * MIB
    r.parked["vega-bge-m3"] = 600
    assert "vega-bge-m3" in model_load.loaded_models(S, 0.0), "it has a live process: it stays in the list"
    assert model_load.parked(S) == {"vega-bge-m3": (600 * MIB, 600 * MIB)}
    assert model_load.held_sizes(S)["vega-bge-m3"][1] == RESIDUE * MIB, "what the card holds of it now"
    assert model_load.size_vram(S, "vega-bge-m3") == 900 * MIB, "its largest is not lowered"
    assert model_load.rest_vram(S, "vega-bge-m3") == 900 * MIB
    assert model_load.steady_vram(S, "vega-bge-m3") is None, "what a park leaves is never the model at rest"


def test_a_model_listed_parked_is_parked_whatever_its_reading(node):
    """The review of 2026-11-25, reproduced: a first cut took a parked model
    for "on the card" when it read near what it held at rest — and after one
    partly paged reading (600 of 939 MiB, routine in query mode) the spec's
    own parked row (337 MiB, 603 to return) was misread, and the keeper
    un-parked it at every beat. Nothing is told from the reading any more."""
    r = node({"vega-bge-m3": 939}, parks=["vega-bge-m3"])
    model_load.loaded_models(S, 0.0)
    r.on_card = lambda m: 600                           # partly paged: the fewest reading over half the largest
    model_load.loaded_models(S, 0.0)
    r.parked["vega-bge-m3"] = 603
    r.on_card = lambda m: 337
    model_load.loaded_models(S, 0.0)
    assert model_load.parked(S) == {"vega-bge-m3": (603 * MIB, 603 * MIB)}
    r.on_card = lambda m: 939                           # ...and even holding all of it: the listing's word stands
    model_load.loaded_models(S, 0.0)
    assert "vega-bge-m3" in model_load.parked(S)
    assert not hasattr(model_load, "parked_stale") and not hasattr(model_load, "record_loaded")
    did = resident._parked_follow([S])
    assert set(did) == {"floor", "stopped"} and did["stopped"] == [] and not _verbs(r, "load"), \
        "Louise never calls /backend/load: the model's next request makes the record exact"


# ---- the verb: /backend/park ------------------------------------------------------

@pytest.mark.parametrize("answer, want", [
    (None, (model_load.PARKED, 600 * MIB)),
    ((409, "model_busy"), (model_load.PARK_BUSY, 0)),
    ((412, "cannot_park"), (model_load.PARK_STOP, 0)),
    ((412, "parking_off"), (model_load.PARK_STOP, 0)),
    ((412, "host_memory_floor"), (model_load.PARK_STOP, 0)),
    ((404, "model_not_loaded"), (model_load.PARK_GONE, 0)),
    ((500, "model_stopped"), (model_load.PARK_GONE, 0)),
    ((500, "server_error"), (model_load.PARK_STOP, 0)),
    ((404, ""), (model_load.PARK_STOP, 0)),             # a server with no such route
])
def test_each_answer_of_the_park_tells_its_caller_what_to_do_next(node, answer, want):
    node({"vega-bge-m3": 900}, parks=["vega-bge-m3"], answers={} if answer is None else {"vega-bge-m3": answer})
    assert model_load.park(S, "vega-bge-m3") == want


def test_a_runner_that_cannot_be_reached_is_never_raised(node, monkeypatch):
    node({"vega-bge-m3": 900}, parks=["vega-bge-m3"])
    monkeypatch.setattr(httpc, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert model_load.park(S, "vega-bge-m3") == (model_load.PARK_STOP, 0)


# ---- the eviction parks where the model can ----------------------------------------

def test_without_the_patch_room_is_made_by_stopping_as_before(node):
    r = node({"vega-bge-m3": 2_000, "omnivoice-cpp": 1_700}, extra_mib=27_000, patched=False, parks=["vega-bge-m3"])
    _used(vega_bge_m3=30, omnivoice_cpp=2)
    _known(louise_chat=1_500)
    out = model_evict.make_room_for(S, "louise-chat", horizon=time.time())
    assert out.fits is True and out.unloaded == ("vega-bge-m3",) and out.parked == ()
    assert r.asked == [("shutdown", "vega-bge-m3")], "no park is asked of a model that does not say it parks"


def test_a_model_that_parks_is_parked_and_counted_at_what_left_the_card(node):
    """The oldest real use still goes first; it is parked, not stopped, and
    what it frees is its weights — its process's residue stays."""
    r = node({"vega-bge-m3": 2_000, "omnivoice-cpp": 1_700, "louise-chat": 7_000}, extra_mib=20_000,
             parks=["vega-bge-m3"], floor=12 * GIB)
    _used(vega_bge_m3=30, omnivoice_cpp=2, louise_chat=0.1)
    _known(vega_qwen3_4b=1_900)                              # 323 MiB free: 1,577 short
    out = model_evict.make_room_for(S, "vega-qwen3-4b", horizon=time.time())
    assert out.fits is True and out.parked == ("vega-bge-m3",) and out.unloaded == ()
    assert r.asked == [("park", "vega-bge-m3")] and "vega-bge-m3" in r.models, "its process stays"
    line = [t for t in node.said if t.startswith("card: parked")]
    # and how long the park took: what the engine's byte check costs is read here (2026-11-26)
    assert len(line) == 1 and "vega-bge-m3 (last used 30.0 h ago, 1,700 MiB to host memory in 0." in line[0]
    assert " s) for vega-qwen3-4b" in line[0]
    assert "Do parking." in line[0] and not [t for t in node.said if t.startswith("card: unloaded")]


def test_an_encoder_whose_run_is_not_admitted_to_park_is_stopped_for_its_room(node, monkeypatch):
    """München, 2026-11-26: bge-m3 restarted with `park: true` and its env;
    27 s later the keeper said "vega-bge-m3 can be parked" — and the
    gate's park cycles had not run for that run. A park is for a run
    admitted to park; any other gives its room as before parking existed."""
    from app import run_check
    asked = []
    verdict = {"vega-bge-m3": False}
    monkeypatch.setattr(run_check, "park_admitted",
                        lambda server, model, pid: asked.append(model) or verdict.get(model))

    def room():
        r = node({"vega-bge-m3": 2_000, "omnivoice-cpp": 1_700, "louise-chat": 7_000}, extra_mib=20_000,
                 parks=["vega-bge-m3", "omnivoice-cpp"], floor=12 * GIB)
        _used(vega_bge_m3=30, omnivoice_cpp=2, louise_chat=0.1)
        _known(vega_qwen3_4b=1_900)                          # 323 MiB free: 1,577 short
        node.said.clear()
        return r, model_evict.make_room_for(S, "vega-qwen3-4b", horizon=time.time())

    r, out = room()
    assert out.fits is True and out.unloaded == ("vega-bge-m3",) and out.parked == ()
    assert _verbs(r, "park") == [] and "vega-bge-m3" not in r.models, "stopped: nothing of it was parked"
    line, = [t for t in node.said if t.startswith("card: unloaded")]
    assert "vega-bge-m3 (last used 30.0 h ago, 2,000 MiB, its file says it parks and its run is not admitted to: stopped)" in line
    assert sorted(asked) == ["omnivoice-cpp", "vega-bge-m3"], "asked of each loaded model that says it parks, and of no other"
    # its cycles counted: parked, as LocalAI lists it
    verdict["vega-bge-m3"] = True
    r, out = room()
    assert out.parked == ("vega-bge-m3",) and out.unloaded == () and "vega-bge-m3" in r.models
    # a model that is no collection's encoder (None): LocalAI's word stands
    verdict["vega-bge-m3"] = None
    r, out = room()
    assert out.parked == ("vega-bge-m3",)
    # an answer that cannot be had is a stop, never a park
    monkeypatch.setattr(run_check, "park_admitted",
                        lambda server, model, pid: (_ for _ in ()).throw(OSError("unreadable")))
    r, out = room()
    assert out.unloaded == ("vega-bge-m3",) and out.parked == () and _verbs(r, "park") == []


def test_what_a_park_leaves_on_the_card_is_not_counted_as_freed(node):
    """2,000 MiB held, 1,700 moved: a need of 1,900 over what is free takes
    the next candidate too — the pre-check's bound is not the count."""
    r = node({"vega-bge-m3": 2_000, "omnivoice-cpp": 1_700}, extra_mib=27_000, parks=["vega-bge-m3"])
    _used(vega_bge_m3=30, omnivoice_cpp=2)
    _known(big=2_200)                                        # 323 free: 1,877 short, the park gives 1,700
    out = model_evict.make_room_for(S, "big", horizon=time.time())
    assert out.fits is True and out.parked == ("vega-bge-m3",) and out.unloaded == ("omnivoice-cpp",)
    assert r.asked == [("park", "vega-bge-m3"), ("shutdown", "omnivoice-cpp")]


def test_where_the_parks_leave_the_room_short_the_parked_ones_are_stopped_and_it_fits(node):
    """The review of 2026-11-25, reproduced: 360 MiB free, a 900 MiB model
    that parks, a 1,000 MiB one, 2,200 needed. The check counted both whole
    (2,260); the park gave 600 — "1,935 of 2,200 MiB made", one model parked
    and one stopped for a load that still had no room. What the check counted
    is made: the parked model is stopped for what its process still holds."""
    r = node({"vega-a": 900, "omnivoice-cpp": 1_000}, extra_mib=28_763, parks=["vega-a"])
    _used(vega_a=30, omnivoice_cpp=2)
    _known(big=2_200)
    out = model_evict.make_room_for(S, "big", horizon=time.time())
    assert out.fits is True, out.why
    assert r.asked == [("park", "vega-a"), ("shutdown", "omnivoice-cpp"), ("shutdown", "vega-a")]
    assert out.parked == () and set(out.unloaded) == {"vega-a", "omnivoice-cpp"}
    assert not [t for t in node.said if t.startswith("card: parked")], "it did not stay parked: not said as parked"


def test_a_model_parked_a_moment_ago_and_asked_for_since_is_neither_stopped_nor_counted(node, monkeypatch):
    """The stops take seconds. A model parked at their start may be pinned by
    an embed batch and back on the card before the room is counted short:
    read again first — it is not stopped while it works, and the room its
    park gave is taken again."""
    r = node({"vega-a": 900, "omnivoice-cpp": 1_000}, extra_mib=28_763, parks=["vega-a"])
    _used(vega_a=30, omnivoice_cpp=2)
    _known(big=2_200)
    real_post = r.post

    def post(url, json=None, **kw):
        got = real_post(url, json=json, **kw)
        if url.endswith("/backend/shutdown") and json["model"] == "omnivoice-cpp":
            r.parked.pop("vega-a", None)                # ...asked for meanwhile: back on the card
        return got
    monkeypatch.setattr(httpc, "post", post)
    out = model_evict.make_room_for(S, "big", horizon=time.time())
    assert r.asked == [("park", "vega-a"), ("shutdown", "omnivoice-cpp")], "the one that came back is not stopped"
    assert out.fits is False and out.parked == () and out.unloaded == ("omnivoice-cpp",)
    assert "1,360 of 2,200 MiB made" in out.why, "and the room its park gave is not counted"

    r = node({"vega-a": 900, "omnivoice-cpp": 1_000}, extra_mib=28_763, parks=["vega-a"])
    model_load.reset()
    _known(big=2_200)
    monkeypatch.setattr(model_pins, "read_pinned", lambda server, model: model == "vega-a" and bool(r.parked))
    out = model_evict.make_room_for(S, "big", horizon=time.time())
    assert ("shutdown", "vega-a") not in r.asked and out.parked == ("vega-a",), "pinned since its park: left parked"


def test_a_busy_model_is_not_stopped_the_next_candidate_gives_the_room(node):
    r = node({"vega-bge-m3": 2_000, "omnivoice-cpp": 1_700}, extra_mib=27_000, parks=["vega-bge-m3"],
             answers={"vega-bge-m3": (409, "model_busy")})
    _used(vega_bge_m3=30, omnivoice_cpp=2)
    _known(louise_chat=1_500)
    out = model_evict.make_room_for(S, "louise-chat", horizon=time.time())
    assert out.fits is True and out.unloaded == ("omnivoice-cpp",) and out.parked == ()
    assert r.asked == [("park", "vega-bge-m3"), ("shutdown", "omnivoice-cpp")] and "vega-bge-m3" in r.models


@pytest.mark.parametrize("kind", ["cannot_park", "parking_off", "host_memory_floor"])
def test_a_model_that_cannot_be_parked_now_is_stopped_instead(node, kind):
    r = node({"vega-bge-m3": 2_000}, extra_mib=29_000, parks=["vega-bge-m3"], answers={"vega-bge-m3": (412, kind)})
    _used(vega_bge_m3=30)
    _known(louise_chat=1_500)
    out = model_evict.make_room_for(S, "louise-chat", horizon=time.time())
    assert out.fits is True and out.unloaded == ("vega-bge-m3",)
    assert r.asked == [("park", "vega-bge-m3"), ("shutdown", "vega-bge-m3")]


@pytest.mark.parametrize("answer", [(404, "model_not_loaded"), (500, "model_stopped")])
def test_a_model_the_park_found_gone_is_off_the_card_and_no_stop_follows(node, answer):
    r = node({"vega-bge-m3": 2_000}, extra_mib=29_000, parks=["vega-bge-m3"], answers={"vega-bge-m3": answer})
    _used(vega_bge_m3=30)
    _known(louise_chat=1_500)
    out = model_evict.make_room_for(S, "louise-chat", horizon=time.time())
    assert out.fits is True and r.asked == [("park", "vega-bge-m3")], "500 model_stopped: I skip the shutdown on it"


def test_a_parked_model_gives_way_last_and_is_then_stopped_for_what_it_still_holds(node):
    """It is no candidate while a loaded one can give the room; only then is
    it stopped, since nothing of it is left to park."""
    r = node({"vega-bge-m3": 2_000, "vega-dar": 2_300, "omnivoice-cpp": 1_700}, extra_mib=27_000,
             parks=["vega-bge-m3", "vega-dar"])
    r.parked["vega-bge-m3"] = 1_700                          # parked long ago: 300 MiB still on the card
    _used(vega_bge_m3=90, vega_dar=5, omnivoice_cpp=2)
    _known(a=1_500, b=4_000)
    out = model_evict.make_room_for(S, "a", horizon=time.time())
    assert out.fits is True and out.parked == ("vega-dar",) and r.asked == [("park", "vega-dar")], \
        "the parked one, though used longest ago, is left: a loaded one gives the room"
    r.asked.clear()
    model_load.reset()
    _known(b=4_000)
    out = model_evict.make_room_for(S, "b", horizon=time.time())     # 4,000: everything must go
    assert out.fits is True
    assert r.asked == [("shutdown", "omnivoice-cpp"), ("shutdown", "vega-bge-m3"), ("shutdown", "vega-dar")]
    said = [t for t in node.said if t.startswith("card: unloaded")][-1]
    assert "it was parked: what its process still held" in said


def test_a_parked_model_counts_for_what_it_holds_now_in_the_cards_commitment(node):
    r = node({"vega-bge-m3": 2_000, "louise-chat": 7_000}, parks=["vega-bge-m3"])
    model_load.loaded_models(S, 0.0)
    assert model_evict.committed(S) == 9_000 * MIB
    r.parked["vega-bge-m3"] = 1_700
    model_load.reset()
    _known(vega_bge_m3=2_000)
    assert model_evict.committed(S) == (7_000 + RESIDUE) * MIB, "its weights do not come back by themselves"


def test_a_question_parks_the_corpus_encoders_that_can_and_leaves_a_parked_one(node):
    """A question takes the card from the encoders that only embed
    documents: parked, they are back in the time of a copy when embedding
    resumes; one already parked has nothing on the card to give."""
    from app import question_card
    r = node({"vega-nemotron": 2_500, "vega-dar": 2_300, "vega-medcpt": 900, "louise-chat": 7_000},
             parks=["vega-nemotron", "vega-dar"])
    r.parked["vega-dar"] = 2_000
    want = [resident.Resident(S, m, ("encoder",)) for m in ("vega-nemotron", "vega-dar", "vega-medcpt")]
    done = question_card.free_card(want, question=lambda: True, sleep=lambda s: None)
    assert sorted(done) == ["vega-medcpt", "vega-nemotron"]
    assert r.asked == [("park", "vega-nemotron"), ("shutdown", "vega-medcpt")]
    assert set(r.models) == {"vega-nemotron", "vega-dar", "louise-chat"}, "the parked ones keep their process"


# ---- a parked model comes back ----------------------------------------------------

def test_a_request_to_a_parked_model_makes_its_room_and_brings_it_back(node, monkeypatch):
    r = node({"vega-bge-m3": 2_000, "omnivoice-cpp": 1_700}, extra_mib=27_300, parks=["vega-bge-m3"])
    r.parked["vega-bge-m3"] = 1_700
    _used(vega_bge_m3=1, omnivoice_cpp=20)
    _known(vega_bge_m3=2_000)
    warmed = []

    def warm(bound):
        warmed.append(bound)
        r.parked.pop("vega-bge-m3")                     # LocalAI sends the LoadModel before the request goes
        return SimpleNamespace(status_code=200)
    waited = model_load.ensure_loaded(S, "vega-bge-m3", warm, caller="embed", max_age_s=0.0)
    assert warmed and waited is not None, "one minimal request brought it back"
    assert _verbs(r, "shutdown") == ["omnivoice-cpp"], "room is made for a return as for a load"
    assert [c.outcome for c in node.recorded] == [model_load.RETURNED]
    assert "came back from host memory" in model_load.message(node.recorded[0])
    assert model_load.parked(S) == {} and model_load.ensure_loaded(
        S, "vega-bge-m3", lambda b: warmed.append("again"), caller="embed", max_age_s=0.0) is None
    assert "again" not in warmed, "back on the card: nothing is warmed twice"


def test_a_return_the_card_refuses_leaves_it_parked_and_says_so(node):
    r = node({"vega-bge-m3": 2_000}, parks=["vega-bge-m3"])
    r.parked["vega-bge-m3"] = 1_700
    _known(vega_bge_m3=2_000)
    refused = SimpleNamespace(status_code=503, text="", headers={"Retry-After": "2"}, json=lambda: {
        "error": {"code": 503, "type": "no_room_on_device",
                  "message": "vega-bge-m3: the weights cannot come back to the device now"}})
    model_load.ensure_loaded(S, "vega-bge-m3", lambda bound: refused, caller="embed", max_age_s=0.0)
    assert [c.outcome for c in node.recorded] == ["not returned: failed: HTTP 503"]
    said = model_load.message(node.recorded[0])
    assert "did not come back" in said and "deferred" in said
    assert model_load.parked(S) == {"vega-bge-m3": (1_700 * MIB, 1_700 * MIB)}, "read again: still parked"


def test_a_loaded_model_that_parks_is_not_warmed(node):
    node({"vega-bge-m3": 2_000}, parks=["vega-bge-m3"])
    assert model_load.ensure_loaded(S, "vega-bge-m3", lambda b: pytest.fail("warmed"), caller="embed",
                                    max_age_s=0.0) is None


# ---- the keeper -------------------------------------------------------------------

def test_the_keeper_takes_a_parked_model_as_not_loaded_and_counts_what_it_holds(node):
    r = node({"vega-bge-m3": 2_000, "louise-chat": 7_000}, parks=["vega-bge-m3"])
    bge = resident.Resident(S, "vega-bge-m3", ("query encoder",))
    assert resident._loaded(bge) is True
    r.parked["vega-bge-m3"] = 1_700
    model_load.reset()
    _known(vega_bge_m3=2_000, louise_chat=7_000)
    assert resident._loaded(bge) is False, "listed, and not on the card: brought back as a model is loaded"
    mem = resident.Memory()
    held = resident._held_now([S], mem)
    assert held[("vega-bge-m3", 10 + 1)][:2] == (RESIDUE * MIB, RESIDUE * MIB), \
        "at what its process holds — never 'whole', which is what a model partly paged out counts"
    assert resident._overcommitted([S], mem, (32_656 * MIB, 7_300 * MIB)) is None, \
        "it reads short because it was moved on purpose: no over-commitment is said"


def test_the_keeper_writes_the_hosts_floor_where_a_model_parks_and_nowhere_else(node, monkeypatch):
    """`park_host_floor_bytes` = the node's one host floor ([models]
    page_floor_gib): read whole, one key set, written whole, read again.
    Never where no model parks — a server without the patch is never asked —
    and nothing of the object but the key is ever said (invariant 8)."""
    floor = platform_config.load().models.page_floor_gib * GIB
    r = node({"vega-bge-m3": 2_000, "louise-chat": 7_000}, patched=False)
    model_load.loaded_models(S, 0.0)
    resident._threshold(S)
    assert resident._parked_follow([S]) == {"floor": [], "stopped": []} and r.posted == []

    r = node({"vega-bge-m3": 2_000, "louise-chat": 7_000}, parks=["vega-bge-m3"])
    model_load.reset()
    model_load.loaded_models(S, 0.0)
    resident._threshold(S)
    assert resident._parked_follow([S])["floor"] == [S]
    assert len(r.posted) == 1 and r.posted[0]["park_host_floor_bytes"] == floor
    assert r.posted[0]["api_keys"] == [SECRET] and r.posted[0]["memory_reclaimer_threshold"] == 0.95, \
        "the object is written back whole: a partial body is not documented to leave the rest alone"
    assert r.settings["park_host_floor_bytes"] == floor
    said = [t for t in node.said if "park floor set" in t]
    assert len(said) == 1 and f"{floor // MIB:,} MiB" in said[0] and "vega-bge-m3" in said[0]
    assert not [t for t in node.said if SECRET in t]
    resident._threshold(S)
    assert resident._parked_follow([S])["floor"] == [] and len(r.posted) == 1, "already so: nothing is written"


def test_a_floor_the_server_does_not_take_is_said_once_and_asked_again(node):
    r = node({"vega-bge-m3": 2_000}, parks=["vega-bge-m3"], floor=None)
    r.patched = False                                   # it lists `parks` and drops the key: not taken
    model_load.loaded_models(S, 0.0)
    for _ in range(3):
        resident._threshold(S)
        assert resident._parked_follow([S])["floor"] == []
    said = [t for t in node.said if "park floor" in t]
    assert len(said) == 1 and "until it is set nothing is parked" in said[0] and SECRET not in said[0]
    assert len(r.posted) == 3, "asked again at each beat"


def test_a_settings_write_that_is_not_answered_is_not_sent_again_at_each_beat(node, monkeypatch):
    """Tensor, 2026-11-25, on the real server: a whole-object POST
    /api/settings never answers where LocalAI's watchdog exists only for a
    limit of loaded models and no periodic check is on. Each repeat would
    leave one more request hanging: the server is left alone for an hour,
    said once — for the floor and for the reclaimer's switch alike."""
    r = node({"vega-bge-m3": 2_000}, parks=["vega-bge-m3"])
    sent = []

    def post(url, json=None, **kw):
        if url.endswith("/api/settings"):
            sent.append(json)
            raise httpx.ReadTimeout("no answer")
        return r.post(url, json=json, **kw)
    monkeypatch.setattr(httpc, "post", post)
    clock = {"t": 1_000.0}
    monkeypatch.setattr(resident, "_now", lambda: clock["t"])
    model_load.loaded_models(S, 0.0)
    for _ in range(4):
        resident._threshold(S)
        assert resident._parked_follow([S])["floor"] == []
        resident._reclaimer_follows([S])              # the reclaimer's writer honours the same hold
        clock["t"] += 60.0
    assert len(sent) == 1, "one write, never answered: not repeated at each beat"
    said = [t for t in node.said if "not sent again at each beat" in t]
    assert len(said) == 1 and "park floor" in said[0] and "10 min" in said[0] and SECRET not in said[0]
    clock["t"] += localai_reclaimer.SETTINGS_HOLD_S
    resident._threshold(S)
    resident._parked_follow([S])
    assert len(sent) == 2, "after the hold it is asked once more"
    for nothing_sent in (httpx.ConnectTimeout("down"), httpx.PoolTimeout("no connection free"),
                         httpx.WriteTimeout("not written")):
        with pytest.raises(localai_reclaimer.NotTaken) as e:
            monkeypatch.setattr(httpc, "post", lambda *a, _x=nothing_sent, **k: (_ for _ in ()).throw(_x))
            localai_reclaimer.set_park_floor(S, 5)
        assert not isinstance(e.value, localai_reclaimer.NotAnswered), \
            f"{type(nothing_sent).__name__}: the write never went out whole — asked again at the next beat"


def test_the_host_under_its_floor_takes_its_memory_back_from_the_parked_model_used_longest_ago(node, monkeypatch):
    """LocalAI never stops a parked model by itself, and checks the floor
    only when it parks: the parse pool's room comes before a quick return.
    One a beat; never while the host is above the floor; never a pinned one."""
    floor = platform_config.load().models.page_floor_gib * GIB
    r = node({"vega-bge-m3": 2_000, "vega-dar": 2_300, "louise-chat": 7_000}, parks=["vega-bge-m3", "vega-dar"])
    r.parked.update({"vega-bge-m3": 1_700, "vega-dar": 2_000})
    _used(vega_bge_m3=1, vega_dar=9)
    model_load.loaded_models(S, 0.0)
    monkeypatch.setattr(model_evict, "host_available", lambda: floor + GIB)
    assert model_evict.host_claims([S]) == [] and r.asked == []
    monkeypatch.setattr(model_evict, "host_available", lambda: floor - GIB)
    assert model_evict.host_claims([S]) == ["vega-dar"] and r.asked == [("shutdown", "vega-dar")]
    said = [t for t in node.said if "was parked" in t]
    assert len(said) == 1 and "2,000 MiB of weights in host memory" in said[0] and "under its floor" in said[0]
    monkeypatch.setattr(model_pins, "recorded", lambda: {(S, "vega-bge-m3")})
    model_load.loaded_models(S, 0.0)
    assert model_evict.host_claims([S]) == [], "a gate's or a pass's pin: never touched"
    monkeypatch.setattr(model_pins, "recorded", lambda: None)
    assert model_evict.host_claims([S]) == [], "the pin record unread: nothing is stopped on a guess"
    monkeypatch.setattr(model_pins, "recorded", lambda: frozenset())
    monkeypatch.setattr(model_pins, "read_pinned", lambda server, model: True)
    assert model_evict.host_claims([S]) == [], "a pin LocalAI holds and the record does not: the operator's"
    monkeypatch.setattr(model_pins, "read_pinned", lambda server, model: False)
    monkeypatch.setattr(model_use, "last_used", lambda: {})
    assert model_evict.host_claims([S]) == [], "a model Louise does not know — parked by hand — is never touched"
    monkeypatch.setattr(model_evict, "host_available", lambda: None)
    assert model_evict.host_claims([S]) == [], "unread: nothing is stopped on a guess"


def test_the_one_settings_writer_tells_a_switch_from_a_count(node):
    r = node({}, floor=0)
    assert localai_reclaimer.set_park_floor(S, 0) is False and r.posted == []
    assert localai_reclaimer.set_park_floor(S, 12 * GIB) is True
    assert r.posted[0]["park_host_floor_bytes"] == 12 * GIB and r.posted[0]["memory_reclaimer_enabled"] is True
    r.settings["park_host_floor_bytes"] = 1
    r.settings["memory_reclaimer_enabled"] = True
    assert localai_reclaimer.set_key(S, "park_host_floor_bytes", True) is True, "True is not 1"


# ---- leaving a paging rule gives the host its memory back --------------------------

def _paged_out(r: Runner, *models: str) -> None:
    """The driver holds these in host memory: each reads 0 MiB on the card."""
    was = r.on_card
    r.on_card = lambda m: 0 if m in models else was(m)


def test_idle_and_in_host_memory_is_unloaded_where_the_rule_does_not_page(node, monkeypatch):
    """München, 2026-11-25 22:30Z, the first query -> embed switch: MADLAD,
    both rerankers and the speech model read 0 MiB on the card, 15.9 GiB of
    them in host memory — still of embed mode's set, so the keeper loaded
    nothing; idle, so nothing brought them back; and LocalAI counts the card.
    The parse pool took MemAvailable from 21.9 to 3.6 GiB in two minutes."""
    r = node({"louise-translate-madlad": 6_000, "louise-rerank-medcpt": 800, "louise-tts-omnivoice": 1_700,
              "vega-qwen3-4b": 2_700, "louise-chat-9b": 5_900, "by-hand": 4_000, "vega-bge-m3": 600,
              "never-asked": 900})
    _used(louise_translate_madlad=1, louise_rerank_medcpt=1, louise_tts_omnivoice=2, vega_qwen3_4b=0.001,
          louise_chat_9b=0.1, vega_bge_m3=3)
    _known(louise_translate_madlad=6_000, louise_rerank_medcpt=800, louise_tts_omnivoice=1_700,
           vega_qwen3_4b=2_700, louise_chat_9b=5_900, by_hand=4_000, vega_bge_m3=600, never_asked=900)
    _paged_out(r, "louise-translate-madlad", "louise-rerank-medcpt", "louise-tts-omnivoice", "by-hand",
               "vega-qwen3-4b", "never-asked")
    in_set = {(S, "vega-qwen3-4b"), (S, "louise-chat-9b"), (S, "louise-translate-madlad"), (S, "never-asked")}
    monkeypatch.setattr(model_evict, "rule", lambda: ("page", 12 * GIB, None))
    model_load.loaded_models(S, 0.0)
    assert model_evict.host_leftovers([S], in_set) == [] and r.asked == [], "query mode: that is where they live"

    monkeypatch.setattr(model_evict, "rule", lambda: ("unload", 12 * GIB, 3 * GIB))
    gone = [m for _s, m in model_evict.host_leftovers([S], in_set)]
    assert gone == ["louise-rerank-medcpt", "louise-translate-madlad", "louise-tts-omnivoice", "never-asked"]
    assert _verbs(r, "shutdown") == gone and not _verbs(r, "park")
    assert "vega-qwen3-4b" in r.models, "used four seconds ago: its own batches bring it back"
    assert "by-hand" in r.models, "a model Louise does not know is never touched"
    assert "vega-bge-m3" in r.models, "idle and ON the card: LocalAI's to claim, as always"
    said = [t for t in node.said if t.startswith("card: unloaded")]
    assert len(said) == 1 and "louise-translate-madlad (about 6,000 MiB in host memory)" in said[0]
    assert "loads back onto the card those its budget holds (louise-translate-madlad, never-asked)" in said[0]
    model_load.loaded_models(S, 0.0)
    assert model_evict.host_leftovers([S], in_set) == [], "said and done once"


def test_a_model_whole_on_the_card_is_never_taken_for_one_in_host_memory(node, monkeypatch):
    """The review of 2026-11-25, reproduced: "under its floor of the LARGEST
    it was seen holding" stopped vega-dar-legal-it — about 1.8 GiB at rest,
    8,453 MiB at its largest (a context made on demand), 0.21 — whole and on
    the card, at every idle minute, and the keeper loaded it back each time.
    The test is against what the model holds AT REST, where that is known;
    unknown, with something on the card, it is left alone."""
    r = node({"vega-dar-legal-it": 1_816, "learnt": 2_500, "half-out": 3_000})
    _used(vega_dar_legal_it=1, learnt=1, half_out=1)
    _known(vega_dar_legal_it=8_453, learnt=5_700, half_out=3_300)
    monkeypatch.setattr(model_evict, "rule", lambda: ("unload", 12 * GIB, 3 * GIB))
    rests = {"learnt": 2_500 * MIB, "half-out": 3_000 * MIB}
    monkeypatch.setattr(resident, "rest_known", lambda s, m, mem: rests.get(m))
    model_load.loaded_models(S, 0.0)
    assert model_evict.host_leftovers([S], set()) == [] and r.asked == [], \
        "0.21 of its largest and whole; at rest; and one whose rest nobody knows"
    was = r.on_card
    r.on_card = lambda m: {"half-out": 1_400, "learnt": 1_300}.get(m, was(m))      # 0.47 and 0.52 of their rest
    model_load.loaded_models(S, 0.0)
    assert [m for _s, m in model_evict.host_leftovers([S], set())] == ["half-out"], \
        "under half of what it rests at: in host memory; just over half: on the card"


#: THE NODE'S OWN SET IN EMBED MODE (München, `resident show` and the card, 2026-11-25 22:32–22:41Z):
#: model -> (MiB on the card, MiB at rest as the keeper counts it or None where it is not known, MiB at its largest)
NODE_EMBED_MODE = {
    "louise-chat-qwen3.5-9b-q4-p0": (5_882, 5_882, 8_104),
    "louise-translate-madlad": (7_221, 7_221, 8_005),
    "louise-tts-omnivoice": (1_672, 1_672, 2_867),
    "louise-rerank-nemotron-1b": (3_163, 3_163, 3_348),
    "louise-rerank-medcpt": (423, None, 423),
    "vega-nemotron-embed": (2_519, 2_229, 4_404),
    "vega-qwen3-embedding-4b": (2_736, 2_736, 5_685),
    "vega-medcpt-query": (197, None, 481),
    "vega-bge-m3": (615, 615, 1_335),
    "vega-dar-legal-it": (1_816, None, 8_453),          # 0.21 of its largest, whole and on the card
    "vega-medcpt-article-ov": (312, None, 312),
}


def test_on_the_nodes_own_numbers_nothing_healthy_is_stopped_and_the_four_of_that_night_are(node, monkeypatch):
    """A rule that STOPS a model is run against the sizes of the models it
    will meet (the lesson of the review, 2026-11-25): the node's set in embed
    mode as München read it — nothing is stopped; and the same set as it
    stood at the switch, the four idle Ask models reading 0 MiB — exactly
    those four."""
    r = node({m: v[0] for m, v in NODE_EMBED_MODE.items()})
    for m, (_here, _rest, largest) in NODE_EMBED_MODE.items():
        with model_load._lock:
            model_load._sizes[(S, m)] = largest * MIB
        node_state.touch_encoder(S, m, now=time.time() - 3_600)
    monkeypatch.setattr(resident, "rest_known",
                        lambda s, m, mem: (NODE_EMBED_MODE[m][1] or 0) * MIB or None)
    monkeypatch.setattr(model_evict, "rule", lambda: ("unload", 12 * GIB, 3 * GIB))
    in_set = {(S, m) for m in NODE_EMBED_MODE}
    model_load.loaded_models(S, 0.0)
    assert model_evict.host_leftovers([S], in_set) == [] and r.asked == []
    four = ("louise-translate-madlad", "louise-tts-omnivoice", "louise-rerank-nemotron-1b", "louise-rerank-medcpt")
    _paged_out(r, *four)
    model_load.loaded_models(S, 0.0)
    assert sorted(m for _s, m in model_evict.host_leftovers([S], in_set)) == sorted(four)


def test_a_paged_out_model_reads_a_mib_or_less_not_zero_and_is_still_taken(node, monkeypatch):
    """The node's own paged-out readings (2026-11-25): tts-omnivoice 1.1 MiB,
    dar-legal-it 0.5, nemotron 0.4, bge-m3 0.4. "Nothing on the card" is
    under 2 % of the largest, whatever is known of the model at rest."""
    r = node({"louise-tts-omnivoice": 2_867, "vega-dar-legal-it": 8_453})
    _used(louise_tts_omnivoice=1, vega_dar_legal_it=1)
    _known(louise_tts_omnivoice=2_867, vega_dar_legal_it=8_453)
    r.get_real = r.get

    def get(url, **kw):
        got = r.get_real(url, **kw)
        if url.endswith("/system"):
            rows = got.json()["loaded_models"]
            for row in rows:
                row["size_vram"] = {"louise-tts-omnivoice": 1_153_434, "vega-dar-legal-it": 524_288}[row["id"]]
            return SimpleNamespace(status_code=200, json=lambda: {"loaded_models": rows})
        return got
    monkeypatch.setattr(httpc, "get", get)
    monkeypatch.setattr(model_evict, "rule", lambda: ("unload", 12 * GIB, 3 * GIB))
    monkeypatch.setattr(resident, "rest_known", lambda s, m, mem: None)       # nobody knows what they rest at
    model_load.loaded_models(S, 0.0)
    assert sorted(m for _s, m in model_evict.host_leftovers([S], set())) == \
        ["louise-tts-omnivoice", "vega-dar-legal-it"]


def test_a_model_this_stopped_is_not_stopped_by_it_again_within_the_hour(node, monkeypatch):
    """Loaded back and paged out again, the card is too full for the set:
    the budget's to say — never a stop and a load at every beat."""
    r = node({"louise-translate-madlad": 6_000})
    _used(louise_translate_madlad=1)
    _known(louise_translate_madlad=6_000)
    _paged_out(r, "louise-translate-madlad")
    monkeypatch.setattr(model_evict, "rule", lambda: ("unload", 12 * GIB, 3 * GIB))
    model_load.loaded_models(S, 0.0)
    assert len(model_evict.host_leftovers([S], set())) == 1
    r.models["louise-translate-madlad"] = 6_000         # the keeper loaded it back, and it reads 0 again
    model_load.loaded_models(S, 0.0)
    assert model_evict.host_leftovers([S], set()) == [] and len(_verbs(r, "shutdown")) == 1
    model_evict._left_at[(S, "louise-translate-madlad")] -= model_evict.LEFTOVER_AGAIN_S + 1
    assert len(model_evict.host_leftovers([S], set())) == 1, "an hour later it may be asked again"

    # ...and a spell under a paging rule ends the hour: query -> embed -> query -> embed within it
    # must stop what the second spell in query mode left in host memory (the 22:30Z case, again)
    r.models["louise-translate-madlad"] = 6_000
    model_load.loaded_models(S, 0.0)
    assert model_evict.host_leftovers([S], set()) == []
    monkeypatch.setattr(model_evict, "rule", lambda: ("page", 12 * GIB, None))
    assert model_evict.host_leftovers([S], set()) == []
    monkeypatch.setattr(model_evict, "rule", lambda: ("unload", 12 * GIB, 3 * GIB))
    assert len(model_evict.host_leftovers([S], set())) == 1


def test_a_pinned_a_busy_a_parked_and_a_model_that_holds_no_vram_are_left(node, monkeypatch):
    r = node({"pinned": 2_000, "busy": 2_000, "vega-dar": 2_300, "on-cpu": 0, "elsewhere": 1_000},
             parks=["vega-dar"], cpu={"busy": 80.0})
    r.parked["vega-dar"] = 2_000
    _used(pinned=1, busy=1, vega_dar=1, on_cpu=1, elsewhere=1)
    _known(pinned=2_000, busy=2_000, vega_dar=2_300, elsewhere=1_000)
    _paged_out(r, "pinned", "busy", "elsewhere")
    monkeypatch.setattr(model_evict, "rule", lambda: ("unload", 12 * GIB, 3 * GIB))
    monkeypatch.setattr(model_pins, "recorded", lambda: {(S, "pinned")})
    mem = resident._memory_now()
    mem.elsewhere = {(S, "elsewhere")}
    monkeypatch.setattr(resident, "_memory_now", lambda: mem)
    model_load.loaded_models(S, 0.0)
    assert model_evict.host_leftovers([S], set()) == [] and r.asked == []
    monkeypatch.setattr(model_pins, "recorded", lambda: None)
    assert model_evict.host_leftovers([S], set()) == [], "the pin record unread: nothing is stopped on a guess"


def test_the_keepers_beat_gives_the_host_back_and_takes_it_for_no_eviction(node, monkeypatch):
    """Never while a question holds the card; and the stop is the mode's own
    doing, not the reclaimer's: no back-off, no penalty on the budget — the
    keeper loads the model back onto the card as it loads any missing one."""
    r = node({"louise-translate-madlad": 6_000, "vega-qwen3-4b": 2_700})
    _used(louise_translate_madlad=1, vega_qwen3_4b=0.001)
    _known(louise_translate_madlad=6_000, vega_qwen3_4b=2_700)
    _paged_out(r, "louise-translate-madlad")
    monkeypatch.setattr(model_evict, "rule", lambda: ("unload", 12 * GIB, 3 * GIB))
    want = [resident.Resident(S, "vega-qwen3-4b", ("encoder",)), resident.Resident(S, "louise-translate-madlad",
                                                                                   ("translation",))]
    monkeypatch.setattr(resident, "wanted", lambda *a, **k: want)
    monkeypatch.setattr(resident, "preloads", lambda state=None: (True, ""))
    monkeypatch.setattr(resident, "_question_now", lambda: True)
    resident.beat([], embed_mode=lambda c: "all", state="running")
    assert _verbs(r, "shutdown") == [], "a question's models may be on their way back to the card"
    with resident._lock:
        resident._track[(S, "louise-translate-madlad")] = resident._Track()
        resident._track[(S, "louise-translate-madlad")].warmed_at = resident._now() - 30.0   # warmed half a minute ago
    monkeypatch.setattr(resident, "_due", resident._due)          # the real back-off, by the node's own record
    monkeypatch.setattr(resident, "_question_now", lambda: False)
    monkeypatch.setattr(resident, "_settling", lambda now=None: True)
    resident.beat([], embed_mode=lambda c: "all", state="running")
    assert _verbs(r, "shutdown") == [], "nor in the minutes after a resume, beside the keeper's own loads"
    monkeypatch.setattr(resident, "_settling", lambda now=None: False)
    penalized = []
    monkeypatch.setattr(resident, "_penalize", lambda r_, stayed: penalized.append(r_.model))
    resident.beat([], embed_mode=lambda c: "all", state="running")
    assert _verbs(r, "shutdown") == ["louise-translate-madlad"] and "vega-qwen3-4b" in r.models
    madlad = want[1]
    assert resident._due(madlad, resident._now()) is True and penalized == [], \
        "found off the card 30 s after its load, and not taken for an eviction"
    assert not [t for t in node.said if "off the card again" in t]


# ---- "the device has no room now" is a deferral ------------------------------------

def _no_room(message="vega-bge-m3: the weights cannot come back to the device now", after="2") -> httpx.Response:
    return httpx.Response(503, request=httpx.Request("POST", f"{S}/v1/embeddings"),
                          headers={"Retry-After": after} if after is not None else {},
                          json={"error": {"code": 503, "type": "no_room_on_device", "message": message}})


def test_no_room_is_told_from_every_other_503():
    assert httpc.no_room(_no_room()) == 2.0
    assert httpc.no_room(_no_room(after=None)) == httpc.NO_ROOM_RETRY_S
    loading = httpx.Response(503, request=httpx.Request("POST", S), json={"error": {"type": "", "message": "loading"}})
    assert httpc.no_room(loading) is None, "a model that loads, a load in cooldown: 503 too, and not this"
    assert httpc.no_room(httpx.Response(500, request=httpx.Request("POST", S), json={
        "error": {"type": "no_room_on_device"}})) is None, "the status AND the type"
    assert httpc.no_room(httpx.Response(503, request=httpx.Request("POST", S), text="<html>")) is None
    with pytest.raises(httpc.ModelRefused) as e:
        httpc.raise_for_status(_no_room())
    assert e.value.retry_after_s == 2.0 and "cannot come back to the device now" in str(e.value)


@pytest.mark.parametrize("message", ["vega-bge-m3: the weights cannot come back to the device now",
                                     "vega-bge-m3: the context cannot be created now",
                                     "vega-bge-m3: the computation cannot be placed on the device now"])
def test_for_the_document_path_it_is_busy_never_an_inputs_own_refusal(message):
    """The three refusals of the moment are one fact, and none is the
    text's: not over the context, not a numeric failure, not the wrong
    endpoint — the batch is deferred and no document carries an error."""
    from app import collections as collections_mod, embeddings, worker
    recipe = collections_mod.EmbedRecipe(url="http://enc:1", api="openai", model="m", dtype="f16", ctx=512)
    r = _no_room(message)
    assert not embeddings._over_budget(r) and not embeddings._numeric_failure(r)
    e = embeddings._endpoint_error(recipe, r)
    assert type(e) is embeddings.EndpointBusy and e.status == 503
    assert worker.transient_failure(e), "deferred: no error is written to a document"


def test_for_a_question_the_reranker_keeps_the_fused_order(monkeypatch):
    from app import collections as collections_mod, retrieval
    monkeypatch.setattr(retrieval.httpc, "post",
                        lambda url, **k: _no_room("rerank-bge: the context cannot be created now"))
    hits = [{"collection": "c", "book_id": i, "chunk_id": i, "score": 1.0 / (i + 1), "text": f"t{i}"} for i in range(4)]
    rr = collections_mod.RerankRecipe(url=f"{S}/v1/rerank", api="jina", model="rerank-bge")
    assert retrieval._rerank_list("q", hits, rr, 0.4) == (hits, False)


def test_for_a_question_the_chat_and_the_translator_say_why_and_can_tell_it_from_a_fault(monkeypatch):
    from app import llm, translate
    with pytest.raises(llm.ChatRefused) as e:
        llm.refuse_on_error(_no_room("louise-chat: the context cannot be created now"))
    assert e.value.retry_after_s == 2.0 and "cannot be created now" in str(e.value)
    monkeypatch.setattr(config, "TRANSLATE_URL", S)
    monkeypatch.setattr(httpx, "post", lambda url, **k: _no_room("louise-translate-madlad: the weights cannot come "
                                                                 "back to the device now"))
    with pytest.raises(httpx.HTTPStatusError) as e:
        translate._completion({"a": 1}, timeout=1)
    assert getattr(e.value, "retry_after_s", None) == 2.0
