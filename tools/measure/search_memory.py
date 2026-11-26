"""What ONE search leaves in a process's memory, and where its seconds go,
leg by leg and collection by collection — with no model asked and nothing
written.

    python -m tools.measure.search_memory --query "…" [--collections a,b] [--k 10]
                                          [--times 2] [--trace] [--out FILE]
    (from /srv, the image's tree, in a container of the WEB's recipe: its image,
     env and volumes — never inside the web's own cgroup, so the web's readings
     stay clean)

WHY (München, 2026-11-26, the deploy of a2b5935b). The web held 1,188 MiB of
anonymous memory after its warm-up and ONE question, and 1,130 MiB twenty
minutes later with no search since — over the operator's 1 GB (the web under
1 GB, 2026-11-23). The open of all 69 text-index groups was measured apart, in
its own process: 8 MiB. The claim index the web resolves has its seek table.
So the search holds it, and the line of that question — "rerank 39,324 … rerank
by list: wikipedia 21,071 (… works 18,792)", "fts 12,125 (wikidata 12,125)" —
says where its seconds went and not where its bytes did.

WHAT IT RUNS. `retrieval.federated_search`, the Ask page's own path, over one
collection at a time and then over all of them, in two modes: "fts" (the text
leg) and "catalog" (the catalogue leg) — each followed, as in a real search,
by the grouping of records into works. After every step it reads this
process's own RssAnon and RssFile; then it collects, trims the heap as the
web's idle trim does (`idle_trim.trim`) and reads again: what is still there
is HELD, not waiting to be given back.

WHAT IT LEAVES OUT, and says so in its output:
  - the vector leg and the reranker: both ask a model, and a question takes
    the card from the corpus (measuring.md §6: measure without changing what
    is measured). The reranker is replaced by "kept the fused order";
  - the translation of the query (`translate=False`): the same reason;
  - the search's two writes — the citations queued for a re-embed
    (`verify_on_use.cite`) and the lazy-embed queue: measurement is not a
    writer here.
So a figure it gives is a LOWER bound of what the web's search holds, and the
difference to the web's own reading is what those three add.

`--trace`: Python's tracemalloc on, and the twenty largest allocation sites at
the end. It sees Python's objects only: what tantivy, Lance or SQLite hold is
in RssAnon and in no site — the gap between the two is theirs. Tracing itself
costs memory and time: run once without it for the figures, once with it for
the names.

Prints one JSON object (and writes it to `--out`). Nothing else is written.
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from typing import Any


def rss() -> dict[str, int]:
    """This process's VmRSS, RssAnon and RssFile, in MiB."""
    out: dict[str, int] = {}
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith(("VmRSS", "RssAnon", "RssFile")):
                    key, value = line.split(":", 1)
                    out[key] = int(value.split()[0]) // 1024
    except OSError:
        pass
    return out


def _trimmed() -> dict[str, int]:
    """Collect, give the freed heap back as the web's idle trim does, read."""
    gc.collect()
    try:
        from app import backend, idle_trim
        backend.release_arrow_memory()
        idle_trim.trim(log=lambda *a, **k: None)
    except Exception:   # noqa: BLE001 — no trim here: the reading says what it is
        pass
    return rss()


def _parts(result: dict[str, Any]) -> dict[str, Any]:
    """The search's own timings, the parts this tool is about."""
    t = result.get("timings") or {}
    keep = {k: t.get(k) for k in ("total", "languages", "catalog", "fts", "dense", "rerank") if k in t}
    per = t.get("per_collection") or {}
    if per:
        keep["per_collection"] = {name: {k: v for k, v in legs.items() if isinstance(v, (int, float))}
                                  for name, legs in per.items()}
    works = {name: {k: v for k, v in parts.items() if k.startswith("works")}
             for name, parts in (t.get("rerank_parts") or {}).items()}
    keep["works"] = {name: got for name, got in works.items() if got}
    keep["hits"] = len(result.get("hits") or [])
    return keep


def measure(query: str, names: list[str] | None = None, *, k: int = 10, times: int = 2,
            trace: bool = False) -> dict[str, Any]:
    """The measurement (the module's docstring), as one dict."""
    if trace:
        import tracemalloc
        tracemalloc.start(1)
    from app import collections as collections_mod, retrieval

    left_out = ["the vector leg and the reranker (models)", "the query's translation (a model)",
                "the citations queue and the lazy-embed queue (writes)"]
    retrieval._reranked = lambda query, hits, *a, **kw: (hits, False, "measurement: no reranker asked")
    retrieval.verify_on_use.cite = lambda *a, **kw: None
    retrieval._enqueue_lazy_embed = lambda *a, **kw: None

    out: dict[str, Any] = {"query": query, "k": k, "left_out": left_out, "trace": trace,
                           "start": rss(), "steps": []}
    held = [c for c in collections_mod.load_all() if not collections_mod.is_annotations(c)]
    if names:
        held = [c for c in held if c.name in set(names)]
    out["collections"] = [c.name for c in held]

    def step(what: str, collections: list, mode: str) -> None:
        for n in range(1, times + 1):
            before = rss()
            t0 = time.monotonic()
            try:
                got = _parts(retrieval.federated_search(collections, query, k=k, mode=mode, translate=False))
            except Exception as e:   # noqa: BLE001 — said, and the other steps still run
                got = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
            seconds = round(time.monotonic() - t0, 2)
            after = rss()
            trimmed = _trimmed()
            out["steps"].append({
                "what": what, "mode": mode, "time": n, "seconds": seconds, "search": got,
                "before": before, "after": after, "after_trim": trimmed,
                "held_mib": trimmed.get("RssAnon", 0) - before.get("RssAnon", 0)})

    for c in held:
        for mode in ("fts", "catalog"):
            step(c.name, [c], mode)
    if len(held) > 1:
        for mode in ("fts", "catalog"):
            step("all", held, mode)
    out["end"] = _trimmed()
    out["held_mib"] = out["end"].get("RssAnon", 0) - out["start"].get("RssAnon", 0)
    if trace:
        import tracemalloc
        top = tracemalloc.take_snapshot().statistics("lineno")[:20]
        out["python_sites"] = [{"site": str(s.traceback), "mib": round(s.size / 2 ** 20, 1), "blocks": s.count}
                               for s in top]
        out["python_mib"] = round(tracemalloc.get_traced_memory()[0] / 2 ** 20, 1)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--query", required=True)
    ap.add_argument("--collections", default="", help="comma-separated local names (default: every searched one)")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--times", type=int, default=2, help="each step this many times: the second is the warm one")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    # the app's own lines (a search says its seconds on stdout) go to stderr
    # while it runs: stdout carries the one JSON object and nothing else
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):
        got = measure(a.query, [n for n in a.collections.split(",") if n], k=a.k, times=a.times, trace=a.trace)
    text = json.dumps(got, indent=1, ensure_ascii=False)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
