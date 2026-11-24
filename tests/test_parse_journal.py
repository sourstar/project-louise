"""app/parse_journal.py: the document the worker is parsing in its own
process, known to the process that comes after it.

The node, 2026-11-24: a parse that never ended held the worker; with the
dead-man's switch (app/deadman.py) the worker now leaves by itself — and would
walk into the same document at its restart, for ever, without this."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import parallel, parse_journal, worker

ROOT = Path(__file__).resolve().parent.parent
WHERE = "/data/collections/eurlex/manifest.sqlite"


def _slots(state: Path) -> list[dict]:
    raw = (state / parse_journal.JOURNAL).read_bytes()
    return [json.loads(raw[i:i + parse_journal.SLOT]) for i in range(0, len(raw), parse_journal.SLOT)
            if raw[i:i + parse_journal.SLOT].strip()]


def test_a_document_is_written_down_while_it_is_parsed_and_blanked_after(tmp_path):
    assert parse_journal.start(tmp_path) == [] and parse_journal.active()
    with parse_journal.parsing(WHERE, "hu:32007L0019", "fts"):
        (rec,) = _slots(tmp_path)
        assert (rec["where"], rec["uid"], rec["kind"], rec["pid"]) == (WHERE, "hu:32007L0019", "fts", os.getpid())
        assert rec["thread"] == threading.current_thread().name
    assert _slots(tmp_path) == [], "parsed to its end: nothing in progress"
    with pytest.raises(ValueError):
        with parse_journal.parsing(WHERE, "x:1", "fts"):
            raise ValueError("a document that fails is not one in progress")
    assert _slots(tmp_path) == []


def test_a_slot_is_given_back_so_a_thread_a_parse_never_runs_out(tmp_path):
    """The fts look-ahead starts a thread per link: a slot kept per thread
    would be gone after SLOTS of them, and nothing journalled from then on."""
    parse_journal.start(tmp_path)

    def parse(i):
        with parse_journal.parsing(WHERE, f"e:{i}", "fts"):
            assert [r["uid"] for r in _slots(tmp_path)] == [f"e:{i}"]
    for i in range(parse_journal.SLOTS + 40):
        t = threading.Thread(target=parse, args=(i,))
        t.start()
        t.join(5)
    assert (tmp_path / parse_journal.JOURNAL).stat().st_size <= parse_journal.SLOT, "one slot, used again"
    with parse_journal.parsing(WHERE, "last", "fts"):
        assert [r["uid"] for r in _slots(tmp_path)] == ["last"], "still journalled after that many threads"


def test_each_thread_has_its_own_slot(tmp_path):
    parse_journal.start(tmp_path)
    inside, go = threading.Barrier(3), threading.Event()

    def parse(uid):
        with parse_journal.parsing(WHERE, uid, "fts"):
            inside.wait(5)
            go.wait(5)
    threads = [threading.Thread(target=parse, args=(f"e:{i}",), name=f"pipeline-{i}") for i in range(2)]
    for t in threads:
        t.start()
    inside.wait(5)
    assert sorted(r["uid"] for r in _slots(tmp_path)) == ["e:0", "e:1"]
    go.set()
    for t in threads:
        t.join(5)
    assert _slots(tmp_path) == []


def test_nothing_is_kept_by_a_process_that_did_not_start_one(tmp_path):
    with parse_journal.parsing(WHERE, "e:1", "fts"):
        pass
    assert not parse_journal.active() and not (tmp_path / parse_journal.JOURNAL).exists()


HELD = """
import os, signal, sys, time
from pathlib import Path
from app import parse_journal
parse_journal.start(Path(sys.argv[1]))
with parse_journal.parsing({where!r}, "hu:32007L0019", sys.argv[2]):
    print("in", flush=True)
    os.kill(os.getpid(), signal.SIGKILL)      # the process ends inside the parse: no clean-up runs
