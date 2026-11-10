"""tools/measure/madlad_variants.py: the deterministic pick, both directions,
and the aggregation (scorers injected, so no sacrebleu needed here)."""
from tools.measure import madlad_variants as mv


def _row(seg, domain, bad=False, src="s", tgt="t"):
    return {"segment_id": seg, "domain": domain, "is_bad_source": bad,
            "source": f"{src}{seg}", "target": f"{tgt}{seg}"}


def test_target_code():
    assert mv.target_code("it_IT") == "it"
    assert mv.target_code("pt_BR") == "pt"
    assert mv.target_code("zh_TW") == "zh_Hant"


def test_select_round_robin_skips_bad_and_is_deterministic():
    rows = [_row(0, "canary", bad=True), _row(1, "news"), _row(2, "news"),
            _row(3, "news"), _row(4, "speech"), _row(5, "literary"),
            _row(6, "speech"), {**_row(7, "social"), "target": " "}]
    got = [r["segment_id"] for r in mv.select_rows(rows, 5)]
    assert got == [1, 4, 5, 2, 6]
    assert got == [r["segment_id"] for r in mv.select_rows(list(rows), 5)]
    assert len(mv.select_rows(rows, 100)) == 6


def test_jobs_both_directions():
    js = mv.jobs("de_DE", [_row(1, "news")])
    assert [(j["dir"], j["text"], j["to"], j["ref"]) for j in js] == [
        ("en-xx", "s1", "de", "t1"), ("xx-en", "t1", "en", "s1")]


def test_aggregate_missing_vs_base_and_speed():
    rows = [{"pair": "it_IT", "seg": 1, "dir": "en-xx", "to": "it", "ref": "a",
             "hyp": "a", "batch_s": 2.0, "batch_n": 2},
            {"pair": "it_IT", "seg": 2, "dir": "en-xx", "to": "it", "ref": "b",
             "hyp": None, "batch_s": 2.0, "batch_n": 2}]
    base = {("it_IT", 1, "en-xx"): "a", ("it_IT", 2, "en-xx"): "b"}

    def chrf(h, r):
        return 100.0 * sum(x == y for x, y in zip(h, r)) / len(h)

    def bleu(h, r, lang):
        assert lang == "it"
        return 1.0

    out = mv.aggregate(rows, base, chrf, bleu)
    g = out["en-xx"]
    assert g["n"] == 2 and g["missing"] == 0.5 and g["chrF++"] == 50.0
    assert g["chrF++_vs_base"] == 50.0 and g["s_per_seg"] == 1.0
    assert out["en-xx it_IT"]["n"] == 2
