"""The gate's park cycles (app/hold/embedder.py `park_cycles`; operator,
2026-11-25: "Do parking."; 2026-11-26: bge-m3 first, the engine's byte check
on; docs/notes/parking-keeper.md, parking-step-2.md §3.3 and §10).

A model whose file says `park: true` moves its weights to host memory to give
its room back, and its next request uploads them again — a path no gate had
seen. For such a model the gate parks it and asks a fingerprint text, 64
times, and holds each answer to the same text's first:
  - 64 cycles, EACH COUNTED BY THE ENGINE (one park more, parked; one return
    more, not parked), every answer the same bytes: the run is what it was,
    and its statement is the statement it would have had — no new field;
  - one answer off by its last bits fails an `exact` run, the reason naming
    host memory; a `near` run takes it within the bound;
  - a model that does not park is asked nothing;
  - NOT A CYCLE, asked again and never counted: a park refused because the
    model works, counts something else moved, a model found parked, a parked
    model Louise itself stopped; past the bound the pass is deferred;
  - counts that STARTED AGAIN between the park and the answer are a return
    that failed and a load that answered in its place — HTTP 200, the same
    vectors: the run is REFUSED as a run that parks, and NO statement is kept
    (its vectors are a fresh load's; a kept FAIL would stop the collection
    even with `park: true` taken out again);
  - a park the runner will never make refuses the run; a host with no room
    above its floor defers it; no counts to read refuses it; a runner that
    cannot say whether the model parks defers it;
  - a model that parks NOW and was gated while it did not is gated again —
    and the cycles count only for the statement of the pass that counted them
    (the review of 2026-11-26: counted alone they admitted a run on another
    pass's statement).
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import httpx
import pytest

from app import (collections as collections_mod, embeddings, gate_twin, httpc, localai_reclaimer,
                 model_evict, model_load, own_unloads, platform_config, resident, run_check)
from app.hold import embedder as emb, runs as hold_runs
from tests.hold.conftest import fake_embed
from tests.hold.test_backend_engine import _vega_run
from tests.hold.test_embedder import a_key, codex_768, perturbed, reference
from tests.hold.test_near_exact import _nudged


class Parking:
    """A model that parks, with the engine's own counts, answering as
    `fake_embed`. Scripted per PARK ATTEMPT (1-based): `refuse` — the park's
    answer instead of a park; `also` — something else parked and woke the
    model too; `restart` — the return failed and a load answered; `bad` — the
    answer after that return is `wrong(vector)`."""

    def __init__(self, *, parks: bool = True, counted: bool = True, refuse: dict | None = None,
                 also: set | None = None, restart: set | None = None, bad: int | None = None, wrong=None,
                 found_parked: bool = False, by_node: bool = False, retries: int = 10,
                 gone: bool = False) -> None:
        self._parks, self.counted = parks, counted
        self.gone = gone                 # the runner holds no load of it until a text is asked
        self.refuse, self.also, self.restart = dict(refuse or {}), set(also or ()), set(restart or ())
        self.bad, self.wrong, self.by_node, self.retries = bad, wrong, by_node, retries
        self.n_parks = self.n_returns = self.attempts = self.idles = self.retried = self.loads = 0
        self.parked = found_parked
        self.prepared = False
        self.asked: list = []

    # the gate's ordinary passes
    def one(self, texts):
        return fake_embed(texts)

    def reload(self) -> None:
        self.loads += 1
        self.n_parks = self.n_returns = 0
        self.parked = False

    # the parker
    def parks(self) -> bool:
        return self._parks

    def counts(self):
        if not self.counted or self.gone:
            return None
        return (self.n_parks, self.n_returns, int(self.parked))

    def prepare(self) -> None:
        self.prepared = True

    def idle(self) -> None:
        self.idles += 1

    def park(self) -> str:
        self.attempts += 1
        if self.attempts in self.refuse:
            if self.refuse[self.attempts] in ("model_stopped", "model_not_loaded"):
                self.gone = True
            return self.refuse[self.attempts]
        self.n_parks += 1
        self.parked = True
        if self.attempts in self.also:
            self.n_parks += 1
            self.n_returns += 1
        return emb.PARKED

    def ask(self, text):
        self.asked.append(text)
        out = fake_embed([text])[0]
        if self.gone:                    # a load afresh: a new process, its counts at zero
            self.gone, self.parked = False, False
            self.n_parks = self.n_returns = 0
        if self.parked:
            self.parked = False
            if self.attempts in self.restart:
                self.n_parks = self.n_returns = 0          # a new process: its counts start again
            else:
                self.n_returns += 1
            if self.attempts == self.bad:
                out = self.wrong(out)
        return out

    def retry(self, tries: int) -> bool:
        self.retried += 1
        return tries <= self.retries

    def stopped_by_node(self) -> bool:
        return self.by_node


def _gate(model: Parking, *, exactness: str = "exact", park: bool = True):
    codex = codex_768(version=5)
    ref = reference()
    return codex, emb.run_gate(
        codex, reference=ref, embed=lambda t: ref, seed=b"\x09" * 32,
        stated=_vega_run(codex), embed_one=model.one, contribute=model.one,
        cpu_reference=("sha256:" + "44" * 32, perturbed(ref, indices=(3,), to=0.9995)),
        reload=model.reload, exactness=exactness, park=model if park else None)


# ---- the pass -----------------------------------------------------------------------

def test_sixty_four_cycles_each_counted_and_the_statement_is_the_one_it_would_have_had():
    model = Parking()
    codex, run = _gate(model)
    assert emb.PARK_CYCLES == 64 == model.attempts == model.idles, "a second of idleness before each park"
    assert model.prepared, "the host floor is set before the first park"
    assert (run.park_cycles, run.park_variation, run.load_variation) == (64, 1.0, 1.0)
    assert run.ok, run.why()
    assert "64 park cycles, each counted by the engine" in run.why()
    assert run.park_s is not None and run.return_s is not None, "what a park and a return cost is said"
    # every fingerprint text four times, and not in the same order twice
    assert len(model.asked) == 64 and len(set(map(str, model.asked))) == 16
    assert model.asked[:16] != model.asked[16:32]
    # NO WIRE CHANGE: the body a run that never parked would have, key for key
    _, plain = _gate(Parking(), park=False)
    body = emb.statement(run, codex, a_key(), now="2026-11-26T09:00:00Z")["body"]
    same = emb.statement(plain, codex, a_key(), now="2026-11-26T09:00:00Z")["body"]
    body.pop("tokens_per_s"), same.pop("tokens_per_s")          # each pass's own clock
    assert body == same and hold_runs.passing(body, codex)


def test_a_model_that_does_not_park_is_asked_nothing():
    model = Parking(parks=False, counted=False)
    _codex, run = _gate(model)
    assert (model.attempts, model.idles, model.asked, model.prepared) == (0, 0, [], False)
    assert run.park_cycles == 0 and run.ok and "park" not in run.why()


def test_one_answer_after_a_return_off_by_its_last_bits_fails_an_exact_run():
    model = Parking(bad=41, wrong=lambda v: _nudged(v, 0.9999995))
    codex, run = _gate(model)
    assert run.park_cycles == 64 and run.park_variation < 1.0
    assert run.load_variation == run.park_variation, "the statement's number covers it"
    assert not run.ok
    why = run.why()
    assert "after its weights came back from host memory" in why and "64 park cycles" in why
    assert "fresh loads" not in why, "the loads were exact: the reason names the parking"
    assert not hold_runs.passing(emb.statement(run, codex, a_key(), now="2026-11-26T09:00:00Z")["body"], codex)


def test_a_near_run_takes_it_within_the_bound_and_no_further():
    within = Parking(bad=7, wrong=lambda v: _nudged(v, 0.999995))
    codex, run = _gate(within, exactness="near")
    assert run.ok and run.exactness == "near" and run.load_variation == run.park_variation < 1.0
    assert "the same bytes" not in run.parks_said() and "the closest answer after a return 0.99999" in run.why()
    assert hold_runs.passing(emb.statement(run, codex, a_key(), now="2026-11-26T09:00:00Z")["body"], codex)
    beyond = Parking(bad=7, wrong=lambda v: _nudged(v, 0.9995))
    _codex, run = _gate(beyond, exactness="near")
    assert not run.ok


def test_a_nan_after_a_return_is_a_wrong_answer_not_a_variation():
    model = Parking(bad=3, wrong=lambda v: [float("nan")] + list(v[1:]))
    _codex, run = _gate(model, exactness="near")
    assert run.park_variation == 0.0 and not run.ok


# ---- not a cycle --------------------------------------------------------------------

def test_a_park_refused_because_the_model_works_is_asked_again_and_not_counted():
    model = Parking(refuse={5: emb.PARK_BUSY, 6: emb.PARK_BUSY})
    _codex, run = _gate(model)
    assert run.park_cycles == 64 and model.attempts == 66 and model.retried == 2
    assert run.ok, run.why()


def test_a_model_busy_past_the_bound_defers_the_gate_and_certifies_nothing():
    model = Parking(refuse={n: emb.PARK_BUSY for n in range(1, 99)}, retries=3)
    with pytest.raises(emb.GateDeferred, match="busy and was not parked"):
        _gate(model)
    assert model.loads == 0, "deferred before the fresh loads were spent on it"


def test_counts_something_else_moved_are_not_a_cycle():
    model = Parking(also={9})
    _codex, run = _gate(model)
    assert run.park_cycles == 64 and model.attempts == 65 and model.retried == 1
    assert run.ok, run.why()


def test_a_model_found_parked_is_brought_back_first_and_that_is_not_a_cycle():
    model = Parking(found_parked=True)
    _codex, run = _gate(model)
    assert run.park_cycles == 64 and model.attempts == 64 and model.retried == 1
    assert len(model.asked) == 65, "one request brought it back, uncounted"
    assert run.ok, run.why()


# ---- a return that failed -----------------------------------------------------------

def test_counts_that_started_again_between_the_park_and_the_answer_refuse_the_run_and_keep_nothing(tmp_path):
    """Tensor's junction run, 2026-11-26: the return failed, LocalAI stopped
    the backend, the next request loaded it — HTTP 200, the same vectors, the
    same run id, valid receipts. Only the engine's counts say it. REFUSED AS
    A RUN THAT PARKS, never a kept FAIL: the vectors were a fresh load's, and
    a failing statement would stop the collection even with `park: true`
    taken out of the file again (the review of 2026-11-26)."""
    model = Parking(restart={12})
    with pytest.raises(emb.GateError, match="STARTED AGAIN") as e:
        _gate(model)
    assert not isinstance(e.value, emb.GateDeferred)
    said = str(e.value)
    assert "(12, 11, 1) then (0, 0, 0)" in said, "the counts as they were read"
    assert "Take `park: true` out of the model's file" in said and "its statement stands" in said
    assert model.loads == 0, "refused before the fresh loads"
    # ...and through `validate`: the run's earlier statement is untouched, no record is written
    codex, before = _validated(tmp_path, "c", Parking(), park=False)
    with pytest.raises(emb.GateError, match="STARTED AGAIN"):
        _validated(tmp_path, "c", Parking(restart={3}))
    kept = emb.read_statement(__import__("json").loads(
        hold_runs.path_for(tmp_path, "c", before["run"], "document").read_text(encoding="utf-8")))
    assert kept == before and hold_runs.passing(kept, codex), "the statement it had stands"
    assert hold_runs.parks_measured(tmp_path, "c", "document", before["run"]) is None


def test_a_parked_model_louise_itself_stopped_is_not_a_failed_return():
    """A question took the card while the gate's model was parked, or another
    gate reloaded the shared load: Louise's own stop, noted where every
    process reads it (app/own_unloads.py)."""
    model = Parking(restart={12}, by_node=True)
    _codex, run = _gate(model)
    assert run.park_cycles == 64 and model.attempts == 65
    assert run.ok, run.why()


def test_a_park_that_broke_its_backend_refuses_the_run_the_same_way():
    model = Parking(refuse={7: "model_stopped"})
    with pytest.raises(emb.GateError, match="the park broke the model's backend") as e:
        _gate(model)
    assert not isinstance(e.value, emb.GateDeferred) and "its statement stands" in str(e.value)


# ---- cycles that cannot be asked ----------------------------------------------------

def test_a_park_the_runner_will_never_make_refuses_the_run():
    model = Parking(refuse={1: "cannot_park"})
    with pytest.raises(emb.GateError, match="GGML_SYCL_PARKABLE=1") as e:
        _gate(model)
    assert not isinstance(e.value, emb.GateDeferred), "a rule in the way, not a wait"


@pytest.mark.parametrize("word", ["host_memory_floor", "parking_off"])
def test_a_host_with_no_room_above_its_floor_defers_it(word):
    with pytest.raises(emb.GateDeferred, match="nothing is certified"):
        _gate(Parking(refuse={4: word}))


def test_a_model_the_runner_no_longer_holds_is_asked_a_text_and_the_pass_goes_on():
    """The review of 2026-11-26: the pass read "no counts", waited eleven
    times for a model nobody loaded, and deferred with the server's word
    lost. Not a cycle — and one text puts the model back."""
    model = Parking(refuse={5: "model_not_loaded"})
    _codex, run = _gate(model)
    assert run.park_cycles == 64 and model.attempts == 65 and model.retried == 1
    assert len(model.asked) == 65, "the one text that loaded it again, uncounted"
    assert run.ok, run.why()


def test_a_runner_that_keeps_refusing_defers_with_its_own_word():
    model = Parking(refuse={n: "HTTP 502" for n in range(1, 99)}, retries=2)
    with pytest.raises(emb.GateDeferred, match=r"did not park the model \(HTTP 502\)"):
        _gate(model)


def test_cycles_nobody_counted_are_never_assumed():
    model = Parking(counted=False)
    with pytest.raises(emb.GateError, match="nobody counted") as e:
        _gate(model)
    assert not isinstance(e.value, emb.GateDeferred)
    assert model.attempts == 0 and model.retried >= 1, "read again before it is refused, and nothing parked"


# ---- kept, and required ---------------------------------------------------------------

def _validated(tmp_path, name: str, model: Parking, *, park: bool = True, at: str = "2026-11-26T05:00:00Z"):
    codex = codex_768(version=5)
    ref = reference()
    (tmp_path / name).mkdir(exist_ok=True)
    body = hold_runs.validate(tmp_path, name, codex, role="document", reference=ref, embed=lambda t: ref,
                              embed_one=model.one, contribute=model.one, stated=_vega_run(codex),
                              key=a_key(), now=at, reload=model.reload, park=model if park else None)
    return codex, body


def test_the_cycles_are_recorded_for_the_statement_of_their_own_pass(tmp_path):
    codex, body = _validated(tmp_path, "c", Parking())
    got = hold_runs.parks_measured(tmp_path, "c", "document", body["run"])
    assert (got["cycles"], got["closest"], got["at"]) == (64, 1.0, "2026-11-26T05:00:00Z")
    assert got["statement"] == hold_runs.statement_digest(body)
    assert got["park_s"] is not None and got["return_s"] is not None
    assert hold_runs.parks_measured(tmp_path, "c", "query", body["run"]) is None, "per role"
    _codex, plain = _validated(tmp_path, "d", Parking(parks=False))
    assert hold_runs.parks_measured(tmp_path, "d", "document", plain["run"]) is None


def _holds(monkeypatch, held: dict):
    """`statements`: name -> {run: body}, as the collections' files would give."""
    monkeypatch.setattr(hold_runs, "statements", lambda root, name, *a, **k: dict(held.get(name, {})))
    monkeypatch.setattr(hold_runs, "_others", lambda root, name: sorted(n for n in held if n != name))


