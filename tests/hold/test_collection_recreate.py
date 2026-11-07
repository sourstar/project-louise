"""`hold collection new --re-create`: case F of the collection workflow, a
RE-CREATION (docs/notes/new-collection.md; CLAUDE.md RULE 1). Normattiva,
2026-11-07: its Codex names an engine whose files are lost, and the operator
chose to re-create it with a Codex naming the archived current engine, its
vectors carried over unchanged.

The fixture is that case in small: a built, snapshotted gutenberg collection
`old` with a vector per chunk (fake_embed of its text, stored in f16), and a
recipe making the same records and the same space on another ENGINE. The
card, the CPU twin, the CPU reference and the gate are stood in for, as in
test_collection_new.py; the carry, the seeding, the sample, the digests and
the swap are the real ones."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest

from app import db, embeddings, louise, run_check
from app.hold import calibrate as calib, carry as carry_mod, codex as cx, collection as col, \
    forget as forget_mod, land, records as rec, recreate, snapshot as sn, workflow as wf
from app.pack import records as pack_records
from tests.hold.conftest import KEY, NOW, PLATFORM, WEIGHTS, fake_embed, fake_query_embed
from tests.hold.test_template import ENGINE_TABLE, SPACE, recipe

quiet = lambda *_a, **_k: None                       # noqa: E731
URL = "http://localai:8080"
#: a symmetric space (case B is not F's)
OLD_SPACE = {**SPACE, "query": {"encoder": None, "instruction": None}}
#: the archived current engine: another build of the same weights
NEW_ENGINE = {**ENGINE_TABLE, "build": "b20000", "image": "sha256:" + "d1" * 32}
CARD_RUN = "sha256:run-card"


def _new_recipe(tmp_path, **space_over) -> str:
    """The re-creation's recipe: the predecessor's, its engine the new one,
    no tolerance stated (it is measured)."""
    space = {k: v for k, v in OLD_SPACE.items() if k != "tolerance"}
    space["encoder"] = {**OLD_SPACE["encoder"], "engine": NEW_ENGINE}
    space.update(space_over.pop("space", {}))
    p = tmp_path / "recipe.json"
    p.write_text(json.dumps(recipe(space=space, **space_over)), encoding="utf-8")
    return str(p)


@pytest.fixture
def held(isolated_config, tmp_path, corpus_dir, monkeypatch):
    """`old`: built, frozen as 2026-08, a vector per chunk, its file naming
    its encoder, shared and embedding everything."""
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path / "louise"))
    monkeypatch.delenv("LOUISE_PLATFORM_IMAGE", raising=False)
    root = tmp_path / "collections"
    old = col.create_from_recipe(root, "gutenberg", recipe(space=dict(OLD_SPACE)), platform_image=PLATFORM,
                                 embed=fake_embed, embed_query=fake_query_embed, name="old", key=KEY, now=NOW)
    louise._write_held_file(root, old, URL, api="vega", embed_model="vega-m", share=True, embed="all")
    rec.build(root, "old", corpus_dir=corpus_dir, records_per_shard=2, log=quiet)
    rec.write_validation(root, "old")
    snap = sn.create(root, "old", snapshot_id="2026-08", log=quiet)
    coll = rec._local_collection(root, "old", old)
    con = db.connect(coll)
    try:
        con.execute("INSERT INTO space(id, space_id, dim, pinned_at) VALUES(1, ?, ?, 0)",
                    (old.space_id, old.space.dim))
    finally:
        con.close()
    rows = []
    for s in snap.shards:
        for cid, e in land.cut(old, list(pack_records.iter_records(root / "old" / "shard" / s.path))).items():
            rows.append({"id": cid, "book_id": e["book_id"], "lang": e["lang"], "seq": e["seq"],
                         "chapter": e["chapter"], "text": e["text"], "vector": fake_embed([e["text"]])[0]})
    embeddings.upsert_chunks(rows, collection=coll, dtype=old.space.dtype)
    return root, old, len(rows)


class Counting:
    """The new engine's run: every text it is asked, counted."""

    def __init__(self, fn):
        self.fn, self.calls, self.texts, self.runs = fn, 0, 0, set()

    def __call__(self, texts):
        self.calls += 1
        self.texts += len(texts)
        self.runs = {CARD_RUN}
        return self.fn(texts)


