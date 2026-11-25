"""The tokenizer's own threads, per pool child (app/parallel.py; 2026-11-25).

The legal chunker encodes a text's spans together (`encode_batch`), on the
tokenizers library's thread pool — by default one thread a core in EVERY
process that chunks. Each child is told its number before it encodes
anything: one for a child of the shared pool and of an operator's pass, the
lane's number for the large lane's child (alone by design), held to the node
profile's cores and counted as that many parts against the profile's cap."""
from __future__ import annotations

import os
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from app import chunking_legal as cl
from app import config, cpu_limit, parallel
from tests.test_parallel_large_lane import BIG, K, _coll, _Pool, _rows, fake_compute  # noqa: F401


def _env() -> tuple[str | None, str | None]:
    return os.environ.get("TOKENIZERS_PARALLELISM"), os.environ.get("RAYON_NUM_THREADS")


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("TOKENIZERS_PARALLELISM", raising=False)
    monkeypatch.delenv("RAYON_NUM_THREADS", raising=False)


def test_a_process_is_told_its_tokenizer_threads(clean_env):
    parallel.tokenizer_threads(3)
    assert _env() == ("true", "3")
    parallel.tokenizer_threads(0)
    assert _env() == ("true", "1"), "never fewer than one"


@pytest.mark.parametrize("cores, processes, want", [(None, 11, 4), (None, 2, 2), (12, 11, 4), (8, 11, 4),
                                                    (2, 11, 2), (1, 11, 1)])
def test_the_lane_s_threads_are_held_to_the_profile_s_cores(monkeypatch, cores, processes, want):
    monkeypatch.setattr(cpu_limit, "current", lambda: SimpleNamespace(cores=cores, mode=None))
    monkeypatch.setattr(config, "PARSE_PROCESSES", processes)
    assert parallel.lane_threads() == want


def test_a_pool_child_has_one_thread_and_a_pass_s_child_too(clean_env, monkeypatch):
    monkeypatch.setattr(parallel, "watch_parent", lambda: None)
    monkeypatch.setattr(parallel, "_first_for_the_oom_killer", lambda: None)
    monkeypatch.setattr(os, "nice", lambda n: 0)
    parallel._child_init({})
    assert _env() == ("true", "1")
    os.environ.pop("RAYON_NUM_THREADS")
    parallel._pass_child_init()
    assert _env() == ("true", "1")
    parallel._child_init({}, None, 4)
    assert _env() == ("true", "4"), "the large lane's child: its own number"


def test_the_large_lane_s_child_is_started_with_the_lane_s_threads(monkeypatch):
    """The real executor, a real spawned child: what it reads in its own
    environment before any task."""
    monkeypatch.setattr(cpu_limit, "current", lambda: SimpleNamespace(cores=3, mode=None))
    ex = parallel._lane_executor()
    try:
        assert ex.submit(_env).result(timeout=120) == ("true", "3")
    finally:
        ex.shutdown(wait=False)


def test_no_pool_starts_a_child_without_saying_its_threads():
    """Every executor this module makes names an initializer that sets them:
    a new pool added without one would start one thread a core in each child."""
    import inspect
    import re
    src = inspect.getsource(parallel)
    made = re.findall(r"\binitializer=(\w+)", src)
    assert len(made) >= 6 and set(made) <= {"_child_init", "_pass_child_init"}, made
    assert src.count("ProcessPoolExecutor(max_workers=") + src.count("_Executor(max_workers=") \
        + src.count("_Executor(\n") == len(made), "an executor made without an initializer"


def test_a_large_part_counts_as_its_threads_only_while_it_runs():
    running, done, light = Future(), Future(), Future()
    done.set_result([])
    futures = [(None, running), (None, done), (None, light), (None, [(True, "answered")])]
    weights = {id(running): 4, id(done): 4}
    assert parallel._load(futures, weights) == 4 + 0 + 1 + 0
    assert parallel._load(futures, {}) == parallel._in_flight_count(futures) == 2


def test_the_occupancy_a_large_part_takes_is_given_back(fake_compute, monkeypatch):  # noqa: F811
    """A large part takes `lane_threads()` of the node's occupancy while it
    runs and gives exactly that back: a leak would starve every collection."""
    pool = _Pool()
    monkeypatch.setattr(parallel, "pool", lambda: pool)
    monkeypatch.setattr(parallel, "lane_threads", lambda: 4)
    moves: list[int] = []
    real = parallel._occupy
    monkeypatch.setattr(parallel, "_occupy", lambda name, d: (moves.append(d), real(name, d))[1])
    rows = _rows([1, BIG, 1, 1, BIG, 1])
    assert parallel.compute("chunks", _coll(), rows) == [(True, r["uid"]) for r in rows]
    assert sum(moves) == 0, moves
    assert moves.count(4) == 2 and moves.count(-4) == 2, moves


class _Tok:
    """A tokenizer that batches, recording what each call was handed."""
    padding = None

    def __init__(self):
        self.handed: list[type] = []

    def encode(self, text, add_special_tokens=False):
        ids = text.split()
        return SimpleNamespace(ids=ids, offsets=[(0, len(text))] * len(ids))

    def encode_batch(self, texts, add_special_tokens=False):
        return [self.encode(t) for t in texts]


def test_the_spans_are_cut_as_they_are_encoded_never_listed(monkeypatch):
    """A list of a text's spans is a second copy of the text, alive for the
    whole count — in the child whose peak the large lane's room is reckoned on."""
    seen: list[bool] = []
    real = cl._encode_many

    def spy(tok, texts):
        seen.append(isinstance(texts, (list, tuple)))
        return real(tok, texts)
    monkeypatch.setattr(cl, "_encode_many", spy)
    tok = _Tok()
    text = "a b c d " * 50
    assert cl._count(tok, text) == 200
    assert cl._count_many(tok, [text, "x y", text]) == [200, 2, 200]
    assert len(list(cl._token_ends(tok, text))) == 200
    assert seen == [False, False, False], seen
