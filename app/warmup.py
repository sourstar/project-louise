"""The question path's models, loaded before the first question needs them.

Operator, 2026-10-27 (option A of four, in München's session): "is there a
way to warm up the models at startup or even between startups?" The first
question after the day's restarts got no answer: the translator's cold load
took 23 s, the reranker loaded twice, and the browser had gone when the list
was ready ~65 s in.

So the web, once at its start and again whenever a model server it asks
comes back after being unreachable (a LocalAI restart), warms in the
background, one after the other: the translator, each distinct reranker the
held collections name, and the chat model. Each goes through the existing
warm of its caller (`translate.warm`, `retrieval._warm_reranker`,
`llm.warm`), so app/model_load.py's rules hold: one warm per model at a
time, bounded by the model's load bound, a model already listed as loaded
is not touched. Nothing here blocks a request; a question asked meanwhile
joins the warm in flight.

NOT a keep-alive (option C, not chosen): a model evicted later, under
embedding load, is loaded again by the question that needs it, as before.
NOT the query encoders: 4-8 s cold, and the one gutenberg embeds with is
usually loaded anyway. Since 2026-11-08 the keep-alive exists, as a trial,
in the WORKER (app/resident.py, operator option A: "keep every model Ask uses
resident on the card"): these models and the query encoders, preloaded
and not pinned; this web-side warm is unchanged beside it.

A restart is seen as the server going unreachable and coming back
(`/system` answering None, then ids): LocalAI's `/system` carries no start
time. A restart faster than one `POLL_S` is missed and costs the first
question its load, as before. Standard alternative: a readiness hook in the
runner's orchestrator (a Kubernetes postStart / an init job) — the node runs
TrueNAS apps, which offer none.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable

#: how often each model server is asked whether it is there (one cached GET)
POLL_S = 10.0

_started = False
_lock = threading.Lock()


def _log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [web info:warmup] {msg}", flush=True)


def steps() -> list[tuple[str, str, Callable[[], object]]]:
    """(name, server, warm) for every model the question path always or
    slowly needs, in the order a question meets them."""
    from app import collections as collections_mod, config, llm, model_load, retrieval, translate
    out: list[tuple[str, str, Callable[[], object]]] = []
    if config.TRANSLATE_MODEL and config.TRANSLATE_URL:
        out.append((config.TRANSLATE_MODEL, model_load.server_of(config.TRANSLATE_URL), translate.warm))
    seen: set[tuple[str, str]] = set()
    try:
        held = collections_mod.load_all()
    except Exception:   # noqa: BLE001 — no collections read: no rerankers to warm
        held = []
    for c in held:
        r = collections_mod.reranker_for(c)
        if r is None or not r.model or (r.url, r.model) in seen:
            continue
        seen.add((r.url, r.model))
        out.append((r.model, model_load.server_of(r.url), lambda r=r: retrieval._warm_reranker(r)))
    if config.CHAT_API == "openai" and config.CHAT_URL and config.CHAT_MODEL:
        out.append((config.CHAT_MODEL, model_load.server_of(config.CHAT_URL), llm.warm))
    return out


def held_by_the_mode() -> str | None:
    """Why nothing is warmed now, or None: THE NODE'S MODE DECIDES, AS FOR
    THE KEEPER (`resident.preloads`: paused, seed-only, or a profile that
    holds the node there — "the GPU is the operator's", 2026-10-23). The
    web's warm predates the keeper and never asked: on a PAUSED node, after
    the model server's restart, it loaded MADLAD, a reranker and the chat
    model by itself, onto a card a pass had emptied for its gates (München,
    2026-11-25, twice that day). The node's state is read as the keeper
    reads it — absent is "paused", unreadable "unknown", and neither warms;
    only a resolution that itself fails holds nothing back, and the warm is
    then what it was."""
    try:
        from app import node_state, resident
        ok, why = resident.preloads(node_state.peek_worker_state())
        return None if ok else why
    except Exception:   # noqa: BLE001 — unread: as before
        return None


def warm_all(why: str, only: set[str] | None = None) -> None:
    """Warm each step, one at a time; `only` limits it to these servers.
    Every outcome is said; none is raised. Nothing is warmed where the
    node's mode preloads nothing (`held_by_the_mode`): said in one line."""
    held = held_by_the_mode()
    if held is not None:
        _log(f"nothing warmed ({why}): {held} — a question loads what it needs when it is asked")
        return
    for name, server, warm in steps():
        if only is not None and server not in only:
            continue
        t0 = time.monotonic()
        try:
            warm()
            _log(f"{name}: ready after {time.monotonic() - t0:.1f} s ({why})")
        except Exception as e:   # noqa: BLE001 — a warm never fails anything
            _log(f"{name}: not warmed ({why}): {type(e).__name__}: {str(e)[:200]}")


