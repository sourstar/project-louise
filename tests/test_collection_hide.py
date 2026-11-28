"""A HIDDEN collection (operator, 2026-11-28: "Implement a "hide" check per
collection that hides the collection to the non admin users … Same for
annotations, I may want to keep some annotations for admin purpose only";
07 §7.8 `hidden`; docs/notes/collection-hide.md).

The rule under test: `hidden = true` in the file changes nothing for the
node or an admin, and to every other viewer — a user, the depositor, a guest,
nobody — the collection does not exist: not listed, not searched, not read,
and an annotations collection's claims are not consulted."""
import concurrent.futures as cf
import types

import pytest
from fastapi import HTTPException

from app import auth, cancel, collection_file, collections as collections_mod, query_service, viewing
from app.hold import collection as col
from app.hold.codex import CodexError
from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed, fake_query_embed
from tests.hold.test_template import recipe

ENCODER = ('\n[encoder]\nurl = "http://enc:8080"\napi = "openai"\n'
           'embed_model = "m"\nquery_model = "m"\n')
USER = auth.Viewer("anna", "user")
ADMIN = auth.Viewer("boss", "admin")
MASTER = auth.Viewer("admin", "admin", master=True)
DEPOSIT = auth.Viewer("deposit", "deposit")


@pytest.fixture
def node(tmp_path, isolated_config, monkeypatch):
    """Two collections: `gutenberg`, and `secret`, the one that gets hidden."""
    root = tmp_path / "louise"
    (root / "collections").mkdir(parents=True)
    cfg = tmp_path / "config"
    (cfg / "collections").mkdir(parents=True, exist_ok=True)
    (cfg / "collections" / "gutenberg.toml").unlink()
    monkeypatch.setenv("LOUISE_ROOT", str(root))
    monkeypatch.setenv("CONFIG_DIR", str(cfg))
    monkeypatch.setattr(collections_mod.config, "DATA_DIR", root)
    paths = {}
    for name, tokens in (("gutenberg", 5_000_000), ("secret", 6_000_000)):
        cx = col.create_from_recipe(
            root / "collections", "gutenberg", recipe(work={"tokens": tokens}),
            platform_image=PLATFORM, embed=fake_embed, embed_query=fake_query_embed,
            name=name, key=KEY, now=NOW)
        paths[name] = (cfg / "collections" / f"{name}.toml", cx)

    def write(name, extra=""):
        path, cx = paths[name]
        path.write_text(f'codex = "{cx.collection_id}"\nshare = true\n{extra}' + ENCODER,
                        encoding="utf-8")
        return path

    write("gutenberg")
    write("secret", "hidden = true\n")
    return root, write


def _names(cs):
    return sorted(c.name for c in cs)


# ---- the file's key ---------------------------------------------------------

def test_hidden_is_read_from_the_file_and_is_true_or_false(node):
    _, write = node
    by = {c.name: c for c in collections_mod.load_all()}
    assert by["secret"].hidden is True and by["gutenberg"].hidden is False
    path = write("secret", 'hidden = "yes"\n')
    with pytest.raises(ValueError, match="hidden is true or false"):
        collections_mod._load_one(path)


def test_a_hidden_collection_runs_as_any_other(node):
    """The worker's listing, the operator's: hiding changes nothing it does."""
    assert _names(collections_mod.load_all()) == ["gutenberg", "secret"]
    served, unreadable = collections_mod.load_all(skip_unreadable=True)
    assert _names(served) == ["gutenberg", "secret"] and unreadable == []


def test_the_box_writes_one_line_and_keeps_the_rest(node):
    _, write = node
    out = collection_file.set_values("gutenberg", {"hidden": "true"})
    assert out["hidden"] is True and out["share"] is True
    _c, _raw, path = collection_file.load("gutenberg")
    assert "hidden = true" in path.read_text() and "share = true" in path.read_text()
    assert collections_mod.by_name("gutenberg").hidden is True
    collection_file.set_values("gutenberg", {"hidden": "false"})
    assert collections_mod.by_name("gutenberg").hidden is False
    assert "hidden" not in path.read_text(), "absent = false: written only when true"


