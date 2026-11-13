"""MEASUREMENT (not product code): reranker ARMS compared on IDENTICAL
candidate lists (docs/notes/reranker-survey-2026-11.md, "The benchmark").

WHY A NEW TOOL. rerank_lang_variants.py, rerank_variants.py, legal_pilot.py
and fusion_calibration.py run the live search once per arm, so two arms see
two candidate lists (another translation, an encoder answer, a record
embedded meanwhile) and their difference is not the reranker's alone. Here
each query is searched ONCE, its candidates are stored as the live path would
send them to the reranker, and every arm reranks those very texts.

    python -m tools.measure.rerank_bench capture FORMAT SOURCE... --out SET.jsonl
        [--name NAME] [--collection C] [--works works.json] [--limit N] [--no-translate] [--speed]
    python -m tools.measure.rerank_bench rerank SET.jsonl --arm NAME --model MODEL
        [--instruction TEXT | --instruction collection] [--instructions FILE.json]
        [--instruction-form FORM] [--queries live|typed] [--url URL] [--window N]
        [--runs DIR] [--limit N] [--concurrency 1] [--timeout 300]
    python -m tools.measure.rerank_bench score SET.jsonl [SET2.jsonl ...] --arms A0,A1,A2
        [--base A1] [--runs DIR] [--raw] [--out PREFIX]
    python -m tools.measure.rerank_bench show SET.jsonl --arms A0,A1,A2 [--limit 3] [--top 5]

Run inside the node's image, as chat_eval and legal_pilot are. `capture`
needs the node (its search); `rerank` needs only the model server; `score`
and `show` read files.

CAPTURE. Each judged query goes through the node's own search,
`query_service.run_search([collection], q, 10, "hybrid", translate,
rerank_depth=50)` (the Ask tab's: translation on unless --no-translate), and
stops being the live search at `retrieval._reranked`, which is replaced for
the process: it receives exactly what the reranker would — the plan's core
query, the fused candidates, the plan's translations — keeps them, and hands
the fused order back. Kept per query: the top `depth` (50) records in fused
order, each with its uid, native id, language, leg ranks (`catalog` 1 is what
CATALOG_FIRST moves) and its JUDGED_CHUNKS (2) best matched chunks, each
text built as `_rerank_held` builds it — the record's title line (HEAD), a
blank line, the chunk — and the query the live path sends it against: the
plan's translation into the RECORD's language where the plan made one, else
the core query (05 §5.6, 834baf5). Each chunk is judged at capture (its
target and grade, below), so `score` needs only the files. A collection with
no reranker of its own is given the first held collection's for the capture
only (so its candidates carry their title lines, as fusion_calibration's
--rerank-like does), and the row says it was lent. The vector leg is settled
in this process first, and a search that withheld a leg is never recorded
(measuring.md §13, chat_eval.settle / withheld_legs): the run stops (exit 3)
and resumes there. The searches are real ones, as fusion_calibration's:
they take the lazy-embed queue and the cited chunks' re-check queue as any
search does; no reranker is asked. Resumable: a query already in the file
is skipped; the file's first line pins the settings, the fusion code's
digest and the data's snapshot, and a resume on others is refused (exit 2)
— a new file for them.

THE WINDOW. The live path cuts each text to the reranker's `window` (2,048)
of ITS OWN tokens (`retrieval.fit_to_window`, LocalAI's /v1/tokenize for the
model). The capture keeps the texts UNCUT; `rerank` cuts them with the same
function for the arm's model at the captured window, before the clock
starts — what the node would send were that arm its reranker. For the live
model the cut is the live cut, by construction.

RERANK. One arm = one model name (and an instruction, optionally) on the
same /v1/rerank endpoint (Jina/Cohere: {model, query, documents, top_n} ->
results[{index, relevance_score}]), the captured reranker's URL unless --url.
Each query's requests are the live ones: one call per query variant, its
texts in candidate order, a text sent twice in one call sent once
(`_rerank_held`). Each call is timed alone (wall clock, ms), one request at a
time unless --concurrency; the model is warmed with one throwaway request
and pinned for the run (app/model_pins.py), and a query during which its
backend was (re)loaded is marked `warm: false` and left out of the time
(chat_eval's rule, measuring.md §10). The node paused and nothing else on
the card is the runner's job: the tool says so and does not check. A 5xx is
not retried (a retry would hide in the time): the run stops (exit 1) and
resumes at that query. Resumable per (arm, set, query); the arm file's
first line pins the arm.

QWEN3-RERANKER'S INSTRUCTION. Neither llama.cpp's /v1/rerank nor LocalAI's
JINA request has an instruction field: llama.cpp's converter writes the
GGUF a `rerank` chat template whose user turn is "<Instruct>: Given a web
search query, retrieve relevant passages that answer the query\\n<Query>:
{query}\\n<Document>: {document}", and the server fills {query} and
{document} (server-common.cpp `format_prompt_rerank`, read 2026-11-10). The
model's default instruction is therefore what an arm without one gets. An
arm WITH one puts it in the query, in Qwen3-Reranker's documented form
(its card's `format_instruction`): DOCUMENTED_FORM,
"\\n<Instruct>: {instruction}\\n<Query>: {query}", so that what precedes the
document ends exactly as the card's template does — "<Instruct>: X\\n<Query>:
Q\\n<Document>: D" — after the baked default line and an empty query line,
which cannot be removed through the endpoint. Chosen over a bare prefix
("X Q") because the card's line structure is what the model was trained on;
`--instruction-form` takes any other form with {instruction} and {query}.
THE STANDARD ALTERNATIVE: a second GGUF whose `rerank` template carries the
collection's instruction (gguf-py rewrites metadata), one model per
instruction — worth it once an instructed arm has won.
COLLECTION_INSTRUCTIONS holds one per collection, in English as Qwen
recommends; --instructions FILE.json ({collection: text}) replaces them.

SCORE. Per set and arm, over the queries every named arm answered (paired):
- each record judged by its BEST chunk under the arm (the chunk that scored
  it; A0: its best fused chunk) — the record level, as the live path shows;
- the order as shipped: the reranker's scores (`retrieval._rerank_order`,
  RERANK_FUSE, RERANK_LANGUAGE_ORDER as captured), then CATALOG_FIRST
  (`retrieval._catalog_first`); `--raw` leaves CATALOG_FIRST out. A0 is the
  fused order, as the live path keeps it when no rerank comes back;
- MRR@10 (the first relevant record), nDCG@10 (gain 2^g - 1, each target
  counted once at its first place, the ideal from the query's judged
  targets), R@1 and R@10 (a relevant record within the first 1 / 10: the
  campaign's R@k), the share of the 10 in another language than the
  query's (a record with no language counts as another, as run 5 counted),
  the it/en gap (MRR@10 of the Italian queries minus the English ones) where
  a set has both, judged@10 where the set is not exhaustive (UMBRELA), and
  the warm latency per list (the sum of its calls) p50 / p95;
- each arm against the base (A1) on per-query MRR@10: Fisher's paired
  randomization test, two-sided, 10,000 permutations, seed 0, with the 95 %
  bootstrap interval (encoder_bakeoff.paired, Smucker et al. 2007);
- the same per kind where a set has several (titles / descriptive ...).
A markdown table (stdout, and PREFIX.md) and PREFIX.json.

THE SETS (FORMAT) and their judgements, every one through an existing
loader or format:
  gutenberg   rerank_lang_variants.py's DATA, [{qid, kind, lang, query, books}]
              (a .json list, .jsonl, or the `DATA = [...]` file itself), or
              the TSVs (chat_eval.read_queries: one_language_queries.tsv,
              one_language_titles.tsv) with --works works.json
              (chat_eval.load_works, one_language_score.ALSO). The work is the
              known item: any of its books is relevant, counted once.
  known-item  wikidata_encoder_ab.py's queries.jsonl, {uid, family, lang,
              query} (wikidata's sample; wikipedia's `sample-table`) — the
              one entity is relevant; kind = family. --collection names it.
  umbrela     fusion_calibration.py's judged.jsonl, {query, collection,
              book_id, grade}: one item per (query, collection) with a
              record of grade >= 2 (fusion_calibration.RELEVANT); grades
              0-3 for nDCG; records nobody judged count 0 and judged@10
              says how many there were. kind = collection.
  jurifindit  legal_pilot.py's fetched dataset directory: its coverage
              (legal_pilot.Held, coverage), a covered question's held gold
              articles relevant; a record is relevant when its best chunk IS
              a gold article of the question (work and article, as
              legal_pilot.run reads them). kind = category. --collection.
`--limit N`: the first N queries of every kind (measuring.md §12's pilot);
the whole run resumes after them.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.measure import chat_eval  # noqa: E402 — settle, withheld_legs, read_queries, load_works
from tools.measure.fusion_calibration import RELEVANT as UMBRELA_RELEVANT  # noqa: E402
from tools.measure.fusion_calibration import append_jsonl, read_jsonl  # noqa: E402

TOP = 10
#: the results a search returns (05 §5.4); the candidates are max(4k, depth)
K = 10
#: the arm that is the fused order, no reranker
FUSED = "A0"
#: the arm every other is tested against (the live reranker)
BASE = "A1"
PERMUTATIONS = 10_000
SEED = 0
#: the reason the capture's stand-in gives for the order it hands back;
#: never one of the search's own withheld reasons
CAPTURED = "rerank_bench: captured before the rerank"
#: the capture's settings a resume must share
CAPTURE_PINNED = ("tool", "set", "format", "translate", "speed", "depth", "k", "judged_chunks", "retrieval",
                  "code", "data_version")
#: a pinned key's value in a file written before the key existed: a capture
#: before `--speed` was a Quality capture
PINNED_DEFAULTS = {"speed": False}
#: the arm's settings a resume must share
ARM_PINNED = ("arm", "model", "instruction", "instructions", "form", "queries", "url", "window",
              "capture")
#: Qwen3-Reranker's documented input (its card's `format_instruction`): what
#: an instructed arm puts in the query, after the GGUF template's default line
DOCUMENTED_FORM = "\n<Instruct>: {instruction}\n<Query>: {query}"
#: one task description per collection, Qwen's "Given a ..., retrieve ..."
#: shape; gutenberg's is the "library" instruction query_instruction_probe.py
#: measured on the encoder's query side
COLLECTION_INSTRUCTIONS: dict[str, str] = {
    "gutenberg": ("Given a search for a book by its title, its author or a description of its "
                  "content, retrieve passages of that book"),
    "wikidata": ("Given a search for an entity by its name or by what it is or did, retrieve the "
                 "record that describes that entity"),
    "wikipedia": ("Given a search for a subject, retrieve passages of the encyclopedia article "
                  "about that subject"),
    "normattiva": ("Given a question about Italian law, retrieve the article of Italian "
                   "legislation that answers it"),
    "pubmed": "Given a biomedical question, retrieve abstracts of studies that answer it",
}


class Refused(RuntimeError):
    """A rule stood in the way before anything was done (07 §7.7: exit 2)."""


class BadAnswer(RuntimeError):
    """The reranker answered for other candidates than it was sent."""


def _never() -> bool:
    return False


# ---------------------------------------------------------------- the sets


@dataclass
class Item:
    """One judged query on one collection."""
    key: str
    query: str
    collection: str
    kind: str = ""
    lang: str | None = None
    #: native id -> (target, grade): the records judged for this query
    records: dict[str, tuple[str, int]] = field(default_factory=dict)
    #: target -> grade: what an ideal list holds (nDCG's ideal)
    targets: dict[str, int] = field(default_factory=dict)


#: (item, record uid, chunk id, native id) -> (target, grade), or None
Judge = Callable[[Item, str, str, Any], "tuple[str, int] | None"]


@dataclass
class JudgedSet:
    name: str
    format: str
    source: str
    items: list[Item]
    #: the grade from which a record is relevant
    relevant: int = 1
    #: every record not judged is not relevant (a known item, a gold list)
    exhaustive: bool = True
    #: a chunk's judgement; None: by its record (`Item.records`)
    judge: Judge | None = None


def native_of(uid_or_id: Any) -> str:
    """A record's native id from `<plugin>:<native_id>` or the bare id."""
    s = str(uid_or_id)
    return s.split(":", 1)[1] if ":" in s else s


