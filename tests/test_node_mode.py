"""The node's two modes of use — EMBED and CHAT (operator, 2026-11-25).

  "1. embed mode, intended for heavy embedding (system RAM mostly consumed by
   embedders) in which the smaller chat model is used  2. chat mode in which
   the 35B model is used (system RAM mostly consumed by chat model and
   embedders paging).  3. In embed mode switch to run all  4. In system tab
   shall be selectable for chat mode a run mode among download/seeding,
   seeding or pause.  5. For both modes a network budged shall be settable
   6. The current chat model setting becomes 2 chat settings, large one and a
   small one  7. The mode shall be selectable through the api as well for
   cron jobs"

One switch moves the chat model, the run mode and the network budget
(app/node_mode.py); which mode the node is in is the node's state and
survives a restart, what each mode means is platform.toml's.
"""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, louise, node_mode, node_state, platform_config, worker
from app.transfer import schedule as sched
from app.transfer.clock import FakeClock
from app.transfer.manager import Direction, TransferManager

MB = 1_000_000


@pytest.fixture
def platform(monkeypatch):
    """This test's platform.toml, and config's chat model resolved from it
    (an earlier test's static patch of CHAT_MODEL undone)."""
    for name in ("CHAT_MODEL", "TRANSLATE_MODEL"):
        if name in vars(config):
            monkeypatch.delattr(config, name)
    monkeypatch.setattr(config, "ENV_CHAT_MODEL", "chat-env")
    path = Path(os.environ["CONFIG_DIR"]) / "platform.toml"

    def write(text: str) -> Path:
        path.write_text(text, encoding="utf-8")
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
        return path
    return write


# ---------------- platform.toml: [modes] and the two chat settings ----------------

def test_a_file_that_says_nothing_has_chat_mode_on_download_and_seeding_and_no_budget(platform):
    pc = platform_config.load()
    assert pc.modes.chat_run == "acquire-only", "the first of the three the operator named"
    assert pc.modes.budget("embed") == (None, None) and pc.modes.budget("chat") == (None, None)
    assert pc.models.chat_large is None and pc.models.chat_small is None


def test_modes_reads_chat_modes_run_mode_and_each_modes_budget(platform):
    platform('[modes]\nchat_run = "seed-only"\nembed_down_max = "400Mbit"\nembed_up_max = "40Mbit"\n'
             'chat_down_max = "20MB"\n')
    m = platform_config.load().modes
    assert m.chat_run == "seed-only"
    assert m.budget("embed") == (50 * MB, 5 * MB), "[budget]'s grammar: bits and bytes"
    assert m.budget("chat") == (20 * MB, None), "a direction the mode does not set is [budget]'s"
    assert m.written("chat_down_max") == "20MB" and m.written("chat_up_max") == ""


@pytest.mark.parametrize("text, says", [
    ('[modes]\nchat_run = "running"\n', "chat_run"),            # running everything is embed mode's
    ('[modes]\nchat_run = "sleep"\n', "chat_run"),
    ('[modes]\nchat_budget = "20MB"\n', "no key chat_budget"),  # a misspelt key is not "no budget"
    ('[modes]\nchat_down_max = "fast"\n', "chat_down_max"),
    ('[modes]\nembed_up_max = true\n', "embed_up_max"),
    ('modes = "chat"\n', "modes must be a table"),
])
def test_a_modes_table_that_does_not_read_is_refused_at_load(platform, text, says):
    platform(text)
    with pytest.raises(sched.ScheduleError, match=says):
        platform_config.load()


def test_the_chat_model_is_the_small_one_in_embed_mode_and_the_large_one_in_chat_mode(platform):
    platform('[models]\nchat_large = "chat-35b"\nchat_small = "chat-9b"\n')
    assert config.chat_model_for("chat") == "chat-35b" and config.chat_model_for("embed") == "chat-9b"
    assert config.CHAT_MODEL == "chat-9b", "a node never switched is in embed mode"
    node_mode.switch("chat", by="test")
    assert config.CHAT_MODEL == "chat-35b", "every reader of config.CHAT_MODEL follows the switch"
    node_mode.switch("embed", by="test")
    assert config.CHAT_MODEL == "chat-9b"


