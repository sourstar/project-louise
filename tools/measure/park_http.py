#!/usr/bin/env python3
"""Parking, the two halves together, through LocalAI's HTTP alone (step 2,
docs/notes/parking-step-2.md §8): the SERVER part (staged 0027) parks a model
with `POST /backend/park` and brings it back before the next request, the
ENGINE part (staged 0023) moves the weights. Nothing here talks to a backend:
it is what a caller that manages the device - Louise's keeper - will do, and
it needs nothing inside the model server's container.

On a COPY of a model, never a live one: a model on the park engine, context on
demand, whose file says `park: true` and whose env has `GGML_SYCL_PARKABLE=1`
(and `GGML_SYCL_PARK_VERIFY=1`, so the engine reads the card's bytes back at
every return). Nothing parks while LocalAI's `park_host_floor_bytes` is 0.

    python3 -I tools/measure/park_http.py state   SERVER [MODEL]
    python3 -I tools/measure/park_http.py floor   SERVER BYTES        # the runtime setting; 0 turns parking off
    python3 -I tools/measure/park_http.py park    SERVER MODEL        # one call, its answer whole
    python3 -I tools/measure/park_http.py cycles  SERVER MODEL -n 20 --return request
    python3 -I tools/measure/park_http.py cycles  SERVER MODEL -n 20 --return load
    python3 -I tools/measure/park_http.py refused SERVER MODEL        # the copy's env: GGML_SYCL_PARK_REFUSE=2
    python3 -I tools/measure/park_http.py busy    SERVER MODEL
    python3 -I tools/measure/park_http.py stop    SERVER MODEL        # a PARKED model shut down, then loaded afresh
    python3 -I tools/measure/park_http.py failed  SERVER MODEL        # only where the backend can be made to FAIL a return

SERVER is LocalAI's address (http://host:port). A LocalAI that asks for a key:
`--token-file PATH` (the key is never printed).

A MODEL BEHIND VEGA'S WRAPPER (a `vega-` package) takes no input without a
VeGa tag. `--vega` tags every input as Louise's client does
(app/vega/client.py `tag_batch`; found beside this file's tree, or under
`--louise-src DIR`). `--receipts URL` (VeGa's receipts service, e.g.
http://louise-vega:8000; it implies `--vega`) makes `cycles` hold each return
to VeGa's word as well as the engine's: the receipts of the request after the
return name THE RUN OF BEFORE THE PARK (no second run is stated for a return),
and the wrapper's own count for that run (`GET /vega/keeps`: `calls`,
`created`) went UP - a context was created for the return - and did not start
again, which is how a return that failed and was paid as a load by a new
wrapper process shows: it keeps the run id and gives valid receipts
(docs/notes/parking-step-2.md §11). Each cycle's line prints the run id and
both counts.

A model that answered a request under a second ago is busy for a park (409
`model_busy`): one HTTP request is several calls to its backend, and between
two of them the model is in use. `cycles` and `refused` ask again until that
second has passed, and say how often they were told busy.

`cycles` embeds sixteen sentences, then N times: parks (`/backend/park`), reads
`/system` (the model must be listed "parked", and its process hold less of the
device), brings it back - by the next request, or by `/backend/load` - reads
`/system` again ("loaded"), embeds the sixteen again and compares the vectors
BIT FOR BIT with the first. It prints what each park and each return took.
Exit 0: every cycle parked, came back, and gave the same bytes.

A CYCLE IS COUNTED BY THE ENGINE, NEVER ASSUMED. The same bytes after an HTTP
200 are not a cycle: a return that FAILS is paid by LocalAI as an ordinary load
in a new process, answered 200 with the same vectors. So `cycles` reads the
engine's own counts before and after each step - `GET /backend/monitor?model=`,
LocalAI's own route, which gives the backend's `Status`: `parks` (weights that
LEFT), `unparks` (that came BACK), `no_room` (requests refused for room),
`parked`; of weights, never of attempts; per process, so a model loaded afresh
reads 0 - and a cycle is one where `parks` and `unparks` each went up by
exactly one. Reading them brings nothing back and is not a request. A
Louise gate that certifies park cycles counts them the same way
(docs/notes/parking-step-2.md §10).

`refused` parks once and asks again until it is served: with
`GGML_SYCL_PARK_REFUSE=N` in the copy's env the first N returns are refused,
and each must be HTTP 503, type `no_room_on_device`, with a `Retry-After`, the
model still listed parked; the request after them is served with the same
bytes. `busy` asks to park while a long request runs (from 0.2 s after it was
sent, so that it is in flight): every answer in that time must be 409
`model_busy` (the model is neither parked nor stopped for being busy), and
the request's own answer the same bytes.

`floor` reads LocalAI's settings, sets the one key and writes the whole object
back, as deploy/vega/stage/localai_settings.py does: a change of the node's
configuration, made on the operator's word, and put back to 0 after the rows
unless the keeper has taken it over.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TEXTS = [f"sentence number {i}: the quick brown fox jumps over the lazy dog, {i} times" for i in range(16)]
NO_ROOM = "no_room_on_device"
#: where GET /system lists the loaded models (LocalAI's SystemInformationResponse; read off the real server,
#: after a first cut of this tool said "models" and was right only against its own stand-in)
MODELS_KEY = "loaded_models"
FLOOR_KEY = "park_host_floor_bytes"


class Server:
    """LocalAI's HTTP, the five routes parking uses. Every call returns
    (status, body, headers): an error status is an answer to read, not an
    exception."""

    def __init__(self, base: str, token_file: str | None = None, timeout: float = 300.0, *,
                 vega: bool = False, receipts: str | None = None, louise_src: str | None = None):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._token = Path(token_file).read_text(encoding="utf-8").strip() if token_file else None
        #: VeGa's receipts service, where one was given; LocalAI's key is never sent to it
        self.receipts = receipts.rstrip("/") if receipts else None
        self.last_ids: tuple[str, ...] = ()
        self._tag = None
        if vega or receipts:
            # Louise's own tagging, from the tree this file is in (or the one named): the standard library alone
            sys.path.insert(0, str(Path(louise_src).resolve() if louise_src else Path(__file__).resolve().parents[2]))
            from app.vega import client
            self._tag = client.tag_batch

    def call(self, method: str, path: str, body: object | None = None) -> tuple[int, object, dict[str, str]]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as r:
                return r.status, _json(r.read()), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, _json(e.read()), dict(e.headers)

    def embed(self, model: str, texts: list[str] | None = None) -> tuple[int, object, dict[str, str]]:
        inputs = list(texts or TEXTS)
        if self._tag is not None:
            batch = self._tag(inputs)                # a fresh id each: the receipts of THIS request are asked for by them
            self.last_ids, inputs = tuple(batch.ids), list(batch.inputs)
        status, body, headers = self.call("POST", "/v1/embeddings", {"model": model, "input": inputs})
        if status == 200 and isinstance(body, dict):
            return status, [d["embedding"] for d in body["data"]], headers
        return status, body, headers

    def park(self, model: str) -> tuple[int, object, dict[str, str]]:
        return self.call("POST", "/backend/park", {"model": model})

    def load(self, model: str) -> tuple[int, object, dict[str, str]]:
        return self.call("POST", "/backend/load", {"model": model})

    def row(self, model: str) -> dict | None:
        """the model's entry of GET /system, or None where it is not listed"""
        status, body, _ = self.call("GET", "/system")
        if status != 200 or not isinstance(body, dict):
            raise SystemExit(f"GET /system answered {status}: {str(body)[:200]}")
        return next((m for m in body.get(MODELS_KEY) or [] if m.get("id") == model), None)

    def counts(self, model: str) -> dict[str, int] | None:
        """the ENGINE's own counts for the model's process, through LocalAI's /backend/monitor (the backend's
        `Status`): None where there are none - a model that cannot park, one not loaded, a server without them"""
        status, body, _ = self.call("GET", "/backend/monitor?model=" + urllib.parse.quote(model, safe=""))
        breakdown = (body.get("memory") or {}).get("breakdown") if status == 200 and isinstance(body, dict) else None
        if not isinstance(breakdown, dict) or any(k not in breakdown for k in COUNT_KEYS):
            return None
        return {k: int(breakdown[k]) for k in COUNT_KEYS}

    def _vega(self, path: str) -> object:
        try:
            with urllib.request.urlopen(self.receipts + path, timeout=30) as r:
                return _json(r.read())
        except (urllib.error.URLError, OSError) as e:
            return {"unreachable": type(e).__name__}

    def receipt_runs(self) -> set[str] | None:
        """the runs the receipts of the LAST request's items name, "no receipt" among them where an item has
        none; None where no receipts service was given"""
        if not self.receipts or not self.last_ids:
            return None
        runs: set[str] = set()
        for i in range(0, len(self.last_ids), 128):
            part = self.last_ids[i:i + 128]
            body = self._vega("/vega/receipts?ids=" + ",".join(part))
            found = body.get("receipts") if isinstance(body, dict) else None
            if not isinstance(found, list):
                return {"no receipt"}
            runs |= {str(r.get("run_id")) for r in found}
            if len(found) != len(part):
                runs.add("no receipt")
        return runs

    def wrapper_count(self, run: str) -> dict | None:
        """the wrapper's own count for a run (GET /vega/keeps): calls, created, canary_failed"""
        body = self._vega("/vega/keeps")
        rows = body.get("runs") if isinstance(body, dict) else None
        return next((r for r in rows or [] if r.get("run_id") == run), None)

    def rows(self) -> list[dict]:
        status, body, _ = self.call("GET", "/system")
        if status != 200 or not isinstance(body, dict):
            raise SystemExit(f"GET /system answered {status}: {str(body)[:200]}")
        return list(body.get(MODELS_KEY) or [])

    def set_floor(self, floor: int) -> tuple[object, object]:
        status, settings, _ = self.call("GET", "/api/settings")
        if status != 200 or not isinstance(settings, dict):
            raise SystemExit(f"GET /api/settings answered {status}: {str(settings)[:200]}")
        before = settings.get(FLOOR_KEY, 0)
        status, answer, _ = self.call("POST", "/api/settings", {**settings, FLOOR_KEY: floor})
        if status != 200:
            raise SystemExit(f"POST /api/settings answered {status}: {str(answer)[:200]}")
        status, settings, _ = self.call("GET", "/api/settings")
        return before, settings.get(FLOOR_KEY, 0) if isinstance(settings, dict) else None


