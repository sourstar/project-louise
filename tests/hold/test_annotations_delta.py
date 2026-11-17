"""tests/hold/test_annotations_delta.py — an annotations update FROM THE
DELTA (docs/notes/collection-updates.md §4, operator 2026-11-17: "from the
delta, never a full re-scan"; app/hold/annotations_delta.py): the records
the target's snapshots since the claims' pin do not name are re-pinned from
the claims' own pins, never read, and the update writes the tree the whole
update writes byte for byte, and returns and says the same — for a re-check
of claims given by hand, a new claims file, a method's output that names
records the target's snapshot does not hold yet, a chain that skipped
snapshots, graph claims on two targets, on the pool; and where the delta
cannot be read, every record is read, and it says why."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.hold import annotations as ann
from app.hold import annotations_stream as st
from app.hold import collection as col
from app.hold import shard as shard_mod
from app.hold import snapshot as sn
from app.pack import records as pack_records
from tests.hold.conftest import KEY, NOW, PLATFORM
from tests.hold.test_annotations import (
    TARGET_SNAPSHOT,
    a_claim,
    a_community,
    a_mention,
    a_relation,
    a_target,
    claims_file,
    two_targets,
)
from tests.hold.test_annotations_stream import a_pool, a_wide_target, star_claims, tree
from tests.hold.test_annotations_update import OCTOBER, target_next

NAME = "same"
NOVEMBER = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def served_always(monkeypatch):
    """The fixtures' targets are small and their deltas wide: by the cost
    estimate every one would be read whole (`expected_frames`). Served
    whatever the estimate, so that the delta's path is what is compared —
    the estimate has its own test."""
    from app.hold import annotations_delta as ad
    monkeypatch.setattr(ad, "MAX_FRAMES", 1.0)


def create(root: Path, claims: bytes, **kw):
    if "targets" not in kw:
        kw.update(target_ref="pubmed", from_snapshot=TARGET_SNAPSHOT)
    return ann.create(root, NAME, claims=claims, platform_image=PLATFORM, snapshot_id="2026-09", key=KEY,
                      now=NOW, nonce="0f" * 16, log=lambda m: None, **kw)


def wide_move(root: Path, sid: str, *, changed=(), withdrawn=(), added=(), back=()):
    """`a_wide_target`'s next snapshot `sid` after its newest: the records
    `changed` with a new text, those `withdrawn` gone, those `back` (gone
    before) and `added` (new ids) held — a part re-issued only where it
    moved, its old version kept under its superseded name."""
    from app.hold import records as rec
    from app.share import seeder
    from tests.hold.test_annotations_stream import a_record
    d = col.shard_dir_of(root, "pubmed")
    prev = seeder.current_snapshot(d)
    changed, withdrawn = {f"pubmed:{i}" for i in changed}, {f"pubmed:{i}" for i in withdrawn}
    shards, before, after = [], {}, {}
    extra = {i % 2: [] for i in (0, 1)}
    for i in (*added, *back):
        extra[i % 2].append(i)
    for s in prev.shards:
        part = d / s.path
        old = list(pack_records.iter_records(part))
        before.update({r["uid"]: r["content_hash"] for r in old})
        new = []
        for r in old:
            if r["uid"] in withdrawn:
                continue
            if r["uid"] in changed:
                sections = [["Abstract", r["sections"][0][1] + f", revised in {sid}"]]
                r = dict(r, sections=sections, content_hash=rec.content_hash(r["title"], [tuple(x) for x in sections]))
            new.append(r)
        parity = 1 if s.slice == "year=2022" else 0
        if s.n == 1 and extra[parity]:
            new = sorted(new + [a_record(i) for i in extra[parity]], key=lambda r: int(r["native_id"]))
        after.update({r["uid"]: r["content_hash"] for r in new})
        if new == old:
            shards.append(s)
            continue
        staged = d / "staged.jsonl.zst"
        pack_records.write_shard(staged, new)
        raw = staged.read_bytes()
        root_ = shard_mod.pieces_root(raw)
        shard_mod.promote(staged, part, shard_dir=d, new_root=root_)
        shards.append(sn.Shard(slice=s.slice, n=s.n, root=root_,
                               records_root=rec.records_root(r["content_hash"] for r in new),
                               first_id=new[0]["native_id"], last_id=new[-1]["native_id"], records=len(new),
                               chunks=len(new), chunks_root=s.chunks_root, tokens=0, bytes=len(raw)))
    snap = sn.Snapshot(collection_id=prev.collection_id, snapshot_id=sid, previous=prev.hash,
                       shards=tuple(shards), delta=sn.compute_delta(before, after), files={})
    (d / shard_mod.snapshot_path(sid)).write_bytes(snap.canonical())
    return snap


def update(root: Path, claims, *, since, **kw):
    kw.setdefault("clock", OCTOBER)
    logs: list[str] = []

    def said() -> list[str]:
        return [m for m in logs if "from the delta" not in m and "every claimed record is read" not in m]
    try:
        got = ann.update(root, NAME, claims=claims, key=KEY, now=NOW, log=logs.append, since=since, **kw)
    except Exception as e:                  # noqa: BLE001 — compared, refusal for refusal
        return ("refused", type(e), str(e), said(), None)
    d = got.to_dict()
    form = d.pop("delta", None)
    return ("updated", d, got.dropped, said(), form)


def roots_of(tmp_path, build, first: bytes, *, before=None, create_kw=None) -> dict[str, Path]:
    roots = {k: tmp_path / k for k in ("whole", "delta")}
    for r in roots.values():
        r.mkdir()
        build(r)
        create(r, first, **(create_kw or {}))
        if before is not None:
            before(r)
    return roots


def same(roots: dict[str, Path], then: bytes | None, *, since=None, monkeypatch=None, path=None, **kw):
    """The whole update in one root, the update from the delta in the other:
    the same trees, results and log; the delta's own account returned."""
    whole = update(roots["whole"], then if path is None else path(roots["whole"]), since=None, **kw)
    if monkeypatch is not None:
        a_pool(monkeypatch)
    delta = update(roots["delta"], then if path is None else path(roots["delta"]), since={} if since is None
                   else since, **kw)
    assert delta[:4] == whole[:4], "the same result, the same refusal, the same log"
    assert tree(roots["delta"]) == tree(roots["whole"]), "the same bytes, every file"
    for r in roots.values():
        assert not list(r.glob(".annotations-*")), "no workspace left behind"
    return whole, delta[4]


