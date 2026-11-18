"""tools/measure/reading_known_items.py: the refusals of a written query,
and a passage cut at a sentence end."""
from __future__ import annotations

import random

from tools.measure import reading_known_items as k

PASSAGE = ("Messer Torello da Pavia, ritornando di Levante, fu da tutti tenuto morto; e la donna sua, "
           "volendo rimaritarsi, fece apparecchiare le nozze con grandissima festa.")


def test_a_query_is_refused_for_the_title_a_copied_run_or_its_length():
    assert k.refused("Chi era il mercante di Pavia creduto morto?", PASSAGE, "Il Decameron") is None
    assert k.refused("il decameron di boccaccio e Torello", PASSAGE, "Il Decameron") == "names the title"
    assert k.refused("fu da tutti tenuto morto", PASSAGE, "Il Decameron") == "copies 4 words of the passage"
    assert k.refused(" ".join(["parola"] * 30), PASSAGE, "X") == "longer than 25 words"
    assert k.refused("", PASSAGE, "X") == "empty"


def test_a_passage_is_a_sections_text_cut_at_a_sentence_end():
    rec = {"sections": [["I", "Breve."], ["II", ("Una frase lunga che continua. " * 40)]]}
    p = k.passage_of(rec, random.Random(1))
    assert 500 <= len(p) <= 900 and p.endswith(".")
    assert k.passage_of({"sections": [["I", "corto"]]}, random.Random(1)) is None


def test_the_set_records_its_writer_and_counts_its_strata(monkeypatch, tmp_path):
    import json
    from app import config, llm, reading
    recs = [{"uid": f"gutenberg:{i}", "lang": "it", "title": "Rime",
             "sections": [["I", "Una frase lunga che continua sul mercante. " * 30]]} for i in range(5)]
    monkeypatch.setattr(k, "records_of", lambda name, rng: iter(recs))
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {0: 1554})
    monkeypatch.setattr(config, "CHAT_MODEL", "qwen-test")
    seen = []
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: seen.append((temperature, m)) or
                        "Chi era il mercante?")
    out = tmp_path / "set.jsonl"
    assert k.main(["--stratum", "g:1100:1899", "--kept", "2", "--weights-sha256", "abc", "--out", str(out)]) == 0
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    head, body, tail = lines[0], lines[1:-1], lines[-1]
    assert head["chat_model"] == "qwen-test" and head["weights_sha256"] == "abc" and head["temperature"] == 0
    assert len(head["prompt_sha256"]) == 64 and head["strata"] == ["g:1100:1899"]
    assert len(body) == 2 and all(b["refused"] is None for b in body) and {t for t, _m in seen} == {0.0}
    assert tail == {"counts": {"g:1100:1899": {"kept": 2, "refused": 0}}}
    # the writer is given the passage alone: never the record's title
    assert all("Rime" not in m[0]["content"] and body[0]["passage"] in seen[0][1][0]["content"]
               for _t, m in seen)