def _json(raw: bytes) -> object:
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return raw.decode(errors="replace")


def digest(vectors: object) -> str:
    return hashlib.sha256(json.dumps(vectors, separators=(",", ":")).encode()).hexdigest()[:12]


def error_of(body: object) -> tuple[str, str]:
    """(type, message) of LocalAI's error body; ("", the body) where it is not one"""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("type") or ""), str(body["error"].get("message") or "")
    return "", str(body)[:200]


def mib(n: object) -> str:
    return f"{int(n) / (1 << 20):,.0f} MiB" if isinstance(n, (int, float)) else "?"


def describe(row: dict | None) -> str:
    if row is None:
        return "not listed"
    if not row.get("parks"):
        return f"listed, does not park (size_vram {mib(row.get('size_vram'))})"
    parked = row.get("parked") or {}
    extra = f", {mib(parked.get('size_host'))} on the host since {parked.get('since')}" if row.get("state") == "parked" else ""
    return f"parks, state {row.get('state')!r}, size_vram {mib(row.get('size_vram'))}{extra}"


#: what the engine counts, as its `Status` names them (staged 0023, grpc-server.cpp)
COUNT_KEYS = ("parks", "unparks", "no_room", "parked")


def said(counts: dict[str, int] | None) -> str:
    if counts is None:
        return "no counts"
    return (f"{counts['parks']} parked, {counts['unparks']} back, {counts['no_room']} refused for room, "
            f"{'away' if counts['parked'] else 'on the device'}")


