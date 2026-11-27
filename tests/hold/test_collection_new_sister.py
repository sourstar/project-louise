"""`hold collection new --sister-of`: case J of docs/notes/new-collection.md,
a SISTER into a held space (operator, 2026-11-08: "Make J, it is time to do
it" — normattiva-nemo, Normattiva's records in legi's Nemotron space).

End to end on real collections: her sister holding vectors, a collection of
another space, `derive`, the parts seeded, the manifest and text index
carried with nothing of her sister's vectors said held (hold/carry.py A1i).
Only the card is stood in for: the twin's stated run, the CPU reference and
the gate, which are tested where they live."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from app import louise, run_check, spaces
from app.hold import collection as col
from app.hold.codex import CodexError
from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed
from tests.hold.test_carry import _local, _old, _vectors, quiet  # noqa: F401
from tests.hold.test_template import SPACE, recipe
from tests.hold.test_carry import pair  # noqa: F401  (the fixture)


def _bge_space(root, name="bge"):
    """Another FUNCTION, as legi's Nemotron space is to Normattiva's: other
    weights and width, a query instruction and no question encoder."""
    from functools import partial
    space = {**SPACE, "encoder": {**SPACE["encoder"], "weights": "sha256:" + "be" * 32},
             "dim": 1024, "query": {"encoder": None, "instruction": "query: "}}
    return col.create_from_recipe(root, "gutenberg", recipe(space=space, work={"tokens": 7_000_000}),
                                  platform_image=PLATFORM, embed=partial(fake_embed, dim=1024),
                                  name=name, key=KEY, now=NOW)


@pytest.fixture
def card(monkeypatch, tmp_path):
    seen = {"order": []}
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path / "louise"))

    def stated_run(recipe, probe_item=None):
        return SimpleNamespace(driver="cpu", weights=seen.get("weights"), engine={}, run_id="sha256:twin")
    monkeypatch.setattr(run_check, "stated_run", stated_run)
    monkeypatch.setattr(louise, "cmd_embedder_cpu_reference",
                        lambda a: seen["order"].append(("cpu-reference", a.collection)) or 0)
    monkeypatch.setattr(louise, "cmd_embedder_check",
                        lambda a: seen["order"].append(("gate", a.collection)) or 0)
    from app import node_state
    monkeypatch.setattr(node_state, "embedding_blockers", lambda name, x: [])
    return seen


def _new(root, *extra, name="sis"):
    return louise.main(["hold", "collection", "new", "gutenberg", "--root", str(root), "--name", name,
                        "--sister-of", "old", "--space-from", "bge", "--encoder", "http://localai:8080",
                        "--api", "vega", "--embed-model", "vega-nemo", "--cpu-model", "vega-nemo-cpu",
                        *extra])


def _ready(root, old, card):
    _vectors(root, old)
    con = sqlite3.connect(root / "old" / "state" / "manifest.sqlite")
    con.execute("UPDATE documents SET embed_done=1, embed_version=7")
    con.execute("INSERT INTO chunk_hashes(text_hash, chunk_id, native_id) VALUES(x'01', 'x#0', 1)")
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    con.close()
    (root / "old" / "index").mkdir(parents=True, exist_ok=True)
    (root / "old" / "index" / "words").write_text("the text index")
    bge = _bge_space(root)
    card["weights"] = bge.space.encoder.weights
    return bge, n


def test_a_sister_is_derived_into_the_held_space_and_carries_records_index_and_no_vector(pair, card):
    root, old, _new_codex = pair
    bge, n = _ready(root, old, card)
    assert _new(root) == 0
    name, sister = col.resolve(root, "sis")
    assert sister.space_id == bge.space_id and sister.derived_from == old.collection_id
    def cut(c):
        return [(l.chunker.to_dict(), l.rule, l.form) for l in c.layers]
    assert sister.layers == bge.layers and cut(sister) == cut(old), "the space's layers: her sister's cut"
    assert sister.space.calibration.vectors == bge.space.calibration.vectors, \
        "the held space's reference, inherited: nothing embedded"
    assert card["order"] == [("cpu-reference", "sis"), ("gate", "sis")]
    con = sqlite3.connect(root / "sis" / "state" / "manifest.sqlite")
    try:
        assert con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == n
        assert con.execute("SELECT COUNT(*) FROM documents WHERE embed_done>0 OR embed_version<>0"
                           ).fetchone()[0] == 0, "none of her sister's vectors said held"
        assert con.execute("SELECT COUNT(*) FROM chunk_hashes").fetchone()[0] == 0
    finally:
        con.close()
    assert (root / "sis" / "index" / "words").read_text() == "the text index"
    assert not spaces.known_lance_rows(_local(root, "sis").lance_dir)
    assert spaces.known_lance_rows(_local(root, "old").lance_dir), "her sister keeps hers"
    from app.hold import workflow as wf
    prog = json.loads(wf.progress_path("sis").read_text())
    assert prog["case"] == "J" and set(prog["steps"]) == {"make", "carry", "cpu-reference", "gate"}
    from app import node_state
    assert "sis" not in node_state.paused_collections(), "unpaused once the carry ended"
    card["order"].clear()
    assert _new(root) == 0, "run again: each step kept, the carry checked by its counts"
    assert card["order"] == [("cpu-reference", "sis"), ("gate", "sis")]


def test_the_dry_run_names_case_j_and_makes_nothing(pair, card, capsys):
    root, old, _ = pair
    _ready(root, old, card)
    assert _new(root, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "case J" in out and "no vector" in out and "louise node pause old" in out
    with pytest.raises(CodexError):
        col.resolve(root, "sis")


@pytest.mark.parametrize("why", ["recipe", "no-space-from", "same-space", "other-plugin", "re-create"])
def test_what_case_j_does_not_make_is_refused_before_anything(pair, card, why, tmp_path):
    root, old, _ = pair
    _ready(root, old, card)
    args = ["hold", "collection", "new", "gutenberg", "--root", str(root), "--name", "sis",
            "--sister-of", "old", "--space-from", "bge", "--encoder", "http://localai:8080", "--api", "vega",
            "--embed-model", "vega-nemo", "--cpu-model", "vega-nemo-cpu"]
    if why == "recipe":
        r = tmp_path / "r.json"
        r.write_text("{}")
        args += ["--recipe", str(r)]
    elif why == "no-space-from":
        i = args.index("--space-from")
        del args[i:i + 2]
    elif why == "same-space":
        args[args.index("bge")] = "old"
    elif why == "other-plugin":
        args[3] = "pubmed"
    else:
        args += ["--re-create"]
    assert louise.main(args) == 2
    with pytest.raises(CodexError):
        col.resolve(root, "sis")
    assert card["order"] == []


def test_a_sister_who_cuts_otherwise_carries_her_records_and_the_worker_indexes_them(pair, card):
    """2026-11-08: normattiva-nemo cuts with the legal space's layer, her
    sister with akn_article (A1i relayered). Her manifest is carried with
    every index mark emptied and no text index; her parts are BUILT from her
    sister's record lines, none parsed, so her records are her sister's byte
    for byte and her parts' chunks her own layer's (02 §2.7 amended
    2026-11-08). Her sister keeps everything."""
    from functools import partial
    from app.hold import seed, workflow as wf
    root, old, _ = pair
    _vectors(root, old)
    (root / "old" / "index").mkdir(parents=True, exist_ok=True)
    (root / "old" / "index" / "words").write_text("the text index")
    con = sqlite3.connect(root / "old" / "state" / "manifest.sqlite")
    con.execute("UPDATE documents SET fts_done=1, fts_version=3, chunks=5, tokens=900")
    con.commit()
    n_old = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    con.close()
    space = {**SPACE, "encoder": {**SPACE["encoder"], "weights": "sha256:" + "be" * 32},
             "dim": 1024, "query": {"encoder": None, "instruction": "query: "}}
    layers = [{"rule": "default", "chunker": {"id": "fixed", "version": 1, "params": {"tokens": 3, "overlap": 0}},
               "input": {"form": "text"}}]
    other = col.create_from_recipe(root, "gutenberg", recipe(space=space, layers=layers, work={"tokens": 7_000_000}),
                                   platform_image=PLATFORM, embed=partial(fake_embed, dim=1024),
                                   name="bge", key=KEY, now=NOW)
    card["weights"] = other.space.encoder.weights
    assert _new(root) == 0
    _, sister = col.resolve(root, "sis")
    assert sister.space_id == other.space_id and [l.chunker.id for l in sister.layers] == ["fixed"]
    src, mine = seed.latest_snapshot(root, "old"), seed.latest_snapshot(root, "sis")
    assert src.records == mine.records, "every record of her sister's, none parsed"
    assert sorted(s.chunks_root for s in src.shards) != sorted(s.chunks_root for s in mine.shards), \
        "her chunks: her own layer's"

    assert sorted((s.slice, s.n, s.records_root) for s in src.shards) == \
        sorted((s.slice, s.n, s.records_root) for s in mine.shards), \
        "her records: her sister's, byte for byte (a line carries no layer, 09 §9.5)"
    assert sum(s.chunks for s in src.shards) != sum(s.chunks for s in mine.shards), \
        "her layer cut them otherwise"
    assert not (root / "sis" / "index" / "words").exists(), "the text index is NOT carried: other chunks"
    assert (root / "old" / "index" / "words").read_text() == "the text index", "her sister keeps hers"
    con = sqlite3.connect(root / "sis" / "state" / "manifest.sqlite")
    assert con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == n_old
    assert con.execute("SELECT COUNT(*) FROM documents WHERE fts_done<>0 OR fts_version<>0 OR "
                       "entity_version<>0 OR chunks<>0 OR tokens<>0 OR embed_done>0").fetchone()[0] == 0, \
        "every document indexed again by the worker, with her layer"
    con.close()
    assert not spaces.known_lance_rows(_local(root, "sis").lance_dir)
    prog = json.loads(wf.progress_path("sis").read_text())
    assert prog["steps"]["carry"]["result"]["recut"] is True
    assert card["order"] == [("cpu-reference", "sis"), ("gate", "sis")]
    card["order"].clear()
    assert _new(root) == 0, "run again: the carry kept"


def test_a_sister_written_at_version_9_or_later_states_her_older_sisters_jsonl_parts(pair, card):
    """München, 2026-11-08: normattiva-nemo refused at make — the legal space
    has a document instruction (Codex version 11), her sister is version 4,
    and "a version 9 Codex declares its record parts' container". Absent
    below version 9 means JSONL (09 §9.5): she states it, and her parts are
    her sister's (seed.records_slicing)."""
    from functools import partial
    from app.hold import codex as cx, seed
    root, old, _ = pair
    _vectors(root, old)
    (root / "old" / "index").mkdir(parents=True, exist_ok=True)
    (root / "old" / "index" / "words").write_text("the text index")
    space = {**SPACE, "encoder": {**SPACE["encoder"], "weights": "sha256:" + "be" * 32},
             "dim": 1024, "query": {"encoder": None, "instruction": "query: "},
             "document": {"instruction": "passage: "}, "exactness": "exact"}
    other = col.create_from_recipe(root, "gutenberg", recipe(space=space, work={"tokens": 7_000_000}),
                                   platform_image=PLATFORM, embed=partial(fake_embed, dim=1024),
                                   name="bge", key=KEY, now=NOW)
    assert other.version >= cx.GRAPH_VERSION and old.version < cx.GRAPH_VERSION
    assert not (isinstance(old.slicing, dict) and "container" in old.slicing)
    card["weights"] = other.space.encoder.weights
    assert _new(root) == 0
    _, sister = col.resolve(root, "sis")
    assert sister.version == other.version and sister.space_id == other.space_id
    assert sister.slicing["container"] == cx.JSONL
    assert seed.records_slicing(sister) == seed.records_slicing(old), "the same parts"
    assert seed.record_differences(old, sister) == [], "her records are her sister's"


