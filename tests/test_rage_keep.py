"""RAGE's keep: how long a model keeps its context after an answer, per job
type, trimmed by RAGE itself (app/rage/keep.py; docs/notes/idle-context-brief.md
§1.3).

The operator, 2026-11-11: "Let's start with a 20 ms expiration time, the
timeout shall be trimmed per job type"; "I choose option B, trimmed by RAGE";
"The trimming time shall not be modified if the expiry occurred because of a
job missed scheduling (for instance when a chat model takes over)".

Held here, on a clock the test moves:
  - a stream starts at 20 ms, and its first request judges nothing;
  - back to back requests leave the keep alone;
  - a context that expired just before the stream's next request was READY
    doubles the keep (a premature expiry), up to the ceiling;
  - ready in time and SENT late — stood aside for a person's query, the gate,
    a load — the expiry is excused: the keep does not move;
  - an expiry after a deferred request is excused too;
  - a long pause is no premature expiry: freeing was right;
  - a keep raised for a rhythm that has passed decays, never under the floor;
  - one model, two streams: each has its own keep, and the context one keeps
    alive is not the other's expiry;
  - a request in flight on the model means no context expired;
  - a keep fixed by the caller (the gate's keep 0) is never trimmed;
  - the judgement is O(1): no state grows with the requests sent;
  - the GAPS are counted for every stream, keyed or not: how long the model
    had been idle when the request was ready, by powers of two, with the
    requests sent while it worked and those held back counted apart — and a
    stream whose model takes no keep is sent none and never judged.
"""
from __future__ import annotations

import pytest

from app.rage import keep

SERVER = "http://localai:8080"
MODEL = "vega-nemotron-3-embed-1b"
WIKIDATA = keep.Stream(SERVER, MODEL, "wikidata", "corpus")
NORMATTIVA = keep.Stream(SERVER, MODEL, "normattiva-nemo", "corpus")


#: the operator's start, as the module has it — read before the pin below
OPERATORS_START_MS = keep.START_MS


@pytest.fixture(autouse=True)
def a_start_of_twenty(monkeypatch):
    """The scenarios below are written for a start of 20 ms (the operator's
    first; their gaps and waits are counted against it). The start is a
    parameter of the trim, not part of it: the operator's own is held by its
    own test."""
    monkeypatch.setattr(keep, "START_MS", 20)


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t

    def ms(self, n: float) -> None:
        self.t += n / 1000.0


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def keeps(clock) -> keep.Keeps:
    return keep.Keeps(clock)


def _once(keeps: keep.Keeps, clock: Clock, stream: keep.Stream, *, held_ms: float = 0.0,
          work_ms: float = 5.0) -> int:
    """One request of `stream`: ready now, sent `held_ms` later, answered
    after `work_ms`. The keep it carried."""
    ready = clock()
    clock.ms(held_ms)
    sent = keeps.send(stream, ready)
    clock.ms(work_ms)
    keeps.answered(sent)
    return sent.keep_ms


def _state(keeps: keep.Keeps, stream: keep.Stream) -> dict:
    return next(s for s in keeps.snapshot() if (s["plugin"], s["level"]) == (stream.plugin, stream.level))


def test_a_stream_starts_at_the_operator_s_fifty_milliseconds(keeps, clock, monkeypatch):
    """Operator, 2026-11-11, the node measured: "Start at 50 ms". The floor
    stays his first start, 20 ms: he moved the start, not the floor."""
    assert OPERATORS_START_MS == 50 and keep.FLOOR_MS == 20
    monkeypatch.setattr(keep, "START_MS", OPERATORS_START_MS)
    assert _once(keeps, clock, WIKIDATA) == 50
    s = _state(keeps, WIKIDATA)
    assert (s["requests"], s["premature"], s["excused"], s["paused"]) == (1, 0, {}, 0)
    assert (s["keep_ms"], s["highest_ms"]) == (50, 50)


def test_a_stream_made_at_another_start_begins_there(keeps, clock):
    assert _once(keeps, clock, WIKIDATA) == 20, "the start is read when the stream is made"
    s = _state(keeps, WIKIDATA)
    assert (s["requests"], s["premature"], s["excused"], s["paused"]) == (1, 0, {}, 0)


