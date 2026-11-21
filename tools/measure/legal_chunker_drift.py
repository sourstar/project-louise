"""What `legal_article` version 4 changes, on a collection's REAL documents
(2026-11-21; app/chunking_legal.py, docs/notes/legal-chunker-v4.md): every
document cut at its layer's version (3 on the shared legal space) and at 4,
per language — the documents and chunks, the documents whose chunk TEXTS
differ and those chunks, and the chunks that fell to `_windows` (a sentence
past the window, cut mid-sentence) at each version — with a few examples.

    python -m tools.measure.legal_chunker_drift <collection> [--to 4] [--window 5000]
        [--examples 3] [--limit N] [--processes N] [--out FILE]

The cut is the text index stage's: each document's `[title, sections]`
through `record_source.title_sections_and_source` (its fresh record, the
parse otherwise; app/parallel.py kind "chunks"), its layer through
`layers.chunks_for_document` at the layer's version, and the same layer at
`--to` through `layers._chunker_of`, the stages' own binding. Documents in
scope and without an error, walked by rowid in windows (`--window`), each
window a task of the parse pool (`parallel.each_part`, at most the cores the
node profile allows, `--processes` to ask fewer): never one process.

"Chunks to embed again" are the `--to` chunks whose text the document's
current chunks do not hold: what a carry by chunk hash (06 §6.3) cannot keep.
"Chunks dropped" are the current ones whose text `--to` no longer cuts.

READ-ONLY: the manifest is opened read-only, nothing is written — but the
stage's own read is the stage's, so a sampled record whose sections differ
from the parse writes record_source's mismatch mark, as the worker would
(a defect either way, docs/notes/one-parse.md). One JSON object per language
on stdout, then the totals and the examples; a table on stderr. Exit 1 when
any document could not be cut.

MEMORY: a child cuts a document as the worker's index stage does, twice, so
it meets the same peaks — EUR-Lex5's largest units, ~5 GB for 32021R2017 el's
parse and chunks (docs/notes/STATUS.md, 2026-11-19). Run it in a container of
its own with a memory cap, `--processes` sized to it; a child the kernel
kills for memory ends the pass (`parallel.each_part`, the OOM rule), and
`--limit` pilots a first window.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: the counters kept per language
FIELDS = ("documents", "chunks", "chunks_to", "documents_differ", "chunks_to_embed", "chunks_dropped",
          "windowed", "windowed_to", "documents_windowed", "documents_windowed_to", "errors")
#: characters of context an example shows on each side of its first difference
EXAMPLE_CHARS = 160


@contextmanager
def _counting_windows():
    """`chunking_legal._windows` counted for the cut inside: each piece it
    returns is one chunk (`split` returns its pieces as they are). Wrapped in
    this tool's own process — a child of its pass, or itself — and restored."""
    from app import chunking_legal as cl
    seen = [0]
    plain = cl._windows

    def counted(tok, text, budget):
        got = plain(tok, text, budget)
        seen[0] += len(got)
        return got
    cl._windows = counted
    try:
        yield seen
    finally:
        cl._windows = plain


def cut_both(collection, row: dict, plugin, to_version: int) -> tuple[list[dict], list[dict], int, int] | None:
    """`(current chunks, chunks at to_version, windowed current, windowed at
    to_version)` of one document, or None when its layer is not a
    `legal_article` one."""
    from app import chunking, layers, record_source
    native_id = int(row["native_id"])
    layer = layers.layer_of(collection, native_id, row)
    if layer.chunker != chunking.LEGAL_ARTICLE:
        return None
    title, sections, _source = record_source.title_sections_and_source(
        collection, row, plugin, pair=layers.has_pair_layer(collection))
    with _counting_windows() as w:
        now = layers.chunks_for_document(collection, row, sections, title=title)
    with _counting_windows() as w_to:
        then = list(layers._chunker_of(replace(layer, version=int(to_version)))(
            row["uid"], native_id, sections, title=title))
    return now, then, w[0], w_to[0]


