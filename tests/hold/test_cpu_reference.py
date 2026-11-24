"""The stored CPU reference (operator, 2026-10-31; docs/notes/near-exact-runs.md
§2): the CPU twin's answers to the 512 calibration items, made once and held
against by every gate."""
from __future__ import annotations

import dataclasses

import json

import pytest

from app.hold import calibration as cal
from app.hold import cpu_reference as cpu_ref
from app.hold import embedder as emb
from app.hold.codex import CodexError
from app.rage import reference as rage
from tests.hold.conftest import a_stated_run, fake_embed
from tests.hold.test_embedder import codex_768, perturbed, reference

CPU_RUN = {"driver": "cpu", "engine": {"name": "localai/llama-cpp-cpu"}, "weights": "sha256:" + "aa" * 32}


def _space(ctx=1024, query=None, set_id="s"):
    """A Codex as RAGE's key reads it (app/rage/reference.py): weights w, set
    `set_id`, the window `ctx`, no instruction, the characters cut."""
    from types import SimpleNamespace
    return SimpleNamespace(space=SimpleNamespace(
        encoder=SimpleNamespace(weights="w"), ctx=ctx, cut=None, document_instruction=None,
        query=SimpleNamespace(encoder=SimpleNamespace(weights=query) if query else None,
                              instruction=None),
        calibration=SimpleNamespace(set=set_id)))


def test_a_reference_is_kept_whole_read_back_and_found_by_its_weights_and_set(tmp_path):
    codex = codex_768()
    w = codex.space.encoder.weights
    run = dict(CPU_RUN, weights=w)
    vectors = reference()
    path = rage.put(codex, "document", run_id="sha256:" + "11" * 32, run=run, vectors=vectors)
    got = cpu_ref.read(path)
    assert got.run_id == "sha256:" + "11" * 32 and got.weights == w
    assert got.vectors[0] == pytest.approx(vectors[0], abs=0) and len(got.vectors) == cal.SIZE
    assert rage.resolve(codex, "document", w, cal.SIZE).path == path
    assert rage.resolve(codex, "document", "sha256:" + "bb" * 32, cal.SIZE) is None, \
        "other weights: another function"
    other_set = dataclasses.replace(codex, space=dataclasses.replace(
        codex.space, calibration=dataclasses.replace(codex.space.calibration, set="sha256:other")))
    assert rage.resolve(other_set, "document", w, cal.SIZE) is None
    assert rage.resolve(codex, "query", w, cal.SIZE) is None, "the query role is its own key"
    assert rage.resolve(codex, "document", w, cal.SIZE - 1) is None, "not the set's count"


def test_a_card_run_is_not_a_cpu_reference_and_a_tampered_file_is_not_read(tmp_path):
    codex = _space()
    with pytest.raises(CodexError, match="not the CPU"):
        rage.put(codex, "document", run_id="sha256:" + "22" * 32,
                 run=dict(CPU_RUN, driver="xe 1.3 / level-zero 1.21", weights="w"),
                 vectors=reference())
    path = rage.put(codex, "document", run_id="sha256:" + "33" * 32,
                    run=dict(CPU_RUN, weights="w"), vectors=reference())
    import json
    doc = json.loads(path.read_text())
    doc["sha256"] = "00" * 32
    path.write_text(json.dumps(doc))
    with pytest.raises(CodexError, match="digest"):
        cpu_ref.read(path)
    assert rage.resolve(codex, "document", "w", cal.SIZE) is None, \
        "an unreadable file is passed over, never used"


def _gate(codex, got, cpu_vectors):
    ref = reference()
    return emb.run_gate(codex, reference=ref, embed=lambda texts: got, seed=b"\x09" * 32,
                        stated=a_stated_run(codex), embed_one=fake_embed, contribute=fake_embed,
                        cpu_reference=("sha256:" + "44" * 32, cpu_vectors))


def test_the_gate_holds_every_answer_to_the_stored_cpu_reference():
    """At or above the review line (0.995, operator 2026-10-18) the run passes
    with its numbers kept; below it on ANY of the 512 it fails, whatever the
    sampled sixteen say."""
    codex = codex_768()
    ref = reference()
    close = perturbed(ref, indices=(5,), to=0.9991)
    run = _gate(codex, close, ref)
    assert run.ok and run.cpu_run == "sha256:" + "44" * 32
    assert run.cpu_worst == pytest.approx(0.9991, abs=1e-4) and run.cpu_median == pytest.approx(1.0)
    far = perturbed(ref, indices=(400,), to=0.99)
    run = _gate(codex, far, ref)
    assert not run.ok and "below the review line" in run.why()
    assert run.passed >= run.min_pass, "the sampled quorum alone would have passed it"