def test_requests_back_to_back_leave_the_keep_alone(keeps, clock):
    for _ in range(50):
        assert _once(keeps, clock, WIKIDATA) == 20
        clock.ms(5)                                  # the next is ready 5 ms after the answer
    assert _state(keeps, WIKIDATA)["premature"] == 0


def test_a_context_that_expired_just_before_the_next_request_doubles_the_keep(keeps, clock):
    _once(keeps, clock, WIKIDATA)
    carried = []
    for _ in range(4):
        clock.ms(170)                                # wikipedia's mean gap on record
        carried.append(_once(keeps, clock, WIKIDATA))
    assert carried == [40, 80, 160, 320], "doubled at each premature expiry, then enough"
    clock.ms(170)
    assert _once(keeps, clock, WIKIDATA) == 320, "a 170 ms gap no longer meets an expired keep"
    assert _state(keeps, WIKIDATA)["premature"] == 4


def test_the_keep_stops_at_the_ceiling(keeps, clock):
    _once(keeps, clock, WIKIDATA)
    for _ in range(12):
        k = _state(keeps, WIKIDATA)["keep_ms"]
        clock.ms(k + 50)                             # always back just after its keep ran out
        _once(keeps, clock, WIKIDATA)
    assert _state(keeps, WIKIDATA)["keep_ms"] == keep.CEILING_MS


def test_a_request_ready_in_time_and_sent_late_is_excused(keeps, clock):
    """The chat model takes over: the corpus request was ready 5 ms after its
    stream's last answer and stood aside four seconds. Its keep does not move."""
    _once(keeps, clock, WIKIDATA)
    clock.ms(5)
    assert _once(keeps, clock, WIKIDATA, held_ms=4_000) == 20
    s = _state(keeps, WIKIDATA)
    assert s["premature"] == 0 and s["excused"] == {keep.EXCUSED_HELD: 1}
    clock.ms(5)
    assert _once(keeps, clock, WIKIDATA) == 20, "and the stream goes on at its own keep"


def test_an_excused_expiry_earns_nothing_toward_the_decay(keeps, clock):
    _once(keeps, clock, WIKIDATA)
    clock.ms(100)
    assert _once(keeps, clock, WIKIDATA) == 40       # one premature expiry: 40 ms
    for _ in range(40):                              # 40 excused expiries over 200 s
        clock.ms(5)
        _once(keeps, clock, WIKIDATA, held_ms=5_000)
    assert _state(keeps, WIKIDATA)["keep_ms"] == 40, "no clean interval, no decay"
    assert _state(keeps, WIKIDATA)["excused"] == {keep.EXCUSED_HELD: 40}


def test_the_expiry_after_a_deferred_request_is_excused(keeps, clock):
    """A creation refused for memory, a busy runner: the retry comes late, and
    that is not the stream's rhythm."""
    _once(keeps, clock, WIKIDATA)
    clock.ms(5)
    sent = keeps.send(WIKIDATA, clock())
    clock.ms(30)
    keeps.failed(sent)                               # HTTP 500, a timeout: deferred
    clock.ms(400)                                    # the batch is tried again
    assert _once(keeps, clock, WIKIDATA) == 20
    assert _state(keeps, WIKIDATA)["excused"] == {keep.EXCUSED_DEFERRED: 1}
    assert _state(keeps, WIKIDATA)["premature"] == 0


def test_a_long_pause_is_no_premature_expiry(keeps, clock):
    _once(keeps, clock, WIKIDATA)
    clock.ms(keep.START_MS + keep.PREMATURE_WINDOW_MS + 500)   # the stream had stopped
    assert _once(keeps, clock, WIKIDATA) == 20
    s = _state(keeps, WIKIDATA)
    assert (s["premature"], s["paused"]) == (0, 1)


def test_the_window_follows_the_creation_s_measured_time(keeps, clock):
    assert keeps.creation_ms(SERVER, MODEL) == keep.CREATION_MS
    keeps.creation_ms(SERVER, MODEL, 30)             # measured: a creation takes 30 ms
    _once(keeps, clock, WIKIDATA)
    clock.ms(20 + 400)                               # 400 ms after its keep: over 10 x 30 ms
    assert _once(keeps, clock, WIKIDATA) == 20, "holding 400 ms would not have been cheaper"
    clock.ms(20 + 200)                               # 200 ms after: within it
    assert _once(keeps, clock, WIKIDATA) == 40


