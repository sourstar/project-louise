"""THE CHUNKER MODE of the legal pilot (operator, 2026-11-14: "Build the
chunker mode and send A"; docs/notes/legal-chunking-retrieval.md §5).

`legal_pilot run` measures a COLLECTION: the chunks its worker cut, the
vectors its encoder made, the search the node serves. A chunker setting is
part of the space (invariant 1), so measuring three settings that way would
mean three new spaces, each with its gate, before the decision they exist to
inform. This mode measures the CUT instead, on the records a collection
already holds, with everything else the collection's own:

1. ARMS. Each arm is a `layers.Layer` — the collection's shipped layer, the
   same with `header` or `outline` off, `akn_article`, or any layer given as
   JSON — and cuts the pool's records through `layers._chunker_of`, the
   code path every stage cuts with, `[title, sections]` read as `Held` reads
   them (the stages' record).
2. VECTORS. Every chunk through the collection's SERVED recipe
   (`collections.recipe_for`: its encoder, its query instruction, its
   document instruction — `embeddings.embed_calibration` adds both, as a
   search and the worker do), cached by the sha256 of the text under the
   recipe (`VectorCache`): a text two arms share is embedded once (RULE 1).
   An arm whose layer IS the collection's takes the vectors the collection
   already holds for those chunks (`embeddings.vectors_by_chunk_id`),
   checked against a re-embedded sample (`encoder_bakeoff.check_stored`).
3. FOUR READINGS per question, each through the app's own code:
   - `fts`: a throwaway text index of the arm's chunks, written and searched
     by app/fts.py (its analyzers, stemmers and heading fields);
   - `dense`: an exact scan of the arm's vectors, deep enough for the same
     number of RECORDS as the text leg (05 §5.1);
   - `hybrid`: `retrieval._merge_rrf` over the two, the reader's fusion
     (the record is the unit, its best chunk carried);
   - `reranked`: `retrieval._reranked` over the hybrid list, under each
     record's head (`_attach_heads`), with the collection's reranker. A
     rerank that falls back is a withheld step: the run stops, nothing
     written (measuring.md §13).
4. SCORING as `legal_pilot.run` scores: each hit to (act, article) by the
   label its chunk carries, deduplicated, against the gold BY KEY; the
   metrics of `encoder_bakeoff.metrics_for`. One run file per arm and
   reading, in `run`'s format, so `legal_pilot compare` pairs them;
   `chunker-report` compares every arm with the first, per reading, with
   Holm's correction and the smallest detectable difference.

The candidate set is a POOL (`legal_pilot pool`), every record mapped to its
Work's version as the search would show it (`current_versions`), the same
for every arm: no arm meets distractors another does not. Nothing is
written to the collection.

STANDARD NOT ADOPTED, written down (CLAUDE.md): BEIR's evaluation over
re-chunked corpora, or a RAG bench (ragas, FlashRAG), would score chunks
against chunk-level qrels; the benchmark here labels ARTICLES, and the
reader's search fuses and reranks RECORDS, so the measurement must go
through that fusion — which no external harness reproduces.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import encoder_bakeoff as bakeoff  # noqa: E402

READINGS = ("fts", "dense", "hybrid", "reranked")
#: `run`'s count_fn: the collection's pinned tokenizer (`window_counter`)
_DEFAULT = object()
#: the arms A runs when none are named (operator, 2026-11-14)
DEFAULT_ARMS = ("shipped", "header-off", "akn")
PRESETS = ("shipped", "header-off", "outline-off", "akn")
#: the stored-vector check: a re-embedded sample of an arm that takes the
#: collection's vectors (bakeoff's bound: below it, not this encoder's)
STORED_CHECK = 32


@dataclass(frozen=True)
class Arm:
    name: str
    layer: Any                     # app.layers.Layer

    def manifest(self) -> dict:
        return self.layer.as_manifest()


def shipped_layer(collection):
    """The ONE layer the collection cuts with: an arm replaces it whole."""
    from app import layers
    got = layers.layers_for(collection)
    if len(got) != 1 or got[0].rule != "default":
        raise SystemExit(f"{collection.name} cuts with {len(got)} layers: the chunker mode compares "
                         f"one layer's cut, not a rule's split")
    return got[0]


def arm_of(collection, spec: str) -> Arm:
    """`shipped`, `header-off`, `outline-off`, `akn`, or `NAME={"chunker": …,
    "version": …, "params": {…}}` — a layer the platform would accept
    (`layers.check_params`)."""
    from app import chunking, layers
    name, _, body = spec.partition("=")
    name = name.strip()
    if not name:
        raise SystemExit(f"an arm needs a name: {spec!r}")
    if body:
        try:
            d = json.loads(body)
        except json.JSONDecodeError as e:
            raise SystemExit(f"arm {name}: not JSON ({e})") from None
        chunker = str(d.get("chunker") or "")
        if chunker not in chunking._CHUNKERS:
            raise SystemExit(f"arm {name}: unknown chunker {chunker!r}")
        params = dict(d.get("params") or {})
        try:
            layers.check_params(params, chunker=chunker, where=f"arm {name}")
        except layers.LayerError as e:
            raise SystemExit(str(e)) from None
        version = int(d.get("version") or chunking.chunk_version_for(chunker))
        return Arm(name, layers.Layer(chunker, version, "default", params=params))
    base = shipped_layer(collection)
    if name == "shipped":
        return Arm(name, base)
    if name in ("header-off", "outline-off"):
        if base.chunker != chunking.LEGAL_ARTICLE:
            raise SystemExit(f"arm {name}: {collection.name} cuts with {base.chunker}, which has no "
                             f"{name.split('-')[0]}")
        key = name.split("-")[0]
        return Arm(name, dataclasses.replace(base, params={**base.params, key: False}))
    if name == "akn":
        return Arm(name, layers.Layer(chunking.AKN_ARTICLE, chunking.AKN_CHUNK_VERSION, "default"))
    raise SystemExit(f"unknown arm {name!r}: one of {', '.join(PRESETS)}, or NAME=<layer JSON>")


def arms_of(collection, specs: Iterable[str]) -> list[Arm]:
    arms = [arm_of(collection, s) for s in specs]
    names: set[str] = set()
    cuts: dict[str, str] = {}
    for a in arms:
        if a.name in names:
            raise SystemExit(f"two arms are named {a.name!r}")
        names.add(a.name)
        key = json.dumps(a.manifest(), sort_keys=True)
        if key in cuts:
            raise SystemExit(f"arms {cuts[key]!r} and {a.name!r} cut the same: one of them measures nothing")
        cuts[key] = a.name
    return arms


# ---- the cut ---------------------------------------------------------------------

class OverWindow(SystemExit):
    """An arm whose chunks the encoder would truncate: not comparable
    (operator, 2026-11-08: a chunk truncated at the window "is not
    admissible")."""


def window_counter(collection, recipe) -> Callable[[str], int] | None:
    """Tokens of a document AS THE ENCODER READS IT — the space's document
    instruction and the chunk — in the tokenizer the collection's layer
    pins; None where it pins none (the count cannot be made exactly)."""
    tok_sha = (shipped_layer(collection).params or {}).get("tokenizer")
    if not tok_sha:
        return None
    from app import chunking_legal
    from app.plugins.wikidata import chunker as wd
    tok = wd.tokenizer_for(tok_sha)
    prefix = getattr(recipe, "document_instruction", None) or ""
    return lambda text: chunking_legal._count(tok, prefix + text)


def cut(arm: Arm, held, native_ids: Iterable[int], *, count: Callable[[str], int] | None = None,
        ctx: int | None = None) -> tuple[list[dict], dict[str, Any]]:
    """The arm's chunks of every pool record, in record order, each with the
    record's language; and what was cut (records, records missing, chunks,
    characters, the chunker's token estimate). With `count` and `ctx`, every
    chunk is counted as the encoder reads it: the longest, and how many
    exceed the window — an arm with any is refused (`OverWindow`), since the
    encoder would truncate it and its vectors would not be the text's."""
    from app import layers
    fn = layers._chunker_of(arm.layer)
    plugin = held.collection.plugin
    chunks: list[dict] = []
    missing = 0
    for nid in sorted({int(i) for i in native_ids}):
        row, doc = held._row_and_doc(f"{plugin}:{nid}")
        if doc is None:
            missing += 1
            continue
        lang = (row["lang"] if "lang" in row.keys() else None) or "und"
        for c in fn(row["uid"], nid, doc.sections, title=doc.title):
            c["lang"] = lang
            chunks.append(c)
    cost: dict[str, Any] = {"records": len({c["book_id"] for c in chunks}), "records_missing": missing,
                            "chunks": len(chunks), "characters": sum(len(c["text"]) for c in chunks),
                            "tokens_estimated": sum(int(c.get("tokens") or 0) for c in chunks)}
    if count is not None and ctx:
        sizes = [count(c["text"]) for c in chunks]
        over = sum(1 for n in sizes if n > ctx)
        cost.update({"tokens": sum(sizes), "longest_tokens": max(sizes, default=0), "window": ctx,
                     "over_window": over})
        if over:
            raise OverWindow(f"arm {arm.name}: {over} of {len(chunks)} chunks exceed the encoder's window "
                             f"of {ctx} tokens (longest {max(sizes)}): the encoder would truncate them, "
                             f"so the arm is not comparable (operator, 2026-11-08: not admissible)")
    return chunks, cost


# ---- the vectors -------------------------------------------------------------------

def recipe_key(recipe) -> str:
    """What the encoder computes: its endpoint and model, and the two
    instructions the app adds to what it reads."""
    d = {k: getattr(recipe, k, None) for k in ("url", "api", "model", "dim", "query_instruction",
                                                "document_instruction")}
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


def text_key(text: str, *, query: bool) -> str:
    return hashlib.sha256((("q\0" if query else "d\0") + text).encode("utf-8")).hexdigest()


class VectorCache:
    """Vectors by the sha256 of the text they were made from, one directory
    per recipe; written in parts, each part's vectors first and its keys
    last, so a part whose keys exist is whole (invariant 2: an interrupted
    run resumes where it stopped). Kept as float16, the dtype the
    collections store (`EmbedRecipe.dtype = "f16"`)."""

    def __init__(self, root: Path, recipe) -> None:
        self.dir = Path(root) / recipe_key(recipe)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._rows: dict[str, tuple[int, int]] = {}
        self._parts: list[np.ndarray] = []
        for keys in sorted(self.dir.glob("*.keys.json")):
            vec = keys.with_name(keys.name.replace(".keys.json", ".npy"))
            if not vec.is_file():
                continue
            arr = np.load(vec)
            names = json.loads(keys.read_text(encoding="utf-8"))
            if len(names) != len(arr):
                continue
            p = len(self._parts)
            self._parts.append(arr)
            for r, k in enumerate(names):
                self._rows.setdefault(k, (p, r))

    def __contains__(self, key: str) -> bool:
        return key in self._rows

    def __len__(self) -> int:
        return len(self._rows)

    def add(self, keys: list[str], vectors: np.ndarray) -> None:
        if not keys:
            return
        arr = np.asarray(vectors, dtype=np.float16)
        n = len(self._parts)
        while (self.dir / f"{n:06d}.keys.json").exists():
            n += 1
        vec = self.dir / f"{n:06d}.npy"
        tmp = self.dir / f"{n:06d}.tmp.npy"
        np.save(tmp, arr)
        tmp.replace(vec)
        (self.dir / f"{n:06d}.keys.json").write_text(json.dumps(keys), encoding="utf-8")
        p = len(self._parts)
        self._parts.append(arr)
        for r, k in enumerate(keys):
            self._rows.setdefault(k, (p, r))

    def matrix(self, keys: list[str]) -> np.ndarray:
        out = [self._parts[p][r] for p, r in (self._rows[k] for k in keys)]
        return np.asarray(out, dtype=np.float32) if out else np.zeros((0, 0), dtype=np.float32)


def default_embed(recipe, texts: list[str], *, query: bool) -> list[list[float]]:
    from app import embeddings
    return embeddings.embed_calibration(recipe, texts, query=query)


def embed(texts: list[str], *, cache: VectorCache, recipe, query: bool,
          embed_fn: Callable | None = None, batch: int = bakeoff.SHARD,
          log: Callable[[str], None] = print) -> tuple[np.ndarray, dict[str, Any]]:
    """Each text's vector, from the cache or the encoder, in `texts`' order."""
    embed_fn = embed_fn or default_embed      # looked up when called
    keys = [text_key(t, query=query) for t in texts]
    todo: dict[str, str] = {}
    for k, t in zip(keys, texts):
        if k not in cache and k not in todo:
            todo[k] = t
    t0 = time.perf_counter()
    pending = list(todo.items())
    for start in range(0, len(pending), batch):
        part = pending[start:start + batch]
        vecs = embed_fn(recipe, [t for _, t in part], query=query)
        cache.add([k for k, _ in part], np.asarray(vecs, dtype=np.float32))
        log(f"  embedded {min(start + batch, len(pending))}/{len(pending)}"
            f" {'queries' if query else 'chunks'}")
    seconds = time.perf_counter() - t0
    return cache.matrix(keys), {"texts": len(texts), "unique": len(set(keys)),
                                "embedded": len(pending), "seconds": round(seconds, 1)}


def default_stored(chunk_ids: list[str], collection) -> dict[str, list]:
    from app import embeddings
    got: dict[str, list] = {}
    for i in range(0, len(chunk_ids), 5000):
        got.update(embeddings.vectors_by_chunk_id(chunk_ids[i:i + 5000], collection))
    return got


def seed_stored(chunks: list[dict], collection, *, cache: VectorCache, recipe,
                stored_fn: Callable | None = None, embed_fn: Callable | None = None,
                check: int = STORED_CHECK) -> dict[str, Any]:
    """An arm that cuts as the collection cuts takes the collection's own
    vectors for its chunks (RULE 1), once a re-embedded sample shows they
    are this recipe's (`encoder_bakeoff.check_stored`, refused below its
    bound). A chunk id names its record's version, its place and its
    chunker's version, so equal ids are equal texts (invariant 3)."""
    stored_fn, embed_fn = stored_fn or default_stored, embed_fn or default_embed
    wanted = [c for c in chunks if text_key(c["text"], query=False) not in cache]
    got = stored_fn([c["id"] for c in wanted], collection)
    have = [c for c in wanted if c["id"] in got]
    if not have:
        return {"stored": 0}
    n = min(check, len(have))
    at = sorted(np.random.default_rng(0).choice(len(have), size=n, replace=False).tolist())
    stored = np.asarray([got[have[i]["id"]] for i in at], dtype=np.float32)
    fresh = np.asarray(embed_fn(recipe, [have[i]["text"] for i in at], query=False), dtype=np.float32)
    worst, median = bakeoff.check_stored(stored, fresh)
    keys, vecs = [], []
    for c in have:
        k = text_key(c["text"], query=False)
        if k not in keys:
            keys.append(k)
            vecs.append(got[c["id"]])
    cache.add(keys, np.asarray(vecs, dtype=np.float32))
    return {"stored": len(keys), "check": {"n": n, "worst": round(worst, 6), "median": round(median, 6)}}


# ---- the search ----------------------------------------------------------------------

class ArmSearch:
    """One arm's chunks searched as the reader's search searches a collection:
    a throwaway text index written by app/fts.py, and an exact scan of the
    vectors. The index lives under `workdir`, under the arm's own name."""

    def __init__(self, arm: Arm, collection, chunks: list[dict], vectors: np.ndarray, workdir: Path):
        from app import fts
        self.chunks = chunks
        root = Path(workdir) / f"fts-{arm.name}"
        self.stub = dataclasses.replace(collection, name=f"{collection.name}~{arm.name}", fts_dir=root)
        by_record: dict[int, tuple[str, list[dict]]] = {}
        for c in chunks:
            by_record.setdefault(int(c["book_id"]), (c["lang"], []))[1].append(c)
        fts.index_books([(lang, nid, chs) for nid, (lang, chs) in by_record.items()], collection=self.stub)
        self.groups = fts.existing_groups(self.stub)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True) if len(vectors) else vectors
        self.matrix = (vectors / np.where(norms == 0, 1, norms)).astype(np.float32) if len(vectors) else vectors

    def text_lists(self, question: str, depth: int) -> list[list[dict]]:
        from app import fts
        lists = [fts.search_records(g, question, depth, collection=self.stub) for g in self.groups]
        return [x for x in lists if x]

    def dense_list(self, qvec: np.ndarray, depth: int) -> list[dict]:
        """The chunks by cosine, best first, until `depth` records are held —
        the vector leg's depth in records (05 §5.1, app/leg_depth.py)."""
        if not len(self.matrix):
            return []
        q = qvec / (np.linalg.norm(qvec) or 1.0)
        sims = self.matrix @ q.astype(np.float32)
        out: list[dict] = []
        records: set[int] = set()
        for i in np.argsort(-sims, kind="stable"):
            c = self.chunks[int(i)]
            out.append({"chunk_id": c["id"], "book_id": int(c["book_id"]), "chapter": c.get("chapter") or "",
                        "lang": c["lang"], "text": c["text"], "score": float(sims[int(i)]), "source": "dense"})
            records.add(int(c["book_id"]))
            if len(records) >= depth:
                break                    # the depth-th record's best chunk is in
        return out

    def close(self) -> None:
        import shutil

        from app import fts
        fts.close_all()
        shutil.rmtree(self.stub.fts_dir, ignore_errors=True)


