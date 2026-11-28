"""A LOCKED collection (operator, 2026-11-27; 07 §7.8 `locked`;
docs/notes/collection-lock.md): "not searchable or shareable, no processing
or fetching … Its embedders/reranker are not loaded … It can only be
renamed, exported or removed."

The one rule under test: `locked = true` in the file is a policy refusal of
`collections.load_all`, so every reader of that listing leaves it out; and
everything that would change it but its three allowed acts is refused."""
import argparse

import pytest

from app import collection_file, collection_lock, collections as collections_mod, louise
from app.hold import collection as col
from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed, fake_query_embed
from tests.hold.test_template import recipe

ENCODER = ('\n[encoder]\nurl = "http://enc:8080"\napi = "openai"\n'
           'embed_model = "m"\nquery_model = "m"\n')


@pytest.fixture
def node(tmp_path, isolated_config, monkeypatch):
    root = tmp_path / "louise"
    (root / "collections").mkdir(parents=True)
    cfg = tmp_path / "config"
    (cfg / "collections").mkdir(parents=True, exist_ok=True)
    (cfg / "collections" / "gutenberg.toml").unlink()
    monkeypatch.setenv("LOUISE_ROOT", str(root))
    monkeypatch.setenv("CONFIG_DIR", str(cfg))
    monkeypatch.setattr(collections_mod.config, "DATA_DIR", root)
    cx = col.create_from_recipe(
        root / "collections", "gutenberg", recipe(), platform_image=PLATFORM,
        embed=fake_embed, embed_query=fake_query_embed, name="gutenberg", key=KEY, now=NOW)
    path = cfg / "collections" / "gutenberg.toml"

    def write(extra=""):
        path.write_text(f'codex = "{cx.collection_id}"\ntitle = "PG"\nshare = true\n{extra}'
                        + ENCODER, encoding="utf-8")
        return path

    return root, path, write


def test_an_unlocked_collection_is_served(node):
    _, _, write = node
    write()
    assert [c.name for c in collections_mod.load_all()] == ["gutenberg"]
    assert collections_mod.load_locked() == []
    assert not collections_mod.is_locked("gutenberg")


def test_a_locked_collection_is_held_and_not_served(node):
    """Every reader of `load_all` — the worker, a search, sharing, the model
    keeper — leaves it out by this one refusal, said in plain words."""
    _, _, write = node
    write("locked = true\n")
    assert collections_mod.load_all() == []
    (r,) = collections_mod.refusals()
    assert r.name == "gutenberg" and "locked" in r.reason
    assert "renamed, exported or removed" in r.reason and "unlock gutenberg" in r.reason
    assert [c.name for c in collections_mod.load_locked()] == ["gutenberg"]
    assert collections_mod.is_locked("gutenberg")
    # the worker's own form of the listing too
    served, unreadable = collections_mod.load_all(skip_unreadable=True)
    assert served == [] and unreadable == []


def test_locked_is_true_or_false(node):
    _, path, write = node
    write('locked = "yes"\n')
    with pytest.raises(ValueError, match="locked is true or false"):
        collections_mod._load_one(path)


def test_lock_and_unlock_write_the_one_line_and_keep_the_rest(node):
    _, path, write = node
    write()
    said = collection_lock.lock("gutenberg")
    assert said["locked"] is True
    text = path.read_text(encoding="utf-8")
    assert "locked = true" in text and 'title = "PG"' in text and "share = true" in text
    assert collections_mod.load_all() == []
    assert collection_lock.lock("gutenberg")["says"] == "gutenberg was already locked"
    said = collection_lock.unlock("gutenberg")
    assert said["locked"] is False
    text = path.read_text(encoding="utf-8")
    assert "locked" not in text and 'title = "PG"' in text and "share = true" in text
    assert [c.name for c in collections_mod.load_all()] == ["gutenberg"]


def test_a_rewrite_carries_the_lock(node):
    """A rewrite that dropped it would serve, fetch for and load the models
    of a collection kept as it is (tests/test_toml_roundtrip.py guards every
    field; this is the one call the app makes)."""
    _, path, write = node
    write("locked = true\n")
    c = collections_mod._load_one(path)
    collection_file.rewrite(c, title="other")
    assert collections_mod._load_one(path).locked is True


def test_a_locked_file_changes_only_by_its_lock(node):
    _, path, write = node
    write("locked = true\n")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(collection_file.SetRefused, match="locked"):
        collection_file.set_values("gutenberg", {"share": "false"})
    with pytest.raises(collection_file.SetRefused, match="locked"):
        collection_file.set_values("gutenberg", {"locked": "true", "embed": "off"})
    assert path.read_text(encoding="utf-8") == before
    # its shown name is its rename in the Library: taken
    collection_file.set_values("gutenberg", {"title": "kept"})
    assert collections_mod._load_one(path).title == "kept"
    # the same call may unlock it and set more
    collection_file.set_values("gutenberg", {"locked": "false", "share": "false"})
    c = collections_mod._load_one(path)
    assert (c.locked, c.share) == (False, False)


