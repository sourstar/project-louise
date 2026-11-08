"""`hold collection new --space-from`: case E of docs/notes/new-collection.md,
a second collection in a held space (2026-11-08: neuris and eurlex in legi's
Nemotron space). The space and layers are the held collection's, its
reference vectors REUSED and never re-embedded, its endpoint and model its
file's; a plugin that cuts otherwise, or a recipe stating another space, is
refused before anything is made. The steps' own machinery is tested where it
lives; here, that `new` feeds them the held space and nothing else."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app import louise, run_check
from app.hold import calibrate as calib
from app.hold import calibration as cal_mod
from app.hold import collection as col
from app.hold import deposit
from app.hold.codex import CodexError

WEIGHTS = "sha256:" + "ab" * 32
SET = "sha256:" + "5e" * 32
ENGINE = {"name": "localai/llama-cpp", "image": "sha256:" + "11" * 32}
ARTICLE = [{"chunker": {"id": "akn_article", "version": 1, "params": {}}, "rule": "default"}]
SOURCE_SPACE = {"encoder": {"weights": WEIGHTS, "engine": ENGINE}, "ctx": 8192, "cut": "tokens",
                "document": {"instruction": "passage: "}, "query": {"instruction": "query: "},
                "calibration": {"set": SET, "vectors": "sha256:" + "77" * 32}, "tolerance": "0.999995",
                "envelope": "sha256:" + "88" * 32}


def _layers(raw):
    return [SimpleNamespace(to_dict=(lambda d=d: copy.deepcopy(d))) for d in raw]


def _sid(space: dict, layers) -> str:
    keep = {k: v for k, v in space.items() if k not in ("tolerance", "envelope")}
    keep["calibration"] = {k: v for k, v in (space.get("calibration") or {}).items() if k != "vectors"}
    return "sha256:" + json.dumps([keep, layers], sort_keys=True)


def _codex(space: dict, layers, *, query_encoder=None):
    sp = SimpleNamespace(
        to_dict=lambda: copy.deepcopy(space), ctx=space.get("ctx"), cut=space.get("cut"),
        document_instruction=(space.get("document") or {}).get("instruction"),
        calibration=SimpleNamespace(set=(space.get("calibration") or {}).get("set")),
        tolerance=space.get("tolerance"), dim=None, dtype=None, pooling=None, normalise=None,
        query=SimpleNamespace(encoder=query_encoder),
        encoder=SimpleNamespace(weights=space["encoder"]["weights"],
                                engine=SimpleNamespace(engine_id=json.dumps(space["encoder"]["engine"]))))
    return SimpleNamespace(space=sp, layers=_layers(layers), space_id=_sid(space, layers),
                           collection_id="sha256:" + "ee" * 32)


@pytest.fixture
def steps(monkeypatch, tmp_path):
    seen: dict = {"order": [], "plugin_layers": ARTICLE, "source": _codex(SOURCE_SPACE, ARTICLE),
                  "file": {"url": "http://localai:8080", "api": "vega", "embed_model": "vega-nemo"}}
    monkeypatch.setenv("LOUISE_PLATFORM_IMAGE", "sha256:" + "9a" * 32)
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path / "louise"))

    def resolve(root, name):
        if name == "legi" and seen.get("source") is not None:
            return "legi", seen["source"]
        raise CodexError(f"no collection {name}")
    monkeypatch.setattr(col, "resolve", resolve)

    def dry_codex(plugin, recipe, **kw):
        return _codex(recipe["space"], recipe.get("layers") or seen["plugin_layers"])
    monkeypatch.setattr(col, "dry_codex", dry_codex)
    monkeypatch.setattr(cal_mod, "form", lambda codex: "text")
    monkeypatch.setattr(deposit, "_encoder_block", lambda root, name: seen["file"] if name == "legi" else None)
    monkeypatch.setattr(calib, "load_set", lambda set_id: (set_id, ["t1"]))
    monkeypatch.setattr(louise.cx, "engine_from_dict", lambda d, where: SimpleNamespace(engine_id="e", **d)
                        if isinstance(d, dict) else d)
    monkeypatch.setattr(run_check, "stated_run", lambda recipe, probe_item=None: SimpleNamespace(
        driver="cpu" if recipe.model.endswith("-cpu") else "sycl", weights=WEIGHTS, engine=dict(ENGINE),
        run_id="sha256:run"))

    def measure(*a, **k):
        seen["order"].append("calibrate")
        raise AssertionError("case E measures nothing: the space's reference is reused")
    monkeypatch.setattr(calib, "measure", measure)

    def create_from(root, plugin, recipe, **kw):
        seen["order"].append("create")
        seen["created"] = (plugin, recipe, kw)
        (root / "neuris" / "state").mkdir(parents=True, exist_ok=True)
        made = _codex(recipe["space"], recipe["layers"])
        if seen.get("made_elsewhere"):
            made = _codex(dict(recipe["space"], ctx=1), recipe["layers"])
        return made, root / "neuris.toml"
    monkeypatch.setattr(louise, "create_from", create_from)
    monkeypatch.setattr(col, "name_of", lambda root, ref: "neuris")

    def cpu_reference(a):
        seen["order"].append("cpu-reference")
        seen["cpu_args"] = a
        return 0
    monkeypatch.setattr(louise, "cmd_embedder_cpu_reference", cpu_reference)
    monkeypatch.setattr(louise, "cmd_embedder_check", lambda a: seen["order"].append("gate") or 0)
    from app import node_state
    monkeypatch.setattr(node_state, "embedding_blockers", lambda name, x: [])
    return seen


def _recipe(tmp_path, space=None) -> str:
    body = {"descriptive": {"title": "German federal law"}}
    if space is not None:
        body["space"] = space
    p = tmp_path / "recipe.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return str(p)


def _new(tmp_path, recipe, *extra):
    return louise.main(["hold", "collection", "new", "neuris", "--root", str(tmp_path), "--recipe", recipe,
                        "--name", "neuris", "--space-from", "legi", "--cpu-model", "vega-nemo-cpu", *extra])


def test_the_held_space_is_copied_its_reference_reused_and_nothing_measured(tmp_path, steps):
    (tmp_path / "legi" / "state").mkdir(parents=True)
    (tmp_path / "legi" / "state" / "calibration.json").write_text('{"tolerance": "0.999995"}')
    assert _new(tmp_path, _recipe(tmp_path)) == 0
    assert steps["order"] == ["create", "cpu-reference", "gate"], "no calibrate: the reference is reused"
    plugin, recipe, kw = steps["created"]
    assert (plugin, kw["space_from"], kw["calibration_from"]) == ("neuris", "legi", "legi")
    assert (kw["encoder"], kw["api"], kw["embed_model"]) == ("http://localai:8080", "vega", "vega-nemo"), \
        "the endpoint and model are the held collection's file's (RULE 2)"
    assert recipe["layers"] == ARTICLE and recipe["space"]["tolerance"] == "0.999995"
    assert "envelope" not in recipe["space"] and "vectors" not in recipe["space"]["calibration"], \
        "the computed fields do not travel: the reference is reused by calibration_from"
    assert steps["cpu_args"].refresh and steps["cpu_args"].collection == "neuris"
    assert json.loads((tmp_path / "neuris" / "state" / "calibration.json").read_text()) == \
        {"tolerance": "0.999995"}, "the space's measurement, beside the copy"
    prog = json.loads((tmp_path / "louise" / "state" / "new" / "neuris.json").read_text())
    assert prog["case"] == "E" and set(prog["steps"]) == {"make", "cpu-reference", "gate"}


def test_a_plugin_that_cuts_otherwise_is_refused_before_anything_is_made(tmp_path, steps):
    steps["plugin_layers"] = [{"chunker": {"id": "cdc", "version": 1, "params": {}}, "rule": "default"}]
    assert _new(tmp_path, _recipe(tmp_path)) == 2
    assert steps["order"] == []


def test_a_recipe_stating_another_space_is_refused_and_the_same_one_is_taken(tmp_path, steps):
    other = dict(copy.deepcopy(SOURCE_SPACE), ctx=4096)
    assert _new(tmp_path, _recipe(tmp_path, other)) == 2
    assert steps["order"] == []
    same = {k: v for k, v in copy.deepcopy(SOURCE_SPACE).items() if k not in ("envelope", "tolerance")}
    same["calibration"].pop("vectors")
    assert _new(tmp_path, _recipe(tmp_path, same)) == 0
    assert steps["order"] == ["create", "cpu-reference", "gate"]


def test_an_endpoint_given_must_be_the_held_collection_s(tmp_path, steps):
    assert _new(tmp_path, _recipe(tmp_path), "--encoder", "http://elsewhere:1") == 2
    assert _new(tmp_path, _recipe(tmp_path), "--encoder", "http://localai:8080") == 0


@pytest.mark.parametrize("why", ["absent", "query-encoder", "re-create"])
def test_a_source_case_e_cannot_copy_is_refused(tmp_path, steps, why):
    extra = ()
    if why == "absent":
        steps["source"] = None
    elif why == "query-encoder":
        steps["source"] = _codex(SOURCE_SPACE, ARTICLE, query_encoder={"weights": WEIGHTS})
    else:
        extra = ("--re-create",)
    assert _new(tmp_path, _recipe(tmp_path), *extra) == 2
    assert steps["order"] == []


def test_a_copy_that_did_not_land_in_the_space_is_refused(tmp_path, steps):
    steps["made_elsewhere"] = True
    assert _new(tmp_path, _recipe(tmp_path)) == 2
    assert "cpu-reference" not in steps["order"]


def test_the_dry_run_names_case_e_and_makes_nothing(tmp_path, steps, capsys):
    assert _new(tmp_path, _recipe(tmp_path), "--dry-run") == 0
    out = capsys.readouterr().out
    assert "case E" in out and "stops before make" in out
    assert steps["order"] == [] and not (tmp_path / "louise" / "state" / "new" / "neuris.json").exists()


def test_the_real_copy_lands_in_the_space_with_the_reference_reused(tmp_path, monkeypatch):
    """The steps unmocked where case E's make relies on them: `create_from`
    with `space_from` AND `calibration_from` writes a Codex in the source's
    space whose reference vectors and envelope ARE the source's, with no
    document embedded (the set is not sent again)."""
    from app.bundles import keys as node_keys
    from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed
    from tests.hold.test_template import SPACE, recipe
    space = dict(SPACE, query={"encoder": None, "instruction": "query: "})
    root = tmp_path / "root"
    root.mkdir()
    src = col.create_from_recipe(root, "gutenberg", recipe(space=space), platform_image=PLATFORM,
                                 embed=fake_embed, name="legi", key=KEY, now=NOW)
    monkeypatch.setattr(node_keys, "load_signing_key", lambda: KEY)

    def no_embedding(*a, **k):
        raise AssertionError("case E reuses the reference; nothing is embedded")
    monkeypatch.setattr(louise, "_embed_for_recipe", lambda *a, **k: no_embedding)
    body = recipe(work={"tokens": 6_000_000})            # another collection: its own work
    body.pop("space")
    made = louise._space_from_recipe("gutenberg", body, src)
    codex, _held = louise.create_from(root, "gutenberg", made, platform_image=PLATFORM,
                                      encoder="http://localai:8080", name="neuris", api="vega",
                                      embed_model="vega-nemo", space_from="legi", calibration_from="legi")
    assert codex.space_id == src.space_id and codex.collection_id != src.collection_id
    assert (codex.space.calibration.vectors, codex.space.envelope) == \
        (src.space.calibration.vectors, src.space.envelope)
    assert codex.space.tolerance == src.space.tolerance and codex.layers == src.layers