def readings(search: ArmSearch, question: str, qvec: np.ndarray, *, depth: int,
             rerank_fn: Callable | None) -> dict[str, list[dict]]:
    """The four readings of one question: each leg alone and the two fused,
    as `retrieval` fuses them, and the fused list reranked."""
    from app import retrieval
    text = search.text_lists(question, depth)
    dense = search.dense_list(qvec, depth)
    own = set(range(len(text)))                 # the question as typed: every text list
    out = {"fts": retrieval._merge_rrf(text, depth, own=own),
           "dense": retrieval._merge_rrf([dense] if dense else [], depth),
           "hybrid": retrieval._merge_rrf([*text, *([dense] if dense else [])], depth, own=own)}
    if rerank_fn is not None:
        out["reranked"] = rerank_fn(question, [dict(h) for h in out["hybrid"]])
    return out


class RerankWithheld(RuntimeError):
    """The reranker kept the fused order: not the reading it claims to be."""


def default_rerank(held, collection) -> Callable | None:
    """The collection's reranker over a fused list, as `retrieval` runs it:
    each record's head attached, its top `RERANK_TOP_N` judged. None where
    the collection has no reranker (the reading is then not made)."""
    from app import collections as collections_mod, retrieval
    recipe = collections_mod.reranker_for(collection)
    if recipe is None:
        return None
    depth = retrieval.RERANK_TOP_N

    def rerank(question: str, hits: list[dict]) -> list[dict]:
        retrieval._attach_heads(held.con, collection, hits[:depth])
        ranked, ok, why = retrieval._reranked(question, hits, recipe, retrieval._rerank_budget_s(depth), depth)
        if not ok:
            raise RerankWithheld(f"the reranker kept the fused order: {why or 'no reason given'}")
        return ranked
    return rerank


