"""app/vega/service.py — the receipts service: the bounded store, the runs
under their own names, the intake on its socket, the read-only HTTP API."""
from __future__ import annotations

import json
import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.hold.codex import canonical_json
from app.vega import runs as runs_mod
from app.vega.store import RunStore
from app.vega import service
from app.vega.service import ReceiptStore, Refused, Runs

RUN = {"driver": "cpu", "weights": "sha256:" + "11" * 32,
       "engine": {"name": "localai/fake", "build": "v1", "variant": None,
                  "image": "sha256:" + "22" * 32, "args": ["/b/run.sh", "--addr",
                                                       "127.0.0.1:${PORT}"],
                  "env": {}, "device": "cpu", "options": {"ModelFile": "${WEIGHTS}"}}}
RUN_ID = runs_mod.run_id(RUN)


def receipt(i: int = 0, **kw: str) -> dict[str, str]:
    r = {"id": f"{i:032x}", "run_id": RUN_ID, "input_digest": "sha256:" + "33" * 32,
         "vector_digest": "sha256:" + "44" * 32}
    return r | kw


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_a_receipt_lives_its_time_and_no_longer():
    clock = Clock()
    store = ReceiptStore(ttl_s=600, clock=clock)
    assert store.put(receipt(1)) is True
    clock.t += 599
    assert store.get([receipt(1)["id"]]) == [receipt(1)]
    clock.t += 2
    assert store.get([receipt(1)["id"]]) == [] and len(store) == 0


def test_the_store_is_bounded_oldest_first():
    store = ReceiptStore(max_receipts=3, clock=Clock())
    for i in range(5):
        store.put(receipt(i))
    assert len(store) == 3
    assert [r["id"] for r in store.get([receipt(i)["id"] for i in range(5)])] == \
        [receipt(i)["id"] for i in (2, 3, 4)]


def test_an_id_is_one_time_the_same_receipt_again_is_not_a_second():
    store = ReceiptStore(clock=Clock())
    assert store.put(receipt(1)) is True
    assert store.put(receipt(1)) is False, "a wrapper resending after a lost answer"
    with pytest.raises(Refused, match="one-time") as e:
        store.put(receipt(1, vector_digest="sha256:" + "55" * 32))
    assert e.value.code == service.CONFLICT
    assert store.get([receipt(1)["id"]]) == [receipt(1)], "the first stands"


def test_a_run_is_taken_only_under_its_own_name(tmp_path):
    runs = Runs(RunStore(tmp_path / "runs.sqlite"))
    with pytest.raises(Refused, match="not H"):
        runs.put("sha256:" + "00" * 32, RUN)
    with pytest.raises(Refused, match="exactly driver"):
        runs.put(RUN_ID, {"driver": "cpu"})
    runs.put(RUN_ID, RUN)
    assert runs.known(RUN_ID) and runs.get(RUN_ID) == RUN
    again = Runs(RunStore(tmp_path / "runs.sqlite"))
    assert again.known(RUN_ID), "kept across a restart: a statement may come later"


