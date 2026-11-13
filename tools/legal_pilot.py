"""tools/legal_pilot.py — the pilot's measurement over a BUILT legal
collection (gazzetta-plugin.md §7.2; normattiva-multivigenza-review.md §10;
docs/notes/legal-collection-template.md): what the platform answers on
JuriFindIT's expert questions and on dated questions, scored
DETERMINISTICALLY — a gold article is held or not, a version is the one in
force at the date or not — never by an LLM judge (R2's method).

    python -m tools.legal_pilot fetch         --out DIR
    python -m tools.legal_pilot coverage      --out DIR --collection normattiva
    python -m tools.legal_pilot run           --out DIR --collection normattiva [--k 20]
    python -m tools.legal_pilot temporal-make --collection normattiva --out FILE.jsonl [--limit 200]
    python -m tools.legal_pilot temporal-run  --collection normattiva --questions FILE.jsonl --out DIR

fetch          the dataset, through encoder_bakeoff.fetch — CC-BY-NC-SA-4.0, a
               gated class: record the acknowledgement before any use
               (invariant 9).
coverage       which gold articles the collection holds, BY KEY. The dataset's
               corpus node carries the act as `file_name`
               (`§akn§it§act§rd§STATO_ITALIANO§1942-03-16§0262§ITA`: type,
               date, number) and the article as `path` (`…__article_1167`);
               the act resolves through the collection's own `validity_works`
               keys — the URN:NIR every version carries — and the article
               through the shown version's chunks, whose `chapter` is the
               article's label. No text matching anywhere. A question none of
               whose gold articles is held is EXCLUDED from `run` and listed:
               EU regulations are, Normattiva does not hold them.
run            every covered question through `app.ask.results.search` as the
               node is configured (its reranker included — or, `--reranker
               off` / `--reranker from:<collection>`, none, or that collection's
               lent, for the comparison of piece 5, operator 2026-11-08: "compare
               time and quality by adding 5"), each question's search TIMED
               (seconds: mean, p50, p95, total), recall@k, mrr@k and
               ndcg@k over the gold node ids (encoder_bakeoff.metrics_for, the
               hand-pinned definitions), and the REACH analysis: of the gold
               articles the top-k missed, how many sit in an act one declared
               relation away from a top-k hit's act (`app.ask.traverse`, either
               direction). That number is the measured upper bound of what
               widening the candidate set along the edges could add — the
               question the review left open, answered before anything is
               built for it.
pool           POOLING (TREC's; MS MARCO's re-ranking task), so that two
               collections are compared on ONE candidate set before either is
               fully embedded (operator 2026-11-08: "run a comparison test
               before doing the full embedding"): every covered question's
               top `--depth` records by the TEXT leg alone (no encoder in it),
               plus the gold records, written to runs/pool-<name>.json. A
               collection that embeds only some records would otherwise be
               measured on a dense leg that finds its gold by construction.
enqueue        the pool's records not embedded, queued for embedding in a
               collection (the lazy queue the search itself fills,
               `retrieval._enqueue_lazy_embed`); with `embed_mode = "lazy"`
               its worker embeds those and nothing else.
run --pool     every leg of every search restricted to the pool BEFORE it
               ranks — the facet filters' own prefilter (`book_ids`, 05
               §5.2), as tools/measure/entity_boost_trecpm.py restricts — so
               each collection ranks exactly the same records; refused while
               a pool record is not embedded in the collection run.
run --pilot N  measuring.md §12: the first N covered questions (per
               `category` where the dataset has one) on exactly the run's
               settings, written for a reader to runs/<collection>/pilot-<tag>.md;
               `pilot-ok` records the reading (tools/measure/chat_eval.py's),
               and a whole `run` without one for its settings and code refuses.
               Every run settles the vector leg first and stops at a search
               that withheld a leg or its reranker, nothing written (§13).
compare        two run files over the same questions, paired by question:
               each metric's mean difference, its 95 % bootstrap interval and
               Fisher's randomization p (encoder_bakeoff.paired, Smucker et
               al. CIKM 2007), and the seconds a question of each. Refused
               when the two did not run the same questions or pool.
temporal-make  dated question STUBS mined from the version table: an article
               whose text differs between two consecutive versions of one act,
               a date inside the earlier version's validity, and a numeric
               nugget present in the earlier text and absent from the later.
               They are stubs: a person reads them, keeps the ones that ask a
               real question, and edits the wording (R2 screened its 209 by
               experts; the screening is the measurement's honesty).
temporal-run   each dated question through search(as_of=date): did the version
               in force at the date come back for that article (BY UID — the
               version is a key lookup, invariant 4), does its text carry the
               nugget; and the control, the same question undated: does the
               current version come back and the nugget vanish. The second is
               R2's 0 % provenance of a current-only index, measured here.

Everything reads the node's own stores read-only; nothing writes to a
collection. Runs land under --out/runs as JSON with the collection id and
snapshot named, so two runs are comparable.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import encoder_bakeoff as bakeoff  # noqa: E402 — fetch, metrics_for, LICENCE
from tools.measure import chat_eval  # noqa: E402 — settle, withheld_legs, pilot_ok, ask_code (§12, §13)

KS = (1, 3, 5, 10, 20)
DEFAULT_K = 20

#: an article's number, in the dataset's node path (`article_1167`), in a
#: chunk's label ("Art. 1167.", "CODICE CIVILE-art. 906", "Articolo 11") or in
#: a question; the Latin suffix kept, normalised: `1167`, `2043-bis`
_ARTICLE = re.compile(r"art(?:icle|icolo)?\.?\s*_?\s*(\d+)\s*[-_ ]?\s*"
                      r"(bis|ter|quater|quinquies|sexies|septies|octies|novies|decies)?\b", re.I)
#: any authority, not `stato` alone: D.M. 7 novembre 2001 n. 458 is held as
#: `urn:nir:ministero.giustizia:decreto:2001-11-07;458` and the coverage run
#: of 2026-10-13 counted it "act not held" (review §10.1)
_URN = re.compile(r"^urn:nir:([^:]+):([^:]+):(\d{4}-\d{2}-\d{2});(\d+)")
_NUMBER = re.compile(r"\d[\d.,]*\d|\d")
#: JuriFindIT's act-type abbreviations → the URN:NIR type token, consulted
#: only where one (date, number) names several acts
TYPE_OF: dict[str, tuple[str, ...]] = {
    "rd": ("regio.decreto",), "rdl": ("regio.decreto.legge",), "l": ("legge",),
    "lcost": ("legge.costituzionale",), "dlgs": ("decreto.legislativo",), "dl": ("decreto.legge",),
    "dpr": ("decreto.del.presidente.della.repubblica",),
    "dpcm": ("decreto.del.presidente.del.consiglio.dei.ministri",),
    # a ministerial decree is `decreto` under its ministry's authority
    "dm": ("decreto.ministeriale", "decreto"),
    "dlt": ("decreto.legislativo.luogotenenziale",)}


def article_token(text: str) -> str | None:
    """`1167`, `5-bis` — from a node path, a chunk label or a sentence; None
    where no article number is named."""
    m = _ARTICLE.search(text or "")
    if not m:
        return None
    num, suffix = m.group(1), m.group(2)
    return f"{int(num)}-{suffix.lower()}" if suffix else str(int(num))


def act_of(file_name: str) -> tuple[str, str, int] | None:
    """`(type abbreviation, date, number)` of an Italian act from the
    dataset's `file_name`; None for anything else (EU acts, placeholders)."""
    parts = [p for p in (file_name or "").split("§") if p]
    if len(parts) < 7 or parts[0] != "akn" or parts[1] != "it":
        return None
    digits = re.sub(r"\D", "", parts[6])
    if not digits:
        return None
    return parts[3].lower(), parts[5], int(digits)