# ---- the scoring -------------------------------------------------------------------

def score(held, hits: list[dict], relevant: set[int], by_pair: dict[tuple[str, str], int], k: int,
          synthetic: list[int]) -> tuple[dict[str, float], list[int], list[dict[str, str]]]:
    """`legal_pilot.run`'s scoring of one ranked list: each hit to (act,
    article) by its chunk's label, the first of each pair kept, the gold by
    key; the label is the arm's own (`chapter`), which the seq of another
    arm's cut could not name."""
    from app import retrieval
    from tools import legal_pilot as lp
    ranking: list[int] = []
    seen: list[tuple[str, str]] = []
    shown: list[dict[str, str]] = []
    for h in hits[:k]:                   # the first k hits, as `run` reads the reader's answer
        uid = retrieval.record_of(h)
        seq = lp._seq_of(h.get("chunk_id"))
        work = held.work_of(uid)
        token = lp.article_token(h.get("chapter") or "") if work else None
        pair = (work or uid, token or f"chunk{seq}")
        if pair in seen:
            continue
        seen.append(pair)
        shown.append({"uid": uid, "label": str(h.get("chapter") or pair[1])})
        if pair in by_pair:
            ranking.append(by_pair[pair])
        else:
            synthetic[0] -= 1
            ranking.append(synthetic[0])
    return bakeoff.metrics_for(ranking, relevant, (1, 3, 5, 10, 20)), ranking, shown