def test_a_statement_says_how_it_stands_against_the_cpu_reference(monkeypatch):
    from tests.hold.test_backend_engine import BACKEND, _vega_run
    from tests.hold.test_embedder import a_key
    codex = codex_768(version=5)
    ref = reference()
    run = emb.run_gate(codex, reference=ref, embed=lambda texts: ref, seed=b"\x09" * 32,
                       stated=_vega_run(codex), embed_one=fake_embed, contribute=fake_embed,
                       cpu_reference=("sha256:" + "44" * 32, perturbed(ref, indices=(3,), to=0.9995)))
    body = emb.read_statement(emb.statement(run, codex, a_key(), now="2026-10-31T00:00:00Z"))
    assert body["version"] == 5 and body["exactness"] == "exact" and body["loads"] == 1
    assert body["cpu_reference"]["run"] == "sha256:" + "44" * 32
    assert body["cpu_reference"]["worst"].startswith("0.9995")


def _v5(**over):
    from app.hold import runs as hold_runs
    codex = codex_768(version=5)
    body = {"version": 5, "passed": 16, "median": "1.000000", "exactness": "exact",
            "loads": 1, "load_variation": "1", "cpu_reference": None, **over}
    return hold_runs.passing(body, codex)


def test_a_version_5_statement_is_admitted_by_how_exact_it_is():
    """The gate's own-bytes verdict is in the statement from version 5, and a
    write is admitted by the statement: before it, a run that did not give
    back its own bytes passed `passing` all the same."""
    assert _v5()
    assert not _v5(load_variation="0.999998"), "exact means byte-equal"
    assert not _v5(cpu_reference={"run": "r", "worst": "0.994", "median": "0.9999"}), \
        "below the review line against the stored CPU reference"
    assert _v5(cpu_reference={"run": "r", "worst": "0.996", "median": "0.9999"})
    near = dict(exactness="near", loads=3, load_variation="0.999995",
                cpu_reference={"run": "r", "worst": "0.9995", "median": "1.000000"})
    assert _v5(**near)
    assert not _v5(**dict(near, loads=1)), "one load could be the lucky one"
    assert not _v5(**dict(near, load_variation="0.99990"))
    assert not _v5(**dict(near, cpu_reference=None)), "near needs the CPU to stand against"
    assert not _v5(**dict(near, cpu_reference={"run": "r", "worst": "0.9985",
                                               "median": "1"})), "near needs 0.999"


def test_a_run_that_does_not_give_back_its_own_bytes_is_not_admitted():
    from app.hold import runs as hold_runs
    from tests.hold.test_backend_engine import _vega_run
    from tests.hold.test_embedder import a_key
    codex = codex_768(version=5)
    ref = reference()
    calls = {"n": 0}

    def drifting(texts):
        calls["n"] += 1
        return fake_embed(texts, tag=b"" if calls["n"] == 1 else b"drift")
    run = emb.run_gate(codex, reference=ref, embed=lambda texts: ref, seed=b"\x09" * 32,
                       stated=_vega_run(codex), embed_one=drifting, contribute=fake_embed)
    assert run.reproducible is False and run.load_variation < 1
    body = emb.read_statement(emb.statement(run, codex, a_key(), now="2026-10-31T00:00:00Z"))
    assert int(body["passed"]) >= codex.space.calibration.min_pass
    assert not hold_runs.passing(body, codex), "the statement now carries the verdict"


# ---- the bound on a CPU twin's request (München, 2026-11-01) --------------------
# wikidata's bge-m3 space (ctx 8,191) timed out three times at the card's flat
# 300 s: 16 texts a request (its `[encoder] batch`) of up to 8,191 tokens.

def test_a_request_to_the_cpu_twin_is_bounded_by_the_spaces_window():
    from app.hold import cpu_reference as cpu_ref
    assert cpu_ref.request_timeout(1024, 1) == 300.0
    assert cpu_ref.request_timeout(None, 1) == 300.0, "no window named: one 1,024"
    assert cpu_ref.request_timeout(8191, 1) == 2400.0
    assert cpu_ref.request_timeout(8191, 2) == 4800.0, "one queued behind another"


