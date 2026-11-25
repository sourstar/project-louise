"""EMBED MODE'S EMBEDDING CHOICE (operator, 2026-11-25).

  "In both embed and query modes you can choose to download and seed, seed
   only, no network. In embed mode you can also choose local/p2p embedding.
   In general, query keeps the system unloaded to optimize the query related
   model load, and embed is optimized to give to the embedders the maximum
   room on the card. Network and embedding mode remain personal choices."

`[modes] embed_embedding = "local" | "p2p"` (platform.toml; absent: each
collection's own): while the node is in embed mode, every collection that
embeds its backlog — its file says all or p2p — embeds it the node's way.
The choice says WHO embeds a backlog, never WHETHER: off and on-demand keep
their own word, and p2p still needs the collection shared. ONE resolution
(`collections.desired_embed_mode`) for the worker's apply and every page
(invariant 10); the worker follows a switch or an edit within a round.
"""
import os
import shutil
from pathlib import Path

import pytest

from app import collections as collections_mod, config, db, effective_config, node_mode, platform_config, worker

FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "gutenberg-mini" / "corpus"


@pytest.fixture
def platform(monkeypatch):
    """This test's platform.toml (as tests/test_mode_of_use.py writes it)."""
    for name in ("CHAT_MODEL", "TRANSLATE_MODEL"):
        if name in vars(config):
            monkeypatch.delattr(config, name)
    path = Path(os.environ["CONFIG_DIR"]) / "platform.toml"

    def write(text: str) -> Path:
        path.write_text(text, encoding="utf-8")
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
        return path
    return write


@pytest.fixture(autouse=True)
def the_round_has_read_nothing():
    worker._embedding_choice_seen[0] = worker._UNREAD
    yield
    worker._embedding_choice_seen[0] = worker._UNREAD


def _collection(tmp_path, name="alpha", embed_mode=None, share=False):
    corpus = tmp_path / f"corpus_{name}"
    if not corpus.exists():
        shutil.copytree(FIXTURE_CORPUS, corpus)
    return collections_mod.Collection(
        name=name, plugin="gutenberg", profile=None, embed_mode_default=embed_mode, share=share,
        corpus_dir=corpus, manifest_path=tmp_path / name / "manifest.db",
        fts_dir=tmp_path / name / "fts", lance_dir=tmp_path / name / "lancedb")


def _mode(con) -> str:
    return db.get_scope(con)["embed_mode"]


def _events(con, stage="control"):
    return [r["message"] for r in con.execute(
        "SELECT message FROM events WHERE stage=? ORDER BY id", (stage,))]


# ---------------- platform.toml ----------------

def test_a_file_that_says_nothing_leaves_every_collection_its_own_word(platform):
    pc = platform_config.load()
    assert pc.modes.embed_embedding == "collection"
    assert pc.modes.embedding("embed") == "collection"
    assert pc.modes.embedding("query") is None and pc.modes.embedding("custom") is None, \
        "query embeds nothing, custom is the node before modes"


@pytest.mark.parametrize("choice", ["local", "p2p", "collection"])
def test_embed_modes_embedding_choice_is_read(platform, choice):
    platform(f'[modes]\nembed_embedding = "{choice}"\n')
    pc = platform_config.load()
    assert pc.modes.embedding("embed") == choice
    assert pc.modes.written("embed_embedding") == choice


def test_a_choice_that_is_not_one_of_the_three_is_refused_at_load(platform):
    platform('[modes]\nembed_embedding = "all"\n')
    with pytest.raises(Exception, match="embed_embedding.*collection, local, p2p"):
        platform_config.load()


def test_the_budget_keys_are_still_read_as_rates(platform):
    # the rates were read by position in MODES_KEYS: a key added before them must not shift them
    platform('[modes]\nembed_embedding = "p2p"\nembed_down_max = "400Mbit"\nchat_up_max = "10Mbit"\n')
    pc = platform_config.load()
    assert pc.modes.budget("embed") == (50_000_000, None)
    assert pc.modes.budget("query") == (None, 1_250_000)


# ---------------- the resolution ----------------

CASES = [
    # own file, shared, node mode, choice  ->  what the node runs, is a note owed
    ("all",  True,  "embed",  "p2p",        "p2p",  True),
    ("all",  False, "embed",  "p2p",        "all",  True),     # not shared: p2p publishes what it embeds
    ("p2p",  True,  "embed",  "local",      "all",  True),
    ("all",  True,  "embed",  "local",      "all",  False),    # already what the node chose
    ("p2p",  True,  "embed",  "p2p",        "p2p",  False),
    ("off",  True,  "embed",  "p2p",        "off",  False),    # WHO embeds a backlog, never WHETHER
    ("lazy", True,  "embed",  "local",      "lazy", False),
    (None,   True,  "embed",  "p2p",        "lazy", False),    # a silent file: the default, on-demand
    ("all",  True,  "embed",  "collection", "all",  False),    # no choice made
    ("all",  True,  "query",  "p2p",        "all",  False),    # embed mode's choice, in embed mode only
    ("p2p",  True,  "custom", "local",      "p2p",  False),
]


