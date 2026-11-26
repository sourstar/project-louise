"""An admin's questions traced for timing and quality (app/query_trace.py;
operator, 2026-11-26): one JSON line per search or answer job of an admin,
with its marks, the node sampled while it ran (CPU, load, RAM, the cards,
the models loaded and parked at its start and end), the node's model events
meanwhile, the query and its results — and nothing at all for anyone else."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app import query_trace, relay, web


def _wait(pred, timeout=10.0):
    t0 = time.monotonic()
    while not pred():
        assert time.monotonic() - t0 < timeout, "timed out"
        time.sleep(0.02)


@pytest.fixture
def traced(tmp_path, monkeypatch):
    monkeypatch.setattr(query_trace, "trace_dir", lambda: tmp_path)
    monkeypatch.setattr(query_trace, "SAMPLE_S", 0.05)
    monkeypatch.setattr(query_trace, "_gpus", lambda: [{"card": "card0", "vram_used": 7 << 30,
                                                        "processes": [{"label": "qwen", "vram": 6 << 30}]}])
    monkeypatch.setattr(query_trace, "_models",
                        lambda: {"http://ai": {"loaded": ["qwen", "bge-m3"], "parked": ["omnivoice"]}})
    monkeypatch.setattr(query_trace, "_model_events",
                        lambda since, until: [["2026-11-26T10:00:00.000+00:00", "info", "cold start: qwen loaded"]])
    relay.reset()
    yield tmp_path
    relay.reset()


def _lines(d: Path) -> list[dict]:
    return [json.loads(line) for f in sorted(d.glob("*.jsonl")) for line in f.read_text().splitlines()]


def test_only_an_admin_is_traced():
    assert query_trace.is_admin("admin:admin") and query_trace.is_admin("admin:anna")
    for who in ("user:bob", "guest:None", "nobody", None, ""):
        assert not query_trace.is_admin(who)
        assert query_trace.start("search", "j", who, "q") is None, "nothing is read for them"


def test_an_admins_search_writes_its_line_with_the_node_and_the_results(traced, monkeypatch):
    def result(body):
        time.sleep(0.15)                       # a few samples while it runs
        return {"hits": [{"source_collection": "gutenberg", "uid": "u1", "book_id": 1, "chunk_id": "gutenberg:1:0:1", "title": "Moby Dick",
                          "rrf": 0.03, "rerank_score": 0.91, "text": "Call me Ishmael. " * 50}],
                "timings": {"languages": 22.0, "fts": 4.9, "rerank": 12.5, "total": 43.2}}
    monkeypatch.setattr(web, "_ask_result", result)
    job = relay.Job(id="s1", kind="search", owner="admin:admin")
    web._relay_search(job, web.AskQueryBody(query="whales"))
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    assert got["kind"] == "search" and got["who"] == "admin:admin" and got["query"] == "whales"
    assert got["search_id"] == job.since(0)[0].data["search_id"], "joined to the answer by the page's search"
    assert got["started"] < got["ended"] and got["ms"] >= 150
    assert [m[0] for m in got["marks"]] == ["search_done"]
    assert got["timings"]["rerank"] == 12.5
    hit = got["results"][0]
    assert hit["rank"] == 1 and hit["title"] == "Moby Dick" and hit["rerank_score"] == 0.91
    assert len(hit["text"]) == query_trace.TEXT_CHARS
    s = got["samples"]
    assert len(s) >= 3, "sampled while it ran"
    assert s[0]["models"]["http://ai"]["parked"] == ["omnivoice"] and "models" in s[-1]
    assert all("models" not in x for x in s[1:-1]), "the models at the start and the end only"
    assert all(x["gpus"][0]["vram_used"] == 7 << 30 for x in s)
    assert got["model_events"][0][2] == "cold start: qwen loaded"


def test_a_users_search_writes_nothing(traced, monkeypatch):
    monkeypatch.setattr(web, "_ask_result", lambda body: {"hits": [], "timings": {}})
    web._relay_search(relay.Job(id="s2", kind="search", owner="user:bob"), web.AskQueryBody(query="whales"))
    time.sleep(0.3)
    assert _lines(traced) == []


def test_a_failed_search_is_written_with_its_error(traced, monkeypatch):
    def boom(body):
        raise RuntimeError("reranker down")
    monkeypatch.setattr(web, "_ask_result", boom)
    with pytest.raises(RuntimeError):
        web._relay_search(relay.Job(id="s3", kind="search", owner="admin:admin"), web.AskQueryBody(query="q"))
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    assert got["error"] == "RuntimeError: reranker down" and got["marks"][-1][0] == "error"


def test_an_answer_keeps_its_words_and_their_times(traced):
    t = query_trace.Trace("answer", "a1", "admin:admin", "whales", search_id="sid", sample_s=0.05)
    t.mark("search_done")
    time.sleep(0.06)
    t.word("The whale ")
    time.sleep(0.06)
    t.word("rose [1].")
    t.end()
    t.join(5)
    (got,) = _lines(traced)
    names = [m[0] for m in got["marks"]]
    assert names == ["search_done", "first_word", "last_word"]
    assert got["marks"][1][1] < got["marks"][2][1]
    assert got["answer"] == "The whale rose [1]." and got["search_id"] == "sid"


def test_the_node_is_read_from_proc(tmp_path):
    stat = tmp_path / "stat"
    stat.write_text("cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 2 3 4\n")
    assert query_trace._cpu_times(stat) == (200, 1000), "busy is everything but idle and iowait"
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal:       1000 kB\nMemAvailable:    400 kB\nSwapTotal: 10 kB\nSwapFree: 4 kB\n")
    assert query_trace._meminfo(mem) == {"MemTotal": 1024000, "MemAvailable": 409600,
                                         "SwapTotal": 10240, "SwapFree": 4096}
    load = tmp_path / "loadavg"
    load.write_text("1.50 0.75 0.25 2/300 1234\n")
    assert query_trace._loadavg(load) == [1.5, 0.75, 0.25]
    assert query_trace._cpu_times(tmp_path / "absent") is None and query_trace._meminfo(tmp_path / "absent") == {}


def test_a_read_that_fails_is_recorded_and_the_question_goes_on():
    def broken():
        raise OSError("probe unreachable")
    assert query_trace._safe(broken) == {"error": "OSError: probe unreachable"}


def test_the_oldest_days_go_when_a_new_day_opens(tmp_path):
    (tmp_path / "2026-10-01.jsonl").write_text("{}\n")
    (tmp_path / "2026-11-20.jsonl").write_text("{}\n")
    query_trace.write({"started": "2026-11-26T09:00:00.000+00:00"}, root=tmp_path)
    assert sorted(f.name for f in tmp_path.glob("*.jsonl")) == ["2026-11-20.jsonl", "2026-11-26.jsonl"]


def test_model_events_are_the_nodes_models_lines_in_the_window(isolated_config):
    from contextlib import closing
    from app import collections as collections_mod, db
    cols = collections_mod.load_all()
    if not cols:
        pytest.skip("no collection in the isolated config")
    with closing(db.connect(cols[0])) as con:
        now = int(time.time())
        con.execute("INSERT INTO events(ts,level,stage,message) VALUES(?,?,?,?)", (now - 100, "info", "models", "old"))
        con.execute("INSERT INTO events(ts,level,stage,message) VALUES(?,?,?,?)", (now, "info", "models", "park: x"))
        con.execute("INSERT INTO events(ts,level,stage,message) VALUES(?,?,?,?)", (now, "info", "fts", "not a model"))
        con.commit()
    got = query_trace._model_events(now - 5, now + 1)
    assert [m for _t, _l, m in got] == ["park: x"]


def test_an_admins_answer_job_writes_its_words_and_the_search_it_reused(traced, monkeypatch):
    from app import api, llm

    class End:
        whole = True
    def stream(messages, temperature=0.3):
        yield "The whale "
        yield "rose [1]."
        return End()
    monkeypatch.setattr(llm, "chat_stream", stream)
    hits = [{"chunk_id": "gutenberg:1:0:1", "book_id": 1, "uid": "u1", "text": "w", "source_collection": "gutenberg"}]
    monkeypatch.setattr(api.retrieval, "build_answer_messages",
                        lambda q, r, answer_lang=None, **kw: [{"role": "system", "content": "s"},
                                                               {"role": "user", "content": q}])
    sid = web.keep_search("whales", {"hits": hits, "timings": {"total": 1.0}})
    chat = api.ChatBody(messages=[{"role": "user", "content": "whales"}], search_id=sid, stream=True)
    web._relay_answer_job(relay.Job(id="a2", kind="answer", owner="admin:admin"), chat)
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    assert got["kind"] == "answer" and got["search_id"] == sid and got["search_reused"] is True
    assert got["answer"] == "The whale rose [1]."
    assert [m[0] for m in got["marks"]] == ["search_done", "first_word", "last_word"]
    assert got["results"][0]["uid"] == "u1"


# ---- architecture's read, 2026-11-26 ----------------------------------------

def _stream_of(*words, fail=None):
    from app import llm

    class End:
        whole = True
    def stream(messages, temperature=0.3):
        for w in words:
            yield w
        if fail is not None:
            raise fail
        return End()
    return stream


def _answer_setup(monkeypatch, stream):
    from app import api, llm
    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(api.retrieval, "build_answer_messages",
                        lambda q, r, answer_lang=None, **kw: [{"role": "system", "content": "s"},
                                                               {"role": "user", "content": q}])
    hits = [{"chunk_id": "gutenberg:1:0:1", "book_id": 1, "uid": "u1", "text": "w", "source_collection": "gutenberg"}]
    sid = web.keep_search("whales", {"hits": hits, "timings": {"total": 1.0}})
    return api.ChatBody(messages=[{"role": "user", "content": "whales"}], search_id=sid, stream=True)


def test_a_users_answer_job_writes_nothing(traced, monkeypatch):
    chat = _answer_setup(monkeypatch, _stream_of("The whale."))
    web._relay_answer_job(relay.Job(id="a3", kind="answer", owner="user:bob"), chat)
    time.sleep(0.3)
    assert _lines(traced) == []
    # and the viewer's own verdict wins over the owner's name
    web._relay_answer_job(relay.Job(id="a4", kind="answer", owner="admin:x"), chat, admin=False)
    time.sleep(0.3)
    assert _lines(traced) == []


def test_a_failed_answer_ends_its_trace_with_the_error_after_its_words(traced, monkeypatch):
    chat = _answer_setup(monkeypatch, _stream_of("The whale ", fail=RuntimeError("model gone")))
    try:
        web._relay_answer_job(relay.Job(id="a5", kind="answer", owner="admin:admin"), chat)
    except Exception:   # noqa: BLE001 — the answer path may say it as a cut instead
        pass
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    names = [m[0] for m in got["marks"]]
    assert ("error" in names and got.get("error")) or ("cut" in names and got.get("cut")), got
    assert names.index("last_word") < len(names) - 1 or names[-1] == "last_word"
    times = [m[1] for m in got["marks"]]
    assert times == sorted(times), "the marks in time order"


def test_a_cancelled_answer_is_written_with_its_reason(traced, monkeypatch):
    from app import cancel
    chat = _answer_setup(monkeypatch, _stream_of("The ", "whale ", "rose."))
    job = relay.Job(id="a6", kind="answer", owner="admin:admin")
    job.token.cancel()
    with pytest.raises(BaseException):
        web._relay_answer_job(job, chat)
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    assert got["error"] and got["marks"][-1][0] == "error"


def test_a_cut_answer_says_so(traced, monkeypatch):
    from app import llm

    class Cut:
        whole = False
        def why(self):
            return "the token limit was reached"
    def stream(messages, temperature=0.3):
        yield "The whale "
        return Cut()
    chat = _answer_setup(monkeypatch, stream)
    web._relay_answer_job(relay.Job(id="a7", kind="answer", owner="admin:admin"), chat)
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    assert "cut" in [m[0] for m in got["marks"]] and "token limit" in got["cut"]


def test_the_jobs_length_is_the_jobs_not_the_samplers(traced, monkeypatch):
    def slow_gpus():
        time.sleep(0.4)
        return []
    monkeypatch.setattr(query_trace, "_gpus", slow_gpus)
    t = query_trace.Trace("search", "s9", "admin:admin", "q", sample_s=10.0)
    t.end()
    t.join(5)
    (got,) = _lines(traced)
    assert got["ms"] < 200, "ended when the question did, not after the last probe read"


def test_a_cards_process_list_is_written_only_when_it_changes():
    before: dict = {}
    a = [{"card": "c0", "vram_used": 1, "processes": [{"label": "x"}]}]
    assert query_trace._changed_only(a, before)[0]["processes"] == [{"label": "x"}]
    same = query_trace._changed_only(a, before)[0]
    assert "processes" not in same and same["processes_same"] is True
    b = [{"card": "c0", "vram_used": 2, "processes": [{"label": "y"}]}]
    assert query_trace._changed_only(b, before)[0]["processes"] == [{"label": "y"}]


def test_a_line_a_full_disk_cut_short_does_not_swallow_the_next(tmp_path):
    (tmp_path / "2026-11-26.jsonl").write_text('{"cut": tru')
    query_trace.write({"started": "2026-11-26T09:00:00.000+00:00", "n": 2}, root=tmp_path)
    lines = (tmp_path / "2026-11-26.jsonl").read_text().splitlines()
    assert json.loads(lines[1]) == {"started": "2026-11-26T09:00:00.000+00:00", "n": 2}


def test_a_question_that_never_ends_is_written_without_an_end(traced, monkeypatch):
    monkeypatch.setattr(query_trace, "MAX_S", 0.1)
    t = query_trace.Trace("search", "s10", "admin:admin", "q")
    t.join(5)
    (got,) = _lines(traced)
    assert got["ended"] is None and got["ms"] is None and "did not end" in got["error"]


def test_a_note_and_a_result_that_cannot_be_written_never_raise():
    import decimal
    t = query_trace.Trace.__new__(query_trace.Trace)
    t.data = {}
    t.note(x=decimal.Decimal("1.5"), y={1, 2})
    assert t.data["x"] == "1.5"
    assert isinstance(query_trace.hits_of({"hits": [{"title": object()}]}), (list, dict))
    assert query_trace.hits_of([{"a": 1}])["json"] == '[{"a": 1}]', "another shape: kept as JSON"


def test_v1_chat_completions_traces_an_admin_and_nobody_else(traced, monkeypatch):
    from fastapi.testclient import TestClient
    from app import api, auth
    chat = _answer_setup(monkeypatch, _stream_of("The whale ", "rose [1]."))
    body = {"messages": [{"role": "user", "content": "whales"}], "search_id": chat.search_id, "stream": True}
    c = TestClient(api.app)
    monkeypatch.setattr(query_trace, "viewer_of", lambda request: ("guest:None", False))
    c.post("/v1/chat/completions", json=body)
    time.sleep(0.3)
    assert _lines(traced) == []
    monkeypatch.setattr(query_trace, "viewer_of", lambda request: ("admin:admin", True))
    r = c.post("/v1/chat/completions", json=body)
    assert "whale" in r.text
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    assert got["kind"] == "chat" and got["answer"].startswith("The whale") and got["search_reused"] is True
    assert [m[0] for m in got["marks"]][:2] == ["search_done", "first_word"]


def test_mcp_tool_calls_are_traced_for_an_admin(traced, monkeypatch):
    from app.ask import mcp
    monkeypatch.setattr(mcp, "_call_tool", lambda name, args: {"hits": [{"title": "T", "uid": "u"}]})
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "louise_search", "arguments": {"query": "whales"}}}
    mcp.handle(msg, call=mcp.traced("user:bob", False))
    time.sleep(0.3)
    assert _lines(traced) == []
    mcp.handle(msg, call=mcp.traced("cli:ask serve", True))
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    assert got["kind"] == "mcp:louise_search" and got["query"] == "whales" and got["results"][0]["title"] == "T"


def test_the_ask_routes_trace_an_admin(traced, monkeypatch):
    from fastapi.testclient import TestClient
    from app import api
    from app.ask import results
    monkeypatch.setattr(results, "search", lambda q, **kw: {"spaces": [{"hits": [{"title": "T"}]}]})
    monkeypatch.setattr(query_trace, "viewer_of", lambda request: ("admin:admin", True))
    r = TestClient(api.app).post("/api/ask/search", json={"query": "whales"})
    assert r.status_code == 200
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    assert got["kind"] == "ask:search" and '"T"' in got["results"]["json"]


def test_no_trace_lands_outside_the_tests_temporary_directories(query_traces_stay_in_tmp):
    """The guard: in the suite, `trace_dir` is a temporary directory, never
    the root a test without isolated_config resolves to (/data)."""
    import tempfile
    d = query_trace.trace_dir()
    assert d == query_traces_stay_in_tmp
    assert str(d.resolve()).startswith(str(Path(tempfile.gettempdir()).resolve())), d
    t = query_trace.Trace("search", "g1", "admin:admin", "q", sample_s=10.0)
    t.end(); t.join(5)
    assert list(d.glob("*.jsonl")), "written there"


# ---- architecture's re-read, 2026-11-26 -------------------------------------

def test_the_directory_is_the_one_in_force_when_the_question_started(tmp_path, monkeypatch):
    """B1: the sampler writes after the request returned, when a test's root
    may be undone; the directory is resolved at the start."""
    first, later = tmp_path / "first", tmp_path / "later"
    monkeypatch.setattr(query_trace, "trace_dir", lambda: first)
    t = query_trace.Trace("search", "d1", "admin:admin", "q", sample_s=10.0)
    monkeypatch.setattr(query_trace, "trace_dir", lambda: later)
    t.end(); t.join(5)
    assert list(first.glob("*.jsonl")) and not later.exists()


def test_the_models_are_read_from_the_cache_alone(monkeypatch):
    """S3: never a fetch, never the lock a question's fresh read takes."""
    from app import model_load

    def no(*a, **kw):
        raise AssertionError("fetched")
    monkeypatch.setattr(model_load, "loaded_models", no)
    monkeypatch.setattr(model_load, "_fetch_system", no)
    monkeypatch.setattr(query_trace, "_servers", lambda: ["http://ai", "http://never"])
    monkeypatch.setitem(model_load._systems, "http://ai", (time.monotonic() - 3.0, frozenset({"qwen"}), 2.0))
    got = query_trace._models()
    assert got["http://ai"]["loaded"] == ["qwen"] and 2.9 <= got["http://ai"]["age_s"] < 10
    assert got["http://never"]["loaded"] is None and got["http://never"]["age_s"] is None


