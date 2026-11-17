"""THE ANNOTATIONS CASCADE, run (app/hold/cascade_keeper.py; app/hold/cascade.py;
docs/notes/collection-updates.md §4): a source PUBLISHES a snapshot (written and
signed; its `state/published/<id>.json`), and every annotations collection that
DECLARES it — its Codex `annotates`, its method's `inputs` — is woken and
updated, in Kahn's order, transitively, once; a wake waits for a source whose
update is in flight and for its own next period; a cycle is refused where it is
declared and by the keeper; a crash resumes; a second round does nothing; the
old run's gate opens once `woken.json` says started; and a round reads markers
only."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import collection_file
from app.hold import annotation_methods as am
from app.hold import annotations as ann
from app.hold import cascade, cascade_keeper, update_state
from app.hold import collection as col
from app.hold import shard as shard_mod
from app.hold import snapshot as sn
from tests.hold.conftest import KEY, NOW
from tests.hold.test_annotations import a_claim, a_target, claims_file, make
from tests.hold.test_annotations_update import NAME, OCTOBER, target_next

NOVEMBER = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
B = "pubmed-b"


def _b_produce(collection, out: Path, log=print) -> dict:
    Path(out).write_bytes(claims_file(a_claim(), a_claim("pubmed:2", "pubmed:4")))
    return {"claims": 2}


#: a method on pubmed that also READS the annotations collections held here:
#: its collection declares them (the cascade's `inputs`)
READS_ANNOTATIONS = am.Method("reads-annotations", "pubmed", _b_produce, "a test method", inputs=("annotations",))


@pytest.fixture
def node(tmp_path, monkeypatch):
    """The node's root: pubmed (2026-08) and `pubmed-same-work`, claims given
    by hand, made here (2026-09); every child of a run in this process, with
    the test's clock and key."""
    from app import parallel
    root = tmp_path / "louise" / "collections"
    root.mkdir(parents=True)
    (tmp_path / "config" / "collections").mkdir(parents=True)
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path / "louise"))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    cascade_keeper.reset()
    monkeypatch.setitem(am.METHODS, READS_ANNOTATIONS.name, READS_ANNOTATIONS)
    monkeypatch.setattr(am, "_target_collection", lambda name: SimpleNamespace(name=name, plugin="pubmed"))
    monkeypatch.setattr(parallel, "watch_parent", lambda: None)
    monkeypatch.setattr(am, "_stop_outside_last_writes", lambda: None)
    monkeypatch.setattr(am, "_node_key", lambda name: KEY)
    clock = [OCTOBER]
    real_update, real_finish = ann.update, am.finish
    monkeypatch.setattr(ann, "update", lambda *a, **k: real_update(*a, **{**k, "clock": clock[0], "now": NOW,
                                                                          "key": KEY}))
    monkeypatch.setattr(am, "finish", lambda *a, **k: real_finish(*a, **{**k, "now": NOW, "clock": clock[0]}))
    ran: list[str] = []
    kill = {"next": 0}

    def in_process(target_fn, args, *, stop, poll_s, label, error=None):
        ran.append(label)
        if kill["next"]:
            kill["next"] -= 1
            raise am.ChildFailed(-9, f"{label} exited -9 (killed: out of memory?)")
        with pytest.raises(SystemExit) as e:
            target_fn(*args)
        if e.value.code:
            raise am.ChildFailed(e.value.code, error.read_text())
    monkeypatch.setattr(am, "_run", in_process)
    target, _ = a_target(root)
    make(root)
    lines: list[str] = []
    return SimpleNamespace(root=root, target=target, clock=clock, ran=ran, kill=kill, lines=lines, failed={})


def keep(node, clock=None, **kw):
    if clock is not None:
        node.clock[0] = clock
    return cascade_keeper.keep_round(node.root, clock=node.clock[0], log=node.lines.append, failed=node.failed,
                                     **kw)