# ---- the run -------------------------------------------------------------------------

def run(held, questions: list[dict[str, Any]], gold: dict, arms: list[Arm], native_ids: Iterable[int], *,
        k: int, workdir: Path, recipe, cache: VectorCache, embed_fn: Callable | None = None,
        stored_fn: Callable | None = None, rerank_fn: Callable | None = None,
        only: Iterable[int] | None = None, count_fn: Any = _DEFAULT,
        log: Callable[[str], None] = print) -> dict[str, Any]:
    """Every arm over every covered question: `{arm: {"cost", "embedding",
    reading: {"summary", "per_question"}}}`. Arms one after the other, each
    index removed before the next is built (memory is one arm's)."""
    from app import retrieval
    depth = max(k, retrieval.RERANK_TOP_N, 20)
    by_pair = {(g.work, g.token): g.node_id for g in gold.values() if g.held}
    wanted = set(only) if only is not None else None
    covered: list[tuple[int, dict, set[int]]] = []
    for i, q in enumerate(questions):
        relevant = {int(x) for x in q["relevant_doc_ids"] if int(x) in gold and gold[int(x)].held}
        if relevant and (wanted is None or i in wanted):
            covered.append((i, q, relevant))
    ids = sorted({int(i) for i in native_ids})
    qvecs, qstats = embed([q["question"] for _, q, _ in covered], cache=cache, recipe=recipe, query=True,
                          embed_fn=embed_fn, log=log)
    shipped = json.dumps(shipped_layer(held.collection).as_manifest(), sort_keys=True)
    out: dict[str, Any] = {"questions": len(covered), "query_embedding": qstats, "arms": {}}
    # every arm cut and counted FIRST: an arm the encoder would truncate stops
    # the run before anything is embedded
    count = window_counter(held.collection, recipe) if count_fn is _DEFAULT else count_fn
    ctx = getattr(recipe, "ctx", None)
    cuts = {}
    for arm in arms:
        cuts[arm.name] = cut(arm, held, ids, count=count, ctx=ctx)
        log(f"arm {arm.name}: {json.dumps(arm.manifest(), sort_keys=True)} — {cuts[arm.name][1]}")
    for arm in arms:
        chunks, cost = cuts.pop(arm.name)
        seeded = {"stored": 0}
        if stored_fn is not None and json.dumps(arm.manifest(), sort_keys=True) == shipped:
            seeded = seed_stored(chunks, held.collection, cache=cache, recipe=recipe, stored_fn=stored_fn,
                                 embed_fn=embed_fn)
        vectors, dstats = embed([c["text"] for c in chunks], cache=cache, recipe=recipe, query=False,
                                embed_fn=embed_fn, log=log)
        search = ArmSearch(arm, held.collection, chunks, vectors, workdir)
        per: dict[str, list[dict]] = {r: [] for r in READINGS}
        totals: dict[str, dict[str, float]] = {r: {} for r in READINGS}
        seconds: list[float] = []
        synthetic = [0]
        try:
            for (i, q, relevant), qvec in zip(covered, qvecs):
                t0 = time.perf_counter()
                got = readings(search, q["question"], qvec, depth=depth, rerank_fn=rerank_fn)
                seconds.append(time.perf_counter() - t0)
                for reading, hits in got.items():
                    row, ranking, shown = score(held, hits, relevant, by_pair, k, synthetic)
                    for key, value in row.items():
                        totals[reading][key] = totals[reading].get(key, 0.0) + value
                    per[reading].append({"index": i, "question": q["question"], "relevant": sorted(relevant),
                                         "ranking": ranking, "missed": sorted(relevant - set(ranking)),
                                         "hits": shown, **row})
        finally:
            search.close()
        n = len(covered)
        arm_out: dict[str, Any] = {"layer": arm.manifest(), "cost": cost,
                                   "embedding": {**dstats, **seeded}, "seconds": _timing(seconds)}
        for reading in READINGS:
            if not per[reading]:
                continue
            summary = {key: value / n for key, value in totals[reading].items()} if n else {}
            summary["questions_run"] = n
            arm_out[reading] = {"summary": summary, "per_question": per[reading]}
        out["arms"][arm.name] = arm_out
        del vectors, search
    return out


