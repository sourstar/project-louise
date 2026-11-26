"""tools/measure/park_http.py — parking's rows on a card, through LocalAI's HTTP
alone (docs/notes/parking-step-2.md §8).

The tool is what says "it holds" for the two halves together, so its verdicts
are held here against a stand-in LocalAI that speaks the shapes staged 0027
answers with: a run that parks and returns the same bytes passes; one whose
vectors move, whose record stays "parked", whose refusal is not a deferral, or
whose busy model is parked anyway FAILS; and a run that showed nothing says
NOT RUN instead of passing."""
import hashlib
import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("park_http", ROOT / "tools" / "measure" / "park_http.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

MIB = 1 << 20


class StandIn:
    """a LocalAI with one model that parks, as staged 0027 answers"""

    def __init__(self) -> None:
        self.parks_flag = True          # the model's file says `park: true`
        self.state = "loaded"
        self.floor = 12 << 30
        self.settings = {"memory_reclaimer_enabled": False, "park_host_floor_bytes": 0, "threads": 8}
        self.settings["park_host_floor_bytes"] = self.floor
        self.park_size = 575 * MIB
        self.held = 896 * MIB           # what the process holds of the device when loaded
        self.refuse_returns = 0         # the next N returns are refused
        self.refusal_status = 503       # ... with this status
        self.corrupt_after = None       # after this many returns the vectors change
        self.stuck_parked = False       # the record stays "parked" after a return
        self.park_answer = None         # (status, type): what the park answers instead of parking
        self.park_while_busy = False    # the defect: a busy model is parked
        self.slow = 0.0                 # seconds a request of more than sixteen sentences takes
        self.settle = 0.0               # a park within this long of a request is 409 model_busy, as the real server's
        self.last_request = 0.0
        self.running = 0
        self.returns = 0
        # the ENGINE's own counts, as LocalAI's /backend/monitor gives its backend's Status
        self.n_parks = self.n_unparks = self.n_no_room = 0
        self.monitor = "counts"         # "sampled": LocalAI's fallback for a backend without Status; "none": no route
        self.reload_at = None           # this return FAILS and is paid as a load: a new process, the counts at 0
        self.uncounted_park = False     # the server says parked, the engine counted nothing
        self.calls: list[str] = []
        self.headers: list[dict] = []
        self.lock = threading.Lock()

    def vector(self, text: str) -> list[float]:
        salt = "moved" if self.corrupt_after is not None and self.returns >= self.corrupt_after else ""
        h = hashlib.sha256((text + salt).encode()).digest()
        return [b / 255.0 for b in h[:8]]

    def row(self) -> dict:
        row = {"id": "copy", "backend": "louise-sycl-f16-llama-cpp-ed93e3c1b2e2-stack"}
        if self.parks_flag:
            row["parks"] = True
            row["state"] = self.state
        parked = self.state == "parked"
        row["size_vram"] = self.held - self.park_size if parked else self.held
        if parked and self.parks_flag:
            row["parked"] = {"size_host": self.park_size, "size_vram_needed": self.park_size, "since": "2026-11-26T08:00:00Z"}
        return row


@pytest.fixture
def localai():
    stand_in = StandIn()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):   # quiet
            pass

        def _answer(self, status: int, body: object, headers: dict | None = None) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _error(self, status: int, kind: str, message: str, headers: dict | None = None) -> None:
            self._answer(status, {"error": {"code": status, "type": kind, "message": f'model "copy": {message}'}}, headers)

        def do_GET(self):
            stand_in.calls.append(f"GET {self.path}")
            stand_in.headers.append(dict(self.headers))
            if self.path == "/system":
                self._answer(200, {"backends": [], "loaded_models": [stand_in.row(), {"id": "another", "size_vram": 5}]})
            elif self.path == "/api/settings":
                self._answer(200, stand_in.settings)
            elif self.path == "/backend/monitor?model=copy" and stand_in.monitor == "counts":
                parked = stand_in.state == "parked"
                self._answer(200, {"state": 2, "memory": {"total": 1208184832, "breakdown": {
                    "no_room": stand_in.n_no_room, "park_size": stand_in.park_size, "parked": int(parked),
                    "parked_host": stand_in.park_size if parked else 0, "parks": stand_in.n_parks,
                    "rss": 110960640, "unparks": stand_in.n_unparks}}})
            elif self.path == "/backend/monitor?model=copy" and stand_in.monitor == "sampled":
                self._answer(200, {"state": -1, "memory": {"total": 1208184832, "breakdown": {"gopsutil-RSS": 110960640}}})
            elif self.path.startswith("/backend/monitor?model=") and stand_in.monitor != "none":
                self._answer(500, {"error": {"code": 500, "message": "backend nobody is not currently loaded", "type": ""}})
            else:
                self._answer(404, {"error": {"code": 404, "message": "Resource not found"}})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            stand_in.calls.append(f"POST {self.path}")
            if self.path == "/api/settings":
                stand_in.settings = body
                stand_in.floor = body.get("park_host_floor_bytes", 0)
                return self._answer(200, {"success": True})
            if self.path == "/backend/park":
                if stand_in.park_answer:
                    return self._error(stand_in.park_answer[0], stand_in.park_answer[1], "not parked")
                if stand_in.running and not stand_in.park_while_busy:
                    return self._error(409, "model_busy", "model is busy")
                if time.monotonic() - stand_in.last_request < stand_in.settle:
                    return self._error(409, "model_busy", "its last request was a moment ago")
                if stand_in.floor == 0:
                    return self._error(412, "parking_off", "parking is off: no host memory floor is set")
                if stand_in.state != "parked" and not stand_in.uncounted_park:
                    stand_in.n_parks += 1
                stand_in.state = "parked"
                return self._answer(200, {"message": "model parked", "parked": stand_in.row()["parked"]})
            if self.path in ("/backend/load", "/v1/embeddings"):
                if stand_in.state == "parked":
                    with stand_in.lock:
                        if stand_in.refuse_returns > 0:
                            stand_in.refuse_returns -= 1
                            stand_in.n_no_room += 1
                            return self._error(stand_in.refusal_status, "no_room_on_device",
                                               "the weights cannot come back to the device now", {"Retry-After": "2"})
                        stand_in.returns += 1
                        if stand_in.returns == stand_in.reload_at:
                            stand_in.n_parks = stand_in.n_unparks = stand_in.n_no_room = 0
                        else:
                            stand_in.n_unparks += 1
                        if not stand_in.stuck_parked:
                            stand_in.state = "loaded"
                if self.path == "/backend/load":
                    return self._answer(200, {"loaded": ["copy"], "message": "model loaded"})
                texts = body["input"]
                with stand_in.lock:
                    stand_in.running += 1
                try:
                    if len(texts) > 16 and stand_in.slow:
                        time.sleep(stand_in.slow)
                    return self._answer(200, {"data": [{"embedding": stand_in.vector(t)} for t in texts]})
                finally:
                    with stand_in.lock:
                        stand_in.running -= 1
                        stand_in.last_request = time.monotonic()
            self._answer(404, {"error": {"code": 404, "message": "Resource not found"}})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield stand_in, tool.Server(f"http://127.0.0.1:{httpd.server_address[1]}")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_cycles_that_park_and_return_the_same_bytes_pass_and_say_what_each_took(localai):
    stand_in, server = localai
    passed, lines = tool.cycles(server, "copy", 5, "request")
    assert passed, "\n".join(lines)
    assert lines[0].startswith("before: parks, state 'loaded', size_vram 896 MiB")
    assert lines[-1].startswith("5 of 5 cycles: the same bytes; a park ")
    assert stand_in.calls.count("POST /backend/park") == 5 and "POST /backend/load" not in stand_in.calls
    assert lines[1] == "the engine's counts before: 0 parked, 0 back, 0 refused for room, on the device"
    assert lines[-3] == ("the engine's counts after: 5 parked, 5 back, 0 refused for room, on the device - "
                         "5 parks and 5 returns COUNTED, in the one process")
    assert stand_in.calls.count("GET /backend/monitor?model=copy") == 1 + 5 * 3 + 1
    cycle = lines[2]
    assert "575 MiB to the host" in cycle and "the process holds 321 MiB of the device, 575 MiB given back" in cycle
    assert "back by the request in" in cycle and cycle.endswith("vectors identical")