def test_a_sister_below_version_9_of_a_jsonl_sister_leaves_the_container_out():
    from app.hold import codex as cx
    from app.hold.collection import _slicing_at
    assert _slicing_at({"records_per_shard": 2, "container": cx.JSONL}, 5) == {"records_per_shard": 2}
    assert _slicing_at({"records_per_shard": 2}, 11) == {"records_per_shard": 2, "container": cx.JSONL}
    assert _slicing_at(None, 11) == {"container": cx.JSONL}
    assert _slicing_at({"container": cx.JSONL}, 5) is None
    with pytest.raises(CodexError, match="cannot declare a container"):
        _slicing_at({"container": "parquet"}, 5)


def test_her_cpu_reference_and_gate_run_paused_and_she_is_resumed_after(pair, card, monkeypatch):
    """München, 2026-11-08: unpaused before her gate, the worker would take
    her writer (relayered: to index every document) and the gate lose it."""
    from app import node_state
    root, old, _ = pair
    _ready(root, old, card)
    seen = []
    monkeypatch.setattr(louise, "cmd_embedder_check",
                        lambda a: seen.append("sis" in node_state.paused_collections()) or 0)
    assert _new(root) == 0
    assert seen == [True]
    assert "sis" not in node_state.paused_collections()


def test_a_stop_during_her_records_build_exits_130_and_a_rerun_resumes(pair, card, monkeypatch):
    """München, 2026-11-08: SIGTERM to case J's records build gave exit 143,
    the default handler. Invariant 2: the build finishes its unit, `new`
    exits 130 with the carry not recorded, and a rerun carries again
    (repeatable) and resumes the build."""
    import signal
    from types import SimpleNamespace
    from functools import partial
    from app.hold import records as rec_mod, workflow as wf
    root, old, _ = pair
    _vectors(root, old)
    space = {**SPACE, "encoder": {**SPACE["encoder"], "weights": "sha256:" + "be" * 32},
             "dim": 1024, "query": {"encoder": None, "instruction": "query: "}}
    layers = [{"rule": "default", "chunker": {"id": "fixed", "version": 1, "params": {"tokens": 3, "overlap": 0}},
               "input": {"form": "text"}}]
    other = col.create_from_recipe(root, "gutenberg", recipe(space=space, layers=layers, work={"tokens": 7_000_000}),
                                   platform_image=PLATFORM, embed=partial(fake_embed, dim=1024),
                                   name="bge", key=KEY, now=NOW)
    card["weights"] = other.space.encoder.weights
    real = rec_mod.build
    seen = {}

    def stopped(root_, name, **kw):
        seen["handler"] = signal.getsignal(signal.SIGTERM)
        os_kill_self = kw["stop"]
        os_kill_self.set()                      # as SIGTERM's handler would
        return SimpleNamespace(interrupted=True)
    monkeypatch.setattr(rec_mod, "build", stopped)
    before = signal.getsignal(signal.SIGTERM)
    assert _new(root) == 130
    assert seen["handler"] not in (signal.SIG_DFL, before), "SIGTERM routed to the stop while it builds"
    assert signal.getsignal(signal.SIGTERM) == before, "the handler given back after"
    prog = json.loads(wf.progress_path("sis").read_text())
    assert "carry" not in (prog.get("steps") or {}) or not prog["steps"]["carry"].get("result"), \
        "nothing recorded for a carry that did not finish"
    monkeypatch.setattr(rec_mod, "build", real)
    assert _new(root) == 0, "run again: the carry repeats and the build completes"
    prog = json.loads(wf.progress_path("sis").read_text())
    assert prog["steps"]["carry"]["result"]["recut"] is True