def miscounted(before: dict[str, int], after: dict[str, int] | None, parks: int, unparks: int, parked: int) -> str:
    """the empty string where the engine's counts moved from `before` by exactly this much; else what to say of it"""
    if after is None:
        return "the engine's counts cannot be read any more"
    if after["parks"] < before["parks"] or after["unparks"] < before["unparks"]:
        return (f"the engine's counts started again ({said(after)}; they were {said(before)}): another process "
                f"answers - a return that FAILED and was paid as a load is not a cycle")
    if (after["parks"], after["unparks"], after["parked"]) != (before["parks"] + parks, before["unparks"] + unparks, parked):
        return f"the engine counts {said(after)}; it counted {said(before)} before"
    return ""


def short(run: str) -> str:
    return run[:23] + "..." if run.startswith("sha256:") else run


def kept(count: dict | None) -> str:
    if count is None:
        return "no count"
    return f"calls {count.get('calls')}, created {count.get('created')}, canary_failed {count.get('canary_failed')}"


def wrapper_after_return(server: Server, run: str, before: dict | None, patience: float = 3.0) -> tuple[dict | None, str]:
    """the wrapper's count once the return's request is in it, and what is wrong with it ("" where nothing is).
    The wrapper tells a count before the vector of a call that created a context, so it is normally there at once."""
    deadline = time.monotonic() + patience
    while True:
        now = server.wrapper_count(run)
        behind = (now is not None and before is not None and int(now.get("created", 0)) == int(before.get("created", 0))
                  and int(now.get("calls", 0)) >= int(before.get("calls", 0)))
        if not behind or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    if now is None or before is None:
        return now, "the wrapper's count for the run cannot be read (GET /vega/keeps)"
    if int(now.get("created", 0)) < int(before.get("created", 0)) or int(now.get("calls", 0)) < int(before.get("calls", 0)):
        return now, (f"the wrapper's counts started again ({kept(now)}; they were {kept(before)}): another wrapper "
                     f"process answers - a return that FAILED and was paid as a load is not a cycle")
    if int(now.get("created", 0)) == int(before.get("created", 0)):
        return now, f"the wrapper counted no context created for the return's request ({kept(now)})"
    if int(now.get("canary_failed", 0)) != int(before.get("canary_failed", 0)):
        return now, f"a context did not reproduce its canary after the return ({kept(now)})"
    return now, ""


