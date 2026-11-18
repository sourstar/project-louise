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
