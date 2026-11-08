"""docs/notes/eurlex-recipe.toml through the real loader (`louise._recipe`,
what `hold collection new --recipe` reads) and the real template
(`collection.dry_codex`: every refusal `create` would make, before a card is
called): a version-11 Codex of the shared legal space, its engine block the
one thing the node supplies — here a dummy, since `new` reads it there."""
from __future__ import annotations

import copy
from pathlib import Path

from app import louise
from app.hold import codex as cx
from app.hold import collection as col

ROOT = Path(__file__).resolve().parent.parent
RECIPE = ROOT / "docs" / "notes" / "eurlex-recipe.toml"
PLATFORM = "sha256:" + "1f" * 32

#: THE SHARED SPACE BLOCK, byte for byte as the operator's plan fixes it for
#: the three legal recipes (legi, neuris, eurlex)
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

DUMMY_ENGINE = {"name": "localai/llama-cpp", "build": "0" * 12, "image": "sha256:" + "11" * 32,
                "args": ["${BACKEND}/run.sh"], "device": "test device", "env": {}}


def _with_engine() -> dict:
    recipe = copy.deepcopy(louise._recipe(str(RECIPE)))
    recipe["space"]["encoder"]["engine"] = dict(DUMMY_ENGINE)
    # as `new` does for its dry Codex (app/louise.py): the tolerance is
    # outside space_id (02 §2.4) and measured by `new`, never typed
    recipe["space"].setdefault("tolerance", "0.99")
    return recipe


def test_the_space_block_is_the_shared_one_byte_for_byte():
    assert SHARED_BLOCK in RECIPE.read_text(encoding="utf-8")
    for other in ("legi-recipe.toml", "neuris-recipe.toml"):
        path = RECIPE.parent / other
        if path.exists():                 # built elsewhere: held to the same bytes
            assert SHARED_BLOCK in path.read_text(encoding="utf-8"), other


def test_the_recipe_loads_through_the_real_loader_into_a_version_11_codex():
    dry = col.dry_codex("eurlex", _with_engine(), platform_image=PLATFORM)
    assert dry.version == 11, "the document instruction is a version-11 field (09 §9.3)"
    sp = dry.space
    assert (sp.dim, sp.dtype, sp.ctx, sp.cut, sp.pooling, sp.normalise, sp.exactness, sp.epoch) == (
        2048, "f16", 8191, "tokens", "mean", True, "exact", "0")
    assert sp.query.instruction == "query: " and sp.document_instruction == "passage: "
    assert sp.encoder.weights == "sha256:09596cedbdf2a0d164b37083794632be55dcb2aa2abcf02fb995dfdccbecbf2c"
    assert sp.calibration.set == "sha256:7d06a2f6a4a773da01a517cb30571cac421753df3e30b20fca4716e5441fbf0f"
    assert dry.plugin == "eurlex" and dry.license.cls == "green"
    from app import chunking_legal
    assert [layer.to_dict() for layer in dry.layers] == [chunking_legal.SHARED_LAYER]
    assert dry.slicing["keys"] == ["lang", "type", "year"]
    assert dry.profile["validity"]["work"] == "extra.validity_work"
    assert dry.origin.jurisdiction == "EU" and dry.origin.unit == "file"


def test_the_space_id_does_not_depend_on_the_engine():
    """Invariant 1: the engine is outside `space_id` — two dummy engines
    make one space, so legi's held space and this recipe agree by the block."""
    a = col.dry_codex("eurlex", _with_engine(), platform_image=PLATFORM)
    other = _with_engine()
    other["space"]["encoder"]["engine"]["device"] = "another device"
    b = col.dry_codex("eurlex", other, platform_image=PLATFORM)
    assert a.space_id == b.space_id and a.collection_id != b.collection_id
