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

    def __init__(self, run: str = "run-a"):
        self.calls, self.texts, self.run = 0, 0, run

    def __call__(self, recipe, texts, *, query, runs=None):
        self.calls += 1
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


def _space(exactness: str = "exact", tolerance: str = "0.9999"):
    """A collection as `seed_stored` reads it: the stored rows come from
    `stored_fn`, the rule from its Codex's space."""
    return SimpleNamespace(name="normattiva",
                           codex=SimpleNamespace(space=SimpleNamespace(exactness=exactness, tolerance=tolerance)))


def test_the_shipped_arm_takes_only_identical_texts_held_to_the_spaces_rule(built, tmp_path):
    """München's review of #621: a stored vector is taken only where the
    stored row holds the arm's TEXT byte for byte (a chunk id does not name
    the title a header writes), and only once a re-embedded sample gives
    the stored bytes back in an exact space, or the space's tolerance in a
    near one; the seeds are never cached, so a run without `--stored` cannot
    read them."""
    enc = Encoder()
    arm = cm.arm_of(built.collection, "shipped")
    chunks, _ = cm.cut(arm, built, pool_ids(built))
    held = {c["id"]: (c["text"], np.asarray(enc(RECIPE, [c["text"]], query=False)[0])) for c in chunks}
    texts = [c["text"] for c in chunks]
    lone = next(c for c in chunks if texts.count(c["text"]) == 1)        # a text no other chunk has
    held[lone["id"]] = ("another title\n" + lone["text"], held[lone["id"]][1])   # same id, another text
    stored = lambda ids, c: {i: held[i] for i in ids if i in held}
    seeds, got = cm.seed_stored(chunks, _space(), recipe=RECIPE, stored_fn=stored, embed_fn=enc,
                                log=lambda m: None)
    assert got["stored_text_differs"] == 1 and cm.text_key(lone["text"], query=False) not in seeds
    assert got["stored"] == len(seeds) == len(set(texts)) - 1
    assert got["check"]["bytes_equal"] == got["check"]["n"] and "bytes" in got["check"]["rule"]
    cache = cm.VectorCache(tmp_path / "v", RECIPE)
    before = enc.texts
    _, stats = cm.embed([c["text"] for c in chunks], cache=cache, recipe=RECIPE, query=False, embed_fn=enc,
                        seeds=seeds, log=lambda m: None)
    assert stats["from_stored"] == len(chunks) - 1 and stats["embedded"] == 1 and enc.texts == before + 1
    assert len(cache) == 1, "only what the encoder made is cached, never a seed"
    _, again = cm.embed([c["text"] for c in chunks], cache=cm.VectorCache(tmp_path / "v", RECIPE), recipe=RECIPE,
                        query=False, embed_fn=enc, log=lambda m: None)
    assert again["embedded"] == len({c["text"] for c in chunks}) - 1, "a run without seeds embeds them"
    # an exact space: one bit off is another run's vector — nothing seeded
    nudged = {i: (t, (v + 1e-9).astype(np.float64)) for i, (t, v) in held.items()}
    seeds, got = cm.seed_stored(chunks, _space(), recipe=RECIPE, embed_fn=enc, log=lambda m: None,
                                stored_fn=lambda ids, c: {i: nudged[i] for i in ids})
    assert seeds == {} and "stored_refused" in got and got["check"]["bytes_equal"] < got["check"]["n"]
    # a near space admits it at its tolerance, and refuses below it
    seeds, got = cm.seed_stored(chunks, _space("near"), recipe=RECIPE, embed_fn=enc, log=lambda m: None,
                                stored_fn=lambda ids, c: {i: nudged[i] for i in ids})
    assert seeds and "tolerance" in got["check"]["rule"]
    rng = np.random.default_rng(1)
    seeds, got = cm.seed_stored(chunks, _space("near"), recipe=RECIPE, embed_fn=enc, log=lambda m: None,
                                stored_fn=lambda ids, c: {i: (held[i][0], rng.normal(size=DIM)) for i in ids})
    assert seeds == {} and "stored_refused" in got
    # no Codex, no rule: nothing seeded
    seeds, got = cm.seed_stored(chunks, SimpleNamespace(name="x"), recipe=RECIPE, embed_fn=enc,
                                stored_fn=stored, log=lambda m: None)
    assert seeds == {} and "no Codex" in got["stored_refused"]


