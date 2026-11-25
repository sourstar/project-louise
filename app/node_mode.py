"""The node's MODE OF USE: EMBED or CHAT (operator, 2026-11-25).

  "1. embed mode, intended for heavy embedding (system RAM mostly consumed by
   embedders) in which the smaller chat model is used  2. chat mode in which
   the 35B model is used (system RAM mostly consumed by chat model and
   embedders paging).  3. In embed mode switch to run all  4. In system tab
   shall be selectable for chat mode a run mode among download/seeding,
   seeding or pause.  5. For both modes a network budged shall be settable
   6. The current chat model setting becomes 2 chat settings, large one and a
   small one  7. The mode shall be selectable through the api as well for
   cron jobs"

One memory does not hold the large chat model and every embedder at work, so
the node is used for one or the other, and ONE switch moves the three things
that differ:

  the chat model       embed: `[models] chat_small`    chat: `[models] chat_large`
  the run mode         embed: running ("run all")      chat: `[modes] chat_run`
  the network budget   `[modes] <mode>_down_max` / `<mode>_up_max`, each mode's own

And a fourth, embed mode's own (the same day: "In embed mode you can also
choose local/p2p embedding … Network and embedding mode remain personal
choices"): `[modes] embed_embedding` — who embeds the backlog of the
collections that embed one, each as its file says, every part here, or the
parts the network gives this node (`collections.desired_embed_mode`, applied
by the worker's round beside the mode, `worker._apply_embedding_choice`).

THREE WORDS, kept apart (07 §7.3):
- this MODE (embed | chat): what the node is for now;
- the RUN MODE (`worker_state`: running, acquire-only, seed-only, paused,
  app/node_state.py): what the worker runs. A switch SETS it, once, and it
  stays the operator's to change after (a pause for a deploy), except that
  chat mode never runs everything (`refuses`) and `node resume` goes back to
  what the mode runs (`run_of`);
- a collection's `embed` setting (off, on-demand, all, p2p): its file's,
  untouched — but that in embed mode the node's embedding choice may stand in
  `all` / `p2p` for the collections whose file says one of the two.

WHERE IT LIVES. Which mode the node is in is the node's state, in
`state/node.sqlite` beside the run mode, and it survives a restart for the
run mode's own reason (invariant 10's exception, 07 §7.8): no file sets it,
and a node that came back from a deploy in embed mode would start every
encoder under the large chat model. What each mode MEANS (the two models,
chat mode's run mode, the budgets) is desired state, in platform.toml.

The switch is an EVENT: `switch()` writes the mode and the run mode in one
transaction and records who asked. The worker's round sees the change,
applies the budget, unloads the chat model the node no longer uses and loads
the one it does (`app/worker.py` `_apply_node_mode`). A node that was never
switched is in embed mode with its run mode as it stood: nothing changes
until someone switches.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing

from . import db, node_state, platform_config

EMBED, QUERY, CUSTOM = "embed", "query", "custom"
#: THREE MODES (operator, 2026-11-25, the same day's second word: "Add a 3rd
#: mode custom where you can freely select the chat model and what is
#: currently available in the run mode, basically what we have now plus the
#: selection of the chat model … In general, query keeps the system unloaded
#: to optimize the query related model load, and embed is optimized to give to
#: the embedders the maximum room on the card"):
#:   embed   processing on, the small chat model;
#:   query   processing off, the large chat model (he says "query"; the
#:           morning's word for it was "chat", still taken everywhere);
#:   custom  the node as it was before there were modes: the run mode is the
#:           operator's, any of the four, and the chat model is `[models] chat`.
MODES = (EMBED, QUERY, CUSTOM)
#: the morning's name of query mode: code that says CHAT means QUERY, and the
#: command, the API and a store written before the rename still say "chat"
CHAT = QUERY
ALIASES = {"chat": QUERY}
#: a node never switched is in CUSTOM: exactly the node before modes — its
#: one chat model, its run mode as it stands, nothing set by a mode
DEFAULT = CUSTOM


def named(mode: object) -> str | None:
    """The mode a word names — one of MODES, or an alias of one — or None."""
    word = str(mode or "").strip().lower()
    word = ALIASES.get(word, word)
    return word if word in MODES else None

KEY = "node_mode"
HISTORY_KEY = "node_mode_history"
HISTORY_KEEP = 50

#: the pages' words for the three, in the order they show them
LABELS = ((EMBED, "embed mode"), (QUERY, "query mode"), (CUSTOM, "custom mode"))
SAYS = {
    EMBED: "heavy embedding: the card is the embedders', the small chat model "
           "answers, and everything is parsed, indexed and embedded",
    QUERY: "questions: the system is kept unloaded for a question's models, the "
           "large chat model answers, and nothing is parsed, indexed or embedded",
    CUSTOM: "your own: the run mode you set and the chat model you choose "
            "([models] chat) — the node as it was before there were modes",
}

#: how long a reader that asks often (every `config.CHAT_MODEL`) keeps what
#: it read: a switch made by another process is seen within this
SEEN_FOR_S = 1.0
_seen: tuple[str, float, str] | None = None      # (store path, monotonic, mode)


def _read(con: sqlite3.Connection) -> str:
    return named(db.get_control(con, KEY, DEFAULT)) or DEFAULT


def forget() -> None:
    """Drop what `current()` remembers: the next read asks the store."""
    global _seen
    _seen = None


def current(con: sqlite3.Connection | None = None) -> str:
    """THE mode the node is in. `con` is a `node_state.connect()` a
    long-lived thread holds (the worker's round reads through it, never
    stale). Without one the store is read READ-ONLY, creating nothing
    (`node_state.peek`), and the answer kept for `SEEN_FOR_S`; a store that
    is absent or cannot be read is a node never switched."""
    global _seen
    if con is not None:
        return _read(con)
    try:
        where = str(node_state.path())
    except Exception:   # noqa: BLE001 — no root named: a node never switched
        return DEFAULT
    now = time.monotonic()
    got = _seen
    if got is not None and got[0] == where and now - got[1] < SEEN_FOR_S:
        return got[2]
    try:
        mode = node_state.peek(_read, DEFAULT, error=DEFAULT)
    except Exception:   # noqa: BLE001 — a chat is never failed by a mode it cannot read
        mode = DEFAULT
    _seen = (where, now, mode)
    return mode


def run_of(mode: str, modes: platform_config.ModesConfig | None = None) -> str | None:
    """The run mode a switch to `mode` sets, and `node resume` goes back to:
    everything in embed mode ("In embed mode switch to run all"), `[modes]
    chat_run` in query mode, and NONE in custom mode — there the run mode is
    the operator's own and a switch does not touch it."""
    mode = named(mode) or DEFAULT
    if mode == EMBED:
        return "running"
    if mode == QUERY:
        return QUERY_RUN
    return None