def publish(node, sid: str, texts=None) -> None:
    """pubmed's next snapshot, written and SIGNED: published."""
    prev = max(p.name[:-5] for p in (col.shard_dir_of(node.root, "pubmed") / "snapshots").glob("*.json")
               if not p.name.endswith(".sig.json"))
    import tests.hold.test_annotations_update as tu
    old = tu.TARGET_SNAPSHOT
    tu.TARGET_SNAPSHOT = prev
    try:
        target_next(node.root, node.target, texts or {"pubmed:2": f"two, as of {sid}"}, sid=sid)
    finally:
        tu.TARGET_SNAPSHOT = old
    d = col.shard_dir_of(node.root, "pubmed")
    sn.sign_snapshot(node.root, "pubmed", sn.load(d / shard_mod.snapshot_path(sid)), KEY, now=NOW)


def woken(node, name=NAME):
    return cascade.woken(node.root, name)


def with_b(node):
    """`pubmed-b`: a method collection that declares pubmed (its target)
    and, through its method's inputs, `pubmed-same-work`."""
    from tests.hold.conftest import PLATFORM
    from tests.hold.test_annotations import TARGET_SNAPSHOT
    ann.create(node.root, B, target_ref="pubmed", from_snapshot=TARGET_SNAPSHOT,
               claims=claims_file(a_claim(), a_claim("pubmed:2", "pubmed:4")), platform_image=PLATFORM,
               snapshot_id="2026-09", key=KEY, now=NOW, nonce="0e" * 16, log=lambda m: None)
    collection_file.set_values(B, {"method": READS_ANNOTATIONS.name})


# ---- the published marker ---------------------------------------------------------------

def test_the_signature_writes_the_published_marker_once(node):
    mk = cascade.read_published(node.root, NAME, "2026-09")
    assert mk is not None and mk["by"] == "signature", "the create signed its first snapshot: published"
    snap = sn.load(col.shard_dir_of(node.root, NAME) / shard_mod.snapshot_path("2026-09"))
    assert (mk["collection_id"], mk["snapshot"], mk["delta"]) == (
        snap.collection_id, snap.hash, {"added": 2, "changed": 0, "tombstoned": 0})
    before = cascade.published_path(node.root, NAME, "2026-09").read_bytes()
    sn.sign_snapshot(node.root, NAME, snap, KEY, now="2026-09-09T00:00:00Z")      # a second signature
    assert cascade.published_path(node.root, NAME, "2026-09").read_bytes() == before, "once per snapshot"
    assert not list(cascade.published_path(node.root, NAME, "x").parent.glob("*.tmp"))


def test_a_snapshot_signed_with_no_marker_is_backfilled_from_its_signature(node, monkeypatch):
    target_next(node.root, node.target, {"pubmed:2": "two, revised"}, sid="2026-09")
    d = col.shard_dir_of(node.root, "pubmed")
    snap = sn.load(d / shard_mod.snapshot_path("2026-09"))
    (d / shard_mod.snapshot_sig_path("2026-09")).write_bytes(       # signed before markers existed
        cx_json(sn.sidecar_add(None, snap.hash, KEY, now=NOW)))
    assert cascade.read_published(node.root, "pubmed", "2026-09") is None
    monkeypatch.setattr(sn, "load", lambda *a, **k: pytest.fail("a backfill re-reads no snapshot"))
    got = cascade.backfill(node.root, "pubmed", node.target.collection_id, "2026-09")
    assert (got["by"], got["snapshot"], got["collection_id"]) == ("backfill", snap.hash, node.target.collection_id)
    assert cascade.read_published(node.root, "pubmed", "2026-09") == got


def test_a_round_backfills_the_marker_and_the_backfilled_marker_wakes(node):
    target_next(node.root, node.target, {"pubmed:2": "two, revised"}, sid="2026-09")
    d = col.shard_dir_of(node.root, "pubmed")
    snap = sn.load(d / shard_mod.snapshot_path("2026-09"))
    (d / shard_mod.snapshot_sig_path("2026-09")).write_bytes(cx_json(sn.sidecar_add(None, snap.hash, KEY, now=NOW)))
    (got,) = keep(node)
    assert cascade.read_published(node.root, "pubmed", "2026-09")["by"] == "backfill"
    assert got["against"] == {"pubmed": "2026-09"} and woken(node)["by"] == {"pubmed": "2026-09"}