@pytest.mark.parametrize("line, code", [
    (b"not json", service.INVALID),
    (b'{"kind":"receipt","kind":"run"}', service.INVALID),
    (b'{"kind":"nope"}', service.INVALID),
    (b'{"kind":"receipt","id":"x","run_id":"y","input_digest":"z","vector_digest":"w"}',
     service.INVALID),
    (b'{"kind":"run","run_id":"sha256:00","run":{},"x":1.5}', service.INVALID),
])
def test_the_intake_refuses_what_is_not_a_message(tmp_path, line, code):
    reply = service.handle(line, ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite")))
    assert reply["ok"] is False and reply["code"] == code


def test_a_receipt_for_a_run_never_told_is_refused_until_it_is(tmp_path):
    receipts, runs = ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite"))
    line = canonical_json({"kind": "receipt", **receipt(7)})
    assert service.handle(line, receipts, runs)["code"] == service.UNKNOWN_RUN
    told = canonical_json({"kind": "run", "run_id": RUN_ID, "run": RUN})
    assert service.handle(told, receipts, runs) == {"ok": True}
    assert service.handle(line, receipts, runs) == {"ok": True}
    assert receipts.get([receipt(7)["id"]]) == [receipt(7)]


def _ask(path, *messages):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(str(path))
        f = s.makefile("rb")
        out = []
        for m in messages:
            s.sendall(canonical_json(m) + b"\n")
            out.append(json.loads(f.readline()))
        return out


def test_the_intake_answers_on_its_socket_one_line_each(vega):
    replies = _ask(vega.socket, {"kind": "run", "run_id": RUN_ID, "run": RUN},
                   {"kind": "receipt", **receipt(3)}, {"kind": "receipt", **receipt(3)})
    assert replies == [{"ok": True}] * 3
    assert vega.receipts.get([receipt(3)["id"]]) == [receipt(3)]
    assert oct(vega.socket.stat().st_mode & 0o777) == oct(service.SOCKET_MODE)


def test_a_stale_socket_is_replaced_and_a_file_is_not(tmp_path):
    import asyncio
    from app.vega.service import Intake
    stale = tmp_path / "s.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(stale))
    s.close()
    intake = Intake(stale, ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite")),
                    log=lambda _m: None)

    async def cycle():
        await intake.start()
        await intake.close()
    asyncio.run(cycle())
    plain = tmp_path / "plain"
    plain.write_text("not a socket")
    other = Intake(plain, ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite")))
    with pytest.raises(RuntimeError, match="not a socket"):
        asyncio.run(other.start())
    assert plain.read_text() == "not a socket"


def test_the_http_api_reads_receipts_and_runs(tmp_path):
    receipts, runs = ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite"))
    runs.put(RUN_ID, RUN)
    receipts.put(receipt(1))
    app = FastAPI()
    app.include_router(service.router(receipts, runs))
    http = TestClient(app)
    got = http.get("/vega/receipts", params={"ids": f"{receipt(1)['id']},{receipt(2)['id']}"})
    assert got.status_code == 200 and got.json() == {"receipts": [receipt(1)]}, \
        "the missing one is simply absent"
    assert http.get(f"/vega/runs/{RUN_ID}").json() == RUN
    assert http.get("/vega/runs/sha256:" + "00" * 32).status_code == 404
    assert http.get("/vega/receipts", params={"ids": "nothex"}).status_code == 400
    assert http.get("/vega/receipts").status_code == 400
    many = ",".join(f"{i:032x}" for i in range(service.MAX_IDS + 1))
    assert http.get("/vega/receipts", params={"ids": many}).status_code == 400
    assert http.post("/vega/receipts").status_code == 405, "read-only"


# ---- the trees: a backend's per-file list under its own digest (vega.md §12) ----

TREE = {"run.sh": {"type": "file", "exec": True, "size": 10, "sha256": "ab" * 32},
        "lib": {"type": "dir"},
        "lib/libx.so.1": {"type": "link", "target": "libx.so.1.2.3"}}
IMAGE = "sha256:" + __import__("hashlib").sha256(canonical_json(TREE)).hexdigest()


def test_a_tree_is_kept_under_its_own_digest_and_read_back(tmp_path):
    """`image` must be H of the list's canonical JSON — the wrapper's
    `tree_digest` — or the list is not the one that digest names."""
    runs = Runs(RunStore(tmp_path / "runs.sqlite"))
    with pytest.raises(Refused, match="not H of the tree"):
        runs.put_tree("sha256:" + "00" * 32, TREE)
    with pytest.raises(Refused, match="non-empty object"):
        runs.put_tree(IMAGE, {})
    with pytest.raises(Refused, match="type is one of"):
        runs.put_tree(IMAGE, {"x": {"type": "socket"}})
    with pytest.raises(Refused, match="is a digest"):
        runs.put_tree("not-a-digest", TREE)
    runs.put_tree(IMAGE, TREE)
    assert runs.get_tree(IMAGE) == TREE
    assert runs.get_tree("sha256:" + "00" * 32) is None
    again = Runs(RunStore(tmp_path / "runs.sqlite"))
    assert again.get_tree(IMAGE) == TREE, "kept across a restart, like a run"
    runs.put_tree(IMAGE, TREE)
    assert again.get_tree(IMAGE) == TREE, "the same bytes again are the same row"
    # the wrapper's half (operator, 2026-10-31): its files and its `cuts` setting
    with_wrapper = {**TREE, "/vega/vega_wrapper.py": {"type": "file", "size": 3, "sha256": "cd" * 32},
                    "/vega/vega.json#cuts": {"type": "setting", "value": "tokenizer"}}
    image = "sha256:" + __import__("hashlib").sha256(canonical_json(with_wrapper)).hexdigest()
    runs.put_tree(image, with_wrapper)
    assert runs.get_tree(image) == with_wrapper
    from app.vega import trees
    d = trees.diff(TREE, with_wrapper)
    assert "+ /vega/vega.json#cuts  setting 'tokenizer'" in trees.lines(d)


def test_the_intake_takes_a_tree_message_and_the_http_api_serves_it(tmp_path):
    receipts, runs = ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite"))
    assert service.handle(canonical_json({"kind": "tree", "image": IMAGE, "tree": TREE}),
                          receipts, runs) == {"ok": True}
    bad = service.handle(canonical_json({"kind": "tree", "image": IMAGE, "tree": TREE, "x": 1}),
                         receipts, runs)
    assert bad["ok"] is False and bad["code"] == service.INVALID
    wrong = service.handle(canonical_json({"kind": "tree", "image": "sha256:" + "00" * 32,
                                           "tree": TREE}), receipts, runs)
    assert wrong["ok"] is False and "not H of the tree" in wrong["error"]
    app = FastAPI()
    app.include_router(service.router(receipts, runs))
    http = TestClient(app)
    assert http.get(f"/vega/trees/{IMAGE}").json() == TREE
    assert http.get("/vega/trees/sha256:" + "00" * 32).status_code == 404
    assert http.get("/vega/trees/nope").status_code == 404
    assert service.MAX_LINE >= 16 << 20, "a tree's list is megabytes: the line limit takes it"


# ---- the input lengths against each run's ubatch (operator, 2026-11-12: "for
#      future trimming improvements we should gracefully log when a text is splitted")

def passes(**kw) -> dict:
    b = dict.fromkeys(service.PASS_BUCKETS, 0) | {"1024": 9, "4096": 1}
    m = {"kind": "passes", "run_id": RUN_ID, "ubatch": 2048, "inputs": 10, "over": 1,
         "longest": 3412, "buckets": b}
    return m | kw


def test_a_count_over_the_ubatch_is_said_once_and_read_back(tmp_path):
    receipts, runs, counts = ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite")), service.Passes()
    notes: list[str] = []
    line = canonical_json(passes())
    assert service.handle(line, receipts, runs, counts, notes)["code"] == service.UNKNOWN_RUN
    service.handle(canonical_json({"kind": "run", "run_id": RUN_ID, "run": RUN}), receipts, runs)
    assert service.handle(line, receipts, runs, counts, notes) == {"ok": True}
    (note,) = notes
    assert "1 of 10 input(s) since its load over its ubatch of 2,048 tokens" in note
    assert "the longest 3,412 tokens, 2 passes" in note and RUN_ID[7:19] in note
    service.handle(line, receipts, runs, counts, notes)
    more = passes(inputs=20, buckets=passes()["buckets"] | {"1024": 19})
    service.handle(canonical_json(more), receipts, runs, counts, notes)
    assert len(notes) == 1, "the log says it only when the inputs over the ubatch grew"
    service.handle(canonical_json(passes(inputs=21, over=2, buckets=more["buckets"] | {"4096": 2})),
                   receipts, runs, counts, notes)
    assert len(notes) == 2 and "(+1;" in notes[-1]
    reload = passes(inputs=3, over=1, buckets=dict.fromkeys(service.PASS_BUCKETS, 0) | {"4096": 1, "256": 2})
    service.handle(canonical_json(reload), receipts, runs, counts, notes)
    assert len(notes) == 3 and "1 of 3" in notes[-1], "a smaller count is a new load: counted afresh"
    app = FastAPI()
    app.include_router(service.router(receipts, runs, counts))
    got = TestClient(app).get("/vega/passes").json()
    assert got["buckets"] == list(service.PASS_BUCKETS)
    (row,) = got["runs"]
    assert {k: row[k] for k in ("run_id", "ubatch", "inputs", "over", "longest")} == \
        {"run_id": RUN_ID, "ubatch": 2048, "inputs": 3, "over": 1, "longest": 3412}


@pytest.mark.parametrize("bad", [
    {"x": 1}, {"ubatch": -1}, {"inputs": 1.5}, {"over": True}, {"over": 11},
    {"buckets": {"256": 10}}, {"buckets": dict.fromkeys(service.PASS_BUCKETS, 0)},
    {"run_id": "nope"},
])
def test_a_count_that_does_not_add_up_is_refused(tmp_path, bad):
    receipts, runs, counts = ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite")), service.Passes()
    service.handle(canonical_json({"kind": "run", "run_id": RUN_ID, "run": RUN}), receipts, runs)
    msg = passes() | bad
    raw = json.dumps(msg).encode() if isinstance(bad.get("inputs"), float) else canonical_json(msg)
    reply = service.handle(raw, receipts, runs, counts)
    assert reply["ok"] is False and reply["code"] in (service.INVALID, service.UNKNOWN_RUN)
    assert counts.get() == []


def test_a_service_without_counts_refuses_them_as_a_kind_it_does_not_take(tmp_path):
    receipts, runs = ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite"))
    reply = service.handle(canonical_json(passes()), receipts, runs)
    assert reply["ok"] is False and "kind is run, receipt" in reply["error"], \
        "the wrapper reads this as a service that does not take counts, and stops"


# ---- the calls against each run's keep (operator, 2026-11-10: "we can log
#      expired stay warms for improvement"; docs/notes/idle-context-brief.md §1.3)

def keeps(**kw) -> dict:
    g = dict.fromkeys(service.GAP_BUCKETS, 0) | {"1": 30, "16": 6, "256": 4}
    m = {"kind": "keeps", "run_id": RUN_ID, "on_demand": True, "calls": 50, "keyed": 50,
         "busy": 10, "failed": 0, "told": 50, "created": 4, "created_ms": 160, "gaps": g}
    return m | kw


def test_calls_that_met_an_expired_keep_are_said_when_they_grew_and_read_back(tmp_path):
    receipts, runs, counts = ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite")), service.Keeps()
    notes: list[str] = []
    line = canonical_json(keeps())
    assert service.handle(line, receipts, runs, None, notes, counts)["code"] == service.UNKNOWN_RUN
    service.handle(canonical_json({"kind": "run", "run_id": RUN_ID, "run": RUN}), receipts, runs)
    assert service.handle(line, receipts, runs, None, notes, counts) == {"ok": True}
    (note,) = notes
    assert "4 of 50 call(s) since its load met an expired keep" in note and RUN_ID[7:19] in note
    assert "40 ms a creation on average" in note and "10 call(s) sent while the backend worked" in note
    service.handle(line, receipts, runs, None, notes, counts)
    more = keeps(calls=60, keyed=60, told=60, gaps=keeps()["gaps"] | {"1": 40})
    service.handle(canonical_json(more), receipts, runs, None, notes, counts)
    assert len(notes) == 1, "the log says it only when the created contexts grew"
    service.handle(canonical_json(more | {"calls": 61, "keyed": 61, "told": 61, "created": 5,
                                          "gaps": more["gaps"] | {"256": 5}}),
                   receipts, runs, None, notes, counts)
    assert len(notes) == 2 and "(+1;" in notes[-1]
    reload = keeps(calls=2, keyed=2, told=2, busy=0, created=1, created_ms=41,
                   gaps=dict.fromkeys(service.GAP_BUCKETS, 0) | {"more": 1, "2": 1})
    service.handle(canonical_json(reload), receipts, runs, None, notes, counts)
    assert len(notes) == 3 and "1 of 2" in notes[-1], "a smaller count is a new load: counted afresh"
    app = FastAPI()
    app.include_router(service.router(receipts, runs, keeps=counts))
    got = TestClient(app).get("/vega/keeps").json()
    assert got["buckets"] == list(service.GAP_BUCKETS) and len(got["buckets"]) == 18
    (row,) = got["runs"]
    assert {k: row[k] for k in ("run_id", "on_demand", "calls", "told", "created")} == \
        {"run_id": RUN_ID, "on_demand": True, "calls": 2, "told": 2, "created": 1}
    assert row["gaps"]["more"] == 1


def test_a_load_that_is_not_on_demand_is_counted_and_never_said(tmp_path):
    receipts, runs, counts = ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite")), service.Keeps()
    service.handle(canonical_json({"kind": "run", "run_id": RUN_ID, "run": RUN}), receipts, runs)
    notes: list[str] = []
    msg = keeps(on_demand=False, keyed=0, told=0, created=0, created_ms=0)
    assert service.handle(canonical_json(msg), receipts, runs, None, notes, counts) == {"ok": True}
    assert notes == [] and counts.get()[0]["gaps"]["16"] == 6, "its gaps are what a keep must outlast"


@pytest.mark.parametrize("bad", [
    {"x": 1}, {"calls": -1}, {"calls": 1.5}, {"busy": True}, {"on_demand": 1},
    {"calls": 51}, {"keyed": 51}, {"told": 51}, {"created": 51}, {"told": 3},
    {"created": 0}, {"gaps": {"1": 40}}, {"gaps": dict.fromkeys(service.GAP_BUCKETS, 0)},
    {"run_id": "nope"},
])
def test_a_keep_count_that_does_not_add_up_is_refused(tmp_path, bad):
    receipts, runs, counts = ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite")), service.Keeps()
    service.handle(canonical_json({"kind": "run", "run_id": RUN_ID, "run": RUN}), receipts, runs)
    msg = keeps() | bad
    raw = json.dumps(msg).encode() if isinstance(bad.get("calls"), float) else canonical_json(msg)
    reply = service.handle(raw, receipts, runs, None, None, counts)
    assert reply["ok"] is False and reply["code"] in (service.INVALID, service.UNKNOWN_RUN)
    assert counts.get() == []


def test_a_service_without_keep_counts_refuses_them_as_a_kind_it_does_not_take(tmp_path):
    receipts, runs = ReceiptStore(), Runs(RunStore(tmp_path / "r.sqlite"))
    reply = service.handle(canonical_json(keeps()), receipts, runs, service.Passes())
    assert reply["ok"] is False and "kind is run, receipt" in reply["error"], \
        "the wrapper reads this as a service that does not take the count, and stops"
    assert service.handle(canonical_json(passes()), receipts, runs)["error"] == reply["error"]
