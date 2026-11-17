"""THE ORIGIN ASKED WHETHER A RUN IS COMPLETE, and which runs it offers — the
two questions the update by cadence asks before it moves a byte
(app/hold/update.py step `due`; docs/notes/collection-updates.md §1).

Both are small GETs of the origin's own records (Wikimedia's dumpstatus.json
of each held wiki, its index of dated runs), and both move bytes, so every
answer is drawn from the one budget after it arrives (invariant 5,
`budget.acquire_bytes`, as `specs.wikipedia_snapshots` does). The origin's
etiquette is the acquisition's: its own base URL (the spec's first endpoint,
never a mirror for a record the origin keeps), the product User-Agent its
policy asks for (`transport_https.headers_for`), one connection, the requests
one after another, and the spec's `min_interval_s` between two starts where
it declares one.

An origin that does not answer — a timeout, a refused connection, a 5xx — is
a DEFERRAL (`OriginAway`), never a failure: nothing is written to the
collection, and the pass asks again later (07 §7.7's exit 3). A record the
origin does not have (404) is an answer: the run has not begun there.
"""
from __future__ import annotations

import time
from collections.abc import Callable


class OriginAway(Exception):
    """The origin did not answer: asked again later (07 §7.7, exit 3)."""


#: how long one record's GET may take
TIMEOUT_S = 30.0


def records(spec, records_, *, timeout: float = TIMEOUT_S, client_factory: Callable | None = None,
            sleep=time.sleep) -> dict[str, bytes | None]:
    """`{record.rel_path: its bytes, or None when the origin has none}` for
    each record (the plugin's `update_completion.records`), fetched from the
    spec's own base."""
    import httpx

    from . import budget
    from .transport_https import headers_for
    if not spec.endpoints:
        raise OriginAway(f"{spec.source_id}: the acquisition spec names no endpoint to ask")
    endpoint = spec.endpoints[0]
    base = endpoint.url.rstrip("/")
    gap = float(getattr(spec.etiquette, "min_interval_s", None) or 0.0)
    make = client_factory or (lambda: httpx.Client(timeout=timeout, follow_redirects=True))
    out: dict[str, bytes | None] = {}
    last = None
    try:
        with make() as client:
            for r in records_:
                if gap and last is not None:
                    sleep(max(0.0, gap - (time.monotonic() - last)))
                last = time.monotonic()
                resp = client.get(f"{base}/{r.rel_path}", headers=headers_for(endpoint))
                budget.acquire_bytes(len(resp.content))       # invariant 5: an answer moves bytes
                if resp.status_code == 404:
                    out[r.rel_path] = None
                    continue
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise OriginAway(f"{base}/{r.rel_path}: HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    # the origin REFUSES the request (a 403 to a User-Agent its
                    # policy rejects): not a deferral — said and counted
                    raise RuntimeError(f"{base}/{r.rel_path}: HTTP {resp.status_code}, the origin "
                                       f"refuses the request")
                out[r.rel_path] = resp.content
    except httpx.TransportError as e:
        raise OriginAway(f"{spec.source_id}: {type(e).__name__}: {e}") from None
    return out


def runs(spec) -> tuple[str, ...]:
    """The dated runs the origin offers now, newest first (the spec's
    `snapshot_index`, which draws its listing from the budget itself)."""
    import httpx
    if spec.snapshot_index is None:
        return ()
    try:
        return tuple(spec.snapshot_index())
    except (httpx.TransportError, httpx.HTTPStatusError) as e:
        raise OriginAway(f"{spec.source_id}: the origin's index of runs: {type(e).__name__}: {e}") from None