# ---- a re-check of claims given by hand ------------------------------------------------

def test_a_re_check_reads_only_what_the_targets_delta_names_and_writes_the_same_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(ann, "SHARD_RECORDS", 400)
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 2400, per_part=300), star_claims(2400),
                     before=lambda r: wide_move(r, "2026-09", changed=range(3, 2400, 13),
                                                withdrawn=range(7, 2400, 101)))
    (_, result, dropped, _logs, _), form = same(roots, None, monkeypatch=monkeypatch)
    assert result["changed"] > 0 and result["tombstoned"] > 0 and len(dropped) > 5
    (t,) = form["targets"].values()
    assert (t["from"], t["to"], t["snapshots"]) == (TARGET_SNAPSHOT, "2026-09", 1)
    assert 0 < form["read"] < form["pinned"], "only what the delta names is read"
    assert form["read"] + form["pinned"] >= 1500


def test_a_chain_that_skipped_snapshots_reads_the_union_of_their_deltas(tmp_path, monkeypatch):
    """Pinned to 2026-08 while the target holds 2026-10: what 2026-09 and
    2026-10 name, together — a record gone in one and back in the other
    included, read and found unchanged."""
    monkeypatch.setattr(ann, "SHARD_RECORDS", 400)

    def moves(r):
        wide_move(r, "2026-09", changed=range(5, 1200, 17), withdrawn=(9, 13, 21, 2050))
        wide_move(r, "2026-10", changed=range(1201, 2400, 19), back=(9, 13), withdrawn=(30, 31))
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 2400, per_part=300), star_claims(2400), before=moves)
    (_, result, dropped, _, _), form = same(roots, None)
    (t,) = form["targets"].values()
    assert (t["from"], t["to"], t["snapshots"]) == (TARGET_SNAPSHOT, "2026-10", 2)
    assert result["against"] == {"pubmed": "2026-10"} and result["changed"] > 0
    gone = {u for d in dropped for u in d.split(": ", 1)[1].split(", ")}
    assert "pubmed:30" in gone and "pubmed:9" not in gone and "pubmed:13" not in gone, "9 and 13 are back, 30 gone"
    assert 0 < form["read"] < form["pinned"]


