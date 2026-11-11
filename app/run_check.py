"""The node's side of "validate once; then every answer says which run gave
it" — docs/03-checking.md §3.3; migration-plan step 13e.

`app/hold/runs.py` knows what a validated run is and refuses a write that
does not name one. It embeds nothing: `app/hold/` stays free of the corpus
machinery. THIS module is where the node's embedding client meets it, for
the command line and the worker alike, so there is one way a run gets
validated and one way a write is guarded.

    canary_failed(...)  the engine discarded a context that did not reproduce
                        its canary: its run is SUSPECT. No write relies on its
                        kept statement, and the stage's next look sets that
                        statement aside and gates the run from scratch.
    check_write(...)    the worker, before a write: a DICTIONARY LOOKUP. It
                        never runs the gate — several document threads reach
                        it at once, and the gate is triggered once per
                        (space, run), not once per answer. It raises, the
                        document is deferred untouched, and what it found is
                        forgotten so that `embed_allowed` looks again.
    embed_allowed(...)  the worker, before the stage: which run answers now?
                        One it has not tested is gated AT ONCE, here, by the
                        one thread that owns the stage. A run that fails, or
                        an endpoint that states none, PAUSES the collection's
                        embedding with its reason — never an error on a
                        document, which would take it out of every selection.
    guard(...)          the command line, before a write: the same check, and
                        the gate inline, since there is one thread.
    validate(...)       the gate against the run that answers now, and the
                        statement kept — pass or fail.

VERSION-3 COLLECTIONS ONLY (`runs.applies`). Every collection the node held
when this landed is version 2 or legacy: for them all of this returns at
once and the behavioural backend gate stays what it was. For a version-3
collection it REPLACES that gate — "what keeps later work honest is not a
test repeated during the work" (03 §3.3) — and no setting turns it off.

EVERY WAIT IS THE EMBEDDING CLIENT'S, which bounds each request. The gate is
some 550 of them, sent a slice at a time with `stop` asked between slices, so
a node that is stopping does not start the next (CLAUDE.md: a worker that
cannot finish cannot honour SIGTERM).
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import httpx

from . import collections as collections_mod
from . import config, embeddings
from .hold import calibration as cal, shard as shard_mod, embedder as emb, runs as runs_mod
from .hold.codex import Codex

#: a text short enough to cost nothing, sent to learn which run answers
PROBE = "louise"
#: the same, in a PAIR space's form: the run that answers is asked in the
#: form the pipeline writes (09 §9.12, amended 2026-10-08)
PAIR_PROBE = ("louise", "louise")
#: texts a request-group of the gate; `stop` is asked between two of them
SLICE = 32
PASS_TTL_S = 1800     # ask a validated endpoint which run answers this often
FAIL_TTL_S = 300      # and one that is paused, this often


def probe_for(codex, role: str = "document"):
    """What asks which run answers: a pair on a pair space's document side,
    the text everywhere else — a query is one text in every space."""
    if role == "document" and cal.form(codex) == "pair":
        return PAIR_PROBE
    return PROBE


# ---- the embedding client, remembering who answered ---------------------------

def _stopping(stop: Callable[[], bool] | None, name: str) -> None:
    if stop is not None and stop():
        raise emb.GateDeferred(f"{name}: the node is stopping; the gate is not finished "
                               f"and nothing is kept")


def answers(recipe, *, query: bool = False, how: str = "normalised",
            stop: Callable[[], bool] | None = None, name: str = "",
            collection: Any = None, asked: bool = True,
            timeout: float = 300) -> runs_mod.Answers:
    """The node's embedding client as an `Answers`.

    `how`: "normalised" — what a write stores; "one" — each text alone, as
    the endpoint returned it (the fingerprint); "contribute" — the same raw
    vectors, sent the way THIS peer contributes: its batch (the collection's
    `[encoder] batch`, else EMBED_BATCH), its
    EMBED_CONCURRENCY, and as many requests at once as its worker keeps
    documents in flight (`recipe.concurrency`). That last one is the setting
    the batching fault of 2026-07 lived in, so it is reproduced, not assumed.

    `collection`, for "normalised" on the document side only: the texts are
    that collection's CHUNKS (a check re-embedding what it holds), fed in
    their layer's form through `embeddings.embed_chunks` — a pair space's
    calibration pairs among them, as a check embeds them. Without it they
    are the calibration set, a probe or the gate's, each already in the
    space's form — a text, or a pair (`embeddings.embed_calibration`; 09
    §9.12, amended 2026-10-08).

    `asked` (the query side): the texts are already as the query side
    receives them — the gate and the re-check prefix the space's instruction
    themselves, as calibration did — so the client does not prefix it again
    (2026-10-21: it did, and every query run was gated on the instruction
    twice). A caller handing raw queries passes `asked=False`.

    `timeout` bounds each request (a card's 300 s by default); the CPU
    reference passes its own, scaled to the space's window
    (`cpu_reference.request_timeout`)."""
    def normalised(texts: list[str], seen: set) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), SLICE):
            _stopping(stop, name)
            part = texts[i:i + SLICE]
            if collection is not None and not query:
                out += embeddings.embed_chunks(collection, part, recipe=recipe, runs=seen)
            else:
                out += embeddings.embed_calibration(recipe, part, query=query, runs=seen,
                                                    asked=asked, timeout=timeout)
        return out

    def one(texts: list[str], seen: set) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            _stopping(stop, name)
            out += embeddings.embed_calibration(recipe, [text], query=query, runs=seen,
                                                one_at_a_time=True, asked=asked,
                                                timeout=timeout)
        return out

    def contribute(texts: list[str], seen: set) -> list[list[float]]:
        _stopping(stop, name)
        lanes = max(1, min(int(getattr(recipe, "concurrency", 1) or 1), len(texts)))
        if lanes == 1:
            return embeddings.embed_calibration(recipe, texts, query=query,
                                                raw=True, runs=seen, asked=asked,
                                                timeout=timeout)
        # as the worker does it: `lanes` documents in flight, each its own call
        groups = [list(range(k, len(texts), lanes)) for k in range(lanes)]
        with ThreadPoolExecutor(max_workers=lanes) as ex:
            parts = list(ex.map(
                lambda idx: embeddings.embed_calibration(recipe, [texts[i] for i in idx],
                                                         query=query, raw=True, runs=seen,
                                                         asked=asked, timeout=timeout),
                groups))
        out: list[Any] = [None] * len(texts)
        for idx, vectors in zip(groups, parts):
            for i, v in zip(idx, vectors):
                out[i] = v
        return out

    return runs_mod.Answers({"normalised": normalised, "one": one,
                             "contribute": contribute}[how])


def creates_per_request(recipe, stated=None, *, query: bool = False, name: str = "") -> bool:
    """Whether the run that answers creates its context per request
    (`context_on_demand`). THE RUN'S OWN STATEMENT decides where it lists its
    load options (VeGa's: the engine as LocalAI launched it); otherwise a
    `/system` read made now — never a cached one, and never "no" for a runner
    that could not say (the review of 2026-11-12: a slow `/system` skipped the
    pass and the statement looked the same). A runner with no `/system` lists
    no options and creates no context per request."""
    from . import model_load
    side = embeddings._side(recipe, query)
    engine = getattr(stated, "engine", None)
    options = engine.get("options") if isinstance(engine, dict) else None
    if isinstance(options, dict):
        return model_load.options_on_demand(options)
    server = model_load.server_of(side.url)
    ids = model_load.loaded_models(server, max_age_s=0.0)
    if ids is None:
        if model_load.no_system_route(server):
            return False
        raise emb.GateDeferred(f"{name}: whether {side.model} creates its context per request "
                               f"cannot be told now: {server}/system did not answer")
    if side.model not in ids:
        raise emb.GateDeferred(f"{name}: {side.model} answered but {server}/system does not list it "
                               f"loaded, so its load options cannot be read; asked again later")
    listed = model_load.listed_on_demand(side.url, side.model)
    if listed is None:
        # the node's LocalAI (upstream, without our P1) lists no options: a row
        # without them is no "no" (München, 2026-11-12). An `api = "vega"` run
        # never comes here — its statement lists them
        raise emb.GateDeferred(
            f"{name}: whether {side.model} creates its context per request cannot be told: "
            f"its run states no load options and {server}/system lists none (a LocalAI "
            f"without load_options); an encoder behind VeGa states them "
            f"(docs/notes/vega.md)")
    return listed


#: how long the fresh-context pass waits, at most, for a keep its own earlier
#: passes left on the model to run out before its first round: RAGE's ceiling
#: is 2 s (app/rage/keep.py `CEILING_MS`)
SETTLE_TRIES, SETTLE_WAIT_S = 6, 0.5


def creations_of(recipe, stated, probe: Callable[[], Any], *,
                 get=httpx.get, sleep: Callable[[float], None] = time.sleep):
    """THE ENGINE'S COUNT OF ITS CREATIONS, as the gate's fresh-context pass
    reads it (`embedder.fresh_contexts` `counted`): a function giving
    `(calls, creations)` of the run that answers, since its load — every
    Embedding call it answered, and those its backend said it created the
    context for. The backend's own word per call (P11's trailer), counted by
    the VeGa wrapper and read at `<VEGA_URL>/vega/keeps` (app/vega/service.py).

    None — and the gate then REFUSES the run — where there is no such count:
    an encoder that is not `api = "vega"`, no VEGA_URL, no stated run.

    A READING IS CURRENT only as of the last answer the wrapper told its
    count before: a call with a keep of 0, or one its context was created
    for — every request of the pass. So before the FIRST reading `probe` (one
    request, its keep fixed at 0) is sent until a reading shows a context
    created for it alone, at most `SETTLE_TRIES` times `SETTLE_WAIT_S` apart:
    the count is then current, and the keep the gate's own earlier passes
    left on the model has run out. A model something else keeps busy never
    settles; its first round says so (deferred).

    A reading that cannot be made now is None (the pass is deferred). One
    that never can — a VeGa that keeps no such count, a backend that does not
    say what it created — is a `GateError`: the run is refused, not retried."""
    run_id = getattr(stated, "run_id", None)
    if getattr(recipe, "api", None) != embeddings.VEGA or not config.VEGA_URL or not run_id:
        return None
    state = {"settled": False}

    def read() -> tuple[int, int] | None:
        try:
            r = get(f"{config.VEGA_URL}/vega/keeps", timeout=10)
        except httpx.HTTPError:
            return None
        if r.status_code == 404:
            raise emb.GateError(
                f"{config.VEGA_URL} keeps no count of an engine's context creations (a VeGa "
                f"before `GET /vega/keeps`): a run that creates its context per request is "
                f"not admitted on creations nobody counted")
        try:
            r.raise_for_status()
            rows = r.json().get("runs")
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        row = next((x for x in rows or () if isinstance(x, dict) and x.get("run_id") == run_id), None)
        if row is None:
            return None
        try:
            calls, told, created = int(row["calls"]), int(row["told"]), int(row["created"])
        except (KeyError, TypeError, ValueError):
            return None
        if calls and told != calls:
            raise emb.GateError(
                f"run {run_id[:19]}…: its backend said what it did with its context for {told} "
                f"of {calls} call(s) — an engine before P11's count (the trailer "
                f"`llama-context-created-us`): its creations cannot be counted, and the run "
                f"is not admitted on them (docs/notes/idle-context-brief.md §10)")
        return calls, created

    def counted() -> tuple[int, int] | None:
        if not state["settled"]:
            state["settled"] = True
            before = None
            for attempt in range(SETTLE_TRIES):
                probe()
                now = read()
                if (before is not None and now is not None
                        and (now[0] - before[0], now[1] - before[1]) == (1, 1)):
                    break
                before = now
                if attempt < SETTLE_TRIES - 1:
                    sleep(SETTLE_WAIT_S)
        return read()
    return counted


def fresh_contexts(recipe, *, query: bool = False, stated=None, probe_item=PROBE,
                   **kw) -> runs_mod.Answers | None:
    """For a run that creates its context per request (`creates_per_request`):
    `answers(how="one")` with every request's keep fixed at 0, so each text is
    embedded on a context created for it (app/rage/keep.py `fixed`; the brief
    §1.4) — the gate's fresh-context pass. Its `creations` is the engine's
    count, which the pass holds every round to (`creations_of`); None where
    there is no count to read, and the gate then refuses such a run. None for
    any other run: the gate asks nothing."""
    from .rage import keep as rage_keep
    if not creates_per_request(recipe, stated, query=query, name=kw.get("name", "")):
        return None
    one = answers(recipe, query=query, how="one", **kw)

    def fresh(texts: list[str], seen: set) -> list[list[float]]:
        with rage_keep.fixed(0):
            return one._fn(texts, seen)
    out = runs_mod.Answers(fresh)
    out.creations = creations_of(recipe, stated, lambda: fresh([probe_item], set()))
    return out


def batched(recipe) -> bool:
    """Does this peer contribute otherwise than strictly one text at a time?"""
    return (embeddings.batch_size(recipe) > 1 or config.EMBED_CONCURRENCY > 1
            or int(getattr(recipe, "concurrency", 1) or 1) > 1)


def contributing(recipe) -> dict[str, int] | None:
    """How this peer contributes, as the gate's `contribute` pass measures it
    and `runs.check` compares it (`runs.record_batching`); None for strictly
    one text at a time, which every gate measures."""
    if not batched(recipe):
        return None
    return {"embed_batch": embeddings.batch_size(recipe), "embed_concurrency": int(config.EMBED_CONCURRENCY),
            "lanes": int(getattr(recipe, "concurrency", 1) or 1)}


def _endpoint(recipe, query: bool) -> str:
    return recipe.query_url if query and recipe.query_url else recipe.url


def _who(recipe, query: bool) -> tuple[str, str]:
    """What answers a side: its endpoint AND the model the request names.
    One LocalAI address serves every model (VeGa, 2026-10-09): keyed on the
    URL alone, the run deposit-granite last heard was taken for gutenberg's
    and quarantined its embedding. CLAUDE.md: bookkeeping keys on identity."""
    model = (getattr(recipe, "query_model", None) if query else None) or getattr(recipe, "model", "") or ""
    return _endpoint(recipe, query), model


def answering_run(recipe, *, query: bool = False, probe_item=PROBE) -> str:
    """Which run answers at this endpoint now: one tiny request, in the
    space's form (`probe_for`)."""
    probe = answers(recipe, query=query)
    probe([probe_item])
    if len(probe.runs) != 1 or None in probe.runs:
        raise runs_mod.RunRefused(
            f"{_endpoint(recipe, query)} states no run identity (no "
            f"`{runs_mod.RUN_HEADER}` on its answer). A pass validates a RUN, so there "
            f"is nothing here to validate: searching with this endpoint works and "
            f"nothing is written through it (03 §3.3). Writing needs an encoder that "
            f"states the run of every answer: `api = \"vega\"`, whose receipts "
            f"name it (docs/notes/vega.md)")
    return next(iter(probe.runs))


def stated_run(recipe, *, query: bool = False, probe_item=PROBE) -> emb.StatedRun:
    """The run that answers now AND what it stands for — the platform's own
    description, verified against the id (runs.describe). An encoder declared
    `api = "vega"` has its runs described by VeGa, where its receipts are
    read: `<VEGA_URL>/vega/runs/<run_id>` (docs/notes/vega.md §9)."""
    return describe_run(recipe, answering_run(recipe, query=query, probe_item=probe_item),
                        query=query)


def describe_run(recipe, run_id: str, *, query: bool = False) -> emb.StatedRun:
    """What a run id the endpoint stated stands for, verified against the id:
    VeGa's description for an `api = "vega"` encoder, the platform's otherwise
    (`hold collection create` holds the runs that embedded its reference to
    the recipe's engine with it, docs/notes/v5-migration.md G5)."""
    if getattr(recipe, "api", None) == embeddings.VEGA:
        return runs_mod.describe(_endpoint(recipe, query), run_id, get=httpx.get,
                                 at=f"{config.VEGA_URL}/vega/runs/{run_id}")
    return runs_mod.describe(_endpoint(recipe, query), run_id, get=httpx.get)


# ---- the gate against the run that answers ------------------------------------

def _member(root: Path, name: str, codex: Codex) -> Path:
    return Path(root) / name / "shard" / shard_mod.calibration_paths(
        codex.space.calibration.set)[0]


def calibration_items(root: Path, name: str, codex: Codex) -> list:
    """The space's calibration set as its encoder is fed it (09 §9.12): the
    platform's texts, or a pair space's pairs from its member — refused (exit
    2, a rule in the way) when the member does not carry them."""
    return cal.items(codex, _member(root, name, codex))


def reference(root: Path, name: str, codex: Codex, role: str):
    member = _member(root, name, codex)
    if not member.is_file():
        raise emb.GateError(
            f"{name}: no calibration member at {member.name}. It is written by `hold "
            f"collection create` (09 §9.12); without it there is nothing to compare "
            f"an encoder against")
    if role == "query":
        return cal.read_query_vectors(
            member, expect=codex.space.calibration.query_vectors).tolist()
    return cal.read_member(member, expect_vectors=codex.space.calibration.vectors,
                           expect_envelope=codex.space.envelope)[0].tolist()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pinned(recipe, role: str, kind: str, name: str):
    """The encoder pinned for the length of a gate or a re-check, its state
    before restored after (app/model_pins.py; operator, 2026-10-23)."""
    from . import model_pins
    url, model = _who(recipe, role == "query")
    return model_pins.holding(url, model, f"{kind} {name} {role} (pid {os.getpid()})",
                              collection=name)


#: how long the multi-load gate waits for a model it stopped to answer again
RELOAD_WAIT_S = 600.0


def _reloader(recipe, *, query: bool, stop=None, name: str = ""):
    """THE MULTI-LOAD GATE'S RELOAD (docs/notes/near-exact-runs.md §3): stop the
    model at its runner, then wait — bounded — until it answers again, so the
    gate's next text is the first of a fresh load. Only a VeGa encoder, whose
    runner is LocalAI, is reloaded; for any other the gate measures one load."""
    if getattr(recipe, "api", None) != "vega":
        return None
    import time

    def reload() -> None:
        embeddings.reload_model(recipe, query=query)
        deadline = time.monotonic() + RELOAD_WAIT_S
        while True:
            _stopping(stop, name)
            try:
                embeddings.embed_calibration(recipe, ["load"], query=query, runs=set(),
                                             one_at_a_time=True)
                return
            except Exception as e:                  # noqa: BLE001 — loading, or gone
                if time.monotonic() > deadline:
                    raise emb.GateDeferred(f"{name}: the model did not come back within "
                                           f"{RELOAD_WAIT_S:.0f} s of its reload: "
                                           f"{type(e).__name__}: {e}") from e
                time.sleep(2.0)
    return reload


def validate(root: Path, name: str, codex: Codex, recipe, *, role: str = "document",
             key=None, now: str | None = None,
             stop: Callable[[], bool] | None = None,
             on_run: Callable[[Any], None] | None = None) -> dict[str, Any]:
    with _pinned(recipe, role, "gate", name):
        return _validate(root, name, codex, recipe, role=role, key=key, now=now,
                         stop=stop, on_run=on_run)


def _validate(root: Path, name: str, codex: Codex, recipe, *, role: str = "document",
              key=None, now: str | None = None,
              stop: Callable[[], bool] | None = None,
              on_run: Callable[[Any], None] | None = None) -> dict[str, Any]:
    """The gate against the run that answers now, and its statement KEPT,
    pass or fail. Returns the statement's body.

    Unreachable, stopping, or the run changed during the pass:
    `GateDeferred` — no statement, and it is tried again (03 §3.1)."""
    query = role == "query"
    # FIRST, and before the network: without the member there is nothing to
    # compare against, which is a rule in the way (exit 2) and no deferral —
    # an unreachable encoder must not be what the operator reads instead
    reference_vectors = reference(root, name, codex, role)
    try:
        stated = stated_run(recipe, query=query, probe_item=probe_for(codex, role))
        _stopping(stop, name)
        if key is None:
            from .bundles import keys as node_keys
            key = node_keys.load_signing_key()
        kw = {"query": query, "stop": stop, "name": name}
        # the set as the space's encoder is fed it — a pair space's from its
        # member (09 §9.12, amended 2026-10-08) — read where the gate always
        # read it, so a text space fails here exactly as it did
        items = calibration_items(root, name, codex)
        # the stored CPU reference for these weights, when one was made
        # (docs/notes/near-exact-runs.md §2): every answer is held to it
        from .hold import cpu_reference as cpu_ref
        cpu = cpu_ref.find(root, name, role, weights=stated.weights,
                           set_id=codex.space.calibration.set, count=len(reference_vectors))
        body = runs_mod.validate(
            root, name, codex, role=role,
            reference=reference_vectors,
            embed=answers(recipe, **kw),
            embed_one=answers(recipe, how="one", **kw),
            contribute=answers(recipe, how="contribute", **kw),
            stated=stated, key=key, now=now or _now(), items=items, on_run=on_run,
            cpu_reference=None if cpu is None else (cpu.run_id, cpu.vectors),
            reload=_reloader(recipe, query=query, stop=stop, name=name),
            fresh=fresh_contexts(recipe, stated=stated,
                                 probe_item=probe_for(codex, role), **kw),
            # the Codex's requirement wins over the node's setting: a node may
            # be stricter than its collection, never looser (09 §9.3)
            exactness=(emb.EXACT if runs_mod.requires_exact(codex)
                       else getattr(recipe, "exactness", emb.EXACT)))
        _mark(codex, role, body["run"], body)      # measured now: nothing to re-check
        measured = contributing(recipe)
        if measured is not None:
            runs_mod.record_batching(root, name, role, body["run"], measured)
        return body
    except (runs_mod.RunRefused, emb.GateError):
        raise
    except Exception as e:                              # noqa: BLE001
        raise emb.GateDeferred(
            f"{name}: the encoder was unreachable or failed during the gate: "
            f"{type(e).__name__}: {e}") from e


def signer(key=None) -> str | None:
    """The key a statement must be signed by to count here (runs.statements):
    `key`'s public half when the caller signs with one, else this node's
    own — the key `validate` signs with. None on a peer with no key, which
    holds no statements of its own.

    ONLY A KEY FOUND IS REMEMBERED. Remembering "no key" would outlive the
    key being put in place, and every statement this node then signs would
    be ignored and gated again, for ever."""
    from .hold import envelope as env
    if key is not None:
        return env.key_text(key.verify_key)
    if _me:
        return _me[0]
    try:
        from . import identity
        text = identity.own()       # the web reads the public file only
    except Exception:                                   # noqa: BLE001
        return None
    if text is None:
        return None
    _me.append(text)
    return text


_me: list[str] = []


#: (space, role, run, the statement's digest) re-checked, or freshly gated,
#: since THIS process started. A kept statement is relied on only once it is
#: here (`recheck`) — THAT statement: one written since (a `hold embedder
#: check` from the command line, 2026-10-21) is re-checked in its turn. Keyed
#: on the run alone, the worker never re-checked the new one, and the web
#: withheld the vector leg until the worker restarted (München, live).
_rechecked: set[tuple[str, str, str, str]] = set()


def _mark(codex: Codex, role: str, run_id: str, body: dict[str, Any]) -> None:
    entry = (codex.space_id, role, run_id, _statement_digest(body))
    _rechecked.add(entry)
    if _publishing:
        _publish(entry)


# ---- the worker's re-check record, for the web process (operator, 2026-09-28) ----
#
# The web process never re-checks: a search must not wait for sixteen
# embeddings. It relies on a kept statement only when THE WORKER, in its
# current start, has re-checked that very statement (its body's digest) or
# gated the run itself. The worker empties the record when it starts, so a
# restart puts every statement back behind the worker's re-check.

RECORD_KIND = "louise-rechecked"
_publishing: list[bool] = []
_record_lock = threading.Lock()
_record_read: dict[str, tuple[tuple[int, int], frozenset]] = {}


def record_path() -> Path:
    return collections_mod.louise_root() / "state" / "rechecked.json"


def _statement_digest(body: dict[str, Any]) -> str:
    from .hold.codex import canonical_json, digest
    return digest(canonical_json(body))


def _write_record(checked: list) -> None:
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"kind": RECORD_KIND, "pid": os.getpid(),
                               "started": _started[0], "checked": checked},
                              sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


