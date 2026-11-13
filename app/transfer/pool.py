"""Bounded worker pool for acquisition (architecture-v2.md §5.2's three
parallelism axes).

  across files    — a sliding window of workers over a prioritised queue
  across mirrors  — per-endpoint slots, EWMA-steered (see TransferManager)
  across stages   — download and checksum overlap ACROSS files, because one
                    work item owns a whole file's chain (fetch -> verify ->
                    digest). There is deliberately no second pool fed by a
                    bounded queue: producers blocking on put() while holding
                    every slot, or shutdown(wait=True) joining a worker blocked
                    on an undrained queue, both hang with no pytest-timeout in
                    this repo to catch it.

SLIDING WINDOW, NOT SUBMIT-ALL. If every item were queued up front, "stop
starting new files" would be a no-op on SIGTERM — every file would already be
in flight or queued. Submission is throttled to a small multiple of the worker
count and halts the moment the stop event is set.

RESULTS COME BACK TO THE CALLER'S THREAD. `on_result` is invoked from the
thread that called run_pool, never from a worker, so callers can touch a
SQLite connection (which is thread-affine) while still committing each row as
early as possible rather than batching to the end.
"""
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, Iterable, Iterator, TypeVar

T = TypeVar("T")
R = TypeVar("R")

_WINDOW_MULTIPLE = 2


def run_pool(items: Iterable[T],
             worker: Callable[[T], R],
             on_result: Callable[[R], None],
             max_workers: int = 4,
             stop: threading.Event | None = None) -> int:
    """Run `worker` over `items`, calling `on_result` on THIS thread for each.

    `worker` must not raise — catch inside it and return a failure record, the
    idiom app/retrieval.py::federated_search already uses. A raising worker
    would make the executor's iterator throw partway through, and every item
    after it would silently lose its result (files on disk with no manifest
    row — a CLAUDE.md invariant-2 break).

    Returns the number of items processed.
    """
    if max_workers <= 1:
        # Serial fallback with no executor at all, matching
        # embeddings._embed_texts()'s convention for a single worker.
        processed = 0
        for item in items:
            if stop is not None and stop.is_set():
                break
            on_result(worker(item))
            processed += 1
        return processed

    pending: Iterator[T] = iter(items)
    processed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures: set = set()

        def _submit_next() -> bool:
            if stop is not None and stop.is_set():
                return False
            try:
                item = next(pending)
            except StopIteration:
                return False
            futures.add(ex.submit(worker, item))
            return True

        for _ in range(max_workers * _WINDOW_MULTIPLE):
            if not _submit_next():
                break

        while futures:
            done, still_running = wait(futures, return_when=FIRST_COMPLETED)
            futures = set(still_running)
            for future in done:
                on_result(future.result())
                processed += 1
                _submit_next()
    return processed


#: how long a worker waits for an endpoint's cool-down or pacing before the
#: unit is DEFERRED to the next round (backoff.py; the architecture session,
#: 2026-11-14: "never a thread blocked for 15 minutes")
SLOT_WAIT_BOUND_S = 120.0


def _min_interval(spec) -> float | None:
    et = getattr(spec, "etiquette", None) if spec is not None else None
    return getattr(et, "min_interval_s", None)


def _paced(manager, key: str, spec, stop, sem, bound_s: float, sleep=time.sleep) -> bool:
    """With the slot held: wait out the endpoint's cool-down and
    `min_interval_s` since its last start, a stop checked every step. False
    (the slot released) when the wait passes `bound_s` or a stop comes."""
    import time as _time
    waited_until = _time.monotonic() + bound_s
    while True:
        if stop is not None and stop.is_set():
            sem.release()
            return False
        wait_s = manager.pace(key, _min_interval(spec))
        if wait_s <= 0:
            return True
        if _time.monotonic() + wait_s > waited_until:
            sem.release()
            return False
        sleep(min(0.5, wait_s))


def acquire_endpoint_slot(manager, ranked, spec, stop: threading.Event | None = None, *,
                          bound_s: float | None = None):
    """Take a per-endpoint concurrency slot, preferring the fastest mirror that
    has one free right now and is not cooling down.

    Returns (key, endpoint, semaphore), or None if the stop event fired or
    every endpoint is cooling down past SLOT_WAIT_BOUND_S (the unit is then
    deferred to the next round: nothing is written, it stays pending). The
    caller MUST release the semaphore in a finally.

    Politeness is never overridden by parallelism: slots come from
    min(global per_endpoint, Endpoint.max_conns, RateLimit.max_conns), and
    a request starts only after the endpoint's cool-down and its
    `min_interval_s` (app/acquire/backoff.py, the manager's `pace`).
    """
    if not ranked:
        return None
    bound = SLOT_WAIT_BOUND_S if bound_s is None else bound_s
    # First pass: a free slot on the fastest-measured mirror not cooling down,
    # without blocking.
    for key, endpoint in ranked:
        if manager.cooled_for(key) > 0:
            continue
        sem = manager.endpoint_semaphore(key, manager.endpoint_slots(endpoint, spec))
        if sem.acquire(blocking=False):
            if _paced(manager, key, spec, stop, sem, bound):
                return key, endpoint, sem
            return None
    # Everything is busy or cooling: queue on the endpoint whose cool-down
    # ends first, waking periodically so a stop event is noticed; past the
    # bound, deferred.
    key, endpoint = min(ranked, key=lambda ke: manager.cooled_for(ke[0]))
    if manager.cooled_for(key) > bound:
        return None
    sem = manager.endpoint_semaphore(key, manager.endpoint_slots(endpoint, spec))
    while True:
        if stop is not None and stop.is_set():
            return None
        if sem.acquire(timeout=0.1):
            if _paced(manager, key, spec, stop, sem, bound):
                return key, endpoint, sem
            return None