# ---- the collection, by key ------------------------------------------------------

class Held:
    """A built collection's acts and articles, read by key from its own
    stores: `validity_works` (every version's canonical ids), `validity` (the
    chain, the shown version), the records themselves for the article labels
    — cut through app.layers exactly as the stages cut them."""

    def __init__(self, collection, con) -> None:
        from app import collections as collections_mod
        self.collection, self.con = collection, con
        self.plugin = collections_mod.PLUGIN_REGISTRY[collection.plugin]
        self._acts: dict[tuple[str, int], list[tuple[str, str]]] | None = None
        self._records: dict[str, Any] = {}
        self._articles: dict[str, tuple[dict[str, list[int]], dict[int, str]]] = {}

    def acts(self) -> dict[tuple[str, int], list[tuple[str, str]]]:
        """`(date, number) → [(URN type token, work)]` from the URN keys."""
        if self._acts is None:
            out: dict[tuple[str, int], list[tuple[str, str]]] = {}
            for r in self.con.execute("SELECT key, work FROM validity_works WHERE key LIKE 'urn:nir:%'"):
                m = _URN.match(r["key"])
                if not m:
                    continue
                entry = (m.group(2), r["work"])
                lst = out.setdefault((m.group(3), int(m.group(4))), [])
                if entry not in lst:
                    lst.append(entry)
            self._acts = out
        return self._acts

    def work_for(self, act: tuple[str, str, int]) -> str | None:
        abbrev, day, number = act
        found = self.acts().get((day, number)) or []
        if len(found) == 1:
            return found[0][1]
        wanted = TYPE_OF.get(abbrev) or ()
        exact = [w for t, w in found if t in wanted]
        return exact[0] if len(exact) == 1 else None

    def keys_of(self, work: str) -> set[str]:
        return {r[0] for r in self.con.execute("SELECT key FROM validity_works WHERE work=?", (work,))}

    def shown(self, work: str) -> tuple[str, int] | None:
        r = self.con.execute("SELECT uid, native_id FROM validity WHERE work=? AND shown=1", (work,)).fetchone()
        return (r["uid"], int(r["native_id"])) if r else None

    def work_of(self, uid: str) -> str | None:
        r = self.con.execute("SELECT work FROM validity WHERE uid=?", (uid,)).fetchone()
        return r["work"] if r else None

    def version(self, uid: str) -> dict[str, Any] | None:
        r = self.con.execute("SELECT uid, work, valid_from, valid_to, shown FROM validity WHERE uid=?",
                             (uid,)).fetchone()
        return dict(r) if r else None

    def _row_and_doc(self, uid: str):
        if uid not in self._records:
            from app import plugin_io
            row = self.con.execute("SELECT * FROM documents WHERE uid=?", (uid,)).fetchone()
            self._records[uid] = (row, plugin_io.canonical_doc(self.plugin, row)) if row else (None, None)
        return self._records[uid]

    def record(self, uid: str):
        return self._row_and_doc(uid)[1]

    def articles(self, uid: str) -> tuple[dict[str, list[int]], dict[int, str]]:
        """`({article token: [chunk seq]}, {chunk seq: label})` of one version,
        cut as the stages cut it (app.layers, never a chunker directly)."""
        if uid not in self._articles:
            from app import layers
            by_token: dict[str, list[int]] = {}
            by_seq: dict[int, str] = {}
            row, doc = self._row_and_doc(uid)
            if doc is not None:
                for ch in layers.chunks_for_document(self.collection, row, doc.sections, title=doc.title):
                    seq, label = int(ch["seq"]), str(ch.get("chapter") or "")
                    by_seq[seq] = label
                    tok = article_token(label)
                    if tok:
                        by_token.setdefault(tok, []).append(seq)
            self._articles[uid] = (by_token, by_seq)
        return self._articles[uid]

    def text_of(self, uid: str, token: str) -> str:
        doc = self.record(uid)
        if doc is None:
            return ""
        return "\n".join(body for label, body in (doc.sections or []) if article_token(label) == token)


# ---- gold, by key ------------------------------------------------------------------