# ---- a sister in her sister's space with a layer param changed (2026-11-14) ---------------------

@pytest.fixture
def fixed_old(isolated_config, tmp_path, corpus_dir):
    """`old` cutting with `fixed{tokens 3, overlap 0}`, built and frozen."""
    from app.hold import records as rec, snapshot as sn
    from tests.hold.conftest import fake_query_embed
    root = tmp_path / "collections"
    layers = [{"rule": "default", "chunker": {"id": "fixed", "version": 1, "params": {"tokens": 3, "overlap": 0}},
               "input": {"form": "text"}}]
    old = col.create_from_recipe(root, "gutenberg", recipe(layers=layers), platform_image=PLATFORM,
                                 embed=fake_embed, embed_query=fake_query_embed, name="old", key=KEY, now=NOW)
    rec.build(root, "old", corpus_dir=corpus_dir, records_per_shard=2, log=quiet)
    rec.write_validation(root, "old")
    sn.create(root, "old", snapshot_id="2026-08", log=quiet)
    return root, old


def _new_param(root, *params, extra=()):
    args = ["hold", "collection", "new", "gutenberg", "--root", str(root), "--name", "sis", "--sister-of", "old",
            "--encoder", "http://localai:8080", "--api", "vega", "--embed-model", "vega-nemo",
            "--cpu-model", "vega-nemo-cpu", *extra]
    for p in params:
        args += ["--layer-param", p]
    return louise.main(args)


