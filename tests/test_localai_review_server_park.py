"""Parking, step 2, the SERVER part — SENT FOR REVIEW, not staged
(docs/notes/parking-step-2.md §7, 2; deploy/localai/patches/review/).

LocalAI parks a model that can park where it would stop it for room, brings
it back at the next request, and answers a refusal of the moment 503. Its Go
specs ran where it was written (a scripted backend; no real one exists before
the park engine is built). Held here: that the server and the engine say the
SAME words — the message of a park, the key of its bytes, the head of a
refusal — which no Go spec can check across the two; the rules a reviewer
agreed to (a busy model is never stopped, the stop sends no Free to a model
that parks, the host is read at each park, nothing is parked by default); what
it deliberately does not add; and that it is under review, in no series."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
PATCH = PATCHES / "review" / "0027-model-park.patch"
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


def test_it_is_under_review_and_in_no_series():
    assert not (PATCHES / "staged" / PATCH.name).exists()
    for series in (PATCHES / "series", *(PATCHES / "staged").glob("*.series")):
        assert "model-park" not in series.read_text(encoding="utf-8"), series.name
    readme = (PATCHES / "review" / "README.md").read_text(encoding="utf-8")
    assert PATCH.name in readme and "NO engine series" in readme


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
    for said in ("FOR REVIEW", "Where it differs from §3.2 and §3.4", "`POST /backend/load`, which exists, IS it",
                 "LocalAI never stops a PARKED model by itself", "park_host_floor_bytes", "NOT measured",
                 "no Docker", "It does NOTHING until two things are true"):
        assert said in section, said


def test_the_note_carries_both_readings_and_what_the_review_asked_to_have_written():
    section = NOTE[NOTE.index("**2. The parked state, `/backend/park`, `/system`, the floor, 503**"):]
    assert "**THIRD CUT (2026-11-25).**" in section
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