def park_when_idle(server: Server, model: str, patience: float = 5.0) -> tuple[int, object, float, int]:
    """One park, asked again while the answer is 409 model_busy: LocalAI takes a model that answered a request
    under a second ago for busy (one HTTP request is several calls to its backend), so a park asked right after
    a request waits that second out. Returns (status, answer, what the park itself took, how many times busy)."""
    deadline, busy_answers = time.monotonic() + patience, 0
    while True:
        t = time.monotonic()
        status, answer, _ = server.park(model)
        took = time.monotonic() - t
        if status != 409 or error_of(answer)[0] != "model_busy" or time.monotonic() > deadline:
            return status, answer, took, busy_answers
        busy_answers += 1
        time.sleep(0.1)


def cycles(server: Server, model: str, n: int, back: str) -> tuple[bool, list[str]]:
    """N parks and returns; (passed, the lines to print)"""
    lines: list[str] = []
    status, base, _ = server.embed(model)
    if status != 200:
        return False, [f"NOT RUN: the model does not answer: HTTP {status} {error_of(base)}"]
    row = server.row(model)
    lines.append(f"before: {describe(row)}; the sixteen vectors {digest(base)}")
    if row is None or not row.get("parks") or row.get("state") != "loaded":
        return False, lines + ["NOT RUN: /system does not list it as a model that parks, loaded - "
                               "its file needs `park: true`, and the server staged 0027"]
    held = row.get("size_vram")
    first = server.counts(model)
    if first is None:
        return False, lines + ["NOT RUN: the engine's counts cannot be read (GET /backend/monitor?model=...): "
                               "a cycle is counted by the engine, never taken from an HTTP 200"]
    lines.append(f"the engine's counts before: {said(first)}")
    run, made_first = None, None
    runs = server.receipt_runs()
    if runs is not None:
        if len(runs) != 1 or "no receipt" in runs:
            return False, lines + [f"NOT RUN: the sixteen vectors do not carry receipts of ONE run: {sorted(runs)} - is "
                                   "the model behind VeGa's wrapper, and --receipts its receipts service?"]
        (run,) = runs
        made_first = server.wrapper_count(run)
        lines.append(f"VeGa: the sixteen receipts name one run, {run}; the wrapper's count: {kept(made_first)}")
    parks, returns, passed, settled = [], [], True, 0
    for i in range(1, n + 1):
        counted = server.counts(model)
        if counted is None:
            return False, lines + [f"FAIL: cycle {i}: the engine's counts cannot be read any more"]
        made = server.wrapper_count(run) if run else None
        status, answer, took_park, busy_first = park_when_idle(server, model)
        settled += busy_first
        if status != 200:
            kind, message = error_of(answer)
            return False, lines + [f"FAIL: cycle {i}: the park answered HTTP {status} {kind}: {message}"]
        parked = server.row(model)
        if parked is None or parked.get("state") != "parked":
            return False, lines + [f"FAIL: cycle {i}: parked by its answer, but /system says: {describe(parked)}"]
        away = server.counts(model)
        wrong = miscounted(counted, away, 1, 0, 1)
        if wrong or away is None:
            return False, lines + [f"FAIL: cycle {i}: parked by its answer, but {wrong}"]
        moved = (answer.get("parked") or {}).get("size_host") if isinstance(answer, dict) else None
        freed = (held - parked["size_vram"]) if isinstance(held, int) and isinstance(parked.get("size_vram"), int) else None
        t = time.monotonic()
        if back == "load":
            status, answer, _ = server.load(model)
            if status != 200:
                kind, message = error_of(answer)
                return False, lines + [f"FAIL: cycle {i}: /backend/load answered HTTP {status} {kind}: {message}"]
        status, got, _ = server.embed(model)
        took_return = time.monotonic() - t
        if status != 200:
            kind, message = error_of(got)
            return False, lines + [f"FAIL: cycle {i}: the request after the park answered HTTP {status} {kind}: {message}"]
        after = server.row(model)
        same = got == base
        state_ok = after is not None and after.get("state") == "loaded"
        returned = server.counts(model)
        wrong = miscounted(away, returned, 0, 1, 0)   # against the reading taken while it was away
        vega = ""
        if run:
            named = server.receipt_runs() or set()
            made_now, wrong_vega = wrapper_after_return(server, run, made)
            if named != {run}:
                wrong_vega = (f"the receipts after the return name {sorted(short(r) for r in named)}, not the run of before "
                              f"the park: a second run was stated, or a vector has no receipt")
            wrong = wrong or wrong_vega
            vega = (f"; the engine: {said(away)} -> {said(returned)}; receipts of run {short(run)}; the wrapper: "
                    f"{kept(made)} -> {kept(made_now)}")
        passed = passed and same and state_ok and not wrong
        parks.append(took_park)
        returns.append(took_return)
        lines.append(f"cycle {i:2d}: parked in {took_park * 1000:7.1f} ms ({mib(moved)} to the host, the process holds "
                     f"{mib(parked.get('size_vram'))} of the device"
                     + (f", {mib(freed)} given back" if freed is not None else "") + f"); back by "
                     f"{'/backend/load and a request' if back == 'load' else 'the request'} in {took_return * 1000:7.1f} ms; "
                     f"vectors {'identical' if same else 'DIFFERENT: ' + digest(got)}" + vega
                     + ("" if state_ok else f"; /system afterwards: {describe(after)}"))
        if not same or not state_ok:
            return False, lines + [f"FAIL: cycle {i}: " + ("the vectors are not the first ones" if not same
                                                             else "the model is not listed loaded after its return")]
        if wrong:
            return False, lines + [f"FAIL: cycle {i}: answered with the same bytes, but {wrong}"]
    last = server.counts(model)
    lines.append(f"the engine's counts after: {said(last)} - {n} parks and {n} returns COUNTED, in the one process")
    if run:
        made_last = server.wrapper_count(run)
        lines.append(f"VeGa: ONE run through {n} returns, {run}; the wrapper's count: {kept(made_first)} -> {kept(made_last)}")
    lines.append(f"each park was asked right after a request: answered 409 model_busy {settled} times in all before "
                 f"the model had been idle for its second")
    lines.append(f"{n} of {n} cycles: the same bytes; a park {statistics.median(parks) * 1000:.1f} ms (median, "
                 f"{min(parks) * 1000:.1f}-{max(parks) * 1000:.1f}), a return with its sixteen sentences "
                 f"{statistics.median(returns) * 1000:.1f} ms ({min(returns) * 1000:.1f}-{max(returns) * 1000:.1f})")
    return passed, lines


