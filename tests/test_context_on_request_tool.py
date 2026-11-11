"""tools/measure/context_on_request.py: what a context on request would give
back, counted from the card's per-second record (the brief §2; read-only, no
card). Against a record written here:
  - a model is working in every second one of its collections is served;
  - two collections on one model are one model's seconds;
  - a keep that bridges `gap` idle seconds makes one busy period — one
    creation — of runs no further apart, and holds the context through the
    gap and for `gap` after the period;
  - the GB·h given back are the free seconds times the context's size;
  - a line cut short by a stop is passed over, and the cost of the count does
    not depend on how the seconds are spread (one pass, sorted once).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.measure import context_on_request as tool

T0 = int(datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc).timestamp())
NEMOTRON, QWEN = "vega-nemotron-3-embed-1b", "vega-qwen3-embedding-4b"
MODEL_OF = {"wikidata": NEMOTRON, "normattiva-nemo": NEMOTRON, "gutenberg": QWEN}


def _record(tmp_path, served: dict[int, dict[str, float]]):
    """`served`: second offset -> {collection: seconds held at its gate}."""
    day = tmp_path / "2026-11-10.jsonl"
    with day.open("w", encoding="utf-8") as f:
        for off in range(0, 120):
            row = {"t": T0 + off, "q": 0, "gates": {}, "feed": {}, "done": {}}
            if off in served:
                row["gates"] = {"http://localai:8080": {"running": 1, "waiting": 0,
                                                        "served": served[off]}}
            f.write(json.dumps(row) + "\n")
        f.write('{"t": 17300')                       # a write cut short by a stop
    return tmp_path


def test_a_model_s_seconds_are_those_of_its_collections(tmp_path):
    card = _record(tmp_path, {0: {"wikidata": 0.9}, 1: {"normattiva-nemo": 0.4},
                              2: {"wikidata": 1.0, "gutenberg": 0.2}, 50: {"pubmed": 0.3},
                              60: {"wikidata": 0.0}})
    seconds = tool.working_seconds(tool.read(card, T0, T0 + 120), MODEL_OF)
    assert seconds == {NEMOTRON: {T0, T0 + 1, T0 + 2}, QWEN: {T0 + 2}, "pubmed": {T0 + 50}}


def test_busy_periods_and_what_the_context_leaves_free(tmp_path):
    # nemotron works seconds 0-9, 12-14 and 40-44 of a 120 s window
    served = {s: {"wikidata": 1.0} for s in [*range(0, 10), *range(12, 15), *range(40, 45)]}
    seconds = tool.working_seconds(tool.read(_record(tmp_path, served), T0, T0 + 120), MODEL_OF)
    rows = {r.gap_s: r for r in tool.table(seconds, 120, {NEMOTRON: 2048.0}, gaps=(0, 2, 30))}
    # a keep under a second: three periods, the context held only while working
    assert (rows[0].busy_s, rows[0].periods, rows[0].held_s, rows[0].free_s) == (18, 3, 18, 102)
    # a keep that bridges 2 idle seconds joins the first two runs, and holds 2 s after each
    assert (rows[2].periods, rows[2].held_s) == (2, (15 - 0) + 2 + 5 + 2)
    # one that bridges 30: one period, held from 0 to 44 and 30 s after
    assert (rows[30].periods, rows[30].held_s, rows[30].free_s) == (1, 45 + 30, 120 - 75)
    # 2,048 MiB = 2 GiB free for 102 s
    assert abs(rows[0].gib_h - 2.0 * 102 / 3600) < 1e-9
    assert tool.table(seconds, 120, {}, gaps=(0,))[0].gib_h is None, "no size given, none claimed"


def test_the_context_is_never_held_longer_than_the_window(tmp_path):
    served = {s: {"gutenberg": 1.0} for s in range(100, 120)}
    seconds = tool.working_seconds(tool.read(_record(tmp_path, served), T0, T0 + 120), MODEL_OF)
    row, = tool.table(seconds, 120, {}, gaps=(600,))
    assert (row.periods, row.held_s, row.free_s) == (1, 120, 0)


def test_the_command_prints_one_row_a_model_and_gap(tmp_path, capsys):
    served = {s: {"wikidata": 1.0} for s in range(0, 30)} | {90: {"gutenberg": 0.5}}
    card = _record(tmp_path, served)
    rc = tool.main(["--card-dir", str(card), "--until", str(T0 + 120), "--hours", str(120 / 3600),
                    "--model", f"wikidata={NEMOTRON}", "--model", f"gutenberg={QWEN}",
                    "--context", f"{NEMOTRON}=2221", "--gap", "0", "--gap", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    lines = [line for line in out.splitlines() if line.startswith(f"| vega-")]
    assert len(lines) == 4
    assert f"| {NEMOTRON} | 0 s | 30 s (25.0 %) | 1 | 720 | 25.0 % | 75.0 % | 0.1 |" in out
    assert f"| {QWEN} | 5 s | 1 s (0.8 %) | 1 | 720 | 5.0 % | 95.0 % |  |" in out


def test_an_empty_window_says_so(tmp_path, capsys):
    card = _record(tmp_path, {})
    assert tool.main(["--card-dir", str(card), "--until", str(T0 + 120), "--hours", "0.01"]) == 1
    assert "no encoder gate served anything" in capsys.readouterr().err