@pytest.fixture
def steps(held, monkeypatch):
    """The card, the twin, calibrate, the CPU reference and the gate stood
    in for; `create` and everything after it real, on the fake encoder."""
    root, _old, _n = held
    seen: dict = {"order": [], "encoder": Counting(fake_embed)}

    def stated_run(recipe_, probe_item=None):
        if recipe_.model.endswith("-cpu"):
            return SimpleNamespace(driver="cpu", weights=WEIGHTS, engine=dict(NEW_ENGINE), run_id="sha256:twin")
        return SimpleNamespace(driver="sycl", weights=WEIGHTS, engine=dict(NEW_ENGINE), run_id=CARD_RUN)
    monkeypatch.setattr(run_check, "stated_run", stated_run)
    monkeypatch.setattr(calib, "http_embed", lambda ctx, **kw: (lambda ep, texts: None))

    def measure(reference, against, *, set_id, texts, embed):
        seen["order"].append("calibrate")
        return {"tolerance": "0.999", "set": set_id, "reference": {"runs": [CARD_RUN]}}
    monkeypatch.setattr(calib, "measure", measure)

    def create_from(root_, plugin, recipe_, *, platform_image=None, encoder=None, name=None, api=None,
                    embed_model=None, **_kw):
        seen["order"].append("create")
        codex = col.create_from_recipe(root_, plugin, recipe_,
                                       platform_image=louise.preflight_create(recipe_, platform_image),
                                       embed=fake_embed, embed_query=fake_query_embed, name=name, key=KEY,
                                       now=NOW)
        return codex, louise._write_held_file(root_, codex, encoder, api=api, embed_model=embed_model)
    monkeypatch.setattr(louise, "create_from", create_from)

    @contextmanager
    def sample_embed(root_, name, codex):
        seen["order"].append("sample")
        yield seen["encoder"]
    monkeypatch.setattr(louise, "_carry_sample_embed", sample_embed)

    real_carry = carry_mod.carry

    def carry(*a, **kw):
        seen["order"].append("carry")
        return real_carry(*a, **kw)
    monkeypatch.setattr(carry_mod, "carry", carry)

    def cpu_reference(a):
        seen["order"].append("cpu-reference")
        return seen.get("cpu_rc", 0)
    monkeypatch.setattr(louise, "cmd_embedder_cpu_reference", cpu_reference)

    def gate(a):
        # as the real gate does: its collection's write lock, held until the
        # command ends (normattiva's swap, 2026-11-07, refused by it)
        from app.hold import collection as col_
        louise._take_write_lock(a.root, col_.resolve(a.root, a.collection)[0], "hold embedder check")
        seen["order"].append("gate")
        return seen.get("gate_rc", 0)
    monkeypatch.setattr(louise, "cmd_embedder_check", gate)
    return seen


def _new(root, recipe_path, *extra):
    return louise.main(["hold", "collection", "new", "gutenberg", "--root", str(root), "--recipe", recipe_path,
                        "--name", "old", "--re-create", "--cpu-model", "vega-m-cpu", "--sample", "4", *extra])


def test_a_re_creation_carries_the_vectors_and_stops_before_the_swap_deleting_nothing(
        held, steps, tmp_path, capsys):
    root, old, chunks = held
    assert chunks > 4, "the fixture holds more chunks than the sample re-encodes"
    assert _new(root, _new_recipe(tmp_path)) == 2, "the swap waits for the operator's forget"
    assert steps["order"] == ["calibrate", "create", "sample", "carry", "cpu-reference", "gate"]
    assert steps["encoder"].texts == 4, "the sample alone reached the encoder: N, never the corpus"
    new = col.read(root, "old.next")
    assert new.space_id == old.space_id and new.collection_id != old.collection_id
    assert new.space.encoder.engine.engine_id == cx.engine_from_dict(NEW_ENGINE, "e").engine_id
    assert old.space.encoder.engine.engine_id == cx.engine_from_dict(ENGINE_TABLE, "e").engine_id
    assert recreate.counts(root, "old.next") == recreate.counts(root, "old") == \
        {"documents": recreate.counts(root, "old")["documents"], "vectors": chunks}
    kept = json.loads(wf.progress_path("old").read_text())["steps"]["carry"]["result"]
    assert kept["sample"]["checked"] == 4 and kept["sample"]["exact"] == 4 and kept["sample"]["verdict"] == "pass"
    assert kept["lance"]["sha256"] == recreate.tree_digest(root / "old" / "lance")["sha256"]
    assert kept["counts"]["to"]["vectors"] == chunks
    err = capsys.readouterr().err
    assert "louise hold collection forget old --yes" in err and "freed" in err
    # nothing deleted: the predecessor whole, held, with its vectors
    assert (root / "old.toml").is_file() and (root / "old" / "lance").is_dir()
    assert col.read(root, "old").collection_id == old.collection_id
    assert recreate.counts(root, "old")["vectors"] == chunks