def test_a_park_asked_right_after_a_request_waits_the_second_out_and_says_so(localai):
    """The real server takes a model that answered a request under a second ago for busy. Found on it: a first
    cut of `cycles` asked once, and would have failed every cycle."""
    stand_in, server = localai
    stand_in.settle = 0.3
    passed, lines = tool.cycles(server, "copy", 2, "request")
    assert passed, "\n".join(lines)
    told = next(line for line in lines if line.startswith("each park was asked right after a request"))
    assert int(told.split("answered 409 model_busy ")[1].split()[0]) >= 2
    # and a model that stays busy past the tool's patience is a failure said as one, not a hang
    stand_in.settle = 60.0
    status, answer, _, busy_first = tool.park_when_idle(server, "copy", patience=0.3)
    assert status == 409 and tool.error_of(answer)[0] == "model_busy" and busy_first >= 1


def test_a_return_by_backend_load_is_asked_for_each_cycle(localai):
    stand_in, server = localai
    passed, lines = tool.cycles(server, "copy", 3, "load")
    assert passed, "\n".join(lines)
    assert stand_in.calls.count("POST /backend/load") == 3 and "back by /backend/load and a request" in lines[2]


def test_vectors_that_move_after_a_return_fail_the_run_at_that_cycle(localai):
    stand_in, server = localai
    stand_in.corrupt_after = 2
    passed, lines = tool.cycles(server, "copy", 5, "request")
    assert not passed
    assert "vectors DIFFERENT: " in lines[-2] and lines[-1] == "FAIL: cycle 2: the vectors are not the first ones"
    assert stand_in.calls.count("POST /backend/park") == 2, "it stops at the cycle that failed"


