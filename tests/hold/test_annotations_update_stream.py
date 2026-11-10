"""tests/hold/test_annotations_update_stream.py — `hold annotations update`
STREAMS its claims file and the previous snapshot's claims (2026-11-10;
app/hold/annotations_stream.py): for every kind of update — a new claims
file, a re-check against the target's newer text, nothing to update, a dry
run, graph claims — it writes the tree the in-memory path wrote byte for byte
(parts, superseded versions, relations, snapshot, signature, origin files)
and returns and says the same; it refuses what the in-memory path refuses
with the same words; on the pool too; and its peak does not follow the
claims held."""
import json
import random
from datetime import datetime, timezone
import tracemalloc
from pathlib import Path

import pytest

from app.hold import annotations as ann, annotations_stream as st, collection as col, records as rec, \
    shard as shard_mod, snapshot as sn
from app.pack import records as pack_records
from tests.hold.conftest import KEY, NOW, PLATFORM
from tests.hold.test_annotations import (TARGET_SNAPSHOT, a_claim, a_community, a_mention, a_relation,
                                         a_target, claims_file, two_targets)
from tests.hold.test_annotations_stream import a_pool, a_wide_target, star_claims, tree
from tests.hold.test_annotations_update import OCTOBER, target_next

NAME = "same"
GRAPH = [("pubmed", TARGET_SNAPSHOT), ("wikidata", "2026-07")]


def create(root: Path, claims: bytes, **kw):
    if "targets" not in kw:
        kw.update(target_ref="pubmed", from_snapshot=TARGET_SNAPSHOT)
    return ann.create(root, NAME, claims=claims, platform_image=PLATFORM, snapshot_id="2026-09", key=KEY,
                      now=NOW, nonce="0f" * 16, log=lambda m: None, **kw)


def wide_next(root: Path, *, changed=(), withdrawn=()):
    """`a_wide_target`'s next snapshot, 2026-09: the records `changed` with
    a new text, those `withdrawn` gone; a part re-issued only where it moved
    (its old version kept under its superseded name)."""
    d = col.shard_dir_of(root, "pubmed")
    prev = sn.load(d / shard_mod.snapshot_path(TARGET_SNAPSHOT))
    changed, withdrawn = {f"pubmed:{i}" for i in changed}, {f"pubmed:{i}" for i in withdrawn}
    shards, before, after = [], {}, {}
    for s in prev.shards:
        part = d / s.path
        old = list(pack_records.iter_records(part))
        before.update({r["uid"]: r["content_hash"] for r in old})
        new = []
        for r in old:
            if r["uid"] in withdrawn:
                continue
            if r["uid"] in changed:
                sections = [["Abstract", r["sections"][0][1] + ", revised"]]
                r = dict(r, sections=sections, content_hash=rec.content_hash(r["title"], [tuple(x) for x in sections]))
            new.append(r)
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
    snap = sn.Snapshot(collection_id=prev.collection_id, snapshot_id="2026-09", previous=prev.hash,
                       shards=tuple(shards), delta=sn.compute_delta(before, after), files={})
    (d / shard_mod.snapshot_path("2026-09")).write_bytes(snap.canonical())


def update(root: Path, claims, *, memory: bool, **kw):
    kw.setdefault("clock", OCTOBER)
    logs: list[str] = []
    try:
        got = ann.update(root, NAME, claims=claims, key=KEY, now=NOW, log=logs.append, _in_memory=memory, **kw)
    except Exception as e:                  # noqa: BLE001 — compared, refusal for refusal
        return ("refused", type(e), str(e), logs)
    return ("updated", got.to_dict(), got.dropped, logs)


