"""tools/chunker_mode.py — the CHUNKER MODE of the legal pilot (operator,
2026-11-14: "Build the chunker mode and send A"): arms cut the pool's records
in-process through the stages' own chunker path, embedded through the
collection's recipe and cached by text, searched by the app's own text index,
an exact vector scan, the reader's fusion and reranker, scored by key as
`legal_pilot run` scores — and compared, every arm with the first, per reading."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from app import chunking
from app.plugins.wikidata import chunker as wd
from tests.test_legal_pilot import CORPUS, L306_ID, QUESTIONS, built  # noqa: F401 — the fixture
from tools import chunker_mode as cm
from tools import legal_pilot as lp

SHA = "sha256:" + "cd" * 32
DIM = 64


@pytest.fixture(autouse=True)
def word_tokenizer():
    """One token per whitespace-separated word (tests/test_chunking_legal.py's)."""
    tok = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    wd.register(SHA, tok)
    yield tok
    wd._tokenizers.pop(SHA, None)


def legal(header: bool, outline: bool = False, max_tokens: int = 400) -> str:
    return json.dumps({"chunker": "legal_article", "version": 3,
                       "params": {"tokenizer": SHA, "max_tokens": max_tokens, "header": header,
                                  "outline": outline}})


RECIPE = SimpleNamespace(url="http://enc", api="openai", model="m", dim=DIM, ctx=8191,
                         query_instruction="query: ", document_instruction="passage: ")


class Encoder:
    """A bag of words hashed into DIM dimensions: deterministic, and close
    for texts that share words — enough for the dense leg to rank by
    content. Counts what it was asked."""

    def __init__(self):
        self.calls, self.texts = 0, 0

    def __call__(self, recipe, texts, *, query):
        self.calls += 1
        self.texts += len(texts)
        out = []
        for t in texts:
            v = np.zeros(DIM)
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
            out.append((v if v.any() else np.eye(DIM)[0]).tolist())
        return out


def pool_ids(held) -> list[int]:
    return [int(r[0]) for r in held.con.execute("SELECT native_id FROM documents")]


# ---- the arms ------------------------------------------------------------------------

def test_the_arms_are_layers_the_platform_accepts_and_two_alike_are_refused(built):
    c = built.collection
    shipped = cm.arm_of(c, "shipped")
    assert shipped.layer.chunker == chunking.AKN_ARTICLE, "normattiva's own cut"
    with pytest.raises(SystemExit, match="has no header"):
        cm.arm_of(c, "header-off")            # akn_article has no header to turn off
    with pytest.raises(SystemExit, match="cut the same"):
        cm.arms_of(c, ["shipped", "akn"])     # the collection already cuts with akn_article
    on = cm.arm_of(c, f"on={legal(True)}")
    assert on.layer.chunker == "legal_article" and on.layer.params["header"] is True
    with pytest.raises(SystemExit, match="two arms are named"):
        cm.arms_of(c, [f"x={legal(True)}", f"x={legal(False)}"])
    with pytest.raises(SystemExit, match="unknown chunker"):
        cm.arm_of(c, 'bad={"chunker": "nope"}')
    with pytest.raises(SystemExit):
        cm.arm_of(c, 'bad={"chunker": "legal_article", "params": {"max_tokens": 10}}')  # no tokenizer
    with pytest.raises(SystemExit, match="unknown arm"):
        cm.arm_of(c, "whatever")


def test_header_and_outline_presets_change_one_param_of_a_legal_layer(built, monkeypatch):
    from app import layers
    base = layers.Layer("legal_article", 3, "default",
                        params={"tokenizer": SHA, "max_tokens": 400, "header": True, "outline": True})
    monkeypatch.setattr(cm, "shipped_layer", lambda c: base)
    off = cm.arm_of(built.collection, "header-off").layer
    assert off.params == {**base.params, "header": False}
    assert cm.arm_of(built.collection, "outline-off").layer.params == {**base.params, "outline": False}
    assert cm.arm_of(built.collection, "akn").layer.chunker == chunking.AKN_ARTICLE


def test_the_cut_goes_through_the_stages_path_and_the_window_is_counted(built):
    ids = pool_ids(built)
    on, off = cm.arm_of(built.collection, f"on={legal(True)}"), cm.arm_of(built.collection, f"off={legal(False)}")
    with_header, cost_on = cm.cut(on, built, ids)
    bare, cost_off = cm.cut(off, built, ids)
    assert cost_on["records"] == cost_off["records"] == len(ids) and cost_on["records_missing"] == 0
    assert {c["chapter"] for c in with_header} == {c["chapter"] for c in bare}, "the labels are the record's"
    labelled = [c for c in with_header if " › " in c["chapter"]]
    assert labelled and all(c["text"].split("\n", 1)[0].endswith(c["chapter"]) for c in labelled), \
        "the header — title › label — leads every piece"
    assert not any(c["text"].split("\n", 1)[0].endswith(c["chapter"]) for c in bare if " › " in c["chapter"])
    assert cost_on["characters"] > cost_off["characters"]
    assert all(c["lang"] for c in with_header)
    # as the encoder reads it: counted, and an arm past the window refused
    count = lambda text: len(text.split())   # noqa: E731
    _, counted = cm.cut(off, built, ids, count=count, ctx=10_000)
    assert counted["over_window"] == 0 and counted["longest_tokens"] > 0 and counted["window"] == 10_000
    with pytest.raises(cm.OverWindow, match="exceed the encoder's window"):
        cm.cut(off, built, ids, count=count, ctx=3)


# ---- the vectors ---------------------------------------------------------------------

def test_the_cache_resumes_and_a_text_two_arms_share_is_embedded_once(tmp_path):
    enc = Encoder()
    cache = cm.VectorCache(tmp_path, RECIPE)
    a, sa = cm.embed(["uno due", "tre quattro"], cache=cache, recipe=RECIPE, query=False, embed_fn=enc,
                     log=lambda m: None)
    b, sb = cm.embed(["tre quattro", "cinque"], cache=cache, recipe=RECIPE, query=False, embed_fn=enc,
                     log=lambda m: None)
    assert (sa["embedded"], sb["embedded"]) == (2, 1) and enc.texts == 3, "the shared text once"
    assert np.allclose(a[1], b[0], atol=1e-2), "the same vector, from the cache"
    again = cm.VectorCache(tmp_path, RECIPE)
    assert len(again) == 3, "a new process finds every part"
    # a part whose keys were never written is not whole: ignored, embedded again
    (again.dir / "000099.npy").write_bytes(b"\x93NUMPY")
    assert len(cm.VectorCache(tmp_path, RECIPE)) == 3
    # the query side is another text, and another recipe another directory
    _, sq = cm.embed(["uno due"], cache=cache, recipe=RECIPE, query=True, embed_fn=enc, log=lambda m: None)
    assert sq["embedded"] == 1
    other = SimpleNamespace(**{**vars(RECIPE), "document_instruction": None})
    assert cm.VectorCache(tmp_path, other).dir != cache.dir


def test_the_shipped_arm_takes_the_collections_vectors_once_a_sample_reproduces_them(built, tmp_path):
    enc = Encoder()
    arm = cm.arm_of(built.collection, "shipped")
    chunks, _ = cm.cut(arm, built, pool_ids(built))
    held = {c["id"]: enc(RECIPE, [c["text"]], query=False)[0] for c in chunks}
    cache = cm.VectorCache(tmp_path / "a", RECIPE)
    got = cm.seed_stored(chunks, built.collection, cache=cache, recipe=RECIPE,
                         stored_fn=lambda ids, c: {i: held[i] for i in ids}, embed_fn=enc)
    assert got["stored"] == len({c["text"] for c in chunks}) and got["check"]["worst"] > 0.999
    before = enc.texts
    _, stats = cm.embed([c["text"] for c in chunks], cache=cache, recipe=RECIPE, query=False, embed_fn=enc,
                        log=lambda m: None)
    assert stats["embedded"] == 0 and enc.texts == before, "nothing re-embedded"
    rng = np.random.default_rng(1)
    with pytest.raises(SystemExit, match="not this encoder's"):
        cm.seed_stored(chunks, built.collection, cache=cm.VectorCache(tmp_path / "b", RECIPE), recipe=RECIPE,
                       stored_fn=lambda ids, c: {i: rng.normal(size=DIM).tolist() for i in ids}, embed_fn=enc)


# ---- the readings and the run ------------------------------------------------------------

def _coverage(held):
    cov = lp.coverage(held, CORPUS, QUESTIONS)
    return {int(k): lp.gold_from(v) for k, v in cov["gold"].items()}


def test_every_arm_reads_four_ways_scored_by_key_and_leaves_nothing_behind(built, tmp_path):
    gold = _coverage(built)
    enc = Encoder()
    reranked: list[str] = []

    def rerank(question, hits):
        reranked.append(question)
        return list(reversed(hits))

    arms = cm.arms_of(built.collection, ["shipped", f"header={legal(True)}"])
    work = tmp_path / "work"
    got = cm.run(built, QUESTIONS, gold, arms, pool_ids(built), k=5, workdir=work, recipe=RECIPE,
                 cache=cm.VectorCache(tmp_path / "v", RECIPE), embed_fn=enc, stored_fn=None,
                 rerank_fn=rerank, count_fn=None, log=lambda m: None)
    assert got["questions"] == 2 and set(got["arms"]) == {"shipped", "header"}
    for name, arm in got["arms"].items():
        assert set(cm.READINGS) <= set(arm), name
        assert arm["cost"]["chunks"] > 0 and arm["embedding"]["texts"] == arm["cost"]["chunks"]
        for reading in cm.READINGS:
            rows = arm[reading]["per_question"]
            assert [r["index"] for r in rows] == [0, 2] and arm[reading]["summary"]["questions_run"] == 2
    for arm in got["arms"].values():
        for reading in cm.READINGS:
            for row in arm[reading]["per_question"]:
                assert all(x in row["relevant"] or x < 0 for x in row["ranking"]), "gold by key, or a miss"
    assert len(reranked) == 4, "two questions, two arms: the fused list reranked each time"
    assert not any(work.glob("fts-*")), "every arm's throwaway index is removed"


def test_a_question_reaches_the_records_not_chunks_as_the_reader_fuses(built, tmp_path):
    arm = cm.arm_of(built.collection, f"on={legal(True, max_tokens=120)}")
    chunks, _ = cm.cut(arm, built, pool_ids(built))
    enc = Encoder()
    vecs = np.asarray(enc(RECIPE, [c["text"] for c in chunks], query=False), dtype=np.float32)
    search = cm.ArmSearch(arm, built.collection, chunks, vecs, tmp_path)
    try:
        q = np.asarray(enc(RECIPE, ["legge delega governo"], query=True)[0], dtype=np.float32)
        dense = search.dense_list(q, depth=2)
        assert len({h["book_id"] for h in dense}) == 2, "the leg stops at its depth in RECORDS"
        got = cm.readings(search, "legge delega governo", q, depth=5, rerank_fn=None)
        assert "reranked" not in got
        for reading in ("fts", "dense", "hybrid"):
            uids = [h["chunk_id"].rsplit(":", 2)[0] for h in got[reading]]
            assert len(uids) == len(set(uids)), f"{reading}: one entry per record (05 §5.1)"
    finally:
        search.close()


def test_a_hit_scores_by_its_own_label_against_the_gold_by_key(built):
    """`legal_pilot.run`'s scoring: a hit is (act, article) by the label its
    chunk carries; a second hit on the same article counts once; any other
    hit is a miss, never another question's gold."""
    gold = _coverage(built)
    by_pair = {(g.work, g.token): g.node_id for g in gold.values() if g.held}
    law = f"normattiva:{L306_ID}"
    arm = cm.arm_of(built.collection, "shipped")
    chunks, _ = cm.cut(arm, built, [int(L306_ID)])
    art5 = next(c for c in chunks if lp.article_token(c["chapter"]) == "5")
    other = next(c for c in chunks if lp.article_token(c["chapter"]) not in (None, "5"))
    hits = [{"chunk_id": other["id"], "chapter": other["chapter"]},
            {"chunk_id": art5["id"], "chapter": art5["chapter"]},
            {"chunk_id": art5["id"], "chapter": art5["chapter"]}]
    row, ranking, shown = cm.score(built, hits, {1}, by_pair, 10, [0])
    assert ranking[1] == 1 and ranking[0] < 0 and len(ranking) == 2, "the article once, by key"
    assert row["mrr@10"] == pytest.approx(0.5) and shown[1]["uid"] == law
    row, ranking, _ = cm.score(built, hits, {1}, by_pair, 1, [0])
    assert ranking == [ranking[0]] and ranking[0] < 0 and row["recall@1"] == 0.0, "the first k hits only"


def test_a_rerank_that_falls_back_is_withheld(monkeypatch, built):
    from app import collections as collections_mod, retrieval
    monkeypatch.setattr(collections_mod, "reranker_for", lambda c: SimpleNamespace(url="http://rr"))
    monkeypatch.setattr(retrieval, "_attach_heads", lambda con, c, hits: None)
    monkeypatch.setattr(retrieval, "_reranked", lambda q, hits, r, b, d: (hits, False, "timed out"))
    rr = cm.default_rerank(built, built.collection)
    with pytest.raises(cm.RerankWithheld, match="timed out"):
        rr("q", [{"chunk_id": "normattiva:1:0:1"}])
    monkeypatch.setattr(collections_mod, "reranker_for", lambda c: None)
    assert cm.default_rerank(built, built.collection) is None


# ---- the report -------------------------------------------------------------------------

def test_holm_holds_the_family_wise_error():
    assert cm.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert cm.holm([0.5]) == [0.5] and cm.holm([]) == []
    assert cm.holm([0.9, 0.8]) == pytest.approx([1.0, 1.0]), "never below an earlier step, never above 1"


def _run_file(arm, reading, scores, arms):
    rows = [{"index": i, "mrr@10": s, "recall@10": s, "ndcg@10": s} for i, s in enumerate(scores)]
    n = len(scores)
    return {"arm": arm, "legs": reading, "k": 20, "pool": "p", "exact": True, "split": "validation",
            "arms": arms, "collection": "normattiva", "cost": {"chunks": 10, "characters": 100},
            "summary": {"mrr@10": sum(scores) / n, "recall@10": sum(scores) / n, "ndcg@10": sum(scores) / n,
                        "questions_run": n, "seconds": None},
            "per_question": rows}


def test_the_report_compares_every_arm_with_the_runs_first_whatever_the_files_order():
    arms = ["shipped", "header-off", "akn"]
    runs = [_run_file("akn", "hybrid", [0.0, 0.5, 0.0, 0.5], arms),
            _run_file("shipped", "hybrid", [1.0, 0.5, 1.0, 0.5], arms),
            _run_file("header-off", "hybrid", [1.0, 0.5, 0.5, 0.5], arms)]
    rep = cm.report(runs)
    rows = rep["hybrid"]["rows"]
    assert [r["arm"] for r in rows] == arms and rows[0]["baseline"]
    akn = rows[2]["vs_baseline"]["mrr@10"]
    assert akn["a_minus_b"] == pytest.approx(-0.5) and akn["p_holm"] >= akn["p"]
    md = cm.report_markdown(rep)
    assert "## hybrid (4 questions)" in md and "| shipped |" in md and "baseline" in md
    with pytest.raises(SystemExit, match="two files of one arm"):
        cm.report(runs + [_run_file("akn", "hybrid", [0.0, 0.5, 0.0, 0.5], arms)])


# ---- the questions and the command ---------------------------------------------------------

def _dataset(out: Path, train: list | None = None) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(CORPUS), out / "corpus.parquet")
    pq.write_table(pa.Table.from_pylist(QUESTIONS), out / "questions-validation.parquet")
    if train is not None:
        pq.write_table(pa.Table.from_pylist(train), out / "questions-train.parquet")