def test_an_unsigned_snapshot_wakes_nothing(node):
    target_next(node.root, node.target, {"pubmed:2": "two, revised"}, sid="2026-09")
    assert keep(node) == [] and woken(node) is None and node.ran == [], \
        "not published: a partial, running or unsigned update triggers nothing"


def cx_json(doc) -> bytes:
    from app.hold.codex import canonical_json
    return canonical_json(doc)


# ---- woken, run, done; the gate ----------------------------------------------------------

def test_a_published_snapshot_wakes_its_annotations_and_the_old_run_gate_opens(node):
    assert keep(node) == [] and woken(node) is None, "nothing published past the claims' pin"
    publish(node, "2026-09")
    gate = cascade.old_run_gate(node.root, "pubmed", node.target, "2026-09")
    assert not gate.open and gate.waiting == (NAME,)
    (got,) = keep(node)
    assert (got["collection"], got["snapshot_id"], got["against"]) == (NAME, "2026-10", {"pubmed": "2026-09"})
    assert node.ran == [f"annotations-recheck {NAME}"], "claims given by hand: re-checked"
    w = woken(node)
    assert (w["state"], w["sources"], w["by"], w["tries"]) == (
        "done", {node.target.collection_id: "2026-09"}, {"pubmed": "2026-09"}, 1)
    assert w["started"] and w["finished"] and w["error"] is None
    assert cascade.old_run_gate(node.root, "pubmed", node.target, "2026-09").open
    assert cascade.read_published(node.root, NAME, "2026-10") is not None, "its own update is published"
    assert any("re-checked against pubmed 2026-09: snapshot 2026-10" in m for m in node.lines), node.lines


def test_a_second_round_does_nothing(node):
    publish(node, "2026-09")
    keep(node)
    before = (cascade.woken_path(node.root, NAME).read_bytes(), sorted(
        p.name for p in (col.shard_dir_of(node.root, NAME) / "snapshots").iterdir()))
    node.ran.clear()
    assert keep(node) == [] and keep(node, NOVEMBER) == []
    assert node.ran == [], "a completed step is never redone for the same source snapshots"
    assert (cascade.woken_path(node.root, NAME).read_bytes(), sorted(
        p.name for p in (col.shard_dir_of(node.root, NAME) / "snapshots").iterdir())) == before


def test_one_snapshot_a_period_a_second_wake_waits_for_the_next(node):
    publish(node, "2026-09")
    keep(node)                                                   # 2026-10 written in October
    publish(node, "2026-10")
    assert keep(node) == []
    w = woken(node)
    assert w["state"] == "waiting" and w["says"] == "one snapshot a period: runs when 2026-11 opens"
    assert w["sources"] == {node.target.collection_id: "2026-10"} and w["by"] == {"pubmed": "2026-10"}
    assert w["last"]["sources"] == {node.target.collection_id: "2026-09"}, "the run before, kept"
    gate = cascade.old_run_gate(node.root, "pubmed", node.target, "2026-10")
    assert not gate.open, "a queued wake has not started: the old run stays"
    assert keep(node) == [] and node.ran == [f"annotations-recheck {NAME}"], "waits, runs nothing"
    (got,) = keep(node, NOVEMBER)
    assert (got["snapshot_id"], got["against"]) == ("2026-11", {"pubmed": "2026-10"})
    assert woken(node)["state"] == "done" and cascade.old_run_gate(node.root, "pubmed", node.target, "2026-10").open


def test_a_wake_waits_while_a_declared_source_has_an_update_in_flight(node):
    publish(node, "2026-09")
    mk = {"collection_id": node.target.collection_id, "period": "2026-10", "run": "20261001", "step": "fetch",
          "steps": {}, "failure": None}
    update_state.write(node.root, "pubmed", mk)
    assert keep(node) == []
    w = woken(node)
    assert w["state"] == "waiting" and "pubmed's update 2026-10 is at step fetch" in w["says"], w
    assert node.ran == []
    update_state.write(node.root, "pubmed", {**mk, "failure": {"step": "fetch", "tries": 3, "error": "x"}})
    (got,) = keep(node)
    assert got["against"] == {"pubmed": "2026-09"}, "a failure exhausted is not in flight: it runs"


