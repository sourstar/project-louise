# mudler/LocalAI — defects found while running it (one issue each)

Line references and full detail: docs/notes/localai-bugs.md. "Seen" = on a
live node (v4.11.0, Intel Arc Pro B70). "Code" = from reading master, not
reproduced.

**B1 (code) — `transformers` backend, OpenVINO: the device test is always true.**
`if "CPU" or "NPU" in device_map:` is always true, so the GPU-only settings
(`GPU_DISABLE_WINOGRAD_CONVOLUTION`) never apply. Fix: `"CPU" in device_map or
"NPU" in device_map`, and give the inner `if` an `else` (`AUTO:-CPU` would
otherwise leave `ovconfig` unbound).

**B2 (code) — `transformers` backend, OpenVINO: embedding models are re-exported at
every load.** `OVModelForFeatureExtraction.from_pretrained(..., export=True)`
writes to a temporary directory, so OpenVINO's model cache never applies and
every load recompiles. Fix: export once into the models directory, or accept an
IR.

**B3 (code) — watchdog: a backend whose eviction fails stays running, untracked.**
Idle, LRU and busy eviction `untrack` before stopping, and a graceful stop can
fail (`ErrModelBusy` after 30 s). The process then runs outside
`max_active_backends`. Fix: untrack after a successful stop, as `evictLRUModel`
does.

**B4 (code) — memory reclaimer: memory held by another process makes it evict
every model.** `UsagePercent` is the whole device's, so when something outside
LocalAI keeps usage over the threshold, one model is evicted every 500 ms until
none is left. Fix: evict only when LocalAI's own share (`ProcessVRAM`) can bring
usage under the threshold.

**B5 (code) — `LOAD_TO_MEMORY` is ignored with `max_active_backends = 1`.**
`startup.go`: `if options.LoadToMemory != nil && !options.SingleBackend`, and
`SetMaxActiveBackends(1)` sets `SingleBackend`. Nothing is preloaded and nothing
is logged. Fix: preload the first model, or log the skip.

**B6 (code, seen by the fork matt-edmondson/LocalAI) — distributed: eviction does
not check that it frees enough.** One LRU model is evicted, and the new one is
placed on that node whether it fits or not; an estimate of 0 skips the VRAM
filter. Fix: evict until the estimate fits; never treat an unknown size as 0.

**B7 (code) — the loader goes over `max_active_backends` when the models to evict
are busy** (`retryEnforce`: 30 retries, then loads anyway). Fix: answer 503 with
`Retry-After`, as the load cooldown already does.

**B8 (seen) — `POST /models/reload` never forgets a model whose YAML was removed.**
`LoadModelConfigsFromPath` only adds. The model stays listed and loadable until
restart. Fix: drop configs whose source file is gone.

**B9 (seen) — one NaN fails the whole `/v1/embeddings` request.** Go's JSON
encoder refuses NaN, so the response is HTTP 500 and every finite vector is lost.
Fix: check each vector, and answer an error naming the input's index (or retry
it).

**B10 (seen) — a wedged backend is "deleted to recreate it" and keeps running.**
`checkIsLoaded` deletes a local process only `if !process.IsAlive()`, yet logs
"Deleting the process" every 5 minutes; seen for 7 hours. Fix: force-delete
after N failed health checks.

**B11 (seen) — memory reclaimer: a model being loaded is evicted first as
"least recently used".** Its `lastUsed` is zero during the load, so under
pressure the model a request waits for is the one stopped: 500s in a loop while
idle models stay. Fix: set `lastUsed[address] = now` in `AddAddressModelMap`, so
a model being loaded counts as just used (or skip, as busy, an address with a
request waiting on its load).

**B12 (seen) — `intel-transformers` (v4.11.0 gallery image): no OpenVINO model
can load.** optimum-intel 1.15.0 imports `openvino.runtime`, which the
openvino 2026.4.1 beside it no longer has: `ModuleNotFoundError` for every
`OVModel*` type. `intel-rerankers` (optimum-intel 2.2.0) is fine. Fix: pin
optimum-intel ≥ 2.x in `requirements-intel.txt`.

**B13 (code) — `transformers` backend: `Embedding` prints every input in full to
stderr** ("Calculated embeddings for: …"), a whole document per line in the log.
Fix: drop it, or log at debug level with a length.

