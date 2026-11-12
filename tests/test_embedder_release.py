"""What the worker unloads from LocalAI (app/worker._release_embedder,
_sweep_unheld_embedders, model_load.unload).

2026-10-14 (operator): an embedder was held only for a collection that
embeds everything — found when gutenberg's idle 4B embedder held 9.9 GB, the
card crossed LocalAI's eviction threshold and every chat load was evicted
(500s). AMENDED 2026-11-08 (operator: "nothing is evicted only because it's
idle, only if space is claimed"): nothing a held collection names is
unloaded, for its mode or for being idle; a `vega-` model NO held collection
names (a collection removed) still goes, unless a command leases it."""
from contextlib import closing
from dataclasses import replace

import time

import pytest

from app import collections as collections_mod, db, embeddings, model_load, worker
from tests.test_config_tiers_embed_mode import _collection

URL = "http://localai:8080"


def _with(c, model, mode):
    recipe = collections_mod.EmbedRecipe(url=URL, api="vega", model=model, dtype="f16", ctx=512)
    return replace(c, embed=recipe, embed_mode_default=mode)


@pytest.fixture
def runner(monkeypatch):
    model_load.reset()
    held = {"vega-4b", "vega-dar"}
    calls = []

    class R:
        def __init__(self, code): self.status_code = code
    monkeypatch.setattr(model_load, "_fetch_system", lambda server: frozenset(held))
    cpu: dict = {}                               # model -> cpu_percent; absent = idle
    monkeypatch.setattr(model_load, "activity", lambda server: {m: cpu.get(m, 0.0) for m in held})

    def post(url, json=None, timeout=None):
        calls.append((url, json))
        held.discard(json["model"])
        return R(200)
    monkeypatch.setattr(model_load.httpc, "post", post)
    yield held, calls
    model_load.reset()


def test_unload_asks_localai_only_for_a_model_it_holds(runner):
    held, calls = runner
    assert model_load.unload(URL + "/v1", "vega-4b") is True
    assert calls == [(URL + "/backend/shutdown", {"model": "vega-4b"})]
    assert model_load.unload(URL, "vega-4b") is False, "not held any more: nothing asked"
    assert model_load.unload(URL, None) is False and len(calls) == 1


def test_a_release_unloads_nothing_whatever_the_mode(tmp_path, runner):
    """Amended 2026-11-08 (operator: "nothing is evicted only because it's
    idle, only if space is claimed"): until then a collection not on "all"
    had its embedder unloaded here; now nothing is, on any mode."""
    held, calls = runner
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    full = _with(_collection(tmp_path, "normattiva"), "vega-dar", "all")
    off = _with(_collection(tmp_path, "wikipedia"), "vega-bge", "off")
    for c in (lazy, full, off):
        assert worker._release_embedder(c) == []
    assert held == {"vega-4b", "vega-dar"} and calls == []


def test_the_runtime_mode_is_the_one_read(tmp_path, runner):
    held, calls = runner
    c = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    con = db.connect(c)
    scope = db.get_scope(con); scope["embed_mode"] = "all"; db.set_scope(con, scope)
    assert worker._release_embedder(c, con) == [], "the Library set 'all'"
    scope["embed_mode"] = "off"; db.set_scope(con, scope)
    assert worker._release_embedder(c, con, reason="the switch") == [], "no resident pin to give back"
    assert held == {"vega-4b", "vega-dar"} and calls == [], "and nothing unloaded"
    con.close()


def test_a_switch_away_from_all_unloads_nothing(tmp_path, runner, monkeypatch):
    held, calls = runner
    before = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "all")
    con = db.connect(before)
    after = replace(before, embed_mode_default="off")
    monkeypatch.setattr(collections_mod, "load_all", lambda: [after])
    monkeypatch.setattr(worker, "_discover", lambda *a, **k: None)
    worker._sync_collections([before], {before.name: con}, {}, pipeline=False)
    assert calls == [] and "vega-4b" in held
    con.close()