def _example(uid: str, lang: str, now: list[dict], then: list[dict]) -> dict:
    """The first chunk whose text differs, around its first differing character."""
    i = next((k for k, (a, b) in enumerate(zip(now, then)) if a["text"] != b["text"]), min(len(now), len(then)))
    a = now[i]["text"] if i < len(now) else ""
    b = then[i]["text"] if i < len(then) else ""
    at = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    lo = max(0, at - EXAMPLE_CHARS)
    return {"uid": uid, "lang": lang, "chunk": i, "chunks": len(now), "chunks_to": len(then),
            "chapter": (now[i] if i < len(now) else then[i]).get("chapter"),
            "current": a[lo:at + EXAMPLE_CHARS], "to": b[lo:at + EXAMPLE_CHARS]}


def cut_window(collection, lo: int, hi: int, to_version: int, keep: int) -> dict:
    """One task of the pass, in a child of the parse pool: the documents of
    rowids (lo, hi], each cut both ways, summed per language; `keep`
    examples a language at most."""
    from app import db
    per: dict[str, Counter] = {}
    examples: list[dict] = []
    kept: Counter = Counter()
    skipped = Counter()
    first_error: dict[str, str] = {}
    con = db.read_only_connect(collection)
    if con is None:
        return {"langs": {}, "examples": [], "skipped": {"no manifest": 1}, "first_error": {}}
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM documents WHERE rowid > ? AND rowid <= ? ORDER BY rowid", (lo, hi))]
    finally:
        con.close()
    plugin = collection.plugin_instance()
    for row in rows:
        if row.get("error") is not None or not row.get("in_scope"):
            skipped["out of scope or errored"] += 1
            continue
        lang = row.get("lang") or "?"
        c = per.setdefault(lang, Counter())
        try:
            got = cut_both(collection, row, plugin, to_version)
        except Exception as e:   # noqa: BLE001 — counted per language, the pass goes on
            c["errors"] += 1
            first_error.setdefault(lang, f"{row['uid']}: {type(e).__name__}: {e}"[:300])
            continue
        if got is None:
            skipped["another chunker's layer"] += 1
            continue
        now, then, w_now, w_then = got
        texts_now, texts_then = Counter(x["text"] for x in now), Counter(x["text"] for x in then)
        differ = [x["text"] for x in now] != [x["text"] for x in then]
        c.update(documents=1, chunks=len(now), chunks_to=len(then), documents_differ=int(differ),
                 chunks_to_embed=sum((texts_then - texts_now).values()),
                 chunks_dropped=sum((texts_now - texts_then).values()),
                 windowed=w_now, windowed_to=w_then,
                 documents_windowed=int(w_now > 0), documents_windowed_to=int(w_then > 0))
        if differ and kept[lang] < keep:
            kept[lang] += 1
            examples.append(_example(row["uid"], lang, now, then))
    return {"langs": {k: dict(v) for k, v in per.items()}, "examples": examples, "skipped": dict(skipped),
            "first_error": first_error}


def _processes(asked: int | None) -> tuple[int | None, str]:
    """The pool's bound: the node profile's cores (app/cpu_limit.py), `asked`
    when it is fewer; None is the pool's own size."""
    from app import cpu_limit
    lim = cpu_limit.current()
    cores = lim.cores
    if asked is not None:
        cores = asked if cores is None else min(asked, cores)
    return cores, lim.says()


