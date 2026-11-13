"""tests/hold/test_annotation_methods.py — an annotations collection whose
claims are a method's output is updated by the node itself (2026-11-12;
operator: "Can the annotations be updated without manually uploading
something???"; app/hold/annotation_methods.py): the method recorded in its
collection file, run when the collection it annotates holds a newer snapshot,
its whole output given to `update` with what the target's snapshot does not
hold yet set aside; the snapshot written whole and signed under the stop
guard; wikipedia's claims rendered byte for byte as `json.dumps` renders them."""
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from app import collection_file, collections as collections_mod, wiki_versions
from app.hold import annotation_methods as am, annotations as ann, collection as col, shard as shard_mod
from tests.hold.conftest import KEY, NOW
from tests.hold.test_annotations import TARGET_SNAPSHOT, a_claim, a_target, claims_file, make
from tests.hold.test_annotations_update import NAME, OCTOBER, claims_by_uid, target_next

SEPTEMBER = datetime(2026, 9, 20, tzinfo=timezone.utc)


# ---- wikipedia's claims, rendered -------------------------------------------------

def test_wikipedias_claim_lines_are_json_dumps_byte_for_byte():
    from app.plugins.wikipedia import ids
    rng = np.random.default_rng(7)
    n = 5_000
    langs = list(ids.LANG_ORDINAL)
    lang = rng.integers(0, len(langs), n)
    page = rng.integers(1, 10 ** 8, n)
    nid = np.array([ids.native_id_of(langs[o], int(p), archive=bool(p % 2)) for o, p in zip(lang, page)],
                   dtype=np.int64)
    tokens = rng.integers(0, 9_000, n)
    tokens[::5] = 0                                  # lengths not known yet
    m = wiki_versions.arrays(rng.integers(1, 600, n), lang, tokens, nid)
    slow = b"".join(json.dumps(c, ensure_ascii=False, sort_keys=True).encode() + b"\n"
                    for c in wiki_versions.claims(m))
    assert slow.count(b"\n") > 1_000
    old = wiki_versions.LINES_BATCH
    try:
        wiki_versions.LINES_BATCH = 333              # batches cut anywhere
        assert b"".join(wiki_versions.claim_lines(m)) == slow
    finally:
        wiki_versions.LINES_BATCH = old
    assert b"".join(wiki_versions.claim_lines(wiki_versions.arrays(*(np.zeros(0, np.int64),) * 4))) == b""


# ---- update: what the target's snapshot does not hold yet is set aside ------------

@pytest.mark.parametrize("in_memory", [False, True])
def test_an_end_the_target_does_not_hold_yet_is_set_aside_not_refused(tmp_path, in_memory):
    a_target(tmp_path)
    make(tmp_path)
    data = claims_file(a_claim(), a_claim("pubmed:9", "pubmed:1"), a_claim("pubmed:2", "pubmed:4"))
    with pytest.raises(ann.AnnotationsError, match="pubmed:9"):
        ann.update(tmp_path, NAME, claims=data, key=KEY, now=NOW, log=lambda m: None, clock=OCTOBER,
                   _in_memory=in_memory)
    got = ann.update(tmp_path, NAME, claims=data, key=KEY, now=NOW, log=lambda m: None, clock=OCTOBER,
                     unheld="drop", _in_memory=in_memory)
    assert got.dropped == ("line 2: pubmed:9",), "named, as a re-check names one"
    claims = claims_by_uid(tmp_path, got.snapshot)
    assert sorted((c["subject"], c["object"]) for c in claims.values()) == [
        ("pubmed:1", "pubmed:2"), ("pubmed:2", "pubmed:4")]
    # the claims file installed is the method's whole output: the claim set
    # aside is checked again at the next update, and comes in once held
    assert b"pubmed:9" in (tmp_path / NAME / "origin" / "claims.jsonl").read_bytes()


def test_the_streamed_and_in_memory_drops_write_the_same_bytes(tmp_path):
    data = claims_file(a_claim(), a_claim("pubmed:9", "pubmed:1"), a_claim("pubmed:2", "pubmed:4"))
    out = {}
    for mode in (False, True):
        root = tmp_path / str(mode)
        a_target(root)
        make(root)
        got = ann.update(root, NAME, claims=data, key=KEY, now=NOW, log=lambda m: None, clock=OCTOBER,
                         unheld="drop", _in_memory=mode)
        d = col.shard_dir_of(root, NAME)
        out[mode] = (got.to_dict(), {p.relative_to(d).as_posix(): p.read_bytes()
                                     for p in sorted(d.rglob("*")) if p.is_file()})
    assert out[False] == out[True]


