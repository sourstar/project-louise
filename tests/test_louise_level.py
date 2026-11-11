"""Every request to a model endpoint carries its level, and the chat client
speaks either protocol (migration-plan step 13e; docs/09-formats.md §9.12,
*the request's level*; model-pool.md@c572c3b §4, §11).

`Louise-Level` is what lets a model container hold the card's clock while a
person waits without anything being written anywhere: it sees every model
request itself (the model pool's front did, until 2026-10-09). `CHAT_API=openai` is
what lets the chat model leave Ollama for LocalAI — the same file, behind
llama-server — and come back by switching one variable.
"""
import json

import pytest

from app import collections as collections_mod, config, embeddings, llm, \
    retrieval, translate
from app.rage import dispatch
from app.rage.dispatch import Level


@pytest.fixture
def posts(monkeypatch):
    """Every `httpx.post` the clients make, answered by whatever fits."""
    seen = []

    class Answer:
        status_code = 200
        text = ""

        def __init__(self, url):
            self.url = url

        def raise_for_status(self):
            pass

        def json(self):
            if self.url.endswith("/v1/completions"):        # the translator, 15e-2
                return {"choices": [{"text": '{"translations": ["ciao"]}'}]}
            if "rerank" in self.url:
                return {"results": [{"index": 0, "relevance_score": 0.9}]}
            if self.url.endswith("/v1/embeddings"):
                return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
            return {"embeddings": [[1.0, 0.0]]}

    def post(url, **kwargs):
        seen.append((url, kwargs))
        return Answer(url)

    import httpx

    from app import httpc
    monkeypatch.setattr(httpx, "post", post)
    # the embedding and rerank clients go through the pooled client now, which
    # exists so that one TLS context and one connection serve every request
    # (app/httpc.py); the header they carry is what this file is about
    monkeypatch.setattr(httpc, "post", post)
    return seen


def _level(call):
    return call[1]["headers"][dispatch.LEVEL_HEADER]


def test_an_embedding_carries_the_level_of_whoever_asks(posts, isolated_config):
    recipe = collections_mod.recipe_for(collections_mod.default_collection())
    embeddings._embed_texts(["a question"], recipe=recipe)
    with dispatch.working_as(Level.CORPUS, "gutenberg"):
        embeddings._embed_texts(["a chunk"], recipe=recipe)
    assert [_level(c) for c in posts] == ["query", "corpus"], \
        "the web's unmarked call has a person behind it; the worker's is background"


def test_the_openai_embedding_path_carries_it_too(posts, isolated_config):
    import dataclasses
    recipe = dataclasses.replace(
        collections_mod.recipe_for(collections_mod.default_collection()),
        url="http://nas:30437/upstream/bge-m3", api="openai")
    embeddings._embed_texts(["x"], recipe=recipe)
    assert posts[0][0] == "http://nas:30437/upstream/bge-m3/v1/embeddings"
    assert _level(posts[0]) == "query"


def test_a_rerank_and_a_translation_say_what_they_are(posts, monkeypatch, isolated_config):
    recipe = collections_mod.RerankRecipe(url="http://nas:30437/upstream/bge-reranker/v1/rerank",
                                          api="jina")
    hits, reranked = retrieval._rerank_list("whales", [{"text": "a whale"}], recipe, 5.0)
    assert reranked and _level(posts[-1]) == "rerank"

    monkeypatch.setattr(config, "TRANSLATE_URL", "http://nas:30437/upstream/madlad")
    assert translate.translate([("hello", "it")]) == ["ciao"]
    assert _level(posts[-1]) == "translate"


# ---- the chat client, in both protocols -------------------------------------------

class _FakeStream:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self):
        yield from self.lines