@pytest.mark.parametrize("own,share,mode,choice,runs,noted", CASES)
def test_the_choice_governs_the_collections_that_embed_a_backlog_in_embed_mode_only(
        tmp_path, own, share, mode, choice, runs, noted):
    c = _collection(tmp_path, embed_mode=own, share=share)
    got, note = collections_mod.desired_embed_mode(c, mode=mode, choice=choice)
    assert got == runs
    assert (note is not None) == noted, note
    if note:
        assert "embed_embedding" in note and ("all p2p" in note or "all local" in note)


def test_a_collection_that_is_not_shared_says_why_it_does_not_follow_p2p(tmp_path):
    c = _collection(tmp_path, embed_mode="all", share=False)
    _, note = collections_mod.desired_embed_mode(c, mode="embed", choice="p2p")
    assert "not shared" in note and "publishes what it embeds" in note


def test_the_resolution_reads_the_nodes_mode_and_its_file_where_not_given(tmp_path, platform):
    c = _collection(tmp_path, embed_mode="all", share=True)
    platform('[modes]\nembed_embedding = "p2p"\n')
    assert collections_mod.desired_embed_mode(c)[0] == "all", "a node never switched is in custom mode"
    node_mode.switch("embed", by="test")
    assert collections_mod.desired_embed_mode(c)[0] == "p2p"
    node_mode.switch("query", by="test")
    assert collections_mod.desired_embed_mode(c)[0] == "all"


# ---------------- the worker's apply ----------------

def test_a_start_in_embed_mode_applies_the_choice_and_says_whose_word_it_is(tmp_path, platform):
    platform('[modes]\nembed_embedding = "p2p"\n')
    node_mode.switch("embed", by="test")
    c = _collection(tmp_path, embed_mode="all", share=True)
    con = db.connect(c)
    worker._apply_tier2_embed_mode(con, c, "restart")
    assert _mode(con) == "p2p"
    said = _events(con)
    assert any("-> 'p2p' on restart" in m and "embed mode's embedding choice is all p2p" in m
               and "its file says all local" in m for m in said), said
    assert not any("the file wins" in m for m in said), "it is not the collection's file that won"


def test_the_stores_apply_and_the_workers_are_one_resolution(tmp_path, platform):
    platform('[modes]\nembed_embedding = "local"\n')
    node_mode.switch("embed", by="test")
    c = _collection(tmp_path, embed_mode="p2p", share=True)
    con = db.connect(c)
    scope = db.get_scope(con)
    scope["embed_mode"] = "p2p"
    db.set_scope(con, scope)
    assert db.apply_desired_embed_mode(con, c) == "p2p", "what it replaced"
    assert _mode(con) == "all"
    assert db.apply_desired_embed_mode(con, c) is None, "applied again: nothing to do"


def _started(tmp_path, platform, text="", mode=None):
    """Three collections as a node holds them — a backlog all local and
    shared, a backlog all local NOT shared, one on-demand — applied as the
    start applies them, and the round's first read done."""
    if text:
        platform(text)
    if mode:
        node_mode.switch(mode, by="test")
    cols = [_collection(tmp_path, "backlog", "all", share=True),
            _collection(tmp_path, "private", "all", share=False),
            _collection(tmp_path, "asked", "lazy", share=True)]
    cons = {c.name: db.connect(c) for c in cols}
    for c in cols:
        worker._apply_tier2_embed_mode(cons[c.name], c, "restart")
    assert worker._apply_embedding_choice(node_mode.current(), cols, cons) == [], \
        "the first read of a process writes nothing: the start has just applied every collection"
    return cols, cons


def test_a_switch_into_embed_mode_brings_the_choice_and_a_switch_out_takes_it_back(tmp_path, platform, capsys):
    cols, cons = _started(tmp_path, platform, '[modes]\nembed_embedding = "p2p"\n')
    assert [_mode(cons[n]) for n in ("backlog", "private", "asked")] == ["all", "all", "lazy"]

    node_mode.switch("embed", by="test")
    capsys.readouterr()
    assert worker._apply_embedding_choice("embed", cols, cons) == ["backlog", "private"], \
        "the collections that embed a backlog; the on-demand one is not touched"
    assert [_mode(cons[n]) for n in ("backlog", "private", "asked")] == ["p2p", "all", "lazy"]
    line = capsys.readouterr().out
    assert "embed mode's embedding choice (all p2p)" in line and "backlog, private" in line, line
    assert worker._apply_embedding_choice("embed", cols, cons) == [], "nothing changed: one comparison"

    node_mode.switch("query", by="test")
    assert worker._apply_embedding_choice("query", cols, cons) == ["backlog", "private"]
    assert [_mode(cons[n]) for n in ("backlog", "private", "asked")] == ["all", "all", "lazy"], \
        "each collection's own file again"
    assert "no longer in force" in capsys.readouterr().out