def test_a_record_that_stays_parked_after_the_return_fails(localai):
    stand_in, server = localai
    stand_in.stuck_parked = True
    passed, lines = tool.cycles(server, "copy", 3, "request")
    assert not passed and lines[-1] == "FAIL: cycle 1: the model is not listed loaded after its return"


def test_a_return_that_failed_and_was_paid_as_a_load_is_not_a_cycle(localai):
    """The hole the counts close: LocalAI answers the request that met a FAILED return from a new process, HTTP
    200, the same vectors, the model listed loaded. Without the engine's counts that read as a cycle (seen on
    LocalAI's real server with the CPU stand-in's failing return, 2026-11-26: 200 in 0.45 s, the counts at 0)."""
    stand_in, server = localai
    stand_in.reload_at = 3
    passed, lines = tool.cycles(server, "copy", 5, "request")
    assert not passed
    assert lines[-2].endswith("vectors identical"), "everything a caller sees says it worked"
    assert lines[-1] == ("FAIL: cycle 3: answered with the same bytes, but the engine's counts started again "
                         "(0 parked, 0 back, 0 refused for room, on the device; they were 3 parked, 2 back, "
                         "0 refused for room, away): another process answers - a return that FAILED and "
                         "was paid as a load is not a cycle")
    assert stand_in.calls.count("POST /backend/park") == 3, "it stops at the cycle that was not one"
    # and at the FIRST cycle, where the counts before it were 0 too (the case the real server showed)
    stand_in.reload_at, stand_in.state = stand_in.returns + 1, "loaded"
    passed, lines = tool.cycles(server, "copy", 5, "request")
    assert not passed and "FAIL: cycle 1: answered with the same bytes, but the engine's counts started again" in lines[-1]


def test_a_park_the_engine_did_not_count_fails_and_counts_that_cannot_be_read_are_not_run(localai):
    stand_in, server = localai
    stand_in.uncounted_park = True
    passed, lines = tool.cycles(server, "copy", 3, "request")
    assert not passed and lines[-1].startswith("FAIL: cycle 1: parked by its answer, but the engine counts 0 parked")
    # LocalAI's fallback for a backend that has no Status (a model that cannot park), and a server without the route
    for monitor in ("sampled", "none"):
        stand_in.uncounted_park, stand_in.state, stand_in.monitor = False, "loaded", monitor
        parks = stand_in.calls.count("POST /backend/park")
        passed, lines = tool.cycles(server, "copy", 3, "request")
        assert not passed and lines[-1].startswith("NOT RUN: the engine's counts cannot be read"), lines[-1]
        assert stand_in.calls.count("POST /backend/park") == parks, "nothing is parked where nothing can be counted"
    assert server.counts("nobody") is None