def _timing(seconds: list[float]) -> dict[str, float | None]:
    from tools import legal_pilot as lp
    return lp.timing(seconds)


# ---- the report ----------------------------------------------------------------------

def holm(pvalues: list[float]) -> list[float]:
    """Holm's step-down adjustment (Holm, Scand. J. Statist. 1979): the
    family-wise error held at the level whatever the number of arms."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def report(runs: list[dict[str, Any]], metrics: Iterable[str] = ("mrr@10", "recall@10", "ndcg@10")) -> dict:
    """Every arm against the FIRST (its baseline), per reading: each metric's
    difference, interval, p, Holm-adjusted p across the arms, and the
    smallest difference the questions could show; each arm's cost beside."""
    from tools import legal_pilot as lp
    metrics = tuple(metrics)
    by_reading: dict[str, list[dict]] = {}
    for r in runs:
        by_reading.setdefault(r["legs"], []).append(r)
    out: dict[str, Any] = {}
    for reading, rs in by_reading.items():
        # the run's own arm order (`arms`), never the files' names: a
        # timestamped name sorts `akn` before `shipped`
        order = rs[0].get("arms") or []
        rs = sorted(rs, key=lambda r: order.index(r["arm"]) if r["arm"] in order else len(order))
        if len({r["arm"] for r in rs}) != len(rs):
            raise SystemExit(f"{reading}: two files of one arm; give each arm's file once")
        base, others = rs[0], rs[1:]
        rows = [{"arm": base["arm"], "baseline": True, "cost": base.get("cost"),
                 **{m: base["summary"].get(m) for m in metrics}}]
        compared = [lp.compare(o, base, metrics) for o in others]
        for m in metrics:
            adj = holm([c[m]["p"] for c in compared if m in c]) if compared else []
            for c, p in zip([c for c in compared if m in c], adj):
                c[m]["p_holm"] = p
        for o, c in zip(others, compared):
            rows.append({"arm": o["arm"], "cost": o.get("cost"),
                         **{m: o["summary"].get(m) for m in metrics},
                         "vs_baseline": {m: c[m] for m in metrics if m in c}})
        out[reading] = {"questions": compared[0]["questions"] if compared else base["summary"].get("questions_run"),
                        "rows": rows}
    return out


