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
    # busy is FailedPrecondition on both sides, and never a reason to stop
    assert "case codes.FailedPrecondition:" in park
    assert "return ParkCode::FailedPrecondition;" in engine
    # the return is a LoadModel with the options of the load, which the engine compares
    assert "opts := m.LoadOptions()" in park and "LoadModel(loadCtx, opts)" in park
    assert "fields_that_differ(loaded_options, *request)" in engine


def test_a_busy_model_is_never_stopped_for_being_busy():
    park = _added(_sections()["pkg/model/park.go"])
    assert "if client.IsBusy() {\n\t\treturn ParkedState{}, ErrModelBusy" in park, "it never waits either"
    watchdog = _added(_sections()["pkg/model/watchdog.go"])
    busy = watchdog[watchdog.index("parked, err := pm.ParkModelAtAddress(t.model, t.address)"):]
    assert re.search(r"if errors\.Is\(err, ErrModelBusy\) \{\n\t\t\treturn err\n", busy), \
        "the eviction fails; the caller keeps the model and takes another"
    assert "}); ok && !t.wasBusy {" in watchdog, "an eviction forced while busy is a stop, not a park"


def test_the_stop_sends_no_free_to_a_model_that_parks():
    process = _sections()["pkg/model/process.go"]
    assert re.search(r"^-\tif !force \{\n(?:\+.*\n)+\+\tif !force && !model\.Parks\(\) \{", process, re.M)


def test_nothing_is_parked_by_default_and_the_host_is_read_at_each_park():
    park = _added(_sections()["pkg/model/park.go"])
    assert "if floor == 0 {\n\t\treturn ParkedState{}, ErrParkingOff" in park
    assert "if free < size || free-size < floor {" in park
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
    assert "const noRoomRetryAfterSeconds = 2" in app
    assert 'c.Response().Header().Set("Retry-After", strconv.Itoa(noRoomRetryAfterSeconds))' in app
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
