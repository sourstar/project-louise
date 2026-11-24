"""tools/measure/reading_summary_eval.py: the modern partner of a historical
record, the judge's reply read, the paired statistics."""
from __future__ import annotations

import json

from tools.measure import reading_summary_eval as e


def test_a_modern_partner_is_an_english_record_or_an_italian_translation(tmp_path):
    claims = tmp_path / "c.jsonl"
    claims.write_text("\n".join(json.dumps(c) for c in [
        {"type": "same-work", "subject": "g:1", "object": "g:2"},
        {"type": "same-work", "subject": "g:3", "object": "g:4"},
        {"type": "translation-of", "subject": "g:6", "object": "g:5"},
        {"type": "mention", "subject": "g:7", "object": "g:8"}]) + "\n", encoding="utf-8")
    recs = {"g:1": {"lang": "it"}, "g:2": {"lang": "en"},
            "g:3": {"lang": "it"}, "g:4": {"lang": "it"},                       # an Italian edition, not a translation
            "g:5": {"lang": "it"}, "g:6": {"lang": "it", "extra": {"creators_detail": [{"role": "trl"}]}},
            "g:7": {"lang": "it"}, "g:8": {"lang": "en"}}
    got = e.modern_partners(claims, recs)
    assert got["g:1"] == "g:2" and got["g:5"] == "g:6" and "g:3" not in got and "g:7" not in got


def test_the_judges_reply_is_read_and_a_bad_one_kept_for_review(monkeypatch):
    from app import llm
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: 'ok {"agreement": 4, "contradictions": 1}')
    assert e.judge("q", "gold", "cand") == {"agreement": 4.0, "contradictions": 1}
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: "no json")
    assert e.judge("q", "gold", "cand")["agreement"] is None


def test_the_paired_statistics():
    s = e.paired_stats([1.0] * 20, 2000, 1)
    assert s["n"] == 20 and s["mean_diff"] == 1.0 and s["p"] < 0.01 and s["ci95"] == [1.0, 1.0]
    z = e.paired_stats([1.0, -1.0] * 10, 2000, 1)
    assert z["mean_diff"] == 0.0 and z["p"] > 0.5


def test_a_judge_of_another_family_is_asked_by_its_own_model_name(monkeypatch):
    from app import config, llm
    seen = []
    monkeypatch.setattr(config, "CHAT_MODEL", "louise-chat-qwen3.5-9b-q4-p0")
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: seen.append(config.CHAT_MODEL) or
                        '{"agreement": 3, "contradictions": 0}')
    e.judge("q", "g", "c", "louise-chat-command-r7b-q8")
    e.judge("q", "g", "c")
    assert seen == ["louise-chat-command-r7b-q8", "louise-chat-qwen3.5-9b-q4-p0"]
    assert config.CHAT_MODEL == "louise-chat-qwen3.5-9b-q4-p0"


def test_alignment_is_read(monkeypatch):
    from app import llm
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: '{"alignment": 2}')
    assert e.align("old", "new") == 2.0


def test_a_same_work_claim_pairs_in_either_direction(tmp_path):
    # the node's claims: Dante (gutenberg:997, it) is the OBJECT of same-work claims to English records
    claims = tmp_path / "c.jsonl"
    claims.write_text(json.dumps({"type": "same-work", "subject": "gutenberg:1005", "object": "gutenberg:997"}) + "\n",
                      encoding="utf-8")
    recs = {"gutenberg:997": {"lang": "it"}, "gutenberg:1005": {"lang": "en"}}
    assert e.modern_partners(claims, recs) == {"gutenberg:997": "gutenberg:1005"}