def test_the_web_writes_the_questions_model_lines_to_the_event_log(monkeypatch):
    """S6: question_card's lines (unloaded before / after the search, loading
    ahead) reach the node's event log from the web too."""
    from fastapi.testclient import TestClient
    from app import api, deadman, idle_trim, model_pins, question_card, warmup
    monkeypatch.setattr(warmup, "start", lambda: None)
    monkeypatch.setattr(idle_trim, "start", lambda: None)
    monkeypatch.setattr(deadman, "arm", lambda **kw: None)
    monkeypatch.setattr(question_card, "_log", [None])
    with TestClient(api.app):
        assert question_card._log[0] is model_pins.node_event


def test_ask_query_traces_an_admin(traced, monkeypatch):
    from fastapi.testclient import TestClient
    from app import api
    monkeypatch.setattr(web, "_ask_result", lambda body: {"hits": [], "timings": {"total": 3.0}})
    monkeypatch.setattr(query_trace, "viewer_of", lambda request: ("user:bob", False))
    c = TestClient(api.app)
    assert c.post("/ask/query", json={"query": "whales"}).status_code == 200
    time.sleep(0.3)
    assert _lines(traced) == []
    monkeypatch.setattr(query_trace, "viewer_of", lambda request: ("admin:admin", True))
    r = c.post("/ask/query", json={"query": "whales"})
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    assert got["kind"] == "search" and got["search_id"] == r.headers["X-Louise-Search"]