def test_a_model_that_parks_now_and_was_gated_while_it_did_not_is_gated_again(tmp_path, monkeypatch):
    """Its file gained `park: true`, or the runner its patch, under the same
    run id: the statement is good and never saw a return."""
    codex, body = _validated(tmp_path, "c", Parking(), park=False)
    run_id = body["run"]
    _holds(monkeypatch, {"c": {run_id: body}})
    hold_runs.check(tmp_path, "c", codex, {run_id}, signer=None, batched=False)
    with pytest.raises(hold_runs.RunUnvalidated) as e:
        hold_runs.check(tmp_path, "c", codex, {run_id}, signer=None, batched=False, parks=True)
    assert e.value.failed is False and "counted no park cycles" in str(e.value)
    _codex, again = _validated(tmp_path, "c", Parking())
    _holds(monkeypatch, {"c": {run_id: again}})
    hold_runs.check(tmp_path, "c", codex, {run_id}, signer=None, batched=False, parks=True)


def test_cycles_counted_for_one_statement_admit_no_other(tmp_path, monkeypatch):
    """The review of 2026-11-26 (R1): a pass whose answers after a return
    differ keeps a FAILING statement and 64 counted cycles. Parking off, the
    run is measured again and passes; parking on again, the old count must
    not admit that statement — it never saw a return."""
    codex, failed = _validated(tmp_path, "c", Parking(bad=9, wrong=lambda v: _nudged(v, 0.9999995)))
    run_id = failed["run"]
    assert not hold_runs.passing(failed, codex)
    assert hold_runs.parks_measured(tmp_path, "c", "document", run_id)["cycles"] == 64
    _codex, passing = _validated(tmp_path, "d", Parking(), park=False)          # the same run, measured again
    assert passing["run"] == run_id and hold_runs.passing(passing, codex)
    _holds(monkeypatch, {"c": {run_id: passing}})
    with pytest.raises(hold_runs.RunUnvalidated, match="counted no park cycles"):
        hold_runs.check(tmp_path, "c", codex, {run_id}, signer=None, batched=False, parks=True)


