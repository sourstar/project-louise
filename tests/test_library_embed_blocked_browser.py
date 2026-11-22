"""The embed-mode dialog's OK closes it at once, in a real browser
(2026-11-22, the operator on legi_F: set to "all p2p", the dialog said what
blocks it, and OK did not close it — it waited for the Library to load
again, which on the node is slow). tests/browser/library_embed_blocked.js
stands in for the reload and counts it. Skipped, saying why, where no browser and no
driver are installed."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, node_state, spaces, web
from app.api import app
from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "library_embed_blocked.js"
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)


def test_ok_closes_the_blocked_dialog_before_the_library_loads_again(tmp_path, isolated_config, monkeypatch):
    monkeypatch.setattr(spaces, "resolve_embed_model_hash", lambda *a: ("abc123", False))
    monkeypatch.setattr(web, "_ping", lambda url: True)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    node_state.set_worker_state("running")
    with TestClient(app) as c:
        page = c.get("/admin/library", headers={"X-Admin-Token": "sekrit"}).text
    (tmp_path / "library.html").write_text(page, encoding="utf-8")
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(tmp_path), str(STATIC)],
                         capture_output=True, text=True, timeout=120, env=browser_support.env())
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert got["posts"] == ["/admin/library/gutenberg/embed-mode"]
    assert got["before"] and "download/seeding only" in got["text"], "the dialog says what blocks it"
    assert got["afterOk"] is False, "OK closes the dialog itself, not the Library loaded again"
    assert got["reloads"] == 1, "and loads the Library again once, for the card's new setting"