def test_a_private_claims_file_is_linked_not_copied(tmp_path):
    a_target(tmp_path)
    make(tmp_path)
    src = tmp_path / "mine.jsonl"
    src.write_bytes(claims_file(a_claim(), a_claim("pubmed:2", "pubmed:4")))
    ann.update(tmp_path, NAME, claims=src, key=KEY, now=NOW, log=lambda m: None, clock=OCTOBER, private=True)
    assert os.stat(src).st_ino == os.stat(tmp_path / NAME / "origin" / "claims.jsonl").st_ino, \
        "the very bytes, at no copy (RULE 1)"


def test_the_snapshot_is_written_whole_and_signed_inside_the_guard(tmp_path):
    a_target(tmp_path)
    make(tmp_path)
    d = col.shard_dir_of(tmp_path, NAME)
    seen = []

    from contextlib import contextmanager

    @contextmanager
    def guard():
        seen.append(("in", (d / shard_mod.snapshot_path("2026-10")).exists()))
        yield
        seen.append(("out", (d / shard_mod.snapshot_path("2026-10")).exists(),
                     (d / shard_mod.snapshot_sig_path("2026-10")).exists()))
    ann.update(tmp_path, NAME, claims=claims_file(a_claim()), key=KEY, now=NOW, log=lambda m: None,
               clock=OCTOBER, guard=guard)
    assert seen == [("in", False), ("out", True, True)], "the snapshot and its signature, both inside"
    assert not list((d / "snapshots").glob(".*.tmp")), "no temporary left beside it"


# ---- the method, recorded in the collection file ----------------------------------

def _fake_produce(collection, out: Path, log=print) -> dict:
    """A method over a pubmed target: claims about whatever it is told."""
    Path(out).write_bytes(claims_file(*FAKE_CLAIMS))
    return {"claims": len(FAKE_CLAIMS)}


FAKE_CLAIMS: list = []
FAKE = am.Method("fake-pubmed", "pubmed", _fake_produce, "a test method")


@pytest.fixture
def node(tmp_path, monkeypatch):
    """The node's own root and config, an annotations collection made by
    hand, and the fake method known."""
    root = tmp_path / "louise" / "collections"
    root.mkdir(parents=True)
    (tmp_path / "config" / "collections").mkdir(parents=True)
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path / "louise"))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setitem(am.METHODS, FAKE.name, FAKE)
    monkeypatch.setattr(am, "_target_collection", lambda name: type("C", (), {"name": name, "plugin": "pubmed"})())
    _set_claims(a_claim(), a_claim("pubmed:3", "pubmed:1", confidence="0.73"))
    target, _ = a_target(root)
    make(root)
    return root, target


def _set_claims(*claims):
    FAKE_CLAIMS[:] = list(claims)


def test_the_method_is_a_key_of_an_annotations_collections_file_and_survives_a_rewrite(node):
    root, _ = node
    got = collection_file.set_values(NAME, {"method": FAKE.name})
    assert got["set"] == {"method": FAKE.name}
    (c,) = collections_mod.load_annotations()
    assert c.method == FAKE.name
    collection_file.set_values(NAME, {"title": "Same works"})
    (c,) = collections_mod.load_annotations()
    assert (c.method, c.title) == (FAKE.name, "Same works"), "carried through another key's rewrite"
    with pytest.raises(collection_file.SetRefused, match="method is one of"):
        collection_file.set_values(NAME, {"method": "made-up"})
    collection_file.set_values(NAME, {"method": ""})
    (c,) = collections_mod.load_annotations()
    assert c.method is None, "empty: none, the claims given by hand"


def test_a_records_collections_file_refuses_a_method(node):
    _, target = node
    path = collections_mod.config_toml_path("pubmed")
    path.write_text(f'codex = "{target.collection_id}"\nmethod = "wikipedia-versions"\n')
    with pytest.raises(ValueError, match="annotations collection's key"):
        collections_mod._load_one(path)