def test_an_embedder_no_held_collection_names_is_swept(tmp_path, runner):
    """deposit-granite removed, vega-granite stayed loaded (2026-10-14): a
    `vega-` model no held collection names, as document or query model, is
    unloaded; chat and the rest are never touched."""
    held, calls = runner
    held |= {"vega-granite", "louise-chat-qwen3-8b"}
    keep = replace(_with(_collection(tmp_path, "normattiva"), "vega-dar", "all"))
    keep = replace(keep, embed=replace(keep.embed, query_model="vega-4b"))
    gone = worker._sweep_unheld_embedders([keep])
    assert gone == ["vega-granite"]
    assert held == {"vega-4b", "vega-dar", "louise-chat-qwen3-8b"}, "the query model and chat stay"


def test_a_removed_collection_s_embedder_leaves_with_it(tmp_path, runner, monkeypatch):
    held, calls = runner
    gone_c = _with(_collection(tmp_path, "deposit"), "vega-4b", "all")
    stays = _with(_collection(tmp_path, "normattiva"), "vega-dar", "all")
    cons = {gone_c.name: db.connect(gone_c), stays.name: db.connect(stays)}
    monkeypatch.setattr(collections_mod, "load_all", lambda: [stays])
    monkeypatch.setattr(worker, "_discover", lambda *a, **k: None)
    worker._sync_collections([gone_c, stays], cons, {}, pipeline=False)
    assert held == {"vega-dar"} and (URL + "/backend/shutdown", {"model": "vega-4b"}) in calls
    cons[stays.name].close()


def test_an_idle_embedder_a_held_collection_names_is_never_unloaded(tmp_path, runner, monkeypatch,
                                                                  isolated_config):
    """Until 2026-11-08 a model named only by collections not on "all" went
    once no process had asked it anything for two settle beats (IDLE_BEATS,
    2026-10-14; the last request as node_state.encoder_use records it,
    Perugia 2026-10-19). Amended (operator: "nothing is evicted only because
    it's idle, only if space is claimed"): it stays, on/off/lazy alike, until
    LocalAI's reclaimer needs its space."""
    from app import node_state
    held, calls = runner
    lazy = _with(_collection(tmp_path, "gutenberg"), "vega-4b", "lazy")
    off = _with(_collection(tmp_path, "wikipedia"), "vega-dar", "off")
    node_state.touch_encoder(URL, "vega-4b", now=time.time() - 86400)
    for _ in range(3):
        assert worker._sweep_unheld_embedders([lazy, off]) == []
    assert held == {"vega-4b", "vega-dar"} and calls == []


