"""tools/chunker_languages.py — the per-language chunker test on LEMUR
(operator, 2026-11-13: "Build the per-language test on LEMUR"): the parallel
acts as the questions, their bodies read into sections by the eurlex plugin's
own reader, every arm cut, embedded and searched as the chunker mode does it,
in every language with one encoder; and whether an arm's effect differs by
language."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from app.plugins.wikidata import chunker as wd
from tests.test_legal_pilot import (
    built,  # noqa: F401 — the fixture: a held collection to search as
)
from tools import chunker_languages as cl
from tools import chunker_mode as cm

SHA = "sha256:" + "ef" * 32
DIM = 64
RECIPE = SimpleNamespace(url="http://enc", api="openai", model="m", dim=DIM, ctx=8191,
                         query_instruction="query: ", document_instruction="passage: ")
HEADING = {"it": "Articolo", "fr": "Article", "de": "Artikel"}
DECIDES = {"it": "LA COMMISSIONE HA ADOTTATO", "fr": "LA COMMISSION A ADOPTÉ", "de": "DIE KOMMISSION HAT ERLASSEN"}
TOPICS = ["garofano", "pesticida", "rifiuto", "acqua", "aria", "suolo"]


@pytest.fixture(autouse=True)
def word_tokenizer():
    tok = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    wd.register(SHA, tok)
    yield tok
    wd._tokenizers.pop(SHA, None)


class Encoder:
    """Bag of words hashed into DIM dimensions; states one run."""

    def __init__(self, run: str = "run-a"):
        self.texts, self.run = 0, run

    def __call__(self, recipe, texts, *, query, runs=None):
        self.texts += len(texts)
        if runs is not None:
            runs.add(self.run)
        out = []
        for t in texts:
            v = np.zeros(DIM)
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
            out.append((v if v.any() else np.eye(DIM)[0]).tolist())
        return out


def body(lang: str, i: int, *, articles: int = 3, broken: bool = False) -> str:
    word = f"{TOPICS[i]}{lang}"
    parts = [f"{DECIDES[lang]}:", f"considerando {word} e altre cose generali {lang}."]
    for a in range(1, articles + 1):
        head = f"{HEADING[lang]} {a}" if not (broken and a == 2) else f"{HEADING[lang]} zwei"
        parts += [head, " ".join([word] * (3 + a) + [f"comune{lang}"] * 30 + [f"norma{a}"] * 2) + "."]
    return "\n".join(parts)


def lemur_dir(root: Path, lang: str, *, extra: bool = False, broken: int | None = None) -> Path:
    d = root / f"lemur-{lang}"
    d.mkdir(parents=True)
    (d / "SOURCE.json").write_text(json.dumps({"suite": f"lemur-{lang}"}), encoding="utf-8")
    laws = [f"3200{i}D000{i}" for i in range(len(TOPICS))] + (["32099R0001"] if extra else [])
    docs, queries = [], []
    for i, law in enumerate(laws):
        text = body(lang, i % len(TOPICS), broken=(i == broken))
        docs.append({"_id": law, "title": "", "text": text})
        queries.append({"_id": f"q:{law}", "text": f"DECISIONE {TOPICS[i % len(TOPICS)]}{lang} del 2007"})
    for name, rows in (("corpus.jsonl", docs), ("queries.jsonl", queries)):
        (d / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return d


@pytest.fixture
def sets(tmp_path):
    return [lemur_dir(tmp_path, "it", extra=True), lemur_dir(tmp_path, "fr"), lemur_dir(tmp_path, "de", broken=4)]


BASE = {"tokenizer": SHA, "max_tokens": 400}


# ---- the set ---------------------------------------------------------------------------

def test_the_questions_are_the_parallel_acts_read_into_sections_by_the_eurlex_reader(sets):
    loaded = dict(cl.read_set(p) for p in sets)
    acts, said = cl.parallel(loaded)
    assert said == {"parallel": 6, "left_out": {"it": 1, "fr": 0, "de": 0}}
    assert [a.law for a in acts["it"]] == [a.law for a in acts["de"]], "one index per act in every language"
    first = acts["fr"][0]
    assert [label for label, _ in first.sections] == ["Preamble", "Article 1", "Article 2", "Article 3"]
    assert first.articles == 3 and "garofanofr" in first.sections[1][1]
    found = cl.structure_found(acts)
    assert found["it"]["share"] == 1.0 and found["de"]["acts_short"] == 1, "the heading the reader missed, said"
    with pytest.raises(SystemExit, match="not a LEMUR language"):
        (sets[0] / "SOURCE.json").write_text(json.dumps({"suite": "jurifindit"}), encoding="utf-8")
        cl.read_set(sets[0])


def test_the_arms_cut_by_structure_or_by_windows_and_never_write_the_title():
    arms = cl.arms_of(["structure", "structure-32", "window-16"], BASE)
    assert [a.layer.chunker for a in arms] == ["legal_article", "legal_article", "fixed"]
    assert arms[0].layer.params == {**BASE, "header": False, "outline": False}
    assert arms[1].layer.params["max_tokens"] == 32 and arms[2].whole and not arms[0].whole
    with pytest.raises(SystemExit, match="read the answer"):
        cl.arm_of('h={"chunker": "legal_article", "params": {"tokenizer": "' + SHA + '", "max_tokens": 400, '
                  '"header": true}}', BASE)
    with pytest.raises(SystemExit, match="cut the same"):
        cl.arms_of(["structure", "s2=" + json.dumps({"chunker": "legal_article", "version": 3,
                                                     "params": {**BASE, "header": False, "outline": False}})], BASE)
    with pytest.raises(SystemExit, match="unknown arm"):
        cl.arm_of("whatever", BASE)
    with pytest.raises(SystemExit, match="at least 16"):
        cl.arm_of("structure-8", BASE)


def test_the_cut_is_counted_per_language_and_a_tighter_window_splits_more(sets):
    acts, _ = cl.parallel(dict(cl.read_set(p) for p in sets))
    structure, small, window = cl.arms_of(["structure", "structure-32", "window-16"], BASE)
    chunks, cost = cl.cut(structure, acts["it"], "it")
    assert {c["chapter"] for c in chunks} >= {"Articolo 1", "Articolo 3"} and all(c["lang"] == "it" for c in chunks)
    assert cost["chunks_per_section"] == 1.0 and cost["window"] is None
    _, tight = cl.cut(small, acts["it"], "it")
    assert tight["chunks_per_section"] > 1.0, "the cap splits the articles"
    whole, wcost = cl.cut(window, acts["it"], "it")
    assert wcost["sections"] == len(acts["it"]) and not any(c["chapter"] for c in whole), "no structure"
    count = lambda text: len(text.split())
    _, counted = cl.cut(structure, acts["de"], "de", count=count, ctx=10_000)
    assert counted["over_window"] == 0 and counted["longest_tokens"] > 0
    with pytest.raises(cm.OverWindow, match=r"structure \(de\)"):
        cl.cut(structure, acts["de"], "de", count=count, ctx=5)
    # every (language, arm) on threads: the same costs as one at a time
    together = cl.cut_all(acts, [structure, window], count=count, ctx=10_000, workers=4)
    assert together[("de", "structure")] == counted and set(together) == {(lang, a) for lang in acts
                                                                          for a in ("structure", "window-16")}
    with pytest.raises(cm.OverWindow):
        cl.cut_all(acts, [structure], count=count, ctx=5, workers=2)


# ---- the run ------------------------------------------------------------------------------

def test_every_language_and_arm_reads_four_ways_scored_by_act(sets, built, tmp_path):
    acts, _ = cl.parallel(dict(cl.read_set(p) for p in sets))
    arms = cl.arms_of(["structure", "window-16"], BASE)
    reranked = []

    def rerank(q, hits):
        reranked.append(q)
        return hits
    work = tmp_path / "work"
    got = cl.run(acts, arms, k=10, workdir=work, recipe=RECIPE, cache=cm.VectorCache(tmp_path / "v", RECIPE),
                 collection=built.collection, embed_fn=Encoder(), rerank_fn=rerank, log=lambda m: None)
    assert set(got) == {"it", "fr", "de"}
    for block in got.values():
        assert block["questions"] == 6 and set(block["arms"]) == {"structure", "window-16"}
        for arm in block["arms"].values():
            for reading in cm.READINGS:
                rows = arm[reading]["per_question"]
                assert [r["index"] for r in rows] == list(range(6))
                assert all(r["relevant"] == [r["index"]] for r in rows)
            assert arm["dense"]["summary"]["mrr@10"] > 0.5, "the bag of words finds its act"
    assert len(reranked) == 3 * 2 * 6
    assert not any(work.rglob("fts-*")), "every index removed"
    only = cl.run(acts, arms[:1], k=10, workdir=tmp_path / "work2", recipe=RECIPE, cache=cm.VectorCache(tmp_path / "v", RECIPE),
                  collection=built.collection, embed_fn=Encoder(), only=cl.pilot_of(acts, 2), log=lambda m: None)
    assert only["fr"]["questions"] == 2


def test_a_difference_by_language_is_tested_with_the_act_as_the_block():
    rng = np.random.default_rng(3)
    same = np.repeat(rng.normal(0.05, 0.2, size=(80, 1)), 3, axis=1) + rng.normal(0, 0.01, size=(80, 3))
    assert cl.by_language(same, trials=2000) > 0.05
    shifted = same.copy()
    shifted[:, 2] -= 0.3
    assert cl.by_language(shifted, trials=2000) < 0.01
    assert cl.by_language(same[:, :1]) == 1.0


def _file(lang, arm, reading, scores, arms=("structure", "window-512")):
    rows = [{"index": i, "mrr@10": s, "recall@10": s, "ndcg@10": s} for i, s in enumerate(scores)]
    n = len(scores)
    return {"set": "lemur", "lang": lang, "arm": arm, "legs": reading, "arms": list(arms), "k": 20,
            "pool": "lemur-parallel", "split": "test", "exact": True, "collection": "eu", "tag": "t", "code": "c",
            "runs": {"document": "r", "query": "r"}, "stored": False, "parallel": n,
            "structure_found": {lang: {"articles": 3, "most_found": 3, "share": 1.0, "acts_short": 0,
                                       "acts_without_articles": 0}},
            "cost": {"chunks": 10, "characters": 100},
            "summary": {"mrr@10": sum(scores) / n, "recall@10": sum(scores) / n, "ndcg@10": sum(scores) / n,
                        "questions_run": n}, "per_question": rows}


def test_the_report_says_whether_the_hypothesis_holds():
    rng = np.random.default_rng(5)
    base = rng.uniform(0.3, 1.0, size=120)
    worse = np.clip(base - 0.1, 0, 1)
    runs = []
    for lang in ("de", "fr", "it"):
        for reading in ("hybrid", "reranked"):
            runs += [_file(lang, "structure", reading, base.tolist()),
                     _file(lang, "window-512", reading, worse.tolist())]
    got = cl.across(runs, trials=2000)
    assert got["verdict"]["holds"] and got["readings"]["reranked"]["same_best"] == 3
    md = cl.report_markdown(runs)
    assert "the hypothesis holds" in md and "### de" in md and "| fr |" in md
    assert "Per-language layers worth considering: **no**" in md and not got["verdict"]["per_language_layers"]
    assert "title TWICE" not in md, "the legal pilot's notes are not LEMUR's"
    # one language where the window wins: the effect differs by language
    flipped = [dict(r) if r["lang"] != "de" or r["arm"] != "window-512"
               else _file("de", "window-512", r["legs"], np.clip(base + 0.2, 0, 1).tolist()) for r in runs]
    got = cl.across(flipped, trials=2000)
    assert got["verdict"]["differs_by_language"] == ["window-512"] and not got["verdict"]["holds"]
    assert got["verdict"]["per_language_layers"]
    assert "Per-language layers worth considering: **yes**" in cl.report_markdown(flipped)
    assert got["readings"]["reranked"]["best"]["de"] == "window-512"
    with pytest.raises(SystemExit, match="parallel acts"):
        cl.across([r if r["lang"] != "fr" else {**r, "per_question": r["per_question"][:-1]} for r in runs])


# ---- the command --------------------------------------------------------------------------

def test_the_command_pilots_then_runs_then_reports(sets, built, tmp_path, monkeypatch, capsys):
    import contextlib

    from app import collections as collections_mod
    from app import encoder_use
    from tools import legal_pilot as lp
    out = tmp_path / "out"
    monkeypatch.setattr(lp, "_open", lambda name, root: (built.collection, built.con.__class__(":memory:")))
    monkeypatch.setattr(collections_mod, "recipe_for", lambda c: RECIPE)
    monkeypatch.setattr(cm, "default_embed", Encoder())
    monkeypatch.setattr(cm, "pin_runs", lambda c, recipe, log=print: cm.Runs("run-a", "run-a"))
    monkeypatch.setattr(cm, "default_rerank", lambda held, c, **kw: (lambda q, hits: hits))
    monkeypatch.setattr(encoder_use, "held", lambda *a, **k: contextlib.nullcontext())
    flags = ["run", "--sets", *map(str, sets), "--collection", "normattiva", "--out", str(out),
             "--tokenizer", SHA, "--max-tokens", "400", "--arm", "structure", "--arm", "window-16", "--k", "10"]
    assert cl.main(flags + ["--dry-run"]) == 0
    said = capsys.readouterr().out
    assert "6 parallel acts" in said and "de: structure found" in said and "'acts_short': 1" in said
    assert cl.main(flags) == 2 and "no pilot read" in capsys.readouterr().err
    assert cl.main(flags + ["--pilot", "2"]) == 0
    tag = capsys.readouterr().out.split("--tag ")[1].split()[0]
    pilot = (out / "runs" / "lemur" / f"pilot-{tag}.md").read_text(encoding="utf-8")
    assert "## de — window-16 — reranked" in pilot and "GOLD" in pilot
    assert cl.main(["pilot-ok", "--out", str(out), "--tag", tag, "--by", "operator", "--note", "read"]) == 0
    assert cl.main(flags) == 0
    files = sorted((out / "runs").glob("*-lemur-*.json"))
    assert len(files) == 3 * 2 * len(cm.READINGS)
    one = json.loads(files[0].read_text(encoding="utf-8"))
    assert one["parallel"] == 6 and one["left_out"]["it"] == 1 and one["runs"]["document"] == "run-a"
    capsys.readouterr()
    report = tmp_path / "report.md"
    assert cl.main(["report", *map(str, files), "--write", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    assert "## Verdict (reranked mrr@10" in text and "## The structure the reader found" in text
    assert "| de |" in text and "differs by language" in text


def test_an_app_older_than_the_tool_is_named_at_once_not_found_in_a_traceback(tmp_path, monkeypatch):
    """München, 2026-11-14: the dry runs died on the node's older app with
    `ImportError: cannot import name 'rendition'`, three times each. The run
    now says which module, from which commit, before reading anything."""
    assert cl.missing_app() == [], "this checkout has what the tool needs"
    old = (("app.plugins.eurlex.no_such_module", "_sections", "abc1234, a later parser"),
           ("app.plugins.eurlex.rendition", "no_such_name", "def5678, a later reader"))
    assert cl.missing_app(old) == ["app.plugins.eurlex.no_such_module (from abc1234, a later parser)",
                                   "app.plugins.eurlex.rendition.no_such_name (from def5678, a later reader)"]
    monkeypatch.setattr(cl, "NEEDS", old[:1])
    with pytest.raises(SystemExit, match="lacks app.plugins.eurlex.no_such_module .from abc1234"):
        cl.main(["run", "--sets", str(tmp_path / "nowhere"), "--collection", "x", "--out", str(tmp_path / "o")])
    assert not (tmp_path / "o").exists(), "nothing read, nothing written"
