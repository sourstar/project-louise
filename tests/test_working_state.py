"""`louise node checkpoint` — the node's last working state and an agent's
heartbeat (operator, 2026-11-07: "when the limit of usage approaches, and you
are going to be frozen for more than 2 hours, put all the developments on
hold and resume the last working state before freezing"; app/working_state.py).

A save records the mode, the collections' own pauses, every held
collection's embed setting as its file states it, the freezes and the dev
units; with no beat for two hours `watch` restores it — through the
operator commands' own functions (invariant 10), changing exactly what
differs, a collection held now and not then left as it is, one held then
and not now skipped and said, a freeze never changed — and closes it."""
from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
from pathlib import Path

import pytest

from app import collection_file, collections as collections_mod, effective_config, louise, node_state
from app import working_state as ws
from tests.conftest import codex_collection

H = 3600.0


@pytest.fixture
def node(isolated_config, data_dir, config_dir, corpus_dir):
    """A running node holding gutenberg (its file states no embed) and
    wiki (its file states `all`)."""
    codex_collection("wiki", louise_root=data_dir, config_dir=config_dir, corpus_dir=corpus_dir,
                     toml_extra='embed = "all"\n')
    node_state.set_worker_state("running", by="test")
    return {"data": data_dir, "config": config_dir, "corpus": corpus_dir}


def _set_embed(name: str, mode: str) -> None:
    effective_config.set_and_apply(name, {"embed": mode})


def _out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def _file(name: str) -> Path:
    return collections_mod.config_toml_path(name)


# ---- save, status ---------------------------------------------------------------------

def test_save_records_the_working_state_and_status_reads_it(node, capsys):
    node_state.set_paused("wiki", True, by="louise node pause wiki")
    node_state.set_frozen("gutenberg", True, by="louise node freeze gutenberg")
    assert louise.main(["node", "checkpoint", "save", "--note", "engine A/B",
                        "--dev-unit", "louise-engine-ab5", "louise-onednn-arms",
                        "--dev-unit", "louise-engine-ab5"]) == 0
    got = _out(capsys)
    assert got["checkpoint"] == "open" and got["file"] == str(node["data"] / "state" / "working-state.json")
    assert got["dev_units"] == ["louise-engine-ab5", "louise-onednn-arms"], "order kept, once each"
    rec = json.loads(ws.path().read_text(encoding="utf-8"))
    st = rec["state"]
    assert st["mode"] == "running"
    assert {n: h["embed"] for n, h in st["held"].items()} == {"gutenberg": None, "wiki": "all"}
    assert all(h["codex"] for h in st["held"].values()), "which collection each name held"
    assert set(st["paused"]) == {"wiki"} and st["paused"]["wiki"]["by"] == "louise node pause wiki"
    assert set(st["frozen"]) == {"gutenberg"} and st["frozen"]["gutenberg"]["by"] == "louise node freeze gutenberg"
    assert rec["note"] == "engine A/B" and rec["saved_by"].startswith("louise node checkpoint save")
    assert rec["last_beat"] == rec["saved_at"] and rec["hold_until"] is None
    # status: open, nothing to restore yet
    assert louise.main(["node", "checkpoint", "status", "--json"]) == 0
    st = _out(capsys)
    assert st["checkpoint"] == "open" and st["expired"] is False
    assert st["would"] == {"changes": [], "skipped": [], "untouched": [], "notes": []}
    assert louise.main(["node", "checkpoint", "status"]) == 0
    text = capsys.readouterr().out
    assert "checkpoint: open" in text and "restore would change: nothing" in text
    assert "louise-engine-ab5, louise-onednn-arms" in text


def test_a_second_save_is_refused_while_one_is_open_unless_replace(node, capsys):
    assert louise.main(["node", "checkpoint", "save", "--dev-unit", "louise-engine-ab5"]) == 0
    capsys.readouterr()
    node_state.set_worker_state("acquire-only", by="test")
    assert louise.main(["node", "checkpoint", "save"]) == 2
    assert "a checkpoint is open since" in capsys.readouterr().err
    assert ws.read()["state"]["mode"] == "running", "the refused save wrote nothing"
    assert louise.main(["node", "checkpoint", "save", "--replace", "--dev-unit", "louise-x"]) == 0
    rec = ws.read()
    assert rec["state"]["mode"] == "acquire-only"
    assert rec["dev_units"] == ["louise-engine-ab5", "louise-x"], "the replaced one's units are carried"
    assert rec["previous"]["status"] == "open"