def judge_by_record(item: Item, uid: str, chunk_id: str, native: Any) -> tuple[str, int] | None:
    for key in dict.fromkeys((native_of(uid), str(native) if native is not None else "")):
        got = item.records.get(key)
        if got is not None:
            return got
    return None


def first_per_kind(items: Sequence[Item], n: int | None) -> list[Item]:
    """measuring.md §12: the first `n` items of every kind, in set order."""
    if not n:
        return list(items)
    seen: dict[str, int] = {}
    out = []
    for it in items:
        if seen.get(it.kind, 0) < n:
            seen[it.kind] = seen.get(it.kind, 0) + 1
            out.append(it)
    return out


def _data_rows(path: Path) -> list[dict[str, Any]]:
    """rerank_lang_variants.py's DATA: a JSON list, JSON lines, or the
    `DATA = [...]` text the caller prepended to the script."""
    text = path.read_text(encoding="utf-8")
    body = text.lstrip()
    if body.startswith("DATA"):
        body = body.split("=", 1)[1]
    if body.lstrip().startswith("["):
        try:
            return list(json.loads(body))
        except json.JSONDecodeError:
            return list(ast.literal_eval(body.strip()))
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_gutenberg(sources: Sequence[Path], *, works: Path | None = None,
                   collection: str = "gutenberg") -> JudgedSet:
    """The labelled gutenberg queries (one-language-per-work.md, query
    campaign run 5): a query's work is its known item."""
    rows: list[dict[str, Any]] = []
    for src in sources:
        if src.suffix == ".tsv":
            if works is None:
                raise Refused(f"{src}: a TSV set names works by id; give --works works.json "
                              f"(one_language_works.py)")
            from tools.measure.one_language_score import ALSO
            by_work = chat_eval.load_works(works)
            for q in chat_eval.read_queries(src):
                work = q["qid"].split("#")[0]
                books = set(by_work.get(work, set()))
                for other in ALSO.get(work, ()):
                    books |= by_work.get(other, set())
                rows.append({"key": q["key"], "qid": q["qid"], "lang": q["lang"], "query": q["query"],
                             "kind": "title" if "#T" in q["qid"] else "descriptive", "books": sorted(books)})
        else:
            rows.extend(_data_rows(src))
    qids = [str(r.get("key") or r["qid"]) for r in rows]
    unique = len(set(qids)) == len(qids)
    items = []
    for r, qid in zip(rows, qids):
        key = qid if unique else f"{qid}:{r.get('lang')}"
        records = {native_of(b): ("work", 1) for b in r.get("books") or []}
        items.append(Item(key=key, query=str(r["query"]), collection=collection, kind=str(r.get("kind") or ""),
                          lang=r.get("lang"), records=records, targets={"work": 1} if records else {}))
    return JudgedSet(name="gutenberg", format="gutenberg", source=",".join(map(str, sources)), items=items)