def test_the_train_questions_follow_validation_and_runs_on_other_questions_are_not_compared(tmp_path):
    out = tmp_path / "d"
    _dataset(out, train=[{"question": "Altro", "relevant_doc_ids": [1]}])
    assert len(lp._load_dataset(out, "validation")[1]) == 4
    assert [q["question"] for q in lp._load_dataset(out, "all")[1]][-1] == "Altro", "validation keeps its index"
    assert len(lp._load_dataset(out, "train")[1]) == 1
    a = _run_file("shipped", "hybrid", [1.0], ["shipped"])
    b = {**a, "split": "all"}
    with pytest.raises(SystemExit, match="differ in split"):
        lp.compare(a, b)
    from tools import encoder_bakeoff
    assert "questions-train.parquet" in encoder_bakeoff.FILES, "`fetch` takes the train split too"


def test_the_command_pilots_then_runs_then_reports(built, tmp_path, monkeypatch, capsys):
    """The commands as the node runs them: a pilot first (measuring.md §12),
    refused without one, then one file per arm and reading, then the report."""
    import contextlib

    from app import collections as collections_mod, encoder_use
    out = tmp_path / "pilot"
    _dataset(out)
    (out / "runs").mkdir()
    (out / "runs" / "pool-p.json").write_text(json.dumps({"name": "p", "native_ids": pool_ids(built)}),
                                              encoding="utf-8")
    monkeypatch.setattr(lp, "_open", lambda name, root: (built.collection, built.con))
    monkeypatch.setattr(collections_mod, "recipe_for", lambda c: RECIPE)
    monkeypatch.setattr(cm, "default_embed", Encoder())
    monkeypatch.setattr(cm, "default_stored", lambda ids, c: {})
    monkeypatch.setattr(cm, "default_rerank", lambda held, c: (lambda q, hits: hits))
    monkeypatch.setattr(cm, "window_counter", lambda c, r: None)
    monkeypatch.setattr(encoder_use, "held", lambda *a, **k: contextlib.nullcontext())
    flags = ["chunker-run", "--out", str(out), "--collection", "normattiva", "--pool", "p", "--k", "5",
             "--arm", "shipped", "--arm", f"header={legal(True)}"]
    # the default arms refer to a legal layer; normattiva here cuts with akn_article
    assert lp.main(flags) == 2 and "no pilot read" in capsys.readouterr().err
    assert lp.main(flags + ["--pilot", "3"]) == 0
    said = capsys.readouterr().out
    tag = said.split("--tag ")[1].split()[0]
    pilot = (out / "runs" / "normattiva" / f"pilot-{tag}.md").read_text(encoding="utf-8")
    assert "# shipped — hybrid" in pilot and "# header — reranked" in pilot and QUESTIONS[0]["question"] in pilot
    assert not list((out / "runs").glob("*chunker-*.json")), "a pilot is not a run"
    assert lp.main(["pilot-ok", "--out", str(out), "--collection", "normattiva", "--tag", tag,
                    "--by", "operator", "--note", "read"]) == 0
    assert lp.main(flags) == 0
    files = sorted((out / "runs").glob("*chunker-*-pool-p.json"))
    assert len(files) == 2 * len(cm.READINGS)
    one = json.loads(files[0].read_text(encoding="utf-8"))
    assert one["arms"] == ["shipped", "header"] and one["exact"] is True and one["split"] == "validation"
    assert one["cost"]["chunks"] > 0 and one["layer"]["chunker"]["id"] in ("akn_article", "legal_article")
    capsys.readouterr()
    report = tmp_path / "report.md"
    assert lp.main(["chunker-report", *map(str, files), "--write", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    assert "## reranked" in text and "| shipped |" in text and "| header |" in text
    # the dry run cuts and counts, embeds nothing, and says an arm past the window
    monkeypatch.setattr(cm, "window_counter", lambda c, r: (lambda text: len(text.split())))
    monkeypatch.setattr(cm, "default_embed", lambda *a, **k: pytest.fail("a dry run embeds nothing"))
    capsys.readouterr()
    assert lp.main(flags + ["--dry-run"]) == 0
    said = capsys.readouterr().out
    assert '"over_window": 0' in said and '"longest_tokens"' in said and "header:" in said
    monkeypatch.setattr(collections_mod, "recipe_for", lambda c: SimpleNamespace(**{**vars(RECIPE), "ctx": 3}))
    assert lp.main(flags + ["--dry-run"]) == 0
    assert "REFUSED" in capsys.readouterr().out
    # a pool built from other questions than the run asks is refused
    (out / "runs" / "pool-p.json").write_text(json.dumps({"name": "p", "split": "all",
                                                          "native_ids": pool_ids(built)}), encoding="utf-8")
    with pytest.raises(SystemExit, match="built from the all questions"):
        lp.main(flags)
