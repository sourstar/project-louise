"""The node's title (operator, 2026-11-11: "The label on top left is
'Project Louise', customizable is the settings"): platform.toml [node]
title, "Project Louise" when unset, on every page and a saved PDF, written
by `louise node title set|reset` — one line, every other line of the file
kept byte for byte (app/site_title.py)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import api, ask_pdf, louise, platform_config, site_title

FILE = """# the node
[node]
name = "nas"   # its label
address = "http://nas:8742"

[ask]
latency_budget_ms = 8000
"""


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    return tmp_path


def test_unset_it_is_project_louise(cfgdir):
    assert site_title.title() == "Project Louise"
    (cfgdir / "platform.toml").write_text(FILE)
    assert site_title.title() == "Project Louise"


def test_set_writes_one_line_and_reset_gives_the_file_back(cfgdir):
    f = cfgdir / "platform.toml"
    f.write_text(FILE)
    out = site_title.write(f, "  Biblioteca di casa  ")
    assert out["title"] == "Biblioteca di casa" and out["changed"] and out["before"] == "Project Louise"
    text = f.read_text()
    assert text.replace('title = "Biblioteca di casa"\n', "") == FILE, "every other line byte for byte"
    assert site_title.title() == "Biblioteca di casa"
    site_title.write(f, "Bibliothèque « maison »")
    assert site_title.title() == "Bibliothèque « maison »", "replaced where it stands"
    assert f.read_text().count("title =") == 1
    site_title.write(f, None)
    assert f.read_text() == FILE


def test_a_file_without_node_gets_the_table_and_no_file_is_made(cfgdir):
    f = cfgdir / "platform.toml"
    f.write_text("[ask]\nlatency_budget_ms = 8000")
    site_title.write(f, "Casa")
    assert f.read_text() == '[ask]\nlatency_budget_ms = 8000\n\n[node]\ntitle = "Casa"\n'
    assert platform_config._load_one(f).title == "Casa"


@pytest.mark.parametrize("bad", ["", "x" * 81, "two\nlines", 'tab\there'])
def test_a_title_the_node_would_refuse_is_never_written(cfgdir, bad):
    f = cfgdir / "platform.toml"
    f.write_text(FILE)
    with pytest.raises(platform_config.schedule_mod.ScheduleError):
        if bad == "":
            (cfgdir / "x.toml").write_text('[node]\ntitle = ""\n')
            platform_config._load_one(cfgdir / "x.toml")
        else:
            site_title.write(f, bad)
    assert f.read_text() == FILE, "nothing changed"
    assert not list(cfgdir.glob("*.tmp"))


def test_every_page_shows_it(isolated_config, config_dir):
    (config_dir / "platform.toml").write_text('[node]\ntitle = "Biblioteca <di> casa"\n')
    c = TestClient(api.app)
    for path in ("/ask", "/help", "/admin/login"):
        html = c.get(path).text
        assert '<div class="brand">Biblioteca &lt;di&gt; casa</div>' in html, path
        assert "— Biblioteca &lt;di&gt; casa</title>" in html or "<title>Biblioteca &lt;di&gt; casa</title>" in html, path
        assert "Project Louise" not in html.split("</head>")[0], path


def test_the_saved_pdf_carries_it():
    from datetime import datetime, timezone
    doc = ask_pdf.AskPdf(question="Q?", answer="A [1].", references=[
        ask_pdf.PdfReference(n=1, title="T", collection="c", book="1")])
    html = ask_pdf.build_html(doc, base_url="https://n.example", node="n.example",
                              when=datetime(2026, 11, 11, tzinfo=timezone.utc), site='Casa "mia"')
    assert '<span class="brand">CASA &#34;MIA&#34;</span>' in html
    assert 'content: "Casa \\"mia\\" · Q?";' in html, "the footer names it, escaped for CSS"
    assert '<span class="k">On Casa &#34;mia&#34;</span>' in html


def test_the_command_shows_sets_and_resets(cfgdir, capsys):
    (cfgdir / "platform.toml").write_text(FILE)
    assert louise.main(["node", "title", "set", "Casa"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Casa"
    assert louise.main(["node", "title", "show"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Casa"
    assert louise.main(["node", "title", "reset"]) == 0
    assert (cfgdir / "platform.toml").read_text() == FILE
    assert louise.main(["node", "title", "set", "x" * 81]) == 2