def test_the_one_chat_setting_there_was_still_answers_in_both_modes(platform):
    # a platform.toml written before there were two: nothing changes for it
    platform('[models]\nchat = "chat-one"\n')
    assert config.chat_model_for("chat") == config.chat_model_for("embed") == "chat-one"
    platform('[models]\nchat = "chat-one"\nchat_large = "chat-35b"\n')
    assert config.chat_model_for("chat") == "chat-35b" and config.chat_model_for("embed") == "chat-one"
    platform("")
    assert config.chat_model_for("chat") == config.chat_model_for("embed") == "chat-env", \
        "and with neither, the environment's"


# ---------------- the switch ----------------

def test_a_node_never_switched_is_in_embed_mode_and_its_run_mode_is_untouched(platform):
    assert not node_state.path().exists()
    assert node_mode.current() == "embed"
    assert not node_state.path().exists(), "reading the mode creates nothing"
    node_state.set_worker_state("seed-only")
    assert node_mode.current() == "embed" and node_state.worker_state() == "seed-only", \
        "nothing sets the run mode until someone switches"


def test_a_switch_to_embed_mode_runs_everything(platform):
    # operator: "3. In embed mode switch to run all"
    node_state.set_worker_state("paused")
    got = node_mode.switch("embed", by="test")
    assert got == {"mode": "embed", "before": "embed", "run": "running", "run_before": "paused"}
    assert node_state.worker_state() == "running"


@pytest.mark.parametrize("run", platform_config.CHAT_RUNS)
def test_a_switch_to_chat_mode_sets_the_run_mode_the_file_names(platform, run):
    # operator: "a run mode among download/seeding, seeding or pause"
    platform(f'[modes]\nchat_run = "{run}"\n')
    node_state.set_worker_state("running")
    got = node_mode.switch("chat", by="test")
    assert (got["mode"], got["before"], got["run"], got["run_before"]) == ("chat", "embed", run, "running")
    assert node_mode.current() == "chat" and node_state.worker_state() == run
    assert not node_state.mode_runs(node_state.worker_state(), node_state.EMBED), \
        "chat mode never embeds: the memory is the large chat model's"


def test_the_mode_survives_a_restart_and_who_switched_is_recorded(platform):
    node_mode.switch("chat", by="POST /api/mode from 10.0.0.9 (curl)")
    node_mode.forget()                      # a new process remembers nothing
    assert node_mode.current() == "chat"
    with node_state.connect() as c:         # the store itself, as the worker reads it
        assert node_mode.current(c) == "chat"
    assert [(h["mode"], h["run"], h["by"]) for h in node_mode.history()] == \
        [("chat", "acquire-only", "POST /api/mode from 10.0.0.9 (curl)")]
    last = node_state.mode_history()[-1]
    assert last["state"] == "acquire-only" and "chat mode" in last["by"], \
        "the run mode's own history says the mode set it"


def test_a_cron_jobs_call_means_the_same_thing_every_time(platform):
    node_mode.switch("embed", by="cron")
    node_state.set_worker_state("paused", by="a deploy")     # the operator's, afterwards
    assert node_mode.current() == "embed" and node_state.worker_state() == "paused", \
        "the switch sets the run mode once; it does not hold it"
    node_mode.switch("embed", by="cron")
    assert node_state.worker_state() == "running", "switching to the mode it is in sets its run mode again"


def test_chat_mode_refuses_to_run_everything_and_says_where_to_switch(platform):
    assert node_mode.refuses("running") is None, "embed mode takes every run mode"
    node_mode.switch("chat", by="test")
    said = node_mode.refuses("running")
    assert said and "embed mode" in said and "louise node mode embed" in said and "/api/mode" in said
    assert [node_mode.refuses(s) for s in platform_config.CHAT_RUNS] == [None, None, None]


def test_chat_modes_run_mode_changed_in_the_file_is_taken_at_once_in_chat_mode_only(platform):
    assert node_mode.set_chat_run("paused", by="test") is False, "embed mode: the file only"
    node_state.set_worker_state("running")
    node_mode.switch("chat", by="test")
    assert node_mode.set_chat_run("paused", by="the System page") is True
    assert node_state.worker_state() == "paused"
    assert node_mode.set_chat_run("paused", by="the System page") is False, "already there"
    with pytest.raises(ValueError, match="one of"):
        node_mode.set_chat_run("running")