def test_the_runs_are_pinned_and_another_run_stops_the_measurement(tmp_path, monkeypatch):
    runs = cm.Runs("run-a", "run-q")
    asked = []

    def answer(recipe, texts, *, query, runs=None):
        asked.append(query)
        runs.add("run-q" if query else "run-a")
        return [[1.0] * DIM for _ in texts]
    monkeypatch.setattr(cm, "default_embed", answer)
    fn = cm.pinned_embed(runs)
    assert len(fn(RECIPE, ["a"], query=False)) == 1 and len(fn(RECIPE, ["q"], query=True)) == 1
    monkeypatch.setattr(cm, "default_embed", Encoder(run="run-b"))
    with pytest.raises(cm.RunChanged, match="not the pinned run"):
        fn(RECIPE, ["a"], query=False)
    # the cache's directory names the space and both runs
    a = cm.VectorCache(tmp_path, RECIPE, space_id="s1", runs=runs).dir
    assert a != cm.VectorCache(tmp_path, RECIPE, space_id="s1", runs=cm.Runs("run-b", "run-q")).dir
    assert a != cm.VectorCache(tmp_path, RECIPE, space_id="s2", runs=runs).dir
    other = SimpleNamespace(**{**vars(RECIPE), "query_model": "another"})
    assert a != cm.VectorCache(tmp_path, other, space_id="s1", runs=runs).dir


def test_the_runs_are_validated_before_anything_is_embedded(monkeypatch):
    from app import run_check
    from app.hold import runs as runs_mod
    with pytest.raises(SystemExit, match="validates no runs"):
        cm.pin_runs(SimpleNamespace(name="old", codex=None), RECIPE, log=lambda m: None)
    codex = SimpleNamespace(space=SimpleNamespace(query=SimpleNamespace(encoder=None)))
    c = SimpleNamespace(name="normattiva", codex=codex)
    monkeypatch.setattr(run_check, "applies", lambda c: True)
    monkeypatch.setattr(run_check, "answering_run", lambda recipe, query=False: "run-q" if query else "run-d")
    guarded = []
    monkeypatch.setattr(run_check, "guard", lambda root, name, codex, recipe, answered, **kw:
                        guarded.append((sorted(answered), kw["role"])))
    assert cm.pin_runs(c, RECIPE, log=lambda m: None) == cm.Runs("run-d", "run-q")
    assert guarded == [(["run-d"], "document"), (["run-q"], "document")], "a symmetric space: one role"

    def failed(*a, **k):
        raise runs_mod.RunUnvalidated("FAILED the gate", "run-d", failed=True)
    monkeypatch.setattr(run_check, "guard", failed)
    with pytest.raises(SystemExit, match="not measured — FAILED the gate"):
        cm.pin_runs(c, RECIPE, log=lambda m: None)


def test_the_arms_cut_the_record_the_embed_stage_reads(built, monkeypatch):
    """The stage cuts `[title, sections]` from the collection's fresh record
    (app/parallel.py `_one`): so do the arms, the parse only where there is
    no record (München, 2026-11-13)."""
    from app import record_source
    arm = cm.arm_of(built.collection, f"on={legal(True)}")
    ids = pool_ids(built)
    parsed, cost = cm.cut(arm, built, ids)
    assert cost["records_from_records"] == 0
    monkeypatch.setattr(record_source, "record_for",
                        lambda c, row: {"title": "TITOLO DEL RECORD",
                                        "sections": built._row_and_doc(row["uid"])[1].sections})
    recorded, cost = cm.cut(arm, built, ids)
    assert cost["records_from_records"] == len(ids)
    assert any("TITOLO DEL RECORD" in c["text"] for c in recorded)
    assert not any("TITOLO DEL RECORD" in c["text"] for c in parsed)


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
    assert not any(work.glob("cut-*")), "every arm's cut file is taken back and removed"


