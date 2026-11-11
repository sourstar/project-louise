"""What a context on request would give back, read from the card's record.

docs/notes/idle-context-brief.md §2: "What does a keep under a second release,
and how often does it create? … the card record (app/rage/card_record.py, per
second; no card time): per encoder over 24 h, the GB·h given back and the
creations a day, a model counted running in every second it worked."

READ-ONLY, no card, no model: it reads `<root>/state/card/<UTC date>.jsonl`
(one line a second: which collection each encoder gate served) and counts,
per MODEL:
  - the seconds it worked, and their share of the window;
  - its BUSY PERIODS — runs of working seconds no more than `gap` idle
    seconds apart. With a keep that bridges gaps up to `gap`, each period
    costs ONE creation of the context;
  - the time its context would be free, and that time × the context's size:
    the GB·h given back.

The record has one line a second, so it cannot see a gap shorter than that:
the row for `gap = 0` counts a creation at every second that follows an idle
one, and what happens INSIDE a working second — the gaps a keep of 20 ms
must bridge — is the wrapper's to count (the brief §2, the row above this
one). A lower bound on the creations, then, and an exact count of the idle
time at one-second grain.

    python -m tools.measure.context_on_request \\
        --card-dir /data/state/card --hours 24 \\
        --model wikidata=vega-nemotron-3-embed-1b \\
        --model normattiva-nemo=vega-nemotron-3-embed-1b \\
        --model pubmed=vega-medcpt-article --model gutenberg=vega-qwen3-embedding-4b \\
        --context vega-nemotron-3-embed-1b=2221 --context vega-qwen3-embedding-4b=1882

`--model collection=model` says which model serves a collection's corpus
work (a collection not named is reported under its own name); `--context
model=MiB` is the context's size (`size_vram` less the weights file, the
brief §6), without which the GB·h column is empty. Queries are not in this
record (only `q`, a person's query holding the claim): the figures are the
corpus encoders'.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

#: the idle seconds a keep is taken to bridge, one row each
GAPS_S = (0, 1, 2, 5, 10, 60, 600)
MIB = 1024 * 1024


@dataclass(frozen=True)
class Row:
    model: str
    gap_s: int
    busy_s: int          # seconds the model worked
    periods: int         # busy periods: creations, with a keep that bridges `gap_s`
    held_s: int          # seconds the context exists: the busy ones and the bridged gaps
    free_s: int          # the window less `held_s`
    gib_h: float | None  # free_s x the context's size


def read(card_dir: Path, since: float, until: float) -> Iterator[dict]:
    """The record's lines with `since <= t < until`, oldest first; a line that
    is not JSON (a write cut short by a stop) is passed over."""
    day = datetime.fromtimestamp(since, timezone.utc).date()
    last = datetime.fromtimestamp(until, timezone.utc).date()
    while day <= last:
        path = card_dir / f"{day.isoformat()}.jsonl"
        if path.is_file():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and since <= row.get("t", -1) < until:
                        yield row
        day += timedelta(days=1)


def working_seconds(rows: Iterable[dict], model_of: dict[str, str]) -> dict[str, set[int]]:
    """model -> the seconds in which one of its collections was served at an
    encoder gate (`gates[url].served[collection] > 0`)."""
    out: dict[str, set[int]] = {}
    for row in rows:
        t = row.get("t")
        if not isinstance(t, int):
            continue
        for gate in (row.get("gates") or {}).values():
            for plugin, held in (gate.get("served") or {}).items():
                if held and held > 0:
                    out.setdefault(model_of.get(plugin, plugin), set()).add(t)
    return out


def count(model: str, seconds: set[int], window_s: int, gap_s: int,
          context_mib: float | None) -> Row:
    """One model at one `gap_s`: its busy periods and what its context would
    leave free. A run of working seconds ends when more than `gap_s` idle
    seconds follow; the bridged gaps count as held, and so does `gap_s` after
    each period's last second (the keep runs out then), within the window."""
    ts = sorted(seconds)
    periods = held = 0
    start = prev = None
    for t in ts:
        if prev is None or t - prev - 1 > gap_s:
            if prev is not None:
                held += prev - start + 1 + gap_s
            periods += 1
            start = t
        prev = t
    if prev is not None:
        held += prev - start + 1 + gap_s
    held = min(held, window_s)
    free = max(0, window_s - held)
    gib_h = None if context_mib is None else free * context_mib * MIB / 2**30 / 3600.0
    return Row(model, gap_s, len(ts), periods, held, free, gib_h)


def table(seconds: dict[str, set[int]], window_s: int, context_mib: dict[str, float],
          gaps: Iterable[int] = GAPS_S) -> list[Row]:
    return [count(m, s, window_s, g, context_mib.get(m))
            for m, s in sorted(seconds.items()) for g in gaps]


def render(rows: list[Row], window_s: int) -> str:
    out = [f"window: {window_s} s ({window_s / 3600:.1f} h); one line a second, so a gap under 1 s is not seen",
           "",
           "| model | keep bridges | worked | busy periods = creations | per day | context held | free | GiB·h given back |",
           "|---|---|---|---|---|---|---|---|"]
    per_day = 86400.0 / window_s if window_s else 0.0
    for r in rows:
        gib = "" if r.gib_h is None else f"{r.gib_h:.1f}"
        out.append(f"| {r.model} | {r.gap_s} s | {r.busy_s} s ({100.0 * r.busy_s / window_s:.1f} %) | "
                   f"{r.periods} | {r.periods * per_day:.0f} | {100.0 * r.held_s / window_s:.1f} % | "
                   f"{100.0 * r.free_s / window_s:.1f} % | {gib} |")
    return "\n".join(out)


def _pairs(values: list[str], what: str, number: bool = False) -> dict:
    out: dict = {}
    for v in values:
        key, sep, value = v.partition("=")
        if not sep or not key or not value:
            raise SystemExit(f"{what}: expected name=value, got {v!r}")
        out[key] = float(value) if number else value
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--card-dir", type=Path, required=True, help="<root>/state/card")
    p.add_argument("--hours", type=float, default=24.0, help="the window, ending at --until (default 24)")
    p.add_argument("--until", type=float, default=None, help="epoch seconds (default: now)")
    p.add_argument("--model", action="append", default=[], metavar="COLLECTION=MODEL")
    p.add_argument("--context", action="append", default=[], metavar="MODEL=MiB")
    p.add_argument("--gap", action="append", type=int, default=None, metavar="SECONDS",
                   help=f"idle seconds a keep bridges, repeatable (default {' '.join(map(str, GAPS_S))})")
    a = p.parse_args(argv)
    if not a.card_dir.is_dir():
        raise SystemExit(f"{a.card_dir}: no such directory")
    until = int(a.until if a.until is not None else time.time())
    since = until - int(a.hours * 3600)
    seconds = working_seconds(read(a.card_dir, since, until), _pairs(a.model, "--model"))
    if not seconds:
        print(f"{a.card_dir}: no encoder gate served anything between {since} and {until}", file=sys.stderr)
        return 1
    rows = table(seconds, until - since, _pairs(a.context, "--context", number=True),
                 a.gap or GAPS_S)
    print(render(rows, until - since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