#: QUERY MODE'S RUN MODE, always: nothing is parsed, indexed or embedded, and
#: it is a run mode in which the keeper PRELOADS the question's models
#: (resident.PRELOADING_MODES). The morning's cut let query mode be switched
#: to seed-only or paused for its network choice — two run modes in which
#: nothing is preloaded for Ask, the opposite of what query mode is for. What
#: the network does is the mode's NETWORK CHOICE now (`network`), not its run mode
QUERY_RUN = "acquire-only"


def network(mode: str | None = None) -> str | None:
    """The NETWORK CHOICE in force — `mode`'s, or the node's own mode's:
    download-seed, seed or none (`platform_config.NETWORKS`), each of embed
    and query mode having its own (operator, 2026-11-25: "set for each
    embed/query mode, a cron job then simply switches that") — or None in
    custom mode, where the run mode says what the network does, as before.
    `node_state.runs` reads it for every gate that fetches or seeds."""
    which = current() if mode is None else (named(mode) or DEFAULT)
    return platform_config.seen().modes.network(which)


def refuses(state: str, mode: str | None = None) -> str | None:
    """Why the node's mode does not take this run mode, in words; None where
    it does. Chat mode's run modes are three (`platform_config.CHAT_RUNS`):
    running everything is embed mode's, since it starts the embedders under
    the large chat model."""
    if (current() if mode is None else named(mode)) == QUERY and state == "running":
        return ("the node is in query mode, where nothing is parsed, indexed or embedded "
                "(the system is kept unloaded for a question's models): switch to embed "
                "mode to run everything, or to custom mode to set the run mode yourself — "
                "`louise node mode embed|custom`, the System page, or "
                "POST /api/mode {\"mode\": \"embed\"}")
    return None


def _noted(c: sqlite3.Connection, key: str, entry: dict, keep: int) -> None:
    try:
        hist = json.loads(db.get_control(c, key) or "[]")
    except ValueError:
        hist = []
    db.set_control(c, key, json.dumps((hist + [entry])[-keep:]))