def test_the_reference_asks_one_text_a_request_under_that_bound(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app import collections as collections_mod
    from app import config, encoder_use, louise, run_check
    from app.hold import collection as col
    from app.hold import embedder as emb
    from app.hold import fingerprint
    recipe = collections_mod.EmbedRecipe(url="http://localai:8080", api="vega",
                                         model="vega-bge-m3-cpu", dtype="f16", ctx=8191,
                                         batch=16)
    codex = _space(ctx=8191)
    monkeypatch.setattr(col, "resolve", lambda root, name: ("wikidata", codex))
    monkeypatch.setattr(louise, "_recipe_for", lambda c, n: recipe)
    monkeypatch.setattr(louise, "_as_candidate", lambda r, a: r)
    monkeypatch.setattr(run_check, "calibration_items", lambda *a: ["x"] * 3)
    monkeypatch.setattr(run_check, "probe_for", lambda *a: "louise")
    stated = SimpleNamespace(driver="cpu", run_id="sha256:" + "c" * 64, engine={}, weights="w")
    monkeypatch.setattr(run_check, "stated_run", lambda *a, **k: stated)
    monkeypatch.setattr(emb, "gate_texts", lambda c, role, items: list(items))
    monkeypatch.setattr(emb, "reproduces", lambda a, b: True)
    monkeypatch.setattr(fingerprint, "indices", lambda texts: [0])
    monkeypatch.setattr(config, "EMBED_CONCURRENCY", 2)
    asked = []

    def answers(r, **kw):
        asked.append((r.batch, kw.get("timeout")))
        fn = lambda texts: [[1.0, 0.0] for _ in texts]
        fn.runs = {stated.run_id}
        return fn
    monkeypatch.setattr(run_check, "answers", answers)

    class Held:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(encoder_use, "held", lambda *a, **k: Held())
    a = SimpleNamespace(root=tmp_path, collection="wikidata", model="vega-bge-m3-cpu",
                        query_model=None)
    assert louise.cmd_embedder_cpu_reference(a) == 0
    assert asked == [(1, 4800.0), (1, 4800.0)], "one text a request, the window's bound"


# ---- a review line per quantization level, measured (operator, 2026-11-01) ----
# "Calculate a reasonable threshold per quantization level, do not just assume
# it is the same as the full precision." The CPU rounds activations to 8 bits for
# every quantised weight type, so each level is measured card against its CPU
# twin and its line computed by one rule; an unmeasured level has none.

@pytest.mark.parametrize("model,level", [
    ("Qwen3-Embedding-4B-Q4_K_M.gguf", "Q4_K_M"),
    ("/models/x-IQ4_XS.gguf", "IQ4_XS"),
    ("model-q8_0.gguf", "Q8_0"),
    ("bge-m3-Q6_K.gguf", "Q6_K"),
    ("bge-m3-FP16.gguf", "FP16"),
    ("MedCPT-Query-Encoder-f16.gguf", "F16"),
    ("Q40.gguf", None),
    (None, None),
])
def test_the_level_is_the_type_tag_of_the_weights_localai_loaded(model, level):
    engine = {"options": {"Model": model}} if model is not None else {"name": "x"}
    assert emb.quant_level(engine) == level


def test_the_rule_gives_headroom_over_the_worst_measured_and_never_tightens_full_precision():
    assert emb.cpu_line_from(0.989292) == 0.983, "gutenberg's Q4_K_M: 1 - 1.5 x 0.010708"
    assert emb.cpu_line_from(0.99) == 0.985
    assert emb.cpu_line_from(0.99993) == 0.995, "a quantised level: never stricter than 0.995"
    assert emb.cpu_line_from(0.95) == 0.925


def test_a_sixteen_bit_level_is_as_strict_as_its_measurement_and_never_looser_than_0995():
    # operator, 2026-11-02: "Lift the cap for 16-bit as recommended"
    assert emb.cpu_line_from(0.998362, full_precision=True) == 0.997, "AMD's bge-m3 F16"
    assert emb.cpu_line_from(0.99993, full_precision=True) == 0.999
    assert emb.cpu_line_from(0.99, full_precision=True) == 0.995, "never looser than 0.995"


@pytest.mark.parametrize("model,line", [
    ("Qwen3-Embedding-4B-Q4_K_M.gguf", 0.991),     # 4-bit: the operator's line (2026-11-08)
    ("bge-m3-FP16.gguf", 0.997),                   # F16: the cross-vendor worst 0.998362
    ("MedCPT-Query-Encoder-f16.gguf", 0.997),
    ("harrier-oss-v1-0.6b-f16.gguf", 0.997),
    ("x-BF16.gguf", 0.995),                        # BF16 not measured: the floor
    ("dar-legal-it", 0.995),                       # no tag: a full-precision backend
    ("bge-m3-Q8_0.gguf", None),                    # less quantized, not measured: the old rule, no line
    ("x-Q5_K_M.gguf", None),
    ("x-Q3_K_M.gguf", None),                       # more quantized: the old rule too
    ("x-IQ4_XS.gguf", 0.991),                      # every 4-bit level, measured or not
    ("x-Q4_0.gguf", 0.991),
    ("x-MXFP4.gguf", 0.991),
])
def test_each_level_has_its_own_line_or_none_until_measured(model, line):
    assert emb.cpu_review_line({"options": {"Model": model}}) == line


def _engine(model):
    from dataclasses import replace

    from tests.hold.test_backend_engine import BACKEND
    return replace(BACKEND, options={"Model": model, "ModelFile": "${WEIGHTS}"})


def _gated(codex, engine, cpu):
    from tests.hold.test_backend_engine import _vega_run
    ref = reference()
    return emb.run_gate(codex, reference=ref, embed=lambda texts: ref, seed=b"\x09" * 32,
                        stated=_vega_run(codex, engine), embed_one=fake_embed,
                        contribute=fake_embed, cpu_reference=("sha256:" + "44" * 32, cpu))


def test_a_level_is_held_to_its_own_line():
    codex = codex_768()
    ref = reference()
    # gutenberg's Q4_K_M as both engines stated it (München, 2026-11-08): at the 4-bit line
    for worst in (0.991184, 0.991228):
        assert _gated(codex, _engine("Qwen3-Embedding-4B-Q4_K_M.gguf"),
                      perturbed(ref, indices=(235,), to=worst)).ok, worst
    gutenberg = perturbed(ref, indices=(235,), to=0.989292)
    below = _gated(codex, _engine("Qwen3-Embedding-4B-Q4_K_M.gguf"), gutenberg)
    assert not below.ok and "below the review line 0.991" in below.why() \
        and "4-bit, the operator (2026-11-08)" in below.why(), "the old computed 0.983 is gone"
    full = _gated(codex, _engine("bge-m3-FP16.gguf"), perturbed(ref, indices=(235,), to=0.9965))
    assert not full.ok and "below the review line 0.997" in full.why(), "16-bit keeps its line"
    broken = _gated(codex, _engine("Qwen3-Embedding-4B-Q4_K_M.gguf"),
                    perturbed(ref, indices=(7,), to=0.126))
    assert not broken.ok and "below the review line 0.991" in broken.why(), \
        "a broken engine still fails"


def test_an_unmeasured_level_is_never_judged_on_another_levels_line():
    codex = codex_768()
    ref = reference()
    run = _gated(codex, _engine("bge-m3-Q8_0.gguf"), perturbed(ref, indices=(3,), to=0.9999))
    assert run.cpu_line is None and run.quant == "Q8_0"
    assert not run.ok and "not measured yet" in run.why() and "cross_vendor_check" in run.why()
    from tests.hold.test_backend_engine import _vega_run
    no_reference = emb.run_gate(codex, reference=ref, embed=lambda texts: ref, seed=b"\x09" * 32,
                                stated=_vega_run(codex, _engine("bge-m3-Q8_0.gguf")),
                                embed_one=fake_embed, contribute=fake_embed)
    assert no_reference.ok, "without a CPU reference there is nothing to judge: exact passes"


def test_a_statement_is_admitted_at_its_levels_line():
    # gutenberg's live run on ea2417d (document-b12481cba1aaceb2.json, München 2026-11-08)
    gutenberg = {"run": "r", "worst": "0.991184", "median": "0.998318"}
    q4 = {"options": {"Model": "Qwen3-Embedding-4B-Q4_K_M.gguf"}}
    assert _v5(engine=q4, cpu_reference=gutenberg), "at the 4-bit line 0.991"
    assert not _v5(cpu_reference=gutenberg), "full precision keeps 0.995"
    assert not _v5(engine=q4, cpu_reference={"run": "r", "worst": "0.989292", "median": "0.998358"}), \
        "below 0.991: the computed 0.983 no longer admits it (operator, 2026-11-08)"
    q8 = {"options": {"Model": "bge-m3-Q8_0.gguf"}}
    assert not _v5(engine=q8, cpu_reference={"run": "r", "worst": "0.9999", "median": "1"}), \
        "an unmeasured level is not admitted against a CPU reference"
    assert _v5(engine=q8), "and nothing to judge without one"


def test_a_role_this_run_already_kept_is_not_made_again(monkeypatch, tmp_path):
    """gutenberg, 2026-11-02: the document role kept at 06:51 (6.7 h of CPU),
    the query role 352 texts in — a stop and a new start would have made the
    document role again. The same run on the same set gives the same bytes
    (invariant 12): the kept role is skipped; `--again` makes it again."""
    from types import SimpleNamespace

    from app import collections as collections_mod
    from app import encoder_use, louise, run_check
    from app.hold import collection as col
    from app.hold import cpu_reference as cpu_ref
    from app.hold import embedder as emb
    from app.hold import fingerprint
    recipe = collections_mod.EmbedRecipe(url="http://localai:8080", api="vega",
                                         model="vega-q-cpu", dtype="f16", ctx=8191, batch=1)
    codex = _space(ctx=8191, query="w")
    stated = SimpleNamespace(driver="cpu", run_id="sha256:" + "d" * 64, engine={}, weights="w")
    monkeypatch.setattr(col, "resolve", lambda root, name: ("gutenberg", codex))
    monkeypatch.setattr(louise, "_recipe_for", lambda c, n: recipe)
    monkeypatch.setattr(louise, "_as_candidate", lambda r, a: r)
    monkeypatch.setattr(run_check, "calibration_items", lambda *a: ["x"] * 3)
    monkeypatch.setattr(run_check, "probe_for", lambda *a: "louise")
    monkeypatch.setattr(run_check, "stated_run", lambda *a, **k: stated)
    monkeypatch.setattr(emb, "gate_texts", lambda c, role, items: [f"{role}:{i}" for i in items])
    monkeypatch.setattr(emb, "reproduces", lambda a, b: True)
    monkeypatch.setattr(fingerprint, "indices", lambda texts: [0])
    embedded = []

    def answers(r, **kw):
        def fn(texts):
            embedded.extend(texts)
            return [[1.0, 0.0] for _ in texts]
        fn.runs = {stated.run_id}
        return fn
    monkeypatch.setattr(run_check, "answers", answers)

    class Held:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(encoder_use, "held", lambda *a, **k: Held())
    rage.put(codex, "document", run_id=stated.run_id,
             run={"driver": "cpu", "engine": {}, "weights": "w"}, vectors=[[0.0, 1.0]] * 3)
    a = SimpleNamespace(root=tmp_path, collection="gutenberg", model="vega-q-cpu",
                        query_model="vega-q-cpu", again=False)
    assert louise.cmd_embedder_cpu_reference(a) == 0
    assert embedded and all(t.startswith("query:") for t in embedded), "the document role was kept"
    assert rage.resolve(codex, "query", "w", 3).run_id == stated.run_id
    assert rage.resolve(_space(ctx=8191, query="w", set_id="other"), "document", "w", 3) is None, \
        "another set"
    assert not cpu_ref.legacy_directory(tmp_path, "gutenberg", "document").exists(), \
        "no collection keeps a copy"
    embedded.clear()
    a.again = True
    assert louise.cmd_embedder_cpu_reference(a) == 0
    assert any(t.startswith("document:") for t in embedded), "--again makes it again"


# ---- the pass is resumable (invariant 2) --------------------------------------
# gutenberg, 2026-11-02: the query role held 352 of 512 answers in memory when
# one request came back after a 5 h `docker pause` with its VeGa receipt
# expired; the pass exited 1 and the 352 answers (~4.6 h of CPU) were lost.

def _pass(monkeypatch, tmp_path, n, ask):
    """`hold embedder cpu-reference` on `n` items with the document role only;
    `ask(texts)` answers or raises. Returns a function that runs the command."""
    from types import SimpleNamespace

    from app import collections as collections_mod
    from app import encoder_use, louise, run_check
    from app.hold import collection as col
    from app.hold import embedder as emb
    from app.hold import fingerprint
    recipe = collections_mod.EmbedRecipe(url="http://localai:8080", api="vega",
                                         model="vega-q-cpu", dtype="f16", ctx=1024, batch=1)
    codex = _space(ctx=1024)
    stated = SimpleNamespace(driver="cpu", run_id="sha256:" + "d" * 64, engine={}, weights="w")
    monkeypatch.setattr(col, "resolve", lambda root, name: ("gutenberg", codex))
    monkeypatch.setattr(louise, "_recipe_for", lambda c, n: recipe)
    monkeypatch.setattr(louise, "_as_candidate", lambda r, a: r)
    monkeypatch.setattr(run_check, "calibration_items", lambda *a: [str(i) for i in range(n)])
    monkeypatch.setattr(run_check, "probe_for", lambda *a: "louise")
    monkeypatch.setattr(run_check, "stated_run", lambda *a, **k: stated)
    monkeypatch.setattr(emb, "gate_texts", lambda c, role, items: [f"t{i}" for i in items])
    monkeypatch.setattr(emb, "reproduces", lambda a, b: True)
    # the fingerprint's sixteen are asked apart (`how="one"`): none here, so
    # what is asked is the role's pass alone
    monkeypatch.setattr(fingerprint, "indices", lambda texts: [])

    def answers(r, **kw):
        from app.hold import runs as runs_mod

        def fn(texts, runs):
            out = ask(texts)
            runs.add(getattr(ask, "run", stated.run_id))
            return out
        return runs_mod.Answers(fn)
    monkeypatch.setattr(run_check, "answers", answers)

    class Held:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(encoder_use, "held", lambda *a, **k: Held())

    def run(again=False):
        return louise.cmd_embedder_cpu_reference(SimpleNamespace(
            root=tmp_path, collection="gutenberg", model="vega-q-cpu", query_model=None,
            again=again))
    return run, stated


def _store(role="document", ctx=1024):
    """The node's store for the pass `_pass` runs: weights w, set s, its window."""
    return cpu_ref.node_directory(cpu_ref.node_key(weights="w", set_id="s", ctx=ctx,
                                                   instruction=None, role=role))


def _vec(text):
    # a float64 that float32 rounds: the journal must give back the bytes a
    # whole pass would write
    k = int(text[1:])
    return [k / 3.0, 1.0 - k / 7.0]


def test_a_pass_stopped_by_an_expired_receipt_resumes_where_it_stopped(monkeypatch, tmp_path):
    from app import embeddings
    asked: list[str] = []
    fail_at = {"t21"}

    def ask(texts):
        for t in texts:
            if t in fail_at:
                fail_at.discard(t)
                raise embeddings.ReceiptDeferred("no receipt for input 0: expired")
        asked.extend(texts)
        return [_vec(t) for t in texts]
    run, stated = _pass(monkeypatch, tmp_path, 40, ask)
    assert run() == 3, "a receipt that expired defers the pass, it does not fail it"
    j = cpu_ref.journal_in(_store(), run_id=stated.run_id, set_id="s")
    assert len(j.read_text().splitlines()) == 16, "the two windows answered before it are kept"
    asked.clear()
    assert run() == 0
    assert asked[0] == "t16" and "t0" not in asked, "only what the journal does not hold is asked"
    assert not j.exists(), "the journal goes once the reference is kept"
    resumed = rage.resolve(_space(), "document", "w", 40)
    # the same bytes as one uninterrupted pass, on a node whose store is empty
    whole = tmp_path / "whole"
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path / "another-node"))
    run2, _ = _pass(monkeypatch, whole, 40, lambda texts: [_vec(t) for t in texts])
    assert run2() == 0
    once = rage.resolve(_space(), "document", "w", 40)
    assert once.path != resumed.path, "another node's store"
    assert resumed.vectors == once.vectors
    assert resumed.path.read_bytes() == once.path.read_bytes()