def test_due_when_the_target_holds_a_newer_snapshot_and_the_period_is_open(node):
    root, target = node
    d = am.due(root, NAME, FAKE.name, clock=OCTOBER)
    assert (d.due, d.pinned, d.target_snapshot) == (False, TARGET_SNAPSHOT, TARGET_SNAPSHOT), \
        "read from the claims themselves: no marker yet, no wasted first pass"
    target_next(root, target, {"pubmed:2": "two, revised"}, sid="2026-09")
    d = am.due(root, NAME, FAKE.name, clock=OCTOBER)
    assert d.due and (d.pinned, d.target_snapshot) == (TARGET_SNAPSHOT, "2026-09")
    early = am.due(root, NAME, FAKE.name, clock=SEPTEMBER)
    assert not early.due and "once its period opens" in early.reason, "one update a period (09 §9.4)"


def test_the_worker_derives_once_the_target_moves_and_not_again(node):
    root, target = node
    collection_file.set_values(NAME, {"method": FAKE.name})
    lines: list[str] = []

    def run(root_, name, method, stop=None):
        return am.derive(root_, name, method, key=KEY, now=NOW, log=lines.append, clock=OCTOBER)
    assert am.maintain(root, log=lines.append, clock=OCTOBER, run=run) == [], "nothing due"
    _set_claims(a_claim(), a_claim("pubmed:3", "pubmed:1", confidence="0.73"), a_claim("pubmed:2", "pubmed:4"))
    target_next(root, target, {"pubmed:2": "two, revised", "pubmed:3": None}, sid="2026-09")
    (got,) = am.maintain(root, log=lines.append, clock=OCTOBER, run=run)
    assert got["snapshot_id"] == "2026-10" and got["against"] == {"pubmed": "2026-09"}
    assert got["dropped_count"] == 1, "pubmed:3 withdrawn by the target: set aside, named"
    from app.share import seeder
    claims = claims_by_uid(root, seeder.current_snapshot(col.shard_dir_of(root, NAME)))
    assert {c["target_snapshot"] for c in claims.values()} == {"2026-09"}, "every claim re-pinned"
    assert sorted((c["subject"], c["object"]) for c in claims.values()) == [
        ("pubmed:1", "pubmed:2"), ("pubmed:2", "pubmed:4")]
    mk = am.read_marker(root, NAME)
    assert (mk["method"], mk["snapshot"], mk["against"], mk["set_aside"]) == (
        FAKE.name, "2026-10", {"pubmed": "2026-09"}, 1)
    assert any("derived with fake-pubmed against pubmed 2026-09" in m for m in lines)
    assert am.maintain(root, log=lines.append, clock=OCTOBER, run=run) == [], "not due again"


def test_a_failure_is_said_once_and_a_killed_child_is_tried_again(node):
    root, target = node
    collection_file.set_values(NAME, {"method": FAKE.name})
    target_next(root, target, {"pubmed:2": "two, revised"}, sid="2026-09")
    calls, lines, failed = [], [], {}

    def killed(root_, name, method, stop=None):
        calls.append(name)
        raise am.ChildFailed(-9, "annotations-method exited -9 (killed: out of memory?)")
    for _ in range(am.KILLED_TRIES + 2):
        am.maintain(root, log=lines.append, clock=OCTOBER, run=killed, failed=failed)
    assert len(calls) == am.KILLED_TRIES, "tried again, then left for the target's next snapshot"
    assert sum("failed" in m for m in lines) == 1, "said once"

    calls.clear(); lines.clear(); failed.clear()

    def refused(root_, name, method, stop=None):
        calls.append(name)
        raise ann.AnnotationsError("no")
    for _ in range(3):
        am.maintain(root, log=lines.append, clock=OCTOBER, run=refused, failed=failed)
    assert len(calls) == 1 and sum("failed" in m for m in lines) == 1


def test_a_collection_without_a_method_keeps_the_manual_path(node):
    root, target = node
    target_next(root, target, {"pubmed:2": "two, revised"}, sid="2026-09")
    assert am.maintain(root, clock=OCTOBER, run=lambda *a, **k: pytest.fail("ran")) == []


def test_a_method_of_another_plugin_is_refused(node):
    root, _ = node
    with pytest.raises(ann.AnnotationsError, match="annotates a wikipedia collection"):
        am.due(root, NAME, "wikipedia-versions")


# ---- the processes of their own -----------------------------------------------------

def _child_ok():
    sys.exit(0)


