"""The gate's fresh-context pass (app/hold/embedder.py `fresh_contexts`;
docs/notes/idle-context-brief.md §1.4, invariant 12).

A model loaded with `context_on_demand` creates its context per request, so
every creation is a first pass after a load — thousands a day in production,
where the gate's fresh loads are three. For such a model the gate asks the
sixteen fingerprint texts and the longest texts on 300 contexts of their own
and holds each answer to the same text's first:
  - 300 creations, in a new order each round, every one the same bytes: the
    run is exact, and says how many contexts it was asked on;
  - ONE answer in 300 off by its last bits fails an `exact` run, with the
    reason naming the contexts and not the loads; a `near` run takes it within
    the bound, and its statement's one variation covers it;
  - further apart than the bound, or a NaN, fails either;
  - a model without the option is asked nothing: the gate is what it was;
  - only a model LocalAI lists with the option gets the pass, and its
    requests carry a keep of 0;
  - COUNTED, NEVER ASSUMED (the review of 2026-11-12): every round must have
    made one context per text by the engine's own count — a context another
    request held is not a fresh one (deferred), and an engine whose creations
    cannot be counted is not admitted (refused);
  - the option is read from the run's own statement where it lists its load
    options, else from a `/system` read made now; a runner that cannot say —
    a listing without `load_options` included — defers the gate, never skips
    the pass.
"""
from __future__ import annotations

import numpy as np
import pytest

from app import collections as collections_mod, model_load, run_check
from app.hold import embedder as emb, runs as hold_runs
from app.rage import keep
from tests.hold.conftest import fake_embed
from tests.hold.test_backend_engine import _vega_run
from tests.hold.test_embedder import a_key, codex_768, perturbed, reference
from tests.hold.test_near_exact import _nudged


class OnDemand:
    """A model that creates its context per request and answers as
    `fake_embed` — but for the creation numbered `bad`, where the text gets
    `wrong(vector)` instead."""

    def __init__(self, *, bad: int | None = None, wrong=None, held_round: int | None = None) -> None:
        self.created = 0          # the answers on a context of their own
        self.counter = 0          # the engine's own count of its creations
        self.bad, self.wrong = bad, wrong
        self.orders: list[list] = []
        self.loads = 0
        self.held_round = held_round   # this round's probes land on a context another request held

    def reload(self) -> None:
        self.loads += 1

    def one(self, texts):
        return fake_embed(texts)

    def fresh(self, texts):
        self.orders.append(list(texts))
        out = fake_embed(texts)
        for i in range(len(texts)):
            self.created += 1
            if len(self.orders) != self.held_round:
                self.counter += 1
            if self.created == self.bad:
                out[i] = self.wrong(out[i])
        return out


class Probe:
    """What run_check gives the gate: the fresh embed, and the engine's count."""

    def __init__(self, model: OnDemand, *, counted: bool = True) -> None:
        self.model = model
        if counted:
            self.creations = lambda: model.counter

    def __call__(self, texts):
        return self.model.fresh(texts)


def _gate(model: OnDemand, *, exactness: str = "exact", fresh: bool = True, counted: bool = True):
    codex = codex_768(version=5)
    ref = reference()
    return codex, emb.run_gate(
        codex, reference=ref, embed=lambda t: ref, seed=b"\x09" * 32,
        stated=_vega_run(codex), embed_one=model.one, contribute=model.one,
        cpu_reference=("sha256:" + "44" * 32, perturbed(ref, indices=(3,), to=0.9995)),
        reload=model.reload, exactness=exactness,
        fresh=Probe(model, counted=counted) if fresh else None)


def test_three_hundred_contexts_of_their_own_and_an_exact_model_stays_exact():
    model = OnDemand()
    codex, run = _gate(model)
    assert model.created == emb.FRESH_CONTEXTS == 300
    assert (run.contexts, run.context_variation, run.load_variation) == (300, 1.0, 1.0)
    assert run.ok, run.why()
    # short and long probes, and not the same order twice
    assert len(model.orders[0]) == 16 + emb.LONG_PROBE
    assert model.orders[0] != model.orders[1]
    assert sorted(map(str, model.orders[0])) == sorted(map(str, model.orders[1]))
    assert max(map(len, model.orders[0])) > 10 * min(map(len, model.orders[0])), "long and short"
    body = emb.statement(run, codex, a_key(), now="2026-11-11T09:00:00Z")["body"]
    assert body["exactness"] == "exact" and float(body["load_variation"]) == 1.0
    assert "contexts" not in body, "09 §9.12's statement is what it was"