def test_a_window_from_another_run_is_not_journalled(monkeypatch, tmp_path):
    calls = {"n": 0}

    def ask(texts):
        calls["n"] += 1
        ask.run = "sha256:" + ("d" if calls["n"] == 1 else "e") * 64
        return [_vec(t) for t in texts]
    run, stated = _pass(monkeypatch, tmp_path, 20, ask)
    assert run() == 3
    j = cpu_ref.journal_in(_store(), run_id=stated.run_id, set_id="s")
    assert len(j.read_text().splitlines()) == cpu_ref.WINDOW, "only the first window's run is the pass's"


def test_a_torn_line_is_asked_again_and_again_starts_over(monkeypatch, tmp_path):
    asked: list[str] = []

    def ask(texts):
        asked.extend(texts)
        return [_vec(t) for t in texts]
    run, stated = _pass(monkeypatch, tmp_path, 10, ask)
    # a pass killed mid-line leaves its journal in the node's store
    j = cpu_ref.journal_in(_store(), run_id=stated.run_id, set_id="s")
    texts = [f"t{i}" for i in range(10)]
    seen: list[str] = []

    class Stop(Exception):
        pass

    def first(ts):
        seen.extend(ts)
        if len(seen) > 8:
            raise Stop
        return [_vec(t) for t in ts]
    first.runs = {stated.run_id}
    with pytest.raises(Stop):
        cpu_ref.embed_resumable_in(_store(), "document", run_id=stated.run_id,
                                   set_id="s", texts=texts, embed=first, window=4)
    lines = j.read_text().splitlines()
    assert len(lines) == 8
    j.write_text("\n".join(lines[:-1]) + "\n" + lines[-1][:20])   # killed mid-line
    assert run() == 0
    assert asked == ["t7", "t8", "t9"], "the torn line's text is asked again, the rest kept"
    asked.clear()
    assert run(again=True) == 0
    assert asked == texts, "--again makes the role again from nothing"