@dataclass
class Gold:
    node_id: int
    file_name: str
    path: str
    act: tuple[str, str, int] | None
    token: str | None
    work: str | None = None
    uid: str | None = None
    seqs: list[int] = field(default_factory=list)

    @property
    def held(self) -> bool:
        return bool(self.uid and self.seqs)

    @property
    def why(self) -> str:
        if self.act is None:
            return "not an Italian act"
        if self.token is None:
            return "no article in the node path"
        if self.work is None:
            return "act not held"
        if not self.seqs:
            return "article not among the shown version's chunks"
        return "held"


def gold_dict(g: Gold) -> dict[str, Any]:
    """The gold as a run file carries it: the fields, and the two verdicts."""
    return {**asdict(g), "held": g.held, "why": g.why}


def gold_from(d: dict[str, Any]) -> Gold:
    fields_ = {k: v for k, v in d.items() if k not in ("held", "why")}
    if fields_.get("act"):
        fields_["act"] = tuple(fields_["act"])
    return Gold(**fields_)


def resolve_gold(held: Held, corpus_rows: Iterable[dict[str, Any]], wanted: set[int]) -> dict[int, Gold]:
    out: dict[int, Gold] = {}
    for r in corpus_rows:
        nid = int(r["id"])
        if nid not in wanted:
            continue
        path = str(r.get("path") or "")
        last = [p for p in path.split("__") if p.startswith("article")]
        g = Gold(node_id=nid, file_name=str(r.get("file_name") or ""), path=path,
                 act=act_of(str(r.get("file_name") or "")), token=article_token(last[-1]) if last else None)
        if g.act and g.token:
            g.work = held.work_for(g.act)
            if g.work:
                shown = held.shown(g.work)
                if shown:
                    g.uid = shown[0]
                    g.seqs = list(held.articles(g.uid)[0].get(g.token, []))
        out[nid] = g
    return out


def coverage(held: Held, corpus_rows: Iterable[dict[str, Any]],
             questions: list[dict[str, Any]]) -> dict[str, Any]:
    wanted = {int(i) for q in questions for i in q["relevant_doc_ids"]}
    gold = resolve_gold(held, corpus_rows, wanted)
    covered, excluded = [], []
    for i, q in enumerate(questions):
        ids = [int(x) for x in q["relevant_doc_ids"]]
        held_ids = [x for x in ids if x in gold and gold[x].held]
        if held_ids:
            covered.append({"index": i, "gold": ids, "held": held_ids})
        else:
            excluded.append({"index": i, "question": q["question"],
                             "why": sorted({gold[x].why if x in gold else "node not in corpus" for x in ids})})
    why: dict[str, int] = {}
    for g in gold.values():
        why[g.why] = why.get(g.why, 0) + 1
    return {"questions": len(questions), "covered": len(covered), "excluded": len(excluded),
            "articles": len(gold), "articles_held": sum(1 for g in gold.values() if g.held),
            "articles_by_outcome": why, "covered_questions": covered, "excluded_questions": excluded,
            "gold": {str(k): gold_dict(v) for k, v in gold.items()}}


# ---- the run -----------------------------------------------------------------------