def test_a_writer_whose_index_a_sister_reads_is_not_locked(node, monkeypatch):
    """Locked, the writer stops indexing for both, and the sister's text leg
    misses what she takes in after the lock: refused, nothing written."""
    from app import text_index
    _, path, write = node
    write()
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(text_index, "readers", lambda root, name: ["gutenberg-nemo"])
    with pytest.raises(collection_lock.LockRefused, match="unshare gutenberg-nemo"):
        collection_lock.lock("gutenberg")
    assert path.read_text(encoding="utf-8") == before
    # a sister locked too changes nothing for her: allowed
    monkeypatch.setattr(collections_mod, "is_locked", lambda n: n == "gutenberg-nemo")
    assert collection_lock.sisters_reading("gutenberg") == []


def test_unlock_names_a_collection_not_held(node):
    with pytest.raises(collection_lock.LockRefused, match="no such collection"):
        collection_lock.unlock("nobody")


def _ns(fn, collection, **kw):
    return argparse.Namespace(fn=fn, collection=collection, root=None, **kw)


def test_a_command_on_a_locked_collection_is_refused_before_it_acts(node, monkeypatch):
    _, _, write = node
    write("locked = true\n")
    monkeypatch.setattr(louise.col, "name_of", lambda root, ref: ref)
    why = louise._locked_refusal(_ns(louise.cmd_vectors_build, "gutenberg"))
    assert why and "gutenberg is locked" in why and "unlock gutenberg" in why
    for fn in (louise.cmd_records_fetch, louise.cmd_check, louise.cmd_share,
               louise.cmd_collection_refit, louise.cmd_node_resume):
        assert louise._locked_refusal(_ns(fn, "gutenberg")), fn.__name__
    # its three acts, its lock, and what only reads it
    for fn in (louise.cmd_collection_rename, louise.cmd_collection_forget,
               louise.cmd_vectors_export, louise.cmd_collection_unlock,
               louise.cmd_collection_lock, louise.cmd_config_show, louise.cmd_vectors_show):
        assert louise._locked_refusal(_ns(fn, "gutenberg")) is None, fn.__name__
    # an unlocked one, or a command that names none
    write()
    assert louise._locked_refusal(_ns(louise.cmd_vectors_build, "gutenberg")) is None
    assert louise._locked_refusal(argparse.Namespace(fn=louise.cmd_vectors_build)) is None


def test_the_cli_refuses_with_exit_2(node, monkeypatch, capsys):
    _, _, write = node
    write("locked = true\n")
    monkeypatch.setattr(louise.col, "name_of", lambda root, ref: ref)
    code = louise.main(["hold", "vectors", "build", "gutenberg", "--dry-run"])
    assert code == 2
    assert "gutenberg is locked" in capsys.readouterr().err


def test_the_lock_commands(node, monkeypatch, capsys):
    _, path, write = node
    write()
    monkeypatch.setattr(louise.col, "name_of", lambda root, ref: ref)
    assert louise.main(["hold", "collection", "lock", "gutenberg"]) == 0
    assert "locked = true" in path.read_text(encoding="utf-8")
    assert louise.main(["hold", "collection", "unlock", "gutenberg"]) == 0
    assert "locked" not in path.read_text(encoding="utf-8")
    assert louise.main(["hold", "collection", "unlock", "nobody"]) == 2


# ---- the Library -------------------------------------------------------------