def test_memory_is_one_arms_worth(built, tmp_path, monkeypatch):
    """The pilot's OOM kills (2026-11-14, 8 GB, at the moment its first arm's
    228k vectors were complete): four arms' chunks held at once, every pool
    record parsed and cached, every cache part in RAM, and two full copies of
    the arm's matrix while it was normalised. Each, bounded."""
    from app import plugin_io, record_source
    enc = Encoder()
    # the cache's parts are memory-mapped, on reload and once written
    cache = cm.VectorCache(tmp_path / "v", RECIPE)
    m, _ = cm.embed(["uno due", "tre quattro"], cache=cache, recipe=RECIPE, query=False, embed_fn=enc,
                    log=lambda m: None)
    assert all(isinstance(part, np.memmap) for part in cache._parts)
    again = cm.VectorCache(tmp_path / "v", RECIPE)
    assert all(isinstance(part, np.memmap) for part in again._parts)
    keys = [cm.text_key(t, query=False) for t in ("tre quattro", "uno due")]
    got = again.matrix(keys)
    assert got.dtype == np.float32 and got.flags["C_CONTIGUOUS"] and np.allclose(got, m[::-1])
    # the search normalises the arm's matrix in place: no second array
    arm = cm.arm_of(built.collection, "shipped")
    chunks, _ = cm.cut(arm, built, pool_ids(built))
    vecs = np.asarray(enc(RECIPE, [c["text"] for c in chunks], query=False), dtype=np.float32)
    search = cm.ArmSearch(arm, built.collection, chunks, vecs, tmp_path / "w")
    try:
        assert search.matrix is vecs and np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-5)
    finally:
        search.close()
    # the cut parses only what no record holds, and caches nothing on the held collection
    built._records.clear()
    monkeypatch.setattr(record_source, "record_for",
                        lambda c, row: {"title": "T", "sections": [("Art. 1", "testo")]})
    monkeypatch.setattr(plugin_io, "canonical_doc", lambda *a, **k: pytest.fail("parsed though a record holds it"))
    _, cost = cm.cut(arm, built, pool_ids(built))
    assert cost["records_from_records"] == len(pool_ids(built)) and built._records == {}


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
    rows = rep["readings"]["hybrid"]["rows"]
    assert [r["arm"] for r in rows] == arms and rows[0]["baseline"]
    akn = rows[2]["vs_baseline"]["mrr@10"]
    assert akn["a_minus_b"] == pytest.approx(-0.5) and akn["p_holm"] >= akn["p"]
    md = cm.report_markdown(rep)
    assert "## hybrid (4 questions)" in md and "| shipped |" in md and "baseline" in md
    assert "title TWICE" in md and "bias toward the challengers" in md, "read with: said on the page"
    with pytest.raises(SystemExit, match="two files of one arm"):
        cm.report(runs + [_run_file("akn", "hybrid", [0.0, 0.5, 0.0, 0.5], arms)])
    # files of another measurement are refused, whatever else matches
    for key, value in (("tag", "other"), ("code", "sha256:x"), ("runs", {"document": "b"}), ("stored", True)):
        stray = {**_run_file("header-off", "hybrid", [1.0, 0.5, 0.5, 0.5], arms), key: value}
        with pytest.raises(SystemExit, match=f"differ in {key}"):
            cm.report(runs[:2] + [stray])