def test_a_sister_with_one_layer_param_changed_is_a_new_space_with_her_sister_s_reference(fixed_old, card):
    """The architecture session's chunker measurement (2026-11-14): Normattiva
    with `header = false`, one factor. Her sister's layers with the param
    changed, a new space, the same encoder and reference; her records her
    sister's, her chunks cut again by her own layer."""
    from app.hold import seed
    root, old = fixed_old
    card["weights"] = old.space.encoder.weights
    assert _new_param(root, "tokens=4") == 0
    _, sister = col.resolve(root, "sis")
    assert sister.space_id != old.space_id and sister.derived_from == old.collection_id
    assert [l.chunker.params for l in sister.layers] == [{"tokens": 4, "overlap": 0}]
    assert [l.chunker.id for l in sister.layers] == ["fixed"] and sister.space.encoder == old.space.encoder
    assert sister.space.calibration.vectors == old.space.calibration.vectors, "her sister's reference"
    src, mine = seed.latest_snapshot(root, "old"), seed.latest_snapshot(root, "sis")
    assert src.records == mine.records and \
        sorted(s.records_root for s in src.shards) == sorted(s.records_root for s in mine.shards)
    assert card["order"] == [("cpu-reference", "sis"), ("gate", "sis")]


def test_the_dry_run_names_the_layer_change_and_makes_nothing(fixed_old, card, capsys):
    root, old = fixed_old
    card["weights"] = old.space.encoder.weights
    assert _new_param(root, "tokens=4", extra=("--dry-run",)) == 0
    out = capsys.readouterr().out
    assert "tokens = 4" in out and "'tokens': 3" in out and "'tokens': 4" in out
    with pytest.raises(CodexError):
        col.resolve(root, "sis")


