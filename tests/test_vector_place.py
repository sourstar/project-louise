"""app/vector_place.py — where a collection's vector table lives: on the data
pool where searches read it, or in the BUILD AREA (LOUISE_BUILD_DIR;
operator, 2026-11-07, docs/notes/storage-tiering.md §8: "B, ssd3 as the
build area"), and `hold vectors move` between the two.

The table is a real Lance table (several versions, a deletion, a scalar
index), searched through the node's own cached connection
(`embeddings._conn`) before, during and after every move."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app import collections as collections_mod, config, embeddings, spaces, vector_place
from app.hold import recreate

DIM = 16
ROOT_DIR = Path(__file__).resolve().parent.parent


class Killed(BaseException):
    """A kill, where the hook is called: nothing catches it."""


def _collection(root: Path, name: str):
    return collections_mod.Collection(
        name=name, plugin="gutenberg", profile=None, embed_mode_default=None,
        corpus_dir=root / name / "origin", manifest_path=root / name / "state" / "manifest.sqlite",
        fts_dir=root / name / "index", lance_dir=vector_place.table_dir(root, name))


def _vec(i: int) -> np.ndarray:
    v = np.random.default_rng(i).standard_normal(DIM).astype("float32")
    return v / np.linalg.norm(v)


def _write_table(coll, n: int = 240) -> int:
    """A table as the node's are: three commits, a deletion, a scalar index."""
    import lancedb
    db = lancedb.connect(str(coll.lance_dir))
    rows = [{"id": f"c{i}", "book_id": i // 4, "text": f"chunk {i}", "vector": _vec(i)} for i in range(n)]
    tbl = db.create_table("chunks", data=rows[: n // 2])
    tbl.add(rows[n // 2:])
    tbl.delete("book_id = 3")
    tbl.create_scalar_index("book_id")
    return tbl.count_rows()


def _search(coll, *, fresh: bool = True) -> list[tuple[str, float]]:
    if fresh:
        embeddings.forget(coll)
    hits = embeddings._conn(coll).open_table("chunks").search(_vec(10_000)).limit(15).to_list()
    return [(h["id"], round(float(h["_distance"]), 6)) for h in hits]


@pytest.fixture
def node(isolated_config, tmp_path, monkeypatch):
    """The node's own root, a build area, and collection `c` holding a table
    on the data pool."""
    root = config.louise_root() / "collections"
    build = tmp_path / "ssd3"
    build.mkdir()
    monkeypatch.setattr(config, "BUILD_DIR", build)
    monkeypatch.setattr(vector_place, "_hook", None)
    coll = _collection(root, "c")
    rows = _write_table(coll)
    embeddings.forget(coll)
    return SimpleNamespace(root=root, build=build, coll=coll, rows=rows, base=root / "c",
                           link=vector_place.table_dir(root, "c"), baseline=_search(coll))


def _kill_at(monkeypatch, step: str, nth: int = 1) -> None:
    seen = {"n": 0}

    def hook(s: str) -> None:
        if s == step:
            seen["n"] += 1
            if seen["n"] >= nth:
                raise Killed(step)
    monkeypatch.setattr(vector_place, "_hook", hook)


def _one_valid_table(n) -> None:
    """THE NAME HOLDS ONE VALID TABLE: the rows and the nearest neighbours
    the collection had, through the path every reader opens."""
    assert n.link.is_dir(), f"{n.link} is no table"
    assert spaces.known_lance_rows(n.link) == n.rows
    assert _search(n.coll) == n.baseline


def _no_leftovers(n, to: str) -> None:
    assert not (n.base / vector_place.MOVING).exists() and not (n.base / vector_place.MOVING).is_symlink()
    assert not (n.base / "state" / vector_place.STATE).exists()
    assert not (n.base / "state" / vector_place.JOURNAL).exists()
    assert not list(n.base.glob(vector_place.XCHG + "*"))
    if to == vector_place.HOT:
        assert not n.link.is_symlink() and not (n.build / "lance" / "c").exists()
    else:
        assert vector_place.physical(n.link) == n.build / "lance" / "c"
        assert not any(p.name.endswith(vector_place.PART) for p in (n.build / "lance" / "c").rglob("*"))


def _to_build(n):
    return vector_place.move(n.root, "c", vector_place.BUILD, collection=n.coll)


# ---- unset: today's paths, byte for byte -------------------------------------

def test_unset_every_path_is_todays(isolated_config, tmp_path, monkeypatch):
    from app.hold import carry, records as rec
    root = config.louise_root() / "collections"
    assert config.BUILD_DIR is None and vector_place.area() is None
    assert vector_place.table_dir(root, "x") == root / "x" / "lance"
    assert carry._paths(root, "x")["lance"] == root / "x" / "lance"
    assert recreate._tree(root, "x", "lance") == root / "x" / "lance"
    assert vector_place.destination_for_new(root, "x") == root / "x" / "lance"
    assert vector_place.place_new(root, "x") is None and not (root / "x").exists(), "nothing made"
    codex = SimpleNamespace(plugin="gutenberg", license=SimpleNamespace(cls="green"), layers=())
    monkeypatch.setattr("app.layers.layers_for", lambda c: None)
    assert rec._local_collection(root, "x", codex).lance_dir == root / "x" / "lance"
    for c in collections_mod.load_all():
        assert c.lance_dir == config.louise_root() / "collections" / c.name / "lance"
        assert not Path(c.lance_dir).is_symlink()


def test_a_root_not_the_nodes_is_never_placed(node, tmp_path):
    elsewhere = tmp_path / "scratch"
    assert vector_place.place_new(elsewhere, "x") is None and not elsewhere.exists()
    coll = _collection(elsewhere, "y")
    _write_table(coll)
    with pytest.raises(vector_place.PlaceError, match="not this node's root"):
        vector_place.move(elsewhere, "y", vector_place.BUILD)


# ---- a new collection's table in the build area --------------------------------

def test_place_new_links_the_table_into_the_build_area(node):
    target = vector_place.place_new(node.root, "fresh")
    link = vector_place.table_dir(node.root, "fresh")
    assert target == node.build / "lance" / "fresh" and link.is_symlink()
    assert vector_place.physical(link) == target and vector_place.tier(link) == vector_place.BUILD
    assert vector_place.place_new(node.root, "fresh") == target, "repeatable"
    coll = _collection(node.root, "fresh")
    rows = _write_table(coll)
    assert spaces.known_lance_rows(target) == rows, "the bytes are written in the build area"
    assert not (node.root / "fresh" / "lance").resolve().is_relative_to(node.root)


def test_place_new_replaces_the_empty_directory_a_reader_made(node):
    (node.root / "e" / "lance").mkdir(parents=True)
    assert vector_place.place_new(node.root, "e") == node.build / "lance" / "e"
    assert (node.root / "e" / "lance").is_symlink() and not (node.root / "e" / vector_place.PLACE).exists()


def test_place_new_refuses_a_table_on_either_side(node):
    with pytest.raises(vector_place.PlaceError, match="already holds a vector table"):
        vector_place.place_new(node.root, "c")
    (node.build / "lance" / "left").mkdir(parents=True)
    (node.build / "lance" / "left" / "f").write_bytes(b"x")
    with pytest.raises(vector_place.PlaceError, match="was not forgotten"):
        vector_place.place_new(node.root, "left")
    assert not (node.root / "left").exists()


def test_an_unmounted_build_area_is_refused_never_the_containers_disk(node, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BUILD_DIR", tmp_path / "not-mounted")
    with pytest.raises(vector_place.PlaceError, match="storage mounted"):
        vector_place.place_new(node.root, "z")
    with pytest.raises(vector_place.PlaceError, match="storage mounted"):
        vector_place.plan(node.root, "c", vector_place.BUILD)
    assert not (tmp_path / "not-mounted").exists() and not (node.root / "z").exists()


def test_unplace_new_undoes_a_placement_whose_collection_was_not_made(node):
    vector_place.place_new(node.root, "gone")
    vector_place.unplace_new(node.root, "gone")
    assert not (node.root / "gone").exists() and not (node.build / "lance" / "gone").exists()


# ---- a link that reaches nothing --------------------------------------------------

def test_a_table_whose_build_area_is_gone_is_refused_not_replaced(node):
    from app import db
    _to_build(node)
    os.rename(node.build / "lance" / "c", node.build / "lance" / "c.away")
    with pytest.raises(vector_place.PlaceError, match="storage mounted"):
        db.connect(node.coll)
    assert not (node.build / "lance" / "c").exists(), "no empty table made in its place"


def test_the_loader_refuses_that_collection_and_keeps_the_others(isolated_config, tmp_path, monkeypatch):
    c = collections_mod.load_all()[0]
    link = Path(c.lance_dir)
    if link.exists():
        import shutil
        shutil.rmtree(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(tmp_path / "ssd3" / "lance" / c.name, target_is_directory=True)
    served = collections_mod.load_all()
    assert c.name not in {s.name for s in served}
    [refusal] = [r for r in collections_mod.refusals() if r.name == c.name]
    assert "storage mounted" in refusal.reason and "not served" in refusal.reason


# ---- the move ------------------------------------------------------------------------

def test_the_dry_run_says_bytes_room_time_and_what_it_frees_and_writes_nothing(node, tmp_path):
    before = recreate.tree_digest(tmp_path)
    out = vector_place.plan(node.root, "c", vector_place.BUILD)
    assert recreate.tree_digest(tmp_path) == before, "not a byte written"
    size = recreate.tree_digest(node.link)["bytes"]
    assert out["bytes"] == out["to_copy"] == out["frees"]["bytes"] == size > 0
    assert out["from"]["tier"] == "hot" and out["to"]["path"] == str(node.build / "lance" / "c")
    assert out["room"]["enough"] and out["room"]["free"] > 0
    assert out["estimate"]["total_s"] == out["estimate"]["copy_s"] + out["estimate"]["verify_s"]
    assert "frees" in out["say"] and "at the measured rates" in out["say"]
    assert "louise node pause c" in out["next"]


def test_the_estimate_is_the_measured_rates():
    gib = 700 * vector_place.GIB
    up = vector_place.estimate(gib, "build", "hot")
    assert up["copy_s"] == round(700 * 1024 / 187) and up["verify_s"] == round(700 * 1024 / 1400)
    down = vector_place.estimate(gib, "hot", "build")
    assert down["copy_s"] == round(18 * 1024 / 287 + 682 * 1024 / 158)
    assert down["verify_s"] == round(700 * 1024 / 465)


def test_a_move_to_build_and_back_keeps_every_byte_and_every_answer(node):
    cached = embeddings._conn(node.coll).open_table("chunks")          # a reader's, opened before
    digest = recreate.tree_digest(node.link)
    out = _to_build(node)
    assert out["moved"] and vector_place.tier(node.link) == "build"
    assert out["digest"]["sha256"] == digest["sha256"] and out["rows"] == node.rows
    assert out["freed"] == digest["bytes"]
    assert recreate.tree_digest(node.link) == digest, "the same bytes at the same paths"
    _one_valid_table(node)
    hits = cached.search(_vec(10_000)).limit(15).to_list()
    assert [(h["id"], round(float(h["_distance"]), 6)) for h in hits] == node.baseline, \
        "a table opened before the move answers the same after it"
    _no_leftovers(node, "build")
    back = vector_place.move(node.root, "c", vector_place.HOT, collection=node.coll)
    assert back["moved"] and not node.link.is_symlink() and node.link.is_dir()
    assert recreate.tree_digest(node.link) == digest
    _one_valid_table(node)
    _no_leftovers(node, "hot")
    assert vector_place.move(node.root, "c", vector_place.HOT)["nothing"], "there already"


def test_a_cached_connection_writes_behind_the_new_link_after_a_move(node):
    conn = embeddings._conn(node.coll)
    _to_build(node)
    conn.open_table("chunks").add([{"id": "late", "book_id": 999, "text": "late", "vector": _vec(999)}])
    assert spaces.known_lance_rows(node.build / "lance" / "c") == node.rows + 1


BUILD_STEPS = [("copying", 3), ("copied", 2), ("verified", 1), ("linked", 1), ("exchanged", 1),
               ("recorded", 1), ("removed", 1)]
HOT_STEPS = [("copying", 3), ("copied", 2), ("verified", 1), ("exchanged", 1), ("recorded", 1), ("removed", 1)]


@pytest.mark.parametrize("step,nth", BUILD_STEPS)
def test_a_kill_at_each_step_to_build_leaves_one_table_and_resumes(node, monkeypatch, step, nth):
    _kill_at(monkeypatch, step, nth)
    with pytest.raises(Killed):
        _to_build(node)
    monkeypatch.setattr(vector_place, "_hook", None)
    _one_valid_table(node)
    out = _to_build(node)
    assert out.get("moved"), out
    assert vector_place.tier(node.link) == "build"
    _one_valid_table(node)
    _no_leftovers(node, "build")


@pytest.mark.parametrize("step,nth", HOT_STEPS)
def test_a_kill_at_each_step_to_hot_leaves_one_table_and_resumes(node, monkeypatch, step, nth):
    _to_build(node)
    _kill_at(monkeypatch, step, nth)
    with pytest.raises(Killed):
        vector_place.move(node.root, "c", vector_place.HOT, collection=node.coll)
    monkeypatch.setattr(vector_place, "_hook", None)
    _one_valid_table(node)
    out = vector_place.move(node.root, "c", vector_place.HOT, collection=node.coll)
    assert out.get("moved"), out
    _one_valid_table(node)
    _no_leftovers(node, "hot")


@pytest.mark.parametrize("to", ["build", "hot"])
@pytest.mark.parametrize("step", ["exchange-1", "exchange-2"])
def test_without_rename_exchange_a_stop_between_the_renames_is_finished(node, monkeypatch, to, step):
    """A filesystem without RENAME_EXCHANGE: three renames; a stop between
    them is settled by the next run's first step, which leaves the table
    under its name before anything else reads it."""
    if to == "hot":
        _to_build(node)
    monkeypatch.setattr(vector_place, "_renameat2_exchange", lambda a, b: False)
    _kill_at(monkeypatch, step)
    with pytest.raises(Killed):
        vector_place.move(node.root, "c", to, collection=node.coll)
    monkeypatch.setattr(vector_place, "_hook", None)
    assert vector_place.settle(node.base)
    _one_valid_table(node)
    assert vector_place.move(node.root, "c", to, collection=node.coll).get("moved")
    _one_valid_table(node)
    _no_leftovers(node, to)


def test_a_stop_in_the_removal_leaves_the_new_table_and_resumes(node, monkeypatch):
    real = vector_place.remove_table

    def half(path):
        files = sorted(p for p in Path(vector_place.physical(path)).rglob("*") if p.is_file())
        for p in files[: len(files) // 2]:
            p.unlink()
        raise Killed("removing")
    monkeypatch.setattr(vector_place, "remove_table", half)
    with pytest.raises(Killed):
        _to_build(node)
    monkeypatch.setattr(vector_place, "remove_table", real)
    _one_valid_table(node)
    assert _to_build(node)["moved"]
    _no_leftovers(node, "build")


def test_sigterm_finishes_the_file_in_flight_and_a_run_again_goes_on(node, monkeypatch):
    import threading
    stop = threading.Event()
    seen = {"n": 0}

    def hook(s):
        if s == "copied":
            seen["n"] += 1
            if seen["n"] == 2:
                stop.set()
    monkeypatch.setattr(vector_place, "_hook", hook)
    out = vector_place.move(node.root, "c", vector_place.BUILD, collection=node.coll, stop=stop, threads=1)
    assert out["interrupted"] and out["copied_files"] == 2
    monkeypatch.setattr(vector_place, "_hook", None)
    _one_valid_table(node)
    assert vector_place.tier(node.link) == "hot", "nothing switched"
    again = vector_place.move(node.root, "c", vector_place.BUILD, collection=node.coll)
    assert again["moved"] and again["copied"]["copied_files"] == again["copied"]["files"] - 2, \
        "the two files done are not copied again"
    _no_leftovers(node, "build")


def test_a_real_kill_mid_copy_resumes(node, tmp_path):
    """SIGKILL, not a hook: the process gone mid-copy, and the next run
    finishes from the journal."""
    marker = tmp_path / "killed-here"
    script = textwrap.dedent(f"""
        import time
        from pathlib import Path
        from app import collections as cm, config, vector_place
        root = Path({str(node.root)!r})
        def hook(s, n={{"n": 0}}):
            if s == "copied":
                n["n"] += 1
                if n["n"] == 3:
                    Path({str(marker)!r}).write_text("x")
                    time.sleep(120)
        vector_place._hook = hook
        vector_place.move(root, "c", "build", threads=1)
    """)
    env = {**os.environ, "LOUISE_ROOT": str(node.root.parent), "LOUISE_BUILD_DIR": str(node.build),
           "VECTORS_MIN_FREE_GB": str(config.VECTORS_MIN_FREE_GB),
           "VECTORS_MIN_FREE_FRACTION": str(config.VECTORS_MIN_FREE_FRACTION),
           "PYTHONPATH": str(ROOT_DIR)}
    proc = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT_DIR, env=env, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(600):
            if marker.exists() or proc.poll() is not None:
                break
            time.sleep(0.1)
        assert marker.exists(), f"the copy never reached its third file: {proc.stderr.read() if proc.poll() is not None else ''}"
        proc.kill()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    _one_valid_table(node)
    assert (node.base / "state" / vector_place.JOURNAL).read_text().count("\n") == 3
    out = _to_build(node)
    assert out["moved"] and out["copied"]["copied_files"] == out["copied"]["files"] - 3
    _one_valid_table(node)
    _no_leftovers(node, "build")


def test_a_write_between_the_copy_and_the_switch_is_copied_before_switching(node, monkeypatch):
    import lancedb
    seen = {"n": 0}

    def hook(s):
        if s == "verified" and seen["n"] == 0:
            seen["n"] += 1
            lancedb.connect(str(node.link)).open_table("chunks").add(
                [{"id": "slipped", "book_id": 777, "text": "slipped", "vector": _vec(777)}])
    monkeypatch.setattr(vector_place, "_hook", hook)
    out = _to_build(node)
    assert out["moved"] and out["rows"] == node.rows + 1
    assert spaces.known_lance_rows(node.link) == node.rows + 1, "the late write was not lost"


def test_no_room_refuses_before_a_byte_moves(node, monkeypatch):
    monkeypatch.setattr(vector_place, "_free", lambda path: (1024, 0))
    with pytest.raises(vector_place.PlaceError, match="nothing was copied"):
        _to_build(node)
    assert not (node.build / "lance" / "c").exists() and vector_place.begun(node.base / "state") is None
    assert vector_place.plan(node.root, "c", vector_place.BUILD)["refusal"]
    _one_valid_table(node)


def test_a_begun_move_names_its_direction(node, monkeypatch):
    _kill_at(monkeypatch, "verified")
    with pytest.raises(Killed):
        _to_build(node)
    with pytest.raises(vector_place.PlaceError, match="a move to build is begun"):
        vector_place.move(node.root, "c", vector_place.HOT)


def test_the_digest_is_recreates_tree_digest(node):
    files, links, _d = vector_place._listing(node.link)
    entries = {r: (files[r][0], vector_place._sha(node.link / r)) for r in files}
    assert vector_place.digest(entries, links) == recreate.tree_digest(node.link)


# ---- forget and rename follow the link ----------------------------------------------

def test_forget_removes_the_build_areas_folder(node):
    from app.hold import forget as forget_mod
    _to_build(node)
    plan = forget_mod.forget(node.root, "c", dry_run=True)
    assert str(node.build / "lance" / "c") in plan["would_remove"]
    forget_mod.forget(node.root, "c")
    assert not (node.build / "lance" / "c").exists() and not node.base.exists()


def test_forget_vectors_removes_the_table_behind_the_link(node):
    from app.hold import forget as forget_mod
    _to_build(node)
    plan = forget_mod.forget(node.root, "c", kinds=["vectors"], dry_run=True)
    assert str(node.build / "lance" / "c") in plan["would_remove"]
    forget_mod._forget_vectors(node.root, "c", None, node.base, kinds=("vectors",), dry_run=False)
    assert not (node.build / "lance" / "c").exists()
    assert not node.link.exists() and not node.link.is_symlink()
    assert not list(node.base.glob("lance.forgotten-*"))


def test_rename_carries_the_build_areas_folder_and_its_link(node, monkeypatch):
    from app.hold import rename as rename_mod
    _to_build(node)
    (node.root / "c" / "shard").mkdir()
    moves = rename_mod.plan(node.root, "c", "d")
    assert moves["lance"] == [str(node.build / "lance" / "c"), str(node.build / "lance" / "d")]
    rename_mod.rename(node.root, "c", "d")
    link = vector_place.table_dir(node.root, "d")
    assert vector_place.physical(link) == node.build / "lance" / "d" and not (node.build / "lance" / "c").exists()
    assert spaces.known_lance_rows(link) == node.rows


def test_rename_waits_for_a_begun_move(node, monkeypatch):
    from app.hold import rename as rename_mod
    from app.hold.codex import CodexError
    _kill_at(monkeypatch, "verified")
    with pytest.raises(Killed):
        _to_build(node)
    with pytest.raises(CodexError, match="is begun"):
        rename_mod.plan(node.root, "c", "d")


# ---- the command ---------------------------------------------------------------------

def test_the_command_is_a_dry_run_by_default_and_asks_for_the_pause(node, capsys):
    from app import louise, node_state
    from app.hold import collection as col
    from app.hold.codex import License
    from tests.hold.conftest import pubmed
    col.create(node.root, pubmed(license=License("green", None)), name="p")
    coll = _collection(node.root, "p")
    _write_table(coll)
    digest = recreate.tree_digest(coll.lance_dir)
    argv = ["hold", "vectors", "move", "p", "--to", "build", "--root", str(node.root)]
    assert louise.main(argv) == 0
    out = capsys.readouterr()
    assert '"dry_run": true' in out.out and "frees" in out.err and "at the measured rates" in out.err
    assert not Path(coll.lance_dir).is_symlink(), "a dry run moves nothing"
    node_state.set_worker_state("running", by="test")
    assert louise.main([*argv, "--apply"]) == 2
    assert "louise node pause p" in capsys.readouterr().err
    node_state.set_paused("p", True, by="test")
    assert louise.main([*argv, "--apply"]) == 0
    assert vector_place.tier(coll.lance_dir) == "build" and recreate.tree_digest(coll.lance_dir) == digest
    assert '"moved": true' in capsys.readouterr().out
    assert louise.main(["hold", "vectors", "move", "p", "--to", "hot", "--root", str(node.root), "--apply"]) == 0
    assert not Path(coll.lance_dir).is_symlink() and recreate.tree_digest(coll.lance_dir) == digest
