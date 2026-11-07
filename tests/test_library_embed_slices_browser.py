"""The grow dialog says what a check means — in a real browser (operator,
2026-11-07: "For wikidata the archive is unique, thus it makes no sense to
show a false choice, but it makes sense to show the embedding options
there. In general, you may add a short comment or tag about the meaning of
checking/unchecking (dowload+embed, or embed)"; "Like at the top of the
checkboxes").

The Library page rendered with four cards, served by tests/browser/
library_embed_slices.js, which answers every request itself. Read in
document order: wikidata (one archive) shows no box to hold and its
families under "embed", the nine embedded checked; wikipedia (a download a
language) shows its held languages checked and greyed under "download +
embed (per Embed which)", then its "embed" group; a card holding
everything has every box greyed; the extra formats have their own header.
Each header comes before its group's first checkbox. Saving wikidata's
choice says what changes and posts once. Skipped where no browser is
installed.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import collections as collections_mod, web
from tests import browser_support

SCRIPT = Path(__file__).parent / "browser" / "library_embed_slices.js"
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
#: the operator's nine (2026-10-31: "hold all 14 and embed 9")
NINE = ("person", "work", "place", "organization", "event", "heritage", "built", "scholarly", "property")
FOURTEEN = NINE + ("taxon", "astronomy", "molecular", "internal", "other")

pytestmark = pytest.mark.skipif(not browser_support.available(), reason=browser_support.REASON)


def _ns(name, plugin, slices, embed=None):
    return SimpleNamespace(name=name, plugin=plugin, profile=None, slices=slices,
                           embed_slices=embed, embed_select=None)


def _card(base, ns):
    return {**base, "name": ns.name, "plugin": ns.plugin, "profile_mode": "additive",
            "profile_parts": web._profile_held(ns), "grow_options": web._profile_order(ns.plugin),
            "embed_select": web._embed_select_view(ns), "grow": web._grow_view(ns),
            "embed_slices": web._embed_slices_view(ns, declined=33_876_938),
            "extras_offered": [], "extras_held": []}


@pytest.fixture
def got(tmp_path, isolated_config):
    base = web._collection_shelf(collections_mod.default_collection(), {}, "running")
    wikidata = _card(base, _ns("wikidata", "wikidata", "all", tuple(f"family={f}" for f in NINE)))
    # the dump's own count of a family (the pack's family report), where a node keeps it
    for r in wikidata["embed_slices"]["rows"]:
        if r["short"] == "taxon":
            r["documents"] = 3_600_000
    cards = [wikidata,
             _card(base, _ns("wikipedia", "wikipedia", ("lang=en", "lang=it", "lang=de"))),
             _card(base, _ns("wikipedia-all", "wikipedia", "all")),
             {**base, "extras_offered": ["epub"], "extras_held": []}]
    html = web.TEMPLATES.env.get_template("library.html").render(
        request=None, active="library", shelves=cards, counts_age=0, plugins=[],
        plugin_profiles={}, plugin_extras={}, corpus_mounts=[], corpus_mounts_dir="",
        worker_state="running")
    (tmp_path / "library.html").write_text(html, encoding="utf-8")
    run = subprocess.run([browser_support.NODE, str(SCRIPT), str(tmp_path), str(STATIC)],
                         capture_output=True, text=True, timeout=120, env=browser_support.env())
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


def _boxes(d, cls):
    return [o for o in d["order"] if o.get("box") == cls]


def _tags(d):
    return [o["tag"] for o in d["order"] if "tag" in o]


def _first_box_after_each_tag(d):
    """Each header, and what follows it in document order: a checkbox, never
    another header — the header is at the top of its group."""
    out = {}
    for i, o in enumerate(d["order"]):
        if "tag" in o:
            out[o["tag"]] = d["order"][i + 1] if i + 1 < len(d["order"]) else None
    return out


def test_one_archive_shows_the_embed_choice_and_no_false_hold_choice(got):
    d = got["wikidata"]
    assert d["order"][0] == {"tag": "embed"}, "the header comes before the group's first checkbox"
    assert _tags(d) == ["embed"]
    assert not _boxes(d, "grow_lang") and not _boxes(d, "grow_all"), "nothing to hold is offered"
    rows = _boxes(d, "embed_slice")
    assert [r["value"] for r in rows] == [f"family={f}" for f in FOURTEEN]
    assert [r["value"] for r in rows if r["checked"]] == [f"family={f}" for f in NINE]
    assert not any(r["disabled"] for r in rows)
    assert d["order"][-1] == {"box": "embed_slices_all", "value": None, "checked": False, "disabled": False}
    assert ("The archive is downloaded whole: every family is held and searchable by words. "
            "The checks choose which families are embedded.") in d["text"]
    assert "every family held" in d["text"] and "grown later" not in d["text"], \
        "one archive held whole: nothing is grown later"
    assert "Preview (dry-run)" not in d["buttons"] and "Save the embedded families…" in d["buttons"]


def test_separate_downloads_keep_their_held_boxes_checked_and_grey_under_their_tag(got):
    d = got["wikipedia"]
    assert d["order"][0] == {"tag": "download + embed (per Embed which)"}
    assert _tags(d) == ["download + embed (per Embed which)", "embed"]
    first = _first_box_after_each_tag(d)
    assert first["download + embed (per Embed which)"]["box"] == "grow_lang"
    assert first["embed"]["box"] == "embed_slice"
    held = {r["value"]: (r["checked"], r["disabled"]) for r in _boxes(d, "grow_lang")}
    assert {v for v, s in held.items() if s == (True, True)} == {"en", "it", "de"}
    assert all(s == (False, False) for v, s in held.items() if v not in ("en", "it", "de"))
    assert _boxes(d, "grow_all") == [{"box": "grow_all", "value": None, "checked": False, "disabled": False}]
    assert ("Checked and greyed: held here — downloaded, kept and searched by words. Grow adds "
            "languages; a held one is not removed here, because removing it deletes its data.") in d["text"]
    # the embed group: the languages held, in the source's order, every one
    # embedded (the file names none)
    assert [(r["value"], r["checked"], r["disabled"]) for r in _boxes(d, "embed_slice")] == [
        ("lang=en", True, True), ("lang=de", True, True), ("lang=it", True, True)]
    assert "With “Embed which” on smart, the smart choice is made over every language held, then " \
           "only its articles in the checked languages are embedded" in d["text"]
    assert "Preview (dry-run)" in d["buttons"] and "Save the embedded languages…" in d["buttons"]


def test_everything_held_greys_every_row(got):
    d = got["wikipedia-all"]
    assert d["order"][0]["tag"].startswith("download + embed")
    rows = _boxes(d, "grow_lang")
    assert rows and all(r["checked"] and r["disabled"] for r in rows)
    assert _boxes(d, "grow_all") == [{"box": "grow_all", "value": None, "checked": True, "disabled": True}]


def test_the_extra_formats_have_their_own_header(got):
    d = got["gutenberg"]
    assert _tags(d) == ["download — extra formats, kept here only (see Help)"]
    assert d["order"][0]["tag"].startswith("download — extra formats")
    assert _first_box_after_each_tag(d)[_tags(d)[0]]["box"] == "grow_extra"


def test_saving_the_embed_choice_says_what_changes_and_posts_once(got):
    c = got["confirm"]
    assert c["buttons"] == ["Cancel", "Save"] and c["focused"] == "confirm-cancel"
    assert ("property: not embedded here from now on. What is already embedded of it keeps its "
            "vectors and stays searchable; the rest stays held and searched by words.") in c["text"]
    assert ("taxon: queued for the encoder — about 3.600.000 documents: what of it is not embedded "
            "yet goes to the encoder, as the embed mode allows.") in c["text"]
    assert "The worker applies it after a scan it starts on seeing the change" in c["text"]
    assert len(got["posted"]) == 1
    post = got["posted"][0]
    assert post["path"] == "/admin/library/wikidata/embed-slices"
    assert json.loads(post["body"]) == {"slices": [f"family={f}" for f in NINE[:-1] + ("taxon",)]}


def test_every_family_held_greys_the_rows_and_gives_back_every_declined_document(got):
    rows = _boxes(got["every"], "embed_slice")
    assert len(rows) == 14 and all(r["checked"] and r["disabled"] for r in rows)
    text = got["everyConfirm"]["text"]
    assert ("taxon, astronomy, molecular, internal, other: queued for the encoder — 33.876.938 "
            "documents") in text, "the stored count of documents not embedded here"
    assert "Every family held is embedded." in text
    assert got["afterCancel"] == {"shown": False, "posts": 1}, "Cancel posts nothing"


def test_none_checked_is_said_and_nothing_posted(got):
    assert "Choose at least one family to embed. To embed nothing, set the embed mode to off." \
        in got["none"]["text"]
    assert got["none"]["posts"] == 1
