"""One pooled HTTP client for the paths that call a model server.

`httpx.post(url, ...)` — the module-level function — builds a whole `Client`
for that one request and throws it away: a fresh TLS context, which parses
and loads the system CA bundle, and a fresh connection, which pays the TCP
and TLS handshake again. It is the right call for something that happens
once. It is the wrong call inside a loop that feeds a GPU.

MEASURED ON THE NODE, 2026-09-22: `ssl.create_default_context` under
`embeddings._request_vectors` was 185 of 1,122 samples — 16 % of the
worker's whole CPU — while the encoder it feeds sat at 99 % of one core
waiting for work. The endpoints are on the LAN and speak plain HTTP, so the
TLS context was not even used; it is built by the constructor regardless.

So: one `Client` per process, its connection pool kept warm, requests issued
through it from every thread (httpx's Client is safe to share; each request
takes a connection from the pool). Timeouts stay per request, because they
differ per caller and per batch.

Not a general HTTP layer: acquisition has its own transports with their own
budgets and retries (`app/acquire/`), and nothing here touches the transfer
manager's byte budget (invariant 5) — these calls carry model input and
output, which the budget has never covered.
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

#: The pool. Sized for the callers that share it: a few model endpoints, each
#: with its own dispatch gate bounding how many requests can be in flight
#: (`app/rage/dispatch.py`), plus the searches. Keepalive holds a connection per
#: endpoint per lane rather than re-handshaking each time.
LIMITS = httpx.Limits(max_connections=64, max_keepalive_connections=32,
                      keepalive_expiry=120.0)

_lock = threading.Lock()
_client: httpx.Client | None = None


def client() -> httpx.Client:
    """The process's client, built on first use."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = httpx.Client(limits=LIMITS, follow_redirects=False)
    return _client


def post(url: str, **kw: Any) -> httpx.Response:
    return client().post(url, **kw)


def get(url: str, **kw: Any) -> httpx.Response:
    return client().get(url, **kw)


def put(url: str, **kw: Any) -> httpx.Response:
    return client().put(url, **kw)


def patch(url: str, **kw: Any) -> httpx.Response:
    return client().patch(url, **kw)


def reset() -> None:
    """Drop the client and its pool — for tests, and for a process that has
    just been told its endpoints moved."""
    global _client
    with _lock:
        old, _client = _client, None
    if old is not None:
        try:
            old.close()
        except Exception:                               # noqa: BLE001
            pass


# THE SERVER'S REASON ON EVERY REFUSAL (2026-11-21, the Bug session: an Ask
# question answered "400 Bad Request" and nothing else). `raise_for_status`
# names the status alone; LocalAI says why in the body ("exceeds the
# available context size", an unknown field, an unloaded model). The chat
# calls carried it since 2026-11-16 (app/llm.py `refuse_on_error`); the
# reranker, the translator and the encoder did not.
#: what of a refusal's body is said
ERROR_BODY_CHARS = 300


class ModelRefused(httpx.HTTPStatusError):
    """A model server's error answer: the status AND the first
    ERROR_BODY_CHARS of its body. An `httpx.HTTPStatusError`, `.response`
    and `.request` kept, so every handler of one handles it."""


def refusal(r: httpx.Response) -> tuple[str, str]:
    """(message, body) of an error answer: "<status> from <url>: <reason>",
    a streamed body read first; the reason the status phrase where the body
    says nothing or cannot be read."""
    try:
        r.read()
        text = r.text
    except Exception:                    # noqa: BLE001 — the status is still said
        text = ""
    try:
        url = r.request.url
    except Exception:                    # noqa: BLE001 — a response built without its request
        url = "the model server"
    reason = " ".join(text.split())[:ERROR_BODY_CHARS] or getattr(r, "reason_phrase", "") or "no reason given"
    return f"{r.status_code} from {url}: {reason}", text


def raise_for_status(r: httpx.Response, cls: type[httpx.HTTPStatusError] = ModelRefused) -> None:
    """`r.raise_for_status()`, carrying the server's reason (`refusal`). It
    wraps httpx's own check, so whatever that calls an error, this does;
    anything but an httpx Response is left to its own check, as before."""
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        if not isinstance(r, httpx.Response):
            raise
        message, _ = refusal(r)
        raise cls(message, request=e.request, response=r) from None
