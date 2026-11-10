"""A new collection's [encoder] takes the batch, concurrency and device of a
collection held here that the same encoder already serves — the same space
first, else the same model at the same endpoint (operator, 2026-11-10: "What
you are building for normattiva-nemo must be reusable for the other legal
collections in the pipeline"; RULE 2). The gate measures the run at that
batching before a batched answer is written, so nothing carried is trusted."""
import tomllib
from types import SimpleNamespace

from app import collections as collections_mod, louise
from tests.hold.test_held_file_reranker import URL, _made


def recipe(url=URL, model="qwen", stated=("batch", "concurrency"), batch=16, concurrency=4, device="gpu"):
    return collections_mod.EmbedRecipe(url=url, api="vega", dtype="f16", ctx=512, model=model,
                                       batch=batch, concurrency=concurrency,
                                       stated=frozenset(stated), device=device)


def held(name, embed, space_id="sha256:other"):
    return SimpleNamespace(name=name, plugin="x", reranker=None, embed=embed,
                           codex=SimpleNamespace(space_id=space_id))


def encoder_of(path):
    return tomllib.loads(path.read_text())["encoder"]


def test_the_same_space_gives_its_batch_concurrency_and_device(tmp_path, monkeypatch):
    codex = _made(tmp_path, "neuris")
    monkeypatch.setattr(collections_mod, "load_all", lambda *a, **k: [
        held("aaa", recipe(batch=2, concurrency=2), "sha256:other"),
        held("legi", recipe(url="http://other.invalid/x", model="x"), codex.space_id)])
    path = louise._write_held_file(tmp_path / "collections", codex, URL)
    enc = encoder_of(path)
    assert (enc["batch"], enc["concurrency"], enc["device"]) == (16, 4, "gpu"), \
        "the same space wins over a same-model collection earlier by name"
    assert "carried from legi (the same space" in path.read_text()


def test_else_the_same_model_at_the_same_endpoint(tmp_path, monkeypatch):
    codex = _made(tmp_path, "legi2")
    monkeypatch.setattr(collections_mod, "load_all", lambda *a, **k: [
        held("other", recipe(model="another", batch=8)),
        held("wikidata", recipe(url=URL + "/", stated=("batch",), batch=16, device=None))])
    enc = encoder_of(louise._write_held_file(tmp_path / "collections", codex, URL))
    assert enc["batch"] == 16 and "concurrency" not in enc and "device" not in enc, \
        "only the keys the source states"


def test_nothing_served_by_it_carries_nothing(tmp_path, monkeypatch):
    codex = _made(tmp_path, "fresh")
    monkeypatch.setattr(collections_mod, "load_all", lambda *a, **k: [
        held("other", recipe(model="another"))])
    enc = encoder_of(louise._write_held_file(tmp_path / "collections", codex, URL))
    assert not set(louise.DRIVING) & set(enc)


def test_what_is_carried_reads_back_as_the_recipe(tmp_path, monkeypatch):
    codex = _made(tmp_path, "eurlex")
    monkeypatch.setattr(collections_mod, "load_all", lambda *a, **k: [
        held("legi", recipe(), codex.space_id)])
    raw = encoder_of(louise._write_held_file(tmp_path / "collections", codex, URL))
    got = collections_mod.parse_recipe(raw, where="eurlex")
    assert (got.batch, got.concurrency, got.spends, got.stated) == (16, 4, "gpu",
                                                                    frozenset({"batch", "concurrency"}))


def test_a_bi_encoders_question_side_is_carried_from_the_same_space(tmp_path, monkeypatch):
    """München, 2026-11-10: pubmed's re-created file lost its [encoder] node
    keys; a lost query side would embed questions with the document model."""
    codex = _made(tmp_path, "pubmed2")
    medcpt = collections_mod.EmbedRecipe(url=URL, api="openai", dtype="f16", ctx=512, model="qwen",
                                         query_url="http://enc.invalid/upstream/medcpt-query",
                                         query_model="medcpt-query", concurrency=4,
                                         stated=frozenset({"concurrency"}), device="gpu")
    monkeypatch.setattr(collections_mod, "load_all", lambda *a, **k: [
        held("other", recipe(), "sha256:other"), held("pubmed", medcpt, codex.space_id)])
    enc = encoder_of(louise._write_held_file(tmp_path / "collections", codex, URL))
    assert (enc["query_url"], enc["query_model"]) == ("http://enc.invalid/upstream/medcpt-query", "medcpt-query")
    assert (enc["concurrency"], enc["device"]) == (4, "gpu") and "batch" not in enc


def test_another_spaces_question_side_is_never_carried(tmp_path, monkeypatch):
    codex = _made(tmp_path, "pubmed3")
    other = collections_mod.EmbedRecipe(url=URL, api="openai", dtype="f16", ctx=512, model="qwen",
                                        query_url="http://enc.invalid/upstream/q2", query_model="q2")
    monkeypatch.setattr(collections_mod, "load_all", lambda *a, **k: [held("x", other, "sha256:other")])
    enc = encoder_of(louise._write_held_file(tmp_path / "collections", codex, URL))
    assert "query_url" not in enc and enc["query_model"] == enc["embed_model"]