def test_a_methods_output_ahead_of_the_snapshot_set_aside_then_brought_in(tmp_path, monkeypatch):
    """The live-ahead case: a method's output names records the target's
    snapshot does not hold yet. Set aside the first time (`unheld="drop"`);
    the next snapshot holds them, and the delta's update — which no pin of
    theirs can serve — reads them and brings them in, as the whole does."""
    monkeypatch.setattr(ann, "SHARD_RECORDS", 400)
    ahead = [a_claim(f"pubmed:{i}", f"pubmed:{i + 1}") for i in range(2401, 2411, 2)]
    lines = star_claims(2400, mess=False) + claims_file(*ahead)
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 2400, per_part=300), star_claims(2400, mess=False))
    for r in roots.values():
        (r / "method.jsonl").write_bytes(lines)

    def path(r):
        return r / "method.jsonl"
    (_, first, dropped, _, _), _form = same(roots, None, path=path, unheld="drop", private=True)
    assert first["dropped_count"] == len(ahead) and all("pubmed:24" in d for d in dropped)
    for r in roots.values():
        (r / "method.jsonl").write_bytes(lines)          # the method's next run: the same output
        wide_move(r, "2026-09", changed=range(4, 2400, 23), added=range(2401, 2411))
    (_, then, dropped, _, _), form = same(roots, None, path=path, unheld="drop", private=True, clock=NOVEMBER)
    assert then["dropped_count"] == 0 and then["added"] == len(ahead), "the claims set aside come in"
    (t,) = form["targets"].values()
    assert t["from"] == TARGET_SNAPSHOT and t["to"] == "2026-09"


def test_a_new_claims_file_from_the_delta_writes_the_same_tree(tmp_path, monkeypatch):
    from tests.hold.test_annotations_update_stream import next_file
    monkeypatch.setattr(ann, "SHARD_RECORDS", 400)
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 2400, per_part=300), star_claims(2400),
                     before=lambda r: wide_move(r, "2026-09", changed=range(2, 2400, 11)))
    (_, result, _, _, _), form = same(roots, next_file(2400), monkeypatch=monkeypatch)
    assert result["added"] > 0 and result["tombstoned"] > 0 and form["pinned"] > form["read"] > 0


def test_a_refusal_from_the_delta_is_the_whole_runs(tmp_path):
    """A new claims file naming a record the target no longer holds is
    refused with the same words, whatever was read from the delta."""
    from tests.hold.test_annotations_update_stream import next_file
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 1200, per_part=300), star_claims(1200),
                     before=lambda r: wide_move(r, "2026-09", changed=range(2, 1200, 11), withdrawn=(6, 600)))
    (kind, _, words, _, _), _ = same(roots, next_file(1200))
    assert kind == "refused" and "does not hold" in words


