"""The Library card, compact on a phone (operator, 2026-11-11):
1. "The gap between the collections cards shall be the same between a card
   and the border";
2. "just show 'embedded' (in one row) and '29.640.094 / 44.266.030 · 67,0 %',
   then in another row the 'ETA' with best/current, best calculated on the
   work to do divided by the theorical top speed with 100% GPU available,
   and current calculated with the current speed. No extra text around like
   wikidata 'Embedded: 9 of 14 families: …'"."""
import json
import re
import time
from pathlib import Path

import pytest

from app import collections as collections_mod, db, web
from app.rage import card_record

from tests.test_library_card_eta import _render, _seed, _STAGES

CSS = (Path(web.__file__).parent / "static" / "admin.css").read_text(encoding="utf-8")


# ---- 1. the gaps -----------------------------------------------------------------

def test_on_a_phone_the_gap_between_cards_is_the_gap_to_the_border():
    phone = CSS[CSS.index("@media (max-width:760px){"):]
    phone = phone[:phone.index("\n}")]
    side = re.search(r"main\{padding:\d+px (\d+)px\}", phone).group(1)
    gap = re.search(r"\.grid\{[^}]*gap:(\d+)px", phone).group(1)
    assert gap == side, "between two cards as between a card and the screen's edge"
    assert ".grid>.panel{margin-bottom:0}" in CSS, "a card's own margin would add to the gap"


# ---- 2. the best ETA: the work left at the whole card ------------------------------

def _second(t, busy, served, done=None, q=0):
    return {"t": t, "q": q, "cards": {"0000:03:00.0": busy} if busy is not None else None,
            "gates": {"http://localai:8080": {"running": 1, "waiting": 0, "served": served}},
            "feed": {}, "done": done or {}}


def test_card_seconds_are_split_by_each_gates_service_and_a_query_gives_none():
    rows = [_second(1, 0.8, {"wikidata": 3.0, "wikipedia": 1.0}, {"wikidata": [2, 9]}),
            _second(2, 0.5, {"wikidata": 1.0}, {"wikidata": [1, 4]}),
            _second(3, 1.0, {"wikidata": 1.0}, q=1),                 # a person's query held the card
            _second(4, None, {"wikidata": 1.0}, {"wikidata": [5, 5]})]  # no card figure: not counted
    got = card_record.gpu_time(rows)
    assert got["wikidata"][0] == 3
    assert got["wikidata"][1] == pytest.approx(0.8 * 0.75 + 0.5)
    assert got["wikipedia"] == [0, pytest.approx(0.8 * 0.25)]