def test_an_update_is_in_flight_until_its_snapshot_is_published(node):
    base = {"collection_id": "c", "period": "2026-09", "run": "r", "steps": {}, "failure": None}
    assert cascade.update_in_flight(node.root, "pubmed") is None, "no marker"
    for step, flying in (("due", True), ("fetch", True), ("records", True), ("snapshot", True), ("done", False)):
        update_state.write(node.root, "pubmed", {**base, "step": step})
        assert bool(cascade.update_in_flight(node.root, "pubmed")) is flying, step
    publish(node, "2026-09")
    for step in ("embed", "old-run"):
        update_state.write(node.root, "pubmed", {**base, "step": step})
        assert cascade.update_in_flight(node.root, "pubmed") is None, \
            f"{step}: its snapshot is published — the old-run step waits for the cascade, never the reverse"


# ---- order, transitivity, cycles ----------------------------------------------------------

def test_kahns_order_puts_every_collection_after_what_it_declares():
    edges = {"pubmed": {"a", "b"}, "a": {"b"}, "wikidata": {"c"}, "b": {"c"}}
    order, left = cascade.kahn({"pubmed", "wikidata", "a", "b", "c"}, edges)
    assert left == set() and order == ["pubmed", "a", "b", "wikidata", "c"]
    order, left = cascade.kahn({"x", "y", "z", "w"}, {"x": {"y"}, "y": {"z"}, "z": {"y", "w"}})
    assert order == ["x"] and left == {"y", "z", "w"}
    assert cascade.loops({"x": {"y"}, "y": {"z"}, "z": {"y", "w"}}, left) == [["z", "y", "z"]], \
        "z declares y, which declares z; w, after them, is on no loop"


def test_a_collection_runs_after_the_sources_it_declares_and_the_cascade_is_transitive(node):
    with_b(node)
    decl = cascade.declarations(node.root)
    assert decl.sources[B] == {"pubmed": "annotates", NAME: "input"}
    assert cascade.annotators(node.root, ann_id(node, NAME)) == [B], "a method's input wakes too (the gate's)"
    assert cascade.annotators(node.root, node.target.collection_id) == sorted([NAME, B])
    publish(node, "2026-09")
    got = keep(node)
    assert [g["collection"] for g in got] == [NAME, B], "Kahn's order: pubmed-b after what it reads"
    wb = woken(node, B)
    assert wb["sources"] == {node.target.collection_id: "2026-09", ann_id(node, NAME): "2026-10"}, \
        "ONE run, against every source's newest — pubmed-same-work's 2026-10, made in this very round"
    assert node.ran == [f"annotations-recheck {NAME}", f"annotations-method {B}", f"annotations-update {B}"]
    # pubmed-same-work's own update publishes: what declares IT is woken
    node.ran.clear()
    node.clock[0] = NOVEMBER
    ann.update(node.root, NAME, claims=claims_file(a_claim(), a_claim("pubmed:3", "pubmed:4")), log=lambda m: None)
    (got,) = keep(node)
    assert got["collection"] == B and woken(node, B)["by"] == {NAME: "2026-11"}
    assert node.ran == [f"annotations-method {B}", f"annotations-update {B}"]


def ann_id(node, name):
    return col.resolve(node.root, name)[1].collection_id


def test_a_downstream_collection_waits_for_its_source_annotations_own_wake(node):
    with_b(node)
    publish(node, "2026-09")
    keep(node)                                               # October: both ran
    publish(node, "2026-10")                                 # pubmed-same-work waits for November
    assert keep(node) == []
    assert woken(node)["state"] == "waiting"
    w = woken(node, B)
    assert w["state"] == "waiting" and f"waits for {NAME}, whose own update is waiting" in w["says"], w