def test_after_the_operators_forget_the_swap_takes_the_name_and_the_file(held, steps, tmp_path, capsys):
    from app import node_state
    root, old, chunks = held
    recipe_path = _new_recipe(tmp_path)
    assert _new(root, recipe_path) == 2
    made = col.read(root, "old.next")
    forget_mod.forget(root, "old")                     # the operator's, in the session that deletes
    steps["order"].clear()
    capsys.readouterr()
    assert _new(root, recipe_path, "--dry-run") == 0, "the runbook's step 5 reads its dry run first"
    said = capsys.readouterr().out
    assert f"kept from the earlier run: {recreate.counts(root, 'old.next')['documents']:,} documents, " \
           f"{chunks:,} vector rows" in said
    assert all(f"{k:<13} kept from an earlier run" in said
               for k in ("calibrate", "tolerance", "make", "carry"))
    assert all(f"{k:<13} runs again (on every run)" in said for k in ("cpu-reference", "gate")), \
        "the dry run says what the run does: the gate against the run that answers now"
    assert steps["order"] == []
    assert _new(root, recipe_path) == 0, "the swap is not refused by the gate of its own run"
    assert "carry" not in steps["order"] and steps["encoder"].texts == 4, "carried once"
    assert steps["order"][-2:] == ["cpu-reference", "gate"]
    name, codex = col.resolve(root, "old")
    assert (name, codex.collection_id) == ("old", made.collection_id)
    assert not (root / "old.next").exists()
    assert recreate.counts(root, "old")["vectors"] == chunks
    import tomllib
    file = tomllib.loads((root / "old.toml").read_text())
    assert file["codex"] == made.collection_id and file["embed"] == "off" and file["share"] is True
    assert file["encoder"]["url"] == URL
    err = capsys.readouterr().err
    assert "louise hold config apply old --set embed=all" in err
    assert "old" in node_state.paused_collections(), "paused until the operator resumes it"
    assert _new(root, recipe_path) == 0, "done is done: every step kept"


def test_a_stop_after_the_carry_resumes_without_carrying_again(held, steps, tmp_path):
    root, _old, _n = held
    steps["cpu_rc"] = 1
    assert _new(root, _new_recipe(tmp_path)) == 1
    assert steps["order"] == ["calibrate", "create", "sample", "carry", "cpu-reference"]
    steps["order"].clear()
    steps["cpu_rc"] = 0
    assert _new(root, _new_recipe(tmp_path)) == 2
    assert steps["order"] == ["cpu-reference", "gate"], "calibrate, make and carry checked, not redone"
    assert steps["encoder"].calls == 1, "the sample was not re-encoded"


def test_a_sample_worse_than_the_tolerance_refuses_and_carries_nothing(held, steps, tmp_path, capsys):
    root, _old, chunks = held
    steps["encoder"].fn = lambda texts: fake_embed(texts, tag=b"another function")
    assert _new(root, _new_recipe(tmp_path)) == 1
    assert "carry" not in steps["order"] and "cpu-reference" not in steps["order"]
    first = capsys.readouterr().err
    assert "below tolerance 0.999" in first
    assert _new(root, _new_recipe(tmp_path)) == 1
    seed_of = lambda err: err.split("(seed ", 1)[1].split(";", 1)[0].split(")", 1)[0]  # noqa: E731
    assert seed_of(capsys.readouterr().err) == seed_of(first), \
        "the same request on the same run draws the same chunks: a failure is not drawn away"
    assert not (root / "old.next" / "lance").exists(), "not a vector copied"
    assert recreate.counts(root, "old.next")["documents"] in (None, 0)
    assert recreate.counts(root, "old")["vectors"] == chunks
    assert "carry" not in json.loads(wf.progress_path("old").read_text())["steps"]


