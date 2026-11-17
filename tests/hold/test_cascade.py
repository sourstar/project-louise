"""The gate for deleting the origin's old run (app/hold/cascade.py;
docs/notes/collection-updates.md §3): the snapshot built from the next run
PUBLISHED (its signatures' sidecar), and the annotations cascade it
triggers STARTED (each declaring collection's `state/woken.json`), never
before; vacuously true where none declares it."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.hold import cascade

# ---------------------------------------------------------------- the old run's gate

def _codex(cid="sha256:" + "a" * 64):
    return SimpleNamespace(collection_id=cid)


def _published(root, name="wikipedia", sid="2026-11", sig=True):
    d = root / name / "shard" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text("{}")
    if sig:
        (d / f"{sid}.sig.json").write_text("{}")


def _woken(root, name, **doc):
    p = root / name / "state" / "woken.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc))


def test_the_old_run_waits_for_the_signature(tmp_path, monkeypatch):
    monkeypatch.setattr(cascade, "annotators", lambda root, cid: [])
    _published(tmp_path, sig=False)
    g = cascade.old_run_gate(tmp_path, "wikipedia", _codex(), "2026-11")
    assert not g.open and "to be published" in g.says
    _published(tmp_path)
    g = cascade.old_run_gate(tmp_path, "wikipedia", _codex(), "2026-11")
    assert g.open and g.published, "vacuously true when no annotations collection declares it"


@pytest.mark.parametrize("doc,started", [
    ({"sources": {"C": "2026-11"}, "state": "running"}, True),
    ({"sources": {"C": "2026-11"}, "state": "done"}, True),
    ({"sources": {"C": "2026-11"}, "state": "failed"}, True),
    ({"sources": {"C": "2026-11"}, "state": "waiting"}, False),
    ({"sources": {"C": "2026-10"}, "state": "done"}, False),
    ({"sources": {"X": "2026-11"}, "state": "done"}, False),
    ({"state": "done"}, False),
])
def test_the_cascade_has_started_only_from_this_snapshot(tmp_path, monkeypatch, doc, started):
    monkeypatch.setattr(cascade, "annotators", lambda root, cid: ["versions"])
    _published(tmp_path)
    _woken(tmp_path, "versions", **doc)
    g = cascade.old_run_gate(tmp_path, "wikipedia", _codex("C"), "2026-11")
    assert g.open is started
    if not started:
        assert g.waiting == ("versions",) and "versions not woken yet" in g.says


def test_the_annotators_are_those_whose_codex_names_this_collection(tmp_path, monkeypatch):
    from app.hold import collection as col
    held = [("versions", SimpleNamespace(annotates=[SimpleNamespace(collection_id="C")])),
            ("other", SimpleNamespace(annotates=[SimpleNamespace(collection_id="D")])),
            ("two", SimpleNamespace(annotates=(SimpleNamespace(collection_id="D"),
                                               SimpleNamespace(collection_id="C")))),
            ("wikipedia", SimpleNamespace(annotates=None))]
    monkeypatch.setattr(col, "_held", lambda root: held)
    assert cascade.annotators(tmp_path, "C") == ["two", "versions"]