def test_graph_claims_on_two_targets_from_the_delta_write_the_same_tree(tmp_path):
    """Mentions (a chunk end: always read, its text re-cut), a relation (its
    evidence chunk read), a community (its members pinned by no hash: read)
    and pairs; the entity target pinned by the claims whose first end is
    one of its records, and unmoved."""
    probe = tmp_path / "probe"
    probe.mkdir()
    text, _, _ = two_targets(probe)
    first = claims_file(a_mention(text), a_relation(text), a_community(), a_claim("pubmed:2", "pubmed:3"),
                        a_claim("pubmed:4", "pubmed:3"))
    targets = [("pubmed", TARGET_SNAPSHOT), ("wikidata", "2026-07")]
    roots = roots_of(tmp_path, two_targets, first, create_kw={"targets": targets, "annotator": "a graph builder"},
                     before=lambda r: target_next(r, col.read(r, "pubmed"), {"pubmed:2": "two, revised"}))
    (_, result, _, _, _), form = same(roots, None)
    assert result["changed"] == 3, "re-pinned: the claims whose first end is pubmed's (the entity target did not move)"
    t = form["targets"]
    assert (t["pubmed"]["from"], t["pubmed"]["to"], t["pubmed"]["changed"]) == (TARGET_SNAPSHOT, "2026-09", 1)
    assert (t["wikidata"]["from"], t["wikidata"]["to"], t["wikidata"]["snapshots"]) == ("2026-07", "2026-07", 0)
    assert form["read"] >= 2 and form["pinned"] >= 2


# ---- where the delta cannot be read: every record is, and it says why ---------------------

def _moved_twice(r, *, drop_between: bool = False):
    wide_move(r, "2026-09", changed=range(3, 600, 7))
    wide_move(r, "2026-10", changed=range(601, 1200, 7))
    if drop_between:
        (col.shard_dir_of(r, "pubmed") / shard_mod.snapshot_path("2026-09")).unlink()


def test_a_snapshot_missing_between_reads_every_record_and_says_why(tmp_path):
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 1200, per_part=300), star_claims(1200),
                     before=lambda r: _moved_twice(r, drop_between=True))
    (_, result, _, _, _), form = same(roots, None)
    why = form["targets"]["pubmed"]["whole"]
    assert "2026-09" in why and "not held here" in why
    assert form["pinned"] == 0 and form["read"] > 0 and result["written"]


def test_the_claims_own_pin_decides_never_the_callers_word(tmp_path):
    """A caller that believes the claims pinned to 2026-07: the chain is
    read from 2026-08, where every claim says they are, and the caller's
    word is noted, not used."""
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 1200, per_part=300), star_claims(1200),
                     before=_moved_twice)
    (_, result, _, _, _), form = same(roots, None, since={"pubmed": "2026-07"})
    t = form["targets"]["pubmed"]
    assert (t["from"], t["given"], t["to"], t["snapshots"]) == (TARGET_SNAPSHOT, "2026-07", "2026-10", 2)
    assert form["pinned"] > 0 and result["written"]


def test_a_since_naming_no_target_is_refused(tmp_path):
    a_target(tmp_path)
    create(tmp_path, claims_file(a_claim()))
    with pytest.raises(ann.AnnotationsError, match="--since names gutenberg"):
        ann.update(tmp_path, NAME, since={"gutenberg": "2026-08"}, clock=OCTOBER, log=lambda m: None)
    with pytest.raises(ValueError, match="since"):
        ann.update(tmp_path, NAME, since={}, _in_memory=True, clock=OCTOBER, log=lambda m: None)


def test_a_delta_that_omits_a_change_is_caught_where_a_record_is_read_anyway(tmp_path, monkeypatch):
    """A record whose text a claim's chunk end re-cuts is read whatever the
    delta says; where the claims pin it with another hash than the target
    holds and the chain names no change, the delta is not the record of
    what changed: nothing is written."""
    import numpy as np

    from app.hold import annotations_delta as ad
    text, _, _ = two_targets(tmp_path)
    create(tmp_path, claims_file(a_mention(text), a_claim("pubmed:2", "pubmed:3")),
           targets=[("pubmed", TARGET_SNAPSHOT), ("wikidata", "2026-07")], annotator="a graph builder")
    target_next(tmp_path, col.read(tmp_path, "pubmed"), {"pubmed:1": "Douglas Adams wrote it, and more"})
    monkeypatch.setattr(ad, "chain_uids", lambda *a, **k: ad.Touched(
        np.zeros(0, dtype="S16"), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=bool), 1))
    with pytest.raises(ann.AnnotationsError, match="do not name a record whose content hash changed"):
        ann.update(tmp_path, NAME, since={}, clock=OCTOBER, key=KEY, now=NOW, log=lambda m: None)
    assert not (col.shard_dir_of(tmp_path, NAME) / "snapshots/2026-10.json").exists()