def test_status_with_nothing_saved_says_so_and_creates_nothing(isolated_config, capsys):
    assert louise.main(["node", "checkpoint", "status", "--json"]) == 0
    assert _out(capsys)["checkpoint"] == "none"
    assert not ws.path().exists() and not node_state.path().exists()


# ---- restore ---------------------------------------------------------------------------

def test_restore_changes_exactly_what_differs_and_nothing_else(node, capsys):
    """The development changed the mode, gutenberg's pause, wiki's embed;
    the operator froze wiki; a collection was added (paused, embed off) and
    one held at save was removed. Restore puts back the three, leaves the
    new one and the freeze, says the removed one."""
    codex_collection("old", louise_root=node["data"], config_dir=node["config"], corpus_dir=node["corpus"])
    ws.save(note="dev", units=["louise-engine-ab5"])
    # the development
    node_state.set_worker_state("acquire-only", by="dev")
    node_state.set_paused("gutenberg", True, by="dev")
    _set_embed("wiki", "off")
    node_state.set_frozen("wiki", True, by="the operator")
    codex_collection("fresh", louise_root=node["data"], config_dir=node["config"], corpus_dir=node["corpus"])
    node_state.set_paused("fresh", True, by="dev")
    _set_embed("fresh", "off")
    _file("old").rename(_file("old").with_suffix(".retired"))
    fresh_before, gutenberg_before = _file("fresh").read_bytes(), _file("gutenberg").read_bytes()

    assert louise.main(["node", "checkpoint", "restore", "--dry-run"]) == 0
    dry = _out(capsys)
    assert [ws.said(c) for c in dry["changes"]] == [
        "mode acquire-only -> running", "gutenberg: paused -> not paused", "wiki: embed off -> all"]
    assert node_state.worker_state() == "acquire-only", "a dry run changes nothing"

    assert louise.main(["node", "checkpoint", "restore", "--by", "the test"]) == 0
    got = _out(capsys)
    assert [ws.said(c) for c in got["changed"]] == [ws.said(c) for c in dry["changes"]]
    assert node_state.worker_state() == "running"
    assert node_state.paused_collections() == {"fresh"}, "the new collection's pause is untouched"
    assert collection_file.stated_embed("wiki") == "all"
    assert collections_mod.by_name("wiki").embed_mode_default == "all"
    assert _file("fresh").read_bytes() == fresh_before and _file("gutenberg").read_bytes() == gutenberg_before
    assert node_state.frozen_collections() == {"wiki"}, "a freeze is the operator's: never changed"
    assert got["untouched"] == [{"collection": "fresh", "why": "held now, not at save: left as it is"}]
    assert got["skipped"] == [{"collection": "old", "why": "held at save, not now: nothing of it is restored"}]
    assert got["notes"][0]["what"] == "frozen" and got["notes"][0]["now"] == ["wiki"]
    assert got["failed"] == []
    # each write recorded with who restored
    assert node_state.mode_history()[-1]["by"] == "the test"
    h = node_state.pause_history()[-1]
    assert (h["collection"], h["paused"], h["by"]) == ("gutenberg", False, "the test")
    rec = ws.read()
    assert rec["status"] == "closed" and rec["closed_how"] == "restored" and rec["closed_by"] == "the test"
    assert rec["restored"]["changed"] == got["changed"]
    # IDEMPOTENT: nothing open, nothing done
    assert louise.main(["node", "checkpoint", "restore"]) == 0
    assert _out(capsys)["restored"] is None
    assert node_state.mode_history()[-1]["by"] == "the test"


def test_a_name_that_now_holds_another_collection_is_not_restored(node):
    """München, 2026-11-10: a checkpoint saved before pubmed9 was renamed over
    pubmed, restored a day later, paused the new pubmed and set its embed off
    — by name. A name is not a collection: restored only where the name holds
    the collection it held at save."""
    ws.save()
    _set_embed("wiki", "off")
    node_state.set_paused("wiki", True, by="dev")
    text = _file("wiki").read_text(encoding="utf-8")
    held_then = ws.read()["state"]["held"]["wiki"]["codex"]
    _file("wiki").write_text(text.replace(held_then, "f" * 64), encoding="utf-8")   # another collection
    node_state.set_worker_state("acquire-only", by="dev")
    got = ws.restore(by="t")
    assert [c["what"] for c in got["changed"]] == ["mode"], "the mode is the node's, not a name's"
    (skip,) = got["skipped"]
    assert skip["collection"] == "wiki" and "another collection" in skip["why"]
    assert 'embed = "off"' in _file("wiki").read_text(encoding="utf-8")
    assert "wiki" in node_state.paused_collections()