def refused(server: Server, model: str, tries: int = 6) -> tuple[bool, list[str]]:
    """one park, then requests until one is served: every refusal a deferral, the model still parked"""
    status, base, _ = server.embed(model)
    if status != 200:
        return False, [f"NOT RUN: the model does not answer: HTTP {status} {error_of(base)}"]
    status, answer, _, _ = park_when_idle(server, model)
    if status != 200:
        return False, [f"NOT RUN: the park answered HTTP {status} {error_of(answer)}"]
    counted = server.counts(model)
    lines, n, passed = [f"parked; the sixteen vectors {digest(base)}; the engine's counts: {said(counted)}"], 0, True
    for attempt in range(1, tries + 1):
        status, got, headers = server.embed(model)
        if status == 200:
            same = got == base
            row = server.row(model)
            lines.append(f"request {attempt}: served, vectors {'identical' if same else 'DIFFERENT'}; {describe(row)}")
            if n == 0:
                return False, lines + ["NOT RUN: no return was refused - is GGML_SYCL_PARK_REFUSE in the copy's env?"]
            after = server.counts(model)
            lines.append(f"the engine's counts: {said(after)}")
            wrong = "" if counted is None else miscounted(counted, after, 0, 1, 0)
            if not wrong and counted is not None and after is not None and after["no_room"] != counted["no_room"] + n:
                wrong = f"the engine counts {after['no_room'] - counted['no_room']} refusal(s) for room, {n} were answered"
            if wrong:
                return False, lines + [f"FAIL: served, but {wrong}"]
            ok = passed and same and row is not None and row.get("state") == "loaded"
            return ok, lines + [("ok" if ok else "FAIL") + f": {n} return(s) refused as a deferral, then served"]
        kind, message = error_of(got)
        retry = headers.get("Retry-After") or headers.get("retry-after")
        row = server.row(model)
        deferral = status == 503 and kind == NO_ROOM and bool(retry) and row is not None and row.get("state") == "parked"
        passed = passed and deferral
        n += 1
        lines.append(f"request {attempt}: HTTP {status} {kind!r} Retry-After {retry!r}: {message[:120]}; {describe(row)}"
                     + ("" if deferral else "   <- NOT a deferral that leaves it parked"))
        if not deferral and (row is None or row.get("state") != "parked"):
            return False, lines + ["FAIL: after a refused return the model is not listed parked"]
    return False, lines + [f"FAIL: not served after {tries} requests"]