def load_known_item(path: Path, *, collection: str) -> JudgedSet:
    """wikidata_encoder_ab.py's queries.jsonl: one entity per question."""
    items = []
    for q in read_jsonl(path):
        uid = str(q["uid"])
        items.append(Item(key=f"{uid}:{q.get('lang')}", query=str(q["query"]), collection=collection,
                          kind=str(q.get("family") or ""), lang=q.get("lang"),
                          records={native_of(uid): (uid, 1)}, targets={uid: 1}))
    return JudgedSet(name=collection, format="known-item", source=str(path), items=items)


def load_umbrela(path: Path, *, collections: Iterable[str] | None = None) -> JudgedSet:
    """fusion_calibration.py's judged.jsonl: each (query, collection) with a
    record of grade >= RELEVANT, every judged record of it graded 0-3."""
    wanted = set(collections) if collections else None
    groups: dict[tuple[str, str], dict[str, int]] = {}
    for r in read_jsonl(path):
        if r.get("grade") is None or (wanted is not None and r["collection"] not in wanted):
            continue
        groups.setdefault((r["query"], r["collection"]), {})[str(r["book_id"])] = int(r["grade"])
    items = []
    for (query, name), grades in groups.items():
        if not any(g >= UMBRELA_RELEVANT for g in grades.values()):
            continue
        key = hashlib.sha256(f"{query}\0{name}".encode()).hexdigest()[:16]
        items.append(Item(key=key, query=query, collection=name, kind=name,
                          records={b: (b, g) for b, g in grades.items()}, targets=dict(grades)))
    items.sort(key=lambda i: (i.collection, i.query))
    return JudgedSet(name="umbrela", format="umbrela", source=str(path), items=items,
                     relevant=UMBRELA_RELEVANT, exhaustive=False)


def load_jurifindit(directory: Path, *, collection: str) -> JudgedSet:
    """legal_pilot.py's fetched JuriFindIT, its gold resolved by key against
    the held collection (legal_pilot.coverage): needs the node."""
    from tools import legal_pilot as lp
    c, con = lp._open(collection, None)
    held = lp.Held(c, con)
    corpus, questions = lp._load_dataset(directory)
    cov = lp.coverage(held, corpus, questions)
    gold = {int(k): lp.gold_from(v) for k, v in cov["gold"].items()}
    by_pair = {(g.work, g.token): g.node_id for g in gold.values() if g.held}
    items = []
    for i, q in enumerate(questions):
        relevant = sorted(int(x) for x in q["relevant_doc_ids"] if int(x) in gold and gold[int(x)].held)
        if relevant:
            items.append(Item(key=str(i), query=str(q["question"]), collection=c.name,
                              kind=str(q.get("category") or ""), lang="it",
                              targets={str(n): 1 for n in relevant}))

    def judge(item: Item, uid: str, chunk_id: str, native: Any) -> tuple[str, int] | None:
        seq = lp._seq_of(chunk_id)
        work = held.work_of(uid)
        label = held.articles(uid)[1].get(seq) if (work and seq is not None) else None
        token = lp.article_token(label) if label else None
        nid = by_pair.get((work, token)) if token else None
        return (str(nid), 1) if nid is not None and str(nid) in item.targets else None
    return JudgedSet(name="jurifindit", format="jurifindit", source=str(directory), items=items, judge=judge)


def load_set(fmt: str, sources: Sequence[Path], *, collection: str | None = None,
             works: Path | None = None) -> JudgedSet:
    if fmt == "gutenberg":
        return load_gutenberg(sources, works=works, collection=collection or "gutenberg")
    if len(sources) != 1:
        raise Refused(f"{fmt} reads one source, not {len(sources)}")
    if fmt == "umbrela":
        return load_umbrela(sources[0], collections=[collection] if collection else None)
    if not collection:
        raise Refused(f"{fmt}: --collection names the collection its queries were made from")
    if fmt == "known-item":
        return load_known_item(sources[0], collection=collection)
    if fmt == "jurifindit":
        return load_jurifindit(sources[0], collection=collection)
    raise Refused(f"no set format {fmt!r} (gutenberg, known-item, umbrela, jurifindit)")


# ---------------------------------------------------------------- files


def _rows_after(path: Path, header: dict[str, Any], pinned: Sequence[str]) -> list[dict[str, Any]]:
    """The rows of `path` after its pinned first line; the line written when
    the file is new. Other settings refuse (a new file for them)."""
    rows = read_jsonl(path)
    head = next((r["header"] for r in rows if "header" in r), None)
    if head is None:
        if rows:
            raise Refused(f"{path}: rows without a header line; not a {header['tool']} file")
        path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(path, {"header": header})
        return []
    changed = [k for k in pinned
               if _digest(head.get(k, PINNED_DEFAULTS.get(k))) != _digest(header.get(k, PINNED_DEFAULTS.get(k)))]
    if changed:
        raise Refused(f"{path}: written before with other settings ({', '.join(changed)}); "
                      f"use another file for these")
    return [r for r in rows if "header" not in r]