def test_an_answer_goes_through_the_ask_paths_own_steps(monkeypatch):
    """The real historical.prepare and retrieval.build_answer_messages, on a
    result shaped as the search makes it (the node's crash: KeyError 'catalog')."""
    from app import llm, reading
    seen = []
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: seen.append(m) or "La risposta.")
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {})
    searched = {"hits": [{"chunk_id": "gutenberg:1:0:1", "uid": "gutenberg:1", "book_id": 1, "text": "Il mercante tornò.",
                          "title": "Novelle", "lang": "it"}], "catalog": [], "facts": [], "timings": {}}
    text, changed = e.answer("Chi tornò?", searched, glossary=True)
    assert text == "La risposta." and changed is False
    assert any("Il mercante tornò." in str(m) for m in seen[0])


def test_the_on_arm_reads_the_glossary_and_the_off_arm_does_not(monkeypatch):
    """München's check (9160bf1, ON and OFF byte-identical): the ON arm's
    prompt carries the glossary's text, the OFF arm's does not."""
    from types import SimpleNamespace
    from app import llm, reading
    seen = []
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: seen.append(str(m)) or "R.")
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {0: reading.Year(1350, "x")})
    monkeypatch.setattr(reading, "lookup", lambda words, store=None: SimpleNamespace(version="v"))
    monkeypatch.setattr(reading, "apply", lambda text, y, found, use, lang=None:
                        (text.replace("huomo", "uomo"), [SimpleNamespace(source="wikisource-readings")]))
    searched = {"hits": [{"chunk_id": "gutenberg:1:0:1", "uid": "gutenberg:1", "book_id": 1,
                          "text": "Il huomo tornò.", "title": "Novelle", "lang": "it"}],
                "catalog": [], "facts": [], "timings": {}}
    _t, on = e.answer("Chi tornò?", searched, glossary=True)
    _t, off = e.answer("Chi tornò?", searched, glossary=False)
    assert on is True and off is False
    assert "Il uomo tornò." in seen[0] and "Il uomo tornò." not in seen[1]


def test_the_search_is_never_translated(monkeypatch):
    from app import retrieval
    asked = []
    monkeypatch.setattr(retrieval, "search", lambda con, q, **kw: asked.append(kw) or {"hits": [{"x": 1}], "catalog": []})
    got = e.best_passage(None, object(), "q", "gutenberg:5")
    assert asked[0]["translate"] is False and asked[0]["book_ids"] == {5} and got["catalog"] == []


def test_its_own_pairs_and_each_judge_over_all_answers_before_the_next(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app import collections as collections_mod, llm
    from tools.measure import reading_known_items as ki
    rec = {"uid": f"gutenberg:{1}", "lang": "it", "title": "Commedia",
           "sections": [["I", "x"], ["II", "y"], ["III", " ".join(f"Il re {i} entrò nella sala che non era più tempo."
                                                              for i in range(40))]]}
    monkeypatch.setattr(e, "pairs_of", lambda claims, name, span: ([(rec, "gutenberg:2"), (dict(rec, uid="gutenberg:3"),
                                                                    "gutenberg:4")], {"records_with_a_modern_version": 2,
                                                                                      "historical_in_span": 2}))
    monkeypatch.setattr(e, "texts_of", lambda name, uids: {u: dict(rec, uid=u) for u in uids})
    monkeypatch.setattr(collections_mod, "by_name", lambda n: SimpleNamespace(name=n))
    from app import reading
    monkeypatch.setattr(reading, "tables_held", lambda store=None: "wikisource-readings 3 abc")
    monkeypatch.setattr(e, "ro_connect", lambda c: None)
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: "Che cosa accadde nella sala?")
    monkeypatch.setattr(e, "best_passage", lambda con, col, q, uid: {"hits": [{"text": "t " + uid}], "catalog": []})
    monkeypatch.setattr(e, "align", lambda h, g, judge_model=None: 4.0)
    monkeypatch.setattr(e, "answer", lambda q, res, glossary: ("on" if glossary else "off", glossary))
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {0: reading.Year(1350, "x")})
    monkeypatch.setattr(e, "readings_in", lambda text, year: 1)
    order = []
    monkeypatch.setattr(e, "judge", lambda q, gold, cand, jm=None: order.append(jm) or
                        {"agreement": 4.0 if cand == "on" else 3.0, "contradictions": 0})
    out = tmp_path / "eval.jsonl"
    e.main(["--collection", "g", "--claims", str(tmp_path / "c"), "--out", str(out), "--weights-sha256", "a",
            "--judge-model", "louise-chat-command-r7b-q8", "--per-record", "1"])
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["pairs_before_sampling"] == {"records_with_a_modern_version": 2, "historical_in_span": 2}
    # every answer by the first judge, then every one by the second: two model switches at most
    assert order == [None] * 4 + ["louise-chat-command-r7b-q8"] * 4
    summary = lines[-1]["summary"]
    assert summary["judged"] == 2 and summary["agreement_on"] == 4.0 and summary["too_few"] is True



