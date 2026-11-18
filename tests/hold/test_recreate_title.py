"""A RE-CREATED COLLECTION KEEPS ITS PREDECESSOR'S DISPLAY KEYS (07 §7.8;
app/hold/recreate.py `predecessor_display`). München, 2026-11-17: the
re-created `wikipedia-articles`, made again from its own claims, lost
`title = "Wikipedia - versions"` — the create wrote a file of its own, the
forget removed the predecessor's, the rename moved the new one onto the
name — and was restored by `hold config apply`. Created under the interim
name `<name>.next` while `<name>` is held, a collection takes its title;
the rename carries it; nothing else of the file is taken, and nothing of
the node's runtime state (invariant 10)."""
from __future__ import annotations

import json

import pytest
import tomllib

from app import collection_file, louise
from app.hold import annotations as ann
from app.hold import annotations_export, recreate
from app.hold import forget as forget_mod
from app.hold import rename as rename_mod
from app.hold.deposit import held_file
from app.plugins.wikipedia.ids import native_id_of
from tests.hold import wiki_runs
from tests.hold.conftest import KEY, PLATFORM
from tests.hold.test_origin_reach import SID, build

TITLE = "Wikipedia - versions"


def _file(root, name) -> dict:
    with open(held_file(root, name), "rb") as f:
        return tomllib.load(f)


@pytest.fixture
def node(isolated_config, data_dir, config_dir, monkeypatch):
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    monkeypatch.setenv("LOUISE_PLATFORM_IMAGE", PLATFORM)
    root, _corpus, _codex, _snap = build(data_dir, config_dir)
    return root, data_dir, config_dir


def _claims() -> bytes:
    def uid(p: int) -> str:
        return f"wikipedia:{native_id_of('tr', p)}"
    return b"".join(json.dumps({
        "type": "same-work", "subject": uid(a), "object": uid(b), "subject_lang": "tr", "object_lang": "tr",
        "method": {"kind": "wikidata"}, "evidence": {"wikidata": "Q1"}, "confidence": "1",
        "contributor": "a test annotator"}).encode() + b"\n" for a, b in ((100, 101), (104, 105)))


def test_a_re_created_annotations_collection_keeps_its_title_through_forget_and_rename(node, tmp_path):
    root, _d, _c = node
    said: list[str] = []
    ann.create(root, "wikipedia-articles", target_ref="wikipedia", from_snapshot=SID, claims=_claims(),
               platform_image=PLATFORM, snapshot_id=SID, key=KEY, now="2026-11-17T00:00:00Z", log=said.append)
    assert "title" not in _file(root, "wikipedia-articles"), "a first create has no predecessor"
    collection_file.set_values("wikipedia-articles", {"title": TITLE})      # the card's "rename…"
    assert _file(root, "wikipedia-articles")["title"] == TITLE
    out = tmp_path / "claims.jsonl"
    annotations_export.export(root, "wikipedia-articles", out, log=lambda *_: None)
    # THE RE-CREATION: its create, under the interim name, while the predecessor is held
    ann.create(root, "wikipedia-articles.next", target_ref="wikipedia", from_snapshot=SID, claims=out,
               platform_image=PLATFORM, snapshot_id=SID, key=KEY, now="2026-11-17T00:00:01Z", log=said.append)
    mine = _file(root, "wikipedia-articles.next")
    assert mine["title"] == TITLE
    assert any("taken from wikipedia-articles's collection file" in m for m in said), "said aloud"
    # only the display key: the rest is the create's own, as before
    assert {k: v for k, v in mine.items() if k not in ("codex", "title")} == {
        "slices": "all", "kinds": ["records", "relations"], "embed": "off", "share": False}
    # the forget and the rename, as the runbook runs them
    forget_mod.forget(root, "wikipedia-articles")
    rename_mod.rename(root, "wikipedia-articles.next", "wikipedia-articles")
    assert _file(root, "wikipedia-articles")["title"] == TITLE


def test_a_plain_create_under_the_interim_name_takes_the_title_and_nothing_else(node, capsys):
    root, _d, _c = node
    collection_file.set_values("wikipedia", {"title": "Wikipedia (all eleven)"})
    nxt = wiki_runs.codex(root, "wikipedia.next", parser=5, shard_records=4)
    path = louise._write_held_file(root, nxt, "http://encoder:8080/upstream/fake-model")
    got = _file(root, "wikipedia.next")
    assert path == held_file(root, "wikipedia.next") and got["title"] == "Wikipedia (all eleven)"
    assert got["share"] is False and got["embed"] == "off" and got["slices"] == []
    assert "taken from wikipedia's collection file" in capsys.readouterr().err


def test_only_an_interim_name_with_a_held_predecessor_takes_anything(node):
    root, _d, _c = node
    collection_file.set_values("wikipedia", {"title": "Wikipedia"})
    assert recreate.predecessor_display(root, "wikipedia.next") == {"title": "Wikipedia"}
    assert recreate.predecessor_display(root, "wikipedia-sister") == {}
    assert recreate.predecessor_display(root, "nothing-held.next") == {}
    assert recreate.predecessor_display(root, ".next") == {}
