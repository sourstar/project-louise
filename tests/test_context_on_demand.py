"""A model loaded with `context_on_demand`, as Louise meets it
(docs/notes/idle-context-brief.md; app/model_load.py, app/embeddings.py,
app/resident.py).

Such a model keeps its weights and creates its context when a request
arrives; a request tells it how long to keep the context after the answer
(`metadata.keep_context_ms`, RAGE's value: app/rage/keep.py). Held here:
  - Louise knows the option from LocalAI's `/system`, which lists the
    options each model was loaded with (P1) — nothing is typed (RULE 2);
  - a request to such a model carries the keep in its `metadata`, and a
    request to any other model carries nothing more than before — a runner
    that does not know the field is never sent it;
  - through VeGa the keep is OUTSIDE the tag: the inputs, their digests and
    the receipts are what they were (09 §9.12);
  - a creation the engine refuses for memory is a DEFERRAL, never an error on
    a document, and the expiry that follows it does not move the keep;
  - the resident keeper counts such a model at its RUNNING size: what it
    gives back while idle is headroom, not budget for another preload.
"""
from __future__ import annotations

import httpx
import pytest

from app import collections as collections_mod, embeddings, model_load, resident
from app.rage import dispatch, keep
from tests.test_model_load import REAL_FETCH, Runner, SERVER

MIB = 1024 * 1024
NEMOTRON = "vega-nemotron-3-embed-1b"
BGE = "vega-bge-m3"
OPTED_IN = collections_mod.EmbedRecipe(url=SERVER, api="openai", model=NEMOTRON, dtype="f32", ctx=512)
PLAIN = collections_mod.EmbedRecipe(url=SERVER, api="openai", model=BGE, dtype="f32", ctx=512)

#: the engine's own words for a creation it could not make (P11,
#: server-context.cpp): through LocalAI, gRPC INTERNAL, HTTP 500
REFUSAL = ("rpc error: code = Internal desc = the context cannot be created now, "
           "the memory it needs may be in use: try again later")


class Node(Runner):
    """LocalAI with the options each model was loaded with (P1)."""

    def __init__(self) -> None:
        super().__init__(load_s=0.0)
        self.options: dict[str, list[str]] = {}
        self.vram: dict[str, int] = {}
        self.refuse = 0

    def get(self, url, timeout=None, **_kw):
        assert url == f"{SERVER}/system", url
        with self.lock:
            self.system_reads += 1
            held = sorted(self.loaded)
        rows = []
        for m in held:
            row: dict = {"id": m, "process": {"pid": self.pid(m)},
                         "load_options": {"Model": f"{m}.gguf", "ContextSize": 8192,
                                          "Options": self.options.get(m, [])}}
            if m in self.vram:
                row["size_vram"] = self.vram[m]
            rows.append(row)
        return httpx.Response(200, request=httpx.Request("GET", url),
                              json={"backends": ["llama-cpp"], "loaded_models": rows})

    def post(self, url, json=None, timeout=None, headers=None, **kw):     # noqa: A002
        if self.refuse and json.get("input") != ["warm"]:
            self.refuse -= 1
            with self.lock:
                self.calls.append({"url": url, "json": json, "timeout": timeout})
            return httpx.Response(500, request=httpx.Request("POST", url),
                                  json={"error": {"message": REFUSAL}})
        return super().post(url, json=json, timeout=timeout, headers=headers, **kw)


@pytest.fixture
def node(monkeypatch):
    n = Node()
    n.loaded |= {NEMOTRON, BGE}
    n.options[NEMOTRON] = ["ubatch:8192", "context_on_demand", "parallel:1"]
    n.options[BGE] = ["ubatch:1024", "parallel:1"]
    model_load.reset()
    keep.reset()
    dispatch.reset()
    monkeypatch.setattr(model_load, "_fetch_system", REAL_FETCH)
    monkeypatch.setattr(model_load.httpc, "get", n.get)
    monkeypatch.setattr(embeddings.httpc, "post", n.post)
    monkeypatch.setattr(model_load, "load_timeout_s", lambda model: 5.0)
    yield n
    model_load.reset()
    keep.reset()
    dispatch.reset()


# ---- the option is read, never typed ---------------------------------------------------

@pytest.mark.parametrize("options, on", [
    (["context_on_demand"], True),
    (["context_on_demand:true"], True),
    (["ubatch:2048", "context_on_demand:1", "parallel:1"], True),
    (["--context-on-demand"], True),
    (["context_on_demand:false"], False),
    (["context_on_demand:off"], False),
    (["context_on_demand", "context_on_demand:false"], False),      # the last one said
    (["keep_context_ms:500"], False),                               # a default keep alone opts nothing in
    (["ubatch:2048"], False),
    ([], False),
    (None, False),
])
def test_the_option_as_localai_lists_it(options, on):
    assert model_load._options_on_demand({"Model": "m.gguf", "Options": options}) is on