def test_a_mode_that_is_not_one_of_the_two_is_refused(platform):
    with pytest.raises(ValueError, match="embed or chat"):
        node_mode.switch("turbo")
    assert node_mode.current() == "embed" and node_mode.history() == []


# ---------------- the network budget ----------------

def _manager(down="10MB", up="1MB", schedule_text="", clock=None):
    pc = platform_config.PlatformConfig(bandwidth=platform_config.BandwidthConfig(
        down_max_bps=sched.parse_rate(down), up_max_bps=sched.parse_rate(up),
        schedule=sched.parse_schedule(schedule_text), schedule_text=schedule_text))
    return TransferManager(platform=pc, clock=clock or FakeClock())


def _rates(m):
    return m.bucket(Direction.DOWN).core.rate, m.bucket(Direction.UP).core.rate


def test_a_modes_budget_stands_in_the_budgets_place_and_leaves_what_it_does_not_set():
    # operator: "5. For both modes a network budged shall be settable"
    m = _manager(down="10MB", up="1MB")
    assert m.set_mode_budget(2 * MB, None) is True
    assert _rates(m) == pytest.approx((2 * MB, 1 * MB)), "up: [budget]'s, the mode sets none"
    assert m.set_mode_budget(2 * MB, None) is False, "the same budget again changes nothing"
    assert m.set_mode_budget(50 * MB, 4 * MB) is True
    assert _rates(m) == pytest.approx((50 * MB, 4 * MB)), "a mode may be given MORE than [budget]"
    assert m.set_mode_budget(None, None) is True
    assert _rates(m) == pytest.approx((10 * MB, 1 * MB)), "no mode budget: [budget]'s again"


def test_the_schedule_and_an_override_apply_on_top_of_a_modes_budget():
    clock = FakeClock(start_wall_minutes=13 * 60)             # the 30 % branch
    m = _manager(down="10MB", schedule_text="01:00-07:00 full; else 30%", clock=clock)
    m.set_mode_budget(20 * MB, None)
    assert m.bucket(Direction.DOWN).core.rate == pytest.approx(6 * MB)
    m.set_override(1 * MB)
    assert m.bucket(Direction.DOWN).core.rate == pytest.approx(1 * MB)
    m.set_override(None)
    clock.advance(13 * 3600)                                  # 02:00, the full window
    m.maybe_refresh()
    assert m.bucket(Direction.DOWN).core.rate == pytest.approx(20 * MB)


def test_a_mode_budget_of_zero_moves_nothing():
    m = _manager(down="10MB")
    m.set_mode_budget(0, None)
    assert m.bucket(Direction.DOWN).core.rate == 0


def test_the_budget_in_force_says_whose_each_ceiling_is(platform):
    platform('[budget]\ndown_max = "10MB"\nup_max = "1MB"\n[modes]\nchat_down_max = "2MB"\n')
    pc = platform_config.load()
    assert node_mode.budget("chat", pc) == {"down": 2 * MB, "up": 1 * MB, "down_own": True, "up_own": False}
    assert node_mode.budget("embed", pc) == {"down": 10 * MB, "up": 1 * MB, "down_own": False, "up_own": False}


# ---------------- the worker's round ----------------

def _lines(capsys):
    return [ln for ln in capsys.readouterr().out.splitlines() if "node in " in ln or " mode: " in ln]


