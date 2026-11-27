"""tools/measure/jsonl.py: a JSON Lines file's lines, cut at "\n" alone."""
import json

from tools.measure import jsonl


def test_a_separator_inside_a_string_does_not_cut_the_line():
    rows = [{"t": "a b c\x85d\x0be\x0cf\x1cg"}, {"t": "next"}]
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    assert len(text.splitlines()) > 2, "what str.splitlines() does to it"
    assert [json.loads(line) for line in jsonl.lines(text)] == rows


def test_it_reads_as_splitlines_does_where_splitlines_was_right():
    for text in ("", "a", "a\n", "a\nb", "a\nb\n", "a\r\nb\r\n", "\n", "a\n\nb\n"):
        assert jsonl.lines(text) == text.splitlines(), repr(text)