def test_a_re_creation_keeps_the_space_and_the_records_and_changes_something(held, steps, tmp_path, capsys):
    root, _old, _n = held
    for over, says in (({"space": {"dim": 384}}, "space is not old's"),
                       ({"parser": {"id": "gutenberg", "version": 99}}, "records are not old's"),
                       ({"space": {"encoder": {**OLD_SPACE["encoder"]}}}, "nothing to re-create")):
        steps["order"].clear()
        assert _new(root, _new_recipe(tmp_path, **over)) == 2, over
        assert steps["order"] == [], "refused before anything is measured"
        assert says in capsys.readouterr().err, over
    assert not (root / "old.next").exists()


def test_the_carry_asks_for_the_predecessors_pause_while_the_worker_holds_it(held, steps, tmp_path, capsys):
    """RULE 1: a tool that needs the worker's writers asks for a stop."""
    from app.hold import lock as lock_mod
    root, _old, _n = held
    worker = lock_mod.take(lock_mod.state_dir(root, "old"), "old", "the worker")
    try:
        assert _new(root, _new_recipe(tmp_path)) == 3
    finally:
        worker.release()
    assert "louise node pause old" in capsys.readouterr().err
    assert "carry" not in steps["order"] and steps["encoder"].calls == 0
    steps["order"].clear()
    assert _new(root, _new_recipe(tmp_path)) == 2, "resumed at the carry once it is let go"
    assert steps["order"] == ["sample", "carry", "cpu-reference", "gate"]


def test_a_copy_with_no_room_is_refused_before_a_byte_moves(held, steps, tmp_path, monkeypatch, capsys):
    root, _old, _n = held
    monkeypatch.setattr(recreate, "room", lambda *a, **k: (10 << 30, 1 << 30))
    assert _new(root, _new_recipe(tmp_path)) == 2
    assert "needs 10.0 GiB and 1.0 GiB is free" in capsys.readouterr().err
    assert "carry" not in steps["order"] and not (root / "old.next" / "lance").exists()


def test_a_flag_that_moves_the_endpoint_is_refused(held, steps, tmp_path, capsys):
    root, _old, _n = held
    assert _new(root, _new_recipe(tmp_path), "--encoder", "http://elsewhere:9") == 2
    assert "keeps the predecessor's endpoint" in capsys.readouterr().err and steps["order"] == []


def test_nothing_held_under_the_name_is_nothing_to_re_create(held, steps, tmp_path, capsys):
    root, _old, _n = held
    assert louise.main(["hold", "collection", "new", "gutenberg", "--root", str(root), "--recipe",
                        _new_recipe(tmp_path), "--name", "nothing", "--re-create", "--cpu-model", "m-cpu"]) == 2
    assert "nothing held under 'nothing'" in capsys.readouterr().err


def test_the_steps_are_case_fs_numbered_in_its_order(held, steps, tmp_path, capsys):
    root, _old, _n = held
    _new(root, _new_recipe(tmp_path))
    said = [line.split()[1] for line in capsys.readouterr().err.splitlines()
            if line[:2] in {f"{i}/" for i in range(1, 10)}]
    assert list(dict.fromkeys(said)) == list(wf.ordered(wf.case("F")))


# ---- the dry run (the pilot rule: München reads a dry run before every whole run) ----

def _louise_error(err: str) -> str:
    """The refusal `main` printed: its last `louise: ` line."""
    return [line for line in err.splitlines() if line.startswith("louise: ")][-1]