"""


def _dies_parsing(state: Path, kind: str) -> None:
    p = subprocess.run([sys.executable, "-c", HELD.format(where=WHERE), str(state), kind], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == -signal.SIGKILL and p.stdout.split() == ["in"], (p.returncode, p.stderr[-500:])


def test_what_a_dead_process_was_parsing_is_a_suspect_of_its_successor(tmp_path, capsys):
    """A process killed inside a parse leaves its slot filled: the next one
    reads it at its start, says it, and keeps it in a file of its own — a
    second restart before the document is dealt with does not forget it."""
    _dies_parsing(tmp_path, "fts")
    left = parse_journal.start(tmp_path)
    assert [(r["where"], r["uid"], r["kind"]) for r in left] == [(WHERE, "hu:32007L0019", "fts")]
    assert parse_journal.strikes(WHERE, "hu:32007L0019") == 1 and parse_journal.strikes(WHERE, "other") == 0
    assert _slots(tmp_path) == [], "the successor's journal starts empty"
    err = capsys.readouterr().err
    assert "document hu:32007L0019 was in progress (fts" in err and "[node warn:parse]" in err
    parse_journal.reset()                          # ...and that one ends too, cleanly
    assert parse_journal.start(tmp_path) == []
    assert parse_journal.strikes(WHERE, "hu:32007L0019") == 1, "kept across a restart that found nothing new"
    with parse_journal.parsing(WHERE, "hu:32007L0019", "embed-chunks"):
        pass                                       # parsed to its end, here: no suspect any more
    assert parse_journal.strikes(WHERE, "hu:32007L0019") == 0
    parse_journal.reset()
    parse_journal.start(tmp_path)
    assert parse_journal.strikes(WHERE, "hu:32007L0019") == 0, "forgotten in the file too"


def test_in_progress_at_two_ends_of_the_process_it_is_refused_here(tmp_path):
    """Where a document cannot be parsed elsewhere (the embed path chunks on
    its own thread), it is tried once more; found in progress a second time,
    it is refused — its stage records that as the document's error. One
    document in progress at two separate ends of the process is not chance."""
    _dies_parsing(tmp_path, "embed-chunks")
    parse_journal.start(tmp_path)
    with parse_journal.parsing(WHERE, "hu:32007L0019", "embed-chunks"):
        pass                                       # one strike: tried again, and it ends this time
    assert parse_journal.strikes(WHERE, "hu:32007L0019") == 0
    parse_journal.reset()
    _dies_parsing(tmp_path, "embed-chunks")
    parse_journal.start(tmp_path)
    parse_journal.reset()
    _dies_parsing(tmp_path, "embed-chunks")        # ...and it held the process again
    parse_journal.start(tmp_path)
    assert parse_journal.strikes(WHERE, "hu:32007L0019") == 2
    with pytest.raises(parse_journal.ParseHeld, match="not parsed there again"):
        with parse_journal.parsing(WHERE, "hu:32007L0019", "embed-chunks"):
            raise AssertionError("it was parsed here a third time")
    assert _slots(tmp_path) == [], "a refusal is not a parse in progress"


class _Own:
    """A process of its own (`parallel._lane_executor`), decided by the test."""
    ran: list[list[str]] = []
    never: set[str] = set()

    def submit(self, fn, kind, collection, part, allowance):
        from concurrent.futures import Future
        uids = [r["uid"] for r in part]
        _Own.ran.append(uids)
        f = Future()
        f.set_running_or_notify_cancel()
        if not set(uids) & _Own.never:
            f.set_result([(True, r["uid"]) for r in part])
        return f

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def _collection():
    return SimpleNamespace(name="eurlex", manifest_path=WHERE, plugin_instance=lambda: object())


@pytest.fixture
def pooled(monkeypatch):
    here: list[str] = []

    def compute(kind, collection, part):
        here.extend(r["uid"] for r in part)
        return [(True, r["uid"]) for r in part]
    _Own.ran, _Own.never = [], set()
    monkeypatch.setattr(parallel, "_one", lambda kind, collection, row: here.append(row["uid"]) or row["uid"])
    monkeypatch.setattr(parallel, "_lane_executor", _Own)
    monkeypatch.setattr(parallel, "ISOLATE_START_S", 0.0)
    monkeypatch.setattr(parallel.config, "PARSE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(parallel.config, "PARSE_PROCESSES", 1)      # no pool: everything in this process
    parallel.reset_failures()
    return here


def test_a_suspect_is_parsed_alone_in_a_process_of_its_own_never_here(tmp_path, pooled):
    """The worker held by one document, ended by its switch, started again:
    the batch of ONE light row it then selects is computed in this process —
    the same document, the same hold. As a suspect it goes to a process of
    its own; it comes back, is answered, and is a suspect no more."""
    _dies_parsing(tmp_path, "fts")
    parse_journal.start(tmp_path)
    rows = [{"uid": "hu:32007L0019", "size": 1}]
    assert parallel.compute("fts", _collection(), rows) == [(True, "hu:32007L0019")]
    assert pooled == [] and _Own.ran == [["hu:32007L0019"]], "in a process of its own, not here"
    assert parse_journal.strikes(WHERE, "hu:32007L0019") == 0
    assert parallel.compute("fts", _collection(), rows) == [(True, "hu:32007L0019")]
    assert pooled == ["hu:32007L0019"], "no suspect any more: one light row is computed here, as before"


def test_a_suspect_that_does_not_come_back_alone_is_an_error_on_it(tmp_path, pooled):
    _dies_parsing(tmp_path, "fts")
    parse_journal.start(tmp_path)
    _Own.never = {"hu:32007L0019"}
    rows = [{"uid": "e:1", "size": 1}, {"uid": "hu:32007L0019", "size": 1}, {"uid": "e:3", "size": 1}]
    got = parallel.compute("fts", _collection(), rows)
    assert got[0] == (True, "e:1") and got[2] == (True, "e:3"), "its neighbours, in order"
    assert got[1][0] is False and got[1][1]["type"] == "ParseNoAnswer"
    assert not worker._deferred_row(got[1][1]), "an error on the document"
    assert "hu:32007L0019" not in pooled
    assert parse_journal.strikes(WHERE, "hu:32007L0019") == 0, "answered for good: no suspect"
    again = parallel.compute("fts", _collection(), [rows[1]])
    assert again[0][1]["type"] == "ParseNoAnswer" and "hu:32007L0019" not in pooled, \
        "remembered: never asked of this process"


def test_the_in_process_parse_writes_each_document_down(tmp_path, monkeypatch):
    parse_journal.start(tmp_path)
    seen: list[list[str]] = []
    monkeypatch.setattr(parallel, "_one",
                        lambda kind, collection, row: seen.append([r["uid"] for r in _slots(tmp_path)]))
    parallel._compute("fts", _collection(), [{"uid": "e:1"}, {"uid": "e:2"}])
    assert seen == [["e:1"], ["e:2"]] and _slots(tmp_path) == []


# ---- the whole of it, in real processes ------------------------------------------------

LIFE = """
import json, re, sys
from pathlib import Path
from app import config, deadman, parallel, parse_journal