def test_the_round_applies_the_modes_budget_and_follows_the_file_without_a_restart(platform, capsys):
    platform('[budget]\ndown_max = "10MB"\nup_max = "1MB"\n[models]\nchat_large = "chat-35b"\n'
             'chat_small = "chat-9b"\n[modes]\nembed_down_max = "8MB"\nchat_down_max = "2MB"\nchat_up_max = "500KB"\n')
    m = TransferManager(platform=platform_config.load(), clock=FakeClock())
    started: list = []
    with node_state.connect() as c:
        assert worker._apply_node_mode(c, manager=m, start=lambda *a: started.append(a)) == "embed"
        assert _rates(m) == pytest.approx((8 * MB, 1 * MB))
        said = _lines(capsys)
        assert len(said) == 1 and "node in embed mode (read): chat model chat-9b" in said[0]
        assert "down 8.0 MB/s, up 1.0 MB/s ([budget])" in said[0]
        assert started == [], "the first read is not a switch: no model is unloaded at boot"

        worker._apply_node_mode(c, manager=m, start=lambda *a: started.append(a))
        assert _lines(capsys) == [], "ONE line: a round that changes nothing says nothing"

        node_mode.switch("chat", by="test")
        assert worker._apply_node_mode(c, manager=m, start=lambda *a: started.append(a)) == "chat"
        assert _rates(m) == pytest.approx((2 * MB, 0.5 * MB))
        assert "node in chat mode (embed -> chat): chat model chat-35b" in _lines(capsys)[0]
        assert started == [("embed", "chat")], "a switch changes the chat model, behind"

        # the page's edit of a budget: the next round has it, no restart
        platform('[budget]\ndown_max = "10MB"\nup_max = "1MB"\n[models]\nchat_large = "chat-35b"\n'
                 'chat_small = "chat-9b"\n[modes]\nchat_down_max = "3MB"\n')
        worker._apply_node_mode(c, manager=m, start=lambda *a: started.append(a))
        assert _rates(m) == pytest.approx((3 * MB, 1 * MB))
        assert "node in chat mode (budget changed)" in _lines(capsys)[0]
        assert started == [("embed", "chat")], "a budget change is not a switch"


class _Runner:
    """What a switch asks of the model server: the unload and the load."""

    def __init__(self, monkeypatch, loaded=("chat-9b",)):
        from app import llm, model_load
        self.calls: list = []
        self.loaded = set(loaded)
        monkeypatch.setattr(config, "CHAT_URL", "http://gpu:8080/v1/chat/completions")
        monkeypatch.setattr(model_load, "unload", self.unload)
        monkeypatch.setattr(llm, "warm", self.warm)

    def unload(self, url, model):
        self.calls.append(("unload", model))
        was = model in self.loaded
        self.loaded.discard(model)
        return was

    def warm(self, *a, **k):
        self.calls.append(("warm", config.CHAT_MODEL))
        self.loaded.add(config.CHAT_MODEL)


def test_a_switch_unloads_the_chat_model_left_and_loads_the_one_entered(platform, monkeypatch, capsys):
    platform('[models]\nchat_large = "chat-35b"\nchat_small = "chat-9b"\n')
    runner = _Runner(monkeypatch)
    node_mode.switch("chat", by="test")
    got = worker._mode_models("embed", "chat", question=lambda: False)
    assert got == {"old": "chat-9b", "new": "chat-35b", "unloaded": True, "loaded": True}
    assert runner.calls == [("unload", "chat-9b"), ("warm", "chat-35b")], \
        "the memory of the model left is given back BEFORE the one entered is loaded"
    assert "chat mode: chat-9b unloaded; chat-35b loaded" in capsys.readouterr().out


def test_a_switch_between_modes_that_name_one_model_unloads_nothing(platform, monkeypatch, capsys):
    platform('[models]\nchat = "chat-one"\n')
    runner = _Runner(monkeypatch, loaded=("chat-one",))
    node_mode.switch("chat", by="test")
    worker._mode_models("embed", "chat", question=lambda: False)
    assert runner.calls == [] and runner.loaded == {"chat-one"}
    assert "both modes name chat-one" in capsys.readouterr().out


def test_a_switch_waits_for_the_answer_in_flight_but_not_for_ever(platform, monkeypatch):
    platform('[models]\nchat_large = "chat-35b"\nchat_small = "chat-9b"\n')
    runner = _Runner(monkeypatch)
    node_mode.switch("chat", by="test")
    asked: list = []

    def question():                         # an answer streams for two looks, then ends
        asked.append(len(runner.calls))
        return len(asked) < 3
    worker._mode_models("embed", "chat", question=question, sleep=lambda s: None)
    assert asked == [0, 0, 0], "nothing was unloaded under the answer"
    assert runner.calls[0] == ("unload", "chat-9b")

    runner.calls.clear()
    worker._mode_models("chat", "embed", wait_s=0.0, question=lambda: True, sleep=lambda s: None)
    assert ("unload", "chat-35b") in runner.calls, "an answer that never ends does not hold the switch"