def pid_of(row: dict | None) -> object:
    return ((row or {}).get("process") or {}).get("pid")


def stop_parked(server: Server, model: str) -> tuple[bool, list[str]]:
    """a PARKED model is shut down (what a keeper does when the host runs short): it is gone without being
    brought back first, and the next request is an ordinary load that gives the same bytes"""
    status, base, _ = server.embed(model)
    if status != 200:
        return False, [f"NOT RUN: the model does not answer: HTTP {status} {error_of(base)}"]
    before = server.row(model)
    run_before = server.receipt_runs()
    status, answer, _, _ = park_when_idle(server, model)
    if status != 200:
        return False, [f"NOT RUN: the park answered HTTP {status} {error_of(answer)}"]
    lines = [f"parked: {describe(server.row(model))}; its process {pid_of(before)}"]
    t = time.monotonic()
    status, answer, _ = server.call("POST", "/backend/shutdown", {"model": model})
    lines.append(f"/backend/shutdown of the parked model: HTTP {status} in {(time.monotonic() - t) * 1000:.0f} ms: {json.dumps(answer)[:120]}")
    gone = server.row(model)
    lines.append(f"/system afterwards: {describe(gone)}")
    t = time.monotonic()
    status2, got, _ = server.embed(model)
    after = server.row(model)
    lines.append(f"the next request: HTTP {status2} in {time.monotonic() - t:.2f} s, vectors "
                 f"{'identical' if got == base else 'DIFFERENT'}; {describe(after)}; its process {pid_of(after)}")
    if run_before is not None:
        lines.append(f"VeGa: the receipts named {sorted(run_before)} before, {sorted(server.receipt_runs() or [])} after the load afresh")
    ok = status == 200 and gone is None and status2 == 200 and got == base and after is not None and after.get("state") == "loaded"
    if ok and pid_of(before) is not None and pid_of(before) == pid_of(after):
        return False, lines + ["FAIL: the same process answers after the shutdown"]
    return ok, lines + [("ok: a parked model is stopped as it is, and loads afresh" if ok
                         else "FAIL: the parked model was not stopped cleanly, or did not come back by an ordinary load")]