class Collection:
    name = "eurlex"
    manifest_path = {where!r}
    def plugin_instance(self):
        raise RuntimeError("no plugin: one row, no peak")

def one(kind, collection, row):
    if row["uid"] == "bad":
        re.match(r"(a+)+$", "a" * 40 + "b")      # the parse that never ends, in ANY process
    return row["uid"]

# at import, so a process of its own parses as this one does
parallel._one = one
config.PARSE_PROCESSES = 1                        # no pool: what is asked is computed in this process
config.PARSE_TIMEOUT_S = 2.0
parallel.ISOLATE_START_S = 6.0

if __name__ == "__main__":
    parse_journal.start(Path(sys.argv[1]))
    deadman.arm(3.0, who="the test's worker")
    rows = [{{"uid": u, "size": 1}} for u in sys.argv[2].split(",")]
    got = parallel.compute("fts", Collection(), rows)
    print(json.dumps([[ok, v if ok else v["type"]] for ok, v in got]), flush=True)
"""


def _life(state: Path, uids: str, limit_s: float = 120):
    # from a FILE: a process of its own imports the main module again (spawn), and
    # `python -c` has none to import — its child would die unpickling, not parsing
    script = state.parent / "life.py"
    script.write_text(LIFE.format(where=WHERE))
    return subprocess.run([sys.executable, str(script), str(state), uids], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=limit_s,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="no interval timer on this platform")
def test_a_worker_held_by_a_document_leaves_and_its_successor_does_not_walk_into_it(tmp_path):
    """The whole of it. FIRST LIFE: the worker computes one light row in its
    own process, the parse never ends, the dead-man's switch ends the
    process. SECOND LIFE: it reads what was in progress, parses that document
    alone in a process of its own — where it never ends either — answers it
    as an error, parses its neighbour, and is still there. Without the
    journal the second life would be the first again, every time."""
    state = tmp_path / "state"
    state.mkdir()
    tmp_path = state
    first = _life(tmp_path, "bad")
    assert first.returncode == -signal.SIGALRM, f"{first.returncode}\n{first.stderr[-1200:]}"
    assert first.stdout == "" and "in one" in first.stderr and "most recent call first" in first.stderr
    assert [r["uid"] for r in _slots(tmp_path)] == ["bad"], "what it was parsing outlives it"

    import time
    t0 = time.monotonic()
    second = _life(tmp_path, "bad,good")
    took = time.monotonic() - t0
    assert second.returncode == 0, f"{second.returncode}\n{second.stderr[-1500:]}"
    assert json.loads(second.stdout) == [[False, "ParseNoAnswer"], [True, "good"]]
    assert took >= 7.0, f"its own process was waited for its bound, then ended: {took:.1f} s"
    control = _life(tmp_path, "fine,good")
    assert json.loads(control.stdout) == [[True, "fine"], [True, "good"]], control.stderr[-800:]
    assert "document bad was in progress (fts" in second.stderr
    assert "were in progress in the worker's own process when it last ended" in second.stderr
    assert "document bad did not come back from a process of its own" in second.stderr
    assert _slots(tmp_path) == []
    suspects = json.loads((tmp_path / parse_journal.SUSPECTS).read_text())["suspects"]
    assert suspects == [], "answered for good: no suspect left for a third life"
