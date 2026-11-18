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
    from app import collections as collections_mod
    from app import llm, reading
    from tools.measure import reading_known_items as ki
    from tools.measure import reading_store
    from tools.measure import reading_summary_eval as ev
    text = " ".join(f"Il re {i} entrò nella sala che non era più tempo." for i in range(40))
    recs = [{"uid": f"gutenberg:{i}", "lang": "it", "title": "Commedia",
             "sections": [["I", "x"], ["II", "y"], ["III", text]]} for i in range(3)]
    monkeypatch.setattr(reading_store, "use", lambda tsv, root=None: "wikisource-readings 3 a; tlio 3 b")
    monkeypatch.setattr(collections_mod, "by_name", lambda n: SimpleNamespace(name=n))
    monkeypatch.setattr(ev, "ro_connect", lambda c: None)
    monkeypatch.setattr(ki, "records_of", lambda name, rng, lang="it": iter(recs))
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {0: reading.Year(1310, "x")})
    monkeypatch.setattr(ev, "readings_in", lambda t, y: 2)
    g = SimpleNamespace(orig="talpe", source="tlio", lemma="talpa", reg="")
    monkeypatch.setattr(bj, "arm_readings", lambda t, y, tlio: [g] if tlio else [])
    monkeypatch.setattr(llm, "chat", lambda m, temperature, max_tokens: "Che cosa accadde nella sala?")
    monkeypatch.setattr(ev, "best_passage", lambda con, col, q, uid: {"hits": [{"text": "t " + uid}]})
    monkeypatch.setattr(bj, "answer", lambda q, res, tlio: "ON [talpa]" if tlio else "OFF")
    out = tmp_path / "j"
    assert bj.main(["make", "--collection", "g", "--out-dir", str(out), "--weights-sha256", "w",
                    "--readings-tsv", str(tmp_path / "t"), "--per-record", "1"]) == 2
    assert "under the 30" in capsys.readouterr().err
    key = json.loads((out / "key.json").read_text(encoding="utf-8"))
    page = (out / "judge.html").read_text(encoding="utf-8")
    assert len(key["items"]) == 3 and "tlio 3" in key["reading_tables"]
    assert len(key["commit"].split("+")[0]) == 40 and key["commit_source"] == "checkout"
    assert key["items_with_a_tell"] == 3 and all(it["tell"] for it in key["items"])
    assert "tlio" not in page.lower()                                     # the key stays out of the page
    for k, it in enumerate(key["items"]):
        assert it["on"] in ("A", "B")



def _r(orig, source, lemma="", reg=""):
    from app.refs.readings import Reading
    return Reading(orig=orig, reg=reg, kind="replace" if reg else "gloss", lemma=lemma, gloss="", lang="it",
                   period_from=1100, period_to=1899, method="m", source=source)


def test_off_is_the_shipped_copy_and_on_adds_tlio_alone(monkeypatch):
    """Architecture, 2026-11-18: OFF keeps the year and the Wikisource
    replacements; ON differs by TLIO's glosses only."""
    from app import reading
    found = reading.Lookup()
    found.by_form = {"huomo": [_r("huomo", "wikisource-readings", reg="uomo")], "talpe": [_r("talpe", "tlio", "talpa")]}
    monkeypatch.setattr(reading, "lookup", lambda words, store=None: found)
    monkeypatch.setattr(reading, "years", lambda hits, collection=None: {0: reading.Year(1310, "x")})
    searched = {"hits": [{"chunk_id": "gutenberg:1:0:1", "uid": "gutenberg:1", "book_id": 1,
                          "text": "Il huomo e le talpe.", "title": "Commedia", "lang": "it"}],
                "catalog": [], "facts": [], "timings": {}}
    off = bj.prepared("Chi?", searched, False)["hits"][0]["text"]
    on = bj.prepared("Chi?", searched, True)["hits"][0]["text"]
    assert "uomo" in off and "huomo" not in off and "[talpa" not in off
    assert "uomo" in on and "[talpa" in on
    y = reading.Year(1310, "x")
    off_r, on_r = bj.arm_readings("Il huomo e le talpe.", y, False), bj.arm_readings("Il huomo e le talpe.", y, True)
    assert [x.orig for x in bj.tlio_only(on_r, off_r)] == ["talpe"]
    # all Wikisource: nothing TLIO adds, the item is not kept
    assert bj.tlio_only(bj.arm_readings("Il huomo.", y, True), bj.arm_readings("Il huomo.", y, False)) == []
    assert reading.CHAT_TLIO is False


def test_a_bracketed_lemma_in_an_answer_is_a_tell_and_scored_apart():
    assert bj.tells(["Il testo dice che abondava [abbondare] il grano.", "Altro."], {"abbondare"})
    assert not bj.tells(["Abbondare di grano.", "Altro."], {"abbondare"})
    key = [{"on": "A", "tell": k < 10} for k in range(30)]
    choices = [{"id": k, "pick": "A", "err_A": False, "err_B": False} for k in range(30)]
    got = bj.score(key, choices)
    assert got["judged"] == 30 and got["without_a_tell"]["judged"] == 20


def test_the_commit_from_the_checkout_the_env_or_nothing(monkeypatch):
    import subprocess
    sha, source = bj.commit_of()
    assert source == "checkout" and len(sha.split("+")[0]) == 40

    def no_git(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", no_git)
    monkeypatch.setenv(bj.COMMIT_ENV, "a" * 40)
    assert bj.commit_of() == ("a" * 40, "env")
    for bad in ("A" * 40, "a" * 39, "abc", "a" * 40 + " "  + "x"):
        monkeypatch.setenv(bj.COMMIT_ENV, bad)
        assert bj.commit_of() == ("", ""), bad
    monkeypatch.delenv(bj.COMMIT_ENV)
    assert bj.commit_of() == ("", "")


def test_make_refuses_when_no_commit_is_known(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bj, "commit_of", lambda: ("", ""))
    out = tmp_path / "j"
    assert bj.main(["make", "--collection", "g", "--out-dir", str(out), "--weights-sha256", "w",
                    "--readings-tsv", str(tmp_path / "t")]) == 2
    assert "no commit known" in capsys.readouterr().err and not out.exists()


def test_a_copy_untracked_inside_another_checkout_is_not_that_checkouts_commit(tmp_path, monkeypatch):
    """Architecture's review of #758: the tool extracted inside another
    checkout was recorded as that checkout's HEAD."""
    import importlib.util
    import shutil
    import subprocess
    repo = tmp_path / "other"
    repo.mkdir()
    for args in (["init", "-q"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
                                  "-m", "x"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    copy = repo / "reading_blind_judge.py"
    shutil.copy(bj.__file__, copy)
    spec = importlib.util.spec_from_file_location("bj_copy", copy)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv(mod.COMMIT_ENV, "b" * 40)
    assert mod.commit_of() == ("b" * 40, "env")
    monkeypatch.delenv(mod.COMMIT_ENV)
    assert mod.commit_of() == ("", "")

