"""A new collection's file takes the [reranker] of a collection of the same
plugin held here (RULE 2; München, 2026-11-10: pubmed9 and wikipedia-v8 were
created with none, renamed over pubmed and wikipedia, and their searches
went unreranked, unsaid)."""
import tomllib
from types import SimpleNamespace

from app import collections as collections_mod, louise
from app.hold import collection as col
from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed, fake_query_embed
from tests.hold.test_template import recipe

URL = "http://enc.invalid/upstream/qwen"
BGE = collections_mod.RerankRecipe(url="http://ai:30286/v1/rerank", api="jina", model="louise-rerank-bge")


def _made(tmp_path, name):
    return col.create_from_recipe(tmp_path / "collections", "gutenberg", recipe(), platform_image=PLATFORM,
                                  embed=fake_embed, embed_query=fake_query_embed, name=name, key=KEY, now=NOW)


def test_a_new_collection_takes_the_reranker_of_its_plugins_held_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(collections_mod, "load_all", lambda *a, **k: [
        SimpleNamespace(name="pubmed", plugin="pubmed", reranker=None),
        SimpleNamespace(name="gutenberg", plugin="gutenberg", reranker=BGE)])
    codex = _made(tmp_path, "gutenberg2")
    path = louise._write_held_file(tmp_path / "collections", codex, URL)
    text = path.read_text()
    got = tomllib.loads(text)["reranker"]
    assert got == {"url": BGE.url, "api": "jina", "rerank_model": "louise-rerank-bge"}
    assert "# [reranker] carried from gutenberg" in text


def test_with_none_held_the_file_names_no_reranker(tmp_path, monkeypatch):
    monkeypatch.setattr(collections_mod, "load_all", lambda *a, **k: [
        SimpleNamespace(name="pubmed", plugin="pubmed", reranker=BGE)])
    codex = _made(tmp_path, "gutenberg3")
    path = louise._write_held_file(tmp_path / "collections", codex, URL)
    assert "reranker" not in tomllib.loads(path.read_text())
