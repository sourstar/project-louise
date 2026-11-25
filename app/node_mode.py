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

THREE WORDS, kept apart (07 §7.3):
- this MODE (embed | chat): what the node is for now;
- the RUN MODE (`worker_state`: running, acquire-only, seed-only, paused,
  app/node_state.py): what the worker runs. A switch SETS it, once, and it
  stays the operator's to change after (a pause for a deploy), except that
  chat mode never runs everything (`refuses`) and `node resume` goes back to
  what the mode runs (`run_of`);
- a collection's `embed` setting (off, on-demand, all, p2p): untouched.

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

EMBED, CHAT = "embed", "chat"
MODES = (EMBED, CHAT)
#: a node never switched: its one chat model answers and everything runs, as
#: before there were two modes
DEFAULT = EMBED

KEY = "node_mode"
HISTORY_KEY = "node_mode_history"
HISTORY_KEEP = 50

#: the pages' words for the two, in the order they show them
LABELS = ((EMBED, "embed mode"), (CHAT, "chat mode"))
SAYS = {
    EMBED: "heavy embedding: the memory is the embedders', the small chat model "
           "answers, and everything runs",
    CHAT: "chat: the memory is the large chat model's, the embedders page in "
          "for a question, and nothing is parsed, indexed or embedded",
}

#: how long a reader that asks often (every `config.CHAT_MODEL`) keeps what
#: it read: a switch made by another process is seen within this
SEEN_FOR_S = 1.0
_seen: tuple[str, float, str] | None = None      # (store path, monotonic, mode)


def _read(con: sqlite3.Connection) -> str:
    mode = db.get_control(con, KEY, DEFAULT)
    return mode if mode in MODES else DEFAULT


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


def run_of(mode: str, modes: platform_config.ModesConfig | None = None) -> str:
    """The run mode a switch to `mode` sets, and `node resume` goes back to:
    everything in embed mode ("In embed mode switch to run all"), `[modes]
    chat_run` in chat mode."""
    if mode != CHAT:
        return "running"
    return (modes if modes is not None else platform_config.seen().modes).chat_run


def refuses(state: str, mode: str | None = None) -> str | None:
    """Why the node's mode does not take this run mode, in words; None where
    it does. Chat mode's run modes are three (`platform_config.CHAT_RUNS`):
    running everything is embed mode's, since it starts the embedders under
    the large chat model."""
    if (current() if mode is None else mode) == CHAT and state == "running":
        return ("the node is in chat mode, where nothing is parsed, indexed or embedded "
                "(the memory is the large chat model's): switch to embed mode to run "
                "everything — `louise node mode embed`, the System page, or "
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
    if mode not in MODES:
        raise ValueError(f"mode is {' or '.join(MODES)}, got {mode!r}")
    run = run_of(mode)
    at = time.time()
    with closing(node_state.connect()) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            before, run_before = _read(c), db.worker_state(c)
            db.set_control(c, KEY, mode)
            db.set_worker_state(c, run)
            _noted(c, HISTORY_KEY, {"at": at, "mode": mode, "run": run, "by": by or "unnamed"},
                   HISTORY_KEEP)
            _noted(c, node_state.MODE_HISTORY_KEY,
                   {"at": at, "state": run, "by": f"{mode} mode, set by {by or 'unnamed'}"},
                   node_state.MODE_HISTORY_KEEP)
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
    forget()
    return {"mode": mode, "before": before, "run": run, "run_before": run_before}


def set_chat_run(run: str, *, by: str = "") -> bool:
    """Chat mode's run mode changed in the file: where the node IS in chat
    mode it takes it at once (the page's select would otherwise wait for the
    next switch). True when the node's run mode was written."""
    if run not in platform_config.CHAT_RUNS:
        raise ValueError(f"chat mode's run mode is one of {', '.join(platform_config.CHAT_RUNS)}, got {run!r}")
    with closing(node_state.connect()) as c:
        if _read(c) != CHAT or db.worker_state(c) == run:
            return False
    node_state.set_worker_state(run, by=f"chat mode's run mode, set by {by or 'unnamed'}")
    return True


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
        "modes": {m: {"chat_model": config.chat_model_for(m), "run": run_of(m, pc.modes),
                      "budget": {k: v for k, v in budget(m, pc).items() if k in ("down", "up")}}
                  for m in MODES},
    }