@pytest.mark.parametrize("params, extra", [
    (("tokens=3",), ()),                        # the same value: the same space
    (("header=false",), ()),                    # a param no layer states
    (("tokens",), ()),                          # not KEY=VALUE
    (("tokens=4",), ("--space-from", "old")),   # both ways at once
])
def test_a_layer_param_that_changes_nothing_or_is_unknown_is_refused(fixed_old, card, params, extra):
    root, old = fixed_old
    card["weights"] = old.space.encoder.weights
    assert _new_param(root, *params, extra=extra) == 2
    with pytest.raises(CodexError):
        col.resolve(root, "sis")


# ---- a sister who changes only her ENCODER SIDE (--encoder-from) ------------
# (operator, 2026-11-27, gutenberg-nemo: "Remember the indexing sharing between
# sisters", "Do it by the books"): her sister's layers, the held collection's
# encoder side whole, its reference carried; her index linked, one identity

def _nemo(root, name="nemo"):
    """Another encoder side, cutting OTHERWISE than her sister (as legi's
    `legal_article` space is to gutenberg's `book`): so a copy of its space
    would cut again, and only its encoder side is what she takes."""
    from functools import partial
    space = {**SPACE, "encoder": {**SPACE["encoder"], "weights": "sha256:" + "ee" * 32},
             "dim": 1024, "query": {"encoder": None, "instruction": "query: "}}
    layers = [{"rule": "default", "chunker": {"id": "fixed", "version": 1, "params": {"tokens": 3, "overlap": 0}},
               "input": {"form": "text"}}]
    return col.create_from_recipe(root, "gutenberg", recipe(space=space, layers=layers,
                                                            work={"tokens": 7_000_000}),
                                  platform_image=PLATFORM, embed=partial(fake_embed, dim=1024),
                                  name=name, key=KEY, now=NOW)


def _ready_nemo(root, old, card):
    _, n = _ready(root, old, card)
    nemo = _nemo(root)
    card["weights"] = nemo.space.encoder.weights
    return nemo, n


def _new_enc(root, *extra, name="sis", source="nemo"):
    return louise.main(["hold", "collection", "new", "gutenberg", "--root", str(root), "--name", name,
                        "--sister-of", "old", "--encoder-from", source, "--encoder", "http://localai:8080",
                        "--api", "vega", "--embed-model", "vega-nemo", "--cpu-model", "vega-nemo-cpu", *extra])