def test_a_sibling_lends_its_cycles_only_with_the_statement_they_were_counted_for(tmp_path, monkeypatch):
    """The review (R9): A and B share a space, both validated before parking.
    A is gated again and FAILS on a return; B, on its own older passing
    statement, must not be admitted by A's 64 counted cycles."""
    codex, b_old = _validated(tmp_path, "b", Parking(), park=False)
    run_id = b_old["run"]
    _codex, a_failed = _validated(tmp_path, "a", Parking(bad=30, wrong=lambda v: _nudged(v, 0.9999995)))
    assert a_failed["run"] == run_id and not hold_runs.passing(a_failed, codex)
    _holds(monkeypatch, {"a": {run_id: a_failed}, "b": {run_id: b_old}})
    with pytest.raises(hold_runs.RunUnvalidated, match="counted no park cycles"):
        hold_runs.check(tmp_path, "b", codex, {run_id}, signer=None, batched=False, parks=True)
    assert hold_runs.parks_measured(tmp_path, "b", "document", run_id) is None, "nothing was copied"
    # the SAME statement, held by both (as `statements_for_space` copies it), lends its cycles
    _codex, a_good = _validated(tmp_path, "a", Parking())
    _holds(monkeypatch, {"a": {run_id: a_good}, "b": {run_id: a_good}})
    hold_runs.check(tmp_path, "b", codex, {run_id}, signer=None, batched=False, parks=True)
    assert hold_runs.parks_measured(tmp_path, "b", "document", run_id)["cycles"] == 64, "one borrowed is one kept"
    # a record whose sibling no longer holds that statement lends nothing
    (tmp_path / "e").mkdir()
    _holds(monkeypatch, {"a": {run_id: a_failed}, "e": {run_id: a_good}})
    with pytest.raises(hold_runs.RunUnvalidated):
        hold_runs.check(tmp_path, "e", codex, {run_id}, signer=None, batched=False, parks=True)


