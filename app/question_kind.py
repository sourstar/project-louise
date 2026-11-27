"""THE QUESTION'S KIND: facts, literature or all (docs/notes/fact-fiction-search.md
§3; operator, 2026-11-27: "The search itself shouldn't return novels when I'm
looking only for facts and vice versa").

One short chat call, thinking off by the model's own switch, bounded at
`TIMEOUT_S`, every answer remembered per question (a failure is not: the next
search asks again). Unsure, no answer in time, or no chat model: all — the
search as it was, never a silent narrowing. A question that names a work, or
asks what a work says, is literature.

It runs BESIDE the legs (`start`): the legs do not wait for it, the filter
does, at most `TIMEOUT_S` from the start. The kind decides only what is left
out before the rerank (app/work_kind.py), and the result says which kind was
applied, how it was decided, and what it left out — one tap changes it.

Standard alternative, written down (CLAUDE.md, "do not reinvent the wheel"):
a zero-shot classifier (an NLI model behind the reranker's server) would
answer without the chat model's queue; it pays once the judged set shows the
chat call missing its bound often.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

#: the bound of the call, from its start: it sits on a search's path
TIMEOUT_S = 2.0
#: room for the JSON and a short thought where a model thinks anyway
MAX_TOKENS = 120
_CACHE_MAX = 512
_cache: dict[str, str] = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="question-kind")

KINDS = ("facts", "literature", "all")

SYSTEM = (
    "You decide what a library search is looking for. Reply 'facts' when it "
    "asks about the real world: history, people, places, science, law, what "
    "happened or is true. Reply 'literature' when it asks for literary works "
    "(novels, poems, plays, tales) or about one: a question that names a work, "
    "or asks what a work says, who is in it or how it ends. Reply 'unsure' "
    "when it could be either. "
    'Reply with ONLY JSON: {"kind": "facts" | "literature" | "unsure"}.'
    " /no_think")
SHOTS = (
    ("causes of the French Revolution", "facts"),
    ("romanzi sulla guerra di Crimea", "literature"),
    ("what happens at the end of Moby Dick", "literature"),
    ("whaling", "unsure"),
)


def reset() -> None:
    with _lock:
        _cache.clear()


def ask(query: str) -> str | None:
    """'facts' | 'literature' | 'all' from the chat model, or None when it
    could not be asked or gave nothing readable."""
    from app import llm
    messages = [{"role": "system", "content": SYSTEM}]
    for q, a in SHOTS:
        messages += [{"role": "user", "content": q},
                     {"role": "assistant", "content": json.dumps({"kind": a})}]
    messages.append({"role": "user", "content": query})
    try:
        raw = llm.chat(messages, temperature=0.0, max_tokens=MAX_TOKENS, num_ctx=2048,
                       timeout=TIMEOUT_S)
    except Exception:
        return None
    obj = llm._last_json_object(raw)
    got = obj.get("kind") if isinstance(obj, dict) else None
    if got in ("facts", "literature"):
        return got
    if got == "unsure":
        return "all"
    return None


def start(query: str) -> Callable[[], tuple[str, str]]:
    """Ask in the background now; the returned resolver gives (kind, by) —
    by: "the question" (the model's answer, or remembered), "not decided in
    time", "not decided" (no answer) — waiting at most what is left of
    `TIMEOUT_S`. Safe to call from several threads."""
    with _lock:
        hit = _cache.get(query)
    if hit is not None:
        return lambda: (hit, "the question")
    deadline = time.monotonic() + TIMEOUT_S
    fut: Future = _pool.submit(ask, query)
    done: list[tuple[str, str]] = []
    once = threading.Lock()

    def resolve() -> tuple[str, str]:
        with once:
            if done:
                return done[0]
            try:
                got = fut.result(timeout=max(0.0, deadline - time.monotonic()))
            except Exception:
                got = "late"
            if got in KINDS:
                with _lock:
                    if len(_cache) >= _CACHE_MAX:
                        _cache.pop(next(iter(_cache)))
                    _cache[query] = got
                done.append((got, "the question"))
            else:
                done.append(("all", "not decided in time" if got == "late" else "not decided"))
            return done[0]
    return resolve
