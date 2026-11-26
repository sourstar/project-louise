"""Parking, step 2, the SERVER part — STAGED after three cuts
(docs/notes/parking-step-2.md §7, 2; deploy/localai/patches/staged/).

LocalAI parks a model that can park where it would stop it for room, brings
it back at the next request, and answers a refusal of the moment 503. Its Go
specs ran where it was written (a scripted backend; no real one exists before
the park engine is built). Held here: that the server and the engine say the
SAME words — the message of a park, the key of its bytes, the head of a
refusal — which no Go spec can check across the two; the rules a reviewer
agreed to (a busy model is never stopped, the stop sends no Free to a model
that parks, the host is read at each park, nothing is parked by default); what
it deliberately does not add; what the two readings changed; and that it is a
patch of the SERVER, in no engine series."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
PATCH = PATCHES / "staged" / "0027-model-park.patch"
ENGINE = PATCHES / "staged" / "0023-llama-cpp-park-when-told.patch"
NOTE = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")


def _text() -> str:
    return PATCH.read_text(encoding="utf-8")


def _sections() -> dict[str, str]:
    text = _text()
    parts = re.split(r"^diff --git a/(\S+) b/\S+\n", text[text.index("\n---\n"):], flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def _added(diff: str) -> str:
    return "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))


def _engine_added() -> str:
    return _added(ENGINE.read_text(encoding="utf-8"))


def test_it_is_a_mail_under_localai_s_policy_for_the_server_alone():
    text = _text()
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    files = set(_sections())
    assert not any(f.startswith("backend/") for f in files), "the server's Go: no engine is rebuilt for it"
    assert {"pkg/model/park.go", "pkg/model/park_internal_test.go", "pkg/model/watchdog_park_internal_test.go",
            "pkg/model/park_watchdog_internal_test.go", "pkg/grpc/free_result_test.go",
            "pkg/xsysinfo/memory_headroom.go", "pkg/xsysinfo/memory_headroom_test.go",
            "core/backend/park_internal_test.go", "core/application/park_floor_internal_test.go",
            "core/http/endpoints/openai/chat_stream_no_room_test.go",
            "core/http/endpoints/localai/backend_park_test.go", "core/http/no_room_handler_test.go"} <= files
    assert "backend/backend.proto" not in files, "LocalAI's own two calls: no new RPC"


def test_it_is_staged_for_the_server_and_in_no_engine_series():
    assert not (PATCHES / "review" / PATCH.name).exists(), "it moved; it is not in two places"
    for series in (PATCHES / "series", *(PATCHES / "staged").glob("*.series")):
        assert "model-park" not in series.read_text(encoding="utf-8"), series.name
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    row = next(line for line in readme.splitlines() if line.startswith(f"| `{PATCH.name}` |"))
    assert "SERVER" in row and "NO engine series" in row and "ACCEPTED by the architecture session at its third cut" in row
    # the condition on the build that carries it, and what turns it on
    assert "whole and green on the node's server tree" in row and "Postgres" in row
    assert "It does nothing until the keeper writes the floor AND a model's file says `park: true`" in row
    moved = (PATCHES / "review" / "README.md").read_text(encoding="utf-8")
    assert "Nothing waits here now." in moved and "to `staged/0027-model-park.patch`, 2026-11-25" in moved


def test_the_server_and_the_engine_say_the_same_words():
    """What no Go spec can hold: the two sides are built apart, and meet only on the node."""
    park = _added(_sections()["pkg/model/park.go"])
    engine = _engine_added()
    # a park's answer
    assert 'parkedMessage = "parked"' in park
    assert 'inline constexpr const char * FREE_PARKED = "parked";' in engine
    # the bytes a park would move: the key of Status's breakdown
    assert 'parkSizeKey = "park_size"' in park
    assert '(*memory->mutable_breakdown())["park_size"]   = m.park_size;' in engine
    # a refusal of the moment: the code AND the head of its message
    assert 'NoRoomOnDeviceType = "no_room_on_device"' in park
    assert 'inline constexpr const char * ERROR_TYPE_NO_ROOM = "no_room_on_device";' in engine
    assert 'return strings.CutPrefix(st.GRPCStatus().Message(), NoRoomOnDeviceType+": ")' in park
    assert 'return std::string(ERROR_TYPE_NO_ROOM) + ": " + message;' in engine
    assert "st.GRPCStatus().Code() != codes.ResourceExhausted" in park
    assert "return grpc::StatusCode::RESOURCE_EXHAUSTED;" in engine
    # busy is FailedPrecondition on both sides, and never a reason to stop - and it is the code AND
    # the head of the message: the engine also says FailedPrecondition of a model it does not hold
    assert "case codes.FailedPrecondition:" in park
    assert "return ParkCode::FailedPrecondition;" in engine
    assert 'busyHead = "busy: "' in park and "if strings.HasPrefix(answer.Message(), busyHead) {" in park
    # (the engine's patch carries llama.cpp's own patch inside: a line that begins "-" there is one it REMOVES)
    kept = "\n".join(line for line in engine.splitlines() if not line.startswith("-"))
    said = re.findall(r'"(busy: [^"]+)"', kept)
    assert len(said) >= 3, "the model is loading, a request is running, requests are waiting"
    for refusal in re.findall(r'FAILED_PRECONDITION, "([^"]+)"\)', kept) + \
            re.findall(r'send_error\(task, "([^"]+)", ERROR_TYPE_UNAVAILABLE\)', kept):
        assert refusal.startswith("busy: "), refusal
    # the return is a LoadModel with the options of the load, which the engine compares
    assert "opts := m.LoadOptions()" in park and "LoadModel(loadCtx, opts)" in park
    assert "fields_that_differ(loaded_options, *request)" in engine


def test_a_busy_model_is_never_stopped_for_being_busy():
    park = _added(_sections()["pkg/model/park.go"])
    assert "if client.IsBusy() {\n\t\treturn ParkedState{}, ErrModelBusy" in park, "it never waits either"
    watchdog = _added(_sections()["pkg/model/watchdog.go"])
    busy = watchdog[watchdog.index("parked, err := pm.ParkModelAtAddress(t.model, t.address)"):]
    assert re.search(r"if errors\.Is\(err, ErrModelBusy\) \{\n\t\t\treturn false, err\n", busy), \
        "the eviction fails; the caller keeps the model and selects again"
    assert "}); ok && t.forRoom && !t.wasBusy {" in watchdog, "an eviction forced while busy is a stop, not a park"


def test_the_review_s_two_changes():
    """The architecture session, 2026-11-25: accepted for staging once these two are in."""
    park = _added(_sections()["pkg/model/park.go"])
    # 1. a return that FAILS does not leave the model parked for ever: its process is stopped,
    #    and the request goes on to an ordinary load; a deferral still leaves it parked
    failed = park[park.index("failed := func(cause error) error {"):]
    assert "ml.deleteProcess(stopCtx, m.ID, true)" in failed
    assert 'return fmt.Errorf("%w: model %q: %w", ErrParkedReturnFailed, m.ID, cause)' in failed
    after = park[park.index("res, err := m.GRPC(false, wd).LoadModel(loadCtx, opts)"):]
    assert after.index("return &NoRoomOnDeviceError{Model: m.ID, Message: message}") < after.index("return failed(err)")
    assert "return failed(errors.New(res.GetMessage()))" in after
    load = _added(_sections()["pkg/model/initializers.go"])
    assert "errors.Is(err, ErrParkedReturnFailed) || errors.Is(err, errParkedModelGone)" in load
    assert "m = ml.CheckIsLoaded(o.modelID)" in load, "what the store holds is looked at again, not assumed"
    # a deadline is the return's own: the caller's cancellation does not reach a call that was sent
    assert "context.WithTimeout(context.WithoutCancel(ctx), unparkTimeout)" in park
    # 2. /backend/park does not wait for the lifecycle lock: not free at once is busy
    locks = _added(_sections()["pkg/model/operation_locks.go"])
    assert "func (l *modelOperationLocks) tryAcquire(modelID string, exclusive bool) (func(), bool) {" in locks
    assert "if !entry.sem.TryAcquire(weight) {" in locks
    told = park[park.index("func (ml *ModelLoader) ParkModel(ctx context.Context, modelName string)"):]
    told = told[:told.index("return ml.park(ctx, modelName, \"\")")]
    assert "ml.operations.tryAcquire(modelName, true)" in told and "acquireContext" not in told
    assert "ErrModelBusy" in told
    endpoint = _added(_sections()["core/http/endpoints/localai/backend_monitor.go"])
    assert "return refuse(http.StatusConflict, parkErrorBusy, err)" in endpoint


def test_what_the_independent_reading_found_is_fixed():
    """docs/notes/parking-step-2.md §7, "finding by finding"; the Go specs ran where it was written."""
    s = _sections()
    park = _added(s["pkg/model/park.go"])
    # 1, 4: the limits under the lock, and the model registered BEFORE its weights move
    back = park[park.index("func (ml *ModelLoader) unpark(ctx context.Context, m *Model) error {"):]
    order = [back.index(x) for x in ("ml.operations.acquireContext(ctx, m.ID, true)", "ml.enforceGroupExclusivity(m.ID)",
                                     "ml.enforceLRULimit()", "wd.AddAddressModelMap(m.address, m.ID)",
                                     "LoadModel(loadCtx, opts)")]
    assert order == sorted(order)
    assert "wd.Untrack(m.address) // still parked: not one of the loaded models" in back
    load = _added(s["pkg/model/initializers.go"])
    assert "enforceLRULimit" not in load, "not before the lock any more"
    # 2: a Free that was sent is followed to its end; one that does not answer stops the process
    assert "context.WithTimeout(context.WithoutCancel(ctx), parkTimeout)" in park
    assert "case codes.DeadlineExceeded:" in park and "ErrParkStoppedModel" in park
    # 6: only an eviction that wants the device back parks
    watchdog = _added(s["pkg/model/watchdog.go"])
    assert "forRoom: true, detached: wd.detachForEvictionLocked(m.address)" in watchdog, "the limit and the groups"
    assert "wd.evictLRU(aggregate.GPUCount > 0)" in watchdog, "the reclaimer, for a device only"
    assert "\twd.evictLRU(false)\n" in watchdog, "LocalAI's own entry point stops, as it did"
    assert "wasBusy: wasBusy, forRoom: forDevice}" in watchdog
    # 7: the host read knows a container's limit, and two parks do not count the same bytes
    assert "return xsysinfo.AllocatableMemory(), nil" in park
    headroom = _added(s["pkg/xsysinfo/memory_headroom.go"])
    for said in ('cgroupV2CurrentPath = "/sys/fs/cgroup/memory.current"', '"inactive_file"', '"total_inactive_file"',
                 "return limit - usage, true"):
        assert said in headroom, said
    assert "ml.parkReserved += size" in park and "ml.parkReserved -= size" in park
    # 8: Free is skipped only where there is a process to stop
    process = s["pkg/model/process.go"]
    assert re.search(r"^-\tif !force \{\n(?:\+.*\n)+\+\tif !force && !\(model\.Parks\(\) && model\.Process\(\) != nil\) \{",
                     process, re.M)
    # 9: a model that does not park is answered before the lock is looked at
    at = park[park.index("func (ml *ModelLoader) ParkModelAtAddress(modelName, address string) (bool, error) {"):]
    assert at.index("return false, ErrModelCannotPark") < at.index("ml.operations.tryAcquire(modelName, true)")
    assert at.index("return false, ErrParkingOff") < at.index("ml.operations.tryAcquire(modelName, true)")
    # 12: a probe does not un-park
    assert "if m.ParkedState().Parked {" in _added(s["core/application/failover.go"])
    # 13: a load refused for room arms no cooldown
    assert "if _, noRoom := NoRoomOnDevice(err); !noRoom {\n\t\t\tml.recordLoadFailure(modelID, err)" in _added(s["pkg/model/loader.go"])
    # 14: the route's own 404, and the one model it stops
    endpoint = _added(s["core/http/endpoints/localai/backend_monitor.go"])
    assert 'parkErrorNotLoaded  = "model_not_loaded"' in endpoint and 'parkErrorStopped    = "model_stopped"' in endpoint
    assert "return refuse(http.StatusNotFound, parkErrorNotLoaded," in endpoint
    assert "return refuse(http.StatusInternalServerError, parkErrorStopped, err)" in endpoint
    # 15: a parked target is not untracked a second time
    assert "parked, err := wd.parkOrShutdownTarget(target)\n\tif parked {" in watchdog
    # 5: the route that brings a model back says a refusal as every route does
    load_route = _added(s["core/http/endpoints/localai/backend_load.go"])
    assert "if message, noRoom := model.NoRoomOnDevice(err); noRoom {" in load_route
    assert 'c.Response().Header().Set("Retry-After", strconv.Itoa(model.NoRoomRetryAfterSeconds))' in load_route
    assert "Type: model.NoRoomOnDeviceType" in load_route
    # 16: the specs that were missing exist
    real = _added(s["pkg/model/park_watchdog_internal_test.go"])
    assert real.count("\tIt(") == 8 and "NewWatchDog(append([]WatchDogOption{WithProcessManager(ml)}, opts...)...)" in real
    assert "gogrpc.NewServer()" in _added(s["pkg/grpc/free_result_test.go"])
    assert 'options("name: m\\npark: true\\n").Parks()' in _added(s["core/backend/park_internal_test.go"])
    assert "newApplication(appConfig)" in _added(s["core/application/park_floor_internal_test.go"])


def test_the_stop_sends_no_free_to_a_model_that_parks_in_a_process_it_stops():
    process = _sections()["pkg/model/process.go"]
    assert "!(model.Parks() && model.Process() != nil)" in _added(process)


def test_nothing_is_parked_by_default_and_the_host_is_read_at_each_park():
    park = _added(_sections()["pkg/model/park.go"])
    assert "if floor == 0 {\n\t\treturn ParkedState{}, ErrParkingOff" in park
    assert "fits := free >= reserved && free-reserved >= size && free-reserved-size >= floor" in park
    order = [park.index(s) for s in ("client.Status(statusCtx)", "free, err := available()", "parker.FreeResult(freeCtx)")]
    assert order == sorted(order), "what would move, then the host NOW, then the park"
    registry = _added(_sections()["core/config/runtime_settings_registry.go"])
    assert 'field("park_host_floor_bytes",' in registry and "restartRequired()" not in registry, "followed at the next park"
    config = _added(_sections()["core/config/model_config.go"])
    assert 'Park *bool `yaml:"park,omitempty" json:"park,omitempty"`' in config


def test_what_it_deliberately_does_not_add():
    text = "\n".join(_added(s) for s in _sections().values())
    assert "/backend/prepare" not in text, "/backend/load, which exists, is the call that brings a model back"
    routes = _added(_sections()["core/http/routes/localai.go"])
    assert 'router.POST("/backend/park", localai.BackendParkEndpoint(backendMonitorService), adminMiddleware)' in routes
    assert 'router.POST("/v1/backend/park", localai.BackendParkEndpoint(backendMonitorService), adminMiddleware)' in routes
    assert '"backend_park":         "/backend/park",' in routes, "listed with the other backend routes"
    schema = _added(_sections()["core/schema/localai.go"])
    # a model that does not park is listed as it always was
    assert 'Parks bool `json:"parks,omitempty"`' in schema and 'State string `json:"state,omitempty"`' in schema
    assert 'Parked *SysInfoParked `json:"parked,omitempty"`' in schema
    for key in ('`json:"size_host"`', '`json:"size_vram_needed"`', '`json:"since"`'):
        assert key in schema, key


def test_a_refusal_is_503_with_a_retry_after_and_its_own_type():
    app = _added(_sections()["core/http/app.go"])
    assert "NoRoomRetryAfterSeconds = 2" in _added(_sections()["pkg/model/park.go"])
    assert 'c.Response().Header().Set("Retry-After", strconv.Itoa(model.NoRoomRetryAfterSeconds))' in app
    assert "return http.StatusServiceUnavailable, message" in app
    assert "errType = model.NoRoomOnDeviceType" in app
    assert app.count("applyNoRoomOnDevice(err, code, c)") == 2, "with a body, and without one (opaque errors)"
    embeddings = _added(_sections()["core/backend/embeddings.go"])
    assert "if _, noRoom := model.NoRoomOnDevice(err); noRoom {" in embeddings, "not rewritten as \"conversation too long\""
    endpoint = _added(_sections()["core/http/endpoints/localai/backend_monitor.go"])
    for code, kind in (("http.StatusConflict", "parkErrorBusy"), ("http.StatusPreconditionFailed", "parkErrorOff"),
                       ("http.StatusPreconditionFailed", "parkErrorHostFloor"), ("http.StatusPreconditionFailed", "parkErrorCannotPark")):
        assert f"return refuse({code}, {kind}, err)" in endpoint


def test_the_note_puts_every_difference_to_the_review_with_its_cost():
    section = NOTE[NOTE.index("**2. The parked state, `/backend/park`, `/system`, the floor, 503**"):]
    for said in ("STAGED (2026-11-25)", "Where it differs from §3.2 and §3.4", "`POST /backend/load`, which exists, IS it",
                 "LocalAI never stops a PARKED model by itself", "park_host_floor_bytes", "NOT measured",
                 "no Docker", "It does NOTHING until two things are true"):
        assert said in section, said


def test_the_note_carries_both_readings_and_what_the_review_asked_to_have_written():
    section = NOTE[NOTE.index("**2. The parked state, `/backend/park`, `/system`, the floor, 503**"):]
    assert "**THIRD CUT (2026-11-25).**" in section
    assert "ACCEPTED it:" in section and "I skip the\nshutdown on it" in section, "the architecture session's words on the third cut"
    assert "conditions, not changes" in section
    # the architecture session's two changes, each with its pros and cons, and the deadline it left to this part
    changes = section[section.index("**The review's two changes (the architecture session, 2026-11-25), built:**"):]
    assert "| | it was | it is | pros | cons |" in changes and "**A deadline on the return**" in changes
    assert "| a return's deadline | pros | cons |" in changes
    # the record that can be stale one way, and the reading the keeper reconciles with
    stale = section[section.index("**The record can be stale ONE way, and the server does not close it.**"):]
    assert "`size_vram` in `GET /system` is the LIVE reading" in stale and "`xsysinfo.ProcessVRAM`" in stale
    # the independent reading: every finding answered, the kept one said
    table = section[section.index("**The independent reading, finding by finding**"):section.index("**Stated limits")]
    rows = re.findall(r"^\| (\d+) \|", table, re.M)
    assert rows == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "12", "13", "14", "15", "16"]
    assert "Kept, and said: **11**" in table and "force_eviction_when_busy" in table
    # the limits it leaves, and what could not run here
    limits = section[section.index("**Stated limits — known, not built:**"):]
    for said in ("`/v1/messages` and `/v1/responses`", "outside the idle timeout", "`spawnGRPCModel`",
                 "346 of 350", "those two suites whole and green on the\nnode's server tree"):
        assert said in limits, said


def test_the_condition_on_the_server_build_is_recorded_with_its_counts_and_its_one_failure_explained():
    """The architecture session: LocalAI's two Postgres suites whole and green on the node's server tree before
    the build is used. They ran there; one spec failed once; it was counted on both trees before being called
    LocalAI's own flake."""
    section = NOTE[NOTE.index("**STEP 1 ON THE NODE: THE SERVER IS BUILT, NOT SWAPPED"):]
    for said in ("`v5.0.1-6-gffb10fdf3`", "Ran 278 of 278", "| the live tree's builder (the control), two runs | ok, ok | ok, ok |",
                 "| the new tree's builder, three runs | 741 of 742, then ok, ok | ok, ok, ok |",
                 "24 failures\nin 300 on the new tree and 20 in 300 on the live one", "The same rate.",
                 "condition on\nthe server build is met"):
        assert said in section, said
    bugs = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
    row = next(line for line in bugs.splitlines() if line.startswith("| B25 | defect |"))
    assert "only THEN subscribes" in row and "ran the two one after the other while the load ran out: not a comparison" in row