def test_eight_threads_borrowing_at_once_write_one_whole_record(tmp_path, monkeypatch):
    """`check` runs on the embed stage's threads: one temporary name per pid
    gave `FileNotFoundError` on the first round (the review, R10) — an error
    on a document, not a deferral."""
    codex, body = _validated(tmp_path, "a", Parking())
    run_id = body["run"]
    (tmp_path / "b").mkdir()
    _holds(monkeypatch, {"a": {run_id: body}, "b": {run_id: body}})
    errors: list = []
    start = threading.Barrier(8)

    def one() -> None:
        try:
            start.wait(5)
            hold_runs.check(tmp_path, "b", codex, {run_id}, signer=None, batched=False, parks=True)
        except Exception as e:   # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=one) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == []
    assert hold_runs.parks_measured(tmp_path, "b", "document", run_id)["cycles"] == 64
    assert not list((tmp_path / "b").rglob("*.tmp")), "no temporary file left behind"


# ---- the node's side: LocalAI ---------------------------------------------------------

S = "http://localai:8080"
M = "vega-bge-m3"
REAL_FETCH = model_load._fetch_system        # the suite's conftest answers `/system` with "nothing known"


SECRET = "sk-never-in-a-log-or-an-error"


class LocalAI:
    def __init__(self, *, parks: bool = True, floor: int = 0) -> None:
        self.parks, self.parked, self.floor = parks, False, floor
        self.n_parks = self.n_returns = 0
        self.answer: tuple[int, str] | None = None
        self.gets: list[str] = []
        self.monitor_ok = True
        self.system: str = "ok"          # ok | down | empty | no-route
        self.settings_post = 200
        self.restart_on_return = False   # the next return fails and a load answers in its place

    def get(self, url, **kw):
        self.gets.append(url)
        if url.endswith("/system"):
            if self.system == "down":
                raise httpx.ConnectError("refused")
            if self.system == "no-route":
                return SimpleNamespace(status_code=404, json=lambda: {})
            row = {"id": M, "size_vram": 1_315 << 20, "process": {"pid": 11, "cpu_percent": 0.0}}
            if self.parks:
                row.update(parks=True, state="parked" if self.parked else "loaded")
                if self.parked:
                    row["parked"] = {"size_host": 577 << 20, "size_vram_needed": 996 << 20, "since": "x"}
            rows = [] if self.system == "empty" else [row]
            return SimpleNamespace(status_code=200, json=lambda: {"loaded_models": rows})
        if url.endswith("/backend/monitor"):
            assert kw["params"] == {"model": M}
            if not self.monitor_ok:
                return SimpleNamespace(status_code=500, json=lambda: {})
            return SimpleNamespace(status_code=200, json=lambda: {"memory": {
                "total": 38_850_000_000,
                "breakdown": {"parks": self.n_parks, "unparks": self.n_returns, "no_room": 0,
                              "parked": int(self.parked), "park_size": 996 << 20, "rss": 1}}})
        if url.endswith("/api/settings"):
            return SimpleNamespace(status_code=200, json=lambda: {"park_host_floor_bytes": self.floor,
                                                                  "api_keys": [SECRET], "p2p_token": SECRET})
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, json=None, **kw):
        if url.endswith("/api/settings"):
            if self.settings_post != 200:
                return SimpleNamespace(status_code=self.settings_post, text=SECRET,
                                       json=lambda: {"error": SECRET})
            self.floor = json["park_host_floor_bytes"]
            return SimpleNamespace(status_code=200, json=lambda: {"success": True})
        if url.endswith("/backend/shutdown"):
            self.parked, self.n_parks, self.n_returns = False, 0, 0
            return SimpleNamespace(status_code=200)
        assert url.endswith("/backend/park") and json == {"model": M}
        if self.answer is not None:
            status, kind = self.answer
            return SimpleNamespace(status_code=status, headers={},
                                   json=lambda: {"error": {"code": status, "type": kind, "message": kind}})
        self.n_parks += 1
        self.parked = True
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {
            "message": "model parked", "parked": {"size_host": 577 << 20, "size_vram_needed": 996 << 20}})

    def embed(self, texts, runs):
        """What an embedding request does to the model: a parked one comes
        back (or, scripted, its return fails and a load answers)."""
        if self.parked:
            self.parked = False
            if self.restart_on_return:
                self.n_parks = self.n_returns = 0
            else:
                self.n_returns += 1
        runs.add(RUN)
        return fake_embed(texts)