def test_a_model_that_does_not_load_at_the_switch_is_said_and_does_not_raise(platform, monkeypatch, capsys):
    from app import llm
    platform('[models]\nchat_large = "chat-35b"\nchat_small = "chat-9b"\n')
    _Runner(monkeypatch)
    monkeypatch.setattr(llm, "warm", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("120 s")))
    got = worker._mode_models("embed", "chat", question=lambda: False)
    assert got["loaded"] is False
    assert "did not load now" in capsys.readouterr().out


# ---------------- the command ----------------

def test_node_mode_reads_and_switches(platform, capsys):
    platform('[models]\nchat_large = "chat-35b"\nchat_small = "chat-9b"\n[modes]\nchat_run = "seed-only"\n'
             'chat_down_max = "2MB"\n')
    assert louise.main(["node", "mode"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["mode"] == "embed" and shown["chat_model"] == "chat-9b"
    assert shown["modes"]["chat"] == {"chat_model": "chat-35b", "run": "seed-only",
                                      "budget": {"down": 2 * MB, "up": None}}
    assert shown["modes"]["embed"]["run"] == "running"

    assert louise.main(["node", "mode", "chat"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert (got["mode"], got["before"], got["run"], got["chat_model"]) == ("chat", "embed", "seed-only", "chat-35b")
    assert node_mode.current() == "chat" and "louise node mode chat" in node_mode.history()[-1]["by"]


def test_node_resume_goes_back_to_what_the_mode_runs(platform, capsys):
    platform('[modes]\nchat_run = "seed-only"\n')
    assert louise.main(["node", "pause"]) == 0 and louise.main(["node", "resume"]) == 0
    assert node_state.worker_state() == "running", "embed mode, and a node never switched: everything, as before"
    node_mode.switch("chat", by="test")
    assert louise.main(["node", "pause"]) == 0 and node_state.worker_state() == "paused"
    capsys.readouterr()
    assert louise.main(["node", "resume"]) == 0
    assert node_state.worker_state() == "seed-only", \
        "chat mode: resume never starts the embedders under the large chat model"
    assert json.loads(capsys.readouterr().out)["mode"] == "chat"


# ---------------- the API ----------------

@pytest.fixture
def client(isolated_config, platform, monkeypatch):
    from app.api import app
    monkeypatch.setattr(config, "ADMIN_TOKEN", "sekrit")
    platform('[models]\nchat_large = "chat-35b"\nchat_small = "chat-9b"\n')
    with TestClient(app) as c:
        yield c


ADMIN = {"X-Admin-Token": "sekrit"}


def test_the_mode_is_switched_through_the_api_for_a_cron_job(client):
    # operator: "7. The mode shall be selectable through the api as well for cron jobs"
    assert client.post("/api/mode", json={"mode": "chat"}).status_code in (401, 403), "the admin's only"
    assert client.get("/api/mode", headers=ADMIN).json()["mode"] == "embed"

    r = client.post("/api/mode", json={"mode": "chat"}, headers={**ADMIN, "User-Agent": "cron-curl"})
    assert r.status_code == 200
    got = r.json()
    assert (got["mode"], got["before"], got["run"], got["chat_model"]) == ("chat", "embed", "acquire-only", "chat-35b")
    assert node_mode.current() == "chat" and node_state.worker_state() == "acquire-only"
    assert "POST /api/mode" in node_mode.history()[-1]["by"] and "cron-curl" in node_mode.history()[-1]["by"]
    assert client.get("/api/status").json()["mode"] == "chat"

    r = client.post("/api/mode", json={"mode": "embed"}, headers=ADMIN)
    assert r.json()["mode"] == "embed" and node_state.worker_state() == "running"
    assert client.post("/api/mode", json={"mode": "turbo"}, headers=ADMIN).status_code == 400
    assert node_mode.current() == "embed"


def test_the_control_route_refuses_to_run_everything_in_chat_mode(client):
    node_mode.switch("chat", by="test")
    r = client.post("/api/control", json={"state": "running"}, headers=ADMIN)
    assert r.status_code == 409 and "embed mode" in r.json()["detail"]
    assert node_state.worker_state() == "acquire-only", "said, not done"
    assert client.post("/api/control", json={"state": "paused"}, headers=ADMIN).status_code == 200
    assert node_state.worker_state() == "paused"
    node_mode.switch("embed", by="test")
    node_state.set_worker_state("paused")
    assert client.post("/api/control", json={"state": "running"}, headers=ADMIN).status_code == 200