def test_the_table_from_the_delta_is_the_whole_runs_digest_for_digest(tmp_path):
    """The one thing the delta changes, compared directly: the targets'
    table, built from pins and the records read, is `read_target`'s."""
    import numpy as np

    from app.hold import annotations_delta as ad
    a_wide_target(tmp_path, 1200, per_part=300)
    create(tmp_path, star_claims(1200))
    wide_move(tmp_path, "2026-09", changed=range(3, 1200, 5), withdrawn=range(8, 1200, 97))
    plan = ann._update_plan(tmp_path, NAME, None, None, OCTOBER)
    (t,) = plan.targets
    with st.workspace(tmp_path, "probe") as work:
        with st.linked_source(tmp_path / NAME / "origin" / "claims.jsonl", work) as src:
            served = ad.plan_delta(plan, {}, log=lambda m: None)
            ad.settle(plan, served, st.held_claims(plan.shard_tree, plan.prev, pins=True))
            sv = st.survey(src, plugins=("pubmed",), orders={"pubmed": "numeric"})
            whole = st.read_target(t, sv.ends["pubmed"], work)
            count: dict = {}
            delta = ad.table(t, sv.ends["pubmed"], served.pins["pubmed"], served.touched["pubmed"], work, count)
        st.forget()
    for k in ("digests", "found", "hashes"):
        assert np.array_equal(getattr(delta, k), getattr(whole, k)), k
    assert (delta.odd, delta.texts) == (whole.odd, whole.texts)
    assert 0 < count["read"] < count["pinned"]


# ---- derive_from_delta: the cascade's step ----------------------------------------------

STAR_FILE: list[bytes] = [b""]
DELTA_RAN: list[str] = []


def _star_produce(collection, out: Path, log=print) -> dict:
    Path(out).write_bytes(STAR_FILE[0])
    return {"claims": STAR_FILE[0].count(b"\n")}


def _star_delta(ctx, deltas, out: Path, log=print) -> dict:
    """A delta form that gives the whole run's bytes — or says it cannot."""
    from app.hold import annotation_methods as am
    DELTA_RAN.append(json.dumps(deltas, sort_keys=True))
    if STAR_FILE[0].startswith(b"#cannot"):
        raise am.WholeRun("this delta cannot apply")
    Path(out).write_bytes(STAR_FILE[0])
    return {"claims": STAR_FILE[0].count(b"\n")}


@pytest.fixture
def children_here(monkeypatch):
    """`derive_from_delta`'s children run in this process (a spawned child
    would not see the test's method), with the test's clock and key."""
    from app import parallel
    from app.hold import annotation_methods as am
    monkeypatch.setattr(parallel, "watch_parent", lambda: None)
    monkeypatch.setattr(am, "_stop_outside_last_writes", lambda: None)
    monkeypatch.setattr(am, "_node_key", lambda name: KEY)
    real = ann.update
    clock = [OCTOBER]
    monkeypatch.setattr(ann, "update", lambda *a, **k: real(*a, **{**k, "clock": clock[0], "now": NOW, "key": KEY}))
    real_finish = am.finish
    monkeypatch.setattr(am, "finish", lambda *a, **k: real_finish(*a, **{**k, "now": NOW, "clock": clock[0]}))
    ran: list[str] = []

    def run(target_fn, args, *, stop, poll_s, label, error=None):
        ran.append(label)
        with pytest.raises(SystemExit) as e:
            target_fn(*args)
        if e.value.code:
            raise am.ChildFailed(e.value.code, error.read_text())
    monkeypatch.setattr(am, "_run", run)
    for name, delta in (("star", None), ("star-delta", _star_delta)):
        monkeypatch.setitem(am.METHODS, name, am.Method(name, "pubmed", _star_produce, "a test method",
                                                        delta=delta, whole_because="a test method made whole"))
    monkeypatch.setattr(am, "_target_collection", lambda name: None)
    return ran, clock