def test_the_embeddings_client_records_each_answer_for_the_sweep(isolated_config, monkeypatch):
    """`embeddings._post` writes the (server, model) it just asked, at most
    once per _TOUCH_EVERY_S, into the node's store — from whichever process
    embeds — and never creates the store or fails the request over it."""
    from app import node_state
    recipe = collections_mod.EmbedRecipe(url=URL + "/v1/embeddings", api="vega", model="vega-4b",
                                         dtype="f16", ctx=512)

    class R:
        status_code = 200
    monkeypatch.setattr(embeddings.httpc, "post", lambda url, timeout=None, **kw: R())
    embeddings._touched.clear()
    # no store yet: nothing written, nothing created
    assert not node_state.path().is_file()
    embeddings._post(recipe, recipe.url, timeout=1.0, json={})
    assert not node_state.path().is_file()
    node_state.connect().close()                 # the worker's boot makes the store
    embeddings._touched.clear()
    embeddings._post(recipe, recipe.url, timeout=1.0, json={})
    used = node_state.encoder_last_used()
    assert set(used) == {(URL, "vega-4b")} and time.time() - used[(URL, "vega-4b")] < 5
    # rate-limited: a second answer within _TOUCH_EVERY_S writes nothing new
    monkeypatch.setattr(node_state, "touch_encoder", lambda *a, **k: (_ for _ in ()).throw(AssertionError("twice")))
    embeddings._post(recipe, recipe.url, timeout=1.0, json={})
    # a store that cannot be written never fails the request
    embeddings._touched.clear()
    monkeypatch.setattr(node_state, "touch_encoder", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert embeddings._post(recipe, recipe.url, timeout=1.0, json={}).status_code == 200


# ---- encoder leases (Perugia, 2026-10-19) ---------------------------------------------

def test_a_leased_embedder_is_left_alone_until_the_lease_expires(tmp_path, isolated_config, monkeypatch):
    """`share contribute` embedded with vega-qwen3-embedding-4b at 142
    chunks/min while LocalAI reported its backend at 0–2.5 % CPU, 0.0 one
    sample in four: the sweep stopped it seven times in 3.4 h, 19 s a cold
    start. A process embedding outside the worker leases the model; the
    sweep skips a leased model however idle it reads, and a lease a dead
    process left expires on its own."""
    from app import node_state
    model_load.reset()
    held = {"vega-4b"}
    monkeypatch.setattr(model_load, "_fetch_system", lambda server: frozenset(held))
    monkeypatch.setattr(model_load, "activity", lambda server: {m: 0.0 for m in held})
    calls = []

    class R:
        status_code = 200

        def json(self):
            return {}
    monkeypatch.setattr(model_load.httpc, "post",
                        lambda url, json=None, timeout=None: (calls.append(json), held.discard(json["model"]), R())[-1])
    # a collection this node holds names another model: vega-4b is no held
    # collection's (the contribute embeds for one held elsewhere)
    off = _with(_collection(tmp_path, "gutenberg"), "vega-dar", "off")
    node_state.lease_encoder(URL, "vega-4b", "share contribute pid 1", now=1000.0, ttl_s=600.0)
    real = node_state.leased_encoders
    monkeypatch.setattr(node_state, "leased_encoders", lambda con=None, now=None: real(con, 1300.0))
    # three beats under the lease, no request of its own recorded: nothing goes
    for _ in range(3):
        assert worker._sweep_unheld_embedders([off]) == [] and calls == []
    # the lease ran out (the process died without releasing): nobody asks, unloaded
    monkeypatch.setattr(node_state, "leased_encoders", lambda con=None, now=None: real(con, 1700.0))
    assert worker._sweep_unheld_embedders([off]) == ["vega-4b"] and calls == [{"model": "vega-4b"}]
    model_load.reset()


def test_the_lease_is_renewed_per_batch_and_released_with_the_command(tmp_path, isolated_config, monkeypatch):
    from app import louise, node_state
    recipe = collections_mod.EmbedRecipe(url=URL + "/v1/embeddings", api="vega", model="vega-4b",
                                         dtype="f16", ctx=512)
    seen = []
    embed = louise._leasing(lambda chunks: seen.append(len(chunks)) or [], recipe,
                            holder="share contribute pid 7")
    assert node_state.leased_encoders() == set()
    embed([1, 2, 3])
    assert node_state.leased_encoders() == {(URL, "vega-4b")} and seen == [3]
    embed([4])
    assert node_state.leased_encoders() == {(URL, "vega-4b")}, "renewed, one row"
    with closing(node_state.connect()) as con:
        assert con.execute("SELECT count(*) FROM encoder_leases").fetchone()[0] == 1
    louise._release_lease("share contribute pid 7")
    assert node_state.leased_encoders() == set()
    # a store that cannot be written never stops the embed
    monkeypatch.setattr(node_state, "lease_encoder", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert embed([5]) == [] and seen == [3, 1, 1]


def test_the_lease_queries_walk_the_primary_key(isolated_config):
    """A read a settle beat and a write a batch: plan-checked (CLAUDE.md)."""
    from app import node_state
    with closing(node_state.connect()) as con:
        for sql, args in (("SELECT server, model FROM encoder_leases WHERE until > ?", (0.0,)),
                          ("DELETE FROM encoder_leases WHERE holder=?", ("x",))):
            plan = " ".join(str(r[-1]) for r in con.execute("EXPLAIN QUERY PLAN " + sql, args))
            assert "TEMP B-TREE" not in plan, plan
        plan = " ".join(str(r[-1]) for r in con.execute(
            "EXPLAIN QUERY PLAN INSERT OR REPLACE INTO encoder_leases VALUES (?,?,?,?)", ("s", "m", "h", 1.0)))
        assert "TEMP B-TREE" not in plan


def test_a_command_holds_its_encoder_for_the_whole_block(isolated_config, monkeypatch):
    """München, 2026-11-01: `hold embedder cpu-reference` held no lease, and the
    sweep unloaded its CPU twin every minute under a call of hours. The lease
    is written before the block, renewed beside it by a thread (one call may
    outlive a lease), and released when the block ends — by an error too."""
    from app import encoder_use, node_state
    recipe = collections_mod.EmbedRecipe(url=URL + "/v1/embeddings", api="vega",
                                         model="vega-4b-cpu", dtype="f16", ctx=512)
    renewed = []
    real = node_state.lease_encoder
    monkeypatch.setattr(node_state, "lease_encoder",
                        lambda *a, **k: renewed.append(time.monotonic()) or real(*a, **k))
    with encoder_use.held(recipe, holder="hold embedder cpu-reference pid 9", every_s=0.05):
        assert node_state.leased_encoders() == {(URL, "vega-4b-cpu")}
        time.sleep(0.3)                          # one long call
        assert len(renewed) >= 3, "renewed while the call runs"
    assert node_state.leased_encoders() == set()
    with pytest.raises(RuntimeError):
        with encoder_use.held(recipe, holder="hold embedder cpu-reference pid 9", every_s=0.05):
            raise RuntimeError("the twin failed")
    assert node_state.leased_encoders() == set(), "released however the block ends"


def test_the_block_renews_its_pin_beside_the_call(isolated_config, monkeypatch):
    """München, 2026-11-12: the block pinned once and never stamped its
    holder again; past model_pins.HOLDER_TTL_S under one CPU call the
    worker's reconcile dropped it as dead and the model ran unpinned. The
    renewal beside the call stamps the pin with the lease."""
    from app import encoder_use, model_pins
    recipe = collections_mod.EmbedRecipe(url=URL + "/v1/embeddings", api="vega",
                                         model="vega-4b-cpu", dtype="f16", ctx=512)
    stamps = []
    monkeypatch.setattr(model_pins, "hold",
                        lambda url, model, holder, **k: stamps.append((model, holder)) or True)
    with encoder_use.held(recipe, holder="hold embedder cpu-reference pid 9", every_s=0.05):
        time.sleep(0.3)                          # one long call
    assert len(stamps) >= 3, "the pin stamped while the call runs, not once"
    assert set(stamps) == {("vega-4b-cpu", "hold embedder cpu-reference pid 9")}


def test_two_passes_with_one_pid_hold_two_leases(isolated_config, monkeypatch):
    """München, 2026-11-12: gutenberg's and NeuRIS's `hold embedder
    cpu-reference`, each pid 1 in its own container, held their leases under
    one name; NeuRIS's release took gutenberg's, and the sweep unloaded
    gutenberg's CPU twin under a call of hours. The holder carries the PID
    namespace: one pass's release is its own."""
    from app import encoder_use, model_pins, node_state
    holders = []
    for ns in ("pid:[4026534171]", "pid:[4026534999]"):
        monkeypatch.setattr(model_pins, "_tag", None)
        monkeypatch.setattr(model_pins.os, "readlink", lambda p, ns=ns: ns)
        holders.append(encoder_use.holder("hold embedder cpu-reference"))
    gutenberg, neuris = holders
    assert gutenberg != neuris
    node_state.lease_encoder(URL, "vega-4b-cpu", gutenberg)
    node_state.lease_encoder(URL, "vega-nemotron-cpu", neuris)
    encoder_use.release(neuris)
    assert node_state.leased_encoders() == {(URL, "vega-4b-cpu")}


def test_a_stop_of_a_model_not_loaded_is_already_stopped(monkeypatch):
    """The gate's fresh loads stop the model and wait for its next answer.
    LocalAI answers 404 to a stop of a model it holds no load of (the node,
    2026-11-12): stopped already, the next request loads it afresh — no
    failure. Any other refusal still is one."""
    import httpx
    recipe = collections_mod.EmbedRecipe(url=URL + "/v1/embeddings", api="vega",
                                         model="vega-4b--gate", dtype="f16", ctx=512)
    status = {"code": 404}
    monkeypatch.setattr(embeddings.httpc, "post",
                        lambda url, json=None, timeout=None:
                        httpx.Response(status["code"], request=httpx.Request("POST", url)))
    embeddings.reload_model(recipe)
    status["code"] = 500
    with pytest.raises(RuntimeError, match="refused to stop vega-4b--gate: HTTP 500"):
        embeddings.reload_model(recipe)


def test_the_embedder_commands_take_the_lease():
    import inspect
    from app import louise
    for fn in (louise.cmd_embedder_cpu_reference, louise._embedder_check_runs, louise._gate):
        assert "encoder_use.held(" in inspect.getsource(fn), fn.__name__