def same_update(tmp_path, build, first: bytes, then: bytes | None, *, monkeypatch=None, before=None,
                create_kw=None, **kw):
    """The same collection in three roots, updated by the in-memory path, by
    the stream from bytes and by the stream from a file — the last on a pool
    of two with small tasks where `monkeypatch` is given: the same trees,
    the same results and the same log."""
    roots = {k: tmp_path / k for k in ("memory", "bytes", "file")}
    for r in roots.values():
        r.mkdir()
        build(r)
        create(r, first, **(create_kw or {}))
        if before is not None:
            before(r)
    path = tmp_path / "update.jsonl"
    if then is not None:
        path.write_bytes(then)
    out = {"memory": update(roots["memory"], then, memory=True, **kw),
           "bytes": update(roots["bytes"], then, memory=False, **kw)}
    if monkeypatch is not None:
        a_pool(monkeypatch)
    out["file"] = update(roots["file"], None if then is None else path, memory=False, **kw)
    trees = {k: tree(r) for k, r in roots.items()}
    assert out["bytes"] == out["memory"], "streamed from bytes: the same result"
    assert out["file"] == out["memory"], "streamed from a file: the same result"
    assert trees["bytes"] == trees["memory"], "streamed from bytes: the same tree"
    assert trees["file"] == trees["memory"], "streamed from a file: the same tree"
    for r in roots.values():
        assert not list(r.glob(".annotations-*")), "no workspace left behind"
    if then is not None:
        assert path.read_bytes() == then, "the file given is read, never moved"
    return out["memory"], trees["memory"]


def next_file(n: int, *, seed: int = 11) -> bytes:
    """The star's next claims file: every seventh claim left out (tombstoned),
    every fifth kept with another confidence (the same claim, changed), new
    claims between the groups' centres and a translation, all reshuffled."""
    rnd = random.Random(seed)
    lines = star_claims(n, mess=False).splitlines()
    out = []
    for j, x in enumerate(lines):
        if j % 7 == 0:
            continue
        c = json.loads(x)
        if j % 5 == 0:
            c["confidence"] = "0.5"
        out.append(json.dumps(c, ensure_ascii=False, sort_keys=True).encode())
    for i in range(1, n - 3, 4 * 9):
        out.append(json.dumps(dict(json.loads(lines[0]), subject=f"pubmed:{i}", object=f"pubmed:{i + 4}"),
                              sort_keys=True).encode())
    out.append(json.dumps(dict(json.loads(lines[1]), type="translation-of"), sort_keys=True).encode())
    rnd.shuffle(out)
    return b"".join(x + (b"\r\n" if j % 3 == 0 else b"\n") for j, x in enumerate(out))


# ---- the same bytes ---------------------------------------------------------------------

def test_a_new_claims_file_for_a_star_writes_the_same_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(ann, "SHARD_RECORDS", 400)
    got, t = same_update(tmp_path, lambda r: a_wide_target(r, 2400, per_part=300), star_claims(2400),
                         next_file(2400), monkeypatch=monkeypatch)
    _, result, dropped, logs = got
    assert result["written"] and result["added"] > 0 and result["changed"] > 0 and result["tombstoned"] > 0
    assert len(result["reissued"]) > 2 and logs and "superseded" not in logs[0]
    assert sum(1 for k in t if k.startswith("same/origin/claims.2026-09.jsonl")) == 1


def test_a_re_check_after_the_target_moved_writes_the_same_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(ann, "SHARD_RECORDS", 400)
    got, _ = same_update(tmp_path, lambda r: a_wide_target(r, 2400, per_part=300), star_claims(2400), None,
                         monkeypatch=monkeypatch,
                         before=lambda r: wide_next(r, changed=range(3, 2400, 13), withdrawn=range(7, 2400, 101)))
    _, result, dropped, logs = got
    assert result["changed"] > 0 and result["tombstoned"] > 0 and len(dropped) > 5
    assert dropped == tuple(sorted(dropped, key=lambda d: int(d.split()[1].rstrip(":")))), "in the file's order"