def _child_sleeps():
    time.sleep(60)


def _child_killed():
    os.kill(os.getpid(), signal.SIGKILL)


def _child_holds_then_exits(flag: str):
    import threading
    from app import parallel
    am._stop_outside_last_writes()
    parallel.watch_parent()                          # a second thread, as in the update's child
    threading.Thread(target=time.sleep, args=(60,), daemon=True).start()
    with am._held_against_stop():
        Path(flag).write_text("in")
        time.sleep(1.5)
        Path(flag).write_text("done")
    time.sleep(60)


def _child_stops_at_once(flag: str):
    am._stop_outside_last_writes()
    Path(flag).write_text("in")
    time.sleep(60)


def test_a_child_is_waited_for_terminated_at_a_stop_and_its_kill_is_said(tmp_path):
    am._run(_child_ok, (), stop=None, poll_s=0.05, label="ok")
    with pytest.raises(am.ChildFailed) as e:
        am._run(_child_killed, (), stop=None, poll_s=0.05, label="killed")
    assert e.value.exitcode == -signal.SIGKILL and "out of memory" in str(e.value)
    t0 = time.monotonic()
    with pytest.raises(am.Stopped):
        am._run(_child_sleeps, (), stop=lambda: time.monotonic() - t0 > 0.5, poll_s=0.05, label="sleeps")
    assert time.monotonic() - t0 < 20


def test_a_stop_waits_for_the_last_writes_held_against_it(tmp_path):
    flag = tmp_path / "flag"
    with pytest.raises(am.Stopped):
        am._run(_child_holds_then_exits, (str(flag),), stop=lambda: flag.exists(), poll_s=0.05, label="held")
    assert flag.read_text() == "done", "the SIGTERM took effect only once the block ended"
    flag.unlink()
    t0 = time.monotonic()
    with pytest.raises(am.Stopped):
        am._run(_child_stops_at_once, (str(flag),), stop=lambda: flag.exists(), poll_s=0.05, label="free")
    assert time.monotonic() - t0 < 10, "outside the last writes a stop is immediate"


def test_the_isolated_derivation_wires_the_method_then_the_update(node, monkeypatch):
    """`derive_isolated`'s two children, their functions called here (a
    spawned child would not see the test's method): the method's output,
    then the update with it, the result read back from the workspace."""
    from app import parallel
    root, target = node
    collection_file.set_values(NAME, {"method": FAKE.name})
    _set_claims(a_claim(), a_claim("pubmed:2", "pubmed:4"))
    target_next(root, target, {"pubmed:2": "two, revised"}, sid="2026-09")
    monkeypatch.setattr(parallel, "watch_parent", lambda: None)
    monkeypatch.setattr(am, "_stop_outside_last_writes", lambda: None)
    real_update = ann.update
    monkeypatch.setattr(ann, "update", lambda *a, **k: real_update(*a, **{**k, "clock": OCTOBER}))
    ran = []

    def in_process(target_fn, args, *, stop, poll_s, label, error=None):
        ran.append(label)
        with pytest.raises(SystemExit) as e:
            target_fn(*args)
        if e.value.code:
            raise am.ChildFailed(e.value.code, error.read_text())
    monkeypatch.setattr(am, "_run", in_process)
    got = am.derive_isolated(root, NAME, FAKE.name)
    assert ran == [f"annotations-method {NAME}", f"annotations-update {NAME}"]
    assert (got["snapshot_id"], got["against"], got["added"]) == ("2026-10", {"pubmed": "2026-09"}, 1)
    assert am.read_marker(root, NAME)["snapshot"] == "2026-10"
    assert not list(root.glob(".annotations-method.*")), "the workspace goes with the pass"

    # a refusal in a child reaches the worker's line by its words, not an exit code
    target_next(root, target, {"pubmed:2": "two, again"}, sid="2026-10")
    monkeypatch.setattr(ann, "update", lambda *a, **k: real_update(*a, **{**k, "clock": OCTOBER}))
    with pytest.raises(am.ChildFailed, match="once its period opens"):
        am.derive_isolated(root, NAME, FAKE.name)


