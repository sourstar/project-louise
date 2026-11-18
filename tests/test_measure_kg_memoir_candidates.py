"""tools/measure/kg_memoir_title_candidates.py: which titles are candidates, and their category."""
from __future__ import annotations

import csv
from pathlib import Path

from tools.measure import kg_memoir_title_candidates as m

HEAD = ["Text#", "Type", "Issued", "Title", "Language", "Authors", "Subjects", "LoCC", "Bookshelves"]


def _catalog(tmp_path: Path, rows: list[list[str]]) -> Path:
    p = tmp_path / "pg_catalog.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        for r in rows:
            w.writerow(r)
    return p


def test_candidates_and_categories(tmp_path: Path) -> None:
    cat = _catalog(tmp_path, [
        ["1", "Text", "", "Ricordi di un garibaldino", "it", "Elia, Augusto", "Italy -- History -- 1815-1870; Elia, Augusto, 1829-1919", "DG", ""],
        ["2", "Text", "", "Memorie di Giuda", "it", "X", "Judas Iscariot -- Fiction", "PQ", ""],
        ["3", "Text", "", "Libro serio", "it", "Y", "Biography", "CT", ""],                       # already a memoir by subjects
        ["4", "Text", "", "Ricordi di Londra", "it", "De Amicis", "London (England) -- Description and travel", "DA", ""],
        ["5", "Text", "", "Le cronache italiane", "it", "Balzani", "Italy -- History -- Historiography", "DG", ""],
        ["6", "Text", "", "Ricordi", "fr", "Z", "", "DC", ""],                                     # another language
        ["7", "Sound", "", "Ricordi", "it", "Z", "", "DG", ""],                                    # not a text
        ["8", "Text", "", "Ricordi del 1870", "it", "De Amicis", "Italy -- History -- 1849-1870 -- Sources", "DG", ""],
    ])
    got = {c.ebook: c for c in m.candidates(cat, "it")}
    assert set(got) == {1, 2, 4, 5, 8}
    assert got[1].category == "about a person" and got[1].suggestion == "yes"
    assert got[2].category == "fiction, poetry or drama" and got[2].suggestion == "no"
    assert got[4].category == "travel account" and got[4].suggestion == "yes"
    assert got[5].category == "history or criticism" and got[5].suggestion == "no"
    assert got[8].category == "eyewitness or source" and got[8].suggestion == "yes"


def test_decided_books_are_not_proposed_again(tmp_path: Path) -> None:
    cat = _catalog(tmp_path, [["1", "Text", "", "Ricordi", "it", "A", "", "DG", ""],
                              ["2", "Text", "", "Memorie", "it", "B", "", "DG", ""]])
    dec = tmp_path / "decided.toml"
    dec.write_text('decided = "2026-11-18"\nyes = [1]\nno = []\n', encoding="utf-8")
    assert [c.ebook for c in m.candidates(cat, "it", m.decided_ebooks([dec]))] == [2]
    assert m.candidates(cat, "it")[0].category == "review"


def test_periodical_before_travel(tmp_path: Path) -> None:
    cat = _catalog(tmp_path, [["1", "Text", "", "The Journal of the Royal Society, Vol. 3", "en", "A",
                               "Voyages and travels -- Periodicals", "G", ""]])
    assert m.candidates(cat, "en")[0].category == "periodical"


def test_a_volume_number_is_not_a_periodical(tmp_path: Path) -> None:
    cat = _catalog(tmp_path, [["1", "Text", "", "Memoirs of the Reign of King George the Third, Volume 1 (of 4)", "en", "W",
                               "Great Britain -- History -- George III, 1760-1820", "DA", ""],
                              ["2", "Text", "", "Chambers's Journal of Popular Literature, No. 702", "en", "C", "", "AP", ""]])
    got = {c.ebook: c.category for c in m.candidates(cat, "en")}
    assert got == {1: "review", 2: "periodical"}