def test_a_locked_file_takes_no_hide(node):
    """A locked collection is kept as it is: only its lock and its title."""
    _, write = node
    write("gutenberg", "locked = true\n")
    with pytest.raises(collection_file.SetRefused, match="locked"):
        collection_file.set_values("gutenberg", {"hidden": "true"})


# ---- who sees it -------------------------------------------------------------

@pytest.mark.parametrize("viewer,sees", [
    (viewing.OPERATOR, True), (MASTER, True), (ADMIN, True),
    (USER, False), (DEPOSIT, False), (auth.GUEST, False), (None, False)])
def test_only_an_admin_and_the_operator_see_a_hidden_collection(viewer, sees):
    hidden = types.SimpleNamespace(hidden=True)
    shown = types.SimpleNamespace(hidden=False)
    assert collections_mod.hidden_from(viewer, hidden) is (not sees)
    assert collections_mod.hidden_from(viewer, shown) is False


def test_outside_a_request_a_process_is_the_operator_and_a_web_process_nobody(monkeypatch):
    assert viewing.current() is viewing.OPERATOR
    with viewing.as_viewer(USER):
        assert viewing.current() is USER
    assert viewing.current() is viewing.OPERATOR
    monkeypatch.setattr(viewing, "_default", viewing.OPERATOR)
    viewing.serve_readers()
    assert viewing.current() is None, "fail-closed: a thread that lost the viewer is nobody"


def test_a_search_pool_thread_carries_the_viewer(monkeypatch):
    """`cancel.carry` is how the search's pools keep the request's state."""
    monkeypatch.setattr(viewing, "_default", None)          # the web process
    with viewing.as_viewer(ADMIN):
        with cf.ThreadPoolExecutor(2) as ex:
            got = list(ex.map(cancel.carry(lambda _i: viewing.current()), range(4)))
            bare = ex.submit(viewing.current).result()
    assert got == [ADMIN] * 4
    assert bare is None, "a thread not carried is nobody, never the operator"


