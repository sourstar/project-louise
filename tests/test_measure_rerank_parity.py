"""tools/measure/rerank_parity.py: one reranker against another on the same lists,
and the candidate against itself. Fake scorers only: no model server."""
import pytest

from tools.measure import rerank_parity as rp


def _scorer(table, calls=None, drift=None):
    """Scores a document by its text from `table`; `drift(how)` perturbs one way of asking."""
    def score(query, docs):
        if calls is not None:
            calls.append(len(docs))
        out = [table[d] for d in docs]
        return drift(docs, out) if drift else out
    return score


PAIRS = [{"query": "q1", "documents": [f"d{i}" for i in range(12)]},
         {"query": "q2", "documents": [f"d{i}" for i in range(12, 24)]}]
REF = {f"d{i}": 1.0 - i / 100 for i in range(24)}


def test_the_same_scores_pass_and_every_way_of_asking_is_tried():
    calls, reloads = [], []
    res = rp.run(PAIRS, _scorer(REF), _scorer(REF, calls), loads=3, cut_by=5, reload=lambda: reloads.append(1))
    v = rp.verdict(res)
    assert v["pass"] and v["candidate_reproduces"] and v["same_top1"] == 1.0 and v["max_abs_delta"] == 0.0
    assert len(reloads) == 2, "loads=3: the first pass and two fresh loads"
    # warm-up, then per query: the list, again, reversed, cut by 5 (5+5+2); then one list per query per fresh load
    assert calls == [1] + [12, 12, 12, 5, 5, 2] * 2 + [12, 12] * 2


def test_a_score_that_moves_with_its_batch_mates_fails_and_says_how():
    def drift(docs, out):        # a request of fewer documents scores the last bit differently
        return [x + 1e-9 for x in out] if len(docs) < 12 else out
    res = rp.run(PAIRS, _scorer(REF), _scorer(REF, drift=drift), loads=1, cut_by=5)
    v = rp.verdict(res)
    assert not v["pass"] and not v["candidate_reproduces"]
    assert res[0].notes == ["cut by 5: scored differently"]


def test_a_fresh_load_that_scores_differently_fails():
    state = {"load": 1}
    def drift(docs, out):
        return [x * (1 + 1e-7) for x in out] if state["load"] > 1 else out
    res = rp.run(PAIRS, _scorer(REF), _scorer(REF, drift=drift), loads=2, cut_by=0,
                 reload=lambda: state.update(load=2))
    assert [r.notes for r in res] == [["load 2: scored differently"]] * 2


def test_order_and_delta_against_the_reference():
    cand = dict(REF)
    cand["d0"], cand["d1"] = REF["d1"], REF["d0"]          # the first two swapped
    res = rp.run(PAIRS, _scorer(REF), _scorer(cand), loads=1, cut_by=0)
    v = rp.verdict(res)
    assert res[0].same_top1 is False and res[1].same_top1 is True and v["same_top1"] == 0.5 and not v["pass"]
    assert res[0].top10_share == 1.0 and res[0].tau < 1.0 and res[0].max_abs_delta == pytest.approx(0.01)


def test_a_tie_for_first_in_the_reference_is_the_same_first():
    # a sigmoid saturates: two documents at 1.0 to the last bit, the candidate puts the second first
    assert rp.same_first([1.0, 1.0, 0.2], [0.9999, 1.0, 0.2])
    assert not rp.same_first([1.0, 0.9, 0.2], [0.8, 1.0, 0.2])


def test_the_calibration_s_difference_is_reported_not_a_line():
    cand = {k: v + 0.005 for k, v in REF.items()}
    res = rp.run(PAIRS, _scorer(REF), _scorer(cand), loads=1, cut_by=0, calibration=(4.0, -2.0))
    v = rp.verdict(res)
    assert v["pass"] and 0 < v["max_calibrated_delta"] < 0.01
    assert v["lines"]["status"] == "proposed, not measured before" and "max_calibrated_delta" not in v["lines"]


def test_fresh_loads_need_an_unload():
    with pytest.raises(ValueError):
        rp.run(PAIRS, _scorer(REF), _scorer(REF), loads=2, reload=None)


def test_a_capture_whose_documents_hold_line_and_paragraph_separators_is_read_whole(tmp_path):
    """JSON keeps U+2028 and U+2029 unescaped inside a string; PubMed's abstracts hold them (München, 2026-11-27)."""
    import json
    from tools.measure import rerank_bench as rb
    from tools.measure.nemotron_rerank_parity import pairs_from_capture
    doc = "Background. Methods: a trial. Results: none.\x85End"
    rows = [{"header": {"tool": "rerank_bench"}},
            {"key": "k1", "candidates": [{"query_sent": "q", "chunks": [{"text": doc}, {"text": "second"}]}]}]
    f = tmp_path / "set.jsonl"
    f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    assert " " in f.read_text(encoding="utf-8"), "written unescaped, as the capture writes it"
    assert pairs_from_capture(f, 10, 10) == [{"query": "q", "documents": [doc, "second"]}]
    assert [r.get("key") for r in rb.read_jsonl(f)] == [None, "k1"]