_started: list[str] = [""]
_entries: list[list[str]] = []


def publish_rechecks() -> None:
    """THE WORKER, at start: begin a record of its own, empty. Only the
    worker publishes — a command-line process re-checks for itself and
    must not replace the record the web process reads."""
    with _record_lock:
        _publishing[:] = [True]
        _started[0] = _now()
        _entries.clear()
        _write_record([])


def _publish(entry: tuple[str, str, str, str]) -> None:
    with _record_lock:
        if list(entry) not in _entries:
            _entries.append(list(entry))
            _write_record(sorted(_entries))


def worker_rechecked(codex: Codex, role: str, run_id: str, body: dict[str, Any]) -> bool:
    """THE WEB PROCESS: has the worker, in its current start, re-checked
    exactly this statement (or gated the run itself)? Read from the record,
    and read again whenever the file changes (keyed on its path, CLAUDE.md).
    No record — no worker has started — is no."""
    path = record_path()
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return False
    cached = _record_read.get(str(path))
    if cached is None or cached[0] != stamp:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            rows = frozenset(tuple(e) for e in doc.get("checked", [])
                             if doc.get("kind") == RECORD_KIND and len(e) == 4)
        except (OSError, ValueError, TypeError):
            rows = frozenset()
        cached = _record_read[str(path)] = (stamp, rows)
    return (codex.space_id, role, run_id, _statement_digest(body)) in cached[1]


