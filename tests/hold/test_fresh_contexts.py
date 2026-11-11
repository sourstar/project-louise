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
    # the count in the signed proof (operator, 2026-11-11: B; 09 §9.12 version 6)
    assert body["version"] == emb.CONTEXTS_VERSION == 6
    assert (body["contexts"], body["context_variation"]) == (300, "1")


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
    assert fresh.creations is None, "not VeGa's encoder: nobody counts its creations, and the gate refuses"


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


# ---- the reading: (calls, creations), the VeGa wrapper's count of the backend's word -------

def test_a_round_is_held_to_its_calls_and_its_creations():
    """The creations alone can add up wrongly: another collection's request
    has a context created during the round while one of the pass's own rides
    a kept one. The calls give it away."""
    texts = [f"text {i}" for i in range(5)]
    first = fake_embed(texts)
    state = {"calls": 0, "created": 0, "rounds": 0}

    def fresh(batch, *, others=0, riders=0):
        state["rounds"] += 1
        state["calls"] += len(batch) + others
        state["created"] += len(batch) - riders + others
        return fake_embed(batch)
    reading = lambda: (state["calls"], state["created"])    # noqa: E731
    assert emb.fresh_contexts(texts, fresh, first, b"seed", creations=12, counted=reading) == (12, 1.0)
    assert state == {"calls": 12, "created": 12, "rounds": 3}

    # one rider, one creation of another's: five contexts made, six calls answered
    with pytest.raises(emb.GateDeferred, match="made 5 context.s. while the run answered 6 call"):
        emb.fresh_contexts(texts, lambda b: fresh(b, others=1, riders=1), first, b"seed",
                           counted=reading)
    # every one of its own rode a context something else holds
    with pytest.raises(emb.GateDeferred, match="made 0 context"):
        emb.fresh_contexts(texts, lambda b: fresh(b, riders=len(b)), first, b"seed", counted=reading)
    # a reading that could not be made, and two readings of different kinds
    with pytest.raises(emb.GateDeferred, match="unknown number"):
        emb.fresh_contexts(texts, fresh, first, b"seed", counted=lambda: None)
    assert emb._round_counts((3, 1), 7, 4) == (None, None)
    assert emb._round_counts(3, 7, 4) == (4, 4), "an int is the creations: the calls are as asked"


class _Keeps:
    """`GET <VEGA_URL>/vega/keeps`, as app/vega/service.py answers it."""

    def __init__(self, run_id: str) -> None:
        self.run_id, self.status, self.down = run_id, 200, False
        self.row: dict | None = {"run_id": run_id, "on_demand": True, "calls": 40, "told": 40,
                                 "created": 7}
        self.urls: list[str] = []

    def get(self, url, timeout=None):
        import httpx
        self.urls.append(url)
        if self.down:
            raise httpx.ConnectError("refused", request=httpx.Request("GET", url))
        other = {"run_id": "sha256:" + "ee" * 32, "calls": 900, "told": 900, "created": 900}
        body = {"buckets": [], "runs": [other] + ([dict(self.row)] if self.row else [])}
        return httpx.Response(self.status, json=body, request=httpx.Request("GET", url))


def _vega_recipe(api: str = "vega"):
    return collections_mod.EmbedRecipe(url="http://localai:8080", api=api,
                                       model="vega-nemotron-3-embed-1b", dtype="f32", ctx=512)


def test_the_count_is_read_from_vega_for_the_run_that_answers(monkeypatch):
    from types import SimpleNamespace
    run_id = "sha256:" + "ab" * 32
    stated = SimpleNamespace(run_id=run_id)
    monkeypatch.setattr(run_check.config, "VEGA_URL", "http://vega:8090")
    vega = _Keeps(run_id)
    waits: list[float] = []

    def probe():                       # one request with a keep of 0: told before its answer
        vega.row["calls"] += 1
        vega.row["told"] += 1
        vega.row["created"] += 1 if len(waits) >= 2 else 0     # a keep of the gate's own, ~1 s
    counted = run_check.creations_of(_vega_recipe(), stated, probe, get=vega.get, sleep=waits.append)
    assert counted() == (43, 8), "three probes: two rode the kept context, the third was created for"
    assert waits == [run_check.SETTLE_WAIT_S] * 2, "waited for the keep to run out, and no longer"
    assert set(vega.urls) == {"http://vega:8090/vega/keeps"}
    vega.row.update(calls=47, told=47, created=11)
    assert counted() == (47, 11) and len(waits) == 2, "settled once: later readings only read"

    # a model something else keeps busy never settles: the reading is given all the same,
    # and the round says what it finds
    busy = _Keeps(run_id)

    def ride():
        busy.row["calls"] += 2
        busy.row["told"] += 2
    waits.clear()
    counted = run_check.creations_of(_vega_recipe(), stated, ride, get=busy.get, sleep=waits.append)
    assert counted() == (40 + 2 * run_check.SETTLE_TRIES, 7)
    assert len(waits) == run_check.SETTLE_TRIES - 1, "bounded"