RUN = "sha256:" + "ab" * 32


@pytest.fixture
def localai(monkeypatch, tmp_path, isolated_config):
    def make(**kw) -> LocalAI:
        r = LocalAI(**kw)
        monkeypatch.setattr(model_load, "_fetch_system", REAL_FETCH)
        monkeypatch.setattr(httpc, "get", r.get)
        monkeypatch.setattr(httpc, "post", r.post)
        return r
    monkeypatch.setattr(own_unloads, "_path", lambda: tmp_path / "state" / "model-unloads.json")
    monkeypatch.setattr(platform_config, "load", lambda *a, **k: SimpleNamespace(
        models=SimpleNamespace(page_floor_gib=make.floor_gib)))
    make.floor_gib = 8
    model_load.reset()
    resident.reset()
    yield make
    model_load.reset()
    resident.reset()


def _recipe():
    return collections_mod.EmbedRecipe(api="vega", url=S, model=M, dtype="f32", ctx=512, dim=768)


def test_the_engines_counts_are_read_from_the_monitor_and_never_from_its_virtual_size(localai):
    r = localai()
    assert model_load.park_counts(S, M) == (0, 0, 0)
    r.n_parks, r.n_returns, r.parked = 7, 6, True
    assert model_load.park_counts(f"{S}/v1/embeddings", M) == (7, 6, 1)
    r.monitor_ok = False
    assert model_load.park_counts(S, M) is None, "unread is unread, never zeros"
    assert model_load.park_counts(S, None) is None


