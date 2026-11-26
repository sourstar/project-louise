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