def test_a_cycle_is_refused_where_it_is_declared_naming_the_loop(node):
    from app import louise
    with_b(node)
    loop = f"{NAME} → {B} → {NAME}"
    with pytest.raises(collection_file.SetRefused, match=loop):
        collection_file.set_values(NAME, {"method": READS_ANNOTATIONS.name})
    assert getattr(collection_file.load(NAME)[0], "method", None) is None, "nothing written"
    assert louise.main(["hold", "config", "apply", NAME, "--set", f"method={READS_ANNOTATIONS.name}"]) != 0


def test_a_cycle_written_by_hand_is_refused_by_the_keeper_and_said_once(node):
    with_b(node)
    p = Path(os.environ["CONFIG_DIR"]) / "collections" / f"{NAME}.toml"
    p.write_text(p.read_text() + f'method = "{READS_ANNOTATIONS.name}"\n')
    publish(node, "2026-09")
    assert keep(node) == [] and keep(node) == [] and node.ran == []
    said = [m for m in node.lines if "form a cycle" in m]
    assert len(said) == 1 and f"{B} → {NAME} → {B}" in said[0] and NAME in said[0] and B in said[0], said


def test_a_create_that_closes_a_loop_is_refused(node, monkeypatch):
    monkeypatch.setattr(cascade, "cycle_through", lambda root, name, **kw: [name, "x", name])
    with pytest.raises(ann.AnnotationsError, match="new-one → x → new-one"):
        make(node.root, name="new-one")
    assert not (node.root / "new-one" / "shard" / "snapshots").exists(), "refused before a byte is written"


# ---- failures, crashes, stops -------------------------------------------------------------

def test_a_child_killed_mid_run_is_tried_again_with_the_same_sources(node):
    publish(node, "2026-09")
    node.kill["next"] = 1
    assert keep(node) == []
    w = woken(node)
    assert w["state"] == "failed" and "tried again at the next pass (1 of 3)" in w["says"] and w["tries"] == 1
    assert cascade.old_run_gate(node.root, "pubmed", node.target, "2026-09").open, "failed counts as started"
    (got,) = keep(node)
    assert got["against"] == {"pubmed": "2026-09"} and woken(node)["state"] == "done" and woken(node)["tries"] == 2


def test_a_run_the_worker_died_during_resumes_and_three_deaths_fail_it_for_good(node):
    publish(node, "2026-09")
    sources = {node.target.collection_id: "2026-09"}
    cascade.write_woken(node.root, NAME, {"sources": sources, "state": "running", "started": NOW, "finished": None,
                                          "error": None, "tries": 1, "by": {"pubmed": "2026-09"}})
    publish(node, "2026-10")                              # a newer one meanwhile: the step resumes as it was
    (got,) = keep(node)
    assert got["against"] == {"pubmed": "2026-09"} and woken(node)["crashes"] == 1
    assert any("resumed: the worker stopped during it" in m for m in node.lines)
    # a run that takes the worker down, again and again
    cascade_keeper.reset()
    cascade.write_woken(node.root, NAME, {"sources": {node.target.collection_id: "2026-10"}, "state": "running",
                                          "started": NOW, "finished": None, "error": None, "tries": 3,
                                          "crashes": 2, "by": {"pubmed": "2026-10"}})
    node.ran.clear()
    assert keep(node, NOVEMBER) == []
    w = woken(node)
    assert w["state"] == "failed" and "the worker stopped 3 times" in w["error"] and node.ran == []
    cascade_keeper.reset()                                # a worker started afresh: not tried again
    node.failed.clear()
    assert keep(node, NOVEMBER) == [] and node.ran == []
    publish(node, "2026-11")                              # until a source moves
    (got,) = keep(node, NOVEMBER)
    assert got["against"] == {"pubmed": "2026-11"}


def test_a_stop_leaves_the_run_to_resume_uncounted(node, monkeypatch):
    publish(node, "2026-09")
    real, stops = am.derive_from_delta, [1]

    def stopped_once(*a, **k):
        if stops:
            stops.pop()
            raise am.Stopped("the worker stopped during annotations-recheck")
        return real(*a, **k)
    monkeypatch.setattr(am, "derive_from_delta", stopped_once)
    assert keep(node) == []
    w = woken(node)
    assert (w["state"], w["stopped"]) == ("running", True) and "resumes at its next start" in w["says"]
    assert cascade.old_run_gate(node.root, "pubmed", node.target, "2026-09").open, "it has started"
    cascade_keeper.reset()                                # the next start
    (got,) = keep(node)
    assert got["against"] == {"pubmed": "2026-09"} and not woken(node).get("crashes"), "a stop is no crash"
    assert any("resumed: stopped with the worker" in m for m in node.lines)