def _hits(answer: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for lst in answer.get("lists") or []:
        hits.extend(lst.get("hits") or [])
    hits.sort(key=lambda h: (h.get("rank") is None, h.get("rank", 0)))
    return hits


def _seq_of(chunk_id: str | None) -> int | None:
    try:
        return int(str(chunk_id).rsplit(":", 2)[1])
    except (IndexError, ValueError):
        return None


def default_search(question: str, *, collections: list[str], limit: int, as_of: str | None):
    from app.ask import results
    return results.search(question, collections=collections, limit=limit, as_of=as_of)


def text_search(question: str, *, collections: list[str], limit: int, as_of: str | None):
    """The search as the reader's, with its legs reduced to the TEXT one:
    the pool's source, so no encoder decides what either collection is
    measured on."""
    from app import query_service
    from app.ask import results

    def fts(names, query, k, mode, translate, **kw):
        return query_service.run_search(names, query, k, "fts", translate, **kw)
    return results.search(question, collections=collections, limit=limit, as_of=as_of, run=fts)


@contextlib.contextmanager
def pool_prefilter(native_ids: set[int] | None):
    """Every leg restricted to `native_ids` before it ranks: the facet
    filters' own prefilter (`query_service.book_ids_for` → `book_ids`, 05
    §5.2), so a pooled run takes the code path a filter takes. A reader's
    filter, where one is given, still applies (the intersection). In this
    process only; None restricts nothing."""
    from app import query_service
    if native_ids is None:
        yield
        return
    real = query_service.book_ids_for
    ids = set(native_ids)

    def scoped(filters):
        inner = real(filters)
        if inner is None:
            return lambda collection: set(ids)
        return lambda collection: (inner(collection) or set()) & ids
    query_service.book_ids_for = scoped
    try:
        yield
    finally:
        query_service.book_ids_for = real


def current_versions(con, native_ids: Iterable[int], as_of: str | None = None, *,
                     window: int = 500) -> tuple[set[int], dict[str, int]]:
    """The pool's records as the run's search sees them: each record of a
    versioned Work becomes its Work's version in the run's window — the
    shown one without `--as-of` (validity.where: `shown = 1`), the one in
    force on the day with it — and a record of no Work stays itself.

    WHY (München, 2026-11-13, the pilot's 0 of 3): jf100 was built on 11-09
    and held the versions shown then; both codes got new versions since (the
    c.c. 6767400735849696380 → 6770222576450470548), the search keeps
    `shown = 1` only, and the pool prefilter dropped the live version: the
    c.c. ranked #1 without the pool and was absent with it. A pool is a set
    of ACTS; the version is the run's to choose, as the search chooses it.
    Returns `(ids, {"kept", "moved", "gone"})`: `gone` a Work with no version
    in the window (not in force on the day)."""
    ids = sorted({int(i) for i in native_ids})
    work_of: dict[int, str] = {}
    for start in range(0, len(ids), window):
        part = ids[start:start + window]
        marks = ",".join("?" for _ in part)
        for r in con.execute(f"SELECT native_id, work FROM validity WHERE native_id IN ({marks})", part):
            work_of[int(r[0])] = r[1]
    works = sorted(set(work_of.values()))
    now: dict[str, int] = {}
    day = int(str(as_of).replace("-", "")) if as_of else None
    for start in range(0, len(works), window):
        part = works[start:start + window]
        marks = ",".join("?" for _ in part)
        if day is None:
            q, params = f"SELECT work, native_id FROM validity WHERE work IN ({marks}) AND shown=1", part
        else:
            q = (f"SELECT work, native_id FROM validity WHERE work IN ({marks}) AND valid_from<=? "
                 f"AND (valid_to IS NULL OR valid_to>?)")
            params = [*part, day, day]
        for r in con.execute(q, params):
            now[r[0]] = int(r[1])
    out: set[int] = set()
    report = {"kept": 0, "moved": 0, "gone": 0}
    for i in ids:
        w = work_of.get(i)
        if w is None:
            out.add(i)
            report["kept"] += 1
        elif w in now:
            out.add(now[w])
            report["kept" if now[w] == i else "moved"] += 1
        else:
            report["gone"] += 1
    return out, report


def _native_of(uid: str | None) -> int | None:
    tail = str(uid or "").rpartition(":")[2]
    return int(tail) if tail.isdigit() else None


POOL_DEPTH = 100


def pool(held: Held, questions: list[dict[str, Any]], gold: dict[int, Gold], *,
         depth: int = POOL_DEPTH, as_of: str | None = None, search_fn: Callable | None = None) -> dict[str, Any]:
    """The candidate set: each covered question's top `depth` records by the
    text leg, and every gold record. What each question contributed, and
    how many of its gold records the text leg already found, are kept."""
    search_fn = search_fn or text_search
    name = held.collection.name
    ids: set[int] = set()
    per: list[dict[str, Any]] = []
    for i, q in enumerate(questions):
        relevant = {int(x) for x in q["relevant_doc_ids"] if int(x) in gold and gold[int(x)].held}
        if not relevant:
            continue
        found = {_native_of(h.get("uid")) for h in _hits(search_fn(q["question"], collections=[name],
                                                                    limit=depth, as_of=as_of))} - {None}
        golds = {_native_of(gold[x].uid) for x in relevant} - {None}
        ids |= found | golds
        per.append({"index": i, "text_records": len(found), "gold_records": len(golds),
                    "gold_found_by_text": len(golds & found)})
    return {"built_from": name, "depth": depth, "as_of": as_of, "records": len(ids),
            "native_ids": sorted(ids), "questions": per}


def pool_state(collection, native_ids: Iterable[int], *, window: int = 500) -> dict[str, int]:
    """The pool's records in `collection`: embedded, waiting, declined (the
    node's choice, -1), absent from its manifest."""
    from app import db
    ids = sorted(set(native_ids))
    out = {"embedded": 0, "waiting": 0, "declined": 0, "absent": 0}
    con = db.connect(collection)
    try:
        for start in range(0, len(ids), window):
            part = [f"{collection.plugin}:{i}" for i in ids[start:start + window]]
            got = dict(con.execute(f"SELECT uid, embed_done FROM documents WHERE uid IN "
                                   f"({','.join('?' for _ in part)})", part).fetchall())
            for uid in part:
                v = got.get(uid)
                key = ("absent" if v is None else "embedded" if v == 1 else "declined" if v == -1
                       else "waiting")
                out[key] += 1
    finally:
        con.close()
    return out


def enqueue(collection, native_ids: Iterable[int]) -> dict[str, int]:
    """The pool's records not embedded, queued for embedding in `collection`
    — the queue the search itself fills (`retrieval._enqueue_lazy_embed`:
    only a document waiting, never a declined one), one BEGIN IMMEDIATE (a
    writer that reads first). Its worker embeds them under any embedding
    mode; under `lazy`, them and nothing else."""
    from app import db, retrieval
    before = pool_state(collection, native_ids)
    con = db.connect(collection)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            retrieval._enqueue_lazy_embed(con, sorted(set(native_ids)), collection)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return {**before, "queued": before["waiting"]}


def pilot_questions(questions: list[dict[str, Any]], gold: dict[int, Gold], n: int) -> list[int]:
    """measuring.md §12: the indexes of the first `n` covered questions of
    every category (the dataset's `category`, where it has one)."""
    seen: dict[str, int] = {}
    out: list[int] = []
    for i, q in enumerate(questions):
        if not any(int(x) in gold and gold[int(x)].held for x in q["relevant_doc_ids"]):
            continue
        cat = str(q.get("category") or "")
        if seen.get(cat, 0) < n:
            seen[cat] = seen.get(cat, 0) + 1
            out.append(i)
    return out


def pilot_tag(*, reranker: str, k: int, pool_name: str | None, as_of: str | None) -> str:
    """The settings a reading is valid for, the code included (§13: a whole
    run runs its pilot's code)."""
    raw = f"rerank-{reranker}-k{k}-pool-{pool_name or 'none'}-asof-{as_of or 'now'}-code-{chat_eval.ask_code()[7:19]}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)


def pilot_report(held: Held, questions: list[dict[str, Any]], got: dict[str, Any], gold: dict[int, Gold],
                 path: Path, *, settings: dict[str, Any]) -> Path:
    """The pilot, one question at a time, for a reader: the question, its
    gold (act and article), the ranked hits with their labels and whether
    each is gold, the metrics and the time."""
    lines = [f"# Pilot: {held.collection.name}", "", "Settings: " + json.dumps(settings, ensure_ascii=False), "",
             "Read each one; then `legal_pilot pilot-ok --out ... --collection ... --tag ... --by ... --note ...`.",
             ""]
    for row in got["per_question"]:
        if "index" not in row:
            continue
        q = questions[row["index"]]
        lines += [f"## {row['index']}. {q['question']}", ""]
        for nid in row["relevant"]:
            g = gold[nid]
            lines.append(f"- gold {nid}: {g.file_name} {g.path} → {g.uid} art. {g.token}")
        for rank, (h, gid) in enumerate(zip(row.get("hits") or [], row["ranking"]), 1):
            mark = "GOLD" if gid > 0 else "    "
            lines.append(f"  {rank:>2}. {mark} {h['uid']} {h['label']}")
        lines.append(f"- " + ", ".join(f"{m} {row[m]:.3f}" for m in row if "@" in m)
                     + f"; {row.get('seconds')} s")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


COMPARED = ("recall@10", "recall@20", "mrr@10", "ndcg@10")


def compare(a: dict[str, Any], b: dict[str, Any], metrics: Iterable[str] = COMPARED) -> dict[str, Any]:
    """Run `a` against run `b`, question by question (per_question rows
    keyed by the question's index): for each metric, a − b with its interval
    and p. Two runs over other questions, k or pool are not comparable."""
    for key in ("k", "pool"):
        if a.get(key) != b.get(key):
            raise SystemExit(f"the two runs differ in {key}: {a.get(key)!r} and {b.get(key)!r}")
    ra = {r["index"]: r for r in a["per_question"] if "index" in r}
    rb = {r["index"]: r for r in b["per_question"] if "index" in r}
    if ra.keys() != rb.keys():
        raise SystemExit(f"the two runs answered other questions ({len(ra)} and {len(rb)}, "
                         f"{len(ra.keys() ^ rb.keys())} not in both)")
    order = sorted(ra)
    out: dict[str, Any] = {"questions": len(order), "a": a.get("collection"), "b": b.get("collection")}
    for m in metrics:
        if not all(m in ra[i] and m in rb[i] for i in order):
            continue
        delta, low, high, p = bakeoff.paired([float(ra[i][m]) for i in order], [float(rb[i][m]) for i in order])
        out[m] = {"a": a["summary"].get(m), "b": b["summary"].get(m), "a_minus_b": delta,
                  "interval95": [low, high], "p": p}
    out["seconds"] = {"a": a["summary"].get("seconds"), "b": b["summary"].get("seconds")}
    return out


class PoolNotEmbedded(RuntimeError):
    """A pooled run on a collection whose dense leg cannot rank every pool
    record: not the comparison it claims to be."""


def default_traverse(uid: str, *, collection: str):
    from app.ask import traverse
    return traverse.traverse(uid, depth=1, collection=collection)


_DEFAULT = object()


def run(held: Held, questions: list[dict[str, Any]], gold: dict[int, Gold], *, k: int = DEFAULT_K,
        search_fn: Callable | None = None, traverse_fn: Callable | None | object = _DEFAULT,
        as_of: str | None = None, only: Iterable[int] | None = None) -> dict[str, Any]:
    """Every covered question: the ranking as (work, article) pairs, the gold
    node ids among them, the metrics, and the reach of the misses. The two
    callables default to the node's own search and traverse, looked up at
    call time (a test replaces them on the module); `traverse_fn=None` skips
    the reach."""
    search_fn = search_fn or default_search
    traverse_fn = default_traverse if traverse_fn is _DEFAULT else traverse_fn
    name = held.collection.name
    by_pair: dict[tuple[str, str], int] = {(g.work, g.token): g.node_id for g in gold.values() if g.held}
    synthetic = -1
    per_question: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    reach = {"misses": 0, "reachable": 0}
    edges_cache: dict[str, set[str]] = {}

    def edge_targets(uid: str) -> set[str]:
        if traverse_fn is None:
            return set()
        if uid not in edges_cache:
            try:
                got = traverse_fn(uid, collection=name)
                edges_cache[uid] = {str(e.get("to")) for e in got.get("edges") or []}
            except Exception as e:   # noqa: BLE001 — the reach is a measurement, never the run's failure
                edges_cache[uid] = set()
                per_question.append({"traverse_error": f"{uid}: {type(e).__name__}: {e}"})
        return edges_cache[uid]

    n = 0
    seconds: list[float] = []
    wanted = set(only) if only is not None else None
    for i, q in enumerate(questions):
        relevant = {int(x) for x in q["relevant_doc_ids"] if int(x) in gold and gold[int(x)].held}
        if not relevant or (wanted is not None and i not in wanted):
            continue
        n += 1
        t0 = time.perf_counter()
        answer = search_fn(q["question"], collections=[name], limit=k, as_of=as_of)
        seconds.append(time.perf_counter() - t0)
        # measuring.md §13: a search that withheld a leg or its reranker is
        # not the search a reader of a settled node gets — never recorded
        legs = chat_eval.withheld_legs(answer)
        if legs:
            raise chat_eval.SearchWithheld(f"question {i}: the search withheld {'; '.join(legs)}: "
                                           f"nothing is written; run again once the leg is back")
        ranking: list[int] = []
        seen_pairs: list[tuple[str, str]] = []
        shown: list[dict[str, str]] = []
        for h in _hits(answer)[:k]:
            uid, seq = str(h.get("uid")), _seq_of(h.get("chunk_id"))
            work = held.work_of(uid)
            token = held.articles(uid)[1].get(seq) if (work and seq is not None) else None
            token = article_token(token) if token else None
            pair = (work or uid, token or f"chunk{seq}")
            if pair in seen_pairs:
                continue
            seen_pairs.append(pair)
            shown.append({"uid": uid, "label": str(h.get("chapter") or h.get("label") or pair[1])})
            if pair in by_pair:
                ranking.append(by_pair[pair])
            else:
                ranking.append(synthetic)
                synthetic -= 1
        row = bakeoff.metrics_for(ranking, relevant, KS)
        for key, value in row.items():
            totals[key] = totals.get(key, 0.0) + value
        missed = sorted(relevant - set(ranking))
        reachable: list[int] = []
        if missed and traverse_fn is not None:
            hit_uids = [str(h.get("uid")) for h in _hits(answer)[:k]]
            hit_works = {held.work_of(u) for u in hit_uids} - {None}
            hit_keys = set().union(*(held.keys_of(w) for w in hit_works)) if hit_works else set()
            for nid in missed:
                g = gold[nid]
                gold_keys = held.keys_of(g.work) if g.work else set()
                forward = any(edge_targets(u) & gold_keys for u in hit_uids)
                backward = bool(g.uid) and bool(edge_targets(g.uid) & hit_keys)
                if forward or backward:
                    reachable.append(nid)
        reach["misses"] += len(missed)
        reach["reachable"] += len(reachable)
        per_question.append({"index": i, "question": q["question"], "relevant": sorted(relevant),
                             "ranking": ranking, "missed": missed, "reachable_by_edge": reachable,
                             "hits": shown, "seconds": round(seconds[-1], 3),
                             **row})                  # every metric, so two runs pair on any
    summary = {key: (value / n if n else 0.0) for key, value in totals.items()}
    summary["questions_run"] = n
    summary["reach_misses"] = reach["misses"]
    summary["reach_reachable_by_one_edge"] = reach["reachable"]
    summary["reach_share"] = (reach["reachable"] / reach["misses"]) if reach["misses"] else None
    summary["seconds"] = timing(seconds)
    return {"summary": summary, "per_question": per_question}


def timing(seconds: list[float]) -> dict[str, float | None]:
    """The searches' wall time: mean, p50, p95 (nearest rank), total."""
    if not seconds:
        return {"mean": None, "p50": None, "p95": None, "total": 0.0}
    xs = sorted(seconds)

    def rank(q: float) -> float:
        return xs[min(len(xs) - 1, max(0, int(round(q * len(xs) + 0.5)) - 1))]
    return {"mean": sum(xs) / len(xs), "p50": rank(0.50), "p95": rank(0.95), "total": sum(xs)}


@contextlib.contextmanager
def reranker_override(choice: str | None):
    """`configured` (or None): the node's own resolution, untouched. `off`:
    no reranker for any collection — the fused order (05 §5.6). `from:<name>`:
    every collection reranked by THAT collection's reranker, as the node
    resolves it (its file's `[reranker]`, else RERANK_URL) — the comparison's
    lent reranker. In this process only; nothing is written."""
    from app import collections as collections_mod
    if choice in (None, "", "configured"):
        yield None
        return
    real = collections_mod.reranker_for
    if choice == "off":
        lent = None
    elif choice.startswith("from:"):
        name = choice[len("from:"):]
        donor = next((c for c in collections_mod.load_all() if c.name == name), None)
        if donor is None:
            raise SystemExit(f"--reranker {choice}: no collection named {name!r} is held here")
        lent = real(donor)
        if lent is None:
            raise SystemExit(f"--reranker {choice}: {name} has no reranker to lend")
    else:
        raise SystemExit(f"--reranker is configured, off or from:<collection>, not {choice!r}")
    collections_mod.reranker_for = lambda collection: lent
    try:
        yield lent
    finally:
        collections_mod.reranker_for = real


# ---- the temporal arm ----------------------------------------------------------------

def _nugget(before: str, after: str) -> str | None:
    """A number the earlier text carries and the later does not."""
    later = set(_NUMBER.findall(after))
    for n in _NUMBER.findall(before):
        if n not in later and len(n) >= 1:
            return n
    return None


def temporal_make(held: Held, *, limit: int = 200) -> list[dict[str, Any]]:
    """Dated question stubs: for each act with two consecutive text
    versions, the articles whose text differs and whose earlier text carries
    a number the later lost — the date is the earlier version's last day."""
    rows = held.con.execute(
        "SELECT uid, work, valid_from, valid_to FROM validity WHERE has_text=1 "
        "ORDER BY work, valid_from, native_id").fetchall()
    out: list[dict[str, Any]] = []
    by_work: dict[str, list] = {}
    for r in rows:
        by_work.setdefault(r["work"], []).append(r)
    for work, versions in by_work.items():
        for a, b in zip(versions, versions[1:]):
            if a["valid_to"] is None or int(a["valid_to"]) != int(b["valid_from"]):
                continue
            tokens_a = held.articles(a["uid"])[0]
            tokens_b = held.articles(b["uid"])[0]
            for token in sorted(set(tokens_a) & set(tokens_b), key=lambda t: (len(t), t)):
                before, after = held.text_of(a["uid"], token), held.text_of(b["uid"], token)
                if not before or before == after:
                    continue
                nugget = _nugget(before, after)
                if not nugget:
                    continue
                last_day = _day(int(a["valid_to"])) - timedelta(days=1)
                doc = held.record(a["uid"])
                name = (doc.extra or {}).get("denomination") or work
                out.append({"work": work, "denomination": name, "article": token,
                            "as_of": last_day.isoformat(), "gold_uid": a["uid"], "control_uid": b["uid"],
                            "nugget": nugget,
                            "question": f"Che cosa stabiliva l'art. {token} della {name} al {last_day.isoformat()}?",
                            "review": "stub — keep only if this asks a real question; edit the wording"})
                if len(out) >= limit:
                    return out
    return out


def _day(yyyymmdd: int) -> date:
    s = f"{int(yyyymmdd):08d}"
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def temporal_run(held: Held, items: list[dict[str, Any]], *, k: int = DEFAULT_K,
                 search_fn: Callable | None = None) -> dict[str, Any]:
    search_fn = search_fn or default_search
    name = held.collection.name
    per: list[dict[str, Any]] = []
    counts = {"n": 0, "version_right": 0, "nugget_found": 0, "control_current": 0, "control_nugget_gone": 0}
    for it in items:
        counts["n"] += 1
        token = article_token(f"art {it['article']}") or str(it["article"])

        def first_match(answer):
            for h in _hits(answer)[:k]:
                uid, seq = str(h.get("uid")), _seq_of(h.get("chunk_id"))
                if held.work_of(uid) == it["work"] and seq is not None:
                    label = held.articles(uid)[1].get(seq, "")
                    if article_token(label) == token:
                        return uid, str(h.get("text") or "")
            return None, ""
        dated_uid, dated_text = first_match(search_fn(it["question"], collections=[name], limit=k,
                                                      as_of=it["as_of"]))
        control_uid, control_text = first_match(search_fn(it["question"], collections=[name], limit=k,
                                                          as_of=None))
        version_right = dated_uid == it["gold_uid"]
        nugget_found = bool(dated_text) and str(it["nugget"]) in dated_text
        control_current = control_uid == it["control_uid"]
        control_gone = bool(control_text) and str(it["nugget"]) not in control_text
        counts["version_right"] += version_right
        counts["nugget_found"] += nugget_found
        counts["control_current"] += control_current
        counts["control_nugget_gone"] += control_gone
        per.append({**it, "dated_uid": dated_uid, "version_right": version_right, "nugget_found": nugget_found,
                    "control_uid_returned": control_uid, "control_current": control_current,
                    "control_nugget_gone": control_gone})
    return {"summary": counts, "per_question": per}


# ---- the command --------------------------------------------------------------------

def _open(name: str, root: Path | None):
    from app import collections as collections_mod, db
    for c in collections_mod.load_all():
        if c.name == name:
            return c, db.connect(c)
    raise SystemExit(f"no collection named {name!r} is held here")


def _load_dataset(out: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import pyarrow.parquet as pq
    corpus = pq.read_table(out / "corpus.parquet", columns=["id", "file_name", "path"]).to_pylist()
    questions = pq.read_table(out / "questions-validation.parquet").to_pylist()
    return corpus, questions


#: the vector leg's settle (measuring.md §13), chat_eval's; a test stands in for it
settle_fn = chat_eval.settle


def _pool_ids(out: Path, name: str) -> list[int]:
    path = out / "runs" / f"pool-{name}.json"
    if not path.is_file():
        raise SystemExit(f"no pool {name!r} at {path}: `legal_pilot pool --name {name}` first")
    return [int(i) for i in json.loads(path.read_text(encoding="utf-8"))["native_ids"]]


def _stamp(held: Held) -> dict[str, Any]:
    codex = getattr(held.collection, "codex", None)
    return {"collection": held.collection.name,
            "collection_id": getattr(codex, "collection_id", None),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _write(out: Path, name: str, payload: dict[str, Any]) -> Path:
    runs = out / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{name}.json"
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-person", action="store_true", dest="as_person",
                    help="hold the node's claim as a person's question does (corpus work "
                         "stands aside): for timings of what a person waits; by default a "
                         "harness holds none (app/rage/dispatch.py `as_harness`)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch"); p.add_argument("--out", type=Path, required=True)
    for cmd in ("coverage", "run", "pool", "enqueue"):
        p = sub.add_parser(cmd)
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--collection", required=True)
        if cmd == "run":
            p.add_argument("--k", type=int, default=DEFAULT_K)
            p.add_argument("--as-of", default=None, dest="as_of")
            p.add_argument("--no-reach", action="store_true", dest="no_reach")
            p.add_argument("--reranker", default="configured",
                           help="configured (default), off, or from:<collection> (lent)")
            p.add_argument("--pool", default=None, help="a pool's name (runs/pool-<name>.json): every leg "
                                                         "restricted to it")
            p.add_argument("--pilot", type=int, default=None,
                           help="the first N covered questions of every category, for a reader (§12)")
        if cmd == "pool":
            p.add_argument("--name", required=True)
            p.add_argument("--depth", type=int, default=POOL_DEPTH)
            p.add_argument("--as-of", default=None, dest="as_of")
        if cmd == "enqueue":
            p.add_argument("--pool", required=True)
    p = sub.add_parser("compare")
    p.add_argument("run_a", type=Path); p.add_argument("run_b", type=Path)
    p = sub.add_parser("pilot-ok")
    p.add_argument("--out", type=Path, required=True); p.add_argument("--collection", required=True)
    p.add_argument("--tag", required=True); p.add_argument("--by", required=True)
    p.add_argument("--note", required=True)
    p = sub.add_parser("temporal-make")
    p.add_argument("--collection", required=True); p.add_argument("--out", type=Path, required=True)
    p.add_argument("--limit", type=int, default=200)
    p = sub.add_parser("temporal-run")
    p.add_argument("--collection", required=True); p.add_argument("--questions", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True); p.add_argument("--k", type=int, default=DEFAULT_K)
    a = ap.parse_args(argv)
    from app.rage import dispatch as _dispatch
    _dispatch.as_harness(not getattr(a, "as_person", False))   # a harness, not a person

    if a.cmd == "fetch":
        return bakeoff.fetch(a.out)
    if a.cmd == "compare":
        got = compare(json.loads(a.run_a.read_text(encoding="utf-8")), json.loads(a.run_b.read_text(encoding="utf-8")))
        print(f"{got['questions']} questions, a = {a.run_a.name}, b = {a.run_b.name}")
        for m in COMPARED:
            if m in got:
                r = got[m]
                print(f"  {m}: a {r['a']:.4f}  b {r['b']:.4f}  a-b {r['a_minus_b']:+.4f}  "
                      f"95% [{r['interval95'][0]:+.4f}, {r['interval95'][1]:+.4f}]  p {r['p']:.4f}")
        print(f"  seconds a question: a {got['seconds']['a']}  b {got['seconds']['b']}")
        return 0
    if a.cmd == "pilot-ok":
        (a.out / "runs" / a.collection).mkdir(parents=True, exist_ok=True)
        return chat_eval.pilot_ok(a.out, a.collection, a.tag, a.by, a.note)
    c, con = _open(a.collection, None)
    held = Held(c, con)
    if a.cmd in ("coverage", "run", "pool", "enqueue"):
        corpus, questions = _load_dataset(a.out)
        cov = coverage(held, corpus, questions)
        gold = {int(k): gold_from(v) for k, v in cov["gold"].items()}
        if a.cmd == "coverage":
            path = _write(a.out, "coverage", {**_stamp(held), **cov})
            print(f"questions {cov['questions']}  covered {cov['covered']}  excluded {cov['excluded']}  "
                  f"articles {cov['articles']} held {cov['articles_held']}  {cov['articles_by_outcome']}")
            print(f"written {path}")
            return 0
        if a.cmd == "pool":
            got = pool(held, questions, gold, depth=a.depth, as_of=a.as_of)
            path = a.out / "runs" / f"pool-{a.name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({**_stamp(held), "name": a.name, **got}, ensure_ascii=False) + "\n",
                            encoding="utf-8")
            found = sum(q["gold_found_by_text"] for q in got["questions"])
            golds = sum(q["gold_records"] for q in got["questions"])
            print(f"pool {a.name}: {got['records']} records from {len(got['questions'])} questions, depth "
                  f"{a.depth} by the text leg of {c.name}; gold records {golds}, {found} of them found by text")
            print(f"written {path}")
            return 0
        if a.cmd == "enqueue":
            ids = sorted(current_versions(held.con, _pool_ids(a.out, a.pool), getattr(a, "as_of", None))[0])
            got = enqueue(c, ids)
            print(f"{c.name}: pool {a.pool}, {len(ids)} records — embedded {got['embedded']}, queued "
                  f"{got['queued']}, declined {got['declined']}, absent {got['absent']}. Its worker embeds "
                  f"the queued ones; with embed_mode = \"lazy\" in its file, them and nothing else")
            return 0
        ids = _pool_ids(a.out, a.pool) if a.pool else None
        if ids is not None:
            current, moved = current_versions(held.con, ids, a.as_of)
            print(f"{c.name}: pool {a.pool}: {len(ids)} records → {len(current)} as the search sees them "
                  f"({moved}: a Work's version is the run's window's, never the pool's day)")
            ids = sorted(current)
            state = pool_state(c, ids)
            if state["embedded"] != len(ids):
                print(f"{c.name}: pool {a.pool} has {len(ids)} records and {state['embedded']} embedded here "
                      f"({state}): a pooled run ranks every pool record by every leg, so it waits "
                      f"(`enqueue`, then the worker)", file=sys.stderr)
                return 3
        # measuring.md §13: the vector leg settled in this process first
        settled = settle_fn([c.name])
        if not settled.get(c.name):
            print(f"{c.name}: the vector leg is not settled here ({settled}); nothing is run", file=sys.stderr)
            return 3
        tag = pilot_tag(reranker=a.reranker, k=a.k, pool_name=a.pool, as_of=a.as_of)
        only = pilot_questions(questions, gold, a.pilot) if a.pilot else None
        if only is None and not chat_eval._pilot_ok_path(a.out, c.name, tag).is_file():
            print(f"{c.name}: no pilot read for these settings (tag {tag}): `run --pilot 3` with the same "
                  f"flags, read it, then `pilot-ok --tag {tag}` (measuring.md §12)", file=sys.stderr)
            return 2
        try:
            with reranker_override(a.reranker) as lent, pool_prefilter(set(ids) if ids is not None else None):
                got = run(held, questions, gold, k=a.k, as_of=a.as_of, only=only,
                          traverse_fn=None if a.no_reach else _DEFAULT)
        except chat_eval.SearchWithheld as e:
            print(f"{c.name}: {e}", file=sys.stderr)
            return 3
        rr = {"choice": a.reranker, "url": getattr(lent, "url", None), "model": getattr(lent, "model", None)}
        settings = {"k": a.k, "as_of": a.as_of, "reranker": rr, "pool": a.pool, "tag": tag,
                    "code": chat_eval.ask_code()}
        if only is not None:
            path = pilot_report(held, questions, got, gold, a.out / "runs" / c.name / f"pilot-{tag}.md",
                                settings=settings)
            print(f"pilot: {got['summary']['questions_run']} questions written to {path}; read them, then "
                  f"`legal_pilot pilot-ok --out {a.out} --collection {c.name} --tag {tag} --by WHO --note WHAT`")
            return 0
        label = "run" + ("" if a.reranker == "configured" else f"-rerank-{a.reranker.replace(':', '-')}") \
            + (f"-pool-{a.pool}" if a.pool else "")
        path = _write(a.out, label,
                      {**_stamp(held), **settings, "pool_records": len(ids) if ids is not None else None,
                       "coverage": {k: v for k, v in cov.items() if k != "gold"}, **got})
        s = got["summary"]
        print("  ".join(f"{m} {s.get(m, 0.0):.4f}" for m in ("recall@10", "mrr@10", "ndcg@10", "recall@20")))
        t = s["seconds"]
        print(f"reranker {a.reranker}  seconds a question: mean {t['mean']}  p50 {t['p50']}  p95 {t['p95']}  "
              f"total {t['total']:.1f}")
        print(f"questions {s['questions_run']}  misses {s['reach_misses']}  reachable by one edge "
              f"{s['reach_reachable_by_one_edge']}  share {s['reach_share']}")
        print(f"written {path}")
        return 0
    if a.cmd == "temporal-make":
        items = temporal_make(held, limit=a.limit)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items), encoding="utf-8")
        print(f"{len(items)} stubs written to {a.out} — review them before they count")
        return 0
    items = [json.loads(line) for line in a.questions.read_text(encoding="utf-8").splitlines() if line.strip()]
    got = temporal_run(held, items, k=a.k)
    path = _write(a.out, "temporal", {**_stamp(held), "k": a.k, **got})
    print(json.dumps(got["summary"]))
    print(f"written {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