def test_an_edit_of_the_choice_is_followed_without_a_restart(tmp_path, platform):
    cols, cons = _started(tmp_path, platform, '[modes]\nembed_embedding = "local"\n', mode="embed")
    assert _mode(cons["backlog"]) == "all"
    platform('[modes]\nembed_embedding = "p2p"\n')
    assert worker._apply_embedding_choice("embed", cols, cons) == ["backlog", "private"]
    assert _mode(cons["backlog"]) == "p2p" and _mode(cons["private"]) == "all"
    platform("")
    assert worker._apply_embedding_choice("embed", cols, cons) == ["backlog", "private"]
    assert _mode(cons["backlog"]) == "all", "the key removed: each collection's own"


def test_a_librarys_override_of_a_collection_the_choice_does_not_govern_survives_a_switch(tmp_path, platform):
    cols, cons = _started(tmp_path, platform, '[modes]\nembed_embedding = "p2p"\n')
    scope = db.get_scope(cons["asked"])
    scope["embed_mode"] = "off"                      # the Library's control, a runtime override
    db.set_scope(cons["asked"], scope)
    node_mode.switch("embed", by="test")
    worker._apply_embedding_choice("embed", cols, cons)
    assert _mode(cons["asked"]) == "off", "an on-demand collection is not the choice's: nothing is applied to it"


def test_a_collection_with_no_choice_made_is_never_written_by_a_switch(tmp_path, platform):
    cols, cons = _started(tmp_path, platform)                       # no [modes] at all
    before = {n: _events(c) for n, c in cons.items()}
    node_mode.switch("embed", by="test")
    assert worker._apply_embedding_choice("embed", cols, cons) == [], \
        "no choice in force before or after: a switch applies nothing"
    assert {n: _events(c) for n, c in cons.items()} == before


def test_sharing_turned_on_under_the_p2p_choice_is_followed_within_a_round(tmp_path, platform, monkeypatch):
    from dataclasses import replace
    from app import discovery
    platform('[modes]\nembed_embedding = "p2p"\n')
    node_mode.switch("embed", by="test")
    before = _collection(tmp_path, embed_mode="all", share=False)
    con = db.connect(before)
    discovery.sync_collection(con, before)
    worker._apply_tier2_embed_mode(con, before, "restart")
    assert _mode(con) == "all", "not shared: all local"
    after = replace(before, share=True)
    monkeypatch.setattr(collections_mod, "load_all", lambda: [after])
    monkeypatch.setattr(worker, "_discover", lambda *a, **k: None)
    worker._sync_collections([before], {before.name: con}, {}, pipeline=False)
    assert _mode(con) == "p2p", "shared now: it follows the node's choice, no restart and no switch needed"


def test_sharing_turned_with_no_choice_in_force_resets_no_override(tmp_path, platform, monkeypatch):
    from dataclasses import replace
    from app import discovery
    before = _collection(tmp_path, embed_mode="all", share=False)
    con = db.connect(before)
    discovery.sync_collection(con, before)
    worker._apply_tier2_embed_mode(con, before, "restart")
    scope = db.get_scope(con)
    scope["embed_mode"] = "off"                      # the Library's runtime override
    db.set_scope(con, scope)
    monkeypatch.setattr(collections_mod, "load_all", lambda: [replace(before, share=True)])
    monkeypatch.setattr(worker, "_discover", lambda *a, **k: None)
    worker._sync_collections([before], {before.name: con}, {}, pipeline=False)
    assert _mode(con) == "off", "the share box is not an embed-mode edit: the override stands as before"


# ---------------- what is shown ----------------

def test_node_mode_says_the_choice_in_force(platform):
    platform('[modes]\nembed_embedding = "p2p"\n')
    assert node_mode.shown()["embedding"] is None, "custom mode: each collection's own"
    assert node_mode.shown()["modes"]["embed"]["embedding"] == "p2p"
    assert "embedding" not in node_mode.shown()["modes"]["query"]
    node_mode.switch("embed", by="test")
    assert node_mode.shown()["embedding"] == "p2p"


def test_hold_config_show_reads_the_same_resolution(tmp_path, platform):
    platform('[modes]\nembed_embedding = "p2p"\n')
    node_mode.switch("embed", by="test")
    c = _collection(tmp_path, embed_mode="all", share=True)
    con = db.connect(c)
    worker._apply_tier2_embed_mode(con, c, "restart")
    got = effective_config._embed_mode(c)
    assert got["value"] == "p2p" and got["tier"] == effective_config.FILES
    assert "platform.toml [modes] embed_embedding" in got["set_in"] and "all" in got["set_in"]
    assert "embed mode's embedding choice" in got["note"]
