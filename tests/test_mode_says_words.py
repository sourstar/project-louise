"""What holds a stage says the node's mode in the Library's words only
(RULE 2; 2026-11-22, the operator on legi_F's dialog: 'The node is in
"download/seeding only" mode (…); this runs when it is set from acquire-only
to "run everything"')."""
from __future__ import annotations

import re

import pytest

from app import node_state, status


@pytest.mark.parametrize("state", [k for k in node_state.MODE_RUNS if k != "running"])
def test_no_mode_key_is_said_only_its_label(state):
    out = status.mode_says(state)
    labels = dict(node_state.MODE_LABELS)
    for key in node_state.MODE_RUNS:
        assert not re.search(rf'(?<![\w-]){re.escape(key)}(?![\w-])', out), \
            f"{state}: the internal key {key!r} in {out!r}"
    assert f'set from "{labels[state]}" to "{labels["running"]}"' in out


@pytest.mark.parametrize("state", list(node_state.MODE_RUNS))
def test_the_librarys_top_line_says_the_mode_by_its_label(state, isolated_config, monkeypatch):
    """The panel at the top of the Library said `worker: acquire-only` above
    the buttons that say "download/seeding only" (2026-11-22)."""
    from fastapi.testclient import TestClient

    from app import config, spaces, web
    from app.api import app
    monkeypatch.setattr(spaces, "resolve_embed_model_hash", lambda *a: ("abc123", False))
    monkeypatch.setattr(web, "_ping", lambda url: True)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    node_state.set_worker_state(state)
    with TestClient(app) as c:
        page = c.get("/admin/library", headers={"X-Admin-Token": "sekrit"}).text
    assert f"worker: <b>{dict(node_state.MODE_LABELS)[state]}</b>" in page