def test_it_refuses_without_reading_tables_and_says_why_when_nothing_was_judged(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace
    from app import collections as collections_mod, reading
    monkeypatch.setattr(reading, "tables_held", lambda store=None: "")
    assert e.main(["--collection", "g", "--claims", str(tmp_path / "c"), "--out", str(tmp_path / "o"),
                   "--weights-sha256", "a"]) == 2
    assert "REFUSED: no reading table is held" in capsys.readouterr().err
    monkeypatch.setattr(reading, "tables_held", lambda store=None: "wikisource-readings 3 abc")
    monkeypatch.setattr(collections_mod, "by_name", lambda n: SimpleNamespace(name=n))
    monkeypatch.setattr(e, "ro_connect", lambda c: None)
    monkeypatch.setattr(e, "pairs_of", lambda claims, name, span: ([], {"records_with_a_modern_version": 0,
                                                                        "historical_in_span": 0}))
    assert e.main(["--collection", "g", "--claims", str(tmp_path / "c"), "--out", str(tmp_path / "o"),
                   "--weights-sha256", "a"]) == 2
    err = capsys.readouterr().err
    assert "pairs before sampling" in err and "REFUSED: nothing was judged" in err



def test_pairing_keeps_claimed_records_light_and_reads_only_the_sampled_texts(monkeypatch, tmp_path):
    from tools.measure import reading_known_items as ki
    from app import reading
    claims = tmp_path / "c.jsonl"
    claims.write_text(json.dumps({"type": "same-work", "subject": "gutenberg:2", "object": "gutenberg:1"}) + "\n",
                      encoding="utf-8")
    big = {"uid": "gutenberg:1", "lang": "it", "title": "Commedia", "extra": {},
           "sections": [["Front matter", "LA COMMEDIA"], ["Canto I", "x" * 100_000]]}
    eng = {"uid": "gutenberg:2", "lang": "en", "title": "The Divine Comedy", "extra": {}, "sections": [["I", "y"]]}
    other = {"uid": "gutenberg:9", "lang": "en", "title": "Unclaimed", "extra": {}, "sections": [["I", "z" * 100_000]]}
    monkeypatch.setattr(ki, "records_of", lambda name, rng, lang="it": iter([big, eng, other]))
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {0: reading.Year(1310, "x")})
    pairs, before = e.pairs_of(claims, "g", (1200, 1900))
    assert before == {"claimed_records": 2, "records_with_a_modern_version": 1, "historical_in_span": 1}
    (light, modern), = pairs
    assert modern == "gutenberg:2" and len(json.dumps(light)) < 1000           # no text kept
    assert e.texts_of("g", {"gutenberg:1"})["gutenberg:1"]["sections"][1][1] == "x" * 100_000