def test_a_keep_decays_when_the_rhythm_has_passed_and_never_under_the_floor(keeps, clock):
    _once(keeps, clock, WIKIDATA)
    for _ in range(3):
        clock.ms(170)
        _once(keeps, clock, WIKIDATA)
    assert _state(keeps, WIKIDATA)["keep_ms"] == 160
    # another lane of the model works throughout, so no context expires and
    # every interval is clean however long: a minute apart, one decay each
    lane = keeps.send(NORMATTIVA, clock())
    seen = []
    for _ in range(30):
        clock.ms(keep.DECAY_EVERY_S * 1000 + 1)
        seen.append(_once(keeps, clock, WIKIDATA))
    keeps.answered(lane)
    assert seen[:4] == [144, 129, 116, 104], "a tenth less after each clean minute"
    assert seen == sorted(seen, reverse=True) and seen[-1] == keep.FLOOR_MS
    assert _state(keeps, WIKIDATA)["premature"] == 3, "the descent itself cost no creation"


def test_a_clean_interval_decays_nothing_before_its_minute(keeps, clock):
    _once(keeps, clock, WIKIDATA)
    clock.ms(100)
    assert _once(keeps, clock, WIKIDATA) == 40
    for _ in range(1_000):                           # ten seconds back to back
        clock.ms(5)
        assert _once(keeps, clock, WIKIDATA) == 40


def test_two_collections_on_one_model_have_each_their_own_keep(keeps, clock):
    """Normattiva comes every 170 ms, wikidata queues back to back: the model
    is one, the job types are two (the operator: "several collections share
    the same model, but their way to queue the jobs may be different")."""
    _once(keeps, clock, NORMATTIVA)
    for _ in range(4):
        clock.ms(170)
        _once(keeps, clock, NORMATTIVA)
    clock.ms(5_000)                                  # normattiva's stream ends; wikidata's begins
    for _ in range(200):
        clock.ms(5)
        _once(keeps, clock, WIKIDATA)
    assert _state(keeps, NORMATTIVA)["keep_ms"] == 320
    assert _state(keeps, WIKIDATA)["keep_ms"] == 20, "its own rhythm, not its neighbour's"


def test_a_context_another_stream_keeps_alive_is_nobody_s_expiry(keeps, clock):
    _once(keeps, clock, NORMATTIVA)
    for _ in range(5):
        clock.ms(170)
        _once(keeps, clock, NORMATTIVA)              # 320 ms by now
    _once(keeps, clock, WIKIDATA)
    for _ in range(10):
        clock.ms(100)                                # past wikidata's 20 ms, inside normattiva's 320
        _once(keeps, clock, NORMATTIVA)
        clock.ms(100)
        assert _once(keeps, clock, WIKIDATA) == 20
    assert _state(keeps, WIKIDATA)["premature"] == 0


def test_a_request_in_flight_on_the_model_means_no_context_expired(keeps, clock):
    _once(keeps, clock, WIKIDATA)
    lane = keeps.send(NORMATTIVA, clock())           # another lane, still working
    clock.ms(500)
    assert _once(keeps, clock, WIKIDATA) == 20
    keeps.answered(lane)
    assert _state(keeps, WIKIDATA)["premature"] == 0


def test_our_clock_runs_behind_the_server_s_and_a_borderline_gap_counts_as_expired(keeps, clock):
    """The answer's way back and the request's way there are the server's
    idle time, not ours: within TRANSIT_MS of the keep, the context is taken
    for gone."""
    _once(keeps, clock, WIKIDATA)
    clock.ms(keep.START_MS - keep.TRANSIT_MS + 1)
    assert _once(keeps, clock, WIKIDATA) == 40


