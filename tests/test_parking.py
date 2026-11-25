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


def test_a_model_listed_parked_that_is_on_the_card_is_loaded_and_its_record_is_put_right(node):
    """The record stale one way (the note, §7): a request crossed the park
    and the engine brought the weights back itself. Told from the reading —
    nearer what the model holds loaded than what a park leaves."""
    r = node({"vega-bge-m3": 900}, parks=["vega-bge-m3"])
    _in_set("vega-bge-m3")
    model_load.loaded_models(S, 0.0)                    # seen loaded and at rest: 900 MiB
    r.parked["vega-bge-m3"] = 600
    r.on_card = lambda m: 900                           # ...and its process holds all of it
    model_load.loaded_models(S, 0.0)
    assert model_load.parked(S) == {} and model_load.parked_stale(S) == {"vega-bge-m3"}
    did = resident._parked_follow([S])
    assert did["record"] == ["vega-bge-m3"] and _verbs(r, "load") == ["vega-bge-m3"]
    assert any("listed parked" in t and "says loaded again" in t for t in node.said)


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
    assert len(line) == 1 and "vega-bge-m3 (last used 30.0 h ago, 1,700 MiB to host memory)" in line[0]
    assert "Do parking." in line[0] and not [t for t in node.said if t.startswith("card: unloaded")]


def test_what_a_park_leaves_on_the_card_is_not_counted_as_freed(node):
    """2,000 MiB held, 1,700 moved: a need of 1,900 over what is free takes
    the next candidate too — the pre-check's bound is not the count."""
    r = node({"vega-bge-m3": 2_000, "omnivoice-cpp": 1_700}, extra_mib=27_000, parks=["vega-bge-m3"])
    _used(vega_bge_m3=30, omnivoice_cpp=2)
    _known(big=2_200)                                        # 323 free: 1,877 short, the park gives 1,700
    out = model_evict.make_room_for(S, "big", horizon=time.time())
    assert out.fits is True and out.parked == ("vega-bge-m3",) and out.unloaded == ("omnivoice-cpp",)
    assert r.asked == [("park", "vega-bge-m3"), ("shutdown", "omnivoice-cpp")]


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
    assert resident._parked_follow([S]) == {"floor": [], "record": [], "stopped": []} and r.posted == []

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
