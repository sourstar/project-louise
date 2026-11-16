"""THE VERSION SHOWN IS THE ONE IN FORCE TODAY (operator, 2026-11-16, "It
should", amending 05 §5.1's "the latest text"; app/validity.py). München's
case: L. 232/2016 showed its 2027-01-01 version while 2026-08-08→2027-01-01
was in force — 147 Normattiva works so. Of a Work's versions with text:
the one in force today; else the latest started before today (a repealed
act's last text); else the earliest (a law in its vacatio legis). Rolled
once a day by the index maintainer, only the Works whose version moved."""
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import db, validity

WORK = "eli/it/legge/2016/232"
PROFILE = {"validity": {"kind": "versions-per-work", "from": "extra.valid_from", "work": "extra.work"}}


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA)
    return c


def coll(clock="Europe/Rome"):
    return SimpleNamespace(name="normattiva", codex=SimpleNamespace(profile=PROFILE,
                                                                    origin=SimpleNamespace(clock=clock)))


def _version(con, n: int, start: int, *, work=WORK, text=True, day=20261116):
    validity.record(con, f"n:{n}", n, {"from": start, "work": work, "has_text": text, "in_force": None},
                    day=day)


def _shown(con, work=WORK) -> list[int]:
    return [r[0] for r in con.execute("SELECT native_id FROM validity WHERE work=? AND shown=1", (work,))]


def test_the_version_in_force_today_is_shown_not_the_latest(con):
    for n, start in ((1, 20170101), (2, 20260808), (3, 20270101)):
        _version(con, n, start)
    assert _shown(con) == [2], "2026-08-08 → 2027-01-01 is in force on 2026-11-16"
    validity.recompute(con, WORK, day=20270101)
    assert _shown(con) == [3], "from its first day"


@pytest.mark.parametrize("starts, texts, day, want", [
    ([20170101, 20200101], [True, True], 20261116, 1),           # the later one in force
    ([20170101, 20200101], [True, False], 20261116, 0),          # repealed (no text): its last text
    ([20170101, 20200101, 20300101], [True, False, True], 20261116, 0),   # suspended today: the one before
    ([20270101, 20280101], [True, True], 20261116, 0),           # all to come: the earliest
    ([20170101], [False], 20261116, None),                       # no text: none
])
def test_the_rule(starts, texts, day, want):
    ends = [*starts[1:], None]
    assert validity.shown_index(starts, ends, texts, day) == want


def test_a_closed_act_keeps_its_last_text(con):
    """An end alone never moves `shown`: the version ended stays shown."""
    assert validity.shown_index([20170101], [20200101], [True], 20261116) == 0


def test_today_is_the_collection_s_clock():
    late = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)
    assert validity.today(coll("Europe/Rome"), now=late) == 20270101
    assert validity.today(coll(None), now=late) == 20261231
    assert validity.today(None, now=late) == 20261231


def test_the_daily_roll_moves_only_the_works_whose_version_starts(con):
    # rows as "the latest text" left them before the amendment: shown on 3
    for n, start in ((1, 20170101), (2, 20260808), (3, 20270101)):
        _version(con, n, start, day=20991231)
    _version(con, 10, 20100101, work="other")
    assert _shown(con) == [3]
    con.execute("UPDATE documents SET fts_done=1, embed_done=1")
    c = coll()
    first = datetime(2026, 11, 16, 9, tzinfo=timezone.utc)
    assert validity.roll(con, c, now=first) == 1, "the first roll: every Work with a version to come"
    assert _shown(con) == [2] and validity.get_rolled(con) == 20261116
    assert con.execute("SELECT COUNT(*) FROM validity WHERE synced=0 AND work=?", (WORK,)).fetchone()[0] >= 2
    assert validity.roll(con, c, now=first) == 0, "the same day: nothing"
    assert validity.roll(con, c, now=datetime(2026, 12, 20, 9, tzinfo=timezone.utc)) == 0, "no version starts"
    assert _shown(con) == [2]
    assert validity.roll(con, c, now=datetime(2027, 1, 3, 9, tzinfo=timezone.utc)) == 1, "days missed: caught up"
    assert _shown(con) == [3]


def test_a_collection_without_validity_is_not_rolled(con):
    assert validity.roll(con, SimpleNamespace(name="g", codex=SimpleNamespace(profile=None))) == 0
    assert validity.get_rolled(con) is None


def test_the_roll_reads_by_the_start_index(con):
    """CLAUDE.md: a hot-path query is plan-checked; the roll's is a range on
    idx_validity_from, never a scan of the version table."""
    for q in ("SELECT work FROM validity WHERE valid_from > ? AND has_text=1",
              "SELECT work FROM validity WHERE valid_from > ? AND valid_from <= ? AND has_text=1"):
        plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + q, [1, 2][:q.count("?")]))
        assert "idx_validity_from" in plan and "SCAN validity" not in plan, plan
