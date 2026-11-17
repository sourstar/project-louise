"""The update by cadence SHOWN in plain words (app/hold/update.py
`describe`): `hold collection show`'s `update` and the Library card's line
(app/web.py `_update_view`), from the marker and the disk alone — "update
2026-11: fetching 7 of 11 languages, 41 of 62 GB"."""
from __future__ import annotations

import dataclasses
import json

from app import collections as collections_mod
from app.acquire import specs
from app.hold import update, update_state
from tests.hold import wiki_runs
from tests.hold.test_update_pass import (  # noqa: F401  (the fixture)
    NEW,
    NEW_PAGES,
    node,
)


def test_shown_in_plain_words_on_show_and_on_the_card(node, capsys, monkeypatch):  # noqa: F811 — the fixture
    from app import louise, web
    node.publish(NEW, NEW_PAGES)
    mk = {"collection_id": node.snapshot("2026-10").collection_id, "period": "2026-11", "run": NEW,
          "from": {"snapshot": "2026-10", "run": wiki_runs.FIRST}, "step": "fetch", "moved": False,
          "steps": {}, "failure": None}
    update_state.write(node.root, "wikipedia", mk)
    small = specs.ACQUIRE_REGISTRY["wikipedia"]
    monkeypatch.setitem(specs.ACQUIRE_REGISTRY, "wikipedia", dataclasses.replace(
        small, estimated_bytes={**small.estimated_bytes, "tr": 62_000_000_000}))
    assert update.describe(node.root, "wikipedia")["says"] == \
        "update 2026-11: fetching 0 of 1 languages, 0.0 of 62 GB"
    part = node.corpus / "trwiki" / NEW / f"trwiki-{NEW}-pages-articles-multistream.xml.bz2.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"x" * 100)                     # a partial fetch: counted, the slice not whole
    assert update.describe(node.root, "wikipedia")["says"] == \
        "update 2026-11: fetching 0 of 1 languages, 0.0 of 62 GB"
    part.unlink()
    monkeypatch.setitem(specs.ACQUIRE_REGISTRY, "wikipedia", small)
    got = node.pass_()
    assert got.step == "old-run"
    capsys.readouterr()
    assert louise.main(["hold", "collection", "show", "wikipedia", "--root", str(node.root)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["update"]["says"].startswith("update 2026-11: waiting for snapshot 2026-11 to be published")
    card = web._update_view(collections_mod.by_name("wikipedia"))
    assert card["says"] == shown["update"]["says"] and card["state"] == "old-run"
    assert web._update_view(collections_mod.by_name("gutenberg")) is None, "no line where none applies"
