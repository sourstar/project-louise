"""DEGRADED UNITS (the operator's rule, 2026-11-28: "keep the record, mark
it as degraded, mark the parts as missing and carry on. Upon future pollings
of the source those degraded records are checked if their records show an
update"). A unit still not whole after its one ask keeps its record, is
marked degraded with why, leaves the asked-first list, and is asked again
only when the origin shows a change."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.acquire import cellar, eurlex_dump
from app.acquire.spec import RemoteFile
from app.plugins.eurlex import rdf
from tests.acquire.test_eurlex_acquisition_review import _reform_corpus
from tests.acquire.test_eurlex_dump import FORMEX, serial  # noqa: F401

URL = "https://cellar.test"


def _ledger(corpus, entry):
    rec = json.loads((corpus / eurlex_dump.INCOMPLETE).read_text())
    rec["it"][FORMEX[0]] = entry
    (corpus / eurlex_dump.INCOMPLETE).write_text(json.dumps(rec))


def _one_ask(corpus, monkeypatch, state):
    monkeypatch.setattr(cellar, "fetch_unit_from_tree",
                        lambda *a, **k: cellar.Fetched(files={rdf.TREE_FILE: 1}, moved=1, tree_from="cellar"))
    monkeypatch.setattr(cellar.TreeUnits, "_bulk_of", lambda self, *a: (None, None))
    monkeypatch.setattr(eurlex_dump, "judge_unit", lambda *a: state)
    rel = cellar.rel_path("it", FORMEX[0])
    f = RemoteFile(rel_path=rel, mtime=None, remote=cellar.encode(FORMEX[0], "it"))
    cellar.TreeUnits().fetch(SimpleNamespace(url=URL), f, corpus / rel)


def test_each_why_is_marked_degraded_after_one_ask_and_leaves_the_list(tmp_path, serial, monkeypatch):  # noqa: F811
    for why in ("unreadable", "lacks 31972R1473.ita.fmx4.zip", "not its name: in each other's place: a, b"):
        corpus, unit = _reform_corpus(tmp_path / why[:6])
        _ledger(corpus, {"held": [0, 0, 0], "in_force": "true", "lacks": [], "reform": True, "tried": None})
        _one_ask(corpus, monkeypatch, {"whole": False, "why": why, "lacks": [], "origin_modified": "2026-11-20"})
        (d,) = eurlex_dump.degraded(corpus)
        assert d["degraded"] and d["why"] == why and d["origin_modified"] == "2026-11-20" and d["at"]
        assert eurlex_dump.incomplete_due(corpus, {"it"}) == [], "left the asked-first list"


def test_a_whole_unit_after_its_ask_is_not_degraded(tmp_path, serial, monkeypatch):  # noqa: F811
    corpus, unit = _reform_corpus(tmp_path)
    _one_ask(corpus, monkeypatch, {"whole": True, "why": None, "lacks": [], "origin_modified": "x"})
    assert eurlex_dump.degraded(corpus) == []


def test_a_degraded_unit_is_asked_again_only_on_a_newer_date(tmp_path, serial, monkeypatch):  # noqa: F811
    corpus, unit = _reform_corpus(tmp_path)
    _one_ask(corpus, monkeypatch, {"whole": False, "why": "lacks x", "lacks": ["x"], "origin_modified": "y"})
    units = cellar.TreeUnits()
    units._read_degraded(corpus)
    monkeypatch.setattr(cellar.TreeUnits, "_reform", lambda self, *a: True)      # it still lacks its package
    asked = []
    monkeypatch.setattr(cellar, "fetch_unit_from_tree",
                        lambda *a, **k: asked.append(1) or cellar.Fetched(files={}, moved=1, tree_from="cellar"))
    rel = cellar.rel_path("it", FORMEX[0])
    from app.plugins.eurlex import from_tree
    from app.plugins.eurlex.source import LANGUAGES
    held = cellar._epoch(from_tree.notice(rdf.loads((corpus / rel).read_bytes()), LANGUAGES["it"],
                                          FORMEX[0]).modified)
    units.fetch(SimpleNamespace(url=URL), RemoteFile(rel_path=rel, mtime=held, remote=cellar.encode(FORMEX[0], "it")),
                corpus / rel)
    assert asked == [], "no change at the origin: not asked on a plain pass"
    units.fetch(SimpleNamespace(url=URL),
                RemoteFile(rel_path=rel, mtime=held + 1, remote=cellar.encode(FORMEX[0], "it")), corpus / rel)
    assert asked == [1], "the origin shows a change: asked again"


def test_the_parse_says_files_hold_another_act_s_bytes():
    from app.plugins.eurlex import source as src
    assert "files-not-their-names" in src.ISSUES
    assert src._not_their_names({"x.txt": b"plain"}, FORMEX[0]) == [], "no Formex wrapper: nothing to judge"
