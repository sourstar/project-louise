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

SERVER is LocalAI's address (http://host:port). A LocalAI that asks for a key:
`--token-file PATH` (the key is never printed).

`cycles` embeds sixteen sentences, then N times: parks (`/backend/park`), reads
`/system` (the model must be listed "parked", and its process hold less of the
device), brings it back - by the next request, or by `/backend/load` - reads
`/system` again ("loaded"), embeds the sixteen again and compares the vectors
BIT FOR BIT with the first. It prints what each park and each return took.
Exit 0: every cycle parked, came back, and gave the same bytes.

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
import urllib.request
from pathlib import Path

TEXTS = [f"sentence number {i}: the quick brown fox jumps over the lazy dog, {i} times" for i in range(16)]
NO_ROOM = "no_room_on_device"
FLOOR_KEY = "park_host_floor_bytes"


class Server:
    """LocalAI's HTTP, the five routes parking uses. Every call returns
    (status, body, headers): an error status is an answer to read, not an
    exception."""

    def __init__(self, base: str, token_file: str | None = None, timeout: float = 300.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._token = Path(token_file).read_text(encoding="utf-8").strip() if token_file else None

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
        status, body, headers = self.call("POST", "/v1/embeddings", {"model": model, "input": texts or TEXTS})
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
        return next((m for m in body.get("models") or [] if m.get("id") == model), None)

    def rows(self) -> list[dict]:
        status, body, _ = self.call("GET", "/system")
        if status != 200 or not isinstance(body, dict):
            raise SystemExit(f"GET /system answered {status}: {str(body)[:200]}")
        return list(body.get("models") or [])

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
    parks, returns, passed = [], [], True
    for i in range(1, n + 1):
        t = time.monotonic()
        status, answer, _ = server.park(model)
        took_park = time.monotonic() - t
        if status != 200:
            kind, message = error_of(answer)
            return False, lines + [f"FAIL: cycle {i}: the park answered HTTP {status} {kind}: {message}"]
        parked = server.row(model)
        if parked is None or parked.get("state") != "parked":
            return False, lines + [f"FAIL: cycle {i}: parked by its answer, but /system says: {describe(parked)}"]
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
        passed = passed and same and state_ok
        parks.append(took_park)
        returns.append(took_return)
        lines.append(f"cycle {i:2d}: parked in {took_park * 1000:7.1f} ms ({mib(moved)} to the host, the process holds "
                     f"{mib(parked.get('size_vram'))} of the device"
                     + (f", {mib(freed)} given back" if freed is not None else "") + f"); back by "
                     f"{'/backend/load and a request' if back == 'load' else 'the request'} in {took_return * 1000:7.1f} ms; "
                     f"vectors {'identical' if same else 'DIFFERENT: ' + digest(got)}"
                     + ("" if state_ok else f"; /system afterwards: {describe(after)}"))
        if not same or not state_ok:
            return False, lines + [f"FAIL: cycle {i}: " + ("the vectors are not the first ones" if not same
                                                             else "the model is not listed loaded after its return")]
    lines.append(f"{n} of {n} cycles: the same bytes; a park {statistics.median(parks) * 1000:.1f} ms (median, "
                 f"{min(parks) * 1000:.1f}-{max(parks) * 1000:.1f}), a return with its sixteen sentences "
                 f"{statistics.median(returns) * 1000:.1f} ms ({min(returns) * 1000:.1f}-{max(returns) * 1000:.1f})")
    return passed, lines


def refused(server: Server, model: str, tries: int = 6) -> tuple[bool, list[str]]:
    """one park, then requests until one is served: every refusal a deferral, the model still parked"""
    status, base, _ = server.embed(model)
    if status != 200:
        return False, [f"NOT RUN: the model does not answer: HTTP {status} {error_of(base)}"]
    status, answer, _ = server.park(model)
    if status != 200:
        return False, [f"NOT RUN: the park answered HTTP {status} {error_of(answer)}"]
    lines, n, passed = [f"parked; the sixteen vectors {digest(base)}"], 0, True
    for attempt in range(1, tries + 1):
        status, got, headers = server.embed(model)
        if status == 200:
            same = got == base
            row = server.row(model)
            lines.append(f"request {attempt}: served, vectors {'identical' if same else 'DIFFERENT'}; {describe(row)}")
            if n == 0:
                return False, lines + ["NOT RUN: no return was refused - is GGML_SYCL_PARK_REFUSE in the copy's env?"]
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
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb in ("state", "floor", "park", "cycles", "refused", "busy"):
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
    server = Server(a.server, a.token_file)

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
    else:
        passed, lines = busy(server, a.model, a.sentences)
    print("\n".join(lines))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