def test_a_journal_line_answers_only_the_same_input_at_the_same_place(tmp_path):
    run_id = "sha256:" + "d" * 64
    one = lambda ts: [_vec(t) for t in ts]
    one.runs = {run_id}
    cpu_ref.embed_resumable_in(_store(), "document", run_id=run_id, set_id="s",
                               texts=["t0", "t1"], embed=one)
    asked: list[str] = []

    def again(ts):
        asked.extend(ts)
        return [_vec(t) for t in ts]
    again.runs = {run_id}
    got = cpu_ref.embed_resumable_in(_store(), "document", run_id=run_id, set_id="s",
                                     texts=["t1", "t0"], embed=again)
    assert asked == ["t1", "t0"], "another order is another input at each place"
    assert got == [_vec_f32("t1"), _vec_f32("t0")]
    asked.clear()
    cpu_ref.embed_resumable_in(_store(), "document", run_id=run_id, set_id="other",
                               texts=["t0", "t1"], embed=again)
    assert asked == ["t0", "t1"], "another set is another journal"


def _vec_f32(text):
    import numpy as np
    return np.asarray(_vec(text), dtype="<f4").astype(float).tolist()


# ---- once per node (operator, 2026-11-06: "I already asked to save the cpu
# reference values so that we don't have to repeat that time consuming part")