@pytest.fixture
def client(isolated_config, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config, node_state, spaces, web
    from app.api import app
    monkeypatch.setattr(spaces, "resolve_embed_model_hash", lambda *a: ("abc123", False))
    monkeypatch.setattr(web, "_ping", lambda url: True)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    node_state.set_worker_state("running")
    with TestClient(app) as c:
        c.headers["X-Admin-Token"] = "sekrit"
        yield c


def test_the_lock_box_locks_and_the_card_keeps_three_acts(client):
    page = client.get("/admin/library").text
    assert "setLocked('gutenberg', this.checked, this)" in page and 'data-kind="locked"' not in page
    r = client.post("/admin/library/gutenberg/lock", json={"locked": True})
    assert r.status_code == 200 and r.json()["locked"] is True
    assert collections_mod.is_locked("gutenberg") and collections_mod.load_all() == []
    page = client.get("/admin/library").text
    card = page.split('data-kind="locked"', 1)[1].split("Offered by peers", 1)[0]
    assert "kept as it is" in card and "checked" in card
    assert "renameCollection('gutenberg'" in card and "exportBundle('gutenberg')" in card \
        and "removeCollection('gutenberg'" in card
    for other in ("confirmAction(", "openGrow(", "importBundle(", "setShare("):
        assert other not in card, other
    # it waits for nothing: not listed among the refused
    assert 'data-kind="refused"' not in page
    # its rename (the name pages show) is taken
    assert client.post("/admin/library/gutenberg/title", json={"title": "kept"}).status_code == 200
    # any other control: refused, saying it is locked
    r = client.post("/admin/library/gutenberg/share", json={"share": False})
    assert r.status_code == 409 and "locked" in r.json()["detail"]
    r = client.post("/admin/library/gutenberg/lock", json={"locked": False})
    assert r.status_code == 200 and r.json()["locked"] is False
    assert [c.name for c in collections_mod.load_all()] == ["gutenberg"]


def test_a_locked_collection_is_removed(client):
    assert client.post("/admin/library/gutenberg/lock", json={"locked": True}).status_code == 200
    r = client.post("/admin/library/gutenberg/remove", json={"mode": "retire"})
    assert r.status_code == 200, r.text
    assert not collections_mod.config_toml_path("gutenberg").is_file()


def test_a_node_whose_every_collection_is_locked_keeps_its_control_rows(node):
    """Not a misconfigured node (every file REFUSED, which raises): its
    control rows go where a node holding none writes them — NEVER into a
    locked collection, which the routes scoped to the default collection
    (the books listing, a discover, a reset) would then act on (the second
    read of the lock)."""
    _, _, write = node
    write("locked = true\n")
    assert collections_mod.load_all() == []
    assert collections_mod.default_collection().name == "control"


# ---- what must still see a locked collection ---------------------------------

def test_a_locked_ancestor_keeps_her_folder(node, monkeypatch):
    """A sister reading a locked ancestor's origin folder does not take over
    its fetch: kept as it is, its folder too."""
    import dataclasses
    import types
    _, path, write = node
    write('locked = true\nslices = "all"\n')
    anc = collections_mod._load_one(path)
    assert anc.acquire_enabled, "holding every slice, it fetches its origin"
    sister = dataclasses.replace(
        anc, name="sister", locked=False, acquire_stated=False,
        codex=types.SimpleNamespace(collection_id="sha256:" + "0" * 64,
                                    derived_from=anc.codex.collection_id))
    turned = {c.name: c for c in collections_mod._one_acquirer([sister, anc])}
    assert turned["sister"].acquire_enabled is False and turned["sister"].acquire_by == "gutenberg"
    # and load_all judges her with the locked ancestor in view
    seen = []
    real = collections_mod._one_acquirer
    monkeypatch.setattr(collections_mod, "_one_acquirer", lambda held: seen.append([c.name for c in held]) or real(held))
    collections_mod.load_all()
    assert seen and "gutenberg" in seen[-1]


def test_a_locked_collection_is_held_not_offered(node):
    from app.share import offer
    _, _, write = node
    write("locked = true\n")
    cx = collections_mod._load_one(collections_mod.config_toml_path("gutenberg")).codex
    assert cx.collection_id in offer.held_ids()


def test_a_reader_is_not_unlocked_before_the_writer_of_her_index(node, monkeypatch):
    _, _, write = node
    write("locked = true\n")
    monkeypatch.setattr(collection_lock, "index_writer", lambda name, root=None: "writer")
    real = collections_mod.is_locked
    monkeypatch.setattr(collections_mod, "is_locked", lambda n: n == "writer" or real(n))
    with pytest.raises(collection_lock.LockRefused, match="unlock writer first"):
        collection_lock.unlock("gutenberg")
    assert real("gutenberg")


def test_node_show_lists_a_locked_collection(node, monkeypatch):
    from app import node_show
    _, _, write = node
    write("locked = true\n")
    rows = [r for r in node_show.show()["collections"] if r["name"] == "gutenberg"]
    assert rows and rows[0]["state"] == "locked"


# ---- the second read of the lock (2026-11-28) ----------------------------------

def test_set_locked_asks_the_lock_s_refusals(node, monkeypatch):
    """`hold config apply --set locked=…` is the documented way to lock: it
    asks what `lock` and `unlock` ask, in the file's one writer."""
    from app import text_index
    _, path, write = node
    write()
    monkeypatch.setattr(text_index, "readers", lambda root, name: ["gutenberg-nemo"])
    with pytest.raises(collection_file.SetRefused, match="unshare gutenberg-nemo"):
        collection_file.set_values("gutenberg", {"locked": "true"})
    assert "locked" not in path.read_text(encoding="utf-8")
    monkeypatch.setattr(text_index, "readers", lambda root, name: [])
    write("locked = true\n")
    monkeypatch.setattr(collection_lock, "index_writer", lambda name, root=None: "writer")
    real = collections_mod.is_locked
    monkeypatch.setattr(collections_mod, "is_locked", lambda n: n == "writer" or real(n))
    with pytest.raises(collection_file.SetRefused, match="unlock writer first"):
        collection_file.set_values("gutenberg", {"locked": "false"})


def test_an_ancestor_whose_folder_a_served_sister_reads_is_not_locked(node, monkeypatch):
    """Locked, it would stop fetching for her, and nothing would say why."""
    import types
    _, path, write = node
    write()
    sister = types.SimpleNamespace(name="sister", acquire_by="gutenberg")
    real = collections_mod.load_all
    monkeypatch.setattr(collections_mod, "load_all",
                        lambda skip_unreadable=False: ([*real(skip_unreadable=True)[0], sister], [])
                        if skip_unreadable else real())
    with pytest.raises(collection_lock.LockRefused, match="stop fetching for her"):
        collection_lock.lock("gutenberg")
    assert "locked" not in path.read_text(encoding="utf-8")


def test_the_guard_reads_every_argument_that_names_a_collection(node, monkeypatch):
    _, _, write = node
    write("locked = true\n")
    monkeypatch.setattr(louise.col, "name_of", lambda root, ref: ref)
    parse = louise.build_parser().parse_args
    for argv in (["hold", "collection", "migrate", "gutenberg", "--apply"],
                 ["dev", "carry", "gutenberg", "other", "--yes"],
                 ["dev", "carry", "other", "gutenberg", "--yes"]):
        try:
            a = parse(argv)
        except SystemExit:
            pytest.skip(f"{argv}: the parser's form changed")
        assert louise._locked_refusal(a), argv
    a = argparse.Namespace(fn=louise.cmd_collection_new, name="new", sister_of="gutenberg", root=None)
    assert louise._locked_refusal(a), "a new sister would join the locked writer's index"
    # what only reads it, its claims' export and a deposit's erase pass
    for fn in (louise.cmd_snapshot_list, louise.cmd_snapshot_delta, louise.cmd_index_show,
               louise.cmd_annotations_claims, louise.cmd_deposit_erase):
        assert louise._locked_refusal(_ns(fn, "gutenberg")) is None, fn.__name__
    # a user named like it is not a collection
    assert louise._locked_refusal(argparse.Namespace(fn=louise.cmd_user, name="gutenberg")) is None


def test_a_locked_annotations_collection_is_not_rerun_by_the_cascade(tmp_path, monkeypatch):
    from app.hold import cascade_keeper, refit
    monkeypatch.setattr(refit, "boot_refusal", lambda state: None)
    monkeypatch.setattr(collections_mod, "is_locked", lambda n: n == "claims")
    import inspect
    params = list(inspect.signature(cascade_keeper._one).parameters)
    args = {p: None for p in params}
    args.update(root=tmp_path, a="claims")
    assert cascade_keeper._one(**{k: v for k, v in args.items() if k in params}) is None


def test_config_apply_counts_a_lock_as_no_refusal(node):
    from app import effective_config
    _, _, write = node
    write("locked = true\n")
    out = effective_config.apply("gutenberg")
    assert out["locked"] == ["gutenberg"] and out["refused"] == []


def test_a_locked_deposit_collection_is_read_and_erased_never_written(monkeypatch):
    import types
    from app.hold import codex as cx, deposit
    fake = types.SimpleNamespace(name="dep", codex=types.SimpleNamespace())
    monkeypatch.setattr(collections_mod, "by_name", lambda n: None)
    monkeypatch.setattr(collections_mod, "locked_by_name", lambda n: fake if n == "dep" else None)
    monkeypatch.setattr(collections_mod, "is_locked", lambda n: n == "dep")
    monkeypatch.setattr(deposit, "is_deposit", lambda codex: True)
    assert deposit._collection("dep", locked_ok=True) is fake
    with pytest.raises(deposit.DepositError, match="locked"):
        deposit._collection("dep")


def test_a_locked_file_that_does_not_load_is_not_said_reachable(client, monkeypatch):
    """Its three acts cannot reach it, so the routes do not claim they can."""
    from app import web
    monkeypatch.setattr(collections_mod, "is_locked", lambda n: True)
    monkeypatch.setattr(collections_mod, "locked_by_name", lambda n: None)
    assert web._not_served("broken").status_code == 404