def failed_return(server: Server, model: str) -> tuple[bool, list[str]]:
    """where the backend FAILS a return (not a refusal for room): the process is stopped and the request that met
    it is served by an ordinary load - never the same fault again from the same process"""
    status, base, _ = server.embed(model)
    if status != 200:
        return False, [f"NOT RUN: the model does not answer: HTTP {status} {error_of(base)}"]
    before = server.row(model)
    run_before = server.receipt_runs()
    status, answer, _, _ = park_when_idle(server, model)
    if status != 200:
        return False, [f"NOT RUN: the park answered HTTP {status} {error_of(answer)}"]
    counted = server.counts(model)
    t = time.monotonic()
    status, got, _ = server.embed(model)
    took = time.monotonic() - t
    after = server.row(model)
    lines = [f"parked; its process {pid_of(before)}; the engine's counts: {said(counted)}",
             f"the request to it: HTTP {status} in {took:.2f} s, vectors "
             f"{'identical' if got == base else ('DIFFERENT' if status == 200 else error_of(got))}; {describe(after)}; "
             f"its process {pid_of(after)}"]
    if pid_of(before) is not None and pid_of(before) == pid_of(after):
        return False, lines + ["NOT RUN: the same process answered - the return did not fail (the backend's test switch "
                               "for a failing return is not in its env)"]
    ok = status == 200 and got == base and after is not None and after.get("state") == "loaded"
    recount = server.counts(model)
    lines.append(f"the engine's counts afterwards: {said(recount)}")
    if run_before is not None:
        lines.append(f"VeGa: the receipts named {sorted(run_before)} before, {sorted(server.receipt_runs() or [])} after")
    if ok and counted is not None and recount is not None and recount["parks"] >= counted["parks"]:
        return False, lines + ["FAIL: another process answers, and the counts did not start again"]
    return ok, lines + [("ok: the failed return cost a load, not an error" if ok
                         else "FAIL: the request that met a failed return was not served by an ordinary load")]