def test_a_park_that_is_refused_is_said_with_its_reason(localai):
    stand_in, server = localai
    stand_in.park_answer = (412, "host_memory_floor")
    passed, lines = tool.cycles(server, "copy", 3, "request")
    assert not passed and lines[-1].startswith("FAIL: cycle 1: the park answered HTTP 412 host_memory_floor")
    stand_in.park_answer = None
    stand_in.floor = 0
    passed, lines = tool.cycles(server, "copy", 3, "request")
    assert not passed and "HTTP 412 parking_off" in lines[-1]


def test_a_model_that_does_not_park_is_not_run_rather_than_passed(localai):
    stand_in, server = localai
    stand_in.parks_flag = False
    passed, lines = tool.cycles(server, "copy", 3, "request")
    assert not passed and lines[-1].startswith("NOT RUN: /system does not list it as a model that parks")
    assert "POST /backend/park" not in stand_in.calls
    passed, lines = tool.cycles(server, "nobody", 3, "request")
    assert not passed and "not listed" in lines[0]


def test_a_refused_return_is_a_deferral_that_leaves_the_model_parked(localai):
    stand_in, server = localai
    stand_in.refuse_returns = 2
    passed, lines = tool.refused(server, "copy")
    assert passed, "\n".join(lines)
    assert lines[1].startswith("request 1: HTTP 503 'no_room_on_device' Retry-After '2'") and "state 'parked'" in lines[1]
    assert lines[3].startswith("request 3: served, vectors identical") and "state 'loaded'" in lines[3]
    assert lines[-1] == "ok: 2 return(s) refused as a deferral, then served"
    # the engine counted them: two refusals for room, one return, in the process that parked
    assert lines[0].endswith("the engine's counts: 1 parked, 0 back, 0 refused for room, away")
    assert lines[-2] == "the engine's counts: 1 parked, 1 back, 2 refused for room, on the device"


def test_a_refusal_answered_as_a_fault_fails_and_none_refused_is_not_run(localai):
    stand_in, server = localai
    stand_in.refuse_returns, stand_in.refusal_status = 1, 500
    passed, lines = tool.refused(server, "copy")
    assert not passed and "<- NOT a deferral that leaves it parked" in lines[1] and lines[-1].startswith("FAIL")
    stand_in.refuse_returns, stand_in.refusal_status = 0, 503
    passed, lines = tool.refused(server, "copy")
    assert not passed and lines[-1].startswith("NOT RUN: no return was refused")


def test_a_park_asked_while_a_request_runs_is_409_and_the_model_is_still_there(localai):
    stand_in, server = localai
    stand_in.slow = 0.6
    passed, lines = tool.busy(server, "copy", sentences=64, lead=0.15)
    assert passed, "\n".join(lines)
    assert "(409, 'model_busy')" in lines[0] and lines[1] == "the request itself: HTTP 200, vectors identical"
    assert lines[-1].startswith("ok: a busy model is refused 409 model_busy")


def test_a_busy_model_that_is_parked_anyway_fails_and_a_request_too_short_is_not_run(localai):
    stand_in, server = localai
    stand_in.slow, stand_in.park_while_busy = 0.6, True
    passed, lines = tool.busy(server, "copy", sentences=64, lead=0.15)
    assert not passed and lines[-1].startswith("FAIL: a park asked while a request ran was not refused as busy")
    stand_in.slow, stand_in.park_while_busy, stand_in.state = 0.0, False, "loaded"
    passed, lines = tool.busy(server, "copy", sentences=32, lead=0.15)
    assert not passed and lines[-1].startswith("NOT RUN: the request ended within 0.15 s"), lines[-1]


def test_a_park_that_reaches_the_server_before_the_request_does_is_not_counted_against_it(localai):
    """Found writing these tests: with no lead the first park could arrive while the model was still idle,
    be rightly accepted, and the run called a failure."""
    stand_in, server = localai
    stand_in.slow = 0.6
    for _ in range(3):
        stand_in.state = "loaded"
        passed, lines = tool.busy(server, "copy", sentences=64, lead=0.15)
        assert passed, "\n".join(lines)
        assert "(200, 'parked')" not in lines[0]