@pytest.mark.parametrize("case", ["nothing", "dry run", "a small file", "the claims held, as given"])
def test_the_small_updates_return_and_write_the_same(tmp_path, case):
    first = claims_file(a_claim(), a_claim("pubmed:3", "pubmed:1", confidence="0.73"))
    then = {"nothing": None, "dry run": claims_file(a_claim("pubmed:2", "pubmed:4"), a_claim()),
            "a small file": claims_file(a_claim("pubmed:2", "pubmed:4"), a_claim()),
            "the claims held, as given": first}[case]
    got, _ = same_update(tmp_path, a_target, first, then, dry_run=case == "dry run")
    assert got[0] == "updated"


def test_a_re_check_of_a_small_target_writes_the_same(tmp_path):
    def moved(r):
        target = col.read(r, "pubmed")
        target_next(r, target, {"pubmed:2": "two, revised", "pubmed:3": None})
    got, _ = same_update(tmp_path, a_target, claims_file(a_claim(), a_claim("pubmed:3", "pubmed:1")), None,
                         before=moved)
    assert got[2] == ("line 2: pubmed:3",)


@pytest.mark.parametrize("container", ["jsonl", "parquet"])
def test_graph_claims_on_two_targets_update_to_the_same_tree(tmp_path, monkeypatch, container):
    probe = tmp_path / "probe"
    probe.mkdir()
    text, _, _ = two_targets(probe)
    first = claims_file(a_mention(text), a_relation(text), a_community(), a_claim("pubmed:2", "pubmed:3"))
    then = claims_file(a_mention(text, start=14, end=19, surface="wrote"), a_community(summary="three"),
                       a_claim("pubmed:2", "pubmed:3"), a_mention(text))
    got, _ = same_update(tmp_path, two_targets, first, then, monkeypatch=monkeypatch,
                         create_kw={"targets": GRAPH, "annotator": "a graph builder", "container": container})
    assert got[1]["added"] == 1 and got[1]["changed"] == 1 and got[1]["tombstoned"] == 1


# ---- the same refusals -------------------------------------------------------------------

def _refused() -> dict[str, tuple[bytes | None, object]]:
    c = a_claim
    first = claims_file(c(), c("pubmed:3", "pubmed:1"))

    def all_withdrawn(r):
        target_next(r, col.read(r, "pubmed"), {"pubmed:1": None, "pubmed:3": None})

    def no_origin(r):
        (r / NAME / "origin" / "claims.jsonl").unlink()

    def part_gone(r):
        d = col.shard_dir_of(r, NAME)
        snap = sn.load(d / "snapshots/2026-09.json")
        (d / snap.shards[0].path).unlink()
    return {
        "an end not held": (claims_file(c(), c("pubmed:1", "pubmed:9"), c("pubmed:2", "pubmed:8")), None),
        "a hash after an end not held": (claims_file(c("pubmed:1", "pubmed:9"), c(subject_hash="00" * 16)), None),
        "not json": (first + b'{"type":\n', None),
        "a repeat": (claims_file(c(), c("pubmed:2", "pubmed:1")), None),
        "a graph claim in a version-7 collection": (
            claims_file(c(), a_community(members=["pubmed:1", "pubmed:2"], contributor="a test annotator")), None),
        "the period": (claims_file(c("pubmed:2", "pubmed:4")), "period"),
        "the last claim gone": (None, all_withdrawn),
        "no claims file to re-check": (None, no_origin),
        "a part of the previous snapshot gone": (claims_file(c()), part_gone),
        "a check before a part gone": (claims_file(c(type="related")), part_gone),
    }


@pytest.mark.parametrize("case", sorted(_refused()))
def test_a_refused_update_has_the_in_memory_path_s_words_and_writes_nothing(tmp_path, case):
    then, setup = _refused()[case]
    kw = {"clock": datetime(2026, 9, 20, tzinfo=timezone.utc)} if setup == "period" else {}
    got, _ = same_update(tmp_path, a_target, claims_file(a_claim(), a_claim("pubmed:3", "pubmed:1")), then,
                         before=None if setup in (None, "period") else setup, **kw)
    assert got[0] == "refused", got


