"""WHEN A MODEL WAS LAST REALLY USED — the record the card's eviction reads
(operator, 2026-11-16, verbatim: "Evinction policy: some embedders have not
been used in a long time; those ones shall always be evincted instead of the
ones that are actively used, either for embedding of for querying. A crowded
GPU is a good benchmark for an effective evinction policy"; app/model_evict.py).

THE ONE TABLE: node_state `encoder_use` (server, model, last_used), as the
encoders' requests (`embeddings._touch`) and the translation model's
(`translate._touch`) already kept it since 2026-10-19 / 2026-11-08 — every
role now writes the same row: a document embedding, a query embedding, a
rerank, a chat answer, a translation, a sentence spoken, a clip transcribed.
No parallel store.

REAL USE ONLY. LocalAI's own `lastUsed` (what its reclaimer evicts by) is
refreshed by ANY request, and on the node Louise's own non-work traffic kept
idle models "recent": the keeper's preload warms (app/resident.py) and the
worker's run check of sixteen texts per encoder at every start (36 requests
to qwen3-4b, 17 to dar-legal-it and medcpt after a 07:31Z restart, none of
them work — München, 2026-11-16), so the reclaimer took the translation
model 9 times out of 10, minutes after its use. Here:
- a WARM never writes the row: every warm (`model_load.ensure_loaded`'s
  `warm`, the keeper's speech request, `retrieval._warm_reranker`,
  `llm.warm`, `embeddings._warm_request`) sends its request past the call
  sites that write it;
- a CHECK never writes it: `not_work()` marks a block whose requests are a
  run check, a gate, a calibration, a probe, a canary, a bundle's
  verification (`embeddings.embed_calibration`, `run_check.answers`,
  `run_check.checking`, app/bundles/importer.py, verify_on_use.py), and a
  request inside it writes nothing. A context variable, so a pool thread
  that copies its caller's context (`embeddings._send_texts`) inherits it.

THE STANDARD: this is the access bit of the CLOCK / LRU page-replacement
family (Corbató 1968): the reference bit is set by a real access, and a
prefetch that nobody has read yet does not make a page "recently used".
Kept per model in SQLite because every process of the node (the worker, the
web, a command) shares it.
"""
from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager
from typing import Iterator

#: the row is rewritten at most this often per (server, model) in this process
TOUCH_EVERY_S = 5.0

_not_work: contextvars.ContextVar[bool] = contextvars.ContextVar("model_use_not_work", default=False)
_touched: dict[tuple[str, str], float] = {}
_lock = threading.Lock()


@contextmanager
def not_work() -> Iterator[None]:
    """The requests of this block are Louise's own — a check, a gate, a
    calibration, a probe, a preload — and are no model's use."""
    token = _not_work.set(True)
    try:
        yield
    finally:
        _not_work.reset(token)


def counts() -> bool:
    """Whether a request made now is real use (no `not_work` block around it)."""
    return not _not_work.get()


def reset() -> None:
    """Forget this process's rate limit (tests)."""
    with _lock:
        _touched.clear()


def touch(url: str | None, model: str | None, *, now: float | None = None) -> None:
    """`model` at `url` just answered a request of real work: its row in
    node_state `encoder_use`. Rate-limited per model, best effort, never
    raises, never creates the store (a node with none has nothing to tell),
    and a request inside `not_work()` writes nothing."""
    if not url or not model or not counts():
        return
    from . import model_load, node_state
    try:
        key = (model_load.server_of(url), model)
        now = time.time() if now is None else now
        with _lock:
            if now - _touched.get(key, 0.0) < TOUCH_EVERY_S:
                return
            _touched[key] = now
        if not node_state.path().is_file():
            return
        node_state.touch_encoder(key[0], key[1], now=now)
    except Exception:   # noqa: BLE001 — bookkeeping for the eviction, not the request
        pass


def last_used() -> dict[tuple[str, str], float]:
    """When each (server, model) was last really used on this node, read
    without creating or writing anything; {} when it cannot be read."""
    from . import node_state
    try:
        return node_state.peek(node_state.encoder_last_used, {}, error={}, table="encoder_use")
    except Exception:   # noqa: BLE001 — unread: no recency
        return {}
