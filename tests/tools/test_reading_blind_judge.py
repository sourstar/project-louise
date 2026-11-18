"""The operator's blind judge of TLIO's glosses in the AI summary (choice C,
2026-11-18): the page never says which answer is ON; scoring unblinds."""
from __future__ import annotations

import json
from types import SimpleNamespace

from tools.measure import reading_blind_judge as bj


def test_the_sign_test():
    assert bj.sign_test(0, 0) == 1.0
    assert bj.sign_test(5, 5) == 1.0
    assert round(bj.sign_test(9, 1), 4) == 0.0215


def test_scoring_unblinds_and_applies_the_rule():
    key = [{"on": "A"}, {"on": "B"}] * 15                       # 30 items
    choices = [{"id": k, "pick": key[k]["on"], "err_A": False, "err_B": False} for k in range(20)]
    choices += [{"id": k, "pick": "same", "err_A": False, "err_B": False} for k in range(20, 30)]
    got = bj.score(key, choices)
    assert (got["judged"], got["on_wins"], got["off_wins"], got["same"]) == (30, 20, 0, 10)
    assert got["tlio_glosses_may_return"] is True
    # ON with more errors fails the rule, however it wins
    choices[0] = {"id": 0, "pick": "A", "err_A": True, "err_B": False}
    assert bj.score(key, choices)["tlio_glosses_may_return"] is False
    # under 30 judged: too few
    assert bj.score(key, choices[:29])["too_few"] is True


def test_the_page_never_names_the_arms():
    items = [{"query": "Chi parla?", "title": "Commedia", "year": 1310, "passage": "Nel mezzo",
              "A": "risposta uno", "B": "risposta due"}]
    p = bj.page(items).lower()
    assert "risposta uno" in p and "risposta due" in p
    for word in ("tlio", "glossary", "gloss", '"on"', "off arm", "on arm"):
        assert word not in p


def test_make_writes_the_page_and_the_key_apart_and_refuses_under_30(monkeypatch, tmp_path, capsys):
    from app import collections as collections_mod, llm, reading
    from tools.measure import reading_known_items as ki, reading_store, reading_summary_eval as ev
    text = " ".join(f"Il re {i} entrò nella sala che non era più tempo." for i in range(40))
    recs = [{"uid": f"gutenberg:{i}", "lang": "it", "title": "Commedia",
             "sections": [["I", "x"], ["II", "y"], ["III", text]]} for i in range(3)]
    monkeypatch.setattr(reading_store, "use", lambda tsv, root=None: "wikisource-readings 3 a; tlio 3 b")
    monkeypatch.setattr(collections_mod, "by_name", lambda n: SimpleNamespace(name=n))
    monkeypatch.setattr(ev, "ro_connect", lambda c: None)
    monkeypatch.setattr(ki, "records_of", lambda name, rng, lang="it": iter(recs))
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {0: reading.Year(1310, "x")})
    monkeypatch.setattr(ev, "readings_in", lambda t, y: 2)
    monkeypatch.setattr(ev, "readings_used", lambda t, y: [{"orig": "talpe"}])
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: "Che cosa accadde nella sala?")
    monkeypatch.setattr(ev, "best_passage", lambda con, col, q, uid: {"hits": [{"text": "t " + uid}]})
    monkeypatch.setattr(ev, "answer", lambda q, res, glossary: ("ON" if glossary else "OFF", glossary))
    out = tmp_path / "j"
    assert bj.main(["make", "--collection", "g", "--out-dir", str(out), "--weights-sha256", "w",
                    "--readings-tsv", str(tmp_path / "t"), "--per-record", "1"]) == 2
    assert "under the 30" in capsys.readouterr().err
    key = json.loads((out / "key.json").read_text(encoding="utf-8"))
    page = (out / "judge.html").read_text(encoding="utf-8")
    assert len(key["items"]) == 3 and "tlio 3" in key["reading_tables"]
    assert "tlio" not in page.lower()                                     # the key stays out of the page
    for k, it in enumerate(key["items"]):
        assert it["on"] in ("A", "B")
