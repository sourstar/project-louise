"""tools/measure/lc_heading_check.py, offline: the main heading and each subdivision must resolve."""
from __future__ import annotations

import pytest

from tools.measure import lc_heading_check as m

KNOWN = {("subjects", "Artists"), ("subjects", "Biography"), ("names", "United States"),
         ("names", "Rosen, Erwin, 1876-1923"), ("subjects", "Generals")}


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "_label", lambda scheme, label: f"uri:{label}" if (scheme, label) in KNOWN else "")
    monkeypatch.setattr(m, "_suggest", lambda label: "Rosen, Erwin, 1876-1923" if label.startswith("Rosen") else "")


def test_a_known_heading_with_subdivisions_is_ok() -> None:
    assert m.check("Generals -- United States -- Biography")["status"] == "ok"
    assert m.check("Artists -- Biography")["main_found_in"] == "subjects"


def test_a_name_is_found_in_names_and_an_unknown_one_gets_a_suggestion() -> None:
    assert m.check("Rosen, Erwin, 1876-1923")["main_found_in"] == "names"
    r = m.check("Rosen, Erwin")
    assert r["status"] == "check" and r["name_suggestion"] == "Rosen, Erwin, 1876-1923"


def test_an_invented_subdivision_is_reported() -> None:
    r = m.check("Artists -- Lives")
    assert r["status"] == "check" and r["subdivisions_not_found"] == "Lives"


def test_national_personal_narratives_are_a_pattern() -> None:
    assert m.check("Generals -- Personal narratives, British")["status"] == "ok"
    assert m.check("Generals -- Personal narratives, british stuff")["status"] == "check"