@pytest.mark.parametrize("status, kind, how", [
    (409, "model_busy", model_load.PARK_BUSY), (412, "cannot_park", model_load.PARK_STOP),
    (412, "host_memory_floor", model_load.PARK_STOP), (404, "model_not_loaded", model_load.PARK_GONE),
    (500, "model_stopped", model_load.PARK_GONE)])
def test_a_refused_park_says_the_runners_own_word(localai, status, kind, how):
    r = localai()
    r.answer = (status, kind)
    assert model_load.park_asked(S, M) == (how, 0, kind)
    assert model_load.park(S, M) == (how, 0), "the evictor's call is what it was"


def test_the_parker_asks_the_runner_whether_the_model_parks_and_sets_the_floor_itself(localai):
    r = localai()
    parker = run_check.Parker(_recipe(), name="c", sleep=lambda s: None)
    assert parker.parks() and run_check.parks_now(_recipe())
    assert parker.counts() == (0, 0, 0)
    parker.prepare()
    assert r.floor == 8 << 30, "the keeper's one value, written by the gate where no keeper beats"
    assert parker.park() == emb.PARKED and parker.counts() == (1, 0, 1)
    r.answer = (409, "model_busy")
    assert parker.park() == emb.PARK_BUSY
    r.answer = (412, "cannot_park")
    assert parker.park() == emb.PARK_NEVER
    r.answer = (412, "host_memory_floor")
    assert parker.park() in emb.PARK_LATER


def test_a_runner_without_the_patch_parks_nothing_and_the_gate_asks_nothing(localai):
    localai(parks=False)
    parker = run_check.Parker(_recipe(), name="c", sleep=lambda s: None)
    assert not parker.parks() and not run_check.parks_now(_recipe())


def test_a_node_with_no_host_floor_cannot_ask_the_cycles_and_says_so(localai):
    localai()
    localai.floor_gib = 0
    with pytest.raises(emb.GateError, match="page_floor_gib is 0") as e:
        run_check.Parker(_recipe(), name="c").prepare()
    assert not isinstance(e.value, emb.GateDeferred)


