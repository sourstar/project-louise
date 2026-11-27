"""tools/measure/translation_waits.py: the language stage read off a query trace,
overlaps found, the translated questions split into alone and overlapped."""
import json

from tools.measure import translation_waits as tw


def _line(started: str, languages: float | None, query: str = "q") -> dict:
    line = {"kind": "search", "query": query, "started": started, "ms": 1000.0}
    if languages is not None:
        line["timings"] = {"languages": languages, "fts": 3.0}
    return line


def test_a_stage_is_read_and_a_line_without_one_is_left_out():
    rows = tw.stages([_line("2026-11-27T20:00:00.000Z", 900.0), _line("2026-11-27T20:00:05.000Z", None),
                      {"kind": "chat", "started": None, "timings": {"languages": 5}}])
    assert len(rows) == 1 and rows[0]["ms"] == 900.0
    assert abs(rows[0]["end"] - rows[0]["start"] - 0.9) < 1e-6


def test_overlaps_are_found_both_ways_and_touching_is_not_one():
    rows = tw.mark_overlaps(tw.stages([
        _line("2026-11-27T20:00:00.000Z", 2000.0, "a"),      # 0.0 - 2.0
        _line("2026-11-27T20:00:01.000Z", 500.0, "b"),       # 1.0 - 1.5, inside a
        _line("2026-11-27T20:00:02.000Z", 300.0, "c"),       # 2.0 - 2.3, touches a's end
        _line("2026-11-27T20:00:10.000Z", 800.0, "d")]))     # alone
    got = {r["query"]: r["overlapped"] for r in rows}
    assert got == {"a": True, "b": True, "c": False, "d": False}


def test_the_summary_splits_the_translated_questions():
    rows = tw.mark_overlaps(tw.stages([
        _line("2026-11-27T20:00:00.000Z", 3000.0, "a"), _line("2026-11-27T20:00:01.000Z", 5000.0, "b"),
        _line("2026-11-27T20:01:00.000Z", 900.0, "c"), _line("2026-11-27T20:02:00.000Z", 4.0, "cached")]))
    s = tw.summary(rows, 200.0)
    assert s["questions"] == 4 and s["translated"] == 3 and s["all_overlapped"] == 2
    assert s["translated_alone"] == {"questions": 1, "p50_ms": 900.0, "p95_ms": 900.0, "max_ms": 900.0}
    assert s["translated_overlapped"]["questions"] == 2 and s["translated_overlapped"]["max_ms"] == 5000.0


def test_the_files_are_read_as_json_lines_and_a_cut_line_is_skipped(tmp_path):
    p = tmp_path / "2026-11-27.jsonl"
    good = json.dumps(_line("2026-11-27T20:00:00.000Z", 700.0, "un titolo con a capo"), ensure_ascii=False)
    p.write_text(good + "\n" + '{"kind": "search", "started": "2026' + "\n", encoding="utf-8")
    lines = tw.read([p])
    assert len(lines) == 1 and lines[0]["query"] == "un titolo con a capo"
    assert tw.main([str(p), "--json"]) == 0