def test_on_the_pool_a_late_refusal_has_the_same_words(tmp_path, monkeypatch):
    lines = star_claims(1600, mess=False).splitlines()
    bad = dict(json.loads(lines[1100]), subject="pubmed:99999")
    then = b"".join(x + b"\n" for x in lines[:1100] + [json.dumps(bad).encode()] + lines[1100:])
    got, _ = same_update(tmp_path, lambda r: a_wide_target(r, 1600, per_part=300), star_claims(1600), then,
                         monkeypatch=monkeypatch)
    assert got[0] == "refused" and "pubmed:99999" in got[2]


# ---- the pieces --------------------------------------------------------------------------

def test_the_claims_held_are_read_as_held_claims_reads_them(tmp_path, monkeypatch):
    monkeypatch.setattr(ann, "SHARD_RECORDS", 250)
    a_wide_target(tmp_path, 1200, per_part=300)
    create(tmp_path, star_claims(1200))
    d = col.shard_dir_of(tmp_path, NAME)
    snap = sn.load(d / "snapshots/2026-09.json")
    want = {st.identity_digest(ann.claim_of(r)): (n, r["content_hash"]) for n, r in ann._held_claims(d, snap).values()}
    got = st.held_claims(d, snap)
    raw = got.idents.tobytes()                  # 16 bytes each: an S16 item drops its trailing zeros
    assert got.error is None and len(got.numbers) == len(want) == 900 and got.top == 900
    assert {raw[16 * j:16 * j + 16]: (int(n), got.content_hash(j)) for j, n in enumerate(got.numbers)} == want
    assert [raw[16 * j:16 * j + 16] for j in range(900)] == sorted(want)


def test_a_re_check_links_the_claims_file_and_copies_nothing(tmp_path, monkeypatch):
    a_target(tmp_path)
    create(tmp_path, claims_file(a_claim(), a_claim("pubmed:3", "pubmed:1")))
    target_next(tmp_path, col.read(tmp_path, "pubmed"), {"pubmed:2": "two, revised"})
    origin = tmp_path / NAME / "origin" / "claims.jsonl"
    seen = []
    real = st.linked_source

    def spy(path, work):
        src = real(path, work)
        seen.append((Path(work) / st.COPY).stat().st_ino == origin.stat().st_ino)
        return src
    monkeypatch.setattr(st, "linked_source", spy)
    got = ann.update(tmp_path, NAME, key=KEY, now=NOW, log=lambda m: None, clock=OCTOBER)
    assert got.changed == 2 and seen == [True] and origin.stat().st_nlink == 1, "both re-pinned to 2026-09"


# ---- the memory ---------------------------------------------------------------------------

def test_the_update_s_peak_does_not_follow_the_claims(tmp_path, monkeypatch):
    """MEASURED 2026-11-10 in the commit that streamed `update` (tracemalloc;
    200,000 claims held, a new file leaving out 28,572, changing 34,285 and
    adding 7,409): the in-memory path peaked at 1,404 MB; the stream, on a
    pool of three, at 55 MB in this process (RSS 168 MB; docs/notes/
    wikipedia-one-version.md). Here, with half of them on a pool of two:
    this process under 64 MB — the claims' integers and digests, the held
    claims' (~40 bytes each), a sort run, the children's answers in flight."""
    n = 133_333
    a_wide_target(tmp_path, n, per_part=5000)
    create(tmp_path, star_claims(n, mess=False))
    path = tmp_path / "next.jsonl"
    path.write_bytes(next_file(n))
    from app import config
    monkeypatch.setattr(config, "PARSE_PROCESSES", 2)
    tracemalloc.start()
    try:
        got = ann.update(tmp_path, NAME, claims=path, key=KEY, now=NOW, log=lambda m: None, clock=OCTOBER)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert got.snapshot is not None and got.tombstoned > 0
    assert peak < 64 << 20, f"peak {peak / 2**20:.0f} MB"


# ---- the workspaces clear themselves ------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]


