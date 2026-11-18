"""tools/measure/reading_year_rules.py: the rule each record gets, counted."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app import collections as collections_mod
from tools.measure import reading_known_items, reading_year_rules as yr


def test_rules_are_counted_with_the_rejected_centuries(monkeypatch, capsys):
    recs = [{"uid": "g:1", "lang": "it", "title": "Commedia", "date": "1880",
             "extra": {"marc": {"520": ["written in the early 14th century"]},
                       "creators_detail": [{"role": "aut", "birth": "1265", "death": "1321"}]}},
            {"uid": "g:2", "lang": "it", "title": "Storia", "date": "1840",
             "extra": {"marc": {"520": ["written in the early 19th century"]},
                       "creators_detail": [{"role": "aut", "birth": "1676", "death": "1748"}]}},
            {"uid": "g:3", "lang": "it", "title": "Senza", "extra": {}},
            {"uid": "g:4", "lang": "en", "title": "English", "extra": {}}]
    monkeypatch.setattr(reading_known_items, "records_of", lambda name, rng: iter(recs))
    monkeypatch.setattr(collections_mod, "by_name", lambda n: SimpleNamespace(name=n, plugin="gutenberg"))
    yr.main(["--collection", "g"])
    got = json.loads(capsys.readouterr().out)
    assert got["records"] == 3
    assert got["rules"] == {"its summary's century and its author's dates": 1,
                            "dates (century rejected)": 1, "none": 1}
    assert got["century_forms"] == {"early Nth century": 2}
