"""`hold collection new --sister-of`: case J of docs/notes/new-collection.md,
a SISTER into a held space (operator, 2026-11-08: "Make J, it is time to do
it" — normattiva-nemo, Normattiva's records in legi's Nemotron space).

End to end on real collections: a mother holding vectors, a collection of
another space, `derive`, the parts seeded, the manifest and text index
carried with nothing of the mother's vectors said held (hold/carry.py A1i).
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
    assert sister.layers == bge.layers and cut(sister) == cut(old), "the space's layers: her mother's cut"
    assert sister.space.calibration.vectors == bge.space.calibration.vectors, \
        "the held space's reference, inherited: nothing embedded"
    assert card["order"] == [("cpu-reference", "sis"), ("gate", "sis")]
    con = sqlite3.connect(root / "sis" / "state" / "manifest.sqlite")
    try:
        assert con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == n
        assert con.execute("SELECT COUNT(*) FROM documents WHERE embed_done>0 OR embed_version<>0"
                           ).fetchone()[0] == 0, "none of the mother's vectors said held"
        assert con.execute("SELECT COUNT(*) FROM chunk_hashes").fetchone()[0] == 0
    finally:
        con.close()
    assert (root / "sis" / "index" / "words").read_text() == "the text index"
    assert not spaces.known_lance_rows(_local(root, "sis").lance_dir)
    assert spaces.known_lance_rows(_local(root, "old").lance_dir), "the mother keeps hers"
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