def a_holder(root: Path, tag: str):
    """Another process in a create's pass: it holds the workspace `tag`,
    with a file in it, until it is killed."""
    import subprocess
    import sys
    import time
    ready = root / f"ready-{tag}"
    code = (f"import sys, time; from pathlib import Path; sys.path.insert(0, {str(REPO)!r}); "
            f"from app.hold import annotations_stream as st; "
            f"ws = st.workspace(Path({str(root)!r}), {tag!r}); d = ws.__enter__(); "
            f"(d / 'claims.jsonl').write_bytes(b'x' * 4096); "
            f"Path({str(ready)!r}).touch(); time.sleep(600)")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=REPO)
    for _ in range(600):
        if ready.exists():
            return proc
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("the holder never took its workspace")


def test_a_workspace_is_cleared_once_its_process_is_gone_and_never_while_it_lives(tmp_path):
    import signal
    live, dead = a_holder(tmp_path, "create.live"), a_holder(tmp_path, "update.dead")
    try:
        old = tmp_path / ".annotations-create.before-locks"         # left by a create before 2026-11-10
        old.mkdir()
        (old / "claims.jsonl").write_bytes(b"y" * 4096)
        (tmp_path / ".annotations-preview.0123.lock").touch()       # a lock with no workspace
        dead.send_signal(signal.SIGKILL)
        dead.wait(timeout=30)
        said: list[str] = []
        gone = st.sweep(tmp_path, said.append)
        assert sorted(Path(g).name for g in gone) == [".annotations-create.before-locks", ".annotations-update.dead"]
        assert len(said) == 2 and all("stopped before it ended" in m for m in said)
        assert (tmp_path / ".annotations-create.live" / "claims.jsonl").is_file(), "a live holder's is kept"
        assert sorted(p.name for p in tmp_path.glob(".annotations-*")) == [
            ".annotations-create.live", ".annotations-create.live.lock"]
        assert st.sweep(tmp_path, said.append) == [] and len(said) == 2, "each said once"
        with pytest.raises(ann.AnnotationsError, match="is in use"):
            with st.workspace(tmp_path, "create.live"):
                pass
        assert (tmp_path / ".annotations-create.live" / "claims.jsonl").is_file(), "never cleared by a second"
    finally:
        live.kill()
        live.wait(timeout=30)
        dead.kill()
    assert [Path(g).name for g in st.sweep(tmp_path, said.append)] == [".annotations-create.live"]
    assert not list(tmp_path.glob(".annotations-*"))


def test_a_create_and_an_update_clear_what_a_stopped_one_left_and_refuse_a_running_one(tmp_path):
    import signal
    a_target(tmp_path)
    dead = a_holder(tmp_path, "create.other")
    dead.send_signal(signal.SIGKILL)
    dead.wait(timeout=30)
    said: list[str] = []
    ann.create(tmp_path, NAME, target_ref="pubmed", from_snapshot=TARGET_SNAPSHOT, claims=claims_file(a_claim()),
               platform_image=PLATFORM, snapshot_id="2026-09", key=KEY, now=NOW, nonce="0f" * 16, log=said.append)
    assert any(".annotations-create.other" in m for m in said) and not list(tmp_path.glob(".annotations-*"))
    live = a_holder(tmp_path, f"update.{NAME}")
    try:
        with pytest.raises(ann.AnnotationsError, match="another create or update of the same collection"):
            ann.update(tmp_path, NAME, claims=claims_file(a_claim("pubmed:2", "pubmed:4")), key=KEY, now=NOW,
                       log=lambda m: None, clock=OCTOBER)
        assert not (col.shard_dir_of(tmp_path, NAME) / "snapshots/2026-10.json").exists()
    finally:
        live.kill()
        live.wait(timeout=30)
    got = ann.update(tmp_path, NAME, claims=claims_file(a_claim("pubmed:2", "pubmed:4")), key=KEY, now=NOW,
                     log=said.append, clock=OCTOBER)
    assert got.snapshot is not None and any(f".annotations-update.{NAME}" in m for m in said)