@pytest.fixture
def chat_server(monkeypatch, isolated_config):
    seen = {}

    class FakeClient:
        def __init__(self, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def post(self, url, json=None, headers=None):
            seen.update(url=url, body=json, headers=headers)

            class R:
                def raise_for_status(self_inner):
                    pass

                def json(self_inner):
                    return seen["answer"]
            return R()

        def stream(self, method, url, json=None, headers=None):
            seen.update(url=url, body=json, headers=headers)
            return _FakeStream(seen["lines"])

    monkeypatch.setattr(llm.httpx, "Client", FakeClient)
    monkeypatch.setattr(config, "CHAT_URL", "http://chat")
    return seen


def test_ollama_stays_exactly_what_it_was(chat_server, monkeypatch):
    monkeypatch.setattr(config, "CHAT_API", "ollama")
    chat_server["answer"] = {"message": {"content": "<think>hm</think>Ahab."}}
    assert llm.chat([{"role": "user", "content": "who?"}], max_tokens=50, num_ctx=4096) == "Ahab."
    assert chat_server["url"] == "http://chat/api/chat"
    body = chat_server["body"]
    assert body["think"] is False and body["options"] == {
        "temperature": 0.3, "num_ctx": 4096, "num_predict": 50}
    assert chat_server["headers"] == {"Louise-Level": "chat"}


def test_openai_is_what_the_model_pool_served(chat_server, monkeypatch):
    monkeypatch.setattr(config, "CHAT_API", "openai")
    monkeypatch.setattr(config, "CHAT_URL", "http://nas:30437/upstream/chat")
    chat_server["answer"] = {"choices": [{"message": {"content": "Ahab."}}]}
    assert llm.chat([{"role": "user", "content": "who?"}], temperature=0.0,
                    max_tokens=50, level=Level.TRANSLATE) == "Ahab."
    assert chat_server["url"] == "http://nas:30437/upstream/chat/v1/chat/completions"
    body = chat_server["body"]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}, \
        "thinking off through the model's own template"
    assert body["max_tokens"] == 50 and body["temperature"] == 0.0 and body["stream"] is False
    assert "options" not in body and "num_ctx" not in json.dumps(body), \
        "the context size is the server's, fixed at launch"
    assert chat_server["headers"] == {"Louise-Level": "translate"}, \
        "the chat model standing in for the translation model says so"


def test_a_streamed_answer_is_read_in_either_protocol(chat_server, monkeypatch):
    monkeypatch.setattr(config, "CHAT_API", "ollama")
    chat_server["lines"] = ['{"message": {"content": "Call me "}}', "",
                            '{"message": {"content": "Ishmael."}, "done": true}',
                            '{"message": {"content": "never read"}}']
    assert "".join(llm.chat_stream([{"role": "user", "content": "?"}])) == "Call me Ishmael."
    assert chat_server["headers"] == {"Louise-Level": "chat"}

    monkeypatch.setattr(config, "CHAT_API", "openai")
    chat_server["lines"] = [": keep-alive", 'data: {"choices": [{"delta": {"role": "assistant"}}]}',
                            'data: {"choices": [{"delta": {"content": "Call me "}}]}',
                            "data: not json",
                            'data: {"choices": [{"delta": {"content": "Ishmael."}}]}',
                            "data: [DONE]",
                            'data: {"choices": [{"delta": {"content": "never read"}}]}']
    assert "".join(llm.chat_stream([{"role": "user", "content": "?"}],
                                   max_tokens=None)) == "Call me Ishmael."
    assert chat_server["url"].endswith("/v1/chat/completions")
    assert "max_tokens" not in chat_server["body"] and chat_server["body"]["stream"] is True


def test_the_levels_header_names_are_the_specs():
    assert dispatch.LEVEL_HEADER == "Louise-Level"
    assert [l.name.lower() for l in Level] == ["corpus", "translate", "query", "rerank", "chat"]


# ---- a chat request gives its context back (operator, 2026-11-11: "Wire chat to release the
#      context after use: we already know that it is a one shot operation") ---------------------

KEEP_0 = {"keep_context_ms": "0"}