def test_the_card_s_rows_are_recorded_with_what_they_did_not_measure():
    """München's pass, ended 2026-11-26 00:19:39Z: parking through the server on a copy of bge-m3 (§9). The rows are
    the tool's own lines; what no row measured is said beside them, and nothing of it is claimed elsewhere."""
    section = NOTE[NOTE.index("## 9. On the card (München, the pass of 2026-11-25 22:46Z to 2026-11-26 00:19:39Z)"):]
    for said in ("`GGML_SYCL_PARKABLE=1` and\n`GGML_SYCL_PARK_VERIFY=1`", "park_host_floor_bytes: 0 -> 12884901888",
                 "20 of 20 cycles: the same bytes; a park 142.4 ms (median, 141.7-147.1)",
                 "20 of 20 cycles: the same bytes; a park 142.4 ms (median, 141.2-143.8)",
                 "the process holds 319 MiB of the device, 996 MiB given back",
                 "{(409, 'model_busy'): 1084}", "HTTP 503 'no_room_on_device' Retry-After '2'",
                 "/backend/shutdown of the parked model: HTTP 200 in 202 ms", "HTTP 200 in 4.17 s",
                 "**A park gives the card back 996 of 1,315 MiB**", "ran together on a\n  card for the first time"):
        assert said in section, said
    # 996 is the loaded size less what the parked process keeps, and the floor is 12 GiB
    assert 1315 - 319 == 996 and 12884901888 == 12 << 30 == 12288 << 20
    still = section[section.index("**NOT measured, still:**"):]
    for row in ("| the card's own counter WHILE parked |", "| a card refusing a return for its own reasons |",
                "| a failed return (`failed`) | no switch on a card |",
                "| the gate's clause with the FINGERPRINT texts (§3.3) |",
                "| a quantized model, buffers above 1 GiB, the 35B, OmniVoice |",
                "| parking BEHIND THE VEGA WRAPPER (LocalAI, the wrapper, the engine) | both copies of this pass were on the engine directly. SHOWN SINCE:",
                "| the engine's own COUNT of the forty cycles | the tool did not read it yet (§10); the forty stand on their times"):
        assert row in still, row
    assert "No real model parks" in still and "both copies are removed" in still
    assert "München confirms the fingerprint texts were NOT run on the copy" in still
    # one package rebuilt, three left as they were: the table as the node reads after the pass
    table = [line for line in still.splitlines() if line.startswith("| `vega-louise-")]
    assert len(table) == 4
    assert [("47bf02eaae7c01e4" in line, "10833aab79818132` | 15e84e9" in line) for line in table] == \
        [(True, False), (False, True), (False, True), (False, True)]
    assert "1ac1ecc | `louise-sycl-f16-llama-cpp-ed93e3c1b2e2-stack-7c301a7ba437`" in table[0]
    assert "NeuRIS\n(nemotron) 509 s, worst 1.000000; wikidata (nemotron) 508 s" in still
    # the two answers that move with this engine and server, as the CPU server showed them
    for said in ('"type":\n"no_room_on_device"}}`; the ring: 2 graphs refused', '"context canary failed" 0',
                 "then 7 chunks, NO `finish_reason`"):
        assert said in section, said
    bugs = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
    row = next(line for line in bugs.splitlines() if line.startswith("| **P26 (staged)** |"))
    assert "**ON THE CARD (München, 2026-11-26, a copy of bge-m3): IT HOLDS**" in row
    assert "NOT measured: a card that refuses for its own reasons" in row and "any model but bge-m3 F16" in row
    row = next(line for line in bugs.splitlines() if line.startswith("| **P21 (staged)** |"))
    assert "**ON THE CARD: IT HOLDS**" in row and "40 of 40 park cycles the same bytes" in row