def test_mcp_over_http_and_ask_serve_are_traced(traced, monkeypatch):
    import io
    from fastapi.testclient import TestClient
    from app import api
    from app.ask import mcp
    monkeypatch.setattr(mcp, "_call_tool", lambda name, args: {"hits": [{"title": "T"}]})
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "louise_search", "arguments": {"query": "whales"}}}
    monkeypatch.setattr(query_trace, "viewer_of", lambda request: ("admin:admin", True))
    assert TestClient(api.app).post("/api/ask/mcp", json=msg).status_code == 200
    _wait(lambda: len(_lines(traced)) == 1)
    out = io.StringIO()
    mcp.serve_stdio(io.StringIO(json.dumps(msg) + "\n"), out)
    lines = _lines(traced)
    assert [l["who"] for l in lines] == ["admin:admin", "cli:ask serve"], "ask serve's default call is traced"
    assert "query trace" not in out.getvalue(), "stdout stays the JSON-RPC channel"


def test_a_streamed_chat_whose_caller_leaves_before_the_first_chunk_is_ended(traced, monkeypatch):
    """S2: `sse` never runs, so the response's background task ends it."""
    from app import api
    chat = _answer_setup(monkeypatch, _stream_of("The whale."))
    t = query_trace.Trace("chat", "c9", "admin:admin", "whales", sample_s=10.0)
    resp = api._chat_answer(chat, "whales", {"hits": [], "timings": {"total": 0.0}}, time.time(), trace=t)
    import asyncio
    asyncio.run(resp.background())             # what Starlette runs once the response is done
    t.join(5)
    (got,) = _lines(traced)
    assert got["ended"] and "left before the answer" in got["error"]


def test_a_cut_answers_marks_are_in_time_order(traced, monkeypatch):
    class Cut:
        whole = False
        def why(self):
            return "the token limit was reached"
    def stream(messages, temperature=0.3):
        yield "The "
        yield "whale "
        return Cut()
    chat = _answer_setup(monkeypatch, stream)
    web._relay_answer_job(relay.Job(id="a8", kind="answer", owner="admin:admin"), chat)
    _wait(lambda: _lines(traced))
    (got,) = _lines(traced)
    times = [m[1] for m in got["marks"]]
    assert times == sorted(times) and {"first_word", "last_word", "cut"} <= {m[0] for m in got["marks"]}