def test_an_encoder_sister_keeps_her_sister_s_layers_and_carries_the_held_reference(pair, card, monkeypatch):
    root, old, _ = pair
    nemo, n = _ready_nemo(root, old, card)
    calls = []
    monkeypatch.setattr(col, "_write_own_calibration", lambda *a, **k: calls.append("embedded"))
    assert _new_enc(root) == 0
    _, sister = col.resolve(root, "sis")
    assert sister.space_id not in (old.space_id, nemo.space_id) and sister.derived_from == old.collection_id
    assert [l.to_dict()["chunker"] for l in sister.layers] == [l.to_dict()["chunker"] for l in old.layers], \
        "her sister's layers, not the encoder source's"
    assert sister.space.encoder == nemo.space.encoder and sister.space.dim == nemo.space.dim
    assert sister.space.query.instruction == "query: " and sister.space.tolerance == nemo.space.tolerance
    assert sister.space.calibration.vectors == nemo.space.calibration.vectors, "its reference, carried"
    assert sister.space.epoch == old.space.epoch and sister.space_from is None
    assert calls == [], "nothing embedded"
    con = sqlite3.connect(root / "sis" / "state" / "manifest.sqlite")
    try:
        assert con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == n
        assert con.execute("SELECT COUNT(*) FROM documents WHERE embed_done>0 OR embed_version<>0"
                           ).fetchone()[0] == 0, "none of her sister's vectors said held"
    finally:
        con.close()
    assert (root / "sis" / "index" / "words").read_text() == "the text index", "her sister's index, carried"
    assert not spaces.known_lance_rows(_local(root, "sis").lance_dir)
    assert card["order"] == [("cpu-reference", "sis"), ("gate", "sis")]
    from app.hold import workflow as wf
    prog = json.loads(wf.progress_path("sis").read_text())
    assert prog["case"] == "J" and set(prog["steps"]) == {"make", "carry", "cpu-reference", "gate"}
    card["order"].clear()
    assert _new_enc(root) == 0, "run again: each step kept, the carry checked by its counts"
    assert card["order"] == [("cpu-reference", "sis"), ("gate", "sis")]


def test_an_encoder_sister_has_her_sister_s_index_identity_and_slices(pair, card, monkeypatch):
    """The share the operator asked for: one identity, so the carry's link
    is joined (at once, or at the next boot's settle)."""
    import tomllib
    from app import collections as collections_mod, text_index
    from app.hold import deposit
    root, old, _ = pair
    _ready_nemo(root, old, card)
    path = deposit.held_file(root, "old")
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        louise._write_held_file(root, old, "http://localai:8080", api="vega", embed_model="vega-old")
        text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("slices = []", 'slices = "all"\nacquire_enabled = true'),
                    encoding="utf-8")
    # her sister's origin is a link to the node's corpus folder, as gutenberg's is
    import shutil
    src, target = root / "old" / "origin", root.parent / "corpora-old"
    if src.exists():
        shutil.move(str(src), str(target))
    else:
        target.mkdir()
    src.symlink_to(target)
    assert _new_enc(root) == 0
    assert (root / "sis" / "origin").resolve() == target.resolve(), "her origin is her sister's folder"
    with open(deposit.held_file(root, "sis"), "rb") as f:
        held = tomllib.load(f)
    assert held["slices"] == "all", "her sister's slices, not []"
    assert "acquire_enabled" not in held, "who acquires is the loader's, not a line in her file"
    files = deposit.held_file(root, "old"), deposit.held_file(root, "sis")
    assert root.name == "collections"
    monkeypatch.setenv("LOUISE_ROOT", str(root.parent))      # the node's layout: <LOUISE_ROOT>/collections
    a, b = collections_mod._one_acquirer([collections_mod._load_one(f) for f in files])
    assert text_index.identity(a) is not None and text_index.identity(a) == text_index.identity(b)
    assert a.slices == b.slices
    assert a.acquire_enabled and not b.acquire_enabled and b.acquire_by == "old", \
        "one acquirer a folder: her sister, while it is held and they read one folder"
    assert collections_mod.sister_refusal(a, held=[a, b]) is None
    assert "grow old" in collections_mod.sister_refusal(b, held=[a, b]), "growing her is refused"
    alone = collections_mod._one_acquirer([collections_mod._load_one(files[1])])[0]
    assert alone.acquire_enabled and alone.acquire_by is None, "her sister gone (legi_F): she acquires"
    assert collections_mod.sister_refusal(alone, held=[alone]) is None