def _whole_derivation(root: Path, method: str, clock) -> dict:
    """The reference: the method whole, then the update reading every record."""
    from app.hold import annotation_methods as am
    with st.workspace(root, "reference") as work:
        am.produce(root, NAME, method, work / "claims.jsonl", log=lambda m: None)
        return am.finish(root, NAME, method, work / "claims.jsonl", key=KEY, now=NOW, log=lambda m: None,
                         clock=clock, since=None)


def _without_form(got: dict) -> dict:
    return {k: v for k, v in got.items() if k not in ("form", "delta", "produced", "method")}


def test_derive_from_delta_re_checks_claims_given_by_hand_as_the_whole_update(tmp_path, children_here):
    from app.hold import annotation_methods as am
    ran, _ = children_here
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 1200, per_part=300), star_claims(1200),
                     before=lambda r: wide_move(r, "2026-09", changed=range(3, 1200, 41), withdrawn=(8, 100)))
    whole = ann.update(roots["whole"], NAME, key=KEY, now=NOW, clock=OCTOBER, log=lambda m: None)
    logs: list[str] = []
    got = am.derive_from_delta(roots["delta"], NAME, None, {"pubmed": ("2026-08", "2026-09")}, log=logs.append)
    assert ran == [f"annotations-recheck {NAME}"]
    assert tree(roots["delta"]) == tree(roots["whole"]), "the same bytes, every file"
    assert _without_form(got) == _without_form(whole.to_dict())
    assert got["form"]["method"] is None and got["form"]["update"]["pinned"] > got["form"]["update"]["read"] > 0
    assert got["form"]["update"]["targets"]["pubmed"]["from"] == "2026-08"
    assert logs and "claims given by hand, re-checked" in logs[-1]
    assert not list(roots["delta"].glob(".annotations-*")), "the workspace goes with the pass"


def test_derive_from_delta_runs_its_update_in_a_child_of_its_own(tmp_path):
    """The real child (spawned), its result read back from the workspace."""
    from app.hold import annotation_methods as am
    a_wide_target(tmp_path, 600, per_part=300)
    create(tmp_path, star_claims(600))
    wide_move(tmp_path, "2026-09", changed=range(3, 600, 11))
    got = am.derive_from_delta(tmp_path, NAME, None, {"pubmed": ("2026-08", "2026-09")}, poll_s=0.1,
                               log=lambda m: None)
    assert got["snapshot_id"] == "2026-10" and got["against"] == {"pubmed": "2026-09"}
    # the child keeps the module's own `MAX_FRAMES`: this small target's wide delta is read whole
    assert "of its frames holding a claimed one" in got["form"]["update"]["targets"]["pubmed"]["whole"]
    assert not list(tmp_path.glob(".annotations-*"))


def test_a_method_with_no_delta_form_runs_whole_says_why_and_its_update_comes_from_the_delta(tmp_path,
                                                                                            children_here):
    from app.hold import annotation_methods as am
    ran, _ = children_here
    STAR_FILE[0] = star_claims(1200, mess=False)
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 1200, per_part=300), STAR_FILE[0],
                     before=lambda r: wide_move(r, "2026-09", changed=range(3, 1200, 37)))
    whole = _whole_derivation(roots["whole"], "star", OCTOBER)
    got = am.derive_from_delta(roots["delta"], NAME, "star", {"pubmed": ("2026-08", "2026-09")}, log=lambda m: None)
    assert ran == [f"annotations-method {NAME}", f"annotations-update {NAME}"]
    assert tree(roots["delta"]) == tree(roots["whole"])
    assert _without_form(got) == _without_form(whole)
    assert (got["form"]["method"], got["form"]["method_why"]) == ("whole", "a test method made whole")
    assert got["form"]["update"]["pinned"] > 0


