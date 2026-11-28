"""THE USERS TAB (operator, 2026-11-28): "on top a drop down menu to select
the user. On the tab the user's first name and name (both optional), then the
admin flag (off by default), the list of the models [collections] with the
hide flag (off by default), the list of the annotations with the hide flag
(off by default) and the list of the voices" — "every voice disabled by
default: if there is no voice, the read aloud feature is disabled (not
shown)". The voice side is tests/test_voice_roles.py's."""
import types

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth, collections as collections_mod, config, query_service, users, viewing
from tests.test_collection_hide import node  # noqa: F401  (the fixture: gutenberg and secret)

BEA = auth.Viewer("bea", "user")
CARLO = auth.Viewer("carlo", "user")
ADMIN = {"X-Admin-Token": "sekrit"}


def _names(cs):
    return sorted(c.name for c in cs)


def test_the_tab_values_are_kept_in_users_toml_and_read_back(isolated_config):
    users.add("bea")
    users.set_profile("bea", first_name="Bea", last_name="Rossi", hide_collections=["sha256:a"],
                      hide_annotations=["sha256:b"], voices=["v1", "v2"])
    users._cache = None
    (u,) = users.load()
    assert (u.first_name, u.last_name) == ("Bea", "Rossi")
    assert u.hide_collections == ("sha256:a",) and u.hide_annotations == ("sha256:b",) and u.voices == ("v1", "v2")
    users.set_profile("bea", first_name="", voices=[])
    (u,) = users.load()
    assert u.first_name is None and u.last_name == "Rossi" and u.voices == (), "a value left out is kept"
    users.set_guest(voices=["v1"])
    assert users.load_guest().voices == ("v1",) and users.load()[0].name == "bea", "the guest beside the accounts"


def test_by_default_nothing_is_hidden_and_no_voice_is_on(isolated_config):
    users.add("bea")
    assert users.hidden_ids(BEA) == frozenset() and users.voices_allowed(BEA) == frozenset()
    assert users.voices_allowed(auth.GUEST) == frozenset()
    assert users.voices_allowed(auth.Viewer("admin", "admin", master=True)) is None, "an admin: every voice"
    assert users.hidden_ids(viewing.OPERATOR) == frozenset()


def test_a_collection_hidden_for_one_reader_is_hidden_for_that_reader_alone(node):
    users.add("bea")
    users.add("carlo")
    secret = collections_mod.by_name("secret")
    collections_mod.load_all()      # secret is hidden for all in the fixture: show it to readers first
    collection_file = __import__("app.collection_file", fromlist=["set_values"])
    collection_file.set_values("secret", {"hidden": "false"})
    gut = collections_mod.by_name("gutenberg")
    users.set_profile("bea", hide_collections=[gut.codex.collection_id])
    with viewing.as_viewer(BEA):
        assert _names(query_service.resolve_collections(None)) == ["secret"]
        with pytest.raises(HTTPException) as e:
            query_service.resolve_collections(["gutenberg"])
        assert "unknown collection" in e.value.detail, "the name nothing holds"
        assert "gutenberg" in collections_mod.hidden_names_here()
    with viewing.as_viewer(CARLO):
        assert _names(query_service.resolve_collections(None)) == ["gutenberg", "secret"]
    with viewing.as_viewer(auth.Viewer("admin", "admin", master=True)):
        assert _names(query_service.resolve_collections(None)) == ["gutenberg", "secret"]
    assert secret is not None


def test_the_guests_entry_hides_for_readers_with_no_account(node):
    gut = collections_mod.by_name("gutenberg")
    users.set_guest(hide_collections=[gut.codex.collection_id])
    with viewing.as_viewer(auth.GUEST):
        assert "gutenberg" not in _names(collections_mod.load_public())
    with viewing.as_viewer(None):
        assert "gutenberg" not in _names(collections_mod.load_public()), "nobody: the guests' entry"


def test_an_annotations_collection_hidden_for_a_reader_is_not_consulted_for_them(monkeypatch, isolated_config):
    from app.ask import annotations as ask_ann
    held = [types.SimpleNamespace(name="works", hidden=False, codex=types.SimpleNamespace(collection_id="c-works")),
            types.SimpleNamespace(name="notes", hidden=False, codex=types.SimpleNamespace(collection_id="c-notes"))]
    monkeypatch.setattr(collections_mod, "load_annotations", lambda: held)
    users.add("bea")
    users.set_profile("bea", hide_annotations=["c-notes"])
    with viewing.as_viewer(BEA):
        assert [a.name for a in ask_ann._held()] == ["works"]
    with viewing.as_viewer(CARLO):
        assert [a.name for a in ask_ann._held()] == ["works", "notes"]


def test_the_last_admin_keeps_the_flag_when_no_admin_token_is_set(isolated_config, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", None)
    users.add("ada", role="admin")
    with pytest.raises(users.UserError, match="last admin"):
        users.set_role("ada", "user")
    users.add("bob", role="admin")
    assert users.set_role("ada", "user").role == "user", "another admin exists"
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    assert users.set_role("bob", "user").role == "user", "the admin token remains"


def test_a_chosen_voice_no_longer_enabled_is_dropped(isolated_config):
    users.add("bea")
    users.set_profile("bea", voices=["v1"])
    users.set_voice("bea", "v1")
    users.set_profile("bea", voices=["v2"])
    assert users.voice_of("bea") is None


@pytest.fixture
def client(isolated_config, monkeypatch):
    from tests.test_web_pages import _patch_hash, _patch_health_pings
    _patch_hash(monkeypatch)
    _patch_health_pings(monkeypatch)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    with TestClient(__import__("app.api", fromlist=["app"]).app) as c:
        yield c


def test_the_tab_shows_the_dropdown_and_saves_each_value(client):
    users.add("bea")
    page = client.get("/admin/users?u=bea", headers=ADMIN)
    assert page.status_code == 200
    assert 'id="user-pick"' in page.text and "Readers with no account" in page.text
    assert 'data-admin' in page.text and "Collections" in page.text and "Voices" in page.text
    assert 'href="/admin/users"' in page.text, "the tab in the menu"
    r = client.patch("/admin/api/users/bea", headers=ADMIN,
                     json={"first_name": "Bea", "hide_collections": ["sha256:x"], "voices": ["v1"]})
    assert r.status_code == 200 and r.json()["first_name"] == "Bea" and r.json()["voices"] == ["v1"]
    assert client.patch("/admin/api/users/bea", headers=ADMIN, json={"role": "admin"}).json()["role"] == "admin"
    g = client.patch("/admin/api/guest", headers=ADMIN, json={"voices": ["v2"]})
    assert g.status_code == 200 and g.json()["voices"] == ["v2"]
    assert client.get("/admin/users?u=guests", headers=ADMIN).status_code == 200
    assert client.get("/admin/users", headers={"Authorization": "Bearer nope"}).status_code in (401, 403)