def test_no_count_to_read_is_no_count_and_a_count_that_never_can_be_is_a_refusal(monkeypatch):
    from types import SimpleNamespace
    run_id = "sha256:" + "ab" * 32
    stated = SimpleNamespace(run_id=run_id)
    nothing = lambda: None             # noqa: E731
    monkeypatch.setattr(run_check.config, "VEGA_URL", "http://vega:8090")
    # not VeGa's encoder, no stated run: the gate refuses such a run (`counted` is None)
    assert run_check.creations_of(_vega_recipe("openai"), stated, nothing) is None
    assert run_check.creations_of(_vega_recipe(), None, nothing) is None
    monkeypatch.setattr(run_check.config, "VEGA_URL", "")
    assert run_check.creations_of(_vega_recipe(), stated, nothing) is None
    monkeypatch.setattr(run_check.config, "VEGA_URL", "http://vega:8090")

    def counted(vega):
        return run_check.creations_of(_vega_recipe(), stated, nothing, get=vega.get,
                                      sleep=lambda s: None)
    # cannot be read NOW: a deferral (None), asked again
    vega = _Keeps(run_id)
    vega.down = True
    assert counted(vega)() is None
    vega = _Keeps(run_id)
    vega.row = None                    # VeGa restarted: its counts are in memory
    assert counted(vega)() is None
    vega = _Keeps(run_id)
    vega.status = 503
    assert counted(vega)() is None
    vega = _Keeps(run_id)
    del vega.row["told"]
    assert counted(vega)() is None
    # can NEVER be read: refused, not retried
    vega = _Keeps(run_id)
    vega.status = 404                  # a VeGa before the count
    with pytest.raises(emb.GateError, match="keeps no count"):
        counted(vega)()
    vega = _Keeps(run_id)
    vega.row["told"] = 0               # a backend that does not say what it created
    with pytest.raises(emb.GateError, match="0 of 40 call"):
        counted(vega)()


def test_the_pass_is_recorded_where_the_operator_can_read_it(tmp_path, monkeypatch, capsys):
    """München, 2026-11-11: "I can't confirm that the 300-fresh-context pass
    ran" — the statement carries no count (09 §9.12 unchanged) and
    `/vega/keeps` starts again at every load. The gate keeps it locally, and
    its report prints it, pass or fail."""
    codex = codex_768(version=5)
    ref = reference()
    (tmp_path / "c").mkdir()
    at = "2026-11-11T15:00:00Z"

    def gate(model, fresh):
        return hold_runs.validate(tmp_path, "c", codex, role="document", reference=ref, embed=lambda t: ref,
                                  embed_one=model.one, contribute=model.one, stated=_vega_run(codex),
                                  key=a_key(), now=at, reload=model.reload,
                                  fresh=Probe(model) if fresh else None)
    body = gate(OnDemand(), fresh=True)
    assert hold_runs.contexts_measured(tmp_path, "c", "document", body["run"]) == \
        {"contexts": 300, "closest": 1.0, "at": at}
    assert body["contexts"] == 300, "and in the signed statement (version 6)"
    assert hold_runs.contexts_measured(tmp_path, "c", "query", body["run"]) is None, "per role"

    bad = gate(OnDemand(bad=9, wrong=lambda v: _nudged(v, 0.9999995)), fresh=True)
    got = hold_runs.contexts_measured(tmp_path, "c", "document", bad["run"])
    assert got["contexts"] == 300 and got["closest"] < 1.0, "a failed pass is recorded too"

    (tmp_path / "d").mkdir()
    plain = hold_runs.validate(tmp_path, "d", codex, role="document", reference=ref, embed=lambda t: ref,
                               embed_one=OnDemand().one, contribute=OnDemand().one, stated=_vega_run(codex),
                               key=a_key(), now=at, reload=OnDemand().reload, fresh=None)
    assert hold_runs.contexts_measured(tmp_path, "d", "document", plain["run"]) is None, \
        "a run without the option is asked nothing and nothing is recorded"


def test_the_gate_report_says_the_count(tmp_path, monkeypatch, capsys):
    import contextlib
    from types import SimpleNamespace
    from app import encoder_use, louise
    model = OnDemand()
    codex, run = _gate(model)
    now = {"run": run, "body": emb.statement(run, codex, a_key(), now="2026-11-11T09:00:00Z")["body"]}

    def validate(root, name, codex_, recipe, *, role, on_run):
        on_run(now["run"])
        return now["body"]
    monkeypatch.setattr(run_check, "reference", lambda *a, **k: None)
    monkeypatch.setattr(run_check, "validate", validate)
    monkeypatch.setattr(louise, "_recipe_for", lambda c, n: SimpleNamespace())
    monkeypatch.setattr(louise, "_as_candidate", lambda r, a: r)
    monkeypatch.setattr(encoder_use, "held", lambda *a, **k: contextlib.nullcontext())
    a = SimpleNamespace(root=tmp_path, query_model=None, model=None)
    louise._embedder_check_runs(a, "c", codex)
    out = capsys.readouterr().out
    assert "  contexts    300 fresh, each counted as created by the engine; closest 1.0000000" in out

    plain = emb.run_gate(codex, reference=reference(), embed=lambda t: reference(), seed=b"\x09" * 32,
                         stated=_vega_run(codex), embed_one=model.one, contribute=model.one,
                         reload=model.reload)
    now.update(run=plain, body=emb.statement(plain, codex, a_key(), now="2026-11-11T09:00:00Z")["body"])
    louise._embedder_check_runs(a, "c", codex)
    assert "  contexts    none asked: the run does not create its context per request" in capsys.readouterr().out



