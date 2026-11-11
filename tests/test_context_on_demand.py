"""A model loaded with `context_on_demand`, as Louise meets it
(docs/notes/idle-context-brief.md; app/model_load.py, app/embeddings.py,
app/resident.py).

Such a model keeps its weights and creates its context when a request
arrives; a request tells it how long to keep the context after the answer
(`metadata.keep_context_ms`, RAGE's value: app/rage/keep.py). Held here:
  - Louise knows the option from LocalAI's `/system` where it lists the
    options each model was loaded with (P1), else from the statement of the
    validated run the model's answers named — the node's LocalAI lists none,
    and a row without them is no "no" — nothing is typed (RULE 2);
  - the gate's keep of 0 reaches the engine whatever `/system` says;
  - a request to such a model carries the keep in its `metadata`, and a
    request to any other model carries nothing more than before — a runner
    that does not know the field is never sent it;
  - a WARM carries a keep of 0: a request that carries none leaves the
    context loaded (the operator, 2026-11-11), and a model preloaded and not
    yet asked would hold it from its load to its first request;
  - through VeGa the keep is OUTSIDE the tag: the inputs, their digests and
    the receipts are what they were (09 §9.12);
  - a creation the engine refuses for memory is a DEFERRAL, never an error on
    a document, and the expiry that follows it does not move the keep;
  - the resident keeper counts such a model at its RUNNING size: what it
    gives back while idle is headroom, not budget for another preload.
"""
from __future__ import annotations

import time
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
    keyed = {s["model"]: s["keep_ms"] for s in keep.snapshot()}
    assert keyed == {NEMOTRON: 20, BGE: None}, "both streams counted, one of them keyed"


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
    with dispatch.working_as(dispatch.Level.CORPUS, "wikidata"):
        # the stream has answered once: what expires next is judged (the review of
        # 2026-11-12: the refusal was the stream's first request, so nothing was)
        embeddings._embed_batch(["alpha", "beta"], 5.0, OPTED_IN)
        node.refuse = 1
        with pytest.raises(embeddings.EndpointDeferred) as e:
            embeddings._embed_batch(["alpha", "beta"], 5.0, OPTED_IN)
        assert not isinstance(e.value, embeddings.InputRefused)
        assert len(node.real()) == 2, "refused once, sent once: no split, no cut"
        # the retry comes after the keep expired: an expiry the refusal caused,
        # which does not move the stream's keep
        time.sleep((20 + keep.TRANSIT_MS + 30) / 1000)
        vectors = embeddings._embed_batch(["alpha", "beta"], 5.0, OPTED_IN)
    assert len(vectors) == 2
    stream, = keep.snapshot()
    assert stream["keep_ms"] == 20 and stream["premature"] == 0
    assert stream["excused"] == {keep.EXCUSED_DEFERRED: 1}, "judged, and excused"


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


def test_such_a_model_never_seen_working_counts_as_a_model_never_seen(node):
    """The review of 2026-11-12: preloaded, its context not yet created, its
    largest reading is its weights alone — ~2 GB short of what its first
    request creates. Counted at its weights, the budget would admit another
    preload and the first creation would be refused; it counts as unknown
    (the set's median) until a reading shows it working."""
    qwen = "vega-qwen3-embedding-4b"
    node.loaded.add(qwen)
    node.options[qwen] = ["context_on_demand"]
    mem = resident.Memory()
    for _ in range(3):
        node.vram[qwen] = 2_381 * MIB
        model_load.loaded_models(SERVER, max_age_s=0.0)
    on_demand = resident.Resident(SERVER, qwen, ("document",), ("gutenberg",))
    assert resident._rest_size(on_demand, mem) is None, "never seen working: unknown, not its weights"
    node.vram[qwen] = 4_263 * MIB
    model_load.loaded_models(SERVER, max_age_s=0.0)
    assert resident._rest_size(on_demand, mem) == 4_263 * MIB, "seen working: its running size"


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


def test_a_warm_gives_its_context_back_at_once(node):
    """The operator, 2026-11-11: "A request that carries no parameter is using
    the context in the canonical way: no free after use". A warm LOADS a
    model, and before the load nobody knows its options: so the warm always
    carries a keep of 0 — a model loaded with the option frees the warm's
    context at once, any other ignores the key — and the real request after
    it carries its stream's keep."""
    node.loaded.discard(NEMOTRON)                    # cold: the request warms it first
    with dispatch.working_as(dispatch.Level.CORPUS, "wikidata"):
        embeddings._request_vectors(["alpha"], 5.0, OPTED_IN)
    warm, = node.warms()
    assert warm["json"] == {"input": ["warm"], "model": NEMOTRON,
                            "metadata": {"keep_context_ms": "0"}}
    assert keep.warm_body() == {"metadata": {"keep_context_ms": "0"}}
    # the options were unknown when the real request went (the listing that
    # found the model cold is still the cached one): it carries nothing, and
    # the next one, after a fresh listing, carries its stream's keep
    model_load.loaded_models(SERVER, max_age_s=0.0)
    with dispatch.working_as(dispatch.Level.CORPUS, "wikidata"):
        embeddings._request_vectors(["beta"], 5.0, OPTED_IN)
    assert node.real()[-1]["json"]["metadata"] == {"keep_context_ms": "20"}