def test_held_to_the_model_a_candidate_nearer_it_than_the_reference_passes_the_delta():
    truth = dict(REF)
    reference = {k: v + 0.012 for k, v in REF.items()}      # the engine replaced: 0.012 from the model
    candidate = {k: v + 0.002 for k, v in REF.items()}      # the candidate: 0.002 from it, 0.010 from the reference
    res = rp.run(PAIRS, _scorer(reference), _scorer(candidate), loads=1, cut_by=0, model=_scorer(truth))
    v = rp.verdict(res)
    assert v["max_abs_delta"] == pytest.approx(0.01) and v["candidate_vs_model"] == pytest.approx(0.002)
    assert v["reference_vs_model"] == pytest.approx(0.012) and v["pass"]


def test_held_to_the_model_a_candidate_farther_than_the_reference_and_the_line_fails():
    reference = {k: v + 0.001 for k, v in REF.items()}
    candidate = {k: v + 0.02 for k, v in REF.items()}
    res = rp.run(PAIRS, _scorer(reference), _scorer(candidate), loads=1, cut_by=0, model=_scorer(dict(REF)))
    assert not rp.verdict(res)["pass"]


def _lists(n=4, k=5):
    return [{"query": f"q{i}", "documents": [f"doc {i}.{j} " + "x" * j for j in range(k)]} for i in range(n)]


def test_another_parallel_identical_scores_and_orders_are_counted():
    from tools.measure import rerank_parity as rp
    base = lambda q, docs: [float(len(d)) + 0.25 * (hash(q) % 3) for d in docs]
    same = rp.run(_lists(), base, base, concurrency=3, concurrent_ms={})
    v = rp.verdict(same)
    assert v["identical_to_reference"] == 4 and v["orders_changed"] == 0 and v["candidate_reproduces"]

    calls = {"n": 0}

    def drifts(q, docs):                     # a candidate whose bits move with its batch-mates
        calls["n"] += 1
        return [s + (1e-7 if calls["n"] % 2 else 0.0) for s in base(q, docs)]
    moved = rp.verdict(rp.run(_lists(), base, drifts, concurrency=3, concurrent_ms={}))
    assert moved["identical_to_reference"] < 4 and moved["orders_changed"] == 0
    assert not moved["candidate_reproduces"] and moved["max_abs_delta"] <= 1e-6


def test_an_order_that_flips_in_any_answer_is_counted():
    from tools.measure import rerank_parity as rp
    base = lambda q, docs: [1.0, 0.5, 0.5000001, 0.1][:len(docs)]
    calls = {"n": 0}

    def flips(q, docs):
        calls["n"] += 1
        s = base(q, docs)
        return [s[0], s[2], s[1], s[3]][:len(docs)] if calls["n"] == 3 else s
    pairs = [{"query": "q", "documents": ["a", "b", "c", "d"]}]
    r = rp.run(pairs, base, flips)[0]
    assert not r.order_same and r.identical


def test_lists_in_flight_are_timed_for_both_and_the_reference_checked():
    from tools.measure import rerank_parity as rp
    base = lambda q, docs: [float(len(d)) for d in docs]
    pairs, wall = _lists(), {}
    results = rp.run(pairs, base, base, concurrency=4, concurrent_ms=wall)
    t = rp.throughput(pairs, results, wall)
    assert t["pairs"] == 20 and t["reference_same_with_lists_in_flight"] is True
    assert {"candidate_ms_per_100_pairs", "reference_ms_per_100_pairs", "candidate_ms_per_100_pairs_concurrent",
            "reference_ms_per_100_pairs_concurrent"} <= set(t)
    # a candidate whose bits move only with other lists in flight is caught by that pass, by name
    state = {"busy": False}

    def busy_only(q, docs):
        return [x + (1e-6 if state["busy"] else 0.0) for x in base(q, docs)]
    orig = rp.concurrently

    def marked(score, *a):
        state["busy"] = True
        try:
            return orig(score, *a)
        finally:
            state["busy"] = False
    rp.concurrently = marked
    try:
        caught = rp.run(pairs, base, busy_only, concurrency=4, concurrent_ms={})
    finally:
        rp.concurrently = orig
    assert all(r.notes == ["4 lists at once: scored differently"] for r in caught)

    calls = {"n": 0}

    def unsteady(q, docs):                   # a reference that moves with others in flight
        calls["n"] += 1
        return [x + (0.001 if calls["n"] > len(pairs) + 1 else 0.0) for x in base(q, docs)]
    wall2 = {}
    rp.run(pairs, unsteady, base, concurrency=4, concurrent_ms=wall2)
    assert wall2["reference_same"] is False