def test_a_checkpoint_that_does_not_say_which_collection_restores_none_by_name(node):
    ws.save()
    rec = json.loads(ws.path().read_text(encoding="utf-8"))
    for h in rec["state"]["held"].values():
        del h["codex"]                     # a checkpoint saved before 2026-11-10
    ws.path().write_text(json.dumps(rec), encoding="utf-8")
    _set_embed("wiki", "off")
    got = ws.restore(by="t")
    assert got["changed"] == [] and "does not say which collection" in got["skipped"][0]["why"]
    assert collection_file.stated_embed("wiki") == "off"


def test_an_embed_the_file_did_not_state_is_restored_as_the_default(node):
    ws.save()
    _set_embed("gutenberg", "all")
    (change,) = ws.restore(by="t")["changed"]
    assert change["to"] == "on-demand" and change["stated_at_save"] is None
    assert collection_file.stated_embed("gutenberg") == "on-demand"
    assert collections_mod.by_name("gutenberg").embed_mode_default == "lazy"


def test_restore_writes_only_through_the_operator_commands_functions(node, monkeypatch):
    """Invariant 10: the mode by `node pause|resume`'s setter, a pause by
    `node pause|resume <c>`'s, an embed setting by `hold config apply
    --set`'s writer and apply — never the database or the file by a
    second path."""
    ws.save()
    node_state.set_worker_state("paused", by="dev")
    node_state.set_paused("wiki", True, by="dev")
    _set_embed("wiki", "off")
    calls: list[tuple] = []

    def spy(owner, name):
        real = getattr(owner, name)

        def call(*a, **kw):
            calls.append((name, a, kw))
            return real(*a, **kw)
        monkeypatch.setattr(owner, name, call)
    spy(node_state, "set_worker_state")
    spy(node_state, "set_paused")
    spy(collection_file, "set_values")
    spy(effective_config, "apply")
    ws.restore(by="t")
    assert calls == [("set_worker_state", ("running",), {"by": "t"}),
                     ("set_paused", ("wiki", False), {"by": "t"}),
                     ("set_values", ("wiki", {"embed": "all"}), {}),
                     ("apply", ("wiki",), {})]
    src = Path(ws.__file__).read_text(encoding="utf-8")
    for second_path in ("set_control", "db.set_", "_write_codex_toml", "_replace(", ".write_text(",
                        "set_frozen", "apply_desired_embed_mode"):
        assert second_path not in src, second_path


def test_a_restore_that_fails_in_part_stays_open_and_the_next_one_finishes(node, monkeypatch, capsys):
    ws.save()
    node_state.set_worker_state("acquire-only", by="dev")
    node_state.set_paused("wiki", True, by="dev")
    real = node_state.set_paused

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(node_state, "set_paused", locked)
    assert louise.main(["node", "checkpoint", "restore"]) == 1
    got = _out(capsys)
    assert got["failed"][0]["collection"] == "wiki" and "database is locked" in got["failed"][0]["error"]
    assert node_state.worker_state() == "running", "what could be restored was"
    rec = ws.read()
    assert rec["status"] == "open" and rec["restore_failed"]["failed"]
    monkeypatch.setattr(node_state, "set_paused", real)
    got = ws.restore(by="t")
    assert [c["what"] for c in got["changed"]] == ["pause"], "only what still differs"
    assert ws.read()["status"] == "closed" and node_state.paused_collections() == set()


# ---- watch -----------------------------------------------------------------------------