def test_a_dry_run_writes_nothing_and_says_what_the_run_would_do(held, steps, tmp_path, capsys):
    root, old, chunks = held
    recipe_path = _new_recipe(tmp_path)
    before = recreate.tree_digest(tmp_path)
    assert _new(root, recipe_path, "--dry-run") == 0
    assert recreate.tree_digest(tmp_path) == before, "not a byte written under the node's roots"
    assert steps["order"] == [] and steps["encoder"].calls == 0, "stopped before calibrate"
    said = capsys.readouterr().out
    documents = recreate.counts(root, "old")["documents"]
    assert "space.encoder.engine (the engine alone, as expected)" in said
    assert f"space_id    {old.space_id} kept (old's)" in said
    assert f"{documents:,} documents, {chunks:,} vector rows" in said and "counted from old at " in said
    assert "free where old.next is made: enough" in said
    assert "louise node pause old" in said
    plan = [line.split()[1] for line in said.splitlines() if line.startswith("    ") and "/" in line.split()[0]]
    assert plan == list(wf.ordered(wf.case("F")))
    assert "1/9 resolve       ran in this dry run" in said and "3/9 calibrate     would run" in said
    seed = said.split("seed ", 1)[1].split()[0]
    # the whole run, after it, draws with the seed the dry run printed
    assert _new(root, recipe_path) == 2
    assert json.loads(wf.progress_path("old").read_text())["steps"]["carry"]["result"]["sample"]["seed"] == seed


def test_a_dry_run_takes_no_lock(held, steps, tmp_path, capsys):
    """While the worker holds the predecessor's write lock the whole run
    stops at the carry (exit 3); the dry run reads, and is not stopped."""
    from app.hold import lock as lock_mod
    root, _old, _n = held
    recipe_path = _new_recipe(tmp_path)
    worker = lock_mod.take(lock_mod.state_dir(root, "old"), "old", "the worker")
    try:
        before = recreate.tree_digest(tmp_path)
        assert _new(root, recipe_path, "--dry-run") == 0
        assert recreate.tree_digest(tmp_path) == before
    finally:
        worker.release()


@pytest.mark.parametrize("over, extra, name, says", [
    ({"space": {"dim": 384}}, (), "old", "space is not old's"),
    ({"parser": {"id": "gutenberg", "version": 99}}, (), "old", "records are not old's"),
    ({"space": {"encoder": {**OLD_SPACE["encoder"]}}}, (), "old", "nothing to re-create"),
    ({}, ("--encoder", "http://elsewhere:9"), "old", "keeps the predecessor's endpoint"),
    ({}, (), "nothing", "nothing held under 'nothing'"),
    ({}, (), "old", "needs 10.0 GiB and 1.0 GiB is free"),
], ids=["space", "records", "itself", "endpoint", "no-predecessor", "no-room"])
def test_a_dry_run_refuses_what_the_run_refuses(held, steps, tmp_path, monkeypatch, capsys,
                                                 over, extra, name, says):
    root, _old, _n = held
    if says.startswith("needs"):
        monkeypatch.setattr(recreate, "room", lambda *a, **k: (10 << 30, 1 << 30))
    recipe_path = _new_recipe(tmp_path, **over)

    def new(*more):
        return louise.main(["hold", "collection", "new", "gutenberg", "--root", str(root), "--recipe",
                            recipe_path, "--name", name, "--re-create", "--cpu-model", "vega-m-cpu",
                            "--sample", "4", *extra, *more])
    before = recreate.tree_digest(tmp_path)
    dry_rc = new("--dry-run")
    dry = _louise_error(capsys.readouterr().err)
    assert recreate.tree_digest(tmp_path) == before, "a refused dry run writes nothing either"
    assert steps["order"] == []
    rc = new()
    assert (dry_rc, dry) == (rc, _louise_error(capsys.readouterr().err)), "the same exit, the same sentence"
    assert rc == 2 and says in dry


def test_a_toml_recipe_with_no_variant_runs_as_its_dry_run_said(held, steps, tmp_path, capsys):
    """München, 2026-11-07: the recipe is TOML, which has no null, so its
    engine table carries no `variant`; the dry run passed and the run stopped
    at calibrate on "missing field(s) variant" — the engine went to
    engine_from_dict raw. Both now read it through the recipe reader
    (`variant` absent is null) at resolve, and the run gets as far as its
    dry run said."""
    root, _old, _n = held
    no_variant = {k: v for k, v in NEW_ENGINE.items() if k != "variant"}
    space = {k: v for k, v in OLD_SPACE.items() if k != "tolerance"}
    space["encoder"] = {**OLD_SPACE["encoder"], "engine": no_variant}
    recipe_path = _new_recipe(tmp_path, space=space)
    assert _new(root, recipe_path, "--dry-run") == 0
    capsys.readouterr()
    assert _new(root, recipe_path) == 2, "the run stops at the swap, as a re-creation does"
    assert "missing field" not in capsys.readouterr().err
    assert "carry" in steps["order"] and "gate" in steps["order"]