def test_her_text_index_is_her_sister_s_own_read_where_it_lies(pair, card, monkeypatch):
    """Their index identities equal (the node computes them from the held
    files): her language groups are never copied nor hard-linked (operator,
    2026-11-27) — she reads her sister's own index where it lies until the
    worker's next boot shares it, and the carry's proof reads it so."""
    from app import text_index
    root, old, _ = pair
    _ready_nemo(root, old, card)
    g = root / "old" / "index" / "en"
    g.mkdir(parents=True, exist_ok=True)
    (g / "meta.json").write_text("{}", encoding="utf-8")
    (g / "seg.store").write_bytes(b"the index")
    monkeypatch.setattr(louise, "_index_identities", lambda root, a, b: ("id1", "id1"))
    assert _new_enc(root) == 0
    assert text_index.placement(root, "sis") == {"dir": "old/index", "identity": "id1", "reads": "old"}
    assert text_index.base(root / "sis" / "index") == root / "old" / "index"
    assert not text_index.group_dirs(root / "sis" / "index"), "no language group copied"
    assert text_index.index_digest(root, "sis")["sha256"] == text_index.index_digest(root, "old")["sha256"]
    assert not [f for f in root.rglob("*") if f.is_file() and not f.is_symlink() and f.stat().st_nlink > 1], \
        "no hard link anywhere"


def test_her_text_index_is_copied_where_the_identities_differ(pair, card, monkeypatch):
    from app import text_index
    root, old, _ = pair
    _ready_nemo(root, old, card)
    g = root / "old" / "index" / "en"
    g.mkdir(parents=True, exist_ok=True)
    (g / "meta.json").write_text("{}", encoding="utf-8")
    (g / "seg.store").write_bytes(b"the index")
    monkeypatch.setattr(louise, "_index_identities", lambda root, a, b: ("id1", "id2"))
    assert _new_enc(root) == 0
    assert text_index.placement(root, "sis") is None
    mine = root / "sis" / "index" / "en" / "seg.store"
    assert mine.read_bytes() == b"the index" and mine.stat().st_ino != (g / "seg.store").stat().st_ino


def test_a_sister_of_a_file_spelling_profile_holds_that_profile_and_acquires_nothing(pair, card, monkeypatch):
    """The older spelling (`profile`, as the node's gutenberg file had it):
    hers says the same, never `slices = []` beside it."""
    import tomllib
    from app.hold import deposit
    root, old, _ = pair
    _ready_nemo(root, old, card)
    path = deposit.held_file(root, "old")
    if not path.is_file():
        louise._write_held_file(root, old, "http://localai:8080", api="vega", embed_model="vega-old")
    path.write_text(path.read_text(encoding="utf-8").replace("slices = []", 'profile = "full"'), encoding="utf-8")
    assert _new_enc(root) == 0
    with open(deposit.held_file(root, "sis"), "rb") as f:
        held = tomllib.load(f)
    assert held.get("profile") == "full" and "slices" not in held and "acquire_enabled" not in held


