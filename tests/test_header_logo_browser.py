"""The title "Project Louise" on the logo's middle (operator, 2026-11-16:
"Align the title to the center of the logo; it looks currently aligned to
the top"), with the menu clear below the logo, measured in a browser at a
desktop, a narrow and a phone width (tests/browser/header_logo.js)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app
from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "header_logo.js"
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)


def test_the_title_sits_on_the_logos_middle(isolated_config, tmp_path):
    html = TestClient(app).get("/ask").text
    # the page as served, its stylesheet and logo read from the tree; no script runs
    html = html.replace('href="/static/', f'href="{STATIC.as_uri()}/').replace(
        'src="/static/', f'src="{STATIC.as_uri()}/')
    page = tmp_path / "ask.html"
    page.write_text(html.replace("<script", "<template").replace("</script>", "</template>"), encoding="utf-8")
    run = subprocess.run([browser_support.NODE, str(SCRIPT), page.as_uri()], capture_output=True, text=True,
                         timeout=120, env=browser_support.env(), check=False)
    assert run.returncode == 0 and run.stdout.count("PASS") == 3, run.stdout + run.stderr