def _back(up: dict[str, bool]) -> set[str]:
    """The model servers that answer now after not answering at the last
    look (`up`, updated in place): a restart, as far as one can be seen."""
    from app import model_load
    back = set()
    for s in {server for _, server, _ in steps()}:
        now = model_load.loaded_models(s, model_load.SYSTEM_TTL_S) is not None
        if now and up.get(s) is False:
            back.add(s)
        up[s] = now
    return back


def _watch() -> None:
    # the historical reader's table versions, off the query path: at start,
    # and at each look, so a sync run by another process is warmed here
    # (app/reading.py `warm`; a version already computed costs one SELECT)
    from app import reading
    reading.warm()
    warm_all("web start")
    up: dict[str, bool] = {}
    while True:
        time.sleep(POLL_S)
        reading.warm()
        back = _back(up)
        if back:
            warm_all("model server back: " + ", ".join(sorted(back)), only=back)


def open_text_indexes(why: str = "web start") -> list[tuple[str, int, float]]:
    """THE TEXT LEG'S INDEXES, OPENED BEFORE THE FIRST QUESTION NEEDS THEM
    (2026-11-25: the operator's first question after a deploy, "fts 12,600",
    over gutenberg — a 54 GB text index in 21 language groups on the SATA
    drive, opened by that question, one group after the other under one lock;
    later searches of the same process measured 0.4 s at the median,
    storage-tiering.md §12). Every searched collection's language groups are
    opened — several at once (`fts.open_all`), the collections one after the
    other so the drive is not asked for everything at the same moment — each
    with its searcher made, and its seconds said: that line is the
    measurement of what a first question paid until now.

    What it does NOT do: read the postings a question's words will need —
    those stay the drive's to give at the first search that asks for them.
    A question asked meanwhile opens what it needs itself and waits for
    nothing here. Returns [(collection, groups opened, seconds)]; never
    raises."""
    from app import collections as collections_mod, fts
    out: list[tuple[str, int, float]] = []
    try:
        held = collections_mod.load_all()
    except Exception as e:   # noqa: BLE001 — no collections read: nothing to open
        _log(f"text indexes not opened ({why}): {type(e).__name__}: {str(e)[:200]}")
        return out
    t0 = time.monotonic()
    for c in held:
        try:
            if collections_mod.is_annotations(c):
                continue                         # claims: no text leg
            opened, failed, seconds = fts.open_all(c)
        except Exception as e:   # noqa: BLE001 — one collection never stops the rest
            _log(f"{c.name}: text index not opened ({why}): {type(e).__name__}: {str(e)[:200]}")
            continue
        if not opened and not failed:
            continue
        out.append((c.name, len(opened), seconds))
        _log(f"{c.name}: text index opened, {len(opened)} language group(s) in {seconds:.1f} s ({why})"
             + (f" — NOT opened: {'; '.join(f'{g}: {m}' for g, m in sorted(failed.items()))}" if failed else ""))
    if out:
        _log(f"text indexes: {sum(n for _, n, _ in out)} group(s) of {len(out)} collection(s) opened in "
             f"{time.monotonic() - t0:.1f} s ({why}) — a first question no longer opens them")
    return out


def start() -> bool:
    """Start the watcher once per process; False when switched off
    (`LOUISE_WARMUP=0`, the test suite's) or already started. The text
    indexes are opened on a thread of their own: drive work beside the
    models' loads, neither waiting for the other."""
    global _started
    if os.getenv("LOUISE_WARMUP", "1") == "0":
        return False
    with _lock:
        if _started:
            return False
        _started = True
    threading.Thread(target=_watch, name="warmup", daemon=True).start()
    threading.Thread(target=open_text_indexes, name="warmup-text", daemon=True).start()
    return True