def test_every_chat_request_to_localai_carries_a_keep_of_zero(chat_server, monkeypatch):
    """An answer, a streamed answer, a round with tools and the warm: each
    carries `metadata.keep_context_ms = "0"`, so a chat model loaded with
    `context_on_demand` frees its context when the answer is complete."""
    from app import model_load
    monkeypatch.setattr(config, "CHAT_API", "openai")
    monkeypatch.setattr(config, "CHAT_MODEL", "qwen3")
    monkeypatch.setattr(config, "CHAT_URL", "http://localai:8080")
    asked = []
    monkeypatch.setattr(model_load, "speaks_localai", lambda server: asked.append(server) or True)
    monkeypatch.setattr(llm, "warm", lambda level=Level.CHAT: None)
    chat_server["answer"] = {"choices": [{"message": {"content": "Ahab."}}]}
    assert llm.chat([{"role": "user", "content": "who?"}]) == "Ahab."
    assert chat_server["body"]["metadata"] == KEEP_0
    assert asked == ["http://localai:8080"], "the runner, as app/model_load.py names it"
    llm.chat_tools([{"role": "user", "content": "who?"}], [{"type": "function", "function": {"name": "f"}}])
    assert chat_server["body"]["metadata"] == KEEP_0 and chat_server["body"]["tools"]
    chat_server["lines"] = ['data: {"choices": [{"delta": {"content": "Ahab."}}]}', "data: [DONE]"]
    assert "".join(llm.chat_stream([{"role": "user", "content": "who?"}])) == "Ahab."
    assert chat_server["body"]["metadata"] == KEEP_0 and chat_server["body"]["stream"] is True
    # nothing else in the body moved
    assert chat_server["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert chat_server["body"]["model"] == "qwen3"


def test_the_warm_of_the_chat_model_carries_it_too(monkeypatch, isolated_config):
    """The keeper preloads the chat model through this warm, and nothing
    follows it: without a keep the model would hold its context from its load
    to the first question."""
    from app import model_load
    monkeypatch.setattr(config, "CHAT_API", "openai")
    monkeypatch.setattr(config, "CHAT_MODEL", "qwen3")
    monkeypatch.setattr(config, "CHAT_URL", "http://localai:8080")
    sent = []

    def ensure_loaded(url, model, send, **kw):
        # the runner is asked what it is BEFORE the body is built
        monkeypatch.setattr(model_load, "speaks_localai", lambda server: True)
        send(5.0)
    monkeypatch.setattr(model_load, "speaks_localai", lambda server: None)
    monkeypatch.setattr(model_load, "ensure_loaded", ensure_loaded)
    monkeypatch.setattr(llm.httpx, "post", lambda url, json=None, timeout=None, headers=None:
                        sent.append((url, json)))
    llm.warm()
    (url, body), = sent
    assert url == "http://localai:8080/v1/chat/completions"
    assert body["metadata"] == KEEP_0 and body["max_tokens"] == 1


@pytest.mark.parametrize("localai", [False, None])
def test_a_runner_that_is_not_localai_is_sent_what_it_always_was(chat_server, monkeypatch, localai):
    """Another OpenAI-style service is sent nothing it did not ask for (a
    strict one refuses a `metadata` it has no use for); one that cannot be
    asked what it is, neither."""
    from app import model_load
    monkeypatch.setattr(config, "CHAT_API", "openai")
    monkeypatch.setattr(config, "CHAT_MODEL", "qwen3")
    monkeypatch.setattr(model_load, "speaks_localai", lambda server: localai)
    monkeypatch.setattr(llm, "warm", lambda level=Level.CHAT: None)
    chat_server["answer"] = {"choices": [{"message": {"content": "Ahab."}}]}
    llm.chat([{"role": "user", "content": "who?"}], temperature=0.0, max_tokens=50)
    assert chat_server["body"] == {
        "model": "qwen3", "messages": [{"role": "user", "content": "who?"}], "stream": False,
        "chat_template_kwargs": {"enable_thinking": False}, "temperature": 0.0, "max_tokens": 50}


def test_ollama_has_its_own_keep_alive_and_no_metadata(chat_server, monkeypatch):
    from app import model_load
    monkeypatch.setattr(config, "CHAT_API", "ollama")
    monkeypatch.setattr(model_load, "speaks_localai", lambda server: pytest.fail("not asked"))
    chat_server["answer"] = {"message": {"content": "Ahab."}}
    llm.chat([{"role": "user", "content": "who?"}])
    assert "metadata" not in chat_server["body"] and "keep_alive" in chat_server["body"]


def test_a_one_shot_body_is_the_key_rage_writes():
    from app.rage import keep
    assert keep.one_shot_body() == {"metadata": {keep.FIELD: "0"}} == keep.warm_body()
