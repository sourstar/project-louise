# LocalAI 5.0.1 Add-ons: can they simplify Louise's deployment?

*2026-11-21. Operator: "evaluate an integration with localai 5.0.1 using the
Add-ons interface. The idea is to simplify the deployment through localai
Add-ons." Read in LocalAI's source at v5.0.1 (dd5780a: `docs/content/addons/`,
`docs/content/features/addon-sdk.md`, `pkg/addons/`) and on the node by
München (read-only, ~01:40Z); the architecture session's view, relayed by
München, is below. Nothing was changed on the node.*

## What "Add-ons" is in v5

Two different things share the Add-ons page:

1. **Add-ons proper.** A separate executable LocalAI starts with
   `--localai-addon-v1` and supervises: it serves chat tools, skills, pages,
   messaging connectors to LocalAI's own agents and chat. It runs as LocalAI's
   OS user, unconfined ("Nothing confines it"), at most 512 MiB packed, Go SDK
   (`pkg/addons/sdk`); an add-on outside LocalAI's repository cannot have a UI
   yet. Signed only through a LocalAI release index; a local zip is
   "Local file, unsigned" and its install is finished in a browser.
2. **Library sources** (`addons/sources.json`, `local-ai library add`,
   `/api/library/sources`). A library may list entries of `kind: backend`: an
   `oci://` image **pinned by digest**, optional sigstore policy, size-checked
   (8 GiB default), installed by `POST /backends/library/install
   {sourceId, name, version}` into the same `/backends/<name>` with the same
   `metadata.json`. Updates are shown, never applied on their own.

## Fit with Louise

| option | pros | cons |
|---|---|---|
| **A. Louise (worker, web, VeGa service) as add-ons** | one install page | **rejected.** Loses the containers' `mem_limit` + `restart: unless-stopped` (the crash rule, the OOM rule) and cgroups `cpu.weight`; unconfined under LocalAI's user; Python app inside a Go-SDK, 512 MiB package; no UI from our repo; couples Louise's life to LocalAI's |
| **B. Louise's backends from a Louise backend library** (vega-louise-*, louise-sycl-f16-llama-cpp-*, louise-cpu-llama-cpp-*, louise-python) | replaces the hand path (tar on tank → `ocifile://` → `/api/backends/install-external` → scripts in `deploy/vega/switch/`) by one source and one named install; digest-pinned and size-checked by LocalAI itself (a standard: OCI + sigstore, not ours); a peer gets the same backend by name (RULE 2); the Backends page shows the library and the pinned digest | images must be pushed to a registry (ghcr.io/sourstar; `deploy/localai/gallery/index.yaml` already names them, never pushed) — GB-sized SYCL stacks; signing needs a GitHub workflow that signs the pushed digest (keyless), else `requireSignature: false`; the route lives in the add-on owner, so the owner must stay on (see Security); conditions below |
| **C. A tools-only add-on "Louise Ask"** for LocalAI's chat | LocalAI's agents could search Louise's collections | an integration, not a deployment simplification; Open WebUI already fronts Ask; Go code to maintain; a token by path inside an unconfined child (inv. 8) |
| status quo | works; engines pinned by tree digest; nothing new | every backend install is a hand procedure per node and per peer |

### Conditions for B (each checked before it is used)

- **Engine identity unchanged (invariant 12, 09 §9.3).** The stated `image`
  is the tree digest of the installed files and leaves out the top-level
  `metadata.json` (`deploy/vega/vega_wrapper.py`, `tree_manifest`), so the
  install route does not move it. But the engine's `build` is
  `metadata.json`'s `version` when set, else the tree's first 12 hex digits
  (`vega_wrapper.py`, `"build": backend.version or short_build(tree)`). A
  library entry's `version` is written there, so **it must equal the build
  every Codex already states** for that backend, or each run states a new
  engine and every collection must be re-gated. Checked on one backend:
  install from the library beside the tar install, compare tree digest and
  stated engine byte for byte.
- **Our core is a source build** (v5.0.1-1-g105104c32, P1), so LocalAI's
  *official* libraries resolve to master's mutable development index. That
  does not touch a Louise library, but `official_libraries` should be off
  (`LOCALAI_OFFICIAL_LIBRARIES=false`) so no mutable official entry can
  shadow or be installed by mistake; `LOCALAI_AUTO_UPGRADE_BACKENDS=false`
  and `--auto-upgrade-addons=false` stay as they are.