def test_a_malformed_engine_is_refused_by_the_dry_run_as_by_the_run(held, steps, tmp_path, capsys):
    """The dry run builds both engines, so an engine the run cannot use is
    refused before calibrate, with the run's own sentence and exit."""
    root, _old, _n = held
    bad = {**NEW_ENGINE, "args": "llama-server"}
    space = {k: v for k, v in OLD_SPACE.items() if k != "tolerance"}
    space["encoder"] = {**OLD_SPACE["encoder"], "engine": bad}
    recipe_path = _new_recipe(tmp_path, space=space)
    before = recreate.tree_digest(tmp_path)
    dry_rc = _new(root, recipe_path, "--dry-run")
    dry = _louise_error(capsys.readouterr().err)
    assert recreate.tree_digest(tmp_path) == before and steps["order"] == []
    rc = _new(root, recipe_path)
    assert (dry_rc, dry) == (rc, _louise_error(capsys.readouterr().err)) and rc == 2
    assert "args is a list of strings" in dry


# ---- the pieces ---------------------------------------------------------------

def test_the_carried_file_is_the_predecessors_but_its_codex_and_embed():
    text = ('codex = "sha256:aa"\ntitle = "N"\nslices = "all"\nkinds = ["records", "vectors"]\n'
            'embed = "p2p"\nshare = true\n\n[encoder]\nurl = "http://x"\napi = "vega"\n'
            'embed_model = "m"\nquery_model = "m"\nbatch = 16\n')
    out, mode = recreate.carried_file(text, "sha256:bb")
    import tomllib
    a, b = tomllib.loads(text), tomllib.loads(out)
    assert mode == "p2p" and b["codex"] == "sha256:bb" and b["embed"] == "off"
    assert {k: v for k, v in b.items() if k not in ("codex", "embed")} == \
        {k: v for k, v in a.items() if k not in ("codex", "embed")}
    with pytest.raises(cx.CodexError):
        recreate.carried_file('title = "no codex"\n', "sha256:bb")


def test_a_tree_digest_sees_one_byte(tmp_path):
    a = tmp_path / "a"
    (a / "d").mkdir(parents=True)
    (a / "d" / "f").write_bytes(b"x" * 1000)
    (a / "g").write_bytes(b"y")
    b = tmp_path / "b"
    import shutil
    shutil.copytree(a, b)
    assert recreate.tree_digest(a) == recreate.tree_digest(b)
    (b / "d" / "f").write_bytes(b"x" * 999 + b"z")
    assert recreate.tree_digest(a)["sha256"] != recreate.tree_digest(b)["sha256"]
    assert recreate.tree_digest(tmp_path / "none") == {"files": 0, "bytes": 0, "sha256": None}


def test_the_forget_plan_names_the_shared_folders_and_frees_only_what_is_not_linked(tmp_path, monkeypatch):
    """`forget --dry-run` named the directory alone and under-said the SHARD,
    origin and bundles folders its links point at; the size counts a part
    the re-created collection hard-linked as kept."""
    root, shards, origins = tmp_path / "collections", tmp_path / "shards", tmp_path / "origins"
    monkeypatch.setenv("SHARDS_ROOT", str(shards))
    monkeypatch.setenv("ORIGINS_ROOT", str(origins))
    for d in (root / "p", root / "p.next", shards / "p" / "parts", origins / "p"):
        d.mkdir(parents=True)
    (root / "p" / "shard").symlink_to(shards / "p", target_is_directory=True)
    (root / "p" / "origin").symlink_to(origins / "p", target_is_directory=True)
    (root / "p.next" / "origin").symlink_to(origins / "p", target_is_directory=True)
    (shards / "p" / "parts" / "a").write_bytes(b"a" * 4096)
    (root / "p" / "vectors").write_bytes(b"v" * 1024)
    os.link(shards / "p" / "parts" / "a", root / "p.next" / "a")      # seeded: one inode, two names
    (root / "p.toml").write_text('codex = "sha256:00"\n')
    plan = recreate.forget_plan(root, "p")
    assert str(shards / "p") in plan["would_remove"] and str(origins / "p") in plan["kept"]
    assert plan["bytes"] >= 4096 + 1024 and plan["freed"] == plan["bytes"] - 4096