def switch(mode: str, *, by: str = "") -> dict:
    """The node switched to `mode`: the mode and the run mode it sets,
    written in ONE transaction (a reader never sees chat mode running
    everything), each write recorded with `by` — the command, or the route
    and its caller. Switching to the mode the node is in sets its run mode
    again: a cron job's call means the same thing every time it runs.
    Returns {"mode", "before", "run", "run_before"}."""
    asked = mode
    mode = named(mode)
    if mode is None:
        raise ValueError(f"mode is {', '.join(MODES[:-1])} or {MODES[-1]}, got {asked!r}")
    run = run_of(mode)
    at = time.time()
    with closing(node_state.connect()) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            before, run_before = _read(c), db.worker_state(c)
            if mode == before and db.get_control(c, KEY) is not None:
                # THE MODE THE NODE IS ALREADY IN: nothing is written. A cron
                # job that says "embed" every night must not un-pause a node
                # someone paused by hand (operator, 2026-11-25: the run and
                # network choices are personal, a switch is for the mode);
                # `louise node resume` is what goes back to the mode's own
                c.execute("ROLLBACK")
                return {"mode": mode, "before": before, "run": run_before, "run_before": run_before,
                        "changed": False}
            db.set_control(c, KEY, mode)
            if run is not None:
                db.set_worker_state(c, run)
            _noted(c, HISTORY_KEY, {"at": at, "mode": mode, "run": run or run_before, "by": by or "unnamed"},
                   HISTORY_KEEP)
            if run is not None:
                _noted(c, node_state.MODE_HISTORY_KEY,
                       {"at": at, "state": run, "by": f"{mode} mode, set by {by or 'unnamed'}"},
                       node_state.MODE_HISTORY_KEEP)
            c.execute("COMMIT")
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
    forget()
    return {"mode": mode, "before": before, "run": run or run_before, "run_before": run_before,
            "changed": True}


def history() -> list[dict]:
    """The recorded switches, oldest first; [] when none."""
    try:
        with closing(node_state.connect()) as c:
            return json.loads(db.get_control(c, HISTORY_KEY) or "[]")
    except (ValueError, sqlite3.Error):
        return []


def budget(mode: str, platform: platform_config.PlatformConfig | None = None) -> dict:
    """The network budget in force in `mode`, bytes a second, None =
    unlimited: the mode's own where `[modes]` sets one, `[budget]`'s
    otherwise, and which of the two each is. Before the schedule's factor
    and any override, which the transfer manager applies on top."""
    pc = platform if platform is not None else platform_config.seen()
    down, up = pc.modes.budget(mode)
    return {"down": down if down is not None else pc.bandwidth.down_max_bps,
            "up": up if up is not None else pc.bandwidth.up_max_bps,
            "down_own": down is not None, "up_own": up is not None}


def apply_budget(manager, con: sqlite3.Connection | None = None) -> str:
    """The budget of the mode the node is in, set on a transfer manager
    (`set_mode_budget`): the worker's each round, and every other process's
    that moves bytes — the web, an operator pass — through
    `transfer.manager.get_manager`. Returns the mode."""
    mode = current(con)
    down, up = platform_config.seen().modes.budget(mode)
    manager.set_mode_budget(down, up)
    return mode


#: the mode whose chat model the worker last made the node's (`applied`)
APPLIED_KEY = "node_mode_applied"


def applied(con: sqlite3.Connection) -> str | None:
    """The mode the worker last acted on, None before it ever did: kept in
    the store, so a switch made while the worker was down — a cron job's,
    during a deploy — still changes the chat model when it comes back."""
    return named(db.get_control(con, APPLIED_KEY))


def set_applied(con: sqlite3.Connection, mode: str) -> None:
    db.set_control(con, APPLIED_KEY, mode)


def shown() -> dict:
    """What the node says of its mode, for `GET /api/mode`, `louise node
    mode` and the status answer: the mode, the run mode, the chat model in
    force and each mode's settings. Reads only."""
    from . import config
    mode = current()
    pc = platform_config.seen()
    return {
        "mode": mode,
        "run": node_state.peek_worker_state(),
        "chat_model": config.chat_model_for(mode),
        "network": pc.modes.network(mode),
        # embed mode's embedding choice where it is in force, None elsewhere
        "embedding": pc.modes.embedding(mode),
        "modes": {m: {"chat_model": config.chat_model_for(m), "run": run_of(m, pc.modes) or "yours",
                      "network": pc.modes.network(m),
                      **({"embedding": pc.modes.embedding(m)} if m == EMBED else {}),
                      "budget": {k: v for k, v in budget(m, pc).items() if k in ("down", "up")}}
                  for m in MODES},
    }