def test_a_fixed_keep_is_never_trimmed(keeps, clock):
    fixed = keep.Stream(SERVER, MODEL, "wikidata", keep.FIXED_LEVEL)
    for _ in range(20):
        sent = keeps.send_fixed(fixed, 0)
        assert sent.keep_ms == 0
        clock.ms(5)
        keeps.answered(sent)
        clock.ms(100)
    s = _state(keeps, fixed)
    assert (s["keep_ms"], s["premature"], s["requests"]) == (0, 0, 20)


def test_the_judgement_keeps_no_history(keeps, clock):
    """A per-request cost that grew with the requests sent would be a bug
    however fast it looks here (CLAUDE.md): one state per stream, one per
    model, whatever was sent."""
    for i in range(5_000):
        clock.ms(3 + i % 400)
        _once(keeps, clock, WIKIDATA)
    assert len(keeps._streams) == 1 and len(keeps._models) == 1
    state = keeps._streams[WIKIDATA]
    assert all(not isinstance(v, (tuple, set)) for v in vars(state).values())
    assert len(state.gaps) == keep.GAP_BUCKETS, "the one list is the histogram, of fixed size"
    assert len(state.excused) <= 2


def test_the_request_s_body_and_the_reasons_a_model_gets_none(monkeypatch):
    from app import model_load
    from app.rage import dispatch
    keep.reset()
    assert keep.metadata(None) == {}
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(model_load, "context_on_demand",
                        lambda url, model: asked.append((url, model)) or model == MODEL)
    assert keep.for_request(SERVER, None, 0.0) is None, "no model named: nothing to count it under"
    plain = keep.for_request(SERVER, "vega-bge-m3", 0.0)
    assert plain is not None and plain.keep_ms is None, "not loaded with the option: counted, no keep"
    assert keep.metadata(plain) == {}
    keep.answered(plain)
    with dispatch.working_as(dispatch.Level.CORPUS, "wikidata"):
        sent = keep.for_request(f"{SERVER}/", MODEL, 0.0)
    assert sent == keep.Sent(keep.Stream(SERVER, MODEL, "wikidata", "corpus"), 20)
    assert keep.metadata(sent) == {"metadata": {"keep_context_ms": "20"}}
    keep.answered(sent)
    with keep.fixed(0), dispatch.working_as(dispatch.Level.CORPUS, "wikidata"):
        gate = keep.for_request(SERVER, MODEL, 0.0)
    assert gate == keep.Sent(keep.Stream(SERVER, MODEL, "wikidata", keep.FIXED_LEVEL), 0)
    keep.answered(gate)
    assert {(s["model"], s["level"]): s["keep_ms"] for s in keep.snapshot()} == {
        (MODEL, "corpus"): 20, (MODEL, "fixed"): 0, ("vega-bge-m3", "query"): None}
    keep.reset()


def test_a_request_that_raises_or_fails_is_not_an_answer():
    from app import model_load
    keep.reset()
    real = model_load.context_on_demand
    model_load.context_on_demand = lambda url, model: True          # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            with keep.request(SERVER, MODEL, 0.0):
                raise RuntimeError("the endpoint is gone")
        with keep.request(SERVER, MODEL, 0.0) as k:
            assert k.body == {"metadata": {"keep_context_ms": "20"}}
            k.fail()
        model, = keep.KEEPS._models.values()
        stream, = keep.KEEPS._streams.values()
        assert model.in_flight == 0 and stream.answered is None
        assert stream.excuse == keep.EXCUSED_DEFERRED
    finally:
        model_load.context_on_demand = real                           # type: ignore[assignment]
        keep.reset()


# ---- the gaps, for every stream ----------------------------------------------------------

BGE = keep.Stream(SERVER, "vega-bge-m3", "wikipedia", "corpus")