# ---- a method collection, due or woken, runs once ---------------------------------------------

def test_a_methods_due_and_a_wake_in_the_same_period_run_once(node):
    collection_file.set_values(NAME, {"method": READS_ANNOTATIONS.name})
    publish(node, "2026-09")                  # both: the method is due (target moved) and the wake
    (got,) = keep(node)
    assert got["collection"] == NAME and node.ran == [f"annotations-method {NAME}", f"annotations-update {NAME}"]
    assert keep(node) == [] and keep(node, NOVEMBER) == [], "once"
    assert any("derived with reads-annotations against pubmed 2026-09" in m for m in node.lines), node.lines


MONTHLY = am.Method("monthly-test", "pubmed", _b_produce, "a monthly test method", cadence="monthly")


def test_a_monthly_methods_period_and_a_wake_in_one_period_run_once(node, monkeypatch):
    monkeypatch.setitem(am.METHODS, MONTHLY.name, MONTHLY)
    collection_file.set_values(NAME, {"method": MONTHLY.name})
    publish(node, "2026-09")                  # October: its period is open AND pubmed woke it
    (got,) = keep(node)
    assert got["snapshot_id"] == "2026-10" and keep(node) == [], "once"
    publish(node, "2026-10")                  # woken again in October: its period's snapshot is written
    assert keep(node) == [] and woken(node)["says"] == "one snapshot a period: runs when 2026-11 opens"
    node.ran.clear()
    (got,) = keep(node, NOVEMBER)             # November: due by its period, and the wake pending
    assert got["snapshot_id"] == "2026-11" and node.ran == [f"annotations-method {NAME}", f"annotations-update {NAME}"]
    assert keep(node, NOVEMBER) == [] and woken(node)["state"] == "done"


# ---- shown ------------------------------------------------------------------------------

def test_the_lines_say_who_woke_what_in_plain_words(node, monkeypatch, capsys):
    from app import louise, node_show
    from app.hold import update as update_mod
    monkeypatch.setattr(update_mod, "_local_hhmm", lambda iso: "03:12" if iso == "2026-11-03T02:12:00Z" else "03:41")
    publish(node, "2026-09")
    src = cascade.shown(node.root, "pubmed", node.target)
    assert src["says"] == "woke 0 of 1 annotations (snapshot 2026-09)"
    cascade.write_woken(node.root, NAME, {"sources": {node.target.collection_id: "2026-09"}, "state": "running",
                                          "started": "2026-11-03T02:12:00Z", "finished": None, "error": None,
                                          "tries": 1, "by": {"pubmed": "2026-09"}})
    assert cascade.shown(node.root, NAME, col.resolve(node.root, NAME)[1])["says"] == \
        "woken by pubmed 2026-09 — running, started 03:12"
    assert cascade.shown(node.root, "pubmed", node.target)["says"] == "woke 1 annotations (snapshot 2026-09)"
    keep(node)
    assert cascade.shown(node.root, NAME, col.resolve(node.root, NAME)[1])["says"] == \
        "woken by pubmed 2026-09 — done 03:41"
    w = {**woken(node), "state": "waiting", "says": "one snapshot a period: runs when 2026-11 opens"}
    assert cascade.says_woken(w) == "woken by pubmed 2026-09 — waiting: one snapshot a period: runs when 2026-11 opens"
    capsys.readouterr()
    assert louise.main(["hold", "collection", "show", "pubmed", "--root", str(node.root)]) == 0
    assert json.loads(capsys.readouterr().out)["cascade"]["says"] == "woke 1 annotations (snapshot 2026-09)"
    assert {r["name"]: r["says"] for r in node_show._cascade()} == {
        "pubmed": "woke 1 annotations (snapshot 2026-09)", NAME: "woken by pubmed 2026-09 — done 03:41"}
    # the Library's cards: the same lines, and the template draws them
    from app import web
    assert web._cascade_view(SimpleNamespace(name="pubmed", codex=node.target))["says"] == \
        "woke 1 annotations (snapshot 2026-09)"
    card = web._cascade_view(SimpleNamespace(name=NAME, codex=col.resolve(node.root, NAME)[1]))
    assert (card["says"], card["state"]) == ("woken by pubmed 2026-09 — done 03:41", "done")
    source = web.TEMPLATES.env.loader.get_source(web.TEMPLATES.env, "library.html")[0]
    web.TEMPLATES.env.parse(source)
    assert "c.cascade.says" in source and "a.cascade.says" in source