def test_the_speed_is_read_from_the_card_record_and_kept_between_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(card_record, "directory", lambda: tmp_path)
    now = int(time.time())
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    lines = [_second(now - 300 + i, 1.0, {"wikidata": 1.0}, {"wikidata": [1, 3]} if i % 2 else None)
             for i in range(120)]
    lines += [_second(now - 100 + i, 1.0, {"brief": 1.0}, {"brief": [1, 1]}) for i in range(10)]
    (tmp_path / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    monkeypatch.setattr(web, "_gpu_speed_memo", {"at": 0.0, "speeds": {}})
    speeds = web._gpu_speeds()
    assert speeds["wikidata"] == pytest.approx(60 / 120), "documents a card-second"
    assert "brief" not in speeds, "ten card-seconds are no measurement"
    (tmp_path / f"{day}.jsonl").unlink()
    assert web._gpu_speeds() is speeds, "re-read at most every TTL"


def test_the_best_is_the_work_over_the_whole_cards_speed_and_never_later_than_current(monkeypatch):
    monkeypatch.setattr(web, "_gpu_speeds", lambda: {"wikidata": 2.0})
    assert web._best_eta("wikidata", 7200, None) == "~60min"            # 7,200 / 2 a second
    assert web._best_eta("wikidata", 7200, 14_400) == "~30min", "not slower than now"
    assert web._best_eta("wikipedia", 7200, None) is None, "no measurement, no best"
    assert web._best_eta("wikidata", 0, None) is None


@pytest.fixture
def card(isolated_config):
    c = collections_mod.default_collection()
    con = db.connect(c)
    _seed(con, [(1, 1, 1, 1, None)] * 9 + [(1, 1, 1, 0, None)] * 991)
    # embedding everything: only then has "current" a rate to say (off or
    # on-demand, it is "-": the test below)
    db.set_scope(con, {**db.get_scope(con), "embed_mode": "all"})
    now = int(time.time())
    web._stage_rate_memo[str(c.manifest_path)] = {
        **{k: 0 for k in _STAGES}, "embed_ph": 1_000, "embed_n": 2, "ts": now - 600}
    web.invalidate_counts()
    return c


def test_the_embedded_rows_say_the_numbers_then_best_and_current(card, monkeypatch):
    monkeypatch.setattr(web, "_gpu_speeds", lambda: {card.name: 1.0})   # 991 docs at 1 a card-second
    shelf = web._collection_shelf(card, {}, "running")
    html = _render(shelf)
    assert shelf["embed_eta"]["best"] == "~16min" and shelf["embed_eta"]["current"].startswith("~")
    row = re.search(r'>embedded</span><span>([^<]*)</span></div>', html)
    assert row and row.group(1) == "9 / 1.000 · 0,9 %", "the numbers alone"
    eta = re.search(r'<div class="lbl" data-eta="embed"[^>]*><span>ETA</span>(.*?)</div>', html, re.S)
    assert eta, "the ETA on a row of its own"
    text = re.sub(r"<[^>]+>", "", eta.group(1))
    # the current benchmark beside the current ETA (operator, 2026-11-11:
    # "the current benchmark in token/s or chunk/s disappeared")
    assert text == f"best ~16min · current {shelf['embed_eta']['current']} ({shelf['embed_eta']['rate']})"


@pytest.mark.parametrize("mode", ["off", "lazy"])
def test_off_or_on_demand_the_current_is_a_dash(card, monkeypatch, mode):
    """The operator, 2026-11-11: "When a collection's embedding is either off
    or on-demand 'current' shall be '-'". Nothing embeds the rest on its own;
    best still says what the whole card would take."""
    monkeypatch.setattr(web, "_gpu_speeds", lambda: {card.name: 1.0})
    con = db.connect(card)
    db.set_scope(con, {**db.get_scope(con), "embed_mode": mode})
    web.invalidate_counts()
    shelf = web._collection_shelf(card, {}, "running")
    assert shelf["embed_eta"]["current"] == "-" and shelf["embed_eta"]["best"] == "~16min"
    assert ("off" if mode == "off" else "on-demand") in shelf["embed_eta"]["current_why"]
    eta = re.search(r'<div class="lbl" data-eta="embed"[^>]*><span>ETA</span>(.*?)</div>', _render(shelf), re.S)
    assert eta and re.sub(r"<[^>]+>", "", eta.group(1)) == "best ~16min · current -"


def test_a_cpu_encoder_has_no_best(card, monkeypatch):
    monkeypatch.setattr(web, "_gpu_speeds", lambda: {card.name: 1.0})
    import dataclasses
    real = web._shelf_recipe
    monkeypatch.setattr(web, "_shelf_recipe", lambda c: dataclasses.replace(real(c), device="cpu"))
    shelf = web._collection_shelf(card, {}, "running")
    assert shelf["embed_device"] == "cpu"
    assert shelf["embed_eta"]["best"] is None and shelf["embed_eta"]["current"], \
        "the whole card is a GPU's: a CPU encoder has its current alone"


def test_no_work_left_no_eta_row(isolated_config):
    c = collections_mod.default_collection()
    con = db.connect(c)
    _seed(con, [(1, 1, 1, 1, None)] * 4)
    web.invalidate_counts()
    shelf = web._collection_shelf(c, {}, "running")
    assert shelf["embed_eta"] is None and 'data-eta="embed"' not in _render(shelf)


def test_a_complete_stage_has_no_eta_row_even_with_blocked_documents_left(isolated_config, monkeypatch):
    """The operator, 2026-11-11: "Hide ETA row when completed". Blocked
    documents wait for "clear errors & retry", not for time — even with a
    measured speed, which would otherwise put a best on the row."""
    c = collections_mod.default_collection()
    monkeypatch.setattr(web, "_gpu_speeds", lambda: {c.name: 1.0})
    con = db.connect(c)
    _seed(con, [(1, 1, 1, 1, None)] * 4 + [(1, 1, 1, 0, "embed: failed")] * 2)
    web.invalidate_counts()
    shelf = web._collection_shelf(c, {}, "running")
    assert shelf["embed_done"] == 4 and shelf["stage_targets"]["embed"] > 4, "work left, all of it blocked"
    assert shelf["embed_eta"] is None and 'data-eta="embed"' not in _render(shelf)


def test_the_slices_line_is_behind_details(card):
    shelf = dict(web._collection_shelf(card, {}, "running"))
    shelf["embed_slices"] = {"line": "Embedded: 9 of 14 families: person, work", "all": False}
    html = _render(shelf)
    details = html.index('data-drawer="details"')
    line = html.index("Embedded: 9 of 14 families")
    assert line > details, "no extra text in sight"
    assert line < html.index('data-drawer="options"')


def test_the_current_benchmark_is_chunks_a_second_from_the_card_record(card, monkeypatch):
    """Operator, 2026-11-11: "ETA row: the current benchmark in token/s or
    chunk/s disappeared". The card record's chunks of the last ten minutes."""
    import itertools
    from app.rage import card_record
    now = time.time()
    rows = [{"t": now - 600 + i, "done": {card.name: [1, 3]}} for i in range(600)]
    monkeypatch.setattr(card_record, "read", lambda since, until=None, out_dir=None:
                        (r for r in itertools.chain(rows) if since <= r["t"] <= (until or now)))
    monkeypatch.setattr(card_record, "gpu_time", lambda rows: {})
    monkeypatch.setattr(web, "_gpu_speed_memo", {"at": 0.0, "speeds": {}})   # restored after
    shelf = web._collection_shelf(card, {}, "running")
    assert shelf["embed_eta"]["rate"] == "3.0 chunks/s"
    assert "(3.0 chunks/s)" in _render(shelf)


def test_an_idle_collection_keeps_its_last_measured_best(card, monkeypatch):
    """Operator, 2026-11-11: "When embedding is disabled or on-demand the best
    ETA shall still be shown". Its card speed is the last one measured."""
    from app.rage import card_record
    monkeypatch.setattr(card_record, "read", lambda *a, **k: iter(()))
    monkeypatch.setattr(card_record, "gpu_time", lambda rows: {card.name: [600, 600.0]})
    monkeypatch.setattr(web, "_gpu_speed_memo", {"at": 0.0, "speeds": {}})   # restored after
    assert web._gpu_speeds()[card.name] == 1.0                      # measured, and kept
    monkeypatch.setattr(card_record, "gpu_time", lambda rows: {})   # embedding stopped since
    monkeypatch.setattr(web, "_gpu_speed_memo", {"at": 0.0, "speeds": {}})   # restored after
    assert web._gpu_speeds()[card.name] == 1.0, "the last measured speed"
    con = db.connect(card)
    db.set_scope(con, {**db.get_scope(con), "embed_mode": "off"})
    web.invalidate_counts()
    shelf = web._collection_shelf(card, {}, "running")
    assert shelf["embed_eta"]["best"] == "~16min" and shelf["embed_eta"]["current"] == "-"
    assert shelf["embed_eta"]["rate"] is None, "nothing embeds: no rate beside '-'"