def test_a_runner_that_lists_no_options_says_no():
    assert model_load._options_on_demand(None) is False
    assert model_load._options_on_demand({"Model": "m.gguf"}) is False
    assert model_load._options_on_demand("context_on_demand") is False


def test_louise_knows_the_option_from_system(node):
    assert model_load.context_on_demand(SERVER, NEMOTRON) is False, "never listed: not known"
    assert model_load.loaded_models(SERVER) == {NEMOTRON, BGE}
    assert model_load.context_on_demand(SERVER, NEMOTRON) is True
    assert model_load.context_on_demand(f"{SERVER}/v1/embeddings", NEMOTRON) is True
    assert model_load.context_on_demand(SERVER, BGE) is False
    assert model_load.context_on_demand(SERVER, None) is False
    # reloaded without it: the next listing says so
    node.options[NEMOTRON] = ["ubatch:8192"]
    model_load.loaded_models(SERVER, max_age_s=0.0)
    assert model_load.context_on_demand(SERVER, NEMOTRON) is False


# ---- the request ------------------------------------------------------------------------

def test_a_request_to_such_a_model_carries_the_keep_and_no_other_does(node):
    with dispatch.working_as(dispatch.Level.CORPUS, "wikidata"):
        embeddings._request_vectors(["alpha", "beta"], 5.0, OPTED_IN)
        embeddings._request_vectors(["gamma"], 5.0, PLAIN)
    opted, plain = node.real()
    assert opted["json"] == {"input": ["alpha", "beta"], "model": NEMOTRON,
                             "metadata": {"keep_context_ms": "20"}}
    assert plain["json"] == {"input": ["gamma"], "model": BGE}, "exactly the body it always was"
    stream, = keep.snapshot()
    assert (stream["plugin"], stream["level"], stream["model"], stream["requests"]) == \
        ("wikidata", "corpus", NEMOTRON, 1)


def test_each_job_type_sends_its_own_keep(node, monkeypatch):
    """Two collections on one model, and a person's query on it: three
    streams, and the one whose context expired between its requests is the
    one whose keep grew."""
    clock = {"t": 100.0}
    monkeypatch.setattr(keep.KEEPS, "_clock", lambda: clock["t"])
    monkeypatch.setattr(embeddings.time, "monotonic", lambda: clock["t"])
    for _ in range(3):
        with dispatch.working_as(dispatch.Level.CORPUS, "normattiva-nemo"):
            embeddings._request_vectors(["a"], 5.0, OPTED_IN)
        clock["t"] += 0.170
    clock["t"] += 10.0
    for _ in range(3):
        with dispatch.working_as(dispatch.Level.CORPUS, "wikidata"):
            embeddings._request_vectors(["b"], 5.0, OPTED_IN)
        clock["t"] += 0.005
    with dispatch.working_as(dispatch.Level.QUERY, "wikidata"):
        embeddings._request_vectors(["what is"], 5.0, OPTED_IN)
    sent = [c["json"]["metadata"]["keep_context_ms"] for c in node.real()]
    assert sent == ["20", "40", "80", "20", "20", "20", "20"]
    assert {(s["plugin"], s["level"]): s["keep_ms"] for s in keep.snapshot()} == {
        ("normattiva-nemo", "corpus"): 80, ("wikidata", "corpus"): 20, ("wikidata", "query"): 20}


def test_through_vega_the_keep_is_outside_the_tag(monkeypatch):
    """09 §9.12 fixes two things in this body, the tagged inputs and
    `encoding_format`; the keep is beside them, and no part of what a receipt
    vouches for."""
    from tests.vega.test_embedding_path import LOCALAI, MODEL, RECIPE, localai, vega  # noqa: F401
    from app.vega import tag as vtag
    bodies: list[dict] = []

    def post(url, json=None, **kw):                                   # noqa: A002
        bodies.append(json)
        raise httpx.ConnectError("stop here: only the body is under test",
                                 request=httpx.Request("POST", url))
    keep.reset()
    monkeypatch.setattr(embeddings.httpc, "post", post)
    monkeypatch.setattr(embeddings.config, "VEGA_URL", "http://vega:9000", raising=False)
    monkeypatch.setattr(model_load, "context_on_demand", lambda url, model: opted["in"])
    g = dispatch.gate(RECIPE.url, RECIPE.concurrency)
    opted = {"in": False}
    with pytest.raises(httpx.ConnectError):
        embeddings._request_vouched(["alpha"], 5.0, RECIPE, None, g, {}, 0.0)
    opted["in"] = True
    with pytest.raises(httpx.ConnectError):
        embeddings._request_vouched(["alpha"], 5.0, RECIPE, None, g, {}, 0.0)
    before, after = bodies
    assert set(before) == {"input", "encoding_format", "model"}
    assert set(after) == set(before) | {"metadata"}
    assert after["metadata"] == {"keep_context_ms": "20"}
    assert after["encoding_format"] == before["encoding_format"] == "base64"
    # the tagged input is the same object but for its one-time id: the same
    # form, the same content, so the same input digest
    (_, form_b, content_b), (_, form_a, content_a) = (vtag.parse(before["input"][0]),
                                                      vtag.parse(after["input"][0]))
    assert (form_a, content_a) == (form_b, content_b) == ("text", "alpha")
    assert vtag.input_digest(form_a, content_a) == vtag.input_digest(form_b, content_b)
    keep.reset()


