# P1 for LocalAI upstream: `GET /system` lists each loaded model's load options

*Prepared 2026-11-11 for the operator, who decided on "C": the operator
submits it to LocalAI in their own words. None of the sessions files
anything. This guide gives the facts the operator needs to own the change.*

- **The diff:** `system-load-options.diff`, against LocalAI master
  `4b3c4ec72fcd25fdef26d24a7816eb59a1c342d1` (2026-11-11). Apply it with
  `git apply`.
- **Its source:** `deploy/localai/patches/0001-system-list-load-options.patch`
  (P1 in our series, written 2026-10-03). The Go code and tests are
  unchanged. The docs page is new, because LocalAI requires docs in the same
  change.
- **No commit message and no trailers** are in the diff. Those are the
  submitter's (§4).

## 1. What it does

LocalAI completes a model's config before it loads it. It adds the context
size and GPU layers guessed from the GGUF, the hardware auto-tuning and the
application's runtime settings. So what a backend actually runs with is in
no file and on no endpoint, only in a DEBUG log line.

The change keeps a copy of the `ModelOptions` sent with `LoadModel` on the
loaded model. `GET /system` then lists, per loaded model:

- `load_options`: those options as protojson, with proto field names and
  unset fields omitted;
- `load_options_hash`: `config.EffectiveModelOptionsHash` of the same
  options, the hash the distributed registry already records per replica.
  It changes exactly when a reload gave the backend different options.

**Why we need it** (the operator's own case, to put in their own words):

- A client deciding how to treat a model needs to know how it was loaded.
  Ours needs to know whether llama.cpp was started with an option that
  frees the context between requests, so it can count the model's memory
  at its working size rather than at its idle size.
- It also needs to notice that a reload changed what a model runs with.

## 2. The files, one line each

| file | change |
|---|---|
| `pkg/model/model.go` | `SetLoadOptions` / `LoadOptions` on `Model`, under the model's lock. Each keeps its own `proto.Clone`, so neither the caller nor a reader can change what was recorded |
| `pkg/model/initializers.go` | one line: after a successful `LoadModel`, `client.SetLoadOptions(options)` |
| `core/schema/localai.go` | two fields on the loaded-model entry: `LoadOptions json.RawMessage` (swagger type object) and `LoadOptionsHash string`, both `omitempty` |
| `core/http/endpoints/localai/system.go` | fills them per model. A marshal or hash error leaves both out; it never fails the request |
| `swagger/{docs.go,swagger.json,swagger.yaml}` | the two fields, added by hand (§5) |
| `docs/content/reference/system-info.md` | two table rows, a short "Load options" section and the example response |
| `pkg/model/load_options_test.go` (new) | the loader records exactly what the backend's `LoadModel` received (a fake backend captures it); the model keeps its own copy |
| `core/http/endpoints/localai/system_info_test.go` | `/system` lists the options with proto field names, unset ones omitted, and their hash |

## 3. Testing it before submitting

- **The tests it carries** (Ginkgo, as the packages already use):
  `go test ./pkg/model/ ./core/http/endpoints/localai/`.
- **Build:** LocalAI's PR template asks for `make build`. That command
  builds the React UI too. `go build ./...` covers the Go side.
- **By hand:** load one model, then `curl -H 'Authorization: Bearer …'
  http://localhost:8080/system`, and compare its `load_options` with the
  "LoadModel" DEBUG line.
- **Status:** not run yet. `git apply --check` passes on v4.12.1
  (`ec92740`, München, 2026-11-11) and on master `4b3c4ec` (this session).
  The tests run in the node's build area (München), because this cloud
  container lacks the room for LocalAI's Go modules. The results go below:
  - *v4.12.1 + P1 (the node's core):* not run yet;
  - *master `4b3c4ec` + this diff:* not run yet.

## 4. LocalAI's rules for AI-assisted changes, and how this meets them

Sources: `AGENTS.md`, `.agents/ai-coding-assistants.md` and `CONTRIBUTING.md`
on master, read 2026-11-11. LocalAI follows the Linux kernel's policy for
AI coding assistants. Unlike llama.cpp's, it does **not** forbid
AI-assisted contributions. It sets rules for them:

- **No `Signed-off-by` from the AI.** The operator adds their own; the PR
  template asks for signed commits (DCO).
- **No `Co-Authored-By` for the AI.** The diff carries none.
- **An `Assisted-by:` trailer**, in this format:
  `Assisted-by: AGENT_NAME:MODEL_VERSION [TOOL1] [TOOL2]`.
  - Here that is `Assisted-by: Claude:<the model version>`. The operator
    fills in the version that wrote it (Claude Code shows it).
  - It is left out of this repository on purpose: this project names no
    model in its files.
  - Plain tools (git, go, make) are not listed.
- **The submitter must understand every line**, and must check that the
  identifiers exist in the current tree. Checked on master `4b3c4ec`:
  `SystemInformations(cl, ml, appConfig, sampler)`, `NewModel`,
  `NewInMemoryModelStore`, `SetModelStore`, `NewModelLoader`,
  `NewModelConfigLoader` and `config.EffectiveModelOptionsHash` all exist
  with the signatures the code and tests use.
- **Conventional commits.** For example the title
  `feat(system): list the options each loaded model was given`. The PR
  title takes `[component]` in front.
- **Docs with code:** done, in `docs/content/reference/system-info.md`.
- **Coverage ratchet:** the change only adds tests.

## 5. What a reviewer may ask, with the answers

- **"Why are the swagger files edited by hand?"** On 2026-10-03,
  regenerating with swag v1.16.6 also rewrote unrelated definitions on
  master. If the maintainers prefer `make swagger`, run it, then keep only
  the two fields' hunks or say what else moved. The hand-added hunks land
  on the right definition on master `4b3c4ec` (beside `backend`, `id`,
  `process`).
- **"Why `json.RawMessage` and not a typed struct?"** The options are
  `pb.ModelOptions`. protojson with proto names is their one stable JSON
  form, and a mirror struct would drift with every new proto field.
- **"Do the options leak anything?"**
  - The model's `env:` entries are copied into the options, as the DEBUG
    log already shows. The docs page says not to put secrets there.
  - Proxy options name a key's variable or file, never the key.
  - `/system` is behind `adminMiddleware` on master
    (`core/http/routes/localai.go`).
- **"Why isn't the LocalAI Assistant's `system_info` tool updated?"**
  - AGENTS.md asks that an API change a user could reach through an agent
    be reflected in `core/services/localaitools/`.
  - That tool already reads `/system`, but reports loaded models by id
    only (`SystemInfo.LoadedModels []string`), in both the `inproc` and
    the `httpapi` client. The new field leaves it unchanged.
  - Exposing `load_options` there would put the models' `env:` into a chat
    model's context. That is a choice the maintainers make, so the guide
    leaves it to them.
  - If they want it, a small follow-up adds `load_options_hash` (no env)
    to the DTO, both clients, the tool schema and the built-in skill.
- **"What about distributed mode?"** The record lives on the frontend that
  issued the load. A model held by a remote worker, or seen by another
  frontend, lists none. This is the per-instance behaviour that
  `.agents/distributed-state.md` asks a change to document, and the docs
  page states it.
- **"What does it cost?"** One protojson marshal and one SHA-256 of a
  `ModelOptions` per loaded model per `/system` call, plus a proto copy
  per load. Not measured; a reviewer may ask for a number.

## 6. What it does not do

- It does not change how a model is loaded, or what is sent to it.
- It lists nothing for a model that this frontend did not load.
- It does not expose the options anywhere other than `/system`.