def test_a_cycle_is_counted_by_the_engine_and_the_note_says_how_a_gate_asks_for_one():
    """§10, for the architecture session's gate clause: the engine's counts, LocalAI's own route for them, the
    failed return that is answered 200, and the three ways to carry the count with their costs."""
    section = NOTE[NOTE.index("## 10. A park cycle is counted by the engine, never assumed (2026-11-26)"):]
    for said in ("`GET /backend/monitor?model=<name>`", "Of weights, never of attempts; per process.",
                 "| three reads of a PARKED model | the same counts three times; `/system` still says parked",
                 "| a FAILED return | the request answered **HTTP 200** in 0.45 s",
                 "20 parks\nand 20 returns COUNTED, in the one process",
                 "FAIL: cycle 1: answered with the same bytes, but the engine's counts started again",
                 "| A. the gate reads `/backend/monitor` itself |", "| B. the wrapper reads `Status`",
                 "| C. count the HTTP 200s (the tool until today) |", "3,168 s of gates",
                 # the card's own reading of the counts (München), and what `total` is
                 "`parks` 1, `unparks` 0, `parked` 1, `parked_host`\n605,265,920, and the same 17 ms later",
                 "2 and 2, 3 and 3, `no_room` 0 throughout",
                 "**`memory.total` is not the model's share of anything.**",
                 "**NOT shown:** a failed return on a card"):
        assert said in section, said
    assert 98 + 1983 + 70 + 509 + 508 == 3168
    # `total` is the process's virtual size and `rss` its resident set, by the engine's patch
    engine = (PATCHES / "staged" / "0023-llama-cpp-park-when-told.patch").read_text(encoding="utf-8")
    assert 'std::ifstream statm("/proc/self/statm");' in engine and "memory->set_total(virtual_bytes);" in engine
    # every row of the choice has its pros and its cons
    rows = [line for line in section.splitlines() if line.startswith(("| A. ", "| B. ", "| C. "))]
    assert len(rows) == 3 and all(len([c for c in row.split("|")[2:4] if c.strip()]) == 2 for row in rows)
    # the wrapper forwards what it does not intercept: the two calls it does are these and no other
    wrapper = (ROOT / "deploy" / "vega" / "vega_wrapper.py").read_text(encoding="utf-8")
    assert "intercepted = {LOAD: proxy._unary(LOAD, proxy.gate.load_model),\n" in wrapper
    assert "return intercepted.get(details.method) or proxy._stream(details.method)" in wrapper