# ---- a creation refused for memory ----------------------------------------------------

def test_the_engine_s_refusal_is_a_deferral_and_never_an_input_s(node):
    """The words must not read as one of the over-long-input spellings
    (`_over_budget`), which would split and cut the batch and put an error on
    a document for a card that was merely full."""
    response = httpx.Response(500, request=httpx.Request("POST", SERVER), json={"error": {"message": REFUSAL}})
    assert embeddings._over_budget(response) is False
    assert embeddings._numeric_failure(response) is False
    node.refuse = 1
    with dispatch.working_as(dispatch.Level.CORPUS, "wikidata"):
        with pytest.raises(embeddings.EndpointDeferred) as e:
            embeddings._embed_batch(["alpha", "beta"], 5.0, OPTED_IN)
        assert not isinstance(e.value, embeddings.InputRefused)
        assert len(node.real()) == 1, "refused once, sent once: no split, no cut"
        # and the retry, late as it is, does not move the stream's keep
        vectors = embeddings._embed_batch(["alpha", "beta"], 5.0, OPTED_IN)
    assert len(vectors) == 2
    stream, = keep.snapshot()
    assert stream["keep_ms"] == 20 and stream["premature"] == 0


# ---- the resident keeper ----------------------------------------------------------------

def test_the_keeper_counts_such_a_model_at_its_running_size(node):
    """qwen3-4b's own figures (the brief §6): 4,263 MiB running, 2,381 MiB
    idle — the weights alone. The budget counts 4,263: the 1,882 MiB it gives
    back while idle are headroom on the card, never room for another preload."""
    qwen, other = "vega-qwen3-embedding-4b", "vega-medcpt-article"
    node.loaded |= {qwen, other}
    node.options[qwen] = ["context_on_demand", "ubatch:2048"]
    node.options[other] = ["ubatch:512"]
    mem = resident.Memory()
    for running in (True, False, True, False):
        node.vram[qwen] = (4_263 if running else 2_381) * MIB
        node.vram[other] = (900 if running else 700) * MIB
        model_load.loaded_models(SERVER, max_age_s=0.0)
    on_demand = resident.Resident(SERVER, qwen, ("document",), ("gutenberg",))
    plain = resident.Resident(SERVER, other, ("document",), ("pubmed",))
    assert resident._size(on_demand, mem) == 4_263 * MIB
    assert resident._rest_size(on_demand, mem) == 4_263 * MIB, "its running size, by rule"
    assert resident._rest_size(plain, mem) == 700 * MIB, "any other model: at rest, as before"


def test_an_idle_reading_under_half_is_not_taken_for_a_model_off_the_card(node):
    """REST_FLOOR reads a size under half the largest as a model partly off
    the card. A model that keeps only its weights while idle can sit there by
    design — 2,381 of 5,643 MiB is 0.42 — and its size is still its running
    one."""
    qwen = "vega-qwen3-embedding-4b"
    node.loaded.add(qwen)
    node.options[qwen] = ["context_on_demand"]
    mem = resident.Memory()
    for mib in (5_643, 2_381, 2_381):
        node.vram[qwen] = mib * MIB
        model_load.loaded_models(SERVER, max_age_s=0.0)
    assert 2_381 / 5_643 < resident.REST_FLOOR
    r = resident.Resident(SERVER, qwen, ("document",), ("gutenberg",))
    assert resident._rest_size(r, mem) == 5_643 * MIB


def test_the_margin_s_sample_counts_it_at_the_same_size_as_the_budget(node):
    """The margin is the card's count minus what the loaded models hold at
    rest. Were such a model counted idle there and running in the budget, its
    context would be paid for twice: in its size, and as "growth" each time it
    works."""
    qwen, other = "vega-qwen3-embedding-4b", "vega-medcpt-article"
    node.loaded = {qwen, other}
    node.options[qwen] = ["context_on_demand"]
    mem = resident.Memory()
    for qwen_mib, other_mib in ((4_263, 900), (2_381, 700)):         # running, then idle
        node.vram[qwen], node.vram[other] = qwen_mib * MIB, other_mib * MIB
        held = resident._held_now([SERVER], mem)
    by_model = {m: v for (m, _pid), v in held.items()}
    assert by_model == {qwen: 4_263 * MIB, other: 700 * MIB}