def test_the_gaps_are_counted_by_powers_of_two_for_a_stream_that_takes_no_keep(keeps, clock):
    """Nothing on the node is loaded with the option yet: the record of how
    idle each model is between a stream's requests is what the first keep is
    chosen from (the brief §2)."""
    first = keeps.send(BGE, clock(), keyed=False)
    assert first.keep_ms is None
    clock.ms(5)
    keeps.answered(first)
    for gap in (0.4, 1, 3, 13, 13, 170, 170, 170, 900, 70_000):
        clock.ms(gap)
        sent = keeps.send(BGE, clock(), keyed=False)
        assert sent.keep_ms is None
        clock.ms(5)
        keeps.answered(sent)
    s = _state(keeps, BGE)
    assert s["keep_ms"] is None and (s["premature"], s["paused"], s["excused"]) == (0, 0, {})
    gaps = s["gaps"]
    assert len(gaps) == keep.GAP_BUCKETS and sum(gaps) == 10, "the first request has no gap before it"
    # under 1 ms, 1, 2-3, 8-15 (twice), 128-255 (three times), 512-1023, and the last bucket
    assert (gaps[0], gaps[1], gaps[2], gaps[4], gaps[8], gaps[10], gaps[-1]) == (1, 1, 1, 2, 3, 1, 1)
    assert s["longest_gap_ms"] == pytest.approx(70_000, abs=1)
    assert keep._over(gaps, 32) == 5 and keep._over(gaps, 512) == 2
    assert keep._under(gaps, 0.5) == "under 16 ms" and keep._under(gaps, 0.9) == "under 1024 ms"


def test_a_request_sent_while_the_model_works_has_no_gap_and_a_held_one_is_counted_apart(keeps, clock):
    _once(keeps, clock, BGE)                                   # (keyed here; the gaps do not care)
    lane = keeps.send(BGE, clock())                            # in flight
    clock.ms(3)
    other = keeps.send(BGE, clock())                           # sent while the first works
    clock.ms(3)
    keeps.answered(lane)
    keeps.answered(other)
    clock.ms(10)
    _once(keeps, clock, BGE, held_ms=4_000)                    # ready 10 ms after, sent 4 s later
    s = _state(keeps, BGE)
    assert (s["requests"], s["busy"], s["held"]) == (4, 1, 1)
    assert sum(s["gaps"]) == 2 and s["gaps"][4] == 1, "judged on when it was READY: 10 ms"


def test_the_line_the_worker_publishes(monkeypatch):
    from app import model_load
    keep.reset()
    clock = Clock()
    monkeypatch.setattr(keep.KEEPS, "_clock", clock)
    for stream, keyed in ((WIKIDATA, True), (BGE, False)):
        sent = keep.KEEPS.send(stream, clock(), keyed=keyed)
        clock.ms(5)
        keep.KEEPS.answered(sent)
        for gap in (5, 170, 40):
            clock.ms(gap)
            sent = keep.KEEPS.send(stream, clock(), keyed=keyed)
            clock.ms(5)
            keep.KEEPS.answered(sent)
        clock.ms(60_000)
    keyed_line, plain_line = sorted(keep.lines(), key=lambda line: "vega-bge-m3" in line)
    assert plain_line.startswith("keep (since start) — wikipedia (corpus) on vega-bge-m3 at "
                                 "http://localai:8080: 4 request(s), 0 while the model worked, "
                                 "0 held back over 50 ms; before the other 3 the model had been idle ")
    assert "32 ms or more: 2, 512 ms or more: 0" in plain_line and "its keep" not in plain_line
    assert "wikidata (corpus)" in keyed_line and "; its keep 80 ms (highest 80), 2 premature" in keyed_line
    assert "excused: none" in keyed_line
    keep.reset()
    assert keep.lines() == []



def test_the_worker_s_beat_publishes_each_stream_s_line(isolated_config, monkeypatch):
    """Published by the worker's beat (`_log_shares`), never by the thread
    that sent the requests — one cumulative line a stream, naming its
    collection."""
    from app import db, worker
    keep.reset()
    clock = Clock()
    monkeypatch.setattr(keep.KEEPS, "_clock", clock)
    for gap in (0, 5, 170):
        clock.ms(gap)
        sent = keep.KEEPS.send(BGE, clock(), keyed=False)
        clock.ms(5)
        keep.KEEPS.answered(sent)
    monkeypatch.setattr(worker, "_last_shares_log", 0.0)
    con = db.connect()
    worker._log_shares(con)
    lines = [r[0] for r in con.execute(
        "SELECT message FROM events WHERE stage='sched' AND message LIKE 'keep (since start)%'")]
    assert len(lines) == 1, lines
    assert "wikipedia (corpus) on vega-bge-m3" in lines[0] and "3 request(s)" in lines[0]
    keep.reset()
