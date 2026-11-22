"""RAGE's CPU REFERENCE — the one resolution every caller goes through
(operator, 2026-11-22: "The CPU reference is per model, not per collection.
This must be in the rag engine API"; "A collection then inherits the
reference cpu model upon creation if already present on the node"; "This
must pass through the rag engine API, never spaghetti code within the
collection").

A CPU reference is the encoder's CPU twin's answers to the calibration set
(docs/notes/near-exact-runs.md §2): what every card run of those weights is
held to (invariant 12). It is the MODEL's — fixed by the weights, the set,
the window (ctx), the cut and the role's instruction — never a collection's.
It lives once in the node's store, `<LOUISE_ROOT>/state/cpu-reference/<key>/`
(app/hold/cpu_reference.py keeps the files), and is found by its key:

- `key(codex, role, weights)`: the key, from a Codex alone;
- `resolve(codex, role, weights, count)`: the reference, or None — the gate
  (app/run_check.py), the re-check and `hold collection new` (which runs no
  `cpu-reference` step for a key already kept: the new collection INHERITS
  it) all ask here, and nothing else does;
- `store(codex, role, weights)`: the store's directory for that key, where
  `hold embedder cpu-reference` makes one (`cpu_reference.obtain`);
- `put(codex, role, …)`: a reference written under its key;
- `twin(model)`: the CPU twin's model name, so nobody has to name it.

Nothing here resolves by a collection's name or root: no collection holds a
copy, and no collection-side code finds or copies one (the per-collection
copies and `carry`'s copying they replace, 2026-11-22). What a collection
kept before the store (2026-11-06) is held to it by `legacy` (`louise dev
cpu-reference-legacy`) and never taken in: such a file names no inputs, so
its key would be a guess.

Standardised alternative, not taken: a content-addressed artifact store
(an OCI registry of references, addressed by the key's digest) would let
peers share them too; it pays once references travel between nodes, which
they do not (local evidence, 03 §3.3).
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.hold import cpu_reference as files

#: the CPU twin of an encoder is served under the encoder's own name with
#: this suffix (vega-nemotron-3-embed-1b-cpu, vega-qwen3-embedding-4b-cpu,
#: vega-dar-legal-it-cpu …): the name taken where none is given, and refused
#: unless its run states the CPU and the same weights
TWIN_SUFFIX = "-cpu"


def twin(model: str) -> str:
    """The CPU twin's model name for an encoder's (RULE 2: determined, not asked)."""
    return model if model.endswith(TWIN_SUFFIX) else f"{model}{TWIN_SUFFIX}"


def key(codex, role: str, weights: str) -> dict[str, Any]:
    """The store's key for a role of a space, from its Codex alone: the
    role's weights (as its run states them), the space's set, ctx and cut,
    and the role's instruction."""
    space = codex.space
    instruction = ((getattr(space, "document_instruction", None) or None) if role == "document"
                   else (space.query.instruction or None))
    return files.node_key(weights=str(weights), set_id=space.calibration.set, ctx=int(space.ctx),
                          instruction=instruction, role=role,
                          cut=getattr(space, "cut", None) or "characters")


def store(codex, role: str, weights: str) -> Path:
    """The store's directory for this key, made (with its key.json)."""
    return files.node_directory(key(codex, role, weights))


def resolve(codex, role: str, weights: str, count: int) -> files.CpuReference | None:
    """THE REFERENCE for this role's inputs on this node, whichever
    collection made it, or None. Nothing is created on a miss."""
    d = files.node_directory(key(codex, role, weights), make=False)
    if not d.is_dir():
        return None
    got = files.node_find(d, set_id=codex.space.calibration.set, count=count)
    return got if got is not None and got.weights == str(weights) else None


def put(codex, role: str, *, run_id: str, run: dict[str, Any], vectors: Sequence[Sequence[float]],
        extra: dict[str, Any] | None = None) -> Path:
    """A reference written under its key (refused unless `run` is a CPU run)."""
    return files.write_in(store(codex, role, run["weights"]), role, run_id=run_id, run=run,
                          set_id=codex.space.calibration.set, vectors=vectors, extra=extra)


def legacy(root: Path, name: str, codex) -> dict[str, Any]:
    """What a collection kept under its own state before the store
    (2026-11-06), held to the store. NOTHING IS WRITTEN: such a file names
    no inputs (ctx, cut, instruction), so filing it under the space's key
    would be a guess — wikidata's copy held characters-cut vectors for a
    tokens space and failed its gate at 0.974831 (2026-11-06). Each file is
    `identical` (the store holds its vectors under the key: the copy is
    redundant), `differs` (the store's, by key, is what every gate reads),
    `unproven` (the store holds none for the key: made once for the node by
    `hold embedder cpu-reference`), or `refused` (another set, unreadable,
    not a CPU run's)."""
    out: dict[str, Any] = {"collection": name, "identical": 0, "differs": [], "unproven": [],
                           "refused": []}
    for role in ("document", "query"):
        d = files.legacy_directory(Path(root), name, role)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                ref = files.read(f)
            except Exception as e:   # noqa: BLE001 — a file that does not read is said, never fatal
                out["refused"].append(f"{role}/{f.name}: {e}"[:160])
                continue
            run = ref.run_object or {}
            if ref.set_id != codex.space.calibration.set or run.get("driver") != "cpu" or not ref.weights:
                out["refused"].append(f"{role}/{f.name}: of another set, or not a CPU run's")
                continue
            held = resolve(codex, role, ref.weights, len(ref.vectors))
            if held is None:
                out["unproven"].append(f"{role}/{f.name}")
            elif files._f4(held.vectors) == files._f4(ref.vectors):
                out["identical"] += 1
            else:
                out["differs"].append(f"{role}/{f.name}")
    return out
