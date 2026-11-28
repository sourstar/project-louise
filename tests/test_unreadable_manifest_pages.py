"""ONE UNREADABLE MANIFEST NEVER TAKES A PAGE DOWN (2026-11-28: EUR-Lex5.next's
manifest overwritten from byte 5 by a TLS alert, "file is not a database";
the operator: "System and library tabs still don't work"). A real file,
damaged as that one was, beside a sound collection: every page and fragment
a GET reaches without a parameter renders, or says so — never a 500."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import collections as collections_mod, config, db
from tests.test_web_pages import _patch_hash, _patch_health_pings

ADMIN = {"X-Admin-Token": "sekrit"}


def _damage(path) -> None:
    """The incident's shape: the first 5 bytes kept, then a TLS 1.2 alert
    record (type 0x15, version 3.3, 26 bytes) over the rest of the header.
    The WAL checkpointed and emptied first: a page 1 still in it would be
    read from there, the damaged file's never."""
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    with open(path, "r+b") as f:
        f.seek(5)
        f.write(b"\x15\x03\x03\x00\x1a" + bytes(range(26)))


def _node(monkeypatch, tmp_path):
    _patch_hash(monkeypatch)
    _patch_health_pings(monkeypatch)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    real = collections_mod.default_collection()
    db.connect(real).close()
    broken = replace(real, name="EUR-Lex5.next",
                     manifest_path=tmp_path / "EUR-Lex5.next" / "state" / "manifest.sqlite")
    db.connect(broken).close()
    for wal in ("-wal", "-shm"):
        p = broken.manifest_path.with_name(broken.manifest_path.name + wal)
        if p.exists():
            p.unlink()
    monkeypatch.setattr(collections_mod, "load_all",
                        lambda *, skip_unreadable=False: ([real, broken], []) if skip_unreadable else [real, broken])
    monkeypatch.setattr(collections_mod, "load_public", lambda *a, **k: [real, broken])
    return real, broken


def _get_routes():
    """Every GET path without a parameter, the included routers walked
    (FastAPI keeps them as `_IncludedRouter`: its own routes, its prefix)."""
    from app.api import app

    def walk(routes, prefix=""):
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None:
                ctx = getattr(r, "include_context", None)
                yield from walk(inner.routes, prefix + (getattr(ctx, "prefix", "") or ""))
                continue
            path = prefix + getattr(r, "path", "")
            if "GET" in (getattr(r, "methods", None) or ()):
                yield path
    # the streams answer for ever (server-sent events): not a page
    streams = ("/admin/network/bandwidth/stream", "/api/events")
    return sorted({p for p in walk(app.routes)
                   if "{" not in p and p not in streams
                   and not p.startswith(("/docs", "/openapi", "/redoc"))})


def _sweep(client) -> dict:
    """The pages that crashed: a 500, or a 5xx that does not say why (a
    route that serves the damaged collection's own data answers 503 and
    names it)."""
    failed = {}
    for path in _get_routes():
        r = client.get(path, headers=ADMIN)
        if r.status_code == 500 or (r.status_code > 500 and "cannot be opened" not in r.text):
            failed[path] = r.status_code
    return failed


def test_a_damaged_first_collection_takes_no_page_down(isolated_config, monkeypatch, tmp_path):
    """The node's case exactly (2026-11-28): EUR-Lex5.next sorted FIRST, so
    she was `default_collection()`, the control manifest every
    `db.connect()` without a collection opens — the System tab's scope read
    among them ("/admin/system" 500 three times until the restore)."""
    from app.api import app
    real, broken = _node(monkeypatch, tmp_path)
    monkeypatch.setattr(collections_mod, "load_all",
                        lambda *, skip_unreadable=False: ([broken, real], []) if skip_unreadable else [broken, real])
    monkeypatch.setattr(collections_mod, "load_public", lambda *a, **k: [broken, real])
    with TestClient(app, raise_server_exceptions=False) as client:
        _damage(broken.manifest_path)
        assert not _sweep(client)


def test_the_sweep_reaches_the_pages():
    paths = _get_routes()
    assert {"/admin/library", "/admin/system", "/library", "/ask"} <= set(paths), paths


def test_a_manifest_damaged_while_the_web_runs_takes_no_page_down(isolated_config, monkeypatch, tmp_path):
    """The node's order: the web started, then the header was overwritten."""
    from app.api import app
    _real, broken = _node(monkeypatch, tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        _damage(broken.manifest_path)
        assert not _sweep(client)


def test_a_manifest_damaged_before_the_start_is_that_collection_s_refusal(isolated_config, monkeypatch, tmp_path):
    """The web starts (and the worker's boot check, the same `refuse_boot`,
    passes): the damaged collection alone is refused, by name, and served
    nowhere; the pages render."""
    from app import query_service, spaces
    from app.api import app
    real, broken = _node(monkeypatch, tmp_path)
    _damage(broken.manifest_path)
    refused = spaces.refuse_boot([real, broken])
    assert [c.name for c, _why in refused] == ["EUR-Lex5.next"]
    assert "its manifest cannot be opened (DatabaseError: file is not a database)" in refused[0][1]
    with TestClient(app, raise_server_exceptions=False) as client:
        assert query_service.withheld_reason(broken) and not query_service.withheld_reason(real)
        assert not _sweep(client)


def test_a_locked_manifest_is_not_refused_at_start():
    """Deferral is not failure: a lock waits, as it always did."""
    import sqlite3
    from app import spaces
    assert spaces.unreadable(sqlite3.OperationalError("database is locked")) is None
    assert spaces.unreadable(sqlite3.DatabaseError("file is not a database"))


def test_the_query_log_goes_to_the_control_fallback_when_the_control_manifest_does_not_open(
        isolated_config, monkeypatch, tmp_path, capsys):
    """A question is never failed by its log line: the control manifest
    unreadable, `db.control()` opens the node's fallback and says so."""
    real, broken = _node(monkeypatch, tmp_path)
    monkeypatch.setattr(collections_mod, "load_all",
                        lambda *, skip_unreadable=False: ([broken, real], []) if skip_unreadable else [broken, real])
    _damage(broken.manifest_path)
    con = db.control()
    try:
        db.log_query(con, "search", "chi era Garibaldi?", "hybrid", 10, {}, 0, [])
        assert con.execute("SELECT COUNT(*) FROM query_log").fetchone()[0] == 1
    finally:
        con.close()
    assert "[EUR-Lex5.next err:control] its manifest cannot be opened" in capsys.readouterr().out