def rechecked(codex: Codex, role: str, run_id: str, body: dict[str, Any]) -> bool:
    """Has THIS process re-checked (or gated) exactly this statement?"""
    return (codex.space_id, role, run_id, _statement_digest(body)) in _rechecked


def recheck(root: Path, name: str, codex: Codex, recipe, run_id: str, *,
            role: str = "document", key=None,
            stop: Callable[[], bool] | None = None,
            log: Callable[[str], None] | None = None) -> bool:
    with _pinned(recipe, role, "recheck", name):
        return _recheck(root, name, codex, recipe, run_id, role=role, key=key,
                        stop=stop, log=log)


def _recheck(root: Path, name: str, codex: Codex, recipe, run_id: str, *,
             role: str = "document", key=None,
             stop: Callable[[], bool] | None = None,
             log: Callable[[str], None] | None = None) -> bool:
    """THE FIRST TIME A KEPT STATEMENT IS RELIED ON IN THIS PROCESS: its own
    sixteen sampled texts, embedded again by the run it names, compared with
    the reference (runs.recheck). True: it holds, and is not asked again
    until the process restarts. False: it did not; every statement of this
    peer for that run and space is set aside, and the caller gates the run
    from scratch. An answer from another run, or the encoder away:
    `GateDeferred` — nothing is decided, and it is asked again."""
    body = runs_mod.statements_for_space(root, name, codex, role,
                                         signer=signer(key)).get(run_id)
    if body is None or not runs_mod.passing(body, codex):
        return True            # nothing kept to re-check; `check` decides
    if rechecked(codex, role, run_id, body):
        return True
    reference_vectors = reference(root, name, codex, role)
    embed = answers(recipe, query=role == "query", stop=stop, name=name)
    try:
        ok, passed, median = runs_mod.recheck(codex, body, reference=reference_vectors,
                                              embed=embed, role=role,
                                              items=calibration_items(root, name, codex))
    except (runs_mod.RunRefused, emb.GateError):
        raise
    except Exception as e:                              # noqa: BLE001
        raise emb.GateDeferred(
            f"{name}: the encoder was unreachable or failed during the re-check: "
            f"{type(e).__name__}: {e}") from e
    if embed.runs != {run_id}:
        raise emb.GateDeferred(
            f"{name}: the re-check of run {run_id[:23]}… was answered by "
            f"{sorted(str(r)[:23] for r in embed.runs if r != run_id) or 'none'}; "
            f"it is inconclusive and asked again")
    if ok:
        _mark(codex, role, run_id, body)
        if log is not None:
            log(f"{role} run {run_id[:23]}…: its kept statement re-checked on start "
                f"({passed} of the sample, median {median:.6f})")
        return True
    moved = runs_mod.set_aside(root, name, codex, run_id, role)
    if log is not None:
        log(f"{role} run {run_id[:23]}…: its kept statement did NOT hold on re-check "
            f"({passed} of the sample, median {median:.6f}); set aside "
            f"{[p.name for p in moved]}, and the run is gated from scratch")
    return False