# ---- the build area (storage-tiering.md §8, operator 2026-11-07) ----

def _the_nodes_own(root, monkeypatch) -> None:
    """`root` made the node's own (`<LOUISE_ROOT>/collections`): the build
    area places only there; its collection files then live in CONFIG_DIR."""
    from app import collections as collections_mod
    monkeypatch.setenv("LOUISE_ROOT", str(root.parent))
    dst = collections_mod.config_toml_path("old")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(root / "old.toml", dst)


def test_with_a_build_area_the_copy_is_built_there_and_the_swap_moves_it_to_the_data_pool(
        held, steps, tmp_path, monkeypatch, capsys):
    """Case F: `<name>.next`'s table is carried onto the build area, proven
    byte for byte there; at the swap, after the operator's forget, it moves
    to the data pool under the name — the same bytes, the build area
    emptied."""
    from app import config, node_state, vector_place
    root, _old, chunks = held
    _the_nodes_own(root, monkeypatch)
    build = tmp_path / "ssd3"
    build.mkdir()
    monkeypatch.setattr(config, "BUILD_DIR", build)
    recipe_path = _new_recipe(tmp_path)
    assert _new(root, recipe_path, "--dry-run") == 0
    said = capsys.readouterr().out
    assert f"vectors     built in the build area, {build / 'lance' / 'old.next'}; moved to the data pool " \
           f"at the swap" in said
    assert "free where old.next is made: enough" in said, "one filesystem here: one line"
    assert _new(root, recipe_path) == 2
    link = root / "old.next" / "lance"
    assert link.is_symlink() and vector_place.physical(link) == build / "lance" / "old.next"
    digest = recreate.tree_digest(root / "old" / "lance")
    assert recreate.tree_digest(link) == digest, "carried onto the build area, byte for byte"
    assert recreate.counts(root, "old.next")["vectors"] == chunks
    forget_mod.forget(root, "old")
    capsys.readouterr()
    assert _new(root, recipe_path) == 0
    lance = root / "old" / "lance"
    assert lance.is_dir() and not lance.is_symlink(), "the swap moved the table to the data pool"
    assert recreate.tree_digest(lance) == digest
    assert recreate.counts(root, "old")["vectors"] == chunks
    assert not any((build / "lance").iterdir()), "nothing left in the build area"
    assert "old" in node_state.paused_collections()
    kept = json.loads(wf.progress_path("old").read_text())["steps"]["swap"]["result"]
    assert kept["vectors"]["tier"] == "hot" and kept["vectors"]["rows"] == chunks


def test_a_swap_whose_move_is_refused_is_not_done_and_resumes(held, steps, tmp_path, monkeypatch, capsys):
    from app import config, vector_place
    root, _old, chunks = held
    _the_nodes_own(root, monkeypatch)
    build = tmp_path / "ssd3"
    build.mkdir()
    monkeypatch.setattr(config, "BUILD_DIR", build)
    recipe_path = _new_recipe(tmp_path)
    assert _new(root, recipe_path) == 2
    forget_mod.forget(root, "old")
    real = vector_place._free
    monkeypatch.setattr(vector_place, "_free", lambda path: (1024, 0))
    assert _new(root, recipe_path) == 2
    assert "nothing was copied" in capsys.readouterr().err
    link = root / "old" / "lance"
    assert link.is_symlink() and recreate.counts(root, "old")["vectors"] == chunks, \
        "renamed, searched from the build area meanwhile"
    assert not (json.loads(wf.progress_path("old").read_text())["steps"].get("swap") or {}).get("result", {}).get("done")
    monkeypatch.setattr(vector_place, "_free", real)
    assert _new(root, recipe_path) == 0
    assert not link.is_symlink() and recreate.counts(root, "old")["vectors"] == chunks
