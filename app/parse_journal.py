"""THE DOCUMENT A PROCESS IS PARSING, kept where its death cannot erase it.

A document on which a parse never ends holds whatever process parses it. In
the parse pool that is a child, which the watchdog ends, and the document is
then parsed alone in a process of its own and answered as an error where it
does not come back (app/parallel.py `_isolated`). In the WORKER'S OWN process
— a batch of one light row, the pool off, the embed path's own chunking —
there is no child to end: the dead-man's switch ends the worker
(app/deadman.py), its restart policy starts it again, and it would then select
the same document and be held again, every ten minutes, for ever.

So the worker writes down what it is about to parse in its own process, and
reads at its start what its predecessor was parsing when it ended:

- THE JOURNAL (`<root>/state/parsing.journal`): one fixed slot per parse in
  progress, written in place before a document's parse and blanked after
  it (`parsing`), then free for the next — the look-ahead starts a thread
  per link, so a slot per THREAD would run out in minutes. One `pwrite`
  each way and no fsync: the bytes are the
  kernel's the moment the call returns, so they survive the PROCESS, which
  is all that is asked; a machine that dies loses them and nothing with
  them.
- THE SUSPECTS (`<root>/state/parse-suspects.json`): at a start, every slot a
  dead process left filled is a document that was in progress when it ended
  — by the switch, by a kill, by a crash. That is a reason to look, not a
  proof: a container killed at a deploy's stop also leaves one. Each gets a
  STRIKE, kept in a file of its own so a second restart does not forget it.
  * Where the document can be parsed elsewhere, it is: `parallel.compute`
    sends a suspect to a process of its own, alone, and what comes back
    decides — its answer, or an error on it. No blame without that.
  * Where it cannot (the embed path chunks in its own thread), it is parsed
    here once more; found in the journal a SECOND time, it is refused
    (`ParseHeld`), which its stage records as the document's error. One
    document in progress at two separate deaths of the process is not
    chance.
  A suspect that is parsed to its end is forgotten.

Only a process that called `start` keeps a journal (the worker). Everywhere
else, and in every child, `parsing` is a no-op.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

JOURNAL = "parsing.journal"
SUSPECTS = "parse-suspects.json"
#: bytes of a slot: a JSON line, space-padded
SLOT = 512
#: slots of the file: parses in progress at once in the worker's own process
SLOTS = 256
#: found in the journal this many times, a document is not parsed in the
#: worker's own process again
STRIKES_OUT = 2
BLANK = b" " * (SLOT - 1) + b"\n"

_lock = threading.Lock()
_fd: int | None = None
_dir: Path | None = None
_pid = 0
_next = 0
#: slots given back, taken first
_free: list[int] = []
#: (where, uid) -> {"strikes", "kind", "thread", "at"}
_suspects: dict[tuple[str, str], dict[str, Any]] = {}


class ParseHeld(RuntimeError):
    """This document was in progress in the worker's own process at
    STRIKES_OUT of its ends: it is not parsed there again."""


def _read_journal(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    out = []
    for i in range(0, len(raw), SLOT):
        line = raw[i:i + SLOT].strip(b" \n\x00")
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue                     # a slot caught half-written: no document named
        if isinstance(rec, dict) and rec.get("uid") is not None and rec.get("where") is not None:
            out.append(rec)
    return out


def _load_suspects(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return {(str(s["where"]), str(s["uid"])): {k: s.get(k) for k in ("strikes", "kind", "thread", "at")}
                for s in doc.get("suspects", []) if int(s.get("strikes") or 0) > 0}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def _save_suspects() -> None:
    """The suspects written whole, by replace (state, never desired state:
    it is this node's memory of its own ends)."""
    if _dir is None:
        return
    doc = {"suspects": [{"where": w, "uid": u, **v} for (w, u), v in sorted(_suspects.items())]}
    tmp = _dir / (SUSPECTS + ".tmp")
    try:
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, _dir / SUSPECTS)
    except OSError as e:
        print(f"{time.strftime('%H:%M:%S')} [node warn:parse] the suspects could not be written: "
              f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)