def test_one_answer_in_three_hundred_off_by_its_last_bits_fails_an_exact_run():
    model = OnDemand(bad=217, wrong=lambda v: _nudged(v, 0.9999995))
    _codex, run = _gate(model)
    assert run.contexts == 300 and run.context_variation < 1.0
    assert run.load_variation == run.context_variation, "the statement's number covers it"
    assert not run.ok
    why = run.why()
    assert "on a context created for the request" in why and "300 fresh contexts" in why
    assert "fresh loads" not in why, "the loads were exact: the reason names the contexts"


def test_a_near_run_takes_it_within_the_bound_and_no_further():
    within = OnDemand(bad=40, wrong=lambda v: _nudged(v, 0.999995))
    codex, run = _gate(within, exactness="near")
    assert run.ok, run.why()
    body = emb.statement(run, codex, a_key(), now="2026-11-11T09:00:00Z")["body"]
    assert body["exactness"] == "near"
    assert float(body["load_variation"]) == pytest.approx(run.context_variation, abs=1e-9)
    assert hold_runs.passing(body, codex)

    beyond = OnDemand(bad=40, wrong=lambda v: _nudged(v, 0.9998))
    _codex, run = _gate(beyond, exactness="near")
    assert not run.ok and run.context_variation < emb.NEAR_VARIATION


def test_a_nan_on_a_fresh_context_is_a_wrong_answer_not_a_variation():
    model = OnDemand(bad=5, wrong=lambda v: [float("nan")] + list(v[1:]))
    for exactness in ("exact", "near"):
        model.created = 0
        _codex, run = _gate(model, exactness=exactness)
        assert run.context_variation == 0.0 and not run.ok


def test_a_model_without_the_option_is_asked_nothing():
    model = OnDemand()
    _codex, run = _gate(model, fresh=False)
    assert model.created == 0 and (run.contexts, run.context_variation) == (0, 1.0)
    assert run.ok, run.why()


def test_the_pass_counts_what_it_is_given():
    texts = [f"text {i}" for i in range(7)]
    first = fake_embed(texts)
    seen: list[int] = []

    def fresh(batch):
        seen.append(len(batch))
        return fake_embed(batch)
    count = lambda: sum(seen)    # noqa: E731 — the engine made one context per text
    assert emb.fresh_contexts(texts, fresh, first, b"seed", creations=20, counted=count) == (20, 1.0)
    assert seen == [7, 7, 6], "whole rounds, then only what is left to make 20"
    with pytest.raises(emb.GateError):
        emb.fresh_contexts(texts, fresh, first[:3], b"seed", counted=count)
    with pytest.raises(emb.GateError):
        emb.fresh_contexts(texts, lambda batch: fake_embed(batch)[:-1], first, b"seed", counted=count)


def test_creations_that_cannot_be_counted_are_never_assumed():
    """No count to read: the run is refused, whatever its answers — it would
    otherwise certify 300 contexts it never saw made."""
    texts = [f"text {i}" for i in range(4)]
    with pytest.raises(emb.GateError, match="count"):
        emb.fresh_contexts(texts, fake_embed, fake_embed(texts), b"seed")
    model = OnDemand()
    with pytest.raises(emb.GateError, match="count"):
        _gate(model, counted=False)


def test_a_context_another_request_held_is_not_a_fresh_one():
    """Another collection on the same model keeps the context alive past a
    keep of 0 (the engine keeps it for the longest keep asked): the round's
    answers came from one context, so the pass is not measured — deferred,
    asked again when nothing else holds the model."""
    model = OnDemand(held_round=3)
    with pytest.raises(emb.GateDeferred, match="held the context"):
        _gate(model)