# ---- the cost of a round ------------------------------------------------------------------

def _aged(*dirs: Path) -> None:
    """Every file and directory older than the racy window: what a node's
    are between two rounds."""
    t = time.time() - 30
    for d in dirs:
        for p in [d, *d.rglob("*")]:
            os.utime(p, (t, t), follow_symlinks=False)


def _steady(node, monkeypatch, rounds=30) -> tuple[float, dict[str, int]]:
    from app.pack import records as pack_records
    counts = {"parts": 0, "sqlite": 0, "listings": 0}
    real_iter, real_connect, real_iterdir = pack_records.iter_records, sqlite3.connect, Path.iterdir

    def iter_records(*a, **k):
        counts["parts"] += 1
        return real_iter(*a, **k)

    def connect(*a, **k):
        counts["sqlite"] += 1
        return real_connect(*a, **k)

    def iterdir(self):
        if self.name == "snapshots":
            counts["listings"] += 1
        return real_iterdir(self)
    keep(node)
    keep(node)                                              # settled
    monkeypatch.setattr(pack_records, "iter_records", iter_records)
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    t0 = time.perf_counter()
    for _ in range(rounds):
        assert keep(node) == []
    dt = (time.perf_counter() - t0) / rounds
    monkeypatch.undo()
    return dt, counts


def test_a_rounds_cost_does_not_grow_with_the_snapshots_or_the_claims(tmp_path, node, monkeypatch):
    """scheduling.md §6, 7: a COST test. A settled round reads markers only —
    no part, no manifest, no listing of a snapshots directory — whatever the
    collections hold: 1 snapshot and 2 claims against 120 snapshots and 600
    claims (measured numbers in the commit message)."""
    publish(node, "2026-09")
    keep(node)
    _aged(node.root, Path(os.environ["CONFIG_DIR"]))
    small, c_small = _steady(node, monkeypatch)
    # the large node: 120 snapshots held by the source, 600 claims
    big = tmp_path / "big"
    root = big / "louise" / "collections"
    root.mkdir(parents=True)
    (big / "config" / "collections").mkdir(parents=True)
    monkeypatch.setenv("LOUISE_ROOT", str(big / "louise"))
    monkeypatch.setenv("CONFIG_DIR", str(big / "config"))
    texts = [f"text {i}" for i in range(601)]
    target, _ = a_target(root, texts=texts)
    make(root, claims=claims_file(*(a_claim(f"pubmed:{i}", f"pubmed:{i + 1}") for i in range(1, 601))))
    d = col.shard_dir_of(root, "pubmed") / "snapshots"
    for y in range(2014, 2024):
        for m in range(1, 13):
            (d / f"{y}-{m:02d}.json").write_text("{}")         # older ones: the listing grows
    node.root, node.target = root, target
    publish(node, "2026-09")
    keep(node)
    _aged(root, big / "config")
    large, c_large = _steady(node, monkeypatch)
    assert c_small == c_large == {"parts": 0, "sqlite": 0, "listings": 0}, (c_small, c_large)
    assert large < max(3 * small, small + 0.002), (small, large)
    print(f"\nround: {small * 1e3:.3f} ms (1 snapshot, 2 claims) vs {large * 1e3:.3f} ms "
          f"(121 snapshots, 600 claims)")