def _foreign(root: Path, name: str, role: str, log: Callable[[str], None] | None) -> None:
    """Report, once per file per process, a statement ignored because another
    key signed it (runs.statements)."""
    for fname in runs_mod.foreign(root, name, role):
        if log is not None and (name, fname) not in _told_foreign:
            _told_foreign.add((name, fname))
            log(f"ignored {fname}: signed by a key that is not this node's; a "
                f"validation counts only when this node made it")


_told_foreign: set[tuple[str, str]] = set()


def _told(body: dict[str, Any]) -> str:
    return (f"run {str(body.get('run'))[:23]}…: {body.get('passed')} of the sample "
            f"passed, median {body.get('median')}, batch-invariant "
            f"{body.get('batch_invariant')}")


def guard(root: Path, name: str, codex: Codex, recipe, answered: set, *,
          role: str = "document", key=None,
          stop: Callable[[], bool] | None = None,
          log: Callable[[str], None] | None = None) -> None:
    """THE COMMAND LINE, before a write: silent when vectors from `answered`
    may be stored. An untested run is gated at once, here, and the write goes
    ahead under it if it passes — nobody has to accept anything (operator,
    2026-09-20). `RunRefused`: exit 2. `RunUnvalidated`: the run was tested
    and is not this space's. `GateDeferred`: exit 3."""
    if not runs_mod.applies(codex):
        return
    together = batched(recipe)
    who = signer(key)
    try:
        runs_mod.check(root, name, codex, answered, signer=who, role=role,
                       batched=together, contributes=contributing(recipe) if together else None)
        _foreign(root, name, role, log)
        stale = [r for r in sorted(r for r in answered if r)
                 if not recheck(root, name, codex, recipe, r, role=role, key=key,
                                stop=stop, log=log)]
        if not stale:
            return
    except runs_mod.RunUnvalidated as e:
        if e.failed:
            raise
        _foreign(root, name, role, log)
        if log is not None:
            log(f"{name}: run {e.run_id[:23]}… is new to this peer; running the gate")
    body = validate(root, name, codex, recipe, role=role, key=key, stop=stop)
    if log is not None:
        log(f"{name}: {_told(body)}")
    runs_mod.check(root, name, codex, answered, signer=who, role=role, batched=together, contributes=contributing(recipe) if together else None)