# ---- the count in the signed statement (operator, 2026-11-11: B; 09 §9.12 version 6) ----

ON_DEMAND_OPTIONS = {"Model": "m.gguf", "Options": ["ubatch:512", "context_on_demand"]}


def _on_demand_body(version: int, contexts: int | None) -> dict:
    model = OnDemand()
    codex, run = _gate(model)
    body = dict(emb.statement(run, codex, a_key(), now="2026-11-11T09:00:00Z")["body"])
    body["engine"] = {**body["engine"], "options": ON_DEMAND_OPTIONS}
    body["version"] = version
    if contexts is None:
        body.pop("contexts"), body.pop("context_variation")
    else:
        body["contexts"] = contexts
    return codex, body


def test_a_run_without_fresh_contexts_keeps_its_version_five_statement():
    codex = codex_768(version=5)
    ref = reference()
    run = emb.run_gate(codex, reference=ref, embed=lambda t: ref, seed=b"\x09" * 32,
                       stated=_vega_run(codex), embed_one=OnDemand().one, contribute=OnDemand().one)
    body = emb.statement(run, codex, a_key(), now="2026-11-11T09:00:00Z")["body"]
    assert body["version"] == 5 and "contexts" not in body, "no other statement moves"


def test_the_count_is_part_of_the_verdict():
    codex, body = _on_demand_body(6, 300)
    assert hold_runs.passing(body, codex)
    _, short = _on_demand_body(6, 12)
    assert not hold_runs.passing(short, codex), "an on-demand run counted on 12 contexts is not admitted"


def test_a_version_six_statement_without_its_count_is_refused():
    from app.hold import envelope as env
    _codex, body = _on_demand_body(6, None)
    with pytest.raises(emb.GateError, match="context_variation|contexts"):
        emb.read_statement(env.sign(env.CALIBRATION, body, a_key()))
    _codex, body = _on_demand_body(6, -1)
    with pytest.raises(emb.GateError, match="whole number"):
        emb.read_statement(env.sign(env.CALIBRATION, body, a_key()))
    _codex, body = _on_demand_body(6, 300)
    assert emb.read_statement(env.sign(env.CALIBRATION, body, a_key()))["contexts"] == 300


def test_an_on_demand_run_gated_before_the_count_was_signed_is_gated_again(monkeypatch, tmp_path):
    """Not a failure: the worker's gate runs again (run_check._look) and its
    statement says the count."""
    codex, old = _on_demand_body(5, None)
    run_id = old["run"]
    monkeypatch.setattr(hold_runs, "statements", lambda *a, **k: {run_id: old})
    with pytest.raises(hold_runs.RunUnvalidated) as e:
        hold_runs.check(tmp_path, "c", codex, {run_id}, signer=None)
    assert e.value.failed is False and "fresh contexts" in str(e.value)
    _, new = _on_demand_body(6, 300)
    monkeypatch.setattr(hold_runs, "statements", lambda *a, **k: {run_id: new})
    hold_runs.check(tmp_path, "c", codex, {run_id}, signer=None)



def test_the_report_reads_a_stub_pass_without_the_count(tmp_path, monkeypatch, capsys):
    """München's deploy, 2026-11-11: two CLI tests hand the report a
    SimpleNamespace pass with no `contexts` — the report says none, never
    an AttributeError."""
    import contextlib
    from types import SimpleNamespace
    from app import encoder_use, louise
    codex, run = _gate(OnDemand())
    body = {k: v for k, v in emb.statement(run, codex, a_key(), now="2026-11-11T09:00:00Z")["body"].items()
            if k not in emb.CONTEXTS_KEYS}

    def validate(root, name, codex_, recipe, *, role, on_run):
        on_run(SimpleNamespace(all_cosines=(1.0, 0.999)))
        return body
    monkeypatch.setattr(run_check, "reference", lambda *a, **k: None)
    monkeypatch.setattr(run_check, "validate", validate)
    monkeypatch.setattr(louise, "_recipe_for", lambda c, n: SimpleNamespace())
    monkeypatch.setattr(louise, "_as_candidate", lambda r, a: r)
    monkeypatch.setattr(encoder_use, "held", lambda *a, **k: contextlib.nullcontext())
    louise._embedder_check_runs(SimpleNamespace(root=tmp_path, query_model=None, model=None), "c", codex)
    assert "  contexts    none asked" in capsys.readouterr().out
