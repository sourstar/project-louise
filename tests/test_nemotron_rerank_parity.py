"""tools/measure/nemotron_rerank_parity.py: the comparison of our llama.cpp
port's scores with NVIDIA's own code, and the engine's reproducibility."""
import json

from tools.measure import nemotron_rerank_parity as par


def test_kendall_tau_counts_concordant_and_discordant_pairs():
    assert par.kendall_tau([1, 2, 3], [10, 20, 30]) == 1.0
    assert par.kendall_tau([1, 2, 3], [30, 20, 10]) == -1.0
    assert abs(par.kendall_tau([1, 2, 3, 4], [1, 2, 4, 3]) - (4 / 6)) < 1e-9
    assert par.kendall_tau([5.0], [1.0]) == 1.0


def test_a_query_passes_on_order_and_top1_and_fails_on_its_own_reproduction():
    r = par.compare("q", [20.6, -23.1, -0.26], [20.4, -23.0, -0.30], [20.4, -23.0, -0.30],
                    [20.4, -23.0, -0.30])
    assert r.tau == 1.0 and r.same_top1 and r.reproduces and r.max_abs_delta == 0.2
    drift = par.compare("q", [3, 2, 1], [3, 2, 1], [3, 2, 1.0001], [3, 2, 1])
    assert not drift.reproduces and drift.notes == ["the same request scored differently"]


def test_the_verdict_needs_every_top1_the_median_tau_and_exact_reproduction():
    good = par.compare("q", [3, 2, 1], [3, 2, 1], [3, 2, 1], [3, 2, 1])
    flipped = par.compare("q", [3, 2, 1], [2, 3, 1], [2, 3, 1], [2, 3, 1])
    assert par.verdict([good, good])["pass"]
    v = par.verdict([good, flipped])
    assert not v["pass"] and v["same_top1"] == 0.5
    assert par.verdict([])["pass"] is False


def test_pairs_come_from_a_capture_as_the_reranker_was_sent_them(tmp_path):
    path = tmp_path / "c.jsonl"
    rows = [{"header": {"tool": "rerank_bench capture"}},
            {"query": "q1", "candidates": [{"query_sent": "q1 (it)", "chunks": [{"text": "a"}, {"text": "b"}]},
                                           {"query_sent": "q1 (it)", "chunks": [{"text": "c"}]}]},
            {"query": "q2", "candidates": [{"chunks": [{"text": "d"}]}]}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = par.pairs_from_capture(path, queries=5, per_query=2)
    assert got == [{"query": "q1 (it)", "documents": ["a", "b"]}, {"query": "q2", "documents": ["d"]}]


def test_run_sends_the_same_cut_documents_to_both_and_reverses_for_the_order_check():
    seen = []

    def ref(q, docs):
        seen.append(("ref", tuple(docs)))
        return [float(len(d)) for d in docs]

    def eng(q, docs):
        seen.append(("eng", tuple(docs)))
        return [float(len(d)) for d in docs]
    long = "x" * (par.MAX_CHARS + 50)
    (r,) = par.run([{"query": "q", "documents": ["ab", long]}], ref, eng)
    assert seen[0] == ("ref", ("ab", "x" * par.MAX_CHARS)), "cut alike on both sides"
    assert seen[-1] == ("eng", ("x" * par.MAX_CHARS, "ab")), "the reversed request"
    assert r.reproduces and r.same_top1


def test_the_template_is_the_model_cards():
    assert par.TEMPLATE.format(q="Q", p="P") == "question:Q \n \n passage:P"
