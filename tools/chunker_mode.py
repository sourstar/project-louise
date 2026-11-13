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
   search and the worker do), under ONE RUN per side, pinned before anything
   is embedded and validated for the space as a write's would be
   (`pin_runs`, `run_check.guard`, 03 §3.3); an answer from another run stops
   the measurement (`RunChanged`). Cached by the sha256 of the text, in a
   directory named by the recipe, the space and the two runs
   (`VectorCache`): a text two arms share is embedded once (RULE 1), and
   vectors of two engines never meet in one directory.
   BY DEFAULT EVERY ARM IS EMBEDDED FRESH under the pinned run: the arms
   then differ in the cut and nothing else (München and the legal session,
   2026-11-13: the stored vectors came from the run that embedded the
   collection, and its drift would read as the cut's effect). With
   `--stored`, an arm whose layer IS the collection's may take the vectors the
   collection holds (`seed_stored`) — only for a chunk whose stored TEXT is
   byte-identical to the arm's, and only once a re-embedded sample under the
   pinned run gives the stored BYTES back in an exact space, or reaches the
   space's tolerance in a near one; refused, the arm is embedded. Seeds are
   kept for the run in memory, never in the cache: a run without `--stored` can
   never read them (München's review of #621, 2026-11-13).
   The arms cut `[title, sections]` as the embed stage reads them: the
   collection's fresh record where it holds one, the parse otherwise
   (`stage_doc`, app/parallel.py `_one`).
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
#: collection's vectors, held to the space's own rule (bytes, or tolerance)
STORED_CHECK = 64
#: the pre-stated decision (München and the legal session, 2026-11-13; the
#: operator's choice A unless said otherwise): one primary measure, one margin
PRIMARY = ("reranked", "mrr@10")
MARGIN = 0.02


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
    instruction and the chunk, with the tokenizer's special tokens (the
    window holds them too; `chunking_legal._count` leaves them out, and its
    budget keeps a margin for them) — in the tokenizer the collection's
    layer pins; None where it pins none (the count cannot be made exactly)."""
    return counter((shipped_layer(collection).params or {}).get("tokenizer"), recipe)


def counter(tok_sha: str | None, recipe) -> Callable[[str], int] | None:
    """`window_counter`'s count for a tokenizer named by its sha256."""
    if not tok_sha:
        return None
    from app.plugins.wikidata import chunker as wd
    tok = wd.tokenizer_for(tok_sha)
    prefix = getattr(recipe, "document_instruction", None) or ""
    return lambda text: len(tok.encode(prefix + text, add_special_tokens=True).ids)


def window_cost(name: str, chunks: list[dict], count: Callable[[str], int] | None,
                ctx: int | None) -> dict[str, Any]:
    """Every chunk counted as the encoder reads it: the tokens, the longest,
    the window and how many exceed it — an arm with any is refused
    (`OverWindow`), since the encoder would truncate it and its vectors would
    not be the text's. `{"window": None}` where nothing can count."""
    if count is None or not ctx:
        return {"window": None}             # said: the window was not counted
    sizes = [count(c["text"]) for c in chunks]
    over = sum(1 for n in sizes if n > ctx)
    if over:
        raise OverWindow(f"arm {name}: {over} of {len(chunks)} chunks exceed the encoder's window "
                         f"of {ctx} tokens (longest {max(sizes)}): the encoder would truncate them, "
                         f"so the arm is not comparable (operator, 2026-11-08: not admissible)")
    return {"tokens": sum(sizes), "longest_tokens": max(sizes, default=0), "window": ctx, "over_window": 0}


def stage_doc(collection, row, doc) -> tuple[str | None, list, bool]:
    """`[title, sections]` as the EMBED STAGE reads them (app/parallel.py
    `_one`, `record_source.title_sections_and_source`): the collection's
    fresh record where it holds one, the parse otherwise; and whether the
    record was read. Through `record_for` alone — the stage's sampled
    re-parse can mark a collection stopped, and a measurement writes
    nothing. Until 2026-11-13 the arms cut the parse alone (München: a
    record whose title differs gives the same chunk id another text)."""
    from app import record_source
    rec = record_source.record_for(collection, row)
    if rec is None:
        return doc.title, doc.sections, False
    return rec.get("title"), [tuple(x) for x in rec.get("sections") or []], True


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
    missing = from_records = 0
    for nid in sorted({int(i) for i in native_ids}):
        row, doc = held._row_and_doc(f"{plugin}:{nid}")
        if doc is None:
            missing += 1
            continue
        lang = (row["lang"] if "lang" in row.keys() else None) or "und"
        title, sections, recorded = stage_doc(held.collection, row, doc)
        from_records += recorded
        for c in fn(row["uid"], nid, sections, title=title):
            c["lang"] = lang
            chunks.append(c)
    cost: dict[str, Any] = {"records": len({c["book_id"] for c in chunks}), "records_missing": missing,
                            "records_from_records": from_records,
                            "chunks": len(chunks), "characters": sum(len(c["text"]) for c in chunks),
                            "tokens_estimated": sum(int(c.get("tokens") or 0) for c in chunks)}
    cost.update(window_cost(arm.name, chunks, count, ctx))
    return chunks, cost


# ---- the vectors -------------------------------------------------------------------

class RunChanged(SystemExit):
    """An answer named another run than the one the measurement is pinned
    to: its vectors are not kept, and the run stops (München, 2026-11-13:
    an engine swapped behind one url would mix two engines' vectors)."""


@dataclass(frozen=True)
class Runs:
    """The run that answers each side, pinned for the whole measurement."""
    document: str
    query: str

    def as_dict(self) -> dict[str, str]:
        return {"document": self.document, "query": self.query}


def pin_runs(collection, recipe, *, log: Callable[[str], None] = print) -> Runs:
    """The runs the measurement is made under, one per side: the run that
    answers NOW (`run_check.answering_run`), held to what a write is held to
    (`run_check.guard`: one this peer has not tested is gated here, at once;
    one that failed, or an endpoint that states none, stops the measurement
    — 03 §3.3, "a check runs under a validated run")."""
    from app import collections as collections_mod
    from app import run_check
    from app.hold import embedder as emb
    from app.hold import runs as runs_mod
    if not run_check.applies(collection):
        raise SystemExit(f"{collection.name}: its Codex validates no runs (before version 3): the chunker "
                         f"mode measures only under a run validated for the space")
    codex = collection.codex
    root = collections_mod.louise_root() / "collections"
    qrole = "query" if codex.space.query.encoder is not None else "document"
    try:
        document = run_check.answering_run(recipe, query=False)
        query = run_check.answering_run(recipe, query=True)
        run_check.guard(root, collection.name, codex, recipe, {document}, role="document", log=log)
        run_check.guard(root, collection.name, codex, recipe, {query}, role=qrole, log=log)
    except (runs_mod.RunRefused, runs_mod.RunUnvalidated, emb.GateDeferred) as e:
        raise SystemExit(f"{collection.name}: not measured — {e}") from None
    log(f"{collection.name}: document run {document[:23]}…, query run {query[:23]}… — validated")
    return Runs(document, query)


def recipe_key(recipe, *, space_id: str | None = None, runs: Runs | None = None) -> str:
    """What computed a vector: both sides' endpoints and models, the two
    instructions the app adds, the window and its cut, the dtype — and the
    collection's space and the two runs that answered (München,
    2026-11-13: without the runs, an engine swapped between a pilot and a
    run shares one directory with the engine it replaced)."""
    d = {k: getattr(recipe, k, None) for k in ("url", "api", "model", "dim", "dtype", "ctx", "cut",
                                                "query_url", "query_model", "query_instruction",
                                                "document_instruction")}
    d["space_id"] = space_id
    d["runs"] = runs.as_dict() if runs is not None else None
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


def text_key(text: str, *, query: bool) -> str:
    return hashlib.sha256((("q\0" if query else "d\0") + text).encode("utf-8")).hexdigest()


class VectorCache:
    """Vectors by the sha256 of the text they were made from, one directory
    per recipe, space and runs (`recipe_key`); written in parts, each part's
    vectors first and its keys last, so a part whose keys exist is whole
    (invariant 2: an interrupted run resumes where it stopped). Kept as
    float16, the dtype the collections store (`EmbedRecipe.dtype = "f16"`).
    Only vectors this tool's encoder calls made: never a stored one."""

    def __init__(self, root: Path, recipe, *, space_id: str | None = None, runs: Runs | None = None) -> None:
        self.dir = Path(root) / recipe_key(recipe, space_id=space_id, runs=runs)
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

    def vector(self, key: str) -> np.ndarray:
        p, r = self._rows[key]
        return self._parts[p][r]

    def matrix(self, keys: list[str], seeds: dict[str, np.ndarray] | None = None) -> np.ndarray:
        seeds = seeds or {}
        out = [seeds[k] if k in seeds else self.vector(k) for k in keys]
        return np.asarray(out, dtype=np.float32) if out else np.zeros((0, 0), dtype=np.float32)


def default_embed(recipe, texts: list[str], *, query: bool, runs: set | None = None) -> list[list[float]]:
    from app import embeddings
    return embeddings.embed_calibration(recipe, texts, query=query, runs=runs)


def pinned_embed(runs: Runs) -> Callable:
    """`default_embed` held to the pinned runs: every answer's run is
    collected (`runs=`, as a write collects it) and must be its side's
    pinned one; another stops the measurement before its vectors are kept."""
    def fn(recipe, texts: list[str], *, query: bool) -> list[list[float]]:
        seen: set = set()
        vectors = default_embed(recipe, texts, query=query, runs=seen)   # looked up when called
        want = runs.query if query else runs.document
        if seen != {want}:
            got = ", ".join(sorted(str(r)[:23] for r in seen)) or "none stated"
            raise RunChanged(f"the {'query' if query else 'document'} side answered from {got}, not the "
                             f"pinned run {want[:23]}…: nothing of this answer is kept; run again (the "
                             f"new run is pinned and validated, in its own cache directory)")
        return vectors
    return fn


def embed(texts: list[str], *, cache: VectorCache, recipe, query: bool,
          embed_fn: Callable | None = None, batch: int = bakeoff.SHARD,
          seeds: dict[str, np.ndarray] | None = None,
          log: Callable[[str], None] = print) -> tuple[np.ndarray, dict[str, Any]]:
    """Each text's vector — a seed's (`seed_stored`), the cache's or the
    encoder's — in `texts`' order. Seeds are never written to the cache."""
    embed_fn = embed_fn or default_embed      # looked up when called
    seeds = seeds or {}
    keys = [text_key(t, query=query) for t in texts]
    todo: dict[str, str] = {}
    for k, t in zip(keys, texts):
        if k not in seeds and k not in cache and k not in todo:
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
    return cache.matrix(keys, seeds), {"texts": len(texts), "unique": len(set(keys)),
                                       "embedded": len(pending), "from_stored": sum(k in seeds for k in keys),
                                       "seconds": round(seconds, 1)}


def default_stored(chunk_ids: list[str], collection) -> dict[str, tuple[str, Any]]:
    """id -> (stored text, stored vector): the text beside the vector, so a
    chunk is seeded only where the collection holds THIS text."""
    from app import embeddings
    got: dict[str, tuple[str, Any]] = {}
    for i in range(0, len(chunk_ids), 5000):
        got.update(embeddings._by_chunk_id(chunk_ids[i:i + 5000], collection, with_text=True))
    return got


def seed_stored(chunks: list[dict], collection, *, recipe, stored_fn: Callable | None = None,
                embed_fn: Callable | None = None, check: int = STORED_CHECK,
                log: Callable[[str], None] = print) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """The collection's own vectors for an arm that cuts as it cuts (RULE 1:
    bytes provably identical), as `{text key: vector}` for this run only.

    A chunk is a candidate only where the stored row holds the arm's TEXT
    byte for byte: a chunk id names the record's version, the place and the
    chunker's version, never the title a header writes (München,
    2026-11-13). The candidates are then held to THE SPACE'S rule against a
    re-embedded sample under the pinned run: the same BYTES in an exact
    space (`runs.requires_exact`), the space's tolerance in a near one.
    Refused — or without a Codex to state a rule — nothing is seeded and
    the arm is embedded: the measurement is then all of one run."""
    from decimal import Decimal

    from app.hold import runs as runs_mod
    stored_fn, embed_fn = stored_fn or default_stored, embed_fn or default_embed
    got = stored_fn([c["id"] for c in chunks], collection)
    same: list[tuple[dict, Any]] = []
    differs = 0
    for c in chunks:
        row = got.get(c["id"])
        if row is None:
            continue
        if row[0] == c["text"]:
            same.append((c, row[1]))
        else:
            differs += 1
    stats: dict[str, Any] = {"stored": 0, "stored_text_differs": differs}
    if not same:
        return {}, stats
    codex = getattr(collection, "codex", None)
    if codex is None:
        stats["stored_refused"] = "no Codex: no rule to hold stored vectors to"
        return {}, stats
    n = min(check, len(same))
    at = sorted(np.random.default_rng(0).choice(len(same), size=n, replace=False).tolist())
    stored = np.stack([np.asarray(same[i][1]) for i in at])
    fresh = np.asarray(embed_fn(recipe, [same[i][0]["text"] for i in at], query=False), dtype=np.float64)
    equal = int(sum(np.array_equal(f.astype(stored.dtype), s) for f, s in zip(fresh, stored)))
    a = stored.astype(np.float64)
    a /= np.linalg.norm(a, axis=1, keepdims=True)
    b = fresh / np.linalg.norm(fresh, axis=1, keepdims=True)
    cos = (a * b).sum(axis=1)
    exact = runs_mod.requires_exact(codex)
    tol = str(codex.space.tolerance)
    verdict = {"n": n, "worst": round(float(cos.min()), 6), "median": round(float(np.median(cos)), 6),
               "bytes_equal": equal,
               "rule": "the same bytes (an exact space)" if exact else f"cosine >= {tol} (the space's tolerance)"}
    stats["check"] = verdict
    ok = equal == n if exact else Decimal(repr(float(cos.min()))) >= Decimal(tol)
    if not ok:
        stats["stored_refused"] = (f"{equal}/{n} sampled stored vectors give back their bytes, worst cosine "
                                   f"{verdict['worst']}: not this run's under {verdict['rule']}")
        log(f"  stored vectors refused — {stats['stored_refused']}; the arm is embedded")
        return {}, stats
    seeds: dict[str, np.ndarray] = {}
    for c, v in same:
        seeds.setdefault(text_key(c["text"], query=False), np.asarray(v, dtype=np.float32))
    stats["stored"] = len(seeds)
    return seeds, stats


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


def default_rerank(held, collection, *, heads: bool = True) -> Callable | None:
    """The collection's reranker over a fused list, as `retrieval` runs it:
    each record's head attached, its top `RERANK_TOP_N` judged. None where
    the collection has no reranker (the reading is then not made).
    `heads=False`: the chunks judged bare — for records a manifest does not
    hold (tools/chunker_languages.py, whose record head would be the query)."""
    from app import collections as collections_mod
    from app import retrieval
    recipe = collections_mod.reranker_for(collection)
    if recipe is None:
        return None
    depth = retrieval.RERANK_TOP_N

    def rerank(question: str, hits: list[dict]) -> list[dict]:
        if heads:
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
    index removed before the next is built (memory is one arm's). Every arm
    is cut and counted before anything is embedded, the questions included."""
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
    shipped = json.dumps(shipped_layer(held.collection).as_manifest(), sort_keys=True)
    # every arm cut and counted FIRST: an arm the encoder would truncate stops
    # the run before anything — a question included — is embedded
    count = window_counter(held.collection, recipe) if count_fn is _DEFAULT else count_fn
    ctx = getattr(recipe, "ctx", None)
    if count is None or not ctx:
        log(f"{held.collection.name}: the window is NOT counted (its layer pins no tokenizer): an arm the "
            f"encoder truncates is not refused here")
    cuts = {}
    for arm in arms:
        cuts[arm.name] = cut(arm, held, ids, count=count, ctx=ctx)
        log(f"arm {arm.name}: {json.dumps(arm.manifest(), sort_keys=True)} — {cuts[arm.name][1]}")
    qvecs, qstats = embed([q["question"] for _, q, _ in covered], cache=cache, recipe=recipe, query=True,
                          embed_fn=embed_fn, log=log)
    out: dict[str, Any] = {"questions": len(covered), "query_embedding": qstats, "arms": {}}
    for arm in arms:
        chunks, cost = cuts.pop(arm.name)
        seeds: dict[str, np.ndarray] = {}
        seeded: dict[str, Any] = {"stored": 0}
        if stored_fn is not None and json.dumps(arm.manifest(), sort_keys=True) == shipped:
            seeds, seeded = seed_stored(chunks, held.collection, recipe=recipe, stored_fn=stored_fn,
                                        embed_fn=embed_fn, log=log)
        vectors, dstats = embed([c["text"] for c in chunks], cache=cache, recipe=recipe, query=False,
                                embed_fn=embed_fn, seeds=seeds, log=log)
        del seeds
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


#: what every file of one report shares: one measurement, one collection,
#: one code, one pair of runs, the same seeding (München, 2026-11-13: the
#: report compared whatever it was handed)
SHARED = ("collection", "collection_id", "tag", "code", "runs", "stored", "k", "pool", "split", "arms")


def _ordered(rs: list[dict[str, Any]], reading: str) -> list[dict[str, Any]]:
    # the run's own arm order (`arms`), never the files' names: a timestamped
    # name sorts `akn` before `shipped`
    order = rs[0].get("arms") or []
    rs = sorted(rs, key=lambda r: order.index(r["arm"]) if r["arm"] in order else len(order))
    if len({r["arm"] for r in rs}) != len(rs):
        raise SystemExit(f"{reading}: two files of one arm; give each arm's file once")
    return rs


def _shared(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in SHARED:
        seen = {json.dumps(r.get(key), sort_keys=True) for r in runs}
        if len(seen) > 1:
            raise SystemExit(f"the files differ in {key} ({', '.join(sorted(seen))}): they are not one "
                             f"measurement")
        out[key] = runs[0].get(key) if runs else None
    for key in ("pool_built_from", "pool_depth"):
        out[key] = runs[0].get(key) if runs else None
    return out


def _bootstrap_means(d: np.ndarray, trials: int, seed: int = 0, chunk: int = 500) -> np.ndarray:
    """The percentile bootstrap of the mean difference — the draws of
    `encoder_bakeoff.paired`'s interval (same seed, same chunks)."""
    rng = np.random.default_rng(seed)
    n = len(d)
    boots = []
    for start in range(0, trials, chunk):
        m = min(chunk, trials - start)
        boots.append(d[rng.integers(0, n, size=(m, n))].mean(axis=1))
        rng.integers(0, 2, size=(m, n))          # paired's sign flips: the same stream after
    return np.concatenate(boots)


def decision(runs: list[dict[str, Any]], *, primary: tuple[str, str] = PRIMARY, margin: float = MARGIN,
             decide: Iterable[str] | None = None, alpha: float = 0.05, trials: int = 10_000) -> dict | None:
    """THE RULE STATED BEFORE THE RUN (München and the legal session,
    2026-11-13: "not worse" is non-inferiority, and needs its margin and its
    one measure fixed before a number is read). For each decided arm against
    the baseline (the run's first arm), d = arm − baseline per question on
    the primary measure:
      keep      the baseline is not worse than the arm by `margin`: H0 mean(d)
                >= margin rejected, one-sided;
      drop      the arm is better than the baseline by `margin`: H0 mean(d)
                <= margin rejected, one-sided;
      undecided neither.
    p from the percentile bootstrap of the mean difference (the interval
    `compare` reports), one-sided, Holm-adjusted across the decided arms.
    Every other metric and reading is descriptive: nothing is decided on it."""
    reading, metric = primary
    rs = [r for r in runs if r["legs"] == reading]
    if len(rs) < 2:
        return None
    rs = _ordered(rs, reading)
    base, others = rs[0], rs[1:]
    if decide:
        decide = list(decide)
        unknown = [a for a in decide if a not in {o["arm"] for o in others}]
        if unknown:
            raise SystemExit(f"--decide {', '.join(unknown)}: not an arm of these files besides the baseline")
        others = [o for o in others if o["arm"] in decide]
    rb = {r["index"]: r for r in base["per_question"] if "index" in r}
    rows = []
    for o in others:
        ro = {r["index"]: r for r in o["per_question"] if "index" in r}
        if ro.keys() != rb.keys():
            raise SystemExit(f"{o['arm']} and {base['arm']} answered other questions")
        order = sorted(rb)
        d = np.asarray([float(ro[i][metric]) - float(rb[i][metric]) for i in order], dtype=np.float64)
        boots = _bootstrap_means(d, trials)
        rows.append({"arm": o["arm"], "questions": len(d), "mean": float(d.mean()),
                     "upper95": float(np.percentile(boots, 95)), "lower95": float(np.percentile(boots, 5)),
                     "p_keep": (int((boots >= margin).sum()) + 1) / (trials + 1),
                     "p_drop": (int((boots <= margin).sum()) + 1) / (trials + 1)})
    for key in ("p_keep", "p_drop"):
        for row, p in zip(rows, holm([r[key] for r in rows])):
            row[key + "_holm"] = p
    split = base.get("split") or "validation"
    for row in rows:
        if row["p_keep_holm"] <= alpha:
            row["verdict"] = "keep"
            row["next"] = f"the baseline ({base['arm']}) is not worse than {row['arm']} by {margin}: it stays"
        elif row["p_drop_holm"] <= alpha:
            row["verdict"] = "drop"
            row["next"] = f"{row['arm']} is better than the baseline by more than {margin}: the baseline gives way"
        else:
            row["verdict"] = "undecided"
            row["next"] = ("the same arms on every question (`--split all`), the same rule" if split != "all"
                           else "undecided on every question: what follows was stated before the run "
                                "(choice A, for the header: it is dropped)")
    return {"reading": reading, "metric": metric, "margin": margin, "alpha": alpha, "baseline": base["arm"],
            "split": split, "rows": rows}


def report(runs: list[dict[str, Any]], metrics: Iterable[str] = ("mrr@10", "recall@10", "ndcg@10"), *,
           primary: tuple[str, str] = PRIMARY, margin: float | None = MARGIN,
           decide: Iterable[str] | None = None) -> dict:
    """Every arm against the FIRST (its baseline), per reading: each metric's
    difference, interval, p, Holm-adjusted p across the arms, and the
    smallest difference the questions could show; each arm's cost and
    vectors beside; and the pre-stated decision on the primary measure."""
    from tools import legal_pilot as lp
    metrics = tuple(metrics)
    about = _shared(runs)
    by_reading: dict[str, list[dict]] = {}
    for r in runs:
        by_reading.setdefault(r["legs"], []).append(r)
    out: dict[str, Any] = {}
    for reading, rs in by_reading.items():
        rs = _ordered(rs, reading)
        base, others = rs[0], rs[1:]
        rows = [{"arm": base["arm"], "baseline": True, "cost": base.get("cost"), "embedding": base.get("embedding"),
                 "builder_share": base["summary"].get("builder_share"),
                 **{m: base["summary"].get(m) for m in metrics}}]
        compared = [lp.compare(o, base, metrics) for o in others]
        for m in metrics:
            adj = holm([c[m]["p"] for c in compared if m in c]) if compared else []
            for c, p in zip([c for c in compared if m in c], adj):
                c[m]["p_holm"] = p
        for o, c in zip(others, compared):
            rows.append({"arm": o["arm"], "cost": o.get("cost"), "embedding": o.get("embedding"),
                         "builder_share": o["summary"].get("builder_share"),
                         **{m: o["summary"].get(m) for m in metrics},
                         "vs_baseline": {m: c[m] for m in metrics if m in c}})
        out[reading] = {"questions": compared[0]["questions"] if compared else base["summary"].get("questions_run"),
                        "rows": rows}
    rule = (decision(runs, primary=primary, margin=margin, decide=decide) if margin is not None else None)
    return {"about": about, "decision": rule, "readings": out}


def _vectors(e: dict | None) -> str:
    if not e:
        return "—"
    said = f"{e.get('from_stored', 0)} stored, {e.get('embedded', 0)} embedded"
    if e.get("stored_refused"):
        said += " (stored refused)"
    return said


def report_markdown(rep: dict, metrics: Iterable[str] = ("mrr@10", "recall@10", "ndcg@10"), *,
                    head: bool = True, notes: bool = True) -> str:
    """`head` and `notes`: the title, the measurement's line and the
    "Read with" notes, which are the legal pilot's (its pool, its header):
    a caller measuring something else (tools/chunker_languages.py) leaves
    them out and says its own."""
    metrics = tuple(metrics)
    about = rep.get("about") or {}
    runs = about.get("runs") or {}
    lines = [] if not head else ["# Chunker mode: every arm against the first", "",
             (f"{about.get('collection')}, pool {about.get('pool')} (built from "
              f"{about.get('pool_built_from') or '?'}'s text leg, depth {about.get('pool_depth') or '?'}), the "
              f"{about.get('split')} questions, k {about.get('k')}; document run "
              f"{str(runs.get('document') or '?')[:23]}, query run {str(runs.get('query') or '?')[:23]}; the "
              f"collection's stored vectors {'allowed' if about.get('stored') else 'not used'}."), ""]
    rule = rep.get("decision")
    if rule:
        arms_n = len(rule["rows"])
        lines += [(f"## Decision: {rule['reading']} {rule['metric']}, margin {rule['margin']} (stated before "
                   f"the run; one-sided {int(100 * (1 - rule['alpha']))} %, Holm across {arms_n} "
                   f"arm{'s' if arms_n != 1 else ''})"), "",
                  (f"| arm − {rule['baseline']} | mean | 95 % bounds (one-sided) | p keep (Holm) "
                   f"| p drop (Holm) | verdict | next |"), "|---|---|---|---|---|---|---|"]
        for r in rule["rows"]:
            lines.append(f"| {r['arm']} | {r['mean']:+.4f} | ≥ {r['lower95']:+.4f}, ≤ {r['upper95']:+.4f} | "
                         f"{r['p_keep_holm']:.4f} | {r['p_drop_holm']:.4f} | **{r['verdict']}** | {r['next']} |")
        lines.append("")
    for reading, block in (rep.get("readings") or {}).items():
        lines += [f"## {reading} ({block['questions']} questions)", "",
                  "| arm | chunks | characters | vectors | " + " | ".join(metrics)
                  + " | from the pool's builder | Δ vs first (95 % CI), p Holm, ±detectable |",
                  "|---|---|---|---|" + "---|" * len(metrics) + "---|---|"]
        for row in block["rows"]:
            cost = row.get("cost") or {}
            vals = " | ".join(f"{row[m]:.4f}" if row.get(m) is not None else "—" for m in metrics)
            share = f"{row['builder_share']:.3f}" if row.get("builder_share") is not None else "—"
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
                         f"{_vectors(row.get('embedding'))} | {vals} | {share} | {delta} |")
        lines.append("")
    if notes:
        builder = about.get("pool_built_from") or "the builder"
        lines += [
            "## Read with", "",
            ("- Only the decision above decides. Every other figure is descriptive: its p is Holm-adjusted "
             "across the arms, not across the metrics and readings."),
            ("- The reranker reads the act's title TWICE in an arm with the header: in the head it attaches "
             "above every chunk (`retrieval._attach_heads`) and in the chunk's own header — as production does "
             "today, so it is measured as it is."),
            (f"- The pool is {builder}'s text-leg top {about.get('pool_depth') or '?'} for each question, and "
             "the gold: every arm meets the distractors the SHIPPED cut's text leg finds and none of its own, "
             "which spares the other arms their hardest ones — a bias toward the challengers. 'from the pool's "
             "builder' is the share of an arm's top-k records that its question's builder list holds."),
            ("- No arm has what a reader's search adds — the query's language, the plan's steps and "
             "translations, the catalogue leg (CATALOG_FIRST never moves a hit), and query vectors are cached "
             "as float16: equal for every arm, so the differences hold; the absolute values are not "
             "production's."), ""]
    return "\n".join(lines)


def tag(arms: list[Arm], *, k: int, pool_name: str, split: str, stored: bool) -> str:
    """The settings a pilot reading is valid for: the arms' layers, k, the
    pool, the questions, whether stored vectors may be taken, and the code
    (this file, the chunkers, Ask's)."""
    import re

    from tools.measure import chat_eval
    h = hashlib.sha256()
    for a in arms:
        h.update(a.name.encode() + json.dumps(a.manifest(), sort_keys=True).encode())
    for f in [Path(__file__), ROOT / "app" / "chunking.py", ROOT / "app" / "chunking_legal.py",
              ROOT / "app" / "layers.py", ROOT / "app" / "fts.py", ROOT / "app" / "retrieval.py"]:
        h.update(f.read_bytes())
    raw = (f"chunker-{h.hexdigest()[:12]}-k{k}-pool-{pool_name}-q-{split}"
           f"-vec-{'stored' if stored else 'fresh'}-code-{chat_eval.ask_code()[7:19]}")
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)


def workdir_for(out: Path) -> Path:
    path = Path(out) / "chunker"
    path.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="index-", dir=path))