**B14 (code, and seen as its consequence) — llama.cpp backend: `Embedding`
ignores the token ids it is given.** `/v1/embeddings` accepts token ids and
`core/backend/embeddings.go` sends them as `PredictOptions.EmbeddingTokens`
with an empty text; `grpc-server.cpp` reads only the text, so the request is
refused ("Input content cannot be empty"). Fix: when `EmbeddingTokens` is
set, embed them, with the special tokens a text gets (so embedding a text's
tokens is embedding the text) — patch ready,
`deploy/localai/patches/0002-llama-cpp-embed-token-ids.patch`.

---

# After B14: found on v5.0.1, written out to be filed as they are (2026-11-26)

One issue each. What lies between `<!-- issue:Bnn -->` and `<!-- /issue -->`
is the issue: its first line is the title, the rest the body, and nothing in
it names this project, its node or its people. The line above each is for
whoever files it and stays here. Each claim about LocalAI's code was read in
v5.0.1's own source (`dd5780a`); "measured" and "seen" say where a number
comes from. The tracker was searched on 2026-11-26 (README.md has the near
hits).

Not here, and why: B15 is fixed upstream (v5.0.0, #13186); B16 is known to
llama.cpp (#5763, #26565); B18 is llama.cpp's and is in
[ggml-llama-cpp-sycl.md](ggml-llama-cpp-sycl.md); B23 was a defect of our
own patch, not of LocalAI.

## B21 — a stream that fails after its first chunk ends as a complete answer

*For the filer: the most serious of these — a wrong answer delivered as
right, on every chat model. Our fix is
`deploy/localai/patches/staged/0025-llama-cpp-stream-fails-after-its-first-chunk.patch`
(9 lines, written as a mail for LocalAI: attach it or open it as the PR).
Live in our engine since 2026-11-25; localai-bugs.md B21.*

<!-- issue:B21 -->
llama-cpp backend: PredictStream tests only its first result for an error; a failure after the first chunk ends the stream with status OK

**LocalAI version:** v5.0.1 (`backend/cpp/llama-cpp/grpc-server.cpp`).

**Describe the bug.** `PredictStream` checks `first_result->is_error()` and
returns the error as the gRPC status (since #12425). The results that follow
are read in the `while (rd.has_next())` loop and are never tested: an error
result is passed to `build_reply_from_json` like any other, its JSON has no
content, so it is written as one more, empty, reply. The loop then ends and
the call returns `grpc::Status::OK`.

So a generation that fails in the middle — a computation that fails on the
device, any error llama.cpp's server reports after the first chunk — reaches
the client as a normal end of stream. The answer is cut short and nothing
says so: no error chunk, no error status. #12204 says "a failure after the
first chunk keeps the in-stream error chunk", but the backend never reports
that failure, so there is no chunk to keep.

**To reproduce.** It needs an error after the first chunk. We used a build
in which one graph computation is made to fail after the first chunk of a
stream. With a prompt that otherwise gives 50 chunks: before, the call ends
OK after 4 chunks; with the fix below it ends `INTERNAL "Compute error."`
after 3, and the next stream from the same process is whole. Through
`/v1/chat/completions` with `stream: true` the fixed build ends with the
error object and `[DONE]`, without a `finish_reason`.

**Expected behavior.** A later error ends the stream with the error as its
status, as an error at the first result already does.

**Suggested fix.** In the loop, after `result == nullptr` is handled:

```cpp
if (result->is_error()) {
    json error_json = result->to_json();
    return grpc::Status(grpc::StatusCode::INTERNAL, error_json.value("message", "Error occurred"));
}
```

A patch with this change is available.
<!-- /issue -->

## B24 — `POST /api/settings` never answers under a limit of loaded models with no periodic check

*For the filer: our fix is
`deploy/localai/patches/staged/0029-watchdog-settings-update-hang.patch`,
live in our server since 2026-11-25; localai-bugs.md B24.*

<!-- issue:B24 -->
POST /api/settings hangs for ever when the watchdog has max_active_backends set and no busy check, idle check or memory reclaimer

**LocalAI version:** v5.0.1. The cause below is in v5.0.1's source; the hang
was measured on a build of v5.0.1 carrying local patches that do not touch
these functions.

**Describe the bug.** With a limit of loaded models (`max_active_backends`
> 0, or `single_backend`) and none of the watchdog's periodic checks on, a
`POST /api/settings` applies the settings and never answers. Every later
write hangs behind it.

**To reproduce.**

```
LOCALAI_MAX_ACTIVE_BACKENDS=1 local-ai run          # watchdog idle and busy checks off, memory reclaimer off
curl -s localhost:8080/api/settings > settings.json
curl -s -X POST -H 'Content-Type: application/json' --data-binary @settings.json localhost:8080/api/settings
```

The POST does not return (we waited 300 s). A `GET /api/settings` from
another client shows the values were applied.

**Cause.** `RestartWatchdog` (`core/application/watchdog.go`) takes
`watchdogMutex`, calls `oldWD.Shutdown()` and then `oldWD.WaitDone()`.
`WaitDone` (`pkg/model/watchdog.go`) reads from `wd.done`, which only `Run()`
writes to when it stops. But `Run()` is started only when a periodic check is
on — `if appConfig.WatchDogBusy || appConfig.WatchDogIdle ||
appConfig.MemoryReclaimerEnabled { go wd.Run() }`, with the comment "LRU
eviction doesn't need the Run() loop". A watchdog that exists for the limit
alone never ran its loop, so nothing ever signals `done`, and the mutex is
never released.

**Expected behavior.** The write answers.

**Suggested fix.** Remember that `Run()` was entered (an `atomic.Bool` set at
its start) and return from `WaitDone` at once when it was not. With that
change the same POST answers in 0.09 s. A patch is available.
<!-- /issue -->

## B26 — `GET /backend/monitor` answers 500 for every backend without `Status`

*For the filer: no patch of ours (nothing we run reads the sample). The
symptom is quoted in LocalAI #11970 (closed for another reason), with no
cause; localai-bugs.md B26.*

<!-- issue:B26 -->
GET /backend/monitor returns 500 "no grpc backend found for <model file>.bin": the fallback process sample looks the backend up under the wrong key

**LocalAI version:** v5.0.1. The cause below is in v5.0.1's source; #11970
quotes the same answer on v4.9.0, and we see it on a build of v5.0.1 with
unrelated local patches.

**Describe the bug.** For a backend that does not implement the `Status` RPC
— the llama-cpp backend, for one — `GET /backend/monitor?model=<name>`
answers HTTP 500 instead of the sample of the backend's process:

```
{"error":{"code":500,"message":"backend <name> experienced an error retrieving status info via rpc: rpc error: code = Unimplemented desc = , then failed local node process sample: no grpc backend found for <model file>.bin","type":""}}
```

**To reproduce.** Load any llama-cpp model, then
`curl 'localhost:8080/backend/monitor?model=<name>'`.

**Cause.** `SampleLocalBackendProcess`
(`core/services/monitoring/backend_monitor.go`) sets `backend = config.Model`
— the model's FILE — appends `.bin` when it is missing, and calls
`modelLoader.GetGRPCPID(backend)`. `GetGRPCPID` (`pkg/model/process.go`)
looks the loader's store up by that string, and the store is keyed by the
model's id. The lookup cannot succeed, so the fallback always fails.

**Expected behavior.** The process sample (`gopsutil` RSS and so on) the
route was written to fall back to.

**Suggested fix.** Call `GetGRPCPID` with the model's id — the name the route
was asked for.
<!-- /issue -->

## B20 — a voice profile's lease outlives the process that took it

*For the filer: a privacy matter — a deleted recording can stay on disk. Our
fix is `deploy/localai/patches/staged/0019-voiceprofile-abandoned-leases.patch`
(with 6 new specs in LocalAI's own suite), live in our server since
2026-11-25; localai-bugs.md B20.*

<!-- issue:B20 -->
voice profiles: a lease left by a killed process is never removed, so a deleted profile's recording can stay on disk

**LocalAI version:** v5.0.1 (`core/services/voiceprofile`).

**Describe the bug.** `Store.LeaseAudios` hard-links a profile's clip into
the Store's own lease directory (`voice-profiles/.leases/<uuid>`, one per
process) so that deleting a profile does not break a synthesis in flight.
The request's handler removes the link when it ends (`defer release()`), and
`Store.Close` removes the directory. A process that is killed does neither,
and nothing else ever looks: no start and no `Delete` examines another
process's lease directory.

So a lease taken by a process that was killed stays for as long as the data
directory lives. If its profile is deleted afterwards, the profile's
directory goes and the recording itself stays on disk behind the link, in a
place no listing of profiles shows. For a voice profile that is a person's
voice outliving its deletion.

**Seen.** On an instance that had been restarted and killed a number of
times over six weeks: nine directories under `.leases`, eight empty, one
holding a 1.3 MB link (link count 2, the same inode as a clip of a profile
that still existed) made weeks earlier by a process long gone.

**To reproduce.** By the code: start a synthesis with `voice:
localai://voice-profiles/<id>`, kill LocalAI with SIGKILL while it runs,
start it again, delete the profile — the clip's bytes are still under
`voice-profiles/.leases/<old uuid>/`. The spec that comes with the patch
does it in-process: a Store that takes a lease and is abandoned as a killed
process leaves it, a second Store that deletes the profile.

**Expected behavior.** Deleting a profile removes its recording once no
living process uses it.

**Suggested fix.** Let each Store hold a lock on an owner file in its lease
directory for as long as it lives (`flock`, `LockFileEx` on Windows — as the
add-on stores already do); the kernel drops it however the process ends. A
lease directory whose owner file can be locked belongs to nobody: remove it,
with its leases, when a Store starts and whenever a profile is deleted. A
patch with this change and its specs is available.
<!-- /issue -->

## B19 — the llama-cpp backend calls `exit()` inside its signal handler

*For the filer: llama.cpp knows the same handler in its own server
(ggml-org/llama.cpp #29581, open, a one-line fix proposed there, not
merged); LocalAI's `grpc-server.cpp` carries its own copy. Our fix is
`deploy/localai/patches/staged/0014-llama-cpp-second-signal-exit.patch`,
live in our engine; localai-bugs.md B19.*

<!-- issue:B19 -->
llama-cpp backend: a second SIGTERM calls exit() inside the signal handler; the process can stay, holding its GPU memory, until LocalAI's SIGKILL 15 s later

**LocalAI version:** v5.0.1 (`backend/cpp/llama-cpp/grpc-server.cpp`,
`signal_handler`).

**Describe the bug.** The backend's handler serves the first SIGTERM/SIGINT
by starting the shutdown, and answers a second one with `fprintf` and
`exit(1)`. `exit()` is not async-signal-safe: it runs the exit handlers and
the static destructors from inside the handler, against the clean-up the
first signal started, and can block there. The log reads "Received second
interrupt, terminating immediately." and the process stays, its device
memory still held, until LocalAI kills it at the end of the 15 s grace.
ggml-org/llama.cpp #29581 describes the same handler in llama.cpp's own
server, with a stack that deadlocks in `free()`.

LocalAI makes the second signal likely: it stops a backend with SIGTERM to
its process GROUP, so anything in that group that forwards the signal to the
backend — a wrapper script, a supervisor — delivers it twice, a millisecond
apart.

**Seen.** A backend behind a small wrapper process (Intel Arc Pro B70, SYCL
build): 3 of 14 `POST /backend/shutdown` took 15.06 s instead of 0.11–0.21 s
and ended with `exitCode=-1`. The same backend started directly, one signal:
0 of 22.

**Expected behavior.** A second signal ends the process at once.

**Suggested fix.** In the second-signal branch use async-signal-safe calls
only: `write(2, …)` for the message and `std::_Exit(1)`. A patch with a
standalone test (a child whose exit handler never returns is gone at once)
is available.
<!-- /issue -->

## B25 — the backend-logs websocket can lose a line, and its spec is flaky for it

*For the filer: no patch of ours; found because it failed one run in three
on a builder; localai-bugs.md B25.*

<!-- issue:B25 -->
backend-logs websocket: a line appended between the initial batch and Subscribe is never sent (the spec "should send initial lines and stream new lines" fails now and then)

**LocalAI version:** v5.0.1 (`core/http/endpoints/localai/backend_logs.go`).

**Describe the bug.** The handler reads the lines so far
(`BackendLogs().GetLines`), writes them as the `initial` message, and only
then calls `BackendLogs().Subscribe`. A line appended between the write and
the subscription is in neither: that client never receives it.

LocalAI's own spec reads the initial message and at once appends a line, so
it loses this race now and then and times out on its 2 s read.

**To reproduce.** Build the package's test binary once and run the one spec
in a loop:

```
go test -c -o localai.test ./core/http/endpoints/localai
for i in $(seq 300); do ./localai.test -ginkgo.focus 'should send initial lines and stream new lines' >/dev/null || echo fail; done
```

On an idle machine: 1 failure in 200 runs. With the machine's CPUs busy: 20
to 24 in 300. (Counted on two builds of v5.0.1 with local patches, none of
which touches these files; the same rate on both.)

**Expected behavior.** Every line appended after the connection is
established is sent.

**Suggested fix.** Subscribe before reading the lines so far, then send the
initial batch (dropping from the stream what the batch already holds), or
take the batch and the subscription under the buffer's lock.
<!-- /issue -->

## B22 — a repeated `LoadModel` is answered "already loaded" by the model's name alone

*For the filer: low urgency upstream; it mattered to us once a second
`LoadModel` became a real call. Fixed inside our own patch of the backend
(`staged/0023`): the options of the load are kept and a repeat that differs
is `FAILED_PRECONDITION` naming the fields; localai-bugs.md B22.*

<!-- issue:B22 -->
llama-cpp backend: a LoadModel for the model a process already holds succeeds with "Model already loaded" whatever its options are

**LocalAI version:** v5.0.1 (`backend/cpp/llama-cpp/grpc-server.cpp`,
`LoadModel`).

**Describe the bug.** When the process already holds a model, `LoadModel`
compares only the name:

```cpp
if (loaded_model_identity == request->model()) {
    // Same model: a repeated request is a no-op, not a reload.
    result->set_message("Model already loaded");
    result->set_success(true);
    return Status::OK;
}
```

A second `LoadModel` with the same name and OTHER options — another context
size, other backend options, another file — gets the same success, and the
options of the first load go on computing. A caller that sends its options
again is told they are in force when they are not.

**To reproduce.** Send `LoadModel` twice to one backend process with the
same `Model` and a different `ContextSize`: both answer success; the context
is the first one's.

**Expected behavior.** "Already loaded" only for the options that are
loaded; a repeat that differs refused (`FAILED_PRECONDITION`, as a load of
another model already is), naming what differs.

**Suggested fix.** Keep the options the model was loaded with and compare
them on a repeat.
<!-- /issue -->

## B17 — a request: let a model file declare the reference-audio contract for a backend the table does not know

*For the filer: file as a FEATURE REQUEST. The present behaviour is
documented ("the flag cannot enable cloning on a backend that does not
implement LocalAI's reference-audio contract"); what is missing is a way to
say that a backend does. Our change is
`deploy/localai/patches/staged/0011-config-voice-cloning-opt-in-unknown-backend.patch`
(P13), live in our server;
localai-bugs.md B17.*

<!-- issue:B17 -->
tts.voice_cloning: true is never read for a backend installed under a name the capability table does not know

**LocalAI version:** v5.0.1 (`core/config/backend_capabilities.go`,
`VoiceCloningForModel`; `core/http/endpoints/localai/tts.go`).

**Is your feature request related to a problem?** A backend that an operator
builds and installs under a name of its own — a patched build of llama-cpp,
say — cannot be given a saved voice profile, even when it implements the
reference-audio contract. Every request with `voice:
localai://voice-profiles/<id>` is answered 400 "selected model does not
support reference-audio voice cloning" before the backend is asked, and
setting `tts.voice_cloning: true` in the model's file changes nothing.

`VoiceCloningForModel` resolves `cfg.Backend` in the capability table first
(the exact name, else with a gallery prefix and a release suffix stripped)
and returns nil when nothing is found. The model's own `tts.voice_cloning`
is read only after that, so for an unknown backend name the explicit opt-in
is never reached.

**Describe the solution you'd like.** Honour an explicit `tts.voice_cloning:
true` for a backend the table does not know — the operator who writes it
answers for the backend — and keep the table's detection for every name it
does know. Without the key, nothing changes.

**Additional context.** A reference passed as a plain file path with
`params.ref_text` already reaches such a backend untouched; only the saved
profiles are closed to it. A small patch is available.
<!-- /issue -->

