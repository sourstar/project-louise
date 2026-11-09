"""The text leg searches its language groups and their variants AT ONCE
(München, 2026-11-09: wikipedia's ten groups one after the other cost
13.2 s a variant, 19-25 s an Ask) — and the fusion sees exactly what the
serial loop gave it (invariant 3)."""
from __future__ import annotations

import threading
import time

from app import collections as collections_mod, db, fts, language_plan, llm, retrieval

GROUPS = ["de", "en", "fr", "it"]


def _run(monkeypatch, threads: int) -> tuple[list, float, int]:
    c = collections_mod.default_collection()
    monkeypatch.setattr(retrieval, "FTS_THREADS", threads)
    monkeypatch.setattr(fts, "existing_groups", lambda collection=None: list(GROUPS))
    monkeypatch.setattr(fts, "names_capable", lambda collection: False)
    monkeypatch.setattr(fts, "group_languages", lambda group, collection=None: [group])
    monkeypatch.setattr(language_plan.LanguagePlan, "searches", lambda self, g: True)
    monkeypatch.setattr(language_plan.LanguagePlan, "variants",
                        lambda self, g, held, main=None: [self.core, f"{self.core} ({g})"])
    monkeypatch.setattr(language_plan.LanguagePlan, "lang_filter", lambda self, g: None)
    monkeypatch.setattr(llm, "language_restriction", lambda q: ([], q))
    live, peak, lock = [0], [0], threading.Lock()

    def search_records(group, q, limit, collection=None, langs=None, **kw):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.15)
        with lock:
            live[0] -= 1
        n = GROUPS.index(group) * 10 + (0 if "(" not in q else 5)
        return [{"score": 1.0 / (1 + i), "book_id": n + i, "chunk_id": f"x:{n + i}:0:1", "chapter": group,
                 "lang": group, "text": q, "source": f"fts:{group}"} for i in range(3)]
    monkeypatch.setattr(fts, "search_records", search_records)
    t0 = time.monotonic()
    out = retrieval._search_one_collection(db.connect(c), c, "Cesare", 5, "fts", False, set(),
                                           language_plan.plan("Cesare", [c], False))
    return [retrieval.record_of(m) for m in out["merged"]], time.monotonic() - t0, peak[0]


def test_the_groups_and_variants_run_at_once_and_fuse_as_the_serial_loop_did(isolated_config, monkeypatch):
    serial, t_serial, peak_serial = _run(monkeypatch, 1)
    parallel, t_parallel, peak_parallel = _run(monkeypatch, 8)
    assert peak_serial == 1 and peak_parallel == 8, "four groups x two variants at once"
    assert parallel == serial, "the same lists in the same order: the same fusion"
    assert t_parallel < t_serial / 3, (t_parallel, t_serial)
