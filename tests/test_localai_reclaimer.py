"""LOCALAI'S MEMORY RECLAIMER FOLLOWS THE RULE IN FORCE (operator, 2026-11-25:
"A for paging"; app/localai_reclaimer.py, app/resident.py `_reclaimer_follows`).

The node, 2026-11-25: `memory_reclaimer_enabled` true in query mode, where
the card holds more than its size, and one model evicted a minute after the
switch ("space is claimed"). Off where idle models are paged — query mode,
or `[models] make_room = "page"` — on otherwise; held by the keeper's beat,
so a switch moves it and a deploy that put the file's value back is
corrected. The settings object carries `api_keys` and the p2p token: nothing
of it but the one key is ever kept or said (invariant 8)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import httpc, localai_reclaimer, node_mode, resident
from tests.test_resident import SERVER, node  # noqa: F401 — `node` is a fixture

SECRET = "sk-THE-KEY-NOBODY-MAY-SEE"


class LocalAI:
    """A LocalAI whose settings carry secrets, as the node's do."""

    def __init__(self, enabled: bool = True, **more) -> None:
        self.settings = {"memory_reclaimer_enabled": enabled, "memory_reclaimer_threshold": 0.98,
                         "api_keys": [SECRET], "p2p_token": SECRET, "watchdog_idle_enabled": False, **more}
        self.posts: list[dict] = []
        self.gets = 0
        self.refuse: int | None = None
        self.ignore = False

    def get(self, url, **kw):
        assert url == f"{SERVER}/api/settings"
        self.gets += 1
        return SimpleNamespace(status_code=200, json=lambda: dict(self.settings))

    def post(self, url, json=None, **kw):
        assert url == f"{SERVER}/api/settings"
        self.posts.append(dict(json))
        if self.refuse:
            return SimpleNamespace(status_code=self.refuse, json=lambda: {"error": SECRET}, text=SECRET)
        if not self.ignore:
            self.settings = {**dict(json), **{k: v for k, v in self.settings.items() if k not in json}}
        return SimpleNamespace(status_code=200, json=lambda: {})


@pytest.fixture
def localai(monkeypatch):
    la = LocalAI()
    monkeypatch.setattr(httpc, "get", la.get)
    monkeypatch.setattr(httpc, "post", la.post)
    return la


def test_the_reclaimer_is_on_unless_the_rule_pages():
    assert localai_reclaimer.wanted("unload") is True and localai_reclaimer.wanted("page") is False


def test_it_is_turned_off_by_writing_the_whole_object_back_with_one_key_changed(localai):
    assert localai_reclaimer.set_enabled(SERVER, False) is True
    (body,) = localai.posts
    assert body["memory_reclaimer_enabled"] is False
    assert {k: v for k, v in body.items() if k != "memory_reclaimer_enabled"} == \
        {k: v for k, v in LocalAI().settings.items() if k != "memory_reclaimer_enabled"}, \
        "every other key as it was read — the threshold, and the keys — a partial body is not known to keep them"
    assert localai.settings["memory_reclaimer_enabled"] is False and localai.gets == 2, "read, written, read again"
    assert localai_reclaimer.set_enabled(SERVER, False) is False and len(localai.posts) == 1, "already so: no write"


def test_the_marker_and_the_keys_the_addon_owner_holds_are_left_out_of_the_write(monkeypatch):
    la = LocalAI(addon_host_settings_authority="owner_required", auto_upgrade_addons=True)
    monkeypatch.setattr(httpc, "get", la.get)
    monkeypatch.setattr(httpc, "post", la.post)
    localai_reclaimer.set_enabled(SERVER, False)
    assert "addon_host_settings_authority" not in la.posts[0] and "auto_upgrade_addons" not in la.posts[0]