def test_watch_does_nothing_before_the_deadline_then_restores_once(node):
    t0 = 1_000_000.0
    ws.save(units=["louise-engine-ab5"], now=t0)
    node_state.set_worker_state("seed-only", by="dev")
    code, out = ws.watch(now=t0 + 1.9 * H)
    assert code == 0 and out["expired"] is False and out["units"] == []
    assert node_state.worker_state() == "seed-only"
    code, out = ws.watch(now=t0 + 2 * H + 1, dry_run=True)
    assert code == ws.EXIT_EXPIRED_DRY and out["units"] == ["louise-engine-ab5"]
    assert [ws.said(c) for c in out["would"]["changes"]] == ["mode seed-only -> running"]
    assert node_state.worker_state() == "seed-only" and ws.read()["status"] == "open"
    code, out = ws.watch(now=t0 + 2 * H + 2)
    assert code == ws.EXIT_RESTORED and out["units"] == ["louise-engine-ab5"]
    assert node_state.worker_state() == "running"
    assert node_state.mode_history()[-1]["by"].startswith("louise node checkpoint watch: no agent beat since")
    rec = ws.read()
    assert rec["status"] == "closed" and rec["closed_how"] == "restored"
    code, out = ws.watch(now=t0 + 3 * H)
    assert code == 0 and out["checkpoint"] == "closed" and out["units"] == [], "then a no-op"