def test_the_command_says_whether_it_is_due_and_derives(node, monkeypatch, capsys):
    from app import louise
    root, target = node
    clock = type("D", (), {"now": staticmethod(lambda tz=None: OCTOBER)})
    monkeypatch.setattr(ann, "datetime", clock)
    monkeypatch.setattr(am, "datetime", clock)
    base = ["hold", "annotations", "derive", NAME, "--root", str(root)]
    assert louise.main(base + ["--dry-run"]) != 0
    assert "records no method" in capsys.readouterr().err, "says what would supply it"
    collection_file.set_values(NAME, {"method": FAKE.name})
    assert louise.main(base + ["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["due"] is False
    _set_claims(a_claim(), a_claim("pubmed:2", "pubmed:4"))
    target_next(root, target, {"pubmed:2": "two, revised"}, sid="2026-09")
    assert louise.main(base + ["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["due"] is True
    assert not (col.shard_dir_of(root, NAME) / "snapshots/2026-10.json").exists(), "a dry run runs nothing"
    assert louise.main(base) == 0
    got = json.loads(capsys.readouterr().out)
    assert (got["snapshot_id"], got["against"], got["produced"]) == ("2026-10", {"pubmed": "2026-09"}, {"claims": 2})


def test_config_apply_sets_a_method_and_finds_the_annotations_collection(node, capsys):
    """München, 2026-11-13: `hold config apply wikipedia-articles --set
    method=wikipedia-versions` wrote the file, then said "no such collection
    on this node" and exited 2 — the apply looked only among the collections
    with a space. It finds the annotations collection, says nothing is
    applied in a state database and when the worker reads the file, and
    `config show` lists its method."""
    from app import louise
    assert louise.main(["hold", "config", "apply", NAME, "--set", f"method={FAKE.name}"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["written"]["set"] == {"method": FAKE.name}
    (row,) = out["applied"]
    assert (row["name"], row["annotations"], row["method"]) == (NAME, True, FAKE.name)
    assert "annotations pass" in row["picked_up"] and out["refused"] == []
    assert louise.main(["hold", "config", "show", NAME]) == 0
    (shown,) = json.loads(capsys.readouterr().out)["collections"]
    assert shown["values"]["method"]["value"] == FAKE.name and shown["values"]["method"]["tier"] == "files"
    assert louise.main(["hold", "config", "apply", "nothing-here"]) == 2


MATCHER = {"kind": "matcher", "id": "louise-classical-matcher", "version": 1}     # a_claim's method
MINE = am.Method("fake-matcher", "pubmed", _fake_produce, "remakes the matcher's claims",
                 types=("same-work",), claim_method=MATCHER, label="update by itself")
OTHER = am.Method("fake-other", "pubmed", _fake_produce, "makes wikidata's claims",
                  types=("same-work",), claim_method={"kind": "wikidata"})


def test_a_method_is_never_handed_claims_it_did_not_make(node, monkeypatch):
    """München, 2026-11-13 (the Library's checkbox): a method's output is the
    WHOLE claim set from then on, so ticking it on a collection of other
    claims would withdraw them. `not_fitting` — asked by the file's writer,
    the worker's due check and the Library — refuses unless the method made
    the current snapshot or the claims are of its kind."""
    monkeypatch.setitem(am.METHODS, MINE.name, MINE)
    monkeypatch.setitem(am.METHODS, OTHER.name, OTHER)
    root, _ = node
    wiki = am.METHODS["wikipedia-versions"]
    assert wiki.claim_method == wiki_versions.METHOD and wiki.types == ("same-work",) and wiki.label
    why = am.not_fitting(root, NAME, OTHER)
    assert why is not None and "are not fake-other's" in why and "'same-work' claim by" in why, why
    with pytest.raises(collection_file.SetRefused, match="are not fake-other's"):
        collection_file.set_values(NAME, {"method": OTHER.name})
    (c,) = collections_mod.load_annotations()
    assert c.method is None, "refused, nothing written"
    with pytest.raises(ann.AnnotationsError, match="are not fake-other's"):
        am.due(root, NAME, OTHER.name)          # a file edited by hand: the worker refuses too
    assert am.not_fitting(root, NAME, MINE) is None
    collection_file.set_values(NAME, {"method": MINE.name})
    assert collections_mod.load_annotations()[0].method == MINE.name
    # what the method made itself fits whatever its claims say
    tree = col.shard_dir_of(root, NAME)
    am._write_marker(root, NAME, {"method": OTHER.name, "snapshot": am.newest_snapshot_id(tree)})
    assert am.not_fitting(root, NAME, OTHER) is None