@pytest.mark.parametrize("how", ["refused", "ignored", "unreadable"])
def test_a_write_that_is_not_taken_is_said_without_a_byte_of_the_object(localai, monkeypatch, how):
    if how == "refused":
        localai.refuse = 409
    elif how == "ignored":
        localai.ignore = True
    else:
        def boom(url, **kw):
            raise RuntimeError(f"connection reset while reading {SECRET}")
        monkeypatch.setattr(httpc, "get", boom)
    with pytest.raises(localai_reclaimer.NotTaken) as e:
        localai_reclaimer.set_enabled(SERVER, False)
    assert SECRET not in str(e.value) and SECRET not in repr(e.value)
    assert e.value.__cause__ is None and (e.value.__context__ is None or e.value.__suppress_context__), \
        "no chained exception that a traceback would print the answer from"


# ---- the keeper holds it to the rule in force ---------------------------------

def _keeper(monkeypatch, la: LocalAI) -> list[tuple[str, str]]:
    said: list[tuple[str, str]] = []
    resident.set_log(lambda level, text: said.append((level, text)))
    monkeypatch.setattr(httpc, "get", la.get)
    monkeypatch.setattr(httpc, "post", la.post)
    return said


def test_in_query_mode_the_keeper_turns_it_off_and_on_again_when_the_mode_is_left(node, monkeypatch):
    la = LocalAI(enabled=True)
    said = _keeper(monkeypatch, la)
    assert resident._threshold(SERVER) == 0.98                  # the beat's own reading of the settings
    assert resident._reclaimer_follows([SERVER]) == [], "custom mode, the file says unload: on, as it is"
    assert la.posts == []
    node_mode.switch("query", by="test")
    resident._threshold(SERVER)
    gets = la.gets
    assert resident._reclaimer_follows([SERVER]) == [SERVER]
    assert la.settings["memory_reclaimer_enabled"] is False and la.settings["api_keys"] == [SECRET]
    assert any("memory reclaimer turned OFF" in t and "query mode" in t for _l, t in said), said
    assert not any(SECRET in t for _l, t in said)
    assert la.gets == gets + 2, "a read before the write and one after it — only when something is to change"
    resident._threshold(SERVER)
    gets = la.gets
    assert resident._reclaimer_follows([SERVER]) == [] and la.gets == gets, "as wanted: nothing read, nothing written"
    # a deploy's stage script put the file's value back: corrected at the next beat
    la.settings["memory_reclaimer_enabled"] = True
    resident._threshold(SERVER)
    assert resident._reclaimer_follows([SERVER]) == [SERVER] and la.settings["memory_reclaimer_enabled"] is False
    node_mode.switch("embed", by="test")
    resident._threshold(SERVER)
    assert resident._reclaimer_follows([SERVER]) == [SERVER]
    assert la.settings["memory_reclaimer_enabled"] is True, "the rule unloads for room again: the reclaimer is its net"
    assert any("memory reclaimer turned ON" in t for _l, t in said)
    resident.set_log(lambda level, text: None)


def test_a_reclaimer_that_cannot_be_set_is_said_once_and_asked_again(node, monkeypatch):
    la = LocalAI(enabled=True)
    la.refuse = 409
    said = _keeper(monkeypatch, la)
    node_mode.switch("query", by="test")
    for _ in range(3):
        resident._threshold(SERVER)
        assert resident._reclaimer_follows([SERVER]) == []
    warns = [t for level, t in said if level == "warn" and "memory reclaimer" in t]
    assert len(warns) == 1 and "should be OFF" in warns[0] and "HTTP 409" in warns[0], warns
    assert len(la.posts) == 3 and not any(SECRET in t for _l, t in said), "asked at each beat, said once"
    la.refuse = None
    resident._threshold(SERVER)
    assert resident._reclaimer_follows([SERVER]) == [SERVER]
    resident.set_log(lambda level, text: None)


def test_settings_that_do_not_say_are_left_alone(node, monkeypatch):
    la = LocalAI()
    del la.settings["memory_reclaimer_enabled"]                  # a runner that is not LocalAI's, or an older one
    _keeper(monkeypatch, la)
    node_mode.switch("query", by="test")
    resident._threshold(SERVER)
    assert resident._reclaimer_follows([SERVER]) == [] and la.posts == []
    resident.set_log(lambda level, text: None)
