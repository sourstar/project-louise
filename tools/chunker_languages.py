"""THE PER-LANGUAGE CHUNKER TEST (operator, 2026-11-13: "Build the per-language
test on LEMUR"; docs/notes/legal-chunking-retrieval.md §5.2).

THE QUESTION. The operator: a chunker serves the most meaningful portioning
of a corpus, and that is fixed by structure and content, not by the language
the content is written in — so the chunker that scores best with one
embedder should score best in every language. Three things could still make
the best setting depend on the language: a window counted in TOKENS (the
same article takes more tokens in one language than another, so a cap splits
it in one and not the other), the embedder's own strength per language, and
the text leg's analyzers. This test asks the question where only the
language changes.

LEMUR (Baba Ahmadi et al., EACL 2026 SRW; Apache-2.0, green): EU
environmental acts in 23 languages; the act's metadata header is the query,
its body the gold (`encoder_bakeoff fetch --suite lemur-<code>`, one
directory a language). The QUESTIONS ARE THE PARALLEL ACTS — those every
language asked holds, with a query — so between two languages the acts,
their structure and what is asked are the same, and the act keeps one index
in every language.

1. SECTIONS. Each body is read into sections by the eurlex plugin's own
   reader of a rendition without Formex (`rendition._sections`, the lines
   normalised as `parse_pdf` normalises them): the preamble, each article
   (its heading in any of the 24 languages, the number first in hu, lt, lv
   and fi included), each annex after the articles — the sections the EU
   collection holds for an act it read from XHTML, HTML or PDF. Per
   language, the articles found against the most any language found for
   the same act: a reader that misses headings in one language degrades the
   structure arms THERE, for a reason that is not the chunker's, and the
   report says so.
2. ARMS. `structure` — `legal_article`, header and outline off, the
   collection's tokenizer and window: the baseline; `structure-<N>`, the
   same at `max_tokens` N; `window-<N>`, the `fixed` chunker over the body
   whole (N tokens estimated as characters / 4, so the same characters in
   every language): no structure; or `NAME=<layer JSON>`, with `"whole":
   true` to cut the body whole. NO ARM WRITES THE ACT'S TITLE: LEMUR's
   query IS the act's header, so an arm writing it into its chunks, or a
   reranker head carrying it, would read the answer — `header` and
   `outline` are refused, and the reranker judges the chunks bare.
3. Everything else is the chunker mode's (tools/chunker_mode.py, after
   München's review of #621): one encoder run per side, pinned and validated
   for the space; every arm embedded fresh, cached by text under the recipe,
   the space and the runs; app/fts.py's index per language and arm; the
   exact dense scan; `retrieval._merge_rrf`; the collection's reranker. The
   encoder, its instructions and the reranker are ONE held collection's
   (`--collection`): one embedder for every language.
4. SCORING. The act is the unit: MRR@k of the act among the parallel acts.
5. THE REPORT. Per language, every arm against the first
   (`chunker_mode.report`). Then ACROSS languages, per reading and arm:
   - each language's Δ (arm − baseline) with its interval
     (`encoder_bakeoff.paired`);
   - whether the Δ DIFFERS BY LANGUAGE: a randomization test with the act
     as the block — each act's Δs exchanged among its languages, the
     statistic the spread of the languages' mean Δ (Fisher's randomization
     test, the paired test's family; the act is a block because it is the
     same act in every language) — Holm across the arms;
   - the best arm in each language, and how many languages agree.
   The decision rests on one measure stated before the run: reranked
   MRR@10 (`--primary`). The hypothesis stands where no arm's effect
   differs by language and every language has the same best arm.

LEMUR's caution stays (legal-embedders-fr-de-eu.md): a title-shaped task —
finding an act from its description, not answering a legal question. It is
the one benchmark whose questions and documents are the same in every
language, which is what this question needs. EUR-Lex-Sum (the EU's
summaries as queries, 24 languages) asks it with content-shaped queries
once its licence is acknowledged (invariant 9).

STANDARDS NOT ADOPTED, written down (CLAUDE.md): a mixed model with a
language × arm interaction (statsmodels) would say the same as the blocked
randomization test with a distributional assumption MRR's discrete scores
do not meet; the Friedman test (scipy) on Δ ranks breaks on MRR's heavy
ties (most acts score 1.0 in every arm).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import chunker_mode as cm  # noqa: E402
from tools import encoder_bakeoff as bakeoff  # noqa: E402

SET = "lemur"
READINGS = cm.READINGS
DEFAULT_ARMS = ("structure", "structure-512", "window-512")
PRIMARY = ("reranked", "mrr@10")
KS = (1, 3, 5, 10, 20)
DEFAULT_K = 20
#: the chunk ids' record prefix: `lemur:<act index>` in every language
UID = "lemur"


@dataclass(frozen=True)
class Act:
    n: int                              # the act's index among the parallel acts: one in every language
    law: str                            # LEMUR's law_id (a CELEX number)
    query: str                          # the act's metadata header
    body: str
    sections: list                      # [(label, text)] as the eurlex reader reads the body
    articles: int                       # the article headings it found


@dataclass(frozen=True)
class Arm:
    """`cm.Arm` with how the body reaches the chunker: its sections, or whole."""
    name: str
    layer: Any
    whole: bool = False

    def manifest(self) -> dict:
        return {**self.layer.as_manifest(), "sections": "whole" if self.whole else "articles"}


# ---- the set -----------------------------------------------------------------------

def read_set(path: Path) -> tuple[str, dict[str, tuple[str, str]]]:
    """One fetched `lemur-<code>` directory: its language and `{law: (query,
    body)}` for every act with a query and a body."""
    source = json.loads((Path(path) / "SOURCE.json").read_text(encoding="utf-8"))
    suite = str(source.get("suite") or "")
    if not suite.startswith(f"{SET}-"):
        raise SystemExit(f"{path} holds {suite!r}, not a LEMUR language (`encoder_bakeoff fetch --suite "
                         f"lemur-<code>`)")
    if not bakeoff.SUITES.get(suite, {}).get("green"):
        raise SystemExit(f"{suite}: not a green set; its licence needs an acknowledgement (invariant 9)")
    docs = {r["_id"]: r.get("text") or "" for r in bakeoff._rows(Path(path) / "corpus.jsonl")}
    queries = {r["_id"].removeprefix("q:"): r.get("text") or "" for r in bakeoff._rows(Path(path) / "queries.jsonl")}
    return suite.split("-", 1)[1], {law: (q, docs[law]) for law, q in queries.items()
                                    if q.strip() and docs.get(law, "").strip()}


def sections_of(body: str) -> tuple[list[tuple[str, str]], int]:
    """The body's sections as the eurlex plugin reads a rendition without
    Formex, and how many article headings it found."""
    from app.plugins.eurlex import formex, rendition
    lines = [("", formex.norm(t)) for t in body.splitlines() if formex.norm(t)]
    sections, articles, _annexes = rendition._sections(lines)
    return sections, len(articles)


def parallel(sets: dict[str, dict[str, tuple[str, str]]]) -> tuple[dict[str, list[Act]], dict[str, Any]]:
    """The acts every language holds, each with one index in every language;
    and what was left out, per language."""
    common = sorted(set.intersection(*(set(v) for v in sets.values()))) if sets else []
    out: dict[str, list[Act]] = {}
    for lang, acts in sets.items():
        rows = []
        for n, law in enumerate(common):
            query, body = acts[law]
            secs, arts = sections_of(body)
            rows.append(Act(n, law, query, body, secs, arts))
        out[lang] = rows
    left = {lang: len(v) - len(common) for lang, v in sets.items()}
    return out, {"parallel": len(common), "left_out": left}


def structure_found(acts: dict[str, list[Act]]) -> dict[str, dict[str, Any]]:
    """Per language: the article headings found, against the most any
    language found for the same act — a parallel act has the same articles
    in every language, so a shortfall is the reader's, not the act's."""
    if not acts:
        return {}
    n = len(next(iter(acts.values())))
    best = [max(acts[lang][i].articles for lang in acts) for i in range(n)]
    out = {}
    for lang, rows in acts.items():
        found = sum(a.articles for a in rows)
        short = sum(1 for a, b in zip(rows, best) if a.articles < b)
        out[lang] = {"articles": found, "most_found": sum(best), "share": round(found / sum(best), 4) if sum(best) else None,
                     "acts_short": short, "acts_without_articles": sum(1 for a in rows if a.articles == 0)}
    return out


# ---- the arms ------------------------------------------------------------------------

def base_params(collection, *, tokenizer: str | None = None, max_tokens: int | None = None) -> dict[str, Any]:
    """The structure arm's tokenizer and window: the collection's own
    `legal_article` layer's, or given — the encoder's tokenizer, so the
    window is counted as the encoder reads it."""
    from app import chunking
    params: dict[str, Any] = {}
    try:
        layer = cm.shipped_layer(collection)
        if layer.chunker == chunking.LEGAL_ARTICLE:
            params = {k: layer.params[k] for k in ("tokenizer", "max_tokens") if k in layer.params}
    except SystemExit:
        pass
    if tokenizer:
        params["tokenizer"] = tokenizer
    if max_tokens:
        params["max_tokens"] = int(max_tokens)
    if "tokenizer" not in params or "max_tokens" not in params:
        raise SystemExit(f"{collection.name} cuts with no legal_article layer: give the encoder's tokenizer "
                         f"(--tokenizer sha256:<tokenizer.json>) and its window (--max-tokens)")
    return params


def arm_of(spec: str, base: dict[str, Any]) -> Arm:
    from app import chunking, chunking_legal, layers
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
        if params.get("header") or params.get("outline"):
            raise SystemExit(f"arm {name}: no arm writes the act's title here — LEMUR's query IS the act's "
                             f"header, so a header or an outline would read the answer")
        try:
            layers.check_params(params, chunker=chunker, where=f"arm {name}")
        except layers.LayerError as e:
            raise SystemExit(str(e)) from None
        version = int(d.get("version") or chunking.chunk_version_for(chunker))
        return Arm(name, layers.Layer(chunker, version, "default", params=params), whole=bool(d.get("whole")))
    legal = {**base, "header": False, "outline": False}
    if name == "structure":
        return Arm(name, layers.Layer(chunking.LEGAL_ARTICLE, chunking_legal.CHUNK_VERSION, "default", params=legal))
    m = re.fullmatch(r"(structure|window)-(\d+)", name)
    if m and m.group(1) == "structure":
        params = {**legal, "max_tokens": int(m.group(2))}
        try:
            layers.check_params(params, chunker=chunking.LEGAL_ARTICLE, where=f"arm {name}")
        except layers.LayerError as e:
            raise SystemExit(str(e)) from None
        return Arm(name, layers.Layer(chunking.LEGAL_ARTICLE, chunking_legal.CHUNK_VERSION, "default", params=params))
    if m:
        return Arm(name, layers.Layer(chunking.FIXED, chunking.chunk_version_for(chunking.FIXED), "default",
                                      params={"tokens": int(m.group(2)), "overlap": 0}), whole=True)
    raise SystemExit(f"unknown arm {name!r}: structure, structure-<N>, window-<N>, or NAME=<layer JSON>")


def arms_of(specs: Iterable[str], base: dict[str, Any]) -> list[Arm]:
    arms = [arm_of(s, base) for s in specs]
    seen: dict[str, str] = {}
    for a in arms:
        if a.name in {b.name for b in arms if b is not a}:
            raise SystemExit(f"two arms are named {a.name!r}")
        key = json.dumps(a.manifest(), sort_keys=True)
        if key in seen:
            raise SystemExit(f"arms {seen[key]!r} and {a.name!r} cut the same: one of them measures nothing")
        seen[key] = a.name
    return arms


def cut(arm: Arm, acts: list[Act], lang: str, *, count: Callable[[str], int] | None = None,
        ctx: int | None = None) -> tuple[list[dict], dict[str, Any]]:
    """The arm's chunks of every act, in act order, each with its language;
    and what was cut, counted as the encoder reads it (`cm.window_cost`)."""
    from app import layers
    fn = layers._chunker_of(arm.layer)
    chunks: list[dict] = []
    sections = 0
    for act in acts:
        secs = [("", act.body)] if arm.whole else act.sections
        sections += sum(1 for _, t in secs if (t or "").strip())
        for c in fn(f"{UID}:{act.n}", act.n, secs, title=None):
            c["lang"] = lang
            chunks.append(c)
    cost: dict[str, Any] = {"records": len({c["book_id"] for c in chunks}), "sections": sections,
                            "chunks": len(chunks), "characters": sum(len(c["text"]) for c in chunks),
                            "chunks_per_section": round(len(chunks) / sections, 4) if sections else None}
    cost.update(cm.window_cost(f"{arm.name} ({lang})", chunks, count, ctx))
    return chunks, cost


def cut_all(acts: dict[str, list[Act]], arms: list[Arm], *, count: Callable[[str], int] | None,
            ctx: int | None, workers: int | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """Every (language, arm) cut and counted, on the cores the node's profile
    allows (CLAUDE.md: a pass over a corpus is never one process's) — threads,
    since the tokenizer counts outside the GIL. An arm past the window in any
    language raises before anything is embedded."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    from app import node_profile
    pairs = [(lang, arm) for lang in acts for arm in arms]
    allowed = node_profile.cpu_cores()
    n = max(1, min(len(pairs), workers or allowed or os.cpu_count() or 1))
    with ThreadPoolExecutor(max_workers=n, thread_name_prefix="lemur-cut") as ex:
        got = list(ex.map(lambda p: cut(p[1], acts[p[0]], p[0], count=count, ctx=ctx)[1], pairs))
    return {(lang, arm.name): cost for (lang, arm), cost in zip(pairs, got)}


# ---- the run ---------------------------------------------------------------------------

def score(hits: list[dict], gold: int, k: int) -> tuple[dict[str, float], list[int]]:
    """The act is the unit: the first `k` hits' acts, each once, against the
    one gold act."""
    from app import retrieval
    ranking: list[int] = []
    for h in hits[:k]:
        tail = str(retrieval.record_of(h)).rpartition(":")[2]
        if tail.isdigit() and int(tail) not in ranking:
            ranking.append(int(tail))
    return bakeoff.metrics_for(ranking, {gold}, KS), ranking


def pilot_of(acts: dict[str, list[Act]], n: int) -> set[int]:
    """measuring.md §12: the first `n` acts — the same in every language,
    the category of this set."""
    return set(range(min(n, len(next(iter(acts.values()), [])))))


def run(acts: dict[str, list[Act]], arms: list[Arm], *, k: int, workdir: Path, recipe, cache: cm.VectorCache,
        collection, embed_fn: Callable | None = None, rerank_fn: Callable | None = None,
        count: Callable[[str], int] | None = None, only: Iterable[int] | None = None,
        log: Callable[[str], None] = print) -> dict[str, Any]:
    """Every language, every arm, every covered act: `{lang: {"questions",
    "query_embedding", "arms": {arm: {"cost", "embedding", reading: {...}}}}}`.
    Every (language, arm) is cut and counted before anything is embedded; it
    is cut again when its turn comes (memory is one arm's)."""
    from app import retrieval
    depth = max(k, retrieval.RERANK_TOP_N, 20)
    ctx = getattr(recipe, "ctx", None)
    if count is None or not ctx:
        log("the window is NOT counted (no tokenizer): an arm the encoder truncates is not refused here")
    costs = cut_all(acts, arms, count=count, ctx=ctx)
    for (lang, name), cost in costs.items():
        log(f"{lang} {name}: {cost}")
    wanted = set(only) if only is not None else None
    out: dict[str, Any] = {}
    for lang, rows in acts.items():
        asked = [a for a in rows if wanted is None or a.n in wanted]
        qvecs, qstats = cm.embed([a.query for a in asked], cache=cache, recipe=recipe, query=True,
                                 embed_fn=embed_fn, log=log)
        lang_out: dict[str, Any] = {"questions": len(asked), "query_embedding": qstats, "arms": {}}
        for arm in arms:
            chunks, _ = cut(arm, rows, lang)
            vectors, dstats = cm.embed([c["text"] for c in chunks], cache=cache, recipe=recipe, query=False,
                                       embed_fn=embed_fn, log=log)
            (Path(workdir) / lang).mkdir(parents=True, exist_ok=True)
            search = cm.ArmSearch(arm, collection, chunks, vectors, Path(workdir) / lang)
            per: dict[str, list[dict]] = {r: [] for r in READINGS}
            totals: dict[str, dict[str, float]] = {r: {} for r in READINGS}
            seconds: list[float] = []
            try:
                for act, qvec in zip(asked, qvecs):
                    t0 = time.perf_counter()
                    got = cm.readings(search, act.query, qvec, depth=depth, rerank_fn=rerank_fn)
                    seconds.append(time.perf_counter() - t0)
                    for reading, hits in got.items():
                        row, ranking = score(hits, act.n, k)
                        for key, value in row.items():
                            totals[reading][key] = totals[reading].get(key, 0.0) + value
                        per[reading].append({"index": act.n, "law": act.law, "question": act.query,
                                             "relevant": [act.n], "ranking": ranking,
                                             "hits": [{"uid": f"{UID}:{n}", "label": rows[n].law}
                                                      for n in ranking], **row})
            finally:
                search.close()
            n = len(asked)
            arm_out: dict[str, Any] = {"layer": arm.manifest(), "cost": costs[(lang, arm.name)],
                                       "embedding": {**dstats, "stored": 0}, "seconds": cm._timing(seconds)}
            for reading in READINGS:
                if per[reading]:
                    summary = {key: value / n for key, value in totals[reading].items()} if n else {}
                    summary["questions_run"] = n
                    arm_out[reading] = {"summary": summary, "per_question": per[reading]}
            lang_out["arms"][arm.name] = arm_out
            del vectors, search, chunks
        out[lang] = lang_out
    return out


# ---- across languages ---------------------------------------------------------------

def _values(r: dict[str, Any], metric: str) -> dict[int, float]:
    return {int(x["index"]): float(x[metric]) for x in r["per_question"] if "index" in x and metric in x}


def by_language(d: np.ndarray, *, trials: int = 10_000, seed: int = 0, chunk: int = 500) -> float:
    """Does a Δ differ by language? `d` is acts × languages. The statistic is
    the variance of the languages' mean Δ; under "no language effect" an
    act's Δs are exchangeable among its languages (the act is the block),
    so each permutation shuffles every row. p = (extreme + 1) / (trials + 1)."""
    if d.shape[1] < 2 or d.shape[0] < 2:
        return 1.0
    observed = float(np.var(d.mean(axis=0)))
    rng = np.random.default_rng(seed)
    extreme = 0
    for start in range(0, trials, chunk):
        m = min(chunk, trials - start)
        shuffled = rng.permuted(np.broadcast_to(d, (m, *d.shape)), axis=2)
        extreme += int((shuffled.mean(axis=1).var(axis=1) >= observed - 1e-15).sum())
    return (extreme + 1) / (trials + 1)


def across(runs: list[dict[str, Any]], *, primary: tuple[str, str] = PRIMARY,
           metric: str | None = None, trials: int = 10_000) -> dict[str, Any]:
    """Per reading and arm against the baseline: each language's Δ with its
    interval and p, whether the Δ differs by language (Holm across the arms),
    and the best arm per language. Every language must answer the same acts."""
    metric = metric or primary[1]
    langs = sorted({r["lang"] for r in runs})
    arms = runs[0].get("arms") or sorted({r["arm"] for r in runs})
    base = arms[0]
    by = {(r["lang"], r["legs"], r["arm"]): r for r in runs}
    out: dict[str, Any] = {"langs": langs, "baseline": base, "metric": metric, "primary": list(primary),
                           "readings": {}}
    for reading in READINGS:
        if not all((lang, reading, a) in by for lang in langs for a in arms):
            continue
        values = {(lang, a): _values(by[(lang, reading, a)], metric) for lang in langs for a in arms}
        acts = set(values[(langs[0], base)])
        if any(set(v) != acts for v in values.values()):
            raise SystemExit(f"{reading}: the languages answered other acts — the test needs the parallel acts "
                             f"in every language")
        order = sorted(acts)
        means = {lang: {a: float(np.mean([values[(lang, a)][i] for i in order])) for a in arms} for lang in langs}
        best = {lang: max(arms, key=lambda a: (means[lang][a], -arms.index(a))) for lang in langs}
        rows = []
        for a in arms[1:]:
            d = np.asarray([[values[(lang, a)][i] - values[(lang, base)][i] for lang in langs] for i in order])
            per_lang = {}
            for j, lang in enumerate(langs):
                delta, lo, hi, p = bakeoff.paired([values[(lang, a)][i] for i in order],
                                                  [values[(lang, base)][i] for i in order])
                per_lang[lang] = {"delta": delta, "interval95": [lo, hi], "p": p}
            rows.append({"arm": a, "per_language": per_lang, "p_language": by_language(d, trials=trials),
                         "spread": float(d.mean(axis=0).max() - d.mean(axis=0).min()) if len(langs) else 0.0})
        for row, p in zip(rows, cm.holm([r["p_language"] for r in rows])):
            row["p_language_holm"] = p
        top = max(set(best.values()), key=list(best.values()).count) if best else None
        out["readings"][reading] = {"acts": len(order), "means": means, "best": best,
                                    "same_best": sum(1 for v in best.values() if v == top), "rows": rows}
    rr = out["readings"].get(primary[0])
    if rr and primary[1] == metric:
        differs = [r["arm"] for r in rr["rows"] if r["p_language_holm"] < 0.05]
        out["verdict"] = {
            "reading": primary[0], "metric": metric,
            "differs_by_language": differs,
            "same_best_arm": rr["same_best"] == len(langs),
            "holds": not differs and rr["same_best"] == len(langs),
            # the legal session, 2026-11-13: a Codex holds several layers, each
            # document in one by a declared rule — a LANGUAGE rule per layer is
            # the option, inside one collection, when an arm's effect differs
            "per_language_layers": bool(differs),
            "said": ("the hypothesis holds on these acts: no arm's effect differs by language, and every "
                     "language has the same best arm" if not differs and rr["same_best"] == len(langs)
                     else ("the effect of " + ", ".join(differs) + " differs by language" if differs
                           else "no arm's effect differs detectably by language, but the best arm is not the "
                                "same everywhere (the arms are within noise of each other there)"))}
    return out


def report_markdown(runs: list[dict[str, Any]], *, primary: tuple[str, str] = PRIMARY) -> str:
    about = runs[0] if runs else {}
    langs = sorted({r["lang"] for r in runs})
    ac = across(runs, primary=primary)
    lines = ["# Chunker per language: LEMUR", "",
             (f"{len(langs)} languages ({', '.join(langs)}), {about.get('parallel')} parallel acts; encoder and "
              f"reranker of {about.get('collection')}; document run {str((about.get('runs') or {}).get('document') or '?')[:23]}, "
              f"query run {str((about.get('runs') or {}).get('query') or '?')[:23]}; every arm embedded fresh."), ""]
    verdict = ac.get("verdict")
    if verdict:
        lines += [f"## Verdict ({verdict['reading']} {verdict['metric']}, stated before the run)", "",
                  f"**{verdict['said']}.**", "",
                  (f"Per-language layers worth considering: **{'yes' if verdict['per_language_layers'] else 'no'}**"
                   + (f" ({', '.join(verdict['differs_by_language'])} differs by language: a layer per language, "
                      "each document in one by a language rule, inside the one collection)"
                      if verdict["per_language_layers"] else " (no arm's effect differs by language)") + "."), ""]
    found = about.get("structure_found") or {}
    if found:
        lines += ["## The structure the reader found, per language", "",
                  "| language | articles found | most any language found | share | acts short | acts without articles |",
                  "|---|---|---|---|---|---|"]
        for lang in langs:
            f = found.get(lang) or {}
            lines.append(f"| {lang} | {f.get('articles')} | {f.get('most_found')} | {f.get('share')} | "
                         f"{f.get('acts_short')} | {f.get('acts_without_articles')} |")
        lines += ["", ("A language short of the others has headings the reader missed: its structure arms "
                       "are cut on less structure there, for a reason that is not the chunker's."), ""]
    for reading, block in ac["readings"].items():
        mark = " — the deciding reading" if reading == primary[0] else ""
        lines += [f"## {reading}{mark} ({block['acts']} acts a language)", "",
                  "| language | " + " | ".join(f"{a} {ac['metric']}" for a in block["means"][langs[0]])
                  + " | best |", "|---|" + "---|" * len(block["means"][langs[0]]) + "---|"]
        for lang in langs:
            m = block["means"][lang]
            lines.append(f"| {lang} | " + " | ".join(f"{v:.4f}" for v in m.values()) + f" | {block['best'][lang]} |")
        lines += ["", f"The same best arm in {block['same_best']} of {len(langs)} languages.", "",
                  f"| arm − {ac['baseline']} | " + " | ".join(langs) + " | differs by language (p, Holm) | spread |",
                  "|---|" + "---|" * len(langs) + "---|---|"]
        for row in block["rows"]:
            cells = []
            for lang in langs:
                v = row["per_language"][lang]
                cells.append(f"{v['delta']:+.4f} [{v['interval95'][0]:+.4f}, {v['interval95'][1]:+.4f}]")
            lines.append(f"| {row['arm']} | " + " | ".join(cells)
                         + f" | {row['p_language']:.4f} ({row['p_language_holm']:.4f}) | {row['spread']:.4f} |")
        lines.append("")
    lines += ["## Per language, every arm against the first", ""]
    for lang in langs:
        rep = cm.report([r for r in runs if r["lang"] == lang], margin=None)
        lines += [f"### {lang}", "", *cm.report_markdown(rep, head=False, notes=False).splitlines()]
    lines += ["## Read with", "",
              ("- LEMUR is a title-shaped task: the query is the act's metadata header, the gold its body. "
               "It finds an act from its description; it does not answer a legal question."),
              ("- No arm writes the act's title, and the reranker judges the chunks bare: the title is the "
               "query here, so either would read the answer."),
              ("- 'differs by language' is a randomization test with the act as the block; a p above 0.05 "
               "says no difference was detected over these acts, not that none exists."), ""]
    return "\n".join(lines)


def tag(arms: list[Arm], langs: list[str], *, k: int, collection: str) -> str:
    """The settings a pilot reading is valid for: the arms, the languages,
    k, the collection whose encoder runs, and the code."""
    from tools.measure import chat_eval
    h = hashlib.sha256()
    for a in arms:
        h.update(a.name.encode() + json.dumps(a.manifest(), sort_keys=True).encode())
    for f in [Path(__file__), Path(cm.__file__), ROOT / "app" / "chunking.py", ROOT / "app" / "chunking_legal.py",
              ROOT / "app" / "layers.py", ROOT / "app" / "fts.py", ROOT / "app" / "retrieval.py",
              ROOT / "app" / "plugins" / "eurlex" / "rendition.py"]:
        h.update(f.read_bytes())
    raw = (f"lemur-{h.hexdigest()[:12]}-k{k}-{'-'.join(langs)}-enc-{collection}"
           f"-code-{chat_eval.ask_code()[7:19]}")
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw)