def start(state_dir: Path) -> list[dict[str, Any]]:
    """Open this process's journal under `state_dir` and return what the
    process before it left in progress — each now a suspect with one strike
    more, said once. Called by the worker at its start; never a reason not
    to start (an unwritable directory: no journal, and it says so)."""
    global _fd, _dir, _pid, _next
    state_dir = Path(state_dir)
    with _lock:
        if _fd is not None:
            return []
        left = _read_journal(state_dir / JOURNAL)
        _suspects.clear()
        _suspects.update(_load_suspects(state_dir / SUSPECTS))
        _dir = state_dir
        for rec in left:
            key = (str(rec["where"]), str(rec["uid"]))
            was = _suspects.get(key, {})
            _suspects[key] = {"strikes": int(was.get("strikes") or 0) + 1, "kind": rec.get("kind"),
                              "thread": rec.get("thread"), "at": rec.get("at")}
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(state_dir / JOURNAL, os.O_RDWR | os.O_CREAT, 0o644)
            os.ftruncate(fd, 0)          # the slots of the process before: read, now empty
        except OSError as e:
            print(f"{time.strftime('%H:%M:%S')} [node warn:parse] no parse journal ({type(e).__name__}: {e}): "
                  f"a document that holds this process will not be known at its restart",
                  file=sys.stderr, flush=True)
            _dir = None
            return left
        _fd, _pid, _next = fd, os.getpid(), 0
        _free.clear()
        if left:
            _save_suspects()
    for rec in left:
        n = _suspects.get((str(rec["where"]), str(rec["uid"])), {}).get("strikes", 1)
        print(f"{time.strftime('%H:%M:%S')} [node warn:parse] {rec.get('where')}: document {rec.get('uid')} was "
              f"in progress ({rec.get('kind')}, thread {rec.get('thread')}) in the worker's own process when "
              f"that process ended ({n} time(s)): parsed alone in a process of its own where that is "
              f"possible, refused here at {STRIKES_OUT}", file=sys.stderr, flush=True)
    return left


def active() -> bool:
    return _fd is not None


def any_suspect() -> bool:
    return bool(_suspects)


def where_of(collection) -> str:
    """A collection's physical identity for the journal: its manifest's path
    (= app/parallel.py `_row_key`), its name where it has none."""
    return str(getattr(collection, "manifest_path", None) or getattr(collection, "name", ""))


def strikes(where: str, uid: str) -> int:
    """How many times this document was found in progress at an end of the
    worker's process and has not been parsed to its end since."""
    if not _suspects:
        return 0
    with _lock:
        return int(_suspects.get((str(where), str(uid)), {}).get("strikes") or 0)


def resolved(where: str, uid: str) -> None:
    """The document was parsed to its end, or answered for good: forgotten."""
    if not _suspects:
        return
    with _lock:
        if _suspects.pop((str(where), str(uid)), None) is not None:
            _save_suspects()


def _slot() -> int | None:
    """A slot for one parse: one given back, else the next unused; None when
    SLOTS are in progress at once (that parse is not journalled)."""
    global _next
    with _lock:
        if _free:
            return _free.pop()
        if _next >= SLOTS:
            return None
        _next += 1
        return _next - 1


class _Parsing:
    __slots__ = ("where", "uid", "kind", "limit", "slot")

    def __init__(self, where: str, uid: str, kind: str, limit: int | None) -> None:
        self.where, self.uid, self.kind, self.limit, self.slot = where, uid, kind, limit, None

    def __enter__(self) -> "_Parsing":
        n = strikes(self.where, self.uid) if _suspects else 0
        if self.limit is not None and n >= self.limit:
            raise ParseHeld(f"its {self.kind} was in progress in the worker's own process at {n} of that "
                            f"process's ends (a parse that never ends, or a kill each time): not parsed "
                            f"there again — app/parse_journal.py, state/{SUSPECTS}")
        fd = _fd
        if fd is None or os.getpid() != _pid:
            return self                  # a child, or a process that keeps no journal
        slot = _slot()
        if slot is None:
            return self
        line = json.dumps({"pid": _pid, "thread": threading.current_thread().name, "kind": self.kind,
                           "where": self.where, "uid": self.uid, "at": round(time.time())},
                          ensure_ascii=False).encode("utf-8")
        if len(line) >= SLOT:
            with _lock:
                _free.append(slot)
            return self                  # a name too long for a slot: not journalled
        try:
            os.pwrite(fd, line + BLANK[len(line):], slot * SLOT)
            self.slot = slot
        except OSError:
            with _lock:
                _free.append(slot)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.slot is not None and _fd is not None:
            try:
                os.pwrite(_fd, BLANK, self.slot * SLOT)
            except OSError:
                pass
            with _lock:
                _free.append(self.slot)
        if exc_type is None and _suspects:
            resolved(self.where, self.uid)     # parsed to its end: no suspect any more
        return False


class _Nothing:
    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


_NOTHING = _Nothing()


def parsing(where: str, uid: Any, kind: str, *, limit: int | None = STRIKES_OUT):
    """`with parsing(where, uid, kind):` around ONE document's parse in this
    process: written down before, blanked after. `where` is the collection's
    physical identity (its manifest's path). Refuses (`ParseHeld`) a document
    that reached `limit` strikes; `limit=None` never refuses (the caller sent
    its suspects elsewhere already). A no-op where no journal is kept and
    nothing is suspected."""
    if _fd is None and not _suspects:
        return _NOTHING
    return _Parsing(str(where), str(uid), kind, limit)


def reset() -> None:
    """Tests: no journal, no suspect."""
    global _fd, _dir, _pid, _next
    with _lock:
        if _fd is not None:
            try:
                os.close(_fd)
            except OSError:
                pass
        _fd, _dir, _pid, _next = None, None, 0, 0
        _free.clear()
        _suspects.clear()