def test_the_decision_is_the_rule_stated_before_the_run():
    """One measure, one margin, one-sided, Holm across the decided arms:
    keep when the baseline is not worse by the margin, drop when the arm is
    better by it, undecided otherwise — and on validation, undecided goes
    on to every question."""
    arms = ["shipped", "header-off", "akn"]
    n = 200
    base = [0.5 + 0.3 * ((i * 7) % 5 - 2) / 2 for i in range(n)]
    jitter = [0.04 * (((i * 13) % 7) - 3) / 3 for i in range(n)]
    same = [b + j for b, j in zip(base, jitter)]                       # mean difference 0, small spread
    better = [b + 0.15 + j for b, j in zip(base, jitter)]              # better by far more than 0.02
    runs = [_run_file("shipped", "reranked", base, arms), _run_file("header-off", "reranked", same, arms),
            _run_file("akn", "reranked", better, arms)]
    rule = cm.decision(runs, margin=0.02)
    got = {r["arm"]: r for r in rule["rows"]}
    assert got["header-off"]["verdict"] == "keep" and got["header-off"]["upper95"] < 0.02
    assert got["akn"]["verdict"] == "drop" and got["akn"]["lower95"] > 0.02
    assert got["akn"]["p_drop_holm"] >= got["akn"]["p_drop"], "Holm across the decided arms"
    # a wide spread on few questions: undecided, and on validation the next step is every question
    noisy = [_run_file("shipped", "reranked", [0.0, 1.0, 0.0, 1.0], arms),
             _run_file("header-off", "reranked", [1.0, 0.0, 0.0, 1.0], arms)]
    one = cm.decision(noisy, margin=0.02)["rows"][0]
    assert one["verdict"] == "undecided" and "--split all" in one["next"]
    noisy = [{**r, "split": "all"} for r in noisy]
    assert "stated before the run" in cm.decision(noisy, margin=0.02)["rows"][0]["next"]
    # only the arms asked about; the page says the rule
    only = cm.decision(runs, margin=0.02, decide=["header-off"])
    assert [r["arm"] for r in only["rows"]] == ["header-off"]
    with pytest.raises(SystemExit, match="not an arm"):
        cm.decision(runs, decide=["outline-off"])
    md = cm.report_markdown(cm.report(runs, margin=0.02))
    assert "## Decision: reranked mrr@10, margin 0.02" in md and "**keep**" in md and "**drop**" in md
    assert cm.report(runs, margin=None)["decision"] is None


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
    monkeypatch.setattr(cm, "pin_runs", lambda c, recipe, log=print: cm.Runs("run-a", "run-a"))
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
    assert one["runs"] == {"document": "run-a", "query": "run-a"} and one["stored"] is False
    assert "-vec-fresh-" in one["tag"] and one["summary"]["builder_share"] is None, "a pool without lists"
    assert all(json.loads(f.read_text(encoding="utf-8"))["embedding"]["from_stored"] == 0 for f in files), \
        "by default every arm fresh, on the one run"
    # `--stored` is another measurement: its own pilot first
    assert lp.main(flags + ["--stored"]) == 2 and "-vec-stored-" in capsys.readouterr().err
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
    # pinned to one run, answered by another (an engine swapped behind the
    # url): it stops, and nothing is written; a new run has its own cache
    monkeypatch.setattr(collections_mod, "recipe_for", lambda c: RECIPE)
    monkeypatch.setattr(cm, "window_counter", lambda c, r: None)
    monkeypatch.setattr(cm, "default_embed", Encoder(run="run-b"))
    monkeypatch.setattr(cm, "pin_runs", lambda c, recipe, log=print: cm.Runs("run-c", "run-c"))
    before = len(list((out / "runs").glob("*chunker-*.json")))
    with pytest.raises(cm.RunChanged):
        lp.main(flags)
    assert len(list((out / "runs").glob("*chunker-*.json"))) == before
    # a pool built from other questions than the run asks is refused — here and by `run`
    (out / "runs" / "pool-p.json").write_text(json.dumps({"name": "p", "split": "all",
                                                          "native_ids": pool_ids(built)}), encoding="utf-8")
    with pytest.raises(SystemExit, match="built from the all questions"):
        lp.main(flags)
    with pytest.raises(SystemExit, match="built from the all questions"):
        lp.main(["run", "--out", str(out), "--collection", "normattiva", "--pool", "p"])