def test_the_floor_is_written_with_the_whole_settings_object(localai):
    stand_in, server = localai
    before, after = server.set_floor(0)
    assert (before, after) == (12 << 30, 0)
    assert stand_in.settings == {"memory_reclaimer_enabled": False, "park_host_floor_bytes": 0, "threads": 8}, \
        "every other key written back as it was read"
    assert server.set_floor(8 << 30) == (0, 8 << 30)


def test_a_key_is_read_from_its_file_sent_as_a_bearer_and_never_printed(localai, tmp_path):
    stand_in, server = localai
    secret = tmp_path / "key"
    secret.write_text("sentinel-key-4f1c\n", encoding="utf-8")
    keyed = tool.Server(server.base, str(secret))
    passed, lines = tool.cycles(keyed, "copy", 1, "request")
    assert passed
    assert any(h.get("Authorization") == "Bearer sentinel-key-4f1c" for h in stand_in.headers)
    assert "sentinel-key-4f1c" not in "\n".join(lines) and "sentinel-key-4f1c" not in repr(keyed.base)


def test_the_shapes_are_the_real_server_s_as_they_were_read_off_it():
    """docs/notes/parking-step-2.md §8: the answers captured from LocalAI's own server with staged 0027, a real
    backend behind it. The stand-in above is only as good as these."""
    assert tool.MODELS_KEY == "loaded_models"
    note = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")
    section = note[note.index("## 8. "):]
    for shape in ('{"backends":["llama-cpp-park"],"loaded_models":[]}',
                  '{"message":"model parked","parked":{"size_host":66547200,"size_vram_needed":66547200,"since":"',
                  '"type":"parking_off"', '"type":"model_not_loaded"', '"type":"cannot_park"',
                  '"type":"host_memory_floor"', '"type":"model_busy"', '`park_host_floor_bytes`'):
        assert shape in section, shape
    # the two answers that move with the park engine, seen on the real server before the node's pass
    assert "BOTH SEEN HERE FIRST" in section and '"code":"no_room_on_device","message":"Compute error:' in section
    assert "ended after 4 with NO `finish_reason`" in section


def test_the_counts_are_read_as_the_real_server_gave_them():
    """docs/notes/parking-step-2.md §10: GET /backend/monitor on LocalAI's real server, a real backend behind it."""
    assert tool.COUNT_KEYS == ("parks", "unparks", "no_room", "parked")
    note = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")
    section = note[note.index("## 10. "):]
    for shape in ('{"state":2,"memory":{"total":1274732544,"breakdown":{"no_room":0,"park_size":66547200,"parked":1,'
                  '"parked_host":66547200,"parks":1,"rss":177532928,"unparks":0}}}',
                  "`GET /backend/monitor?model=<name>`", "| a FAILED return |"):
        assert shape in section, shape
    # the engine's words for them, in the live engine's patch
    patch = (ROOT / "deploy" / "localai" / "patches" / "staged" / "0023-llama-cpp-park-when-told.patch").read_text(encoding="utf-8")
    for line in ('(*memory->mutable_breakdown())["parks"]       = m.n_weights_park;',
                 '(*memory->mutable_breakdown())["unparks"]     = m.n_weights_unpark;',
                 '(*memory->mutable_breakdown())["no_room"]     = m.n_no_room;',
                 "The counts are of weights that LEFT and that came BACK, never of attempts"):
        assert line in patch, line


def test_what_it_reads_of_an_answer():
    assert tool.error_of({"error": {"type": "model_busy", "message": "m"}}) == ("model_busy", "m")
    assert tool.error_of("Resource not found")[0] == ""
    assert tool.describe(None) == "not listed"
    assert tool.describe({"id": "x", "size_vram": 5 * MIB}) == "listed, does not park (size_vram 5 MiB)"
    assert tool.describe({"parks": True, "state": "parked", "size_vram": MIB,
                          "parked": {"size_host": 2 * MIB, "since": "t"}}) == \
        "parks, state 'parked', size_vram 1 MiB, 2 MiB on the host since t"
    assert tool.digest([[0.5]]) == tool.digest([[0.5]]) != tool.digest([[0.25]])