def test_a_platform_file_that_cannot_be_read_is_not_a_node_without_a_floor(localai, monkeypatch):
    """The review: `model_evict.rule()` answers 0 for a file that does not
    load, and the gate refused the run for a floor nobody had set to 0."""
    localai()
    monkeypatch.setattr(platform_config, "load", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad toml")))
    with pytest.raises(emb.GateDeferred, match="platform file could not be read"):
        run_check.Parker(_recipe(), name="c").prepare()


def test_a_floor_that_cannot_be_set_defers_the_gate_and_says_nothing_of_the_settings(localai):
    """Invariant 8: LocalAI's settings carry its API keys and the p2p token."""
    r = localai()
    r.settings_post = 500
    with pytest.raises(emb.GateDeferred, match="park floor could not be set") as e:
        run_check.Parker(_recipe(), name="c").prepare()
    assert SECRET not in str(e.value) and SECRET not in repr(e.value.__cause__)
    # ...and where the settings did not answer the keeper a moment ago, the gate does not ask them again
    resident._hold_settings(S, "LocalAI's park floor", localai_reclaimer.NotAnswered("no answer"))
    before = len(r.gets)
    with pytest.raises(emb.GateDeferred, match="not being written for now"):
        run_check.Parker(_recipe(), name="c").prepare()
    assert len(r.gets) == before


@pytest.mark.parametrize("state, why", [("down", "did not answer"), ("empty", "does not list vega-bge-m3")])
def test_a_runner_that_cannot_say_whether_the_model_parks_defers_the_gate(localai, state, why):
    """The review: silence read as "does not park" skipped the pass, and a
    parking run was certified on no cycle at all."""
    r = localai()
    r.system = state
    with pytest.raises(emb.GateDeferred, match=why):
        run_check.Parker(_recipe(), name="c").parks()


def test_a_runner_that_is_not_localai_parks_nothing(localai):
    r = localai()
    r.system = "no-route"
    assert run_check.Parker(_recipe(), name="c").parks() is False
    assert run_check.parks_now(_recipe()) is False


def test_a_model_a_warm_has_just_loaded_is_read_before_it_is_called_not_parking(localai):
    """The review: the cached `/system` answer lists a model a warm loaded and
    says nothing of it; a check passed on that silence and the next refused."""
    r = localai()
    r.system = "empty"
    assert model_load.loaded_models(S, 0.0) == frozenset()       # read before the model loads
    r.system = "ok"
    model_load._note_loaded(S, M)                                 # a warm of ours loaded it
    assert M not in model_load.parks(S) and not model_load.row_read(S, M)
    reads = r.gets.count(f"{S}/system")
    assert run_check.parks_now(_recipe()) is True, "asked once, now"
    assert r.gets.count(f"{S}/system") == reads + 1 and model_load.row_read(S, M)
    assert run_check.parks_now(_recipe()) is True and r.gets.count(f"{S}/system") == reads + 1, "then from the answer"


# ---- the real parker drives the real pass against the runner ---------------------------

def _driven(r: LocalAI, **kw):
    one = hold_runs.Answers(r.embed)
    parker = run_check.Parker(_recipe(), one, name="c", sleep=lambda s: None, **kw)
    texts = [f"testo {i}" for i in range(16)]
    return parker, texts, fake_embed(texts)


def test_the_gates_own_parker_counts_sixty_four_cycles_at_the_runner(localai):
    r = localai()
    parker, texts, first = _driven(r)
    done, closest, park_s, return_s = emb.park_cycles(texts, parker.ask, first, b"\x01" * 32, parker)
    assert (done, closest) == (64, 1.0) and park_s is not None and return_s is not None
    assert (r.n_parks, r.n_returns, r.parked) == (64, 64, False), "the model is left on the card"
    assert r.floor == 8 << 30 and parker.runs == {RUN}


def test_a_return_that_fails_at_the_runner_refuses_the_run(localai):
    r = localai()
    parker, texts, first = _driven(r)
    r.restart_on_return = True
    with pytest.raises(emb.GateError, match="STARTED AGAIN"):
        emb.park_cycles(texts, parker.ask, first, b"\x01" * 32, parker)


def test_another_gates_reload_of_the_shared_load_is_the_nodes_own_stop(localai):
    """The review: `embeddings.reload_model` and `gate_twin.release` stopped a
    model without a note, so a gate that found its parked model restarted by
    one of them would have called it a failed return."""
    r = localai()
    parker, texts, first = _driven(r)
    assert parker.park() == emb.PARKED and not parker.stopped_by_node()
    embeddings.reload_model(_recipe())
    assert parker.stopped_by_node(), "the gate's reload is noted where every process reads it"
    assert own_unloads.since((S, M), 60) is not None
    gate_twin.release(S, f"{M}--gate")
    assert own_unloads.since((S, f"{M}--gate"), 60) is not None


def test_a_nan_is_handed_to_the_gate_as_the_engine_gave_it(monkeypatch):
    """The client asks a null or NaN vector again (its first self-healing):
    the first answer after a return would be retried away, and the second
    compared (the review). Inside `unhealed` a raw answer is not retried."""
    calls = []

    def post(recipe, url, **kw):
        calls.append(url)
        bad = len(calls) == 1
        return SimpleNamespace(status_code=200, headers={}, raise_for_status=lambda: None,
                               json=lambda: {"data": [{"index": 0, "embedding": [float("nan") if bad else 0.5, 0.5]}]})
    monkeypatch.setattr(embeddings, "_post", post)
    monkeypatch.setattr(embeddings, "_warm_first", lambda *a, **k: None)
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)
    recipe = collections_mod.EmbedRecipe(api="openai", url=f"{S}/v1/embeddings", model=M, dtype="f32", ctx=512)
    with embeddings.unhealed():
        got = embeddings._embed_batch(["uno"], 30, recipe, raw=True)
    assert got[0][0] != got[0][0] and len(calls) == 1, "the NaN itself, asked once"
    healed = embeddings._embed_batch(["uno"], 30, recipe, raw=True)
    assert healed == [[0.5, 0.5]] and not embeddings._unhealed.get(), "outside it the client heals as before"




def test_only_a_stop_noted_after_the_gates_own_park_is_the_nodes(localai):
    localai()
    parker = run_check.Parker(_recipe(), name="c", sleep=lambda s: None)
    assert parker.park() == emb.PARKED            # its own park is noted too (`model_load._note_own`)
    assert not parker.stopped_by_node(), "the gate's own park is not somebody else's stop"
    own_unloads.note(S, M, "a question took the card")
    assert parker.stopped_by_node()


def test_the_request_itself_brings_a_parked_model_back_for_the_gate(localai, monkeypatch):
    """`ensure_loaded` warms a parked model with a request whose vector nobody
    reads; the gate's answer must be the FIRST computed on the returned
    weights, so inside `returns_on_request` room is made and nothing warms."""
    r = localai()
    r.parked = True
    roomed, warmed = [], []
    monkeypatch.setattr(model_load, "_make_room", lambda server, model: roomed.append(model))
    monkeypatch.setattr(model_load, "_record", lambda c: None)
    ended = []
    monkeypatch.setattr(model_evict, "load_ended", lambda server, model, ok: ended.append((model, ok)))
    with model_load.returns_on_request():
        assert model_load.ensure_loaded(S, M, lambda bound: warmed.append(bound), caller="gate") is None
    assert (roomed, warmed) == ([M], []), "room as for its load, and no warm"
    assert ended == [(M, True)], "a room made by paging is not kept for a load nobody ends"
    model_load.ensure_loaded(S, M, lambda bound: warmed.append(bound) or SimpleNamespace(status_code=200),
                             caller="embed")
    assert len(warmed) == 1, "outside it a parked model is warmed as before"


def test_the_parkers_ask_goes_unwarmed_and_remembers_the_runs_that_answered():
    seen = []

    def fn(texts, runs):
        seen.append(model_load._return_on_request.get())
        runs.add("sha256:run-a" if len(seen) == 1 else "sha256:run-b")
        return fake_embed(texts)
    parker = run_check.Parker(_recipe(), hold_runs.Answers(fn), name="c")
    assert parker.runs is None
    parker.ask("uno")
    parker.ask("due")
    assert seen == [True, True] and not model_load._return_on_request.get()
    assert not embeddings._unhealed.get(), "and unhealed only while it asks"
    assert parker.runs == {"sha256:run-a", "sha256:run-b"}, "every answer's run, not the last call's"