# ---- the command ------------------------------------------------------------------------

def _load(paths: list[Path]) -> tuple[dict[str, list[Act]], dict[str, Any]]:
    sets: dict[str, dict] = {}
    for path in paths:
        lang, acts = read_set(path)
        if lang in sets:
            raise SystemExit(f"two directories hold {lang}")
        sets[lang] = acts
    if len(sets) < 2:
        raise SystemExit("the test compares languages: give two or more `lemur-<code>` directories")
    acts, said = parallel(sets)
    if not said["parallel"]:
        raise SystemExit("no act is held in every language given")
    return acts, said


def _write(out: Path, name: str, payload: dict[str, Any]) -> Path:
    runs = Path(out) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{name}.json"
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _run(a) -> int:
    from app import collections as collections_mod
    from app import encoder_use
    from tools import legal_pilot as lp
    from tools.measure import chat_eval
    acts, said = _load(a.sets)
    langs = sorted(acts)
    found = structure_found(acts)
    print(f"{len(langs)} languages, {said['parallel']} parallel acts (left out: {said['left_out']})")
    c, con = lp._open(a.collection, None)
    con.close()
    recipe = collections_mod.recipe_for(c)
    base = base_params(c, tokenizer=a.tokenizer, max_tokens=a.max_tokens)
    arms = arms_of(a.arm or list(DEFAULT_ARMS), base)
    count = cm.counter(base["tokenizer"], recipe)
    if a.dry_run:
        for lang in langs:
            print(f"{lang}: structure found {found[lang]}")
            for arm in arms:
                try:
                    _, cost = cut(arm, acts[lang], lang, count=count, ctx=getattr(recipe, "ctx", None))
                except cm.OverWindow as e:
                    print(f"  {arm.name}: REFUSED — {e}")
                    continue
                print(f"  {arm.name}: {json.dumps(cost)}")
            sys.stdout.flush()
        return 0
    the_tag = tag(arms, langs, k=a.k, collection=c.name)
    only = pilot_of(acts, a.pilot) if a.pilot else None
    if only is None and not chat_eval._pilot_ok_path(a.out, SET, the_tag).is_file():
        print(f"no pilot read for these settings (tag {the_tag}): `run --pilot 3` with the same flags, read it, "
              f"then `pilot-ok --out {a.out} --tag {the_tag} --by WHO --note WHAT` (measuring.md §12)",
              file=sys.stderr)
        return 2
    rerank = cm.default_rerank(None, c, heads=False)
    if rerank is None:
        print(f"{c.name}: no reranker configured: the reranked reading is not made", file=sys.stderr)
    work = cm.workdir_for(a.out)
    holder = encoder_use.holder("chunker_languages run")
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(encoder_use.held(recipe, holder=holder))
            stack.enter_context(encoder_use.held(recipe, holder=holder, query=True))
            runs = cm.pin_runs(c, recipe)
            cache = cm.VectorCache(Path(a.out) / "chunker" / "vectors", recipe,
                                   space_id=getattr(getattr(c, "codex", None), "space_id", None), runs=runs)
            got = run(acts, arms, k=a.k, workdir=work, recipe=recipe, cache=cache, collection=c,
                      embed_fn=cm.pinned_embed(runs), rerank_fn=rerank, count=count, only=only)
    except cm.RerankWithheld as e:
        print(f"{e}: nothing is written; run again once the reranker answers", file=sys.stderr)
        return 3
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    basekeys = {"set": SET, "collection": c.name, "collection_id": getattr(getattr(c, "codex", None), "collection_id", None),
                "k": a.k, "pool": f"{SET}-parallel", "split": "test", "parallel": said["parallel"],
                "left_out": said["left_out"], "langs": langs, "structure_found": found, "exact": True,
                "tag": the_tag, "code": chat_eval.ask_code(), "arms": [x.name for x in arms],
                "runs": runs.as_dict(), "stored": False}
    if only is not None:
        parts = [f"# LEMUR per-language pilot [{the_tag}]", "",
                 (f"The first {len(only)} acts in every language, every arm and reading. Read them; then "
                  f"`chunker_languages.py pilot-ok --out {a.out} --tag {the_tag} --by WHO --note WHAT`."), ""]
        for lang in langs:
            for name, arm in got[lang]["arms"].items():
                for reading in READINGS:
                    if reading not in arm:
                        continue
                    parts += [f"## {lang} — {name} — {reading}", ""]
                    for row in arm[reading]["per_question"]:
                        parts += [f"- act {row['index']} ({row['law']}): {row['question'][:160]!r}",
                                  "  ranked: " + ", ".join(("GOLD " if n == row["index"] else "") + acts[lang][n].law
                                                         for n in row["ranking"][:5]),
                                  f"  mrr@10 {row['mrr@10']:.3f}"]
                    parts.append("")
        path = Path(a.out) / "runs" / SET / f"pilot-{the_tag}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(parts), encoding="utf-8")
        print(f"pilot written to {path}; read it, then `chunker_languages.py pilot-ok --out {a.out} --tag {the_tag} "
              f"--by WHO --note WHAT`")
        return 0
    for lang in langs:
        for name, arm in got[lang]["arms"].items():
            for reading in READINGS:
                if reading not in arm:
                    continue
                path = _write(a.out, f"lemur-{lang}-{name}-{reading}",
                              {**basekeys, "lang": lang, "arm": name, "legs": reading, "layer": arm["layer"],
                               "cost": arm["cost"], "embedding": arm["embedding"], "seconds_arm": arm["seconds"],
                               **arm[reading]})
                s = arm[reading]["summary"]
                print(f"{lang} {name:>14} {reading:>8}  mrr@10 {s.get('mrr@10', 0):.4f}  recall@10 "
                      f"{s.get('recall@10', 0):.4f}  → {path.name}")
    print(f"`chunker_languages.py report {a.out}/runs/*-lemur-*.json` reads every language together")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="every arm in every language, the parallel acts as the questions")
    p.add_argument("--sets", type=Path, nargs="+", required=True,
                   help="`encoder_bakeoff fetch --suite lemur-<code>` directories, one a language")
    p.add_argument("--collection", required=True,
                   help="the held collection whose encoder, instructions and reranker run, in every language")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--arm", action="append", default=None,
                   help="structure, structure-<N>, window-<N>, or NAME=<layer JSON>; repeated; the first is "
                        f"the baseline (default: {', '.join(DEFAULT_ARMS)})")
    p.add_argument("--tokenizer", default=None, help="sha256:<tokenizer.json>, where the collection's layer pins none")
    p.add_argument("--max-tokens", type=int, default=None, dest="max_tokens",
                   help="the structure arm's window, where the collection's layer states none")
    p.add_argument("--k", type=int, default=DEFAULT_K)
    p.add_argument("--pilot", type=int, default=None, help="the first N acts, in every language (§12)")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="the structure found and every arm cut and counted per language; nothing embedded")
    p = sub.add_parser("pilot-ok")
    p.add_argument("--out", type=Path, required=True); p.add_argument("--tag", required=True)
    p.add_argument("--by", required=True); p.add_argument("--note", required=True)
    p = sub.add_parser("report", help="every language and arm together, and the verdict")
    p.add_argument("runs", type=Path, nargs="+")
    p.add_argument("--primary", default=":".join(PRIMARY), help="READING:METRIC the verdict rests on")
    p.add_argument("--write", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return _run(a)
    if a.cmd == "pilot-ok":
        from tools.measure import chat_eval
        (a.out / "runs" / SET).mkdir(parents=True, exist_ok=True)
        return chat_eval.pilot_ok(a.out, SET, a.tag, a.by, a.note)
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in a.runs]
    reading, _, metric = a.primary.partition(":")
    md = report_markdown(runs, primary=(reading, metric))
    print(md)
    if a.write:
        a.write.parent.mkdir(parents=True, exist_ok=True)
        a.write.write_text(md + "\n", encoding="utf-8")
        print(f"written {a.write}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
