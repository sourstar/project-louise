"""The vector store's caches are bounded per PROCESS (app/embeddings.py
`lance_session`; docs/notes/STATUS.md, 2026-11-08).

The defect: every LanceDB connection the worker kept — one per collection
and one per part of a split collection — made its own Lance Session at the
library's defaults, 6 GiB of index cache and 1 GiB of metadata cache, and
the metadata cache keeps a manifest per version committed, so it grew with
every write. The worker reached ~16.8 GB anon RSS and was OOM-killed every
3–6 minutes. Measured through this module's own write path: 380 → 1,665 MB
RSS over 1,500 batches on three 100k-row tables; with one bounded Session,
flat. Lance's own accounting (`Session.size_bytes`) is what these tests
read: its caches are native memory, which tracemalloc cannot see.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

lancedb = pytest.importorskip("lancedb")

from app import collections as collections_mod
from app import config, embeddings, vector_parts, worker

DIM = 16


def _coll(tmp_path: Path, name: str) -> collections_mod.Collection:
    d = tmp_path / name
    return collections_mod.Collection(
        name=name, plugin="pubmed", profile=None, embed_mode_default=None,
        corpus_dir=d / "corpus", manifest_path=d / "state" / "manifest.db",
        fts_dir=d / "fts", lance_dir=d / "lancedb")


def _rows(books, rng) -> list[dict]:
    return [{"id": f"pubmed:{b}:0:1", "book_id": int(b), "lang": "en", "seq": 0, "chapter": "",
             "text": f"the chunk of {b} " * 8, "vector": rng.standard_normal(DIM).astype(np.float16)}
            for b in books]


ROWS = 4000


def _tables(tmp_path, rng, names=("alpha", "beta")) -> list:
    colls = []
    for name in names:
        c = _coll(tmp_path, name)
        embeddings.write_pending([embeddings.PendingDoc(0, _rows(range(ROWS), rng), False, 0)], c)
        colls.append(c)
    return colls


def _work(colls, rng, ops: int, sample) -> None:
    """What the worker does to its vector tables all day: the embed stage's
    read of a batch's held rows and its batched delete-then-insert, and the
    deletion signal's withdraw — each commit a new table version."""
    for k in range(ops):
        c = colls[k % len(colls)]
        books = [int(b) for b in rng.integers(0, ROWS, 8)]
        embeddings.held_chunks(c, books)
        embeddings.write_pending([embeddings.PendingDoc(b, _rows([b], rng), True, 0) for b in books], c)
        if k % 8 == 0:
            embeddings.delete_books([int(b) for b in rng.integers(0, ROWS, 20)], c)
        sample()


@pytest.fixture
def fresh_session(monkeypatch):
    """A process Session made anew under the test's budgets; the previous
    one comes back after (monkeypatch)."""
    monkeypatch.setattr(embeddings, "_session", None)
    monkeypatch.setattr(embeddings, "_session_budget", embeddings._session_budget)

    def make(index: int, metadata: int):
        monkeypatch.setattr(config, "LANCE_INDEX_CACHE_BYTES", index)
        monkeypatch.setattr(config, "LANCE_METADATA_CACHE_BYTES", metadata)
        monkeypatch.setattr(embeddings, "_session", None)
        return embeddings.lance_session()
    return make


def test_every_connection_shares_the_one_bounded_session(tmp_path, monkeypatch, fresh_session):
    """Two collections and a part of a split one: one Session, made with
    the configured budgets — never a connection with a Session of its own."""
    made: list[dict] = []
    real_session = lancedb.Session
    monkeypatch.setattr(embeddings.lancedb, "Session",
                        lambda **kw: made.append(kw) or real_session(**kw))
    session = fresh_session(3 << 20, 1 << 20)
    assert made == [{"index_cache_size_bytes": 3 << 20, "metadata_cache_size_bytes": 1 << 20}]
    seen: list = []
    monkeypatch.setattr(embeddings.lancedb, "connect",
                        lambda uri, **kw: seen.append((uri, kw.get("session"))) or object())
    alpha, beta = _coll(tmp_path, "alpha"), _coll(tmp_path, "beta")
    part = vector_parts.view(_coll(tmp_path, "gamma"), "en", key="lang")
    for c in (alpha, beta, part, alpha):
        embeddings._conn(c)
    assert [uri for uri, _ in seen] == [str(alpha.lance_dir), str(beta.lance_dir), str(part.lance_dir)]
    assert all(s is session for _, s in seen), "every connection takes the process's Session"
    assert embeddings.lance_session() is session and len(made) == 1


def test_the_caches_stay_within_their_budget_whatever_the_commits(tmp_path, fresh_session):
    """The regression: the same work, a bounded Session's caches stay under
    their budget (Lance's own count), and the Session the connections made
    for themselves before grows past it with the commits. `size_bytes` is
    sampled after every batch; its peak is asserted."""
    budget = (1 << 20, 1 << 20)
    session = fresh_session(*budget)
    rng = np.random.default_rng(5)
    colls = _tables(tmp_path / "bounded", rng)
    peak = [0]
    _work(colls, rng, 100, lambda: peak.__setitem__(0, max(peak[0], session.size_bytes)))
    stats = embeddings.lance_cache_stats()
    assert stats["budget"] == sum(budget) and stats["bytes"] == session.size_bytes
    assert session.approx_num_items > 0, "the work went through the process's Session"
    assert peak[0] <= sum(budget), f"peak {peak[0]} bytes over the {sum(budget)}-byte budget"

    # the status quo, on the same work: a Session at Lance's defaults (what
    # each connection made for itself) keeps what the commits leave behind
    unbounded = lancedb.Session.default()
    embeddings._dbs.clear()
    embeddings._session = unbounded
    colls = _tables(tmp_path / "default", rng)
    grown = [0]
    _work(colls, rng, 100, lambda: grown.__setitem__(0, max(grown[0], unbounded.size_bytes)))
    assert grown[0] > 2 * sum(budget), \
        f"the default Session held {grown[0]} bytes: the workload no longer shows the growth"
    embeddings._dbs.clear()


def test_the_worker_reports_what_the_caches_hold():
    """Published by the reporter (`_log_shares`), never the working thread."""
    assert worker.lance_cache_line(None) is None
    line = worker.lance_cache_line({"bytes": 3 << 20, "items": 41, "budget": 1280 << 20})
    assert line == "vector store caches (this process) — 3.0 of 1280 MiB, 41 items"