def _answers(fn, run_id):
    from app.hold import runs as runs_mod

    def call(texts, runs):
        runs.add(run_id)
        return fn(texts)
    return runs_mod.Answers(call)


def test_a_reference_kept_once_is_taken_by_the_next_pass_without_the_cpu(tmp_path):
    texts = [f"t{i}" for i in range(40)]
    asked: list[str] = []

    def twin(ts):
        asked.extend(ts)
        return [_vec(t) for t in ts]
    run_a = "sha256:" + "a" * 64
    d = _store()
    first = cpu_ref.obtain(d, "document", set_id="s", texts=texts, run_id=run_a,
                           run=CPU_RUN, embed=_answers(twin, run_a), one=_answers(twin, run_a))
    assert first.how == "computed" and len(asked) >= len(texts)
    asked.clear()
    again = cpu_ref.obtain(d, "document", set_id="s", texts=texts, run_id=run_a,
                           run=CPU_RUN, embed=_answers(twin, run_a), one=_answers(twin, run_a))
    assert again.how == "stored" and asked == [], "the same run: not one text asked"
    assert again.vectors == first.vectors


def test_another_build_is_carried_only_when_its_fingerprint_answers_are_the_same_bytes(tmp_path):
    """RULE 1: reuse bytes that are provably identical. A new engine build of
    the same weights answers the sixteen fingerprint texts; the same bytes
    carry the kept file, other bytes make it again."""
    from app.hold import fingerprint
    texts = [f"t{i}" for i in range(40)]
    d = _store()
    same = lambda ts: [_vec(t) for t in ts]
    run_a, run_b, run_c = ("sha256:" + c * 64 for c in "abc")
    cpu_ref.obtain(d, "document", set_id="s", texts=texts, run_id=run_a, run=CPU_RUN,
                   embed=_answers(same, run_a), one=_answers(same, run_a))
    asked: list[str] = []

    def b(ts):
        asked.extend(ts)
        return same(ts)
    got = cpu_ref.obtain(d, "document", set_id="s", texts=texts, run_id=run_b, run=CPU_RUN,
                         embed=_answers(b, run_b), one=_answers(b, run_b))
    assert got.how == "carried" and got.run_id == run_b
    assert sorted(asked) == sorted(texts[i] for i in fingerprint.indices(texts)), \
        "only the sixteen were asked"
    assert cpu_ref.read(got.path).run_id == run_b, "kept under the new run too"
    other = lambda ts: [[v[0] + 1e-3, v[1]] for v in same(ts)]
    made = cpu_ref.obtain(d, "document", set_id="s", texts=texts, run_id=run_c, run=CPU_RUN,
                          embed=_answers(other, run_c), one=_answers(other, run_c))
    assert made.how == "computed" and made.vectors != got.vectors


