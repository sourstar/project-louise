"""tools/measure/park_told.py: the client that drives parking through a
backend's own gRPC calls (docs/notes/parking-step-2.md §6) — München's tool
for the rows on copies, and the self-test that ran the handlers here.

Its exchange with a real backend is the self-test's (29 of 29 on a CPU
stand-in). Held here, with a backend that is a script: what `cycles` counts
and what it refuses to go on from — a park that did not park, a refused
return that left the model un-parked, vectors that are not the first ones are
COUNTED, never smoothed over — and that the tool's two messages are the
patch's, byte for byte."""
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("park_told", ROOT / "tools" / "measure" / "park_told.py")
park_told = importlib.util.module_from_spec(spec)
spec.loader.exec_module(park_told)

PATCH = ROOT / "deploy" / "localai" / "patches" / "review" / "0023-llama-cpp-park-when-told.patch"
BASE = [[0.1, 0.2], [0.3, 0.4]]


class Script:
    """a backend that parks: the answers of each call, as park_told.Backend gives them"""

    def __init__(self, refuse_load_at=(), refuse_request_at=(), wrong_at=(), free_code="OK", stays_parked=True):
        self.parked = False
        self.cycle = 0
        self.refuse_load_at, self.refuse_request_at, self.wrong_at = set(refuse_load_at), set(refuse_request_at), set(wrong_at)
        self.free_code, self.stays_parked = free_code, stays_parked
        self.calls: list[str] = []

    def free(self):
        self.calls.append("free")
        if self.free_code != "OK":
            return self.free_code, None, "llama-cpp: this model cannot park", 0.0
        self.parked = True
        self.cycle += 1
        meta = json.dumps({"parked": True, "park_size": 100, "ms": 40.0 + self.cycle}).encode()
        return "OK", SimpleNamespace(message="parked", success=True, metadata=meta), "", 0.05

    def status(self):
        return "OK", {"state": "READY", "parked": int(self.parked), "park_size": 100, "total": 100 if self.parked else 0}

    def load(self, model):
        self.calls.append("load")
        if self.cycle in self.refuse_load_at:
            if not self.stays_parked:
                self.parked = False
            return "RESOURCE_EXHAUSTED", None, "the weights cannot come back to the device now", 0.001
        self.parked = False
        return "OK", SimpleNamespace(message="Model already loaded", success=True), "", 0.02

    def embed_all(self):
        self.calls.append("embed")
        if self.cycle in self.refuse_request_at:
            return "RESOURCE_EXHAUSTED", "the weights cannot come back to the device now", 0.001
        self.parked = False
        return "OK", ([[9.0, 9.0], [9.0, 9.0]] if self.cycle in self.wrong_at else BASE), 0.07


def test_cycles_returned_by_loadmodel_are_counted_and_timed():
    b = Script()
    out = park_told.cycles(b, "m", 3, "load", say=lambda _: None)
    assert (out["cycles"], out["same"], out["refused"], out["wrong"]) == (3, 3, 0, [])
    assert out["park_ms"] == [41.0, 42.0, 43.0], "the park as the ENGINE timed it, from Free's metadata"
    assert out["return_ms"] == [20.0, 20.0, 20.0] and out["first_ms"] == [70.0, 70.0, 70.0]
    assert b.calls == ["embed"] + ["free", "load", "embed"] * 3


def test_cycles_returned_by_the_request_send_no_loadmodel():
    b = Script()
    out = park_told.cycles(b, "m", 2, "request", say=lambda _: None)
    assert (out["cycles"], out["same"]) == (2, 2) and "load" not in b.calls and out["return_ms"] == []
    assert "it brings the weights back and creates its context" in park_told.report(out, "request")


def test_a_refused_return_is_counted_and_the_cycles_go_on():
    said: list[str] = []
    out = park_told.cycles(Script(refuse_load_at={2}), "m", 4, "load", say=said.append)
    assert (out["cycles"], out["same"], out["refused"]) == (3, 3, 1)
    assert len(said) == 1 and "cycle 2" in said[0] and "REFUSED" in said[0] and "still parked" in said[0]
    out = park_told.cycles(Script(refuse_request_at={1}), "m", 3, "request", say=said.append)
    assert (out["cycles"], out["refused"]) == (2, 1)


def test_a_refused_return_that_leaves_the_model_unparked_stops_the_run():
    with pytest.raises(SystemExit, match="refused and the model is not parked any more"):
        park_told.cycles(Script(refuse_load_at={1}, stays_parked=False), "m", 2, "load", say=lambda _: None)


def test_vectors_that_are_not_the_first_ones_are_counted_by_their_cycle():
    out = park_told.cycles(Script(wrong_at={2, 3}), "m", 4, "load", say=lambda _: None)
    assert (out["cycles"], out["same"], out["wrong"]) == (4, 2, [2, 3])
    assert "WRONG at [2, 3]" in park_told.report(out, "load")


def test_a_model_that_does_not_park_stops_the_run_at_once():
    with pytest.raises(SystemExit, match="Free answered UNIMPLEMENTED .* the model does not park"):
        park_told.cycles(Script(free_code="UNIMPLEMENTED"), "m", 2, "load", say=lambda _: None)


def test_the_report_gives_medians_and_counts():
    text = park_told.report(park_told.cycles(Script(), "m", 3, "load", say=lambda _: None), "load")
    assert text.startswith("3 cycles answered, 3 with the first vectors bit for bit; 0 refused")
    assert "the park, as the engine timed it: 42.0 ms (median of 3)" in text
    assert "the return by LoadModel:          20.0 ms (median of 3)" in text


def test_the_tool_s_two_messages_are_the_patch_s_byte_for_byte():
    header = PATCH.read_text(encoding="utf-8")
    assert re.search(r'^\+inline constexpr const char \* LOAD_ALREADY_LOADED = "(.*)";$', header, re.M).group(1) \
        == park_told.ALREADY_LOADED == "Model already loaded"
    assert re.search(r'^\+inline constexpr const char \* FREE_PARKED = "(.*)";$', header, re.M).group(1) == park_told.PARKED


def test_the_stubs_come_from_the_repository_s_own_proto_and_name_the_calls_it_uses():
    pytest.importorskip("grpc_tools")
    _grpc, pb, pb_grpc = park_told.stubs(None)
    stub = pb_grpc.BackendStub(_grpc.insecure_channel("127.0.0.1:1"))
    for call in ("Health", "Free", "LoadModel", "Embedding", "Status"):
        assert hasattr(stub, call), call
    assert pb.ModelOptions(Model="m", ModelFile="f", Embeddings=True, Options=["context_on_demand:true"]).Model == "m"
    assert "metadata" in pb.Result.DESCRIPTOR.fields_by_name, "Free's answer carries the bytes parked there"
    assert pb.StatusResponse.State.Name(2) == "READY"
