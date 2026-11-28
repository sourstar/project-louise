"""WHO IS LOOKING, for what a reader route may show (07 §7.8 `hidden`,
operator 2026-11-28: "hides the collection to the non admin users").

One ContextVar holds the viewer (app/auth.py `Viewer`) of the HTTP request
being answered. `auth.ViewerGate` sets it for every request, so a sync route,
a streamed answer and everything they call read it without a parameter
threaded through. A thread pool does not inherit a ContextVar: the search's
pools carry it with the cancel token (`cancel.carry`).

FAIL-CLOSED. Outside any request, a process that serves readers (the web's
container process, app/web_main.py: `serve_readers`) is looked at by NOBODY,
and nobody sees a hidden collection. A pool that lost the viewer shows less,
never more. Any other process — the worker, the command line, a tool — is
the OPERATOR, who sees everything, as before.

STANDARD NOT ADOPTED, written down (CLAUDE.md): request-scoped identity is
what Starlette's `request.state`, or an auth framework's current-user
(Flask-Login's `current_user`, itself a context-local), gives. Both need the
request object at every call. The ContextVar is Python's own standard for
the same thing (PEP 567), and it reaches the loaders without the object.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Iterator


class _Operator:
    """The node's own processes: they see every collection."""

    def __repr__(self) -> str:
        return "OPERATOR"


OPERATOR = _Operator()
_UNSET = object()
_viewer: contextvars.ContextVar[Any] = contextvars.ContextVar("louise_viewer", default=_UNSET)
#: what a process is looked at by outside any request: the operator, until it
#: serves readers (`serve_readers`); then nobody
_default: Any = OPERATOR


def serve_readers() -> None:
    """This process answers readers: outside a request, nobody is looking."""
    global _default
    _default = None


def current() -> Any:
    """The viewer of the request being answered, else the process's default:
    OPERATOR, or None (nobody) in a process that serves readers."""
    v = _viewer.get()
    return _default if v is _UNSET else v


def captured() -> Any:
    """What `restored` puts back in another thread: the raw value, unset kept."""
    return _viewer.get()


@contextmanager
def restored(value: Any) -> Iterator[None]:
    token = _viewer.set(value)
    try:
        yield
    finally:
        _viewer.reset(token)


@contextmanager
def as_viewer(viewer: Any) -> Iterator[None]:
    """Code run as `viewer` (a request's, or a test's)."""
    with restored(viewer):
        yield


def sees_all(viewer: Any) -> bool:
    """The operator and an admin see hidden collections (`auth.admin`)."""
    if viewer is OPERATOR:
        return True
    from app import auth
    return auth.admin(viewer)