def checking(root: Path, name: str, codex: Codex, recipe, *, role: str = "document",
             key=None, log: Callable[[str], None] | None = None
             ) -> Callable[[list[str]], list[list[float]]]:
    """An embed function for a CHECK, or for vectors that LEAVE this peer —
    T3, T4, `vectors-sample`, `hold vectors check`, an export's challenge
    (03 §3.3): "a check runs under a validated run. An answer that names
    another makes the check inconclusive, and an inconclusive check yields no
    attestation."

    For a version-3 collection every answer must name a run validated for the
    space. One this peer has not tested is gated at once, as `guard` does; one
    that failed, or an endpoint that states none, makes the check
    INCONCLUSIVE — `GateDeferred`, exit 3 — and never a verdict about the
    collection: an encoder that is not the space's says nothing about vectors
    that are. Older collections embed as they always did."""
    # the texts a check embeds are the collection's chunks (document side, in
    # their layer's form — a pair layer's re-cut from the records with their
    # `fields`, or a pair space's calibration pairs) or its queries (the query
    # side, text)
    if not runs_mod.applies(codex):
        if role == "query":
            return lambda texts: embeddings.embed_calibration(recipe, texts, query=True)
        return lambda texts: embeddings.embed_chunks(codex, texts, recipe=recipe)
    # a check's queries are raw: the client prefixes the instruction, as a search does
    embed = answers(recipe, query=role == "query", collection=codex, asked=False)

    def fn(texts: list[str]) -> list[list[float]]:
        vectors = embed(texts)
        try:
            guard(root, name, codex, recipe, embed.runs, role=role, key=key, log=log)
        except (runs_mod.RunRefused, runs_mod.RunUnvalidated) as e:
            raise emb.GateDeferred(
                f"inconclusive — the check did not run under a validated run: {e}") from e
        return vectors
    return fn


# ---- the worker ------------------------------------------------------------------

def applies(collection) -> bool:
    codex = getattr(collection, "codex", None)
    return codex is not None and runs_mod.applies(codex)


def _root() -> Path:
    return collections_mod.louise_root() / "collections"


def _where(collection) -> str:
    """The cache's key: the collection's PLACE, never its name (CLAUDE.md —
    two objects can share a name and point at different files)."""
    return str(runs_mod.directory(_root(), collection.name))


#: place -> (allowed, reason, fresh until, the run the verdict is about,
#: the kept statements as they were when it was given — `_kept_stamp`)
_verdicts: dict[str, tuple[bool, str, float, str | None, tuple]] = {}


def _kept_stamp(collection) -> tuple:
    """The collection's kept statements as the filesystem has them: (name,
    size, mtime_ns) of each. A statement written since a verdict (the gate
    run from the command line) is a reason to look again at once — to
    re-check it and tell the web — not after PASS_TTL_S. One small
    directory, read per round: a handful of entries, one per run and role."""
    try:
        with os.scandir(_where(collection)) as it:
            return tuple(sorted((e.name, e.stat().st_size, e.stat().st_mtime_ns)
                                for e in it if e.name.endswith(".json")))
    except OSError:
        return ()