def test_the_gate_puts_the_viewer_on_every_request(isolated_config, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setattr(auth.config, "ADMIN_TOKEN", "t0k3n-for-the-test")
    app = FastAPI()
    app.add_middleware(auth.ViewerGate)

    @app.get("/ask/probe")
    def probe():
        v = viewing.current()
        return {"role": getattr(v, "role", None), "sees_all": viewing.sees_all(v)}

    c = TestClient(app)
    assert c.get("/ask/probe").json() == {"role": "guest", "sees_all": False}
    assert c.get("/ask/probe", headers={"X-Admin-Token": "t0k3n-for-the-test"}).json() == \
        {"role": "admin", "sees_all": True}
    assert viewing.current() is viewing.OPERATOR, "the request's viewer does not outlive it"


# ---- the reader routes' listings --------------------------------------------

def test_lists_and_searches_leave_it_out_for_a_reader(node):
    with viewing.as_viewer(USER):
        assert _names(collections_mod.load_public()) == ["gutenberg"]
        assert _names(query_service.resolve_collections(None)) == ["gutenberg"]
        assert all(w["collection"] != "secret" for w in query_service.withheld_from(None))
    for v in (ADMIN, MASTER, viewing.OPERATOR):
        with viewing.as_viewer(v):
            assert _names(collections_mod.load_public()) == ["gutenberg", "secret"]
            assert _names(query_service.resolve_collections(None)) == ["gutenberg", "secret"]


def test_named_it_is_the_name_nothing_holds(node):
    """The same answer as a name nothing holds: the route never says it exists."""
    with viewing.as_viewer(USER):
        with pytest.raises(HTTPException) as e:
            query_service.resolve_collections(["secret"])
        assert e.value.status_code == 400 and "unknown collection" in e.value.detail
        with pytest.raises(HTTPException) as e2:
            query_service.resolve_collections(["nothing-here"])
        assert e2.value.status_code == e.value.status_code
    with viewing.as_viewer(ADMIN):
        assert _names(query_service.resolve_collections(["secret"])) == ["secret"]


def test_the_routes_on_disk_shut_it_and_a_deposit_one(node, monkeypatch):
    """The graph walk, facets and figures read the collections on disk: a
    hidden one is shut to a reader, and a deposit one to anyone but the
    operator (07 §7.4, as `load_public`)."""
    root, _ = node
    real = collections_mod.is_private
    monkeypatch.setattr(collections_mod, "is_private", lambda c: c.name == "gutenberg" or real(c))
    with viewing.as_viewer(USER):
        assert collections_mod.closed_names_here() == {"gutenberg", "secret"}
        assert collections_mod.listed_seen(root / "collections") == []
        with pytest.raises(CodexError, match="no collection 'secret' here"):
            collections_mod.resolve_seen(root / "collections", "secret")
    with viewing.as_viewer(ADMIN):
        assert collections_mod.closed_names_here() == {"gutenberg"}
        assert [n for n, _ in collections_mod.listed_seen(root / "collections")] == ["secret"]
    assert collections_mod.closed_names_here() == set(), "the operator sees all on disk"


def test_the_default_collection_of_the_api_is_one_the_reader_may_see(node, monkeypatch):
    from app import api
    monkeypatch.setattr(collections_mod, "default_collection", lambda: collections_mod.by_name("secret"))
    with viewing.as_viewer(USER):
        assert api._collection().name == "gutenberg"
    with viewing.as_viewer(ADMIN):
        assert api._collection().name == "secret"


# ---- annotations ------------------------------------------------------------

def _ann(name, hidden=False):
    return types.SimpleNamespace(name=name, hidden=hidden, codex=types.SimpleNamespace(collection_id=f"c-{name}"))


def test_hidden_annotations_are_never_consulted_for_a_reader(monkeypatch):
    from app.ask import annotations as ask_ann
    held = [_ann("works"), _ann("admin-notes", hidden=True)]
    monkeypatch.setattr(collections_mod, "load_annotations", lambda: held)
    monkeypatch.setattr(collections_mod, "annotations_and_refusals", lambda: (held, []))
    monkeypatch.setattr(collections_mod, "_held_files", lambda: held)
    with viewing.as_viewer(USER):
        assert [a.name for a in ask_ann._held()] == ["works"]
        assert [a.name for a in ask_ann._held(held)] == ["works"], "a list given is filtered too"
        with pytest.raises(HTTPException) as e:
            query_service.resolve_annotations(["admin-notes"])
        assert e.value.status_code == 400 and "admin-notes" not in e.value.detail.split("held here:")[1]
    with viewing.as_viewer(ADMIN):
        assert [a.name for a in ask_ann._held()] == ["works", "admin-notes"]
        assert [a.name for a in query_service.resolve_annotations(["admin-notes"])] == ["admin-notes"]


# ---- a result kept for the answer -------------------------------------------

def test_an_admins_kept_result_is_never_a_readers_answer():
    from app import web
    with viewing.as_viewer(ADMIN):
        sid = web.keep_search("q", {"hits": ["from a hidden one"]})
        assert web.kept_search(sid, "q") == {"hits": ["from a hidden one"]}
    with viewing.as_viewer(USER):
        assert web.kept_search(sid, "q") is None
        mine = web.keep_search("q", {"hits": []})
    with viewing.as_viewer(ADMIN):
        assert web.kept_search(mine, "q") == {"hits": []}, "a reader's result is anyone's to reuse"


# ---- the machine interfaces -------------------------------------------------

def test_a_vector_store_and_localagi_search_do_not_find_it(node):
    from starlette.requests import Request
    from app import openai_stores
    req = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    with viewing.as_viewer(DEPOSIT):
        with pytest.raises(openai_stores.OpenAIError) as e:
            openai_stores._store(req, "secret")
        assert e.value.status == 404
        assert openai_stores._store(req, "gutenberg").name == "gutenberg"
    with viewing.as_viewer(MASTER):
        assert openai_stores._store(req, "secret").name == "secret"
