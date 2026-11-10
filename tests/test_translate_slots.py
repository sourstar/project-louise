"""MADLAD's slots (app/translate_slots.py): the platform file's `[translate]
slots` applied to the model server's own configuration through LocalAI's
model-config API; the client reads the server's `parallel:N` (RULE 2)."""
import pytest

from app import config, httpc, platform_config, translate, translate_slots as ts


class Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        return self._body


@pytest.fixture
def server(monkeypatch):
    """A LocalAI holding MADLAD at parallel:7, recording every call."""
    st = {"options": ["parallel:7", "cache_ram:0", "ubatch:1024"], "calls": [], "patch_status": 200}

    def get(url, timeout):
        st["calls"].append(("GET", url))
        return Resp(body={"name": "madlad", "options": list(st["options"])})

    def patch(url, json, timeout):
        st["calls"].append(("PATCH", url, json))
        if st["patch_status"] != 200:
            return Resp(st["patch_status"], text="invalid config")
        st["options"] = list(json["options"])
        return Resp(body={"success": True, "pending_cleanup": 0})
    monkeypatch.setattr(httpc, "get", get)
    monkeypatch.setattr(httpc, "patch", patch)
    ts.reset()
    yield st
    ts.reset()


def test_the_setting_is_written_once_and_only_its_option_changes(server):
    got = ts.apply("http://ai:8080", "madlad", 4)
    assert got == {"set": 4, "from": 7, "pending_cleanup": 0}
    assert server["options"] == ["cache_ram:0", "ubatch:1024", "parallel:4"], "the other options kept"
    assert server["calls"][-1] == ("PATCH", "http://ai:8080/api/models/config-json/madlad",
                                   {"options": ["cache_ram:0", "ubatch:1024", "parallel:4"]})
    n = len(server["calls"])
    assert ts.apply("http://ai:8080", "madlad", 4) == {"unchanged": 4}
    assert [c[0] for c in server["calls"][n:]] == ["GET"], "nothing differs: one read, no write"


def test_no_setting_writes_nothing_and_a_refusal_is_said(server):
    assert "skipped" in ts.apply("http://ai:8080", "madlad", None) and server["calls"] == []
    server["patch_status"] = 422
    got = ts.apply("http://ai:8080", "madlad", 3)
    assert "refused" in got["failed"] and server["options"][0] == "parallel:7"


def test_the_client_sends_one_wave_of_the_servers_own_slots(server, monkeypatch):
    monkeypatch.setattr(config, "TRANSLATE_URL", "http://ai:8080")
    monkeypatch.setattr(config, "TRANSLATE_MODEL", "madlad")
    assert translate.t5_concurrency() == 7
    ts.apply("http://ai:8080", "madlad", 4)
    assert translate.t5_concurrency() == 4, "the write drops the cached read"
    monkeypatch.setattr(httpc, "get", lambda url, timeout: (_ for _ in ()).throw(OSError("down")))
    assert translate.t5_concurrency() == 4, "unreadable: the last known count"
    ts.reset()
    assert translate.t5_concurrency() == translate.T5_CONCURRENCY, "never read: the default"


def test_the_worker_loop_applies_the_file_and_says_a_change_once(server, monkeypatch):
    monkeypatch.setattr(config, "TRANSLATE_URL", "http://ai:8080")
    monkeypatch.setattr(config, "TRANSLATE_MODEL", "madlad")
    said, rounds = [], iter([False, False, False, True])
    ts.reconcile(lambda: next(rounds), log=lambda lvl, line: said.append((lvl, line)),
                 sleep=lambda s: None, desired=lambda: 4)
    assert server["options"][-1] == "parallel:4"
    assert said == [("info", "translation: madlad slots 7 -> 4 ([translate] slots)")]


def test_the_platform_file_key(tmp_path):
    p = tmp_path / "platform.toml"
    p.write_text("[translate]\nslots = 4\n")
    assert platform_config._load_one(p).translate.slots == 4
    p.write_text("[translate]\nreading = { la = \"index-translate-9b\" }\n")
    assert platform_config._load_one(p).translate.slots is None, "absent: the model's file is left alone"
    for bad in ("0", "33", '"4"', "true"):
        p.write_text(f"[translate]\nslots = {bad}\n")
        with pytest.raises(Exception, match="slots"):
            platform_config._load_one(p)