#: (endpoint, model) -> the lock of whoever is looking at that model now (`_flight`)
_looking: dict[str, threading.Lock] = {}
_guard = threading.Lock()


#: (endpoint URL, role) -> the run its last answer named. Keyed on the
#: ENDPOINT, never a collection's name: every collection that reaches a URL
#: meets the same run, so a run relaunched under one of them is seen by all at
#: once (03 §3.6: "every collection of that space on the peer goes with it").
_answering: dict[tuple[tuple[str, str], str], str] = {}
#: (endpoint, role, run) whose statement a search has read for its load
#: options (`check_query`, unenforced): once a run in this process
_noted: set[tuple[tuple[str, str], str, str]] = set()


def heard(recipe, answered: set, *, role: str = "document") -> None:
    """Remember which run this endpoint's answers named."""
    named = [r for r in answered if r]
    if named:
        _answering[(_who(recipe, role == "query"), role)] = sorted(named)[-1]


def _note_options(recipe, answered: set, known: dict[str, dict[str, Any]], *,
                  role: str = "document") -> None:
    """The load options of the runs that answered, from their own validated
    statements (`engine.options`), noted for the model that answered: where
    `/system` lists none — the node's LocalAI — they are how a request learns
    that its model creates its context per request and takes a keep
    (model_load.note_run_options; app/rage/keep.py)."""
    from . import model_load
    url, model = _who(recipe, role == "query")
    for run_id in sorted(r for r in answered if r):
        engine = (known.get(run_id) or {}).get("engine")
        if isinstance(engine, dict):
            model_load.note_run_options(url, model, engine.get("options"))


#: how long the worker's beat waits for VeGa's counts, at most (`engine_counts`)
COUNTS_TIMEOUT_S = 2.0


def engine_counts(get=httpx.get) -> dict[tuple[str, str], dict[str, Any]]:
    """(endpoint, model) -> the VeGa wrapper's count for the run that
    endpoint's answers last named, since its load (`GET <VEGA_URL>/vega/keeps`,
    docs/notes/vega.md §2): the engine's own word on the contexts it created,
    for RAGE to set its keeps against (app/rage/keep.py `observe`). {} where
    there is nothing to read — no VEGA_URL, no run heard yet, VeGa away or
    older: a reading is skipped, never an error (CLAUDE.md: deferral is not
    failure), and bounded (`COUNTS_TIMEOUT_S`)."""
    heard_runs = {who: run_id for (who, _role), run_id in list(_answering.items()) if run_id}
    if not config.VEGA_URL or not heard_runs:
        return {}
    try:
        r = get(f"{config.VEGA_URL}/vega/keeps", timeout=COUNTS_TIMEOUT_S)
        r.raise_for_status()
        rows = r.json().get("runs")
    except (httpx.HTTPError, ValueError, AttributeError):
        return {}
    by_run = {row.get("run_id"): row for row in rows or () if isinstance(row, dict)}
    return {who: by_run[run_id] for who, run_id in heard_runs.items() if run_id in by_run}


def answering(collection) -> dict[str, str | None]:
    """role -> the run this collection's encoder last stated, as far as THIS
    process has heard. Empty for a collection older than version 3."""
    if not applies(collection):
        return {}
    try:
        recipe = collections_mod.recipe_for(collection)
    except Exception:                                   # noqa: BLE001
        return {}
    return {role: _answering.get((_who(recipe, role == "query"), role))
            for role in runs_mod.ROLES}


def _flight(collection) -> str:
    """What ONE gate at a time means: one per encoder MODEL (its endpoint and
    the model the request names), whatever space asks. Two collections of a
    space that reach one encoder meet the same run, and the second finds the
    first's statement instead of gating it again.

    NOT ONE PER SPACE (the node, 2026-11-11): wikidata and normattiva-nemo,
    two spaces on one Nemotron, gated it at once after an engine switch; each
    fresh-context pass saw the other's requests (61 and 66 calls answered for
    32 asked), both deferred, and both verdicts expired together to gate side
    by side again. The gate's passes — fresh loads, fresh contexts — measure
    the model alone, so a second space on it waits for the round."""
    try:
        url, model = _who(collections_mod.recipe_for(collection), False)
    except Exception:                                   # noqa: BLE001
        url, model = _where(collection), ""
    return f"{url} {model}"


# ---- a context that did not reproduce its canary: the run goes back to the gate ----
#
# Operator, 2026-11-11: "Wire the canary failure to the gate before deployment"
# (docs/notes/idle-context-brief.md §10 "The canary"). An engine loaded with
# `context_on_demand` holds every context it creates to the output its load's
# context gave for a fixed input, and discards one that does not reproduce it.
# The request is deferred; but the failure is the engine's own word that it
# does not reproduce itself on a new context, which is what a run FAILS for
# (invariant 12). So the run stops being relied on at once, and its kept
# statement is set aside as a failed re-check's is (`runs.set_aside`): the
# gate runs against it from scratch — its 300 counted fresh contexts among
# the rest — and the run contributes again only if it passes.

#: (endpoint, model) -> the times its engine was seen, in this process, to
#: discard a context that did not reproduce its canary: by the request that
#: was refused (embeddings._endpoint_error) and, for every other caller's, by
#: the worker's beat from the engine's own count (worker._read_engine_counts).
#: One failure may be seen by both: a count, compared and never summed
_canary: dict[tuple[str, str], int] = {}
#: (the collection's place, role) -> `_canary`'s count when its run was last
#: gated from scratch: failures seen since are what makes it suspect
_canary_gated: dict[tuple[str, str], int] = {}
#: (place, role) -> `_canary`'s count the run's history was last written for:
#: a gate that is deferred and asked again is the same episode, written once
_canary_recorded: dict[tuple[str, str], int] = {}
_canary_lock = threading.Lock()


def canary_failed(url: str, model: str | None) -> None:
    """The engine that answers as `model` at `url` discarded a context that
    did not reproduce its canary. A count and nothing else: whoever reads it
    (`check_write`, `embed_allowed`) acts. Safe from any thread and process;
    only the worker gates."""
    with _canary_lock:
        _canary[(url, model or "")] = _canary.get((url, model or ""), 0) + 1


def _suspect(collection, role: str = "document") -> bool:
    """Whether the run that answers this collection's `role` side had a canary
    failure it has not been gated from scratch for."""
    if not _canary:                                     # the common case: one lookup
        return False
    try:
        who = _who(collections_mod.recipe_for(collection), role == "query")
    except Exception:                                   # noqa: BLE001
        return False
    return _canary.get(who, 0) > _canary_gated.get((_where(collection), role), 0)