def busy(server: Server, model: str, sentences: int = 2048, lead: float = 0.2) -> tuple[bool, list[str]]:
    """parks asked while a long request runs: 409 model_busy each, the model neither parked nor stopped.
    The first park is asked `lead` seconds after the request was sent: one that reaches the server BEFORE the
    request does is rightly accepted (the model was idle), and would say nothing about a busy one."""
    texts = [f"{t} (copy {i // len(TEXTS)})" for i, t in enumerate(TEXTS * (sentences // len(TEXTS)))]
    status, base, _ = server.embed(model, texts)
    if status != 200:
        return False, [f"NOT RUN: the model does not answer: HTTP {status} {error_of(base)}"]
    done: dict[str, object] = {}

    def long_request() -> None:
        done["status"], done["vectors"], _ = server.embed(model, texts)

    thread = threading.Thread(target=long_request)
    thread.start()
    time.sleep(lead)
    answers: list[tuple[int, str]] = []
    while thread.is_alive():
        status, answer, _ = server.park(model)
        answers.append((status, error_of(answer)[0] if status != 200 else "parked"))
        time.sleep(0.01)
    thread.join()
    during = answers[:-1]                  # the last one may have been sent as the request ended
    counts = {a: during.count(a) for a in sorted(set(during))}
    lines = [f"{len(texts)} sentences in one request; while it ran, {len(during)} parks answered: {counts}",
             f"the request itself: HTTP {done.get('status')}, vectors "
             f"{'identical' if done.get('vectors') == base else 'DIFFERENT'}"]
    row = server.row(model)
    lines.append(f"afterwards: {describe(row)}")
    if not during:
        return False, lines + [f"NOT RUN: the request ended within {lead} s, before a park could be asked - "
                               "give it more sentences"]
    ok = (all(a == (409, "model_busy") for a in during) and done.get("status") == 200
          and done.get("vectors") == base and row is not None)
    return ok, lines + [("ok: a busy model is refused 409 model_busy, and is still there" if ok
                         else "FAIL: a park asked while a request ran was not refused as busy, or the request suffered")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--token-file", help="the file that holds LocalAI's API key")
    ap.add_argument("--vega", action="store_true", help="tag every input as Louise's client does (a model behind VeGa's wrapper)")
    ap.add_argument("--receipts", metavar="URL", help="VeGa's receipts service: hold each cycle to its receipts and the "
                                                      "wrapper's count too (implies --vega)")
    ap.add_argument("--louise-src", metavar="DIR", help="the tree that holds app/vega/client.py (default: this file's own)")
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb in ("state", "floor", "park", "cycles", "refused", "busy", "stop", "failed"):
        p = sub.add_parser(verb)
        p.add_argument("server")
        if verb == "floor":
            p.add_argument("bytes", type=int)
        else:
            p.add_argument("model", nargs="?" if verb == "state" else None)
        if verb == "cycles":
            p.add_argument("-n", type=int, default=20)
            p.add_argument("--return", dest="back", choices=("request", "load"), default="request")
        if verb == "busy":
            p.add_argument("--sentences", type=int, default=2048)
    a = ap.parse_args()
    server = Server(a.server, a.token_file, vega=a.vega, receipts=a.receipts, louise_src=a.louise_src)

    if a.verb == "state":
        rows = [server.row(a.model)] if a.model else [r for r in server.rows() if r.get("parks")]
        for row in rows:
            print(f"{(row or {}).get('id', a.model)}: {describe(row)}")
        if not rows:
            print("no model that parks is listed")
        return 0
    if a.verb == "floor":
        before, after = server.set_floor(a.bytes)
        print(f"{FLOOR_KEY}: {before} -> {after}" + ("  (0: nothing is parked)" if after == 0 else f"  ({mib(after)})"))
        return 0 if after == a.bytes else 1
    if a.verb == "park":
        status, answer, _ = server.park(a.model)
        print(f"HTTP {status}: {json.dumps(answer)}")
        print(f"/system: {describe(server.row(a.model))}")
        return 0 if status == 200 else 1
    if a.verb == "cycles":
        passed, lines = cycles(server, a.model, a.n, a.back)
    elif a.verb == "refused":
        passed, lines = refused(server, a.model)
    elif a.verb == "stop":
        passed, lines = stop_parked(server, a.model)
    elif a.verb == "failed":
        passed, lines = failed_return(server, a.model)
    else:
        passed, lines = busy(server, a.model, a.sentences)
    print("\n".join(lines))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
