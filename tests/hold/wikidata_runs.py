"""A wikidata collection as München holds it (2026-11-17), for the tests of
a dated-dump Codex naming its run (09 §9.17; app/hold/origin_run.py,
app/hold/pin_run.py): the live Codex at parser 2 made from the recipe
docs/notes/wikidata-recipe.toml writes — its `origin` block with no `spec`,
so `origin.spec = {"files": null}` and no run — its collection file naming
the run by hand (`snapshot = "20261012"`), a dump of that run under its
origin, and its records built and frozen as 2026-10."""
from __future__ import annotations

import json
from pathlib import Path

RUN = "20261012"
SID = "2026-10"
#: the live recipe's `[origin]`, as docs/notes/wikidata-recipe.toml writes it
ORIGIN = {"form": "the official Wikidata JSON entity dump, the dated run named by the snapshot",
          "unit": "file", "clock": "UTC", "cutoff_rule": "dated-dumps", "jurisdiction": ""}
URL = "http://encoder.invalid:8080"
#: the layer's tokenizer, a word tokenizer registered under this digest (`register_tokenizer`)
TOKENIZER = "sha256:" + "ab" * 32


def register_tokenizer() -> None:
    """One token a whitespace-separated word (tests/test_plugins_wikidata_chunker.py)."""
    from tokenizers import Tokenizer, models, pre_tokenizers

    from app.plugins.wikidata import chunker
    tok = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    chunker.register(TOKENIZER, tok)


def recipe(*, shard_records: int = 2) -> dict:
    from app import collections as collections_mod
    from app.hold import template
    from tests.hold.test_template import SPACE, VALIDATION
    source = collections_mod.PLUGIN_REGISTRY["wikidata"]
    return {"descriptive": {"title": "Wikidata", "summary": "Every entity, textified."},
            "space": dict(SPACE), "work": {"tokens": 40_000_000}, "origin": dict(ORIGIN),
            "validation": {**VALIDATION, "checks": [*VALIDATION["checks"], "records-vs-origin"],
                           "sample": {**VALIDATION["sample"], "records-vs-origin": {"shards": "1"}}},
            "parser": {"id": "wikidata", "version": 2},
            "layers": [{"rule": "default", "chunker": {"id": "wikidata-text", "version": 1,
                                                       "params": {"max_tokens": 64, "tokenizer": TOKENIZER}}}],
            "slicing": {**template.default_slicing(source), "shard_records": int(shard_records)}}


def live(data_dir: Path, config_dir: Path, *, name: str = "wikidata", run: str | None = None):
    """The live collection at parser 2 — the Codex naming no run unless
    `run` — its file stating `snapshot`, its corpus holding the run's dump,
    scanned, built and frozen as SID. Returns (root, corpus, codex)."""
    from unittest import mock

    from app import collections as collections_mod
    from app import db, discovery
    from app.hold import collection as col
    from app.hold import records as rec
    from app.hold import snapshot as sn
    from app.hold import template
    from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed, fake_query_embed
    from tests.test_plugins_wikidata_source import a_dump
    root = Path(data_dir) / "collections"
    root.mkdir(parents=True, exist_ok=True)
    # THE CODE OF 2026-10-20 made it, which pinned no run (`run` None): the
    # creation path's pin taken out, as it was then
    with mock.patch.object(template, "pin_dated", (lambda plugin, origin, run: origin) if run is None
                           else template.pin_dated):
        codex = col.create_from_recipe(root, "wikidata", recipe(), platform_image=PLATFORM, embed=fake_embed,
                                       embed_query=fake_query_embed, name=name, key=KEY, now=NOW, run=run)
    (Path(config_dir) / "collections").mkdir(parents=True, exist_ok=True)
    (Path(config_dir) / "collections" / f"{name}.toml").write_text(
        f'codex = "{codex.collection_id}"\nslices = "all"\nkinds = ["records", "vectors"]\nshare = false\n'
        f'snapshot = "{RUN}"\n', encoding="utf-8")
    corpus = root / name / "origin"
    a_dump(corpus, RUN)
    c = collections_mod.by_name(name)
    con = db.connect(c)
    try:
        discovery.sync_collection(con, c)
    finally:
        con.close()
    rec.build(root, name, corpus_dir=corpus, snapshot_id=SID, records_per_shard=2, log=lambda *_: None)
    sn.create(root, name, snapshot_id=SID, log=lambda *_: None)
    return root, corpus, codex


def whole_recipe(root: Path, tmp: Path, *, name: str = "wikidata") -> Path:
    """The runbook's step-2 recipe: the live Codex with the parser and the
    image changed (docs/notes/wikidata-entity-origin.md)."""
    from tests.hold.conftest import PLATFORM  # noqa: F401 — the image is another one
    c = json.loads((root / name / "shard" / "codex.json").read_bytes())
    c["parser"]["version"] = 3
    c["toolchain"]["platform_image"] = "sha256:" + "3e" * 32
    c.pop("collection_id", None)
    p = Path(tmp) / "wikidata3.json"
    p.write_text(json.dumps(c), encoding="utf-8")
    return p


def create_args(root: Path, recipe_path: Path, name: str = "wikidata.next", *extra: str) -> list[str]:
    return ["hold", "collection", "create", "--root", str(root), "--recipe", str(recipe_path), "--name", name,
            "--encoder", URL, "--api", "vega", "--embed-model", "vega-jina-v3", *extra]