def test_a_translation_s_segments_carry_the_keep_and_its_warm_zero(monkeypatch):
    """MADLAD on llama.cpp is in the first deployment (the brief §1.3). Each
    segment's completion says its keep — the translation stream's, named
    explicitly because a segment runs on a pool thread that carries no level
    — and the warm says 0. A model not loaded with the option is sent the
    body it always was."""
    from app import config, translate
    bodies: list[dict] = []

    def post(url, json=None, timeout=None, headers=None):             # noqa: A002
        bodies.append(json)
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"choices": [{"text": "ciao"}]})
    keep.reset()
    monkeypatch.setattr(config, "TRANSLATE_URL", SERVER)
    monkeypatch.setattr(config, "TRANSLATE_MODEL", "louise-translate-madlad")
    monkeypatch.setattr(translate.httpx, "post", post)
    opted = {"in": False}
    monkeypatch.setattr(model_load, "context_on_demand", lambda url, model: opted["in"])

    assert translate._t5_completion("it", "hello", 5.0) == "ciao"
    assert "metadata" not in bodies[-1], "not loaded with the option: nothing more than before"

    opted["in"] = True
    translate._t5_completion("it", "hello", 5.0)
    assert bodies[-1]["metadata"] == {"keep_context_ms": "20"}
    translate._t5_completion("it", "OK", 5.0, max_tokens=4, warm=True)
    assert bodies[-1]["metadata"] == {"keep_context_ms": "0"}
    streams = {(s["plugin"], s["level"], s["model"]) for s in keep.snapshot()}
    assert streams == {("", "translate", "louise-translate-madlad")}
    keep.reset()


# ---- a LocalAI that lists no load options (the node's: München, 2026-11-12) ------------

class Upstream(Node):
    """LocalAI v4.12.1 as the node runs it: its `/system` rows are {id, backend,
    process, size_vram} — no `load_options` (our P1 is not in it)."""

    def get(self, url, timeout=None, **kw):
        r = super().get(url, timeout=timeout, **kw)
        rows = [{k: v for k, v in row.items() if k != "load_options"} for row in r.json()["loaded_models"]]
        return httpx.Response(200, request=r.request,
                              json={"backends": ["llama-cpp"], "loaded_models": rows})


@pytest.fixture
def upstream(node, monkeypatch):
    up = Upstream()
    up.loaded |= set(node.loaded)
    up.options = node.options
    monkeypatch.setattr(model_load.httpc, "get", up.get)
    monkeypatch.setattr(embeddings.httpc, "post", up.post)
    return up


def test_a_listing_without_options_says_nothing_and_the_run_s_statement_does(upstream):
    """A row without `load_options` is no "no": the option is then read from
    the statement of the validated run the model's answers named (its
    `engine.options`, VeGa's), and a later listing that cannot say leaves it."""
    assert model_load.loaded_models(SERVER, max_age_s=0.0) == {NEMOTRON, BGE}
    assert model_load.listed_on_demand(SERVER, NEMOTRON) is None, "the listing cannot say"
    assert model_load.context_on_demand(SERVER, NEMOTRON) is False, "nobody said: no keep"
    with dispatch.working_as(dispatch.Level.CORPUS, "wikidata"):
        embeddings._request_vectors(["alpha"], 5.0, OPTED_IN)
    assert "metadata" not in upstream.real()[-1]["json"], "the engine keeps its context, as it always did"

    model_load.note_run_options(SERVER, NEMOTRON, {"Model": "n.gguf",
                                                   "Options": ["ubatch:8192", "context_on_demand"]})
    model_load.note_run_options(SERVER, BGE, {"Model": "b.gguf", "Options": ["ubatch:1024"]})
    model_load.loaded_models(SERVER, max_age_s=0.0)
    assert model_load.context_on_demand(SERVER, NEMOTRON) is True, "the run said; the listing did not"
    assert model_load.context_on_demand(SERVER, BGE) is False
    with dispatch.working_as(dispatch.Level.CORPUS, "wikidata"):
        embeddings._request_vectors(["beta"], 5.0, OPTED_IN)
    assert upstream.real()[-1]["json"]["metadata"] == {"keep_context_ms": "20"}

    # a statement without options says nothing either, and leaves the note
    model_load.note_run_options(SERVER, NEMOTRON, None)
    assert model_load.context_on_demand(SERVER, NEMOTRON) is True


def test_a_listing_with_its_options_decides_over_a_run_noted_before(node):
    """Where `/system` lists the options (P1), its reading is the model as it
    is loaded NOW, and wins over the last run noted."""
    model_load.note_run_options(SERVER, BGE, {"Options": ["context_on_demand"]})
    assert model_load.context_on_demand(SERVER, BGE) is True
    model_load.loaded_models(SERVER, max_age_s=0.0)
    assert model_load.listed_on_demand(SERVER, BGE) is False
    assert model_load.context_on_demand(SERVER, BGE) is False


def test_the_gate_s_fixed_keep_reaches_the_engine_whatever_system_says(upstream):
    """The gate fixes a keep of 0 having read the option from the run's own
    statement (run_check.fresh_contexts). Sent only where `/system` said yes,
    it never reached the node's engine: no context was freed, and the pass
    measured one context 300 times."""
    model_load.loaded_models(SERVER, max_age_s=0.0)
    assert model_load.context_on_demand(SERVER, NEMOTRON) is False
    with keep.fixed(0):
        embeddings._request_vectors(["alpha"], 5.0, OPTED_IN)
    assert upstream.real()[-1]["json"]["metadata"] == {"keep_context_ms": "0"}
    embeddings._request_vectors(["beta"], 5.0, OPTED_IN)
    assert "metadata" not in upstream.real()[-1]["json"], "and nothing once the pass is over"