def header_of(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        first = fh.readline()
    head = json.loads(first).get("header") if first.strip() else None
    if not isinstance(head, dict):
        raise Refused(f"{path}: no header line")
    return head


def _digest(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def code_digest() -> str:
    """The code a candidate list comes from: the fusion and the plan."""
    h = hashlib.sha256()
    for rel in ("app/retrieval.py", "app/language_plan.py"):
        f = ROOT / rel
        h.update(rel.encode() + b"\0" + (f.read_bytes() if f.exists() else b"") + b"\0")
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------- capture


def as_sent(query: str, hits: list[dict[str, Any]], queries: dict[str, str] | None,
            depth: int) -> list[dict[str, Any]]:
    """The top `depth` fused records as `retrieval._rerank_held` sends them:
    each record's JUDGED_CHUNKS matched chunks under its title line, and the
    query each is judged against — the plan's translation into the record's
    language, else the core query. Texts uncut (the window is the arm's)."""
    from app import retrieval
    variants = {k.lower(): v for k, v in (queries or {}).items() if v}
    out = []
    for h in hits[:depth]:
        title = h.get(retrieval.HEAD) or ""
        lang = (h.get("lang") or "").lower()
        chunks = []
        for c in (h.get(retrieval.MATCHED) or [h])[:retrieval.JUDGED_CHUNKS]:
            body = c.get("text") or ""
            chunks.append({"chunk_id": c.get("chunk_id"), "text": f"{title}\n\n{body}" if title else body})
        out.append({"uid": retrieval.record_of(h), "book_id": h.get("book_id"), "lang": lang or None,
                    "leg_ranks": dict(h.get("leg_ranks") or {}),
                    "query_sent": variants.get(lang) or query, "chunks": chunks})
    return out


@contextmanager
def intercepted(got: list[dict[str, Any]]) -> Iterator[set[str]]:
    """The live search, stopped at the reranker: `retrieval._reranked`
    keeps what it is handed (in `got`) and gives the fused order back. A
    collection with no reranker is lent the first held one's, so the search
    attaches the title lines the reranker would read; the names lent to are
    yielded."""
    from app import collections as collections_mod, retrieval
    real_reranked, real_for = retrieval._reranked, collections_mod.reranker_for
    lock = threading.Lock()
    lent_to: set[str] = set()
    lender: list[Any] = []

    def lent() -> Any:
        if not lender:
            found = None
            for c in sorted(collections_mod.load_all(skip_unreadable=True), key=lambda c: c.name):
                found = real_for(c)
                if found is not None:
                    break
            lender.append(found)
        return lender[0]

    def reranker_for(collection: Any) -> Any:
        own = real_for(collection)
        if own is not None:
            return own
        with lock:
            lent_to.add(getattr(collection, "name", ""))
        return lent()

    def stand_in(query: str, hits: list[dict[str, Any]], recipe: Any, budget_s: float, depth: int,
                 queries: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], bool, str]:
        kept = {"query": query, "queries": dict(queries or {}), "depth": depth,
                "candidates": as_sent(query, hits, queries, depth),
                "collection": next((h.get("source_collection") for h in hits if h.get("source_collection")), None),
                "reranker": None if recipe is None else {"url": recipe.url, "api": recipe.api,
                                                         "model": recipe.model, "window": recipe.window}}
        with lock:
            got.append(kept)
        return hits, False, CAPTURED

    retrieval._reranked = stand_in
    collections_mod.reranker_for = reranker_for
    try:
        yield lent_to
    finally:
        retrieval._reranked = real_reranked
        collections_mod.reranker_for = real_for


def live_search(collection: str, query: str, *, k: int, translate: bool, depth: int) -> dict[str, Any]:
    """The Ask tab's search of one collection (chat_eval.ask_direct's path)."""
    from app import query_service
    return query_service.run_search([collection], query, k, "hybrid", translate, rerank_depth=depth)


def _withheld(result: dict[str, Any]) -> list[str]:
    """chat_eval.withheld_legs, the capture's own stand-in reason left out."""
    shown = dict(result)
    shown["rerank_withheld"] = [w for w in result.get("rerank_withheld") or []
                                if not (isinstance(w, dict) and w.get("reason") == CAPTURED)]
    return chat_eval.withheld_legs(shown)


def _data_version(names: list[str]) -> Any:
    try:
        return chat_eval.data_version(names)
    except Exception as e:   # noqa: BLE001 — a snapshot that cannot be read is pinned as such
        return {"unreadable": f"{type(e).__name__}: {e}"[:200]}


def capture(js: JudgedSet, out: Path, *, translate: bool = True, speed: bool = False,
            depth: int | None = None, k: int = K,
            limit: int | None = None, search: Callable[..., dict[str, Any]] | None = None,
            settle: Callable[[list[str]], dict[str, bool]] | None = None,
            data_version: Callable[[list[str]], Any] | None = None,
            stop: Callable[[], bool] = _never, say: Callable[[str], None] = print) -> dict[str, Any]:
    """Each item's candidates, once, appended to `out` as it is searched."""
    from app import retrieval
    from app import translate as translate_mod
    depth = depth or retrieval.RERANK_TOP_N
    search = search or live_search
    items = first_per_kind(js.items, limit)
    names = sorted({i.collection for i in js.items})
    header = {"tool": "rerank_bench capture", "set": js.name, "format": js.format, "source": js.source,
              "translate": translate, "speed": speed, "depth": depth, "k": k,
              "judged_chunks": retrieval.JUDGED_CHUNKS,
              "relevant": js.relevant, "exhaustive": js.exhaustive,
              "retrieval": {"catalog_first": retrieval.CATALOG_FIRST, "rerank_fuse": retrieval.RERANK_FUSE,
                            "language_order": retrieval.RERANK_LANGUAGE_ORDER},
              "code": code_digest(), "data_version": (data_version or _data_version)(names)}
    done = {r["key"] for r in _rows_after(out, header, CAPTURE_PINNED)}
    todo = [i for i in items if i.key not in done]
    stats = {"set": js.name, "items": len(items), "already": len(items) - len(todo), "captured": 0,
             "stopped": False}
    if not todo:
        return stats
    # measuring.md §13: the vector leg settled in THIS process first
    settled = (settle or chat_eval.settle)(sorted({i.collection for i in todo}))
    unsettled = sorted(n for n, ok in settled.items() if not ok)
    if unsettled:
        raise chat_eval.SearchWithheld(f"the vector leg is not settled here for {', '.join(unsettled)} "
                                       f"({settled}); nothing is captured")
    judge = js.judge or judge_by_record
    got: list[dict[str, Any]] = []
    with intercepted(got) as lent_to:
        for n, item in enumerate(todo, 1):
            if stop():
                stats["stopped"] = True
                break
            got.clear()
            t0 = time.perf_counter()
            # THE ASK TAB'S SPEED (operator, 2026-11-10: "Rerun in speed mode"):
            # the query translated by the chat model already on the card, MADLAD
            # never asked — the web's own switch, `translate.speed` (app/web.py)
            with translate_mod.speed(speed):
                result = search(item.collection, item.query, k=k, translate=translate, depth=depth)
            seconds = round(time.perf_counter() - t0, 3)
            legs = _withheld(result)
            if legs:
                raise chat_eval.SearchWithheld(f"{js.name} {item.key}: the search withheld {'; '.join(legs)}: "
                                               f"nothing is written; run again once the leg is back")
            if not got:
                raise chat_eval.SearchWithheld(f"{js.name} {item.key}: no list reached the reranker step "
                                               f"(withheld: {result.get('withheld')}); nothing is written")
            kept = got[0]
            for cand in kept["candidates"]:
                for ch in cand["chunks"]:
                    verdict = judge(item, cand["uid"], ch["chunk_id"], cand["book_id"])
                    ch["target"], ch["grade"] = verdict if verdict is not None else (None, None)
            reranker = dict(kept["reranker"] or {}, lent=item.collection in lent_to)
            append_jsonl(out, {"key": item.key, "kind": item.kind, "lang": item.lang, "query": item.query,
                               "collection": item.collection, "targets": item.targets,
                               "plan": {"core": kept["query"], "translations": kept["queries"],
                                        "query_lang": result.get("query_lang")},
                               "reranker": reranker, "candidates": kept["candidates"], "seconds": seconds})
            stats["captured"] += 1
            if n % 25 == 0 or n == len(todo):
                say(f"[{js.name}] captured {n}/{len(todo)} ({stats['already']} before)")
    return stats


# ---------------------------------------------------------------- rerank


@dataclass(frozen=True)
class Arm:
    """A model on the rerank endpoint, and what its query carries."""
    name: str
    model: str
    #: one instruction for every collection
    instruction: str | None = None
    #: or one per collection (COLLECTION_INSTRUCTIONS, --instructions)
    instructions: dict[str, str] | None = None
    form: str = DOCUMENTED_FORM
    #: "live": each record against its language's variant, as the node;
    #: "typed": every record against the core query, one call
    queries: str = "live"

    def query_for(self, collection: str, query: str) -> str:
        instruction = _instruction_of(self.instructions, collection) if self.instructions is not None \
            else self.instruction
        return self.form.format(instruction=instruction, query=query) if instruction else query


def _plugin_of(collection: str) -> str | None:
    from app import collections as collections_mod
    try:
        c = collections_mod.by_name(collection)
    except Exception:                       # noqa: BLE001 — no node config: the name alone
        return None
    return getattr(c, "plugin", None) if c is not None else None


def _instruction_of(table: dict[str, str] | None, collection: str, *,
                    plugin_of: Callable[[str], str | None] = _plugin_of) -> str | None:
    """The instruction for a collection: by its NAME where the table has it,
    else by its PLUGIN (2026-11-13: the operator renamed normattiva-nemo to
    "Normattiva" and normattiva to "normattiva_old"; a name is the node's
    local choice, the plugin is what the collection holds)."""
    table = table or {}
    if collection in table:
        return table[collection]
    plugin = plugin_of(collection)
    return table.get(plugin) if plugin else None


def post_rerank(recipe: Any, query: str, documents: list[str], timeout: float) -> list[float]:
    """One /v1/rerank request as `_rerank_held` sends it (pipeline_eval's
    call_reranker), its scores in document order. The deadline is now, so
    `_rerank_call` asks nothing again after a 5xx: a retry would hide in the
    time this request is measured for."""
    from app import retrieval
    body = {"query": query, "documents": documents, "top_n": len(documents), "model": recipe.model}
    data = retrieval._rerank_call(recipe, body, timeout, time.monotonic())
    rows = data["results"] if isinstance(data, dict) else data
    got = {int(o["index"]): float(o["relevance_score"] if "relevance_score" in o else o["score"]) for o in rows}
    if sorted(got) != list(range(len(documents))):
        raise BadAnswer(f"the reranker answered for {len(got)} of {len(documents)} documents")
    return [got[i] for i in range(len(documents))]


def recipe_for(row: dict[str, Any], arm: Arm, *, url: str | None, window: int | None) -> Any:
    from app import collections as collections_mod
    captured = row.get("reranker") or {}
    if url is None and captured.get("api") not in (None, "jina"):
        raise Refused(f"{row['collection']}: its reranker speaks {captured.get('api')!r}; give --url, "
                      f"a /v1/rerank (Jina) endpoint")
    where = url or captured.get("url")
    if not where:
        raise Refused(f"{row['collection']}: no reranker URL was captured; give --url")
    size = window if window is not None else int(captured.get("window") or 0)
    return collections_mod.RerankRecipe(url=where, api="jina", model=arm.model, window=size)


def rerank_row(row: dict[str, Any], arm: Arm, recipe: Any, *,
               post: Callable[[Any, str, list[str], float], list[float]] = post_rerank,
               cut: Callable[[list[str], Any], list[str]] | None = None,
               timeout: float = 300.0) -> dict[str, Any]:
    """One query's requests, as the live path makes them, each timed."""
    from app import retrieval
    from app.rage import dispatch
    cut = cut or retrieval.fit_to_window
    owner: list[tuple[int, int]] = []
    texts: list[str] = []
    for ci, cand in enumerate(row["candidates"]):
        for hi, ch in enumerate(cand["chunks"]):
            owner.append((ci, hi))
            texts.append(ch["text"])
    # the window of the ARM's tokens, before the clock (as the live path:
    # warmed, cut, then the calls)
    sent = cut(texts, recipe)
    groups: dict[str, list[int]] = {}
    for t, (ci, _) in enumerate(owner):
        q = row["plan"]["core"] if arm.queries == "typed" else row["candidates"][ci]["query_sent"]
        groups.setdefault(q, []).append(t)
    flat: dict[int, float] = {}
    calls = []
    with dispatch.interactive(dispatch.Level.RERANK):      # one list, one claim (_rerank_list)
        for q, idx in groups.items():
            part = [sent[t] for t in idx]
            distinct = list(dict.fromkeys(part))
            query = arm.query_for(row["collection"], q)
            t0 = time.perf_counter()
            got = post(recipe, query, distinct, timeout)
            ms = (time.perf_counter() - t0) * 1000.0
            calls.append({"query": query, "documents": len(distinct), "ms": round(ms, 2)})
            at = {text: i for i, text in enumerate(distinct)}
            for local, t in enumerate(idx):
                flat[t] = got[at[part[local]]]
    scores: list[list[float]] = [[] for _ in row["candidates"]]
    for t, (ci, _) in enumerate(owner):
        scores[ci].append(flat[t])
    return {"key": row["key"], "scores": scores, "calls": calls,
            "ms": round(sum(c["ms"] for c in calls), 2),
            "cut": sum(1 for a, b in zip(texts, sent) if a != b)}


def arm_path(runs: Path, capture_path: Path, arm: str) -> Path:
    return runs / f"{capture_path.stem}.{arm}.jsonl"


def rerank(capture_path: Path, arm: Arm, *, runs: Path | None = None, url: str | None = None,
           window: int | None = None, limit: int | None = None, concurrency: int = 1,
           timeout: float = 300.0, post: Callable[[Any, str, list[str], float], list[float]] = post_rerank,
           cut: Callable[[list[str], Any], list[str]] | None = None,
           pid: Callable[[str, str], int | None] | None = None,
           pin: Callable[[str, str], AbstractContextManager[Any]] | None = None,
           stop: Callable[[], bool] = _never, say: Callable[[str], None] = print) -> dict[str, Any]:
    """Every captured query reranked by `arm`, appended as it is answered."""
    if arm.name == FUSED:
        raise Refused(f"{FUSED} is the fused order: it is read from the capture, never reranked")
    cap_header = header_of(capture_path)
    rows = [r for r in read_jsonl(capture_path) if "header" not in r]
    kinds: dict[str, int] = {}
    picked = []
    for r in rows:
        if not limit or kinds.get(r.get("kind") or "", 0) < limit:
            kinds[r.get("kind") or ""] = kinds.get(r.get("kind") or "", 0) + 1
            picked.append(r)
    if arm.instructions is not None:
        missing = sorted({r["collection"] for r in picked} - set(arm.instructions))
        if missing:
            raise Refused(f"no instruction for {', '.join(missing)}: give --instructions FILE.json "
                          f"({{collection: instruction}})")
    out = arm_path(runs or capture_path.parent, capture_path, arm.name)
    header = {"tool": "rerank_bench rerank", "arm": arm.name, "model": arm.model,
              "instruction": arm.instruction, "instructions": arm.instructions, "form": arm.form,
              "queries": arm.queries, "url": url, "window": window, "capture": _digest(cap_header),
              "capture_file": capture_path.name, "concurrency": concurrency}
    done = {r["key"] for r in _rows_after(out, header, ARM_PINNED)}
    todo = [r for r in picked if r["key"] not in done]
    stats = {"arm": arm.name, "queries": len(picked), "already": len(picked) - len(todo), "reranked": 0,
             "stopped": False, "file": str(out)}
    if not todo:
        return stats
    say(f"rerank_bench {arm.name} ({arm.model}): one arm at a time, the node paused and nothing else on "
        f"the card (measuring.md §6, §10) — the runner's job, not checked here")
    first = recipe_for(todo[0], arm, url=url, window=window)
    lock = threading.Lock()
    failed = threading.Event()

    def one(row: dict[str, Any]) -> None:
        if stop() or failed.is_set():
            return
        recipe = recipe_for(row, arm, url=url, window=window)
        before = pid(recipe.url, recipe.model) if pid else None
        got = rerank_row(row, arm, recipe, post=post, cut=cut, timeout=timeout)
        after = pid(recipe.url, recipe.model) if pid else None
        got["warm"] = pid is None or (before is not None and before == after)
        with lock:
            append_jsonl(out, got)
            stats["reranked"] += 1
            if stats["reranked"] % 25 == 0 or stats["reranked"] == len(todo):
                say(f"[{arm.name}] {stats['reranked']}/{len(todo)} ({stats['already']} before)")

    def guarded(row: dict[str, Any]) -> None:
        try:
            one(row)
        except BaseException:
            failed.set()                # the others start nothing more; the run stops here
            raise

    # pinned first, then warmed with a throwaway request: the first timed
    # query finds the model loaded, and nothing evicts it meanwhile
    with (pin(first.url, first.model) if pin else nullcontext()):
        post(first, "warm", ["warm"], timeout)
        if concurrency <= 1:
            for row in todo:
                if stop():
                    break
                one(row)
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                for f in [ex.submit(guarded, r) for r in todo]:
                    f.result()
    stats["stopped"] = stats["reranked"] < len(todo) and stop()
    return stats


# ---------------------------------------------------------------- score


def ranked(row: dict[str, Any], arm_row: dict[str, Any] | None, settings: dict[str, Any], *,
           raw: bool = False) -> list[tuple[int, int, float | None]]:
    """(candidate, its chunk, its score) in the arm's order: A0 (no arm row)
    the fused order on each record's best fused chunk; an arm the order as
    shipped — `_rerank_order` on each record's best chunk, the language
    slots where captured so, then CATALOG_FIRST unless `raw`."""
    from app import retrieval
    cands = row["candidates"]
    if arm_row is None:
        return [(i, 0, None) for i in range(len(cands))]
    best: dict[int, tuple[float, int]] = {}
    for i, chunk_scores in enumerate(arm_row["scores"]):
        for j, s in enumerate(chunk_scores):
            if i not in best or s > best[i][0]:
                best[i] = (s, j)
    scores = [best[i][0] for i in range(len(cands))]
    head = [{"lang": c.get("lang"), "leg_ranks": c.get("leg_ranks") or {}} for c in cands]
    variants = len({c["query"] for c in arm_row.get("calls") or []})
    if settings.get("language_order") == "slots" and variants > 1:
        order = retrieval._by_language_slots(head, scores)
    else:
        order = retrieval._rerank_order(scores, fuse=bool(settings.get("rerank_fuse")))
    if settings.get("catalog_first") and not raw:
        order = retrieval._catalog_first(order, head)
    return [(i, best[i][1], best[i][0]) for i in order]


def _dcg(gains: Sequence[float]) -> float:
    return sum(g / math.log2(p + 2) for p, g in enumerate(gains))


def query_metrics(row: dict[str, Any], order: Sequence[tuple[int, int, float | None]],
                  relevant: int) -> dict[str, Any]:
    """One query's measures on the first TOP of `order`."""
    cands = row["candidates"]
    top = list(order[:TOP])
    labels = [(cands[i]["chunks"][j].get("target"), cands[i]["chunks"][j].get("grade")) for i, j, _ in top]
    first = next((p for p, (_, g) in enumerate(labels, 1) if g is not None and g >= relevant), None)
    seen: set[str] = set()
    gains = []
    for t, g in labels:
        if t is None or g is None or t in seen:
            gains.append(0.0)
            continue
        seen.add(t)
        gains.append(2.0 ** g - 1.0)
    ideal = _dcg([2.0 ** g - 1.0 for g in sorted(row.get("targets", {}).values(), reverse=True)[:TOP]])
    qlang = (row.get("lang") or (row.get("plan") or {}).get("query_lang") or "").lower() or None
    other = (sum(1 for i, _, _ in top if (cands[i].get("lang") or "und").lower() != qlang) / len(top)
             if qlang and top else None)
    return {"mrr@10": 1.0 / first if first else 0.0, "ndcg@10": _dcg(gains) / ideal if ideal else 0.0,
            "r@1": float(first == 1), "r@10": float(first is not None), "first": first,
            "other_lang@10": other, "judged@10": (sum(1 for _, g in labels if g is not None) / len(top)
                                                 if top else None), "lang": qlang}


def _mean(xs: Iterable[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def summarise(per: list[dict[str, Any]], lists_ms: list[float] | None = None,
              exhaustive: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(per)}
    for m in ("mrr@10", "ndcg@10", "r@1", "r@10", "other_lang@10"):
        out[m] = _mean(p[m] for p in per)
    if not exhaustive:
        out["judged@10"] = _mean(p["judged@10"] for p in per)
    it = [p["mrr@10"] for p in per if p["lang"] == "it"]
    en = [p["mrr@10"] for p in per if p["lang"] == "en"]
    out["it_en_gap"] = ({"mrr@10": round(sum(it) / len(it) - sum(en) / len(en), 4), "it": len(it), "en": len(en)}
                        if it and en else None)
    if lists_ms is not None:
        from tools.legal_pilot import timing
        t = timing(lists_ms)
        out["list_ms"] = {"p50": t["p50"], "p95": t["p95"], "mean": None if t["mean"] is None else round(t["mean"], 1),
                          "warm": len(lists_ms)}
    return out


def versus(a: list[float], b: list[float]) -> dict[str, Any]:
    """a - b on paired per-query values: the mean, its 95 % bootstrap
    interval and the two-sided randomization p (encoder_bakeoff.paired)."""
    from tools.encoder_bakeoff import paired
    delta, lo, hi, p = paired(a, b, trials=PERMUTATIONS, seed=SEED)
    return {"delta": round(delta, 4), "interval95": [round(lo, 4), round(hi, 4)], "p": round(p, 4),
            "better": sum(1 for x, y in zip(a, b) if x > y + 1e-12),
            "worse": sum(1 for x, y in zip(a, b) if x < y - 1e-12), "queries": len(a)}


def _lean(row: dict[str, Any]) -> dict[str, Any]:
    """A captured row without its texts: what scoring reads."""
    for c in row.get("candidates") or []:
        for ch in c.get("chunks") or []:
            ch.pop("text", None)
    return row


def score_set(capture_path: Path, arms: Sequence[str], *, runs: Path | None = None, base: str = BASE,
              raw: bool = False) -> dict[str, Any]:
    head = header_of(capture_path)
    rows = {r["key"]: _lean(r) for r in read_jsonl(capture_path) if "header" not in r}
    runs = runs or capture_path.parent
    by_arm: dict[str, dict[str, dict[str, Any]] | None] = {}
    settings: dict[str, Any] = {}
    for a in arms:
        if a == FUSED:
            by_arm[a] = None
            continue
        p = arm_path(runs, capture_path, a)
        if not p.exists():
            raise Refused(f"{a}: no reranked file {p}")
        settings[a] = header_of(p)
        by_arm[a] = {r["key"]: r for r in read_jsonl(p) if "header" not in r}
    keys = [k for k in rows if all(v is None or k in v for v in by_arm.values())]
    missing = {a: sum(1 for k in rows if k not in v) for a, v in by_arm.items() if v is not None}
    per: dict[str, dict[str, dict[str, Any]]] = {}
    for a, v in by_arm.items():
        per[a] = {k: query_metrics(rows[k], ranked(rows[k], None if v is None else v[k], head["retrieval"],
                                                   raw=raw), head["relevant"]) for k in keys}
    kinds = sorted({rows[k].get("kind") or "" for k in keys})

    def block(subset: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for a, v in by_arm.items():
            ms = None if v is None else [v[k]["ms"] for k in subset if v[k].get("warm", True)]
            s = summarise([per[a][k] for k in subset], ms, exhaustive=head["exhaustive"])
            if v is not None:
                s["cold"] = sum(1 for k in subset if not v[k].get("warm", True))
            if base in per and a != base and subset:
                s["vs_base"] = versus([per[a][k]["mrr@10"] for k in subset], [per[base][k]["mrr@10"] for k in subset])
            out[a] = s
        return out

    result: dict[str, Any] = {"set": head["set"], "capture": str(capture_path), "queries": len(rows),
                              "paired": len(keys), "missing": missing, "arms": block(keys),
                              "settings": settings, "raw": raw, "base": base}
    if len(kinds) > 1:
        result["kinds"] = {kd: block([k for k in keys if (rows[k].get("kind") or "") == kd]) for kd in kinds}
    return result


def _f(x: Any, nd: int = 3) -> str:
    return "–" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _gap(s: dict[str, Any]) -> str:
    gap = s.get("it_en_gap")
    return "–" if not gap else f"{gap['mrr@10']:+.3f} ({gap['it']}/{gap['en']})"


def _latency(s: dict[str, Any]) -> str:
    lat = s.get("list_ms")
    return "–" if not lat else f"{_f(lat['p50'], 0)} / {_f(lat['p95'], 0)} ({lat['warm']})"


def _delta(s: dict[str, Any], interval: bool = True) -> tuple[str, str]:
    vs = s.get("vs_base")
    if not vs:
        return "–", "–"
    lo, hi = vs["interval95"]
    text = f"{vs['delta']:+.3f}" + (f" [{lo:+.3f}, {hi:+.3f}]" if interval else "")
    return text, _f(vs["p"], 4)


def markdown(results: list[dict[str, Any]], base: str = BASE) -> str:
    lines: list[str] = []
    for res in results:
        raw = " (raw: no catalogue-first)" if res["raw"] else ""
        lines += [f"### {res['set']} — {res['paired']} paired queries of {res['queries']}{raw}", ""]
        if any(res["missing"].values()):
            lines += ["Not in every arm: " + ", ".join(f"{a} {n}" for a, n in res["missing"].items() if n), ""]
        lines += [f"| arm | n | MRR@10 | nDCG@10 | R@1 | R@10 | other-lang@10 | it−en MRR (it/en) | judged@10 "
                  f"| list ms p50 / p95 (warm) | ΔMRR vs {base} [95 %] | p |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for a, s in res["arms"].items():
            delta, p = _delta(s)
            lines.append(f"| {a} | {s['n']} | {_f(s['mrr@10'])} | {_f(s['ndcg@10'])} | {_f(s['r@1'])} "
                         f"| {_f(s['r@10'])} | {_f(s['other_lang@10'], 2)} | {_gap(s)} "
                         f"| {_f(s.get('judged@10'), 2)} | {_latency(s)} | {delta} | {p} |")
        for kd, blk in (res.get("kinds") or {}).items():
            lines += ["", f"{res['set']}, kind **{kd or '(none)'}**:", "",
                      f"| arm | n | MRR@10 | nDCG@10 | R@1 | R@10 | ΔMRR vs {base} | p |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|"]
            for a, s in blk.items():
                delta, p = _delta(s, interval=False)
                lines.append(f"| {a} | {s['n']} | {_f(s['mrr@10'])} | {_f(s['ndcg@10'])} | {_f(s['r@1'])} "
                             f"| {_f(s['r@10'])} | {delta} | {p} |")
        lines.append("")
    return "\n".join(lines)


def show(capture_path: Path, arms: Sequence[str], *, runs: Path | None = None, limit: int = 3,
         top: int = 5) -> str:
    """The pilot's reading (measuring.md §12): the first `limit` queries of
    every kind, one at a time — the query and what was sent, each arm's first
    records with their scores and judgement, its calls and their time."""
    head = header_of(capture_path)
    runs = runs or capture_path.parent
    arm_rows: dict[str, dict[str, dict[str, Any]] | None] = {}
    for a in arms:
        p = arm_path(runs, capture_path, a)
        arm_rows[a] = None if a == FUSED else {r["key"]: r for r in read_jsonl(p) if "header" not in r}
    seen: dict[str, int] = {}
    lines = [f"# Pilot reading: {head['set']} ({capture_path.name})", ""]
    for row in (r for r in read_jsonl(capture_path) if "header" not in r):
        kd = row.get("kind") or ""
        if seen.get(kd, 0) >= limit:
            continue
        seen[kd] = seen.get(kd, 0) + 1
        plan = row["plan"]
        lines += [f"## [{kd}] {row['key']} ({row.get('lang') or plan.get('query_lang')}): {row.get('query')}", "",
                  f"- core: {plan.get('core')!r}; translations: "
                  f"{json.dumps(plan.get('translations') or {}, ensure_ascii=False)}",
                  f"- {len(row['candidates'])} candidates; targets {json.dumps(row['targets'], ensure_ascii=False)}"
                  f"; reranker captured {row.get('reranker')}", ""]
        for a, rows in arm_rows.items():
            ar = None if rows is None else rows.get(row["key"])
            if rows is not None and ar is None:
                lines.append(f"**{a}**: not reranked yet")
                continue
            order = ranked(row, ar, head["retrieval"])
            m = query_metrics(row, order, head["relevant"])
            lines.append(f"**{a}**: first relevant at {m['first']}; " +
                         ("" if ar is None else "calls " + "; ".join(
                             f"{c['documents']} docs {c['ms']:.0f} ms against {c['query']!r}" for c in ar["calls"])
                          + f"; warm {ar.get('warm')}"))
            for p, (i, j, s) in enumerate(order[:top], 1):
                c = row["candidates"][i]
                ch = c["chunks"][j]
                mark = "REL " if ch.get("grade") is not None and ch["grade"] >= head["relevant"] else "    "
                text = " ".join((ch.get("text") or "").split())[:140]
                lines.append(f"    {p:>2}. {mark}{c['uid']} [{c.get('lang')}] "
                             f"{'' if s is None else f'{s:.4f} '}{text}")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- main


_stopping = threading.Event()


def _on_signal(signum: int, frame: Any) -> None:   # noqa: ARG001
    print("rerank_bench: stopping after the current query (exit 130)", file=sys.stderr, flush=True)
    _stopping.set()


def _instructions(spec: str | None, path: Path | None) -> tuple[str | None, dict[str, str] | None]:
    if path is not None:
        table = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(table, dict) or not all(isinstance(v, str) for v in table.values()):
            raise Refused(f"{path}: an object {{collection: instruction}}")
        return None, dict(table)
    if spec == "collection":
        return None, dict(COLLECTION_INSTRUCTIONS)
    return (spec or None), None


def _node_pid(url: str, model: str) -> int | None:
    from app import model_load
    return model_load.backend_pid(url, model, 0.0)


def _node_pin(url: str, model: str) -> AbstractContextManager[Any]:
    from app import model_pins
    return model_pins.holding(url, model, f"rerank_bench pid {os.getpid()}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-person", action="store_true", dest="as_person",
                    help="hold the node's claim as a person's question does (corpus work "
                         "stands aside): for timings of what a person waits; by default a "
                         "harness holds none (app/rage/dispatch.py `as_harness`)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("format", choices=("gutenberg", "known-item", "umbrela", "jurifindit"))
    c.add_argument("sources", nargs="+", type=Path)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--name", help="the set's name in the tables (default: the format's)")
    c.add_argument("--collection")
    c.add_argument("--works", type=Path)
    c.add_argument("--limit", type=int, help="the first N queries of every kind (the pilot)")
    c.add_argument("--no-translate", action="store_true", help="the API's search: no translations")
    c.add_argument("--speed", action="store_true",
                   help="the Ask tab's Speed: the chat model translates the query, MADLAD is never asked "
                        "(pinned in the file's header: a Quality file and a Speed file never mix)")
    c.add_argument("--depth", type=int, default=None, help="records captured (default RERANK_TOP_N, 50)")
    r = sub.add_parser("rerank")
    r.add_argument("capture", type=Path)
    r.add_argument("--arm", required=True)
    r.add_argument("--model", required=True, help="the model name the rerank endpoint serves")
    r.add_argument("--instruction", help="an instruction for every query, or `collection` "
                                         "(COLLECTION_INSTRUCTIONS); none: the model's default")
    r.add_argument("--instructions", type=Path, help="FILE.json {collection: instruction}")
    r.add_argument("--instruction-form", default=DOCUMENTED_FORM, dest="form")
    r.add_argument("--queries", choices=("live", "typed"), default="live")
    r.add_argument("--url", help="the /v1/rerank endpoint (default: the captured reranker's)")
    r.add_argument("--window", type=int, help="the cut, in the arm's tokens (default: the captured window)")
    r.add_argument("--runs", type=Path, help="where the arm's file goes (default: beside the capture)")
    r.add_argument("--limit", type=int)
    r.add_argument("--concurrency", type=int, default=1)
    r.add_argument("--timeout", type=float, default=300.0)
    s = sub.add_parser("score")
    s.add_argument("captures", nargs="+", type=Path)
    s.add_argument("--arms", required=True)
    s.add_argument("--base", default=BASE)
    s.add_argument("--runs", type=Path)
    s.add_argument("--raw", action="store_true", help="the reranker's order, without CATALOG_FIRST")
    s.add_argument("--out", type=Path, help="PREFIX: PREFIX.json and PREFIX.md")
    w = sub.add_parser("show")
    w.add_argument("capture", type=Path)
    w.add_argument("--arms", required=True)
    w.add_argument("--runs", type=Path)
    w.add_argument("--limit", type=int, default=3)
    w.add_argument("--top", type=int, default=5)
    a = ap.parse_args(argv)
    from app.rage import dispatch as _dispatch
    _dispatch.as_harness(not getattr(a, "as_person", False))   # a harness, not a person
    try:
        if a.cmd in ("capture", "rerank"):
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, _on_signal)
        if a.cmd == "capture":
            js = load_set(a.format, a.sources, collection=a.collection, works=a.works)
            if a.name:
                js.name = a.name
            got = capture(js, a.out, translate=not a.no_translate, speed=a.speed, depth=a.depth,
                          limit=a.limit,
                          stop=_stopping.is_set)
            print(json.dumps(got))
            return 130 if got["stopped"] else 0
        if a.cmd == "rerank":
            instruction, table = _instructions(a.instruction, a.instructions)
            arm = Arm(name=a.arm, model=a.model, instruction=instruction, instructions=table, form=a.form,
                      queries=a.queries)
            got = rerank(a.capture, arm, runs=a.runs, url=a.url, window=a.window, limit=a.limit,
                         concurrency=a.concurrency, timeout=a.timeout, pid=_node_pid, pin=_node_pin,
                         stop=_stopping.is_set)
            print(json.dumps(got))
            return 130 if got["stopped"] else 0
        arms = [x.strip() for x in a.arms.split(",") if x.strip()]
        if a.cmd == "show":
            print(show(a.capture, arms, runs=a.runs, limit=a.limit, top=a.top))
            return 0
        results = [score_set(p, arms, runs=a.runs, base=a.base, raw=a.raw) for p in a.captures]
        md = markdown(results, a.base)
        print(md)
        if a.out:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.with_suffix(".json").write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
            a.out.with_suffix(".md").write_text(md + "\n", encoding="utf-8")
        return 0
    except Refused as e:
        print(f"rerank_bench: refused: {e}", file=sys.stderr)
        return 2
    except chat_eval.SearchWithheld as e:
        print(f"rerank_bench: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    from app import process_exit
    process_exit.run(main)