def _send_back(collection, run_id: str, role: str,
               log: Callable[[str], None] | None) -> None:
    """Set aside what this peer keeps for the suspect run in this space, and
    forget that it was ever re-checked: the caller gates it from scratch."""
    codex = collection.codex
    moved = runs_mod.set_aside(_root(), collection.name, codex, run_id, role)
    for entry in [e for e in _rechecked if e[:3] == (codex.space_id, role, run_id)]:
        _rechecked.discard(entry)
    # THE RUN'S HISTORY, kept across restarts and across a gate that passes
    # again (runs.record_canary): a second failure of the same run is a repeat
    key = (_where(collection), role)
    try:
        seen = _canary.get(_who(collections_mod.recipe_for(collection), role == "query"), 0)
    except Exception:                                   # noqa: BLE001
        seen = 0
    if seen > _canary_recorded.get(key, 0):
        history = runs_mod.record_canary(_root(), collection.name, role, run_id, _now())
        _canary_recorded[key] = seen
    else:
        history = runs_mod.canary_history(_root(), collection.name, role, run_id)
    if log is not None:
        repeat = "" if len(history) < 2 else (
            f" — A REPEAT: time {len(history)} for this run (before: "
            f"{', '.join(history[:-1][-3:])})")
        log(f"CANARY — a context of {role} run {run_id[:23]}… did not reproduce its canary"
            f"{repeat}: the engine does not reproduce itself on a new context (invariant "
            f"12). Its kept statement is set aside ({[p.name for p in moved] or 'none was kept'}) "
            f"and the run is gated from scratch before anything more is written by it")


def _canary_note(collection, run_id: str, role: str = "document") -> str:
    """What a verdict about a run says of its canary history, '' for a run
    that has none: a pass does not erase it."""
    history = runs_mod.canary_history(_root(), collection.name, role, run_id)
    if not history:
        return ""
    return (f" — gated again after {len(history)} canary failure(s) of this run, the last "
            f"{history[-1]}")


def _gated_again(collection, role: str = "document") -> None:
    """The gate has run against the suspect run and its statement — pass or
    fail — is kept: what was seen until now is answered for."""
    try:
        who = _who(collections_mod.recipe_for(collection), role == "query")
    except Exception:                                   # noqa: BLE001
        return
    with _canary_lock:
        _canary_gated[(_where(collection), role)] = _canary.get(who, 0)


def reset_cache() -> None:
    _verdicts.clear()
    _answering.clear()
    _canary.clear()
    _canary_gated.clear()
    _canary_recorded.clear()
    _noted.clear()
    _rechecked.clear()
    _told_foreign.clear()
    _me.clear()
    _publishing.clear()
    _entries.clear()
    _record_read.clear()


def check_write(collection, recipe, answered: set) -> None:
    """THE WORKER, before a write (`embeddings._rows_for`): may vectors from
    these answers be stored? A lookup; the gate never runs here.

    It raises `RunUnvalidated` or `RunRefused`. Both DEFER the document — it
    is untouched, keeps no error, and stays selectable — because neither is
    the document's fault (`worker.transient_failure`). And the verdict is
    forgotten, so the stage's next `embed_allowed` asks the endpoint again
    and gates the new run at once, or pauses with the reason."""
    if not applies(collection):
        return
    heard(recipe, answered)
    codex = collection.codex
    try:
        if _suspect(collection):
            # a context of this run did not reproduce its canary: its kept
            # statement is not relied on until the stage has gated it again
            run_id = sorted(r for r in answered if r)[-1] if any(answered) else ""
            raise runs_mod.RunUnvalidated(
                f"{collection.name}: a context of the run that answers did not reproduce its "
                f"canary; nothing is written until the run has been gated again", run_id)
        runs_mod.check(_root(), collection.name, codex, answered, signer=signer(),
                       role="document", batched=batched(recipe), contributes=contributing(recipe))
        # A KEPT STATEMENT NOT YET RE-CHECKED IN THIS PROCESS is not relied
        # on: a deferral, and the stage's next `embed_allowed` re-checks it
        # this collection's own statements first — one cached listing; the
        # space's other collections only for a run it holds none for (a
        # borrowed statement is copied in, so that happens once)
        kept = runs_mod.statements(_root(), collection.name, codex, "document", signer=signer())
        if any(r and r not in kept for r in answered):
            kept = runs_mod.statements_for_space(_root(), collection.name, codex, "document",
                                                 signer=signer())
        for run_id in sorted(r for r in answered if r):
            if run_id not in kept or not rechecked(codex, "document", run_id, kept[run_id]):
                raise runs_mod.RunUnvalidated(
                    f"{collection.name}: run {run_id[:23]}… has a kept statement not "
                    f"yet re-checked since this process started; nothing is written "
                    f"until it is", run_id)
        _note_options(recipe, answered, kept)
    except (runs_mod.RunUnvalidated, runs_mod.RunRefused):
        _verdicts.pop(_where(collection), None)
        raise


def forget(collection) -> None:
    """An answer refused before its run could be checked — VeGa's receipt
    did not fit it (`embeddings.ReceiptRefused`): the verdict is forgotten as
    `check_write` forgets one, so the stage's next `embed_allowed` asks the
    endpoint again, and pauses with the refusal as its reason if it refuses
    again."""
    if applies(collection):
        _verdicts.pop(_where(collection), None)


def check_query(collection, recipe, answered: set) -> None:
    """A SEARCH, before the query vector is used (03 §3.3, searching): "a run
    not yet tested answers no query … an answer that names such a run is
    discarded"; a run that FAILED withholds the vector leg, and the text leg
    goes on. It raises `RunUnvalidated`; `retrieval.search` degrades to the
    text leg and says so.

    An endpoint that states no run is searched with (03 §3.3). And while the
    node only REPORTS quarantine (`copy_state.enforced`), this remembers who
    answered and withholds nothing."""
    if not applies(collection):
        return
    codex = collection.codex
    role = "query" if codex.space.query.encoder is not None else "document"
    heard(recipe, answered, role=role)
    from . import copy_state
    if not copy_state.enforced():
        # nothing withheld; a run's load options are still read from its
        # statement, ONCE a run in this process (the search's own keep)
        fresh = {r for r in answered if r and (_who(recipe, role == "query"), role, r) not in _noted}
        if fresh:
            try:
                known = copy_state.space_statements(_root(), collection.name, codex, role)
            except Exception:                           # noqa: BLE001 — telemetry-grade: a keep, not a verdict
                return
            _noted.update((_who(recipe, role == "query"), role, r) for r in fresh if r in known)
            _note_options(recipe, fresh, known, role=role)
        return
    known = copy_state.space_statements(_root(), collection.name, codex, role)   # this node's key only
    for run_id in sorted(r for r in answered if r):
        body = known.get(run_id)
        if body is None:
            raise runs_mod.RunUnvalidated(
                f"{collection.name}: the {role} encoder answered from run "
                f"{run_id[:23]}…, which this peer has not tested: the answer is "
                f"discarded and the vector leg waits for the gate, which runs at once "
                f"(03 §3.3)", run_id)
        if not runs_mod.passing(body, codex):
            raise runs_mod.RunUnvalidated(
                f"{collection.name}: the {role} encoder's run {run_id[:23]}… FAILED the "
                f"gate for this space: the vector leg is withheld and the text leg goes "
                f"on (03 §3.3)", run_id, failed=True)
        if not (rechecked(codex, role, run_id, body)
                or worker_rechecked(codex, role, run_id, body)):
            raise runs_mod.RunUnvalidated(
                f"{collection.name}: the {role} encoder's run {run_id[:23]}… has a kept "
                f"statement the worker has not re-checked since it started: the vector "
                f"leg waits for that re-check, and the text leg goes on", run_id)
    _note_options(recipe, answered, known, role=role)