- **Bandwidth (invariant 5).** The pull is LocalAI's, outside Louise's
  transfer manager — as the tar route's copy is today; nothing changes.

## Security (found while reading, independent of the choice)

München's log read: the add-on owner is ON by default and the node runs with
sign-in off, in "local-owner mode … any caller that can reach this port can
confirm addon proposals". Nothing is installed (`/data/addons` holds only its
state files), and an unsigned zip still needs grants that are locked without
sign-in; but an official-library add-on is an unconfined process anyone on
the network reaching :30286 can propose and confirm.

| choice | pros | cons |
|---|---|---|
| **`LOCALAI_ADDONS_ENABLED=false`** (recommended now) | closes the path; Louise uses no add-on route | B is not possible while it is off (its install route lives in the owner) |
| leave it on | B possible later without a redeploy | an unconfined-process install open to the LAN |
| turn sign-in on | B and safety | Louise's calls to LocalAI's admin routes then need an API key; LocalAI reads it only from an env value (R3 in localai-bugs.md), against invariant 8 |

## The architecture session's view (relayed by München, 2026-11-21)

- Add-ons do not fit Louise's deployment: no container ever; a digest pin
  for add-ons only on a release build, and ours (P1) reads master's mutable
  index.
- The owner is a **second install path**: a click in LocalAI's UI can put a
  backend or an add-on on the node outside every calibration gate (inv. 12);
  an add-on's secrets would sit on LocalAI's filesystem, readable by every
  backend (inv. 8); its settings are a third store set by clicks (inv. 10);
  Louise inside LocalAI's process space breaks the GPU boundary's container
  separation (inv. 7); add-ons share LocalAI's cgroup with no cap each (the
  crash and OOM rules); an install finished in the browser is the dialog
  RULE 2 calls a defect.
- Recommends `LOCALAI_ADDONS_ENABLED=false` **and**
  `LOCALAI_ADDONS_BACKEND_LIBRARY=false`; cons: Studio and Talk unavailable,
  neither used. München has put that switch-off to the operator.
- Deployment is to be simplified inside Louise's own path (the deploy script,
  the TrueNAS app YAML, backends built and pinned by us); a step there that an
  add-on would have made easier goes to architecture.

Where this note differs: B uses only *our* library, whose entries are pinned
by digest whatever the build, and installs by name from the command line
(`local-ai library add`, `POST /backends/library/install`), not a browser.
But it needs the owner on, and so reopens the second path architecture wants
closed unless sign-in is on (inv. 8, R3). Both readings agree on now.

## Decision (operator, 2026-11-21, in this session)

**Switched off**: `LOCALAI_ADDONS_ENABLED=false` and
`LOCALAI_ADDONS_BACKEND_LIBRARY=false` in the LocalAI app's env (a node
action, München's). It replaces the earlier choice below. Option B is not
built while the owner is off.

### Superseded: on, with sign-in (operator, earlier the same day, via München)

**On, with sign-in**, not the switch-off. Wiring asked of architecture: Louise
sends a LocalAI API key read from a FILE on every call (inv. 8 on Louise's
side); then `LOCALAI_AUTH=true`, registration by invite or approval,
named-user ownership. Nothing changes on the node until that is deployed. To
check in that wiring: where LocalAI itself keeps the key (an env value would
be R3 again; a key issued to a user and held in LocalAI's store is not).
Option B stays possible, since the owner stays on.

## Recommendation (before the decision)

- B is the only Add-ons use that simplifies deployment; A is rejected; C is
  not a deployment matter.
- Switch the owner off now (architecture and this note agree; München holds
  the operator's question).
- B is worth building only if the push to a registry is wanted anyway, and
  only with the owner gated: once the images are pushed: a `deploy/localai/library/
  index.yaml` (`kind: backend`, digest-pinned, `version` = stated build), a
  workflow that signs each pushed digest, and one gate per backend proving the
  stated engine unchanged.
- If the operator says nothing, the owner stays on and open (a node action
  needs the operator's word); B is not built.