def test_the_tool_s_sixteen_sentences_are_not_the_gate_s_fingerprints():
    """Why the gate's clause is not claimed from the cycles: the tool embeds its own sentences."""
    tool = (ROOT / "tools" / "measure" / "park_http.py").read_text(encoding="utf-8")
    assert 'TEXTS = [f"sentence number {i}: the quick brown fox' in tool and "for i in range(16)]" in tool
    assert "fingerprint" not in tool.lower()


def test_a_pinned_model_is_parked_when_told_and_never_by_the_watchdog_itself():
    """§12, for the gate, which pins the model it parks. The explicit park does not look at the pin; the
    watchdog's evictions skip a pinned model before any is chosen - upstream's lines, which the patch leaves."""
    section = NOTE[NOTE.index("## 12. A pinned model, an imported twin, a file that says park where the engine cannot (2026-11-26)"):]
    for said in ("| **does `POST /backend/park` park a PINNED model?** | **YES: 200**",
                 "so LocalAI never parks one BY ITSELF", "left a pinned one loaded, untouched (`parks` 0)",
                 "`park: true` kept and `pinned: true` added",
                 "| **does `/api/models/config-json/<model>` return `park`?** | **YES**",
                 "| **does a twin written through `/models/import` from it keep `park: true`?** | **YES**",
                 "that key is the FILE's word, not the engine's", "the park is **412 `cannot_park`**, \"its backend does not park\"",
                 "Pinning does not reserve the card for a PARKED model",
                 "**NOT shown:** a card (München's copies were never pinned)"):
        assert said in section, said
    # the code that makes it so
    patch = _text()
    assert "func (ml *ModelLoader) ParkModel(ctx context.Context, modelName string) (ParkedState, error)" in patch
    park_go = patch[patch.index("+++ b/pkg/model/park.go"):]
    park_go = park_go[:park_go.index("\ndiff --git ", 10)]
    assert "inned" not in park_go, "the explicit park does not ask whether the model is pinned"
    # the config key travels through the import and the config route: a field of the model's configuration
    assert 'Park *bool `yaml:"park,omitempty" json:"park,omitempty"`' in patch
    for words in ("its file does not say `park: true`", "its backend does not park"):
        assert words in patch, words
    # §10's first step says what the route answers where there are no counts, as measured
    assert "the route answers HTTP 500 for it (§12;" in NOTE
    bugs = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
    assert "defects found (B1–B26)" in bugs
    row = next(line for line in bugs.splitlines() if line.startswith("| B26 | defect |"))
    assert "`config.Model + \".bin\"`" in row and "**to file upstream**" in row