def test_a_pass_answered_by_another_run_is_inconclusive(tmp_path):
    """`runs.validate` holds every pass to the stated run; the park cycles
    are one of them."""
    model = Parking()
    model.runs = {"sha256:" + "ee" * 32}
    with pytest.raises(emb.GateDeferred, match="inconclusive"):
        _validated(tmp_path, "c", model)


def test_a_query_model_that_parks_now_is_gated_again_for_its_cycles(monkeypatch, tmp_path):
    """Nobody writes a query's vector, so `runs.check` never sees the query
    side — the look does (`_look_at_the_query_side`): a query embedded on
    weights that came back wrong searches a wrong neighbourhood all the same."""
    codex = SimpleNamespace(space=SimpleNamespace(query=SimpleNamespace(encoder=object())))
    collection = SimpleNamespace(name="c", codex=codex)
    body = {"run": RUN, "passed": 28, "median": "0.9999", "batch_invariant": True}
    state = {"parks": False, "counted": False}
    rechecked, gated, said = [], [], []
    monkeypatch.setattr(run_check.collections_mod, "recipe_for", lambda c: _recipe())
    monkeypatch.setattr(run_check, "answering_run", lambda recipe, query=False: RUN)
    monkeypatch.setattr(run_check, "heard", lambda *a, **k: None)
    monkeypatch.setattr(run_check, "_suspect", lambda *a, **k: False)
    monkeypatch.setattr(run_check, "_root", lambda: tmp_path)
    monkeypatch.setattr(run_check, "signer", lambda *a: None)
    monkeypatch.setattr(run_check.runs_mod, "statements_for_space", lambda *a, **k: {RUN: body})
    monkeypatch.setattr(run_check, "recheck", lambda *a, **k: rechecked.append(k.get("role")) or True)
    monkeypatch.setattr(run_check, "validate", lambda *a, **k: gated.append(k.get("role")) or body)
    monkeypatch.setattr(run_check, "parks_now", lambda recipe, query=False: query and state["parks"])
    monkeypatch.setattr(run_check.runs_mod, "parks_counted", lambda *a, **k: state["counted"])

    run_check._look_at_the_query_side(collection, None, said.append)
    assert (rechecked, gated) == (["query"], []), "a model that does not park: its statement, re-checked"
    state["parks"] = True
    run_check._look_at_the_query_side(collection, None, said.append)
    assert gated == ["query"] and len(rechecked) == 1, "parks now, no cycles counted for this statement: the gate"
    state["counted"] = True
    run_check._look_at_the_query_side(collection, None, said.append)
    assert gated == ["query"] and len(rechecked) == 2, "counted: its statement stands"


# ---- watched: what a park costs -------------------------------------------------------

def test_the_evictors_line_says_how_long_the_park_took(localai):
    localai()
    v = model_evict.Loaded(M, 1_315 << 20, 0.0, parks=True, parked=False)
    assert model_evict.park_took(S, M) is None and model_evict._in_s(S, M) == ""
    assert model_evict.give_back(S, v)[0] == model_evict.PARKED
    assert model_evict.park_took(S, M) is not None
    assert model_evict._in_s(S, M).startswith(" in ") and model_evict._in_s(S, M).endswith(" s")


def test_the_gate_report_says_the_cycles(tmp_path, monkeypatch, capsys):
    import contextlib
    from app import encoder_use, louise
    codex, run = _gate(Parking())
    now = {"run": run, "body": emb.statement(run, codex, a_key(), now="2026-11-26T09:00:00Z")["body"]}

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
    assert "  parking     64 park cycles, each counted by the engine; closest 1.0000000; a park " in out
    assert " s, a return " in out and "at the median" in out, "what the byte check costs is read here"

    _, plain = _gate(Parking(parks=False))
    now.update(run=plain, body=emb.statement(plain, codex, a_key(), now="2026-11-26T09:00:00Z")["body"])
    louise._embedder_check_runs(a, "c", codex)
    assert "  parking     none asked: the model did not park when it was gated" in capsys.readouterr().out


def test_the_log_says_why_a_run_failed_and_what_the_cycles_cost(caplog):
    """The worker's line is the statement's sampled cosines, which a failed
    return leaves all good: the reason is said beside it."""
    import logging
    passed: list = []
    _, good = _gate(Parking())
    _, failed = _gate(Parking(bad=3, wrong=lambda v: _nudged(v, 0.9999995)))
    with caplog.at_level(logging.INFO, logger="app.run_check"):
        run_check._said("c", "document", "sha256:" + "ab" * 32, passed.append)(good)
        run_check._said("c", "document", "sha256:" + "ab" * 32, None)(failed)
    text = caplog.text
    assert "64 park cycles, each counted by the engine" in text and "a return " in text
    assert "the gate FAILED document run" in text and "after its weights came back from host memory" in text
    assert passed == [good], "the caller's own hook still gets the pass"