def test_only_a_model_listed_with_the_option_gets_the_pass_and_its_keep_is_zero(monkeypatch):
    recipe = collections_mod.EmbedRecipe(url="http://localai:8080", api="openai",
                                         model="vega-nemotron-3-embed-1b", dtype="f32", ctx=512)
    listed = {"on": False}
    asked: list = []

    def loaded_models(server, **kw):
        asked.append(kw.get("max_age_s"))
        return frozenset({recipe.model})
    monkeypatch.setattr(model_load, "loaded_models", loaded_models)
    monkeypatch.setattr(model_load, "listed_on_demand", lambda url, model: listed["on"])
    assert run_check.fresh_contexts(recipe) is None
    assert asked == [0.0], "read now, never from a cache"

    listed["on"] = True
    fixed: list[int | None] = []

    def embed_calibration(r, texts, **kw):
        assert kw.get("one_at_a_time") is True and len(texts) == 1
        fixed.append(keep._fixed.get())
        return fake_embed(texts)
    monkeypatch.setattr(run_check.embeddings, "embed_calibration", embed_calibration)
    fresh = run_check.fresh_contexts(recipe)
    assert fresh is not None
    out = fresh(["alpha", "beta", "gamma"])
    assert len(out) == 3 and fixed == [0, 0, 0], "each text alone, its keep fixed at 0"
    assert keep._fixed.get() is None, "and nothing fixed once the pass is over"
    assert fresh.creations is None, "no count of the engine's creations is reachable yet: the gate refuses"


class _Stated:
    def __init__(self, options):
        self.engine = {"options": options} if options is not None else {}


def test_the_run_s_own_statement_decides_where_it_lists_its_options(monkeypatch):
    recipe = collections_mod.EmbedRecipe(url="http://localai:8080", api="openai",
                                         model="vega-nemotron-3-embed-1b", dtype="f32", ctx=512)
    monkeypatch.setattr(model_load, "loaded_models", lambda server, **kw: pytest.fail("not asked"))
    assert run_check.creates_per_request(recipe, _Stated({"Options": ["context_on_demand"]})) is True
    assert run_check.creates_per_request(recipe, _Stated({"Options": ["ubatch:512"]})) is False


def test_a_runner_that_cannot_say_defers_the_gate_and_never_skips_the_pass(monkeypatch):
    """The review of 2026-11-12: a `/system` that was slow read as "no option",
    the pass was skipped, and the statement looked the same."""
    recipe = collections_mod.EmbedRecipe(url="http://localai:8080", api="openai",
                                         model="vega-nemotron-3-embed-1b", dtype="f32", ctx=512)
    monkeypatch.setattr(model_load, "loaded_models", lambda server, **kw: None)
    monkeypatch.setattr(model_load, "no_system_route", lambda server: False)
    with pytest.raises(emb.GateDeferred, match="cannot be told"):
        run_check.creates_per_request(recipe, _Stated(None))
    # a runner with no /system at all lists no options, and makes no context per request
    monkeypatch.setattr(model_load, "no_system_route", lambda server: True)
    assert run_check.creates_per_request(recipe, _Stated(None)) is False
    # listed, but not this model: its options cannot be read
    monkeypatch.setattr(model_load, "loaded_models", lambda server, **kw: frozenset({"another"}))
    with pytest.raises(emb.GateDeferred, match="does not list it"):
        run_check.creates_per_request(recipe, _Stated(None))


def test_a_listing_without_load_options_is_no_no(monkeypatch):
    """München, 2026-11-12: the node's LocalAI (v4.12.1, without our P1) lists
    {id, backend, process, size_vram} and no options. Read as "no option",
    the pass would be skipped for a model that creates its context per
    request; it is "cannot say", and the gate defers. A run behind VeGa never
    comes here: its statement lists the options."""
    recipe = collections_mod.EmbedRecipe(url="http://localai:8080", api="openai",
                                         model="vega-nemotron-3-embed-1b", dtype="f32", ctx=512)
    monkeypatch.setattr(model_load, "loaded_models", lambda server, **kw: frozenset({recipe.model}))
    monkeypatch.setattr(model_load, "listed_on_demand", lambda url, model: None)
    # a run noted earlier is not the run being gated
    monkeypatch.setattr(model_load, "context_on_demand", lambda url, model: pytest.fail("not read"))
    with pytest.raises(emb.GateDeferred, match="lists none"):
        run_check.creates_per_request(recipe, _Stated(None))
    assert run_check.creates_per_request(recipe, _Stated({"Options": ["context_on_demand"]})) is True