def measure(collection, *, to_version: int = 4, window: int = 5000, examples: int = 3,
            limit: int | None = None, processes: int | None = None,
            log=lambda m: print(m, file=sys.stderr, flush=True)) -> dict:
    """The pass: every window of the manifest on the parse pool, in order."""
    from app import db, parallel
    from tools.measure import legal_chunker_drift as me      # by name: a spawned child imports it
    con = db.read_only_connect(collection)
    if con is None:
        raise SystemExit(f"legal_chunker_drift: {collection.name} has no manifest")
    try:
        top = con.execute("SELECT MAX(rowid) FROM documents").fetchone()[0] or 0
    finally:
        con.close()
    if limit is not None:
        top = min(top, int(limit))
    cores, says = _processes(processes)
    if cores == 0:
        raise SystemExit(f"legal_chunker_drift: the node allows no core now ({says}); "
                         f"--processes N runs it anyway")
    tasks = [(collection, lo, min(lo + window, top), int(to_version), int(examples))
             for lo in range(0, top, window)]
    log(f"[drift] {collection.name}: {top} rowids in {len(tasks)} window(s), at most "
        f"{cores or parallel.processes()} process(es) ({says})")
    per: dict[str, Counter] = {}
    kept: Counter = Counter()
    out_examples: list[dict] = []
    skipped: Counter = Counter()
    first_error: dict[str, str] = {}
    t0 = time.monotonic()
    for k, got in enumerate(parallel.each_part(me.cut_window, tasks, limit=cores), 1):
        for lang, counts in got["langs"].items():
            per.setdefault(lang, Counter()).update(counts)
        for ex in got["examples"]:
            if kept[ex["lang"]] < examples:
                kept[ex["lang"]] += 1
                out_examples.append(ex)
        skipped.update(got["skipped"])
        for lang, msg in got["first_error"].items():
            first_error.setdefault(lang, msg)
        if k % 20 == 0:
            log(f"[drift] {k}/{len(tasks)} windows, {sum(c['documents'] for c in per.values())} documents, "
                f"{time.monotonic() - t0:.0f} s")
    langs = {lang: {f: int(per[lang].get(f, 0)) for f in FIELDS} for lang in sorted(per)}
    totals = {f: sum(v[f] for v in langs.values()) for f in FIELDS}
    return {"collection": collection.name, "to": int(to_version), "languages": langs, "totals": totals,
            "skipped": dict(skipped), "first_error": first_error, "examples": out_examples,
            "seconds": round(time.monotonic() - t0, 1)}


def table(result: dict) -> str:
    head = ("lang", "docs", "chunks", "docs differ", "to embed", "dropped", "windowed", f"windowed v{result['to']}")
    rows = [(lang, v["documents"], v["chunks"], v["documents_differ"], v["chunks_to_embed"], v["chunks_dropped"],
             v["windowed"], v["windowed_to"]) for lang, v in result["languages"].items()]
    t = result["totals"]
    rows.append(("all", t["documents"], t["chunks"], t["documents_differ"], t["chunks_to_embed"],
                 t["chunks_dropped"], t["windowed"], t["windowed_to"]))
    lines = [" | ".join(str(x) for x in head), " | ".join("---" for _ in head)]
    lines += [" | ".join(str(x) for x in r) for r in rows]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="legal_chunker_drift", description=__doc__.split("\n")[0])
    p.add_argument("collection")
    p.add_argument("--to", type=int, default=4, help="the version compared with the layer's own (default 4)")
    p.add_argument("--window", type=int, default=5000, help="rowids a task (default 5000)")
    p.add_argument("--examples", type=int, default=3, help="examples a language (default 3)")
    p.add_argument("--limit", type=int, default=None, help="only rowids up to this one (a pilot)")
    p.add_argument("--processes", type=int, default=None, help="fewer processes than the profile allows")
    p.add_argument("--out", type=Path, default=None, help="the whole result as JSON, here too")
    a = p.parse_args(argv)
    from app import chunking_legal as cl
    from app import collections as collections_mod
    if a.to not in cl.VERSIONS:
        print(f"legal_chunker_drift: legal_article has versions {list(cl.VERSIONS)}", file=sys.stderr)
        return 2
    coll = collections_mod.by_name(a.collection)
    if coll is None:
        print(f"legal_chunker_drift: no collection {a.collection!r}", file=sys.stderr)
        return 2
    got = measure(coll, to_version=a.to, window=a.window, examples=a.examples, limit=a.limit,
                  processes=a.processes)
    for lang, v in got["languages"].items():
        print(json.dumps({"lang": lang, **v}, ensure_ascii=False))
    print(json.dumps({k: got[k] for k in ("collection", "to", "totals", "skipped", "first_error", "seconds")},
                     ensure_ascii=False))
    for ex in got["examples"]:
        print(json.dumps({"example": ex}, ensure_ascii=False))
    print(table(got), file=sys.stderr)
    if a.out:
        a.out.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if got["totals"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