def report_markdown(rep: dict, metrics: Iterable[str] = ("mrr@10", "recall@10", "ndcg@10")) -> str:
    metrics = tuple(metrics)
    lines = ["# Chunker mode: every arm against the first", ""]
    for reading, block in rep.items():
        lines += [f"## {reading} ({block['questions']} questions)", "",
                  "| arm | chunks | characters | " + " | ".join(metrics) + " | Δ vs first (95 % CI), p Holm, ±detectable |",
                  "|---|---|---|" + "---|" * len(metrics) + "---|"]
        for row in block["rows"]:
            cost = row.get("cost") or {}
            vals = " | ".join(f"{row[m]:.4f}" if row.get(m) is not None else "—" for m in metrics)
            if row.get("baseline"):
                delta = "baseline"
            else:
                parts = []
                for m in metrics:
                    v = (row.get("vs_baseline") or {}).get(m)
                    if v:
                        parts.append(f"{m} {v['a_minus_b']:+.4f} [{v['interval95'][0]:+.4f}, "
                                     f"{v['interval95'][1]:+.4f}] p {v.get('p_holm', v['p']):.4f} "
                                     f"±{v['detectable80']:.4f}")
                delta = "; ".join(parts)
            lines.append(f"| {row['arm']} | {cost.get('chunks', '—')} | {cost.get('characters', '—')} | "
                         f"{vals} | {delta} |")
        lines.append("")
    return "\n".join(lines)


def tag(arms: list[Arm], *, k: int, pool_name: str, split: str) -> str:
    """The settings a pilot reading is valid for: the arms' layers, k, the
    pool, the questions, and the code (this file, the chunkers, Ask's)."""
    import re

    from tools.measure import chat_eval
    h = hashlib.sha256()
    for a in arms:
        h.update(a.name.encode() + json.dumps(a.manifest(), sort_keys=True).encode())
    for f in [Path(__file__), ROOT / "app" / "chunking.py", ROOT / "app" / "chunking_legal.py",
              ROOT / "app" / "layers.py", ROOT / "app" / "fts.py", ROOT / "app" / "retrieval.py"]:
        h.update(f.read_bytes())
    raw = (f"chunker-{h.hexdigest()[:12]}-k{k}-pool-{pool_name}-q-{split}"
           f"-code-{chat_eval.ask_code()[7:19]}")
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)


def workdir_for(out: Path) -> Path:
    path = Path(out) / "chunker"
    path.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="index-", dir=path))