def test_a_twin_that_does_not_give_back_its_own_bytes_keeps_nothing(tmp_path):
    texts = [f"t{i}" for i in range(20)]
    n = {"calls": 0}

    def drifting(ts):
        n["calls"] += 1
        return [[_vec(t)[0] + n["calls"] * 1e-3, 1.0] for t in ts]
    run_a = "sha256:" + "a" * 64
    with pytest.raises(cpu_ref.NotReproducible):
        cpu_ref.obtain(_store(), "document", set_id="s", texts=texts, run_id=run_a, run=CPU_RUN,
                       embed=_answers(lambda ts: [_vec(t) for t in ts], run_a),
                       one=_answers(drifting, run_a))
    assert cpu_ref.node_find(_store(), set_id="s", count=len(texts)) is None


def test_the_key_is_what_fixes_the_inputs():
    k = {"weights": "w", "set_id": "s", "ctx": 1024, "instruction": "passage: ", "role": "document"}
    base = cpu_ref.node_directory(cpu_ref.node_key(**k))
    for change in ({"weights": "v"}, {"set_id": "t"}, {"ctx": 2048}, {"instruction": None},
                   {"role": "query"}, {"cut": "tokens"}):
        assert cpu_ref.node_directory(cpu_ref.node_key(**{**k, **change})) != base, change
    assert cpu_ref.node_directory(cpu_ref.node_key(**k)) == base


def test_a_token_space_never_takes_a_characters_reference():
    """2026-11-06, wikidata's gate: calibrate cut by characters, the space
    cuts by tokens, and the key named no cut: `cpu-reference` took the
    characters vectors, and 54 long items failed at 0.974831. The cut is in
    the key; a characters key keeps the digest it had before, so the
    entries made right are still found."""
    k = {"weights": "w", "set_id": "s", "ctx": 8191, "instruction": "passage: ", "role": "document"}
    assert cpu_ref.node_key(**k, cut="characters") == {
        "weights": "w", "set": "s", "ctx": 8191, "instruction": "passage: ", "role": "document"}
    assert cpu_ref.node_key(**k, cut="tokens")["cut"] == "tokens"
    assert cpu_ref.node_directory(cpu_ref.node_key(**k, cut="tokens")) != \
        cpu_ref.node_directory(cpu_ref.node_key(**k))


def test_a_second_pass_takes_the_stores_and_no_collection_keeps_a_copy(monkeypatch, tmp_path,
                                                                     capsys):
    """Operator, 2026-11-22: "The CPU reference is per model, not per
    collection". A second pass asks nothing, and nothing is written under the
    collection (its copy, before the store, was a second truth: wikidata's
    held the characters-cut vectors, 2026-11-06)."""
    asked = []

    def ask(texts):
        asked.extend(texts)
        return [_vec(t) for t in texts]
    run, _ = _pass(monkeypatch, tmp_path, 12, ask)
    assert run() == 0 and len(asked) == 12
    capsys.readouterr()
    assert run() == 0 and len(asked) == 12, "nothing asked again"
    assert "(inherited: the node's store holds it" in capsys.readouterr().out
    assert not cpu_ref.legacy_directory(tmp_path, "gutenberg", "document").parent.exists()


def _command(monkeypatch, tmp_path, codex, name, *, twin_weights="w"):
    """`hold embedder cpu-reference <name>` with every twin request recorded."""
    from types import SimpleNamespace

    from app import collections as collections_mod
    from app import encoder_use, louise, run_check
    from app.hold import collection as col
    from app.hold import fingerprint
    recipe = collections_mod.EmbedRecipe(url="http://localai:8080", api="vega",
                                         model="vega-nemo", dtype="f16", ctx=1024, batch=1)
    stated = SimpleNamespace(driver="cpu", run_id="sha256:" + "d" * 64, engine={}, weights=twin_weights)
    asked: dict = {"stated": [], "texts": []}
    monkeypatch.setattr(col, "resolve", lambda root, n: (name, codex))
    monkeypatch.setattr(louise, "_recipe_for", lambda c, n: recipe)
    monkeypatch.setattr(louise, "_as_candidate", lambda r, a: dataclasses.replace(r, model=a.model))
    monkeypatch.setattr(run_check, "calibration_items", lambda *a: [str(i) for i in range(12)])
    monkeypatch.setattr(run_check, "probe_for", lambda *a: "louise")

    def stated_run(r, **k):
        asked["stated"].append(r.model)
        return stated
    monkeypatch.setattr(run_check, "stated_run", stated_run)
    monkeypatch.setattr(emb, "gate_texts", lambda c, role, items: [f"t{i}" for i in items])
    monkeypatch.setattr(emb, "reproduces", lambda a, b: True)
    monkeypatch.setattr(fingerprint, "indices", lambda texts: [])

    def answers(r, **kw):
        from app.hold import runs as runs_mod

        def fn(texts, runs):
            asked["texts"].extend(texts)
            runs.add(stated.run_id)
            return [_vec(t) for t in texts]
        return runs_mod.Answers(fn)
    monkeypatch.setattr(run_check, "answers", answers)

    class Held:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(encoder_use, "held", lambda *a, **k: Held())
    return lambda model=None: louise.cmd_embedder_cpu_reference(SimpleNamespace(
        root=tmp_path, collection=name, model=model, query_model=None, again=False)), asked


