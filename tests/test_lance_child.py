"""The Lance calls that keep the index files they read open run in a child
process that ends with them (app/lance_child.py; München, 2026-11-12: the
worker pinned 52 index files, 38 of them deleted, 4.5 GiB on ssd3, more at
every refresh)."""
import os
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pytest

from app import config, lance_child, vector_index
from tests.test_vector_index import _fill, collection  # noqa: F401 — the fixture


def _held(root) -> tuple[int, int]:
    """(index files, deleted files) under `root` this process holds open."""
    root = str(Path(root).resolve())
    index = deleted = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        if target.startswith(root):
            index += "/_indices/" in target
            deleted += target.endswith("(deleted)")
    return index, deleted


def _refreshes(collection, rounds: int) -> None:
    """The maintainer's cycle: new rows, a refresh (a delta, the deltas
    merged past the cap), the prune that deletes what was merged away."""
    _fill(collection, 600)
    vector_index.build(collection, index_type="ivf_pq", partitions=4)
    for i in range(rounds):
        _fill(collection, 60, seed=10 + i, start=1000 + 100 * i)
        out = vector_index.refresh(collection)
        assert out["refreshed"] and out["after"]["unindexed_rows"] == 0
        vector_index.prune(collection)


@pytest.fixture
def cycle(collection, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "VECTOR_INDEX_MAX_INDICES", 2)
    monkeypatch.setattr(config, "VECTOR_INDEX_KEEP_VERSIONS_H", 0)
    # the disk policy is not under test (this machine may be short of room)
    monkeypatch.setattr(vector_index, "headroom_ok", lambda *a, **k: True)
    return collection


def test_in_process_lance_keeps_the_index_files_it_read_which_is_why_the_child_exists(cycle):
    """The witness: the calls run here (LANCE_CHILD=0) leave this process
    holding index files, deleted ones among them, after the prune. If this
    starts failing, Lance closes its readers: LANCE_CHILD=0 is then the same
    behaviour without the child, and can become the default."""
    _refreshes(cycle, 3)
    index, deleted = _held(cycle.lance_dir)
    assert index > 0 and deleted > 0, (index, deleted)


def test_through_the_child_this_process_holds_no_index_file(cycle, monkeypatch):
    monkeypatch.setattr(config, "LANCE_CHILD", True)
    _refreshes(cycle, 3)
    assert _held(cycle.lance_dir) == (0, 0)
    # and the compactions, which read the index too
    st = vector_index.compact(cycle)
    assert st["compacted"], st
    assert _held(cycle.lance_dir) == (0, 0)


class _Ds:
    """A dataset as the callers hold one."""
    uri = "/nowhere/chunks.lance"

    def __init__(self):
        self.asked = []
        outer = self

        class Opt:
            def optimize_indices(self, **kw):
                outer.asked.append(("optimize_indices", kw))

            def compact_files(self, **kw):
                outer.asked.append(("compact_files", kw))
                return "metrics"
        self.optimize = Opt()


class _Ex:
    """A ProcessPoolExecutor stand-in: what `how` says happens."""
    def __init__(self, how):
        self.how = how

    def __call__(self, *a, **k):
        if self.how == "no process":
            raise OSError(24, "Too many open files")
        return self

    def submit(self, *a, **k):
        f = Future()
        f.set_exception(BrokenProcessPool("a child terminated abruptly"))
        return f

    def shutdown(self, **k):
        pass


def test_a_child_the_kernel_kills_is_a_deferral(monkeypatch):
    monkeypatch.setattr(config, "LANCE_CHILD", True)
    monkeypatch.setattr(lance_child, "ProcessPoolExecutor", _Ex("killed"))
    ds = _Ds()
    with pytest.raises(lance_child.ChildLost, match="died before it answered"):
        lance_child.optimize_indices(ds, num_indices_to_merge=0)
    assert ds.asked == [], "nothing redone here: the caller defers"


def test_no_child_to_be_had_runs_the_call_here_and_switched_off_never_starts_one(monkeypatch):
    monkeypatch.setattr(config, "LANCE_CHILD", True)
    monkeypatch.setattr(lance_child, "ProcessPoolExecutor", _Ex("no process"))
    ds = _Ds()
    assert lance_child.compact_files(ds, materialize_deletions=True) == "metrics"
    assert ds.asked == [("compact_files", {"materialize_deletions": True})]
    monkeypatch.setattr(config, "LANCE_CHILD", False)
    monkeypatch.setattr(lance_child, "ProcessPoolExecutor", None)       # never touched
    lance_child.optimize_indices(ds, num_indices_to_merge=2)
    assert ds.asked[-1] == ("optimize_indices", {"num_indices_to_merge": 2})


def test_the_child_opens_the_dataset_with_the_worker_s_cache_bounds(monkeypatch):
    """München's review, 2026-11-12: without them the child takes Lance's
    defaults (6 GiB of index, 1 GiB of metadata) in the worker's cgroup."""
    import lance
    opened = {}

    class Ds:
        class optimize:
            @staticmethod
            def optimize_indices(**kw):
                opened["kw"] = kw
    monkeypatch.setattr(lance, "dataset", lambda uri, **kw: opened.update(uri=uri, **kw) or Ds())
    lance_child._run("optimize_indices", "/x/chunks.lance", {"num_indices_to_merge": 0}, (1 << 30, 1 << 29))
    assert opened == {"uri": "/x/chunks.lance", "index_cache_size_bytes": 1 << 30,
                      "metadata_cache_size_bytes": 1 << 29, "kw": {"num_indices_to_merge": 0}}