def test_watch_from_the_command_line(node, monkeypatch, capsys):
    t0 = 2_000_000.0
    monkeypatch.setattr(ws, "_clock", lambda: t0)
    assert louise.main(["node", "checkpoint", "save", "--dev-unit", "louise-a", "louise-b"]) == 0
    capsys.readouterr()
    assert louise.main(["node", "checkpoint", "watch"]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(ws, "_clock", lambda: t0 + 31 * 60)
    assert louise.main(["node", "checkpoint", "watch", "--after", "30m", "--dry-run"]) == 11
    assert capsys.readouterr().out.splitlines() == ["louise-a", "louise-b"], "one unit a line"
    assert louise.main(["node", "checkpoint", "watch", "--after", "30m", "--json"]) == 10
    out = _out(capsys)
    assert out["expired"] and out["units"] == ["louise-a", "louise-b"] and out["restored"]["failed"] == []
    assert louise.main(["node", "checkpoint", "watch", "--after", "30m", "--json"]) == 0
    assert louise.main(["node", "checkpoint", "watch", "--after", "soon"]) == 2


# ---- beat, close --------------------------------------------------------------------

def test_beat_is_refused_with_nothing_open(isolated_config, capsys):
    assert louise.main(["node", "checkpoint", "beat"]) == 2
    assert "no checkpoint is open" in capsys.readouterr().err


def test_beat_moves_the_deadline_and_a_declared_step_holds_the_watchdog_at_most_a_day(node, capsys):
    t0 = 3_000_000.0
    ws.save(now=t0)
    rec = ws.beat(now=t0 + 1.5 * H)
    assert ws.deadline(rec) == t0 + 3.5 * H
    rec = ws.beat(for_s=5 * H, now=t0 + 2 * H)
    assert rec["hold_until"] == t0 + 7 * H and not rec["capped"]
    assert ws.watch(now=t0 + 6 * H)[0] == 0, "the declared step is still running"
    assert ws.watch(now=t0 + 7 * H, dry_run=True)[0] == ws.EXIT_EXPIRED_DRY
    rec = ws.beat(for_s=30 * H, now=t0 + 3 * H)
    assert rec["capped"] and rec["hold_until"] == t0 + 3 * H + ws.MAX_HOLD_S
    rec = ws.beat(for_s=0, now=t0 + 4 * H)
    assert rec["hold_until"] is None and ws.deadline(rec) == t0 + 6 * H
    rec = ws.beat(until=t0 + 10 * H, units=["louise-late"], now=t0 + 4 * H)
    assert rec["hold_until"] == t0 + 10 * H and rec["dev_units"] == ["louise-late"]
    with pytest.raises(ws.Refused, match="not in the future"):
        ws.beat(until=t0, now=t0 + 4 * H)
    # the command line: the cap said, a time without its offset refused
    assert louise.main(["node", "checkpoint", "beat", "--for", "30h"]) == 0
    assert "capped" in _out(capsys)["says"]
    assert louise.main(["node", "checkpoint", "beat", "--hold-until", "2026-11-07T18:00:00"]) == 2
    assert "names no offset" in capsys.readouterr().err
    assert louise.main(["node", "checkpoint", "beat", "--for", "5"]) == 2


def test_close_ends_it_and_nothing_is_restored(node, capsys):
    ws.save(units=["louise-engine-ab5"])
    node_state.set_worker_state("acquire-only", by="dev")
    assert louise.main(["node", "checkpoint", "close"]) == 0
    assert "not stopped" in _out(capsys)["says"]
    assert ws.read()["closed_how"] == "closed"
    assert ws.watch(now=ws._clock() + 10 * H)[0] == 0
    assert ws.restore() is None and node_state.worker_state() == "acquire-only"
    assert louise.main(["node", "checkpoint", "beat"]) == 2
    assert louise.main(["node", "checkpoint", "close"]) == 0
    assert _out(capsys)["closed"] is None
    assert louise.main(["node", "checkpoint", "save"]) == 0, "a new save after a close"
    assert ws.read()["previous"]["closed_how"] == "closed"


# ---- the record ---------------------------------------------------------------------

def test_a_crash_mid_write_leaves_the_previous_record_valid(node, monkeypatch):
    t0 = 4_000_000.0
    ws.save(now=t0)
    before = ws.path().read_bytes()

    def crash(*a, **kw):
        raise OSError("killed mid-write")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", crash)
        with pytest.raises(OSError):
            ws.beat(now=t0 + H)
    assert ws.path().read_bytes() == before and ws.read()["last_beat"] == t0
    assert not list(ws.path().parent.glob(".working-state.json.*.tmp")), "no temporary left"
    # a temporary a SIGKILL left behind is nobody's record
    (ws.path().parent / ".working-state.json.999.tmp").write_bytes(b'{"kind": "trunc')
    assert ws.beat(now=t0 + H)["last_beat"] == t0 + H


def test_an_unreadable_record_is_refused_never_taken_for_none(node, capsys):
    ws.path().parent.mkdir(parents=True, exist_ok=True)
    ws.path().write_text('{"kind": "louise-working-state", "version": 1, "status": "op', encoding="utf-8")
    for argv in (["status"], ["watch"], ["beat"], ["restore"], ["save"]):
        assert louise.main(["node", "checkpoint", *argv]) == 2, argv
        assert "not JSON" in capsys.readouterr().err
    assert louise.main(["node", "checkpoint", "save", "--replace"]) == 0
    assert ws.read()["status"] == "open"
    # JSON, but not a record this platform reads: refused the same way
    rec = json.loads(ws.path().read_text(encoding="utf-8"))
    del rec["state"]["held"]
    ws.path().write_text(json.dumps(rec), encoding="utf-8")
    assert louise.main(["node", "checkpoint", "watch"]) == 2
    assert "not a working state this platform reads" in capsys.readouterr().err


def test_another_checkpoint_command_holding_the_record_is_a_deferral(node, monkeypatch, capsys):
    ws.save()
    monkeypatch.setattr(ws, "LOCK_WAIT_S", 0.2)
    with open(ws.path().with_name(ws.FILE_NAME + ".lock"), "a+b") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert louise.main(["node", "checkpoint", "beat"]) == 3
        assert "deferred" in capsys.readouterr().err
    assert louise.main(["node", "checkpoint", "beat"]) == 0


@pytest.mark.parametrize("unit", ["sshd", "docker.service", "louise", "louise-", "louise-a b",
                                  "louise-x;rm", "louise-$(id)", "louise-watchdog", "louise-watchdog.timer"])
def test_only_louise_dev_units_are_recorded(node, unit, capsys):
    assert louise.main(["node", "checkpoint", "save", "--dev-unit", unit]) == 2
    assert "--dev-unit" in capsys.readouterr().err
    assert not ws.path().exists()
    with pytest.raises(ws.Refused):
        ws.check_units(["louise-ok", "-" + unit])
    assert ws.check_units(["louise-engine-ab5", "louise-x@1.service", "louise-engine-ab5"]) == [
        "louise-engine-ab5", "louise-x@1.service"]


def test_durations():
    assert ws.parse_duration("2h") == 2 * H and ws.parse_duration("1h30m") == 1.5 * H
    assert ws.parse_duration("90s") == 90 and ws.parse_duration("1d") == 24 * H
    assert ws.parse_duration("0") == 0
    for bad in ("5", "2x", "h", "-2h", "2h 30m", ""):
        with pytest.raises(ws.Refused):
            ws.parse_duration(bad)
    assert ws.parse_when("2026-11-07T18:00:00Z") == ws.parse_when("2026-11-07T19:00:00+01:00")
    assert re.fullmatch(r"\d+ h \d\d min", ws.span(2 * H + 300))