def test_another_collection_of_the_same_inputs_inherits_it_and_no_twin_is_asked(monkeypatch, tmp_path,
                                                                             capsys):
    """NeuRIS, 2026-11-22: a new collection on an encoder another collection
    already holds a reference for ran the twin's hours again. The reference
    is the model's: resolved by the key, whichever collection made it."""
    run, asked = _command(monkeypatch, tmp_path, _space(), "gutenberg")
    assert run() == 0 and len(asked["texts"]) == 12
    run, asked = _command(monkeypatch, tmp_path, _space(), "neuris")
    capsys.readouterr()
    assert run() == 0
    assert asked == {"stated": [], "texts": []}, "no twin asked, not even its statement"
    assert "inherited" in capsys.readouterr().out
    assert not cpu_ref.legacy_directory(tmp_path, "neuris", "document").parent.exists(), "no copy"


@pytest.mark.parametrize("change", [{"ctx": 2048}, {"set_id": "t"}])
def test_other_inputs_are_another_key_and_are_made_for_themselves(monkeypatch, tmp_path, change):
    run, _ = _command(monkeypatch, tmp_path, _space(), "gutenberg")
    assert run() == 0
    run, asked = _command(monkeypatch, tmp_path, _space(**change), "other")
    assert run() == 0 and len(asked["texts"]) == 12, f"{change}: made, never taken from another key"
    assert rage.resolve(_space(**change), "document", "w", 12) is not None


def test_the_twin_is_the_encoders_model_with_cpu_and_must_run_its_weights(monkeypatch, tmp_path):
    """RULE 2: nobody has to name the twin; and a twin of other weights is
    refused before a text is asked (its reference would sit under a key no
    gate of this space reads)."""
    run, asked = _command(monkeypatch, tmp_path, _space(), "gutenberg")
    assert run() == 0 and asked["stated"] == ["vega-nemo-cpu"]
    run, asked = _command(monkeypatch, tmp_path, _space(ctx=4096), "g2", twin_weights="v")
    assert run(model="vega-other-cpu") == 2 and asked["texts"] == []
    assert rage.twin("vega-nemo") == "vega-nemo-cpu" and rage.twin("vega-nemo-cpu") == "vega-nemo-cpu"


def test_check_says_which_roles_would_find_their_reference_and_makes_nothing(monkeypatch, tmp_path, capsys):
    """München, 2026-11-24: an engine cut-over backed out at its first gate,
    with the web down, for a reference the node had never made (bge-m3). The
    check reads what the gate would resolve, asks no twin, writes nothing."""
    from types import SimpleNamespace

    from app import louise
    run, asked = _command(monkeypatch, tmp_path, _space(), "gutenberg")
    check = lambda: louise.cmd_embedder_cpu_reference(SimpleNamespace(  # noqa: E731
        root=tmp_path, collection="gutenberg", model=None, query_model=None, again=False, check=True))
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert check() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["missing"] == 1 and out["references"][0]["role"] == "document"
    assert out["references"][0]["make"] == "louise hold embedder cpu-reference gutenberg"
    assert asked == {"stated": [], "texts": []} and sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before, \
        "no twin asked, nothing written"
    assert run() == 0                                                  # made once for the node
    capsys.readouterr()
    assert check() == 0
    found = json.loads(capsys.readouterr().out)["references"][0]
    assert found["found"] is True and "path" in found


def test_check_passes_over_a_collection_that_embeds_nothing(monkeypatch, tmp_path, capsys):
    """München, 2026-11-24: the first --check on the node crashed on an
    annotations collection (gutenberg-book-editions), whose Codex has no
    space. It embeds nothing, so there is nothing to find: said, not missing."""
    from types import SimpleNamespace

    from app import louise
    from app.hold import collection as col
    monkeypatch.setattr(col, "list_named", lambda root: [("gutenberg-book-editions", SimpleNamespace(space=None))])
    assert louise.cmd_embedder_cpu_reference(SimpleNamespace(
        root=tmp_path, collection=None, model=None, query_model=None, again=False, check=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"references": [{"collection": "gutenberg-book-editions", "found": None,
                                   "why": "no space: it embeds nothing"}], "missing": 0}
