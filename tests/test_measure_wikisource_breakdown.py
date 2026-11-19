"""tools/measure/wikisource_fetch_breakdown.py — the per-category fetch
measurement behind docs/notes/wikisource-fetch-breakdown.md: the bucket a
work's categories put it in, and the breakdown counting a scan file once per
category however many works transclude it."""
import json

from tools.measure import wikisource_fetch_breakdown as wfb


def test_the_first_matching_bucket_wins_and_maintenance_categories_are_no_subject():
    assert wfb.bucket_of(["Drama_film", "Silent_film"]) == "film"            # a film is no play
    assert wfb.bucket_of(["Romanzi", "Storia"]) == "fiction"                 # a historical novel is fiction
    assert wfb.bucket_of(["Memorie", "Storia_d'Italia"]) == "memoir"
    assert wfb.bucket_of(["Testi_in_cui_è_citato_il_testo_Viaggio_in_Italia"]) == "other"
    assert wfb.bucket_of(["Weekly_List,_National_Register_of_Historic_Places"]) == "law and official"
    assert wfb.bucket_of(["Biblioteca_comunale_di_Trento"]) == "other"       # a library is no Bible
    assert wfb.bucket_of(["Bibbia"]) == "religion"
    assert wfb.bucket_of([]) == "other"


def test_a_scan_shared_by_two_works_of_one_category_counts_once(tmp_path):
    works = [{"title": "A", "categories": ["Diari"], "own_bytes": 10, "files": ["f.djvu"]},
             {"title": "B", "categories": ["Lettere"], "own_bytes": 5, "files": ["f.djvu", "g.pdf"]},
             {"title": "C", "categories": ["Romanzi"], "own_bytes": 1, "files": ["f.djvu"]}]
    (tmp_path / "it.works.jsonl").write_text("".join(json.dumps(w) + "\n" for w in works), encoding="utf-8")
    (tmp_path / "it.files.json").write_text(json.dumps({
        "f.djvu": {"page_bytes": 100, "scan_bytes": 1000}, "g.pdf": {"page_bytes": 50, "scan_bytes": None}}))
    res = wfb.breakdown("it", tmp_path)
    assert res["memoir"] == {"works": 2, "text_bytes": 15 + 150, "scan_files": 2, "scan_bytes": 1000,
                             "scan_unknown": 1, "top": ["Diari", "Lettere"]}
    assert res["fiction"]["scan_files"] == 1 and res["fiction"]["text_bytes"] == 101