def test_the_dry_run_names_the_encoder_side_and_makes_nothing(pair, card, capsys):
    root, old, _ = pair
    _ready_nemo(root, old, card)
    assert _new_enc(root, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "case J" in out and "encoder side" in out and "carried" in out and "nothing embedded" in out
    with pytest.raises(CodexError):
        col.resolve(root, "sis")


@pytest.mark.parametrize("why", ["with-space-from", "with-layer-param", "her-sister-s-own", "unknown",
                                 "no-sister", "a-held-space", "another-form"])
def test_what_an_encoder_sister_cannot_be_is_refused_before_anything(pair, card, why, monkeypatch):
    root, old, _ = pair
    _ready_nemo(root, old, card)
    args = ["hold", "collection", "new", "gutenberg", "--root", str(root), "--name", "sis",
            "--sister-of", "old", "--encoder-from", "nemo", "--encoder", "http://localai:8080", "--api", "vega",
            "--embed-model", "vega-nemo", "--cpu-model", "vega-nemo-cpu"]
    if why == "with-space-from":
        args += ["--space-from", "bge"]
    elif why == "with-layer-param":
        args += ["--layer-param", "tokens=4"]
    elif why == "her-sister-s-own":
        args[args.index("nemo")] = "old"            # her own encoder side: her sister's space
    elif why == "unknown":
        args[args.index("nemo")] = "nobody"
    elif why == "a-held-space":
        # bge cuts as her sister does: her space would BE bge's, which is --space-from
        card["weights"] = col.resolve(root, "bge")[1].space.encoder.weights
        args[args.index("nemo")] = "bge"
    elif why == "another-form":
        from app.hold import calibration as cal
        real = cal.form
        monkeypatch.setattr(cal, "form", lambda c: "pair" if c.collection_id == col.resolve(root, "nemo")[1]
                            .collection_id else real(c))
    else:
        i = args.index("--sister-of")
        del args[i:i + 2]
    assert louise.main(args) == 2
    with pytest.raises(CodexError):
        col.resolve(root, "sis")
    assert card["order"] == []


def test_derive_refuses_encoder_from_beside_a_space_or_layers(pair):
    root, old, _ = pair
    nemo = _nemo(root)
    for extra in ({"space_from": nemo}, {"layers": old.layers}, {"space": nemo.space}):
        with pytest.raises(CodexError, match="encoder_from"):
            col.derive(root, old.collection_id, encoder_from=nemo, plan_only=True, **extra)


def test_one_acquirer_a_folder_and_the_file_s_own_word_wins(tmp_path):
    """München's check of the node, 2026-11-27: legi_F (her sister gone)
    acquires; EUR-Lex5.next acquires by its stated `true` while EUR-Lex5's
    file says `false`; a sister reading her OWN folder acquires."""
    import dataclasses
    from types import SimpleNamespace
    from app import collections as collections_mod
    shared, own = tmp_path / "corpus", tmp_path / "own"
    shared.mkdir()
    own.mkdir()

    def c(name, cid, sister=None, acquire=True, stated=False, folder=shared):
        return collections_mod.Collection(
            name=name, plugin="gutenberg", profile=None, embed_mode_default=None, corpus_dir=folder,
            manifest_path=tmp_path / name / "m.sqlite", fts_dir=tmp_path / name / "i",
            lance_dir=tmp_path / name / "l", acquire_enabled=acquire, acquire_stated=stated,
            codex=SimpleNamespace(collection_id=cid, derived_from=sister))
    root = c("root", "R")
    sister = c("sis", "S", sister="R")
    out = collections_mod._one_acquirer([root, sister])
    assert out[0].acquire_enabled and not out[1].acquire_enabled and out[1].acquire_by == "root"
    assert collections_mod._one_acquirer([sister])[0].acquire_enabled, "her sister not held"
    stated = c("next", "N", sister="R", stated=True)
    assert collections_mod._one_acquirer([c("root", "R", acquire=False, stated=True), stated])[1].acquire_enabled, \
        "a file's own true wins"
    assert collections_mod._one_acquirer([root, c("weekly", "W", sister="R", folder=own)])[1].acquire_enabled, \
        "her own folder: her own fetch"
    # her sister acquires nothing (a stated false, or slices = []): she keeps her own, never nobody
    assert collections_mod._one_acquirer([c("root", "R", acquire=False, stated=True), sister])[1].acquire_enabled
    assert collections_mod._one_acquirer([c("root", "R", acquire=False), sister])[1].acquire_enabled
    # a chain S1 -> S2 -> S3 in one folder: exactly one acquirer
    s2, s3 = c("s2", "S2", sister="R"), c("s3", "S3", sister="S2")
    chain = collections_mod._one_acquirer([root, s2, s3])
    assert [x.acquire_enabled for x in chain] == [True, False, False] and chain[2].acquire_by == "root"
    # idempotent: run again over its own answer
    again = collections_mod._one_acquirer(chain)
    assert [(x.acquire_enabled, x.acquire_by) for x in again] == [(x.acquire_enabled, x.acquire_by) for x in chain]


def test_a_sister_s_room_counts_what_her_carry_copies(pair, card):
    """gutenberg-nemo's dry run (2026-11-27) said 73.1 GiB: gutenberg's 54-GB
    index, which her carry links, and its 19-GiB vector table, which a
    sister never carries. Counted as copied: her sister's manifest, and the
    index only where it is not linked."""
    from app.hold import recreate
    root, old, _ = pair
    _ready(root, old, card)
    full = recreate.to_copy(root, "old", "sis")
    assert full.get("lance", 0) > 0, "her sister holds vectors"
    mine = recreate.to_copy(root, "old", "sis", index_by_path=True, parts=recreate.SISTER_PARTS)
    assert "lance" not in mine and mine["state/manifest.sqlite"] == full["state/manifest.sqlite"]
    assert set(recreate.to_copy(root, "old", "sis", parts=recreate.RECUT_SISTER_PARTS)) == {"state/manifest.sqlite"}
    need, _ = recreate.room(root, "old", "sis", make=False, index_by_path=True, parts=recreate.SISTER_PARTS)
    assert need < sum(full.values())
