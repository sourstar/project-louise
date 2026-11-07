"""WHICH HELD SLICES ARE EMBEDDED, shown and changed in the Library (07 §7.8
`embed_slices`; operator, 2026-11-07: "About wikidata embedding: we agreed
to embed only certain families, but the grow profile shows that all the
families are checked, and they cannot be unchecked"; then "For wikidata
the archive is unique, thus it makes no sense to show a false choice, but
it makes sense to show the embedding options there").

The card's line, the grow dialog's "embed" group and `hold collection
show` read the worker's own resolution (`slice_scope.embed_view`,
invariant 10); the route writes the file through its one writer and
refuses what is not held, or nothing; an edit makes the worker scan, so the
slice scope does not wait for the interval. tests/test_library_embed_
slices_browser.py opens the dialogs in a browser.
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import re
import tomllib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import collections as collections_mod, config, db, louise, node_state, slice_scope, spaces, web
from app.acquire.spec import Filter
from app.acquire.specs import ACQUIRE_REGISTRY
from app.api import app

#: a document's family, as the pack's codes would give it
FAMILY = {1: "person", 2: "work", 3: "taxon", 4: "person", 5: "internal", 6: "work", 7: "taxon"}


def _wd(tmp_path, slices, embed):
    d = tmp_path / "wd"
    d.mkdir(exist_ok=True)
    return collections_mod.Collection(
        name="wikidata", plugin="wikidata", profile=None, embed_mode_default=None,
        corpus_dir=d / "corpus", manifest_path=d / "manifest.db", fts_dir=d / "fts",
        lance_dir=d / "lancedb", slices=slices, embed_slices=embed)


def _worker_embeds(tmp_path, c) -> set[str]:
    """The families whose documents the worker leaves to the encoder: its
    slice-scope pass run over a manifest, then read back — in scope and not
    declined."""
    con = db.connect(c)
    for i in FAMILY:
        con.execute("INSERT INTO documents(uid, source, native_id, path) VALUES (?,?,?,?)",
                    (f"wd:{i}", "wikidata", str(i), f"dump::{i}"))
    context = SimpleNamespace(fmap=SimpleNamespace(sha256="sha256:" + "a" * 64),
                              dump=SimpleNamespace(name="20261012"), walked={})
    slice_scope.apply(con, c, context_for=lambda collection, log=None, stop=None: context,
                      slice_hint=lambda plugin, row, ctx: f"family={FAMILY[int(row['native_id'])]}/range=0")
    rows = con.execute("SELECT native_id, in_scope, embed_done FROM documents").fetchall()
    return {FAMILY[int(r["native_id"])] for r in rows if r["in_scope"] and r["embed_done"] != -1}


@pytest.mark.parametrize("slices, embed, line, worker", [
    # the operator's case: hold all, embed some
    ("all", ("family=person", "family=work"),
     "Embedded: 2 of 14 families: person, work", {"person", "work"}),
    # the file names none: every family held
    ("all", None, "Embedded: 14 of 14 families — every family held",
     {"person", "work", "taxon", "internal"}),
    # a hand-written list naming a family not held: nothing of it is embedded
    (("family=person", "family=work", "family=taxon"), ("family=person", "family=internal"),
     "Embedded: 1 of 3 families: person · also chosen but not held here, so not embedded until "
     "grown: internal", {"person"}),
])
def test_the_card_line_is_what_the_worker_embeds(isolated_config, tmp_path, slices, embed, line, worker):
    c = _wd(tmp_path, slices, embed)
    view = web._embed_slices_view(c)
    assert view["line"] == line
    assert _worker_embeds(tmp_path, c) == worker
    # every family the fixture has documents of: the card's check is the worker's
    shown = {r["short"]: r["embedded"] for r in view["rows"]}
    assert {f for f in set(FAMILY.values()) if shown.get(f)} == worker
    assert view["one_archive"] is True and view["total"] == 14


def test_one_slice_held_or_none_listed_offers_nothing():
    assert web._embed_slices_view(SimpleNamespace(name="w", plugin="wikidata", profile=None,
                                                  slices=("family=person",), embed_slices=None,
                                                  embed_select=None)) is None
    # PubMed's years are not delivered apart: "all" of them cannot be listed
    assert web._embed_slices_view(SimpleNamespace(name="p", plugin="pubmed", profile=None, slices="all",
                                                  embed_slices=None, embed_select=None)) is None


def test_under_smart_the_line_says_how_the_two_combine():
    """embed_select.decide: the smart choice over every language held, then
    narrowed to embed_slices — a part of a language embeds nothing there."""
    ns = SimpleNamespace(name="wikipedia", plugin="wikipedia", profile=None,
                         slices=("lang=en", "lang=it", "lang=de"),
                         embed_slices=("lang=en", "lang=it/pages=3M"), embed_select=None)
    view = web._embed_slices_view(ns)
    assert view["smart"] is True and view["embedded"] == ["lang=en"]
    assert view["line"] == "Embedded: 1 of 3 languages: en — within them, the smart choice of “Embed which”"
    # without smart the part is embedded where it is, and said
    ns.embed_select = "all"
    view = web._embed_slices_view(ns)
    assert view["line"] == "Embedded: 1 of 3 languages: en; and part of it"
    assert slice_scope.embed_view(ns)["part"] == {"lang=it": ["lang=it/pages=3M"]}


def test_the_grow_dialogs_header_says_what_a_check_means():
    def ns(plugin, slices):
        return SimpleNamespace(name=plugin, plugin=plugin, profile=None, slices=slices,
                               embed_slices=None, embed_select=None)
    wp = web._grow_view(ns("wikipedia", ("lang=en", "lang=it")))
    assert wp["tag"] == "download + embed (per Embed which)" and wp["one_archive"] is False
    assert web._grow_view(ns("embedded", ("lang=en",)))["tag"] == "download + embed"
    wd = web._grow_view(ns("wikidata", "all"))
    assert wd["one_archive"] is True and (wd["held"], wd["total"], wd["nouns"]) == (14, 14, "families")


# ---- the route, the file, the worker's value --------------------------------

@pytest.fixture
def languages(isolated_config, config_dir, monkeypatch):
    """The test node's gutenberg, its source delivering three languages
    apart (as wikipedia's does) and its file holding all three."""
    spec = ACQUIRE_REGISTRY["gutenberg"]
    monkeypatch.setitem(ACQUIRE_REGISTRY, "gutenberg", dataclasses.replace(
        spec, profile_mode="additive",
        profiles={**spec.profiles, **{l: Filter(include=(f"{l}/*",)) for l in ("en", "it", "de")}},
        slice_profiles={"lang=en": "en", "lang=it": "it", "lang=de": "de"},
        profile_labels={"en": "English", "it": "Italian", "de": "German"}))
    path = config_dir / "collections" / "gutenberg.toml"
    path.write_text(path.read_text(encoding="utf-8") + 'slices = ["lang=en", "lang=it", "lang=de"]\n',
                    encoding="utf-8")
    monkeypatch.setattr(spaces, "resolve_embed_model_hash", lambda *a: ("abc123", False))
    monkeypatch.setattr(web, "_ping", lambda url: True)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    node_state.set_worker_state("running")
    with TestClient(app) as c:
        c.headers["X-Admin-Token"] = "sekrit"
        yield c, path


def _line(client) -> str:
    html = client.get("/admin/library").text
    m = re.search(r'<div class="note" data-embed-slices[^>]*>([^<]*)</div>', html)
    return m.group(1) if m else ""


def test_the_route_writes_the_file_and_the_workers_value_follows(languages):
    client, path = languages
    assert _line(client) == "Embedded: 3 of 3 languages — every language held"
    r = client.post("/admin/library/gutenberg/embed-slices", json={"slices": ["lang=en", "lang=it"]})
    assert r.status_code == 200, r.text
    assert r.json()["line"] == "Embedded: 2 of 3 languages: en, it"
    assert tomllib.loads(path.read_text(encoding="utf-8"))["embed_slices"] == ["lang=en", "lang=it"]
    c = collections_mod.by_name("gutenberg")
    assert slice_scope.embedded(c) == ("lang=en", "lang=it"), "what the slice scope declines outside"
    assert _line(client) == "Embedded: 2 of 3 languages: en, it"
    # every language held: the key leaves the file
    r = client.post("/admin/library/gutenberg/embed-slices", json={"slices": None})
    assert r.status_code == 200 and r.json()["embed_slices"] is None
    assert "embed_slices" not in tomllib.loads(path.read_text(encoding="utf-8"))
    assert slice_scope.embedded(collections_mod.by_name("gutenberg")) is None
    assert _line(client) == "Embedded: 3 of 3 languages — every language held"


def test_the_route_refuses_nothing_and_what_is_not_held_in_plain_words(languages):
    client, path = languages
    before = path.read_bytes()
    r = client.post("/admin/library/gutenberg/embed-slices", json={"slices": []})
    assert r.status_code == 400
    assert r.json()["detail"] == ("Choose at least one language to embed. To embed nothing, set the "
                                  "embed mode to off.")
    r = client.post("/admin/library/gutenberg/embed-slices", json={"slices": ["lang=en", "lang=fr"]})
    assert r.status_code == 400
    assert r.json()["detail"] == ("fr is not held on this node, so it cannot be embedded here: "
                                  "grow adds it first")
    assert path.read_bytes() == before, "nothing written"


def test_hold_collection_show_prints_the_same_reading(languages):
    client, path = languages

    def show() -> dict:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert louise.main(["hold", "collection", "show", "gutenberg",
                                "--root", str(config.DATA_DIR / "collections")]) == 0
        return json.loads(out.getvalue())
    got = show()
    assert (got["embed_slices"], got["embed_slices_not_held"], got["embed_select"]) == \
        (["lang=en", "lang=it", "lang=de"], [], "all")
    path.write_text(path.read_text(encoding="utf-8") + 'embed_slices = ["lang=it", "lang=fr"]\n',
                    encoding="utf-8")
    got = show()
    assert (got["embed_slices"], got["embed_slices_not_held"]) == (["lang=it"], ["lang=fr"])
    assert got["embed_slices"] == slice_scope.embed_view(collections_mod.by_name("gutenberg"))["embedded"]


def test_the_grow_dialog_routes_one_archive_to_the_embed_choice(languages):
    """Read off the page's script where no browser runs: one archive opens
    the embed choice; every group's header is written before its rows."""
    client, _ = languages
    html = client.get("/admin/library").text
    grow = html[html.index("function openGrow("):html.index("function growExtrasBlock(")]
    assert "if((GROW[name]||{}).one_archive){openGrowOneArchive(name);return}" in grow
    additive = html[html.index("function openGrowAdditive("):html.index("function openGrowOneArchive(")]
    assert additive.index("groupTag(g.tag") < additive.index("${rows}")
    assert "${h?'checked disabled':''}" in additive and "${all?'checked disabled':''}" in additive
    block = html[html.index("function embedSlicesBlock("):html.index("function smartCombines(")]
    assert block.index("groupTag('embed')") < block.index("${rows}")
    assert 'EMBED_SLICES["gutenberg"]=' in html and 'GROW["gutenberg"]=' in html