def test_passages_without_a_word_the_tables_read_are_not_drawn_and_unchanged_answers_not_judged(monkeypatch, tmp_path):
    """The node at 9160bf1: 14 judged, ON == OFF byte for byte — random
    passages held no word the tables read."""
    from types import SimpleNamespace
    from app import collections as collections_mod, llm, reading
    text = " ".join(f"Il re {i} entrò nella sala che non era più tempo." for i in range(40))
    rec = {"uid": "gutenberg:1", "lang": "it", "title": "Commedia", "sections": [["I", "x"], ["II", "y"], ["III", text]]}
    plain = dict(rec, uid="gutenberg:3")
    monkeypatch.setattr(e, "pairs_of", lambda c, n, s: ([(rec, "gutenberg:2"), (plain, "gutenberg:4")], {}))
    monkeypatch.setattr(e, "texts_of", lambda name, uids: {"gutenberg:1": rec, "gutenberg:3": plain})
    monkeypatch.setattr(collections_mod, "by_name", lambda n: SimpleNamespace(name=n))
    monkeypatch.setattr(reading, "tables_held", lambda store=None: "wikisource-readings 3 abc")
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {0: reading.Year(1350, "x")})
    monkeypatch.setattr(e, "ro_connect", lambda c: None)
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: "Che cosa accadde nella sala?")
    monkeypatch.setattr(e, "best_passage", lambda con, col, q, uid: {"hits": [{"text": "t " + uid}], "catalog": []})
    monkeypatch.setattr(e, "align", lambda h, g, judge_model=None: 4.0)
    drawn = []
    monkeypatch.setattr(e, "readings_in", lambda t, y: 0 if "gutenberg:3" in drawn[-1:] else 2)
    import tools.measure.reading_known_items as ki
    real = ki.passage_of
    monkeypatch.setattr(ki, "passage_of", lambda r, rng: drawn.append(r["uid"]) or real(r, rng))
    calls = iter([("gold", False), ("on", False), ("off", False)] * 10)
    monkeypatch.setattr(e, "answer", lambda q, res, glossary: next(calls))
    judged = []
    monkeypatch.setattr(e, "judge", lambda q, gold, cand, jm=None: judged.append(cand) or
                        {"agreement": 3.0, "contradictions": 0})
    out = tmp_path / "eval.jsonl"
    e.main(["--collection", "g", "--claims", str(tmp_path / "c"), "--out", str(out), "--weights-sha256", "a",
            "--per-record", "1"])
    summary = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()][-1]["summary"]
    assert summary["records_without_a_passage_the_tables_read"] == 1      # gutenberg:3: no word read
    assert summary["answered"] == 1 and summary["judged"] == 0 and judged == []


def test_each_row_names_the_readings_the_glossary_applied(monkeypatch):
    from app import reading
    from app.refs.readings import Reading
    r = Reading(orig="huomo", reg="uomo", kind="replace", lemma="", gloss="", lang="it", period_from=1400,
                period_to=1899, method="m", source="wikisource-readings")
    monkeypatch.setattr(reading, "lookup", lambda words, store=None: None)
    monkeypatch.setattr(reading, "apply", lambda text, y, found, use, lang="it": (text, [r]))
    assert e.readings_used("Il huomo", reading.Year(1500, "x")) == [
        {"orig": "huomo", "kind": "replace", "reg": "uomo", "lemma": "", "gloss": "", "source": "wikisource-readings"}]



def test_the_passage_draw_reads_tlio_as_the_on_arm_does(monkeypatch):
    """The node at e133b0c: the draw ran under CHAT_TLIO's default (off) and
    found TLIO's words in no passage."""
    from types import SimpleNamespace
    from app import reading
    from app.refs.readings import Reading
    g = Reading(orig="talpe", reg="", kind="gloss", lemma="talpa", gloss="", lang="it", period_from=1100,
                period_to=1400, method="t", source="tlio")
    monkeypatch.setattr(reading, "lookup", lambda words, store=None: SimpleNamespace(by_form={"talpe": [g]}, version="v"))
    assert reading.CHAT_TLIO is False
    assert e.readings_in("le talpe", reading.Year(1300, "x")) == 1
    assert reading.CHAT_TLIO is False                                   # restored