def test_a_methods_delta_form_is_taken_where_it_applies_and_its_fallbacks_say_why(tmp_path, children_here):
    """Its own last output to start from, the same method, every source's
    delta starting at what it last read: the delta form runs, and the bytes
    are the whole run's. No previous run, a method change, a source moved
    without a delta, the form's own refusal: whole, each said."""
    from app.hold import annotation_methods as am
    _, clock = children_here
    STAR_FILE[0] = star_claims(1200, mess=False)
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 1200, per_part=300), STAR_FILE[0],
                     before=lambda r: wide_move(r, "2026-09", changed=range(5, 1200, 29)))
    whole = _whole_derivation(roots["whole"], "star-delta", OCTOBER)
    DELTA_RAN.clear()
    got = am.derive_from_delta(roots["delta"], NAME, "star-delta", {"pubmed": ("2026-08", "2026-09")},
                               log=lambda m: None)
    assert got["form"]["method"] == "whole" and "no previous run" in got["form"]["method_why"] and not DELTA_RAN
    assert tree(roots["delta"]) == tree(roots["whole"]) and _without_form(got) == _without_form(whole)

    # the next month: the marker is the method's own, the delta starts where it last read
    for r in roots.values():
        wide_move(r, "2026-10", changed=range(7, 1200, 31))
    clock[0] = NOVEMBER
    whole = _whole_derivation(roots["whole"], "star-delta", NOVEMBER)
    got = am.derive_from_delta(roots["delta"], NAME, "star-delta", {"pubmed": ("2026-09", "2026-10")},
                               log=lambda m: None)
    assert (got["form"]["method"], got["form"]["method_why"]) == ("delta", None)
    assert json.loads(DELTA_RAN[-1]) == {"pubmed": ["2026-09", "2026-10"]}
    assert tree(roots["delta"]) == tree(roots["whole"]) and _without_form(got) == _without_form(whole)

    # the fallbacks, asked without running
    root = roots["delta"]
    method = am.get("star-delta")
    assert "starts at 2026-08" in am.delta_applies(root, NAME, method, {"pubmed": ("2026-08", "2026-11")})
    for r in roots.values():
        wide_move(r, "2026-11", changed=(9,))
    assert "moved from 2026-10 to 2026-11, and no delta of it is given" in am.delta_applies(root, NAME, method, {})
    mk = am.read_marker(root, NAME)
    am._write_marker(root, NAME, dict(mk, method="star"))
    assert "a method change runs whole" in am.delta_applies(root, NAME, method, {"pubmed": ("2026-10", "2026-11")})
    am._write_marker(root, NAME, mk)
    STAR_FILE[0] = b"#cannot\n"
    with st.workspace(root, "probe") as work:
        counts = am.produce(root, NAME, "star-delta", work / "c.jsonl", log=lambda m: None,
                            deltas={"pubmed": ["2026-10", "2026-11"]})
    assert counts["form"] == {"method": "whole", "why": "this delta cannot apply"}


def test_a_delta_that_would_read_every_frame_anyway_is_not_taken(tmp_path, monkeypatch):
    """A frame is decompressed whole for one wanted record: where the
    claimed records the chain names are expected in most of the target's
    frames, the target is read whole, and the update says so."""
    from app.hold import annotations_delta as ad
    monkeypatch.setattr(ad, "MAX_FRAMES", 0.5)
    roots = roots_of(tmp_path, lambda r: a_wide_target(r, 1200, per_part=300), star_claims(1200),
                     before=lambda r: wide_move(r, "2026-09", changed=range(3, 1200, 7)))
    (_, result, _, _, _), form = same(roots, None)
    why = form["targets"]["pubmed"]["whole"]
    assert "of its frames holding a claimed one" in why and form["pinned"] == 0 and result["written"]
    # a narrow delta of the same target is taken
    assert ad.expected_frames(sn.load(col.shard_dir_of(roots["delta"], "pubmed") / "snapshots/2026-09.json"),
                              2, 900, 1000) < 0.5