def _look(collection, stop: Callable[[], bool] | None,
          log: Callable[[str], None]) -> tuple[bool, str, str | None]:
    """Ask the endpoint which run answers, and settle it: (may it embed, why,
    the run that answered). Never raises."""
    name, codex = collection.name, collection.codex
    run_id = None
    try:
        recipe = collections_mod.recipe_for(collection)
        run_id = answering_run(recipe, probe_item=probe_for(codex))
        heard(recipe, {run_id})
        who = signer()
        suspect = _suspect(collection)
        if suspect:
            _send_back(collection, run_id, "document", log)
        try:
            runs_mod.check(_root(), name, codex, {run_id}, signer=who,
                           batched=batched(recipe), contributes=contributing(recipe))
            _foreign(_root(), name, "document", log)
            if not recheck(_root(), name, codex, recipe, run_id, stop=stop, log=log):
                log(_told(validate(_root(), name, codex, recipe, stop=stop)))
                runs_mod.check(_root(), name, codex, {run_id}, signer=who,
                               batched=batched(recipe), contributes=contributing(recipe))
        except runs_mod.RunUnvalidated as e:
            if e.failed:
                if suspect:
                    _gated_again(collection)    # it was gated, and failed: kept so
                raise
            _foreign(_root(), name, "document", log)
            log(f"run {run_id[:23]}… is {'gated again' if suspect else 'new to this peer'}; "
                f"running the gate against it")
            told = _told(validate(_root(), name, codex, recipe, stop=stop))
            if suspect:
                _gated_again(collection)        # pass or fail, its statement is kept
            log(told)
            runs_mod.check(_root(), name, codex, {run_id}, signer=who,
                           batched=batched(recipe), contributes=contributing(recipe))
        return True, (f"run {run_id[:23]}… is validated for this space"
                      + _canary_note(collection, run_id)), run_id
    except (runs_mod.RunRefused, runs_mod.RunUnvalidated, emb.GateError) as e:
        return False, str(e), run_id
    except Exception as e:                              # noqa: BLE001
        # the endpoint away: a deferral, retried; nothing is flagged
        return False, f"the encoder could not be asked: {type(e).__name__}: {e}", run_id


def _look_at_the_query_side(collection, stop: Callable[[], bool] | None,
                            log: Callable[[str], None]) -> None:
    """AN ASYMMETRIC SPACE'S QUERY ENCODER IS A RUN OF ITS OWN (03 §3.3),
    gated "at once, and by itself" like the document one: until it is tested
    the space's collections answer no query (§3.6). Nobody embeds a document
    with it, so nothing here decides whether embedding may go on — it only
    sees to it that the statement exists. Never raises."""
    name, codex = collection.name, collection.codex
    if codex.space.query.encoder is None:
        return
    try:
        recipe = collections_mod.recipe_for(collection)
        run_id = answering_run(recipe, query=True)
        heard(recipe, {run_id}, role="query")
        suspect = _suspect(collection, "query")
        if suspect:
            _send_back(collection, run_id, "query", log)
        if run_id in runs_mod.statements_for_space(_root(), name, codex, "query",
                                                   signer=signer()):
            if recheck(_root(), name, codex, recipe, run_id, role="query", stop=stop,
                       log=log):
                return
        log(f"query run {run_id[:23]}… is {'gated again' if suspect else 'new to this peer'}; "
            f"running the gate against it")
        told = _told(validate(_root(), name, codex, recipe, role="query", stop=stop))
        if suspect:
            _gated_again(collection, "query")
        log("query " + told)
    except Exception as e:                              # noqa: BLE001
        log(f"the query encoder's run could not be settled: {type(e).__name__}: {e}")


def embed_allowed(con, collection, *, stop: Callable[[], bool] | None = None) -> bool:
    """THE WORKER, before the embed stage of a version-3 collection. Cached,
    logs its transitions where the backend gate logs its own, never raises."""
    from . import db
    from .verify import backend_gate

    where = _where(collection)
    now = time.time()
    cached = _verdicts.get(where)
    # A VERDICT IS ABOUT A RUN. When any collection that reaches this encoder
    # has since heard another run from it, the verdict is about a run that no
    # longer answers, however fresh it is: look again — for every collection
    # of the encoder, not only the one that noticed.
    now_answering = answering(collection).get("document")
    stamp = _kept_stamp(collection)
    # ...and a verdict given before a context of the run failed its canary is
    # about a run that has to be gated again (`_suspect`)
    if cached is not None and cached[2] > now and cached[4] == stamp and (
            now_answering is None or cached[3] is None or cached[3] == now_answering) \
            and not _suspect(collection) and not _suspect(collection, "query"):
        return cached[0]
    with _guard:
        lock = _looking.setdefault(_flight(collection), threading.Lock())
    if not lock.acquire(blocking=False):
        # someone is asking (or gating) already: ONE gate per model (`_flight`).
        # Nothing to wait for — this round has no embedding, the next asks
        return False
    try:
        def log(message: str) -> None:
            # a context that did not reproduce its canary is an error to read
            db.log_event(con, "error" if message.startswith("CANARY") else "info", "run-check",
                         f"[{collection.name}] {message}"[:500])

        allowed, reason, run_id = _look(collection, stop, log)
        _look_at_the_query_side(collection, stop, log)
        # stamped AFTER looking: a statement the look itself wrote (a gate)
        # is not a reason to look again
        _verdicts[where] = (allowed, reason,
                            now + (PASS_TTL_S if allowed else FAIL_TTL_S), run_id,
                            _kept_stamp(collection))
        if cached is None or cached[0] != allowed or cached[1] != reason:
            db.log_event(con, "info" if allowed else "error", "run-check",
                         (f"[{collection.name}] embedding "
                          f"{'enabled' if allowed else 'PAUSED'}: {reason}")[:500])
            try:
                db.set_control(con, backend_gate.GATE_STATE_KEY, json.dumps(
                    {"allowed": allowed, "reason": reason[:400], "ts": int(now)}))
            except Exception:                           # noqa: BLE001
                pass        # a status write must never break the gate
        return allowed
    finally:
        lock.release()
