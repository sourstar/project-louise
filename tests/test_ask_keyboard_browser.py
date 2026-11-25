"""The on-screen keyboard never covers the question being edited.

Operator, on an Android phone (Chrome), 2026-11-25, through the Bug session:
to change a question already asked he taps it, and the keyboard opens over
the question box and over the system's cut/copy/paste menu — he cannot see
what he edits. Since Chrome Android 108 the keyboard resizes only the VISUAL
viewport unless the viewport meta says `interactive-widget=resizes-content`,
and after an answer the page is left scrolled down, the box wherever that
left it.

Two halves, both here: base.html's meta asks for the page to be resized, and
the Ask page keeps the focused box inside what the keyboard leaves, with room
above it for the menu (ask.html `keepQuestionInView`). Where the box is on
the screen is layout, so the second half runs in a real browser
(tests/browser/ask_keyboard.js); a headless one has no keyboard, so both of
its models are played.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app
from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "ask_keyboard.js"
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


@pytest.mark.parametrize("page", ["/ask", "/help"])
def test_every_page_asks_the_browser_to_resize_for_the_keyboard(isolated_config, page):
    html = TestClient(app).get(page).text
    assert '<meta name="viewport" content="width=device-width, initial-scale=1, ' \
           'interactive-widget=resizes-content">' in html


@pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)
def test_the_focused_question_stays_above_the_keyboard_with_room_for_the_menu(tmp_path, isolated_config):
    (tmp_path / "ask.html").write_text(TestClient(app).get("/ask").text, encoding="utf-8")
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(tmp_path), str(STATIC)],
                         capture_output=True, text=True, timeout=120, env=browser_support.env())
    assert run.returncode == 0, run.stderr[-2000:]
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert "interactive-widget=resizes-content" in got["meta"]
    room, left = got["room"], got["left"]

    def seen(b):
        return b["focused"] and b["top"] >= room and b["bottom"] <= left - 8

    # the keyboard resizes the page (what the meta asks of Chrome on Android)
    assert got["resizeBefore"]["top"] == 560 and not seen({**got["resizeBefore"], "focused": True}), \
        "the case: the box is where the keyboard will be"
    assert got["resizeFocused"]["scrollY"] == got["resizeBefore"]["scrollY"], \
        "a box in view, with room above, is not moved by the focus alone"
    assert seen(got["resizeAfter"]), got["resizeAfter"]
    # the keyboard lies over the page (a browser that ignores the meta)
    assert got["overFocused"]["top"] == 560 and got["overMoved"] is True
    assert seen(got["overAfter"]), got["overAfter"]
    # no room above the box for the cut/copy/paste menu: it is given some
    assert got["topAfter"]["top"] >= room, got["topAfter"]
    # and a keyboard opened for another field moves nothing
    assert got["unfocused"]["after"]["scrollY"] == got["unfocused"]["before"]["scrollY"]
