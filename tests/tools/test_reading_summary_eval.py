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


def test_the_search_is_never_translated(monkeypatch):
    from app import retrieval
    asked = []
    monkeypatch.setattr(retrieval, "search", lambda con, q, **kw: asked.append(kw) or {"hits": [{"x": 1}], "catalog": []})
    got = e.best_passage(None, object(), "q", "gutenberg:5")
    assert asked[0]["translate"] is False and asked[0]["book_ids"] == {5} and got["catalog"] == []
