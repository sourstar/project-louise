"""docs/notes/legi-recipe.toml through the real loader: `hold collection
new`'s recipe reader (app/louise.py `_recipe`) and the dry Codex `new`
holds a collection to (app/hold/collection.py `dry_codex`), with a dummy
engine where the node's is read — a version-11 Codex with the shared
legal space's values (legal-collections-nemotron.md)."""
from __future__ import annotations

import json
from pathlib import Path

from app import louise
from app.hold import codex as cx, collection as col

RECIPE = Path(__file__).resolve().parent.parent / "docs" / "notes" / "legi-recipe.toml"

#: THE SHARED SPACE BLOCK, byte-identical in the three legal recipes
#: (legi, neuris, eurlex): one space_id across French, German and EU law
SHARED_BLOCK = '''[space]
dim = 2048
dtype = "f16"
# 8191, not 8192: llama.cpp embeds at most context_size − 1 tokens, and the model runs
# context_size 8192 (München's dry run, 2026-11-08); wikidata's space on the same model is 8191
ctx = 8191
cut = "tokens"
pooling = "mean"
normalise = true
exactness = "exact"
epoch = "0"
evaluation = ""
# tolerance: measured by `new`, never typed

[space.calibration]
set = "sha256:7d06a2f6a4a773da01a517cb30571cac421753df3e30b20fca4716e5441fbf0f"

[space.calibration.quorum]
n = 16
min_pass = 12

[space.document]
instruction = "passage: "

[space.query]
instruction = "query: "

[space.encoder]
weights = "sha256:09596cedbdf2a0d164b37083794632be55dcb2aa2abcf02fb995dfdccbecbf2c"
label = "nvidia/Nemotron-3-Embed-1B-BF16 (c0c9fea9; f16 GGUF, llama.cpp 3f8af15a; vega-nemotron-3-embed-1b)"
# [space.encoder.engine]: read on the node with `louise hold embedder run --encoder http://192.168.178.10:30286 --api vega --model vega-nemotron-3-embed-1b`; `new` holds the endpoint to it.
'''

#: an engine of the form the node states (a backend engine, 09 §9.3) — a
#: placeholder: the real one is read on the node
DUMMY_ENGINE = {"name": "localai/llama-cpp", "build": "3f8af15a", "image": "sha256:" + "0" * 64,
                "args": ["${BACKEND}/run.sh", "--addr", "127.0.0.1:${PORT}"], "device": "cpu",
                "options": {"Model": "weights/nemotron", "ModelFile": "${WEIGHTS}"}}


def _recipe() -> dict:
    r = louise._recipe(str(RECIPE))
    r["space"]["encoder"]["engine"] = dict(DUMMY_ENGINE)
    r["space"]["tolerance"] = "0.999"          # what `new` writes after measuring it
    return r


def test_the_space_block_is_the_shared_one_byte_for_byte():
    text = RECIPE.read_text(encoding="utf-8")
    assert SHARED_BLOCK in text
    assert "tolerance =" not in text.split("[space]", 1)[1].split("[work]", 1)[0], "measured, never typed"


def test_the_header_names_the_case_a_command_dry_run_first():
    head = RECIPE.read_text(encoding="utf-8").split("[descriptive]", 1)[0]
    cmd = ("louise hold collection new legi --recipe /config/recipes/legi-recipe.toml --name legi "
           "--encoder http://192.168.178.10:30286 --api vega --embed-model vega-nemotron-3-embed-1b "
           "--cpu-model vega-nemotron-3-embed-1b-cpu")
    assert cmd in head
    assert head.index("--dry-run") < head.index(cmd), "the dry run first"


def test_the_recipe_makes_a_version_11_codex_with_the_shared_space():
    codex = col.dry_codex("legi", _recipe(), platform_image="sha256:" + "1" * 64)
    assert codex.version == cx.DOCUMENT_VERSION == 11
    d = json.loads(codex.canonical())
    sp = d["space"]
    assert (sp["dim"], sp["dtype"], sp["ctx"], sp["cut"], sp["pooling"], sp["normalise"]) == \
        (2048, "f16", 8191, "tokens", "mean", True)
    assert (sp["exactness"], sp["epoch"], sp["evaluation"]) == ("exact", "0", "")
    assert sp["document"] == {"instruction": "passage: "}
    assert sp["query"] == {"encoder": None, "instruction": "query: "}
    assert sp["calibration"]["set"] == "sha256:7d06a2f6a4a773da01a517cb30571cac421753df3e30b20fca4716e5441fbf0f"
    assert sp["calibration"]["quorum"] == {"n": 16, "min_pass": 12}
    assert sp["encoder"]["weights"] == "sha256:09596cedbdf2a0d164b37083794632be55dcb2aa2abcf02fb995dfdccbecbf2c"
    assert sp["encoder"]["engine"]["build"] == "3f8af15a"
    from app import chunking_legal
    assert d["layers"] == [chunking_legal.SHARED_LAYER], "the shared legal layer, byte for byte"
    assert d["profile"]["validity"] == {"from": "extra.version_from", "kind": "versions-per-work",
                                        "work": "extra.work_key"}
    assert d["license"] == {"class": "yellow", "terms_sha256":
                            "sha256:a1deb9d7554ad10e041dbd7440b3dec0ac0d3679f9e1655ee9d850908b015995"}, \
        "yellow, its terms the plugin's TERMS.txt by hash (operator 2026-11-08, A)"
    assert d["parser"] == {"id": "legi", "version": 2} and d["plugin"] == "legi"
    assert d["slicing"]["parts"]["rule"] == "content" and d["slicing"]["keys"] == ["kind", "year"]
    assert d["origin"]["jurisdiction"] == "FR" and d["origin"]["unit"] == "container-member"


def test_the_space_id_is_the_block_s_and_not_the_collection_s():
    """Another plugin's recipe with the same block lands in the same space —
    what neuris and eurlex will be (case E, phase 2); the engine is outside
    `space_id`."""
    a = col.dry_codex("legi", _recipe(), platform_image="sha256:" + "1" * 64)
    other = _recipe()
    other["descriptive"] = {"title": "another", "summary": "another collection, the same space"}
    other["space"]["encoder"]["engine"] = dict(DUMMY_ENGINE, build="ffffffff")
    b = col.dry_codex("legi", other, platform_image="sha256:" + "2" * 64)
    assert a.space_id == b.space_id and a.collection_id != b.collection_id
