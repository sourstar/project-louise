"""tests/test_deploy_set_platform_image.py — the runbook's step that tells
the app's web and worker the image they start in (deploy/16f/
set_platform_image.py). The node declared pubmed's Codex image for two
weeks of later builds (found 2026-10-07)."""
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "deploy" / "16f" / "set_platform_image.py"
_spec = importlib.util.spec_from_file_location("set_platform_image", _PATH)
spi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spi)

OLD = "sha256:" + "85" * 32
NEW = "sha256:" + "29" * 32
_spec = importlib.util.spec_from_file_location("set_commands", _PATH.with_name("set_commands.py"))
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)
#: what deploy/truenas-app.yaml says web and worker run
WANTED = sc.wanted()


def a_config():
    return {"services": {
        "web": {"environment": {"ADMIN_TOKEN": "s3cret", spi.KEY: OLD}},
        "worker": {"environment": {"ADMIN_TOKEN": "s3cret", spi.KEY: OLD, "NODE_KEY_PATH": "/k"}},
        "gpu-probe": {"command": ["x"]}}}


def test_both_services_are_told_and_nothing_else_moves():
    cfg = a_config()
    assert spi.apply(cfg, NEW) == {"web": OLD, "worker": OLD}
    assert cfg["services"]["web"]["environment"] == {"ADMIN_TOKEN": "s3cret", spi.KEY: NEW}
    assert cfg["services"]["worker"]["environment"] == {"ADMIN_TOKEN": "s3cret", spi.KEY: NEW, "NODE_KEY_PATH": "/k"}
    assert cfg["services"]["gpu-probe"] == {"command": ["x"]}, "the probe carries no environment, by design"


def test_a_config_it_does_not_recognise_is_left_untouched():
    with pytest.raises(ValueError, match="sha256"):
        spi.apply(a_config(), "85fd59f1")
    cfg = a_config()
    cfg["services"]["worker"]["environment"] = ["A=1"]
    with pytest.raises(ValueError, match="not a mapping"):
        spi.apply(cfg, NEW)
    assert cfg["services"]["web"]["environment"][spi.KEY] == OLD, "refused before any change"
    with pytest.raises(ValueError, match="services"):
        spi.apply({}, NEW)


def test_the_output_names_the_variable_and_never_another_value(monkeypatch, capsys):
    import sys
    import types
    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def call(self, method, *args, **kw):
            calls.append(method)
            if method == "app.config":
                return a_config() if calls.count("app.config") == 1 else spi.apply(a_config(), NEW) and \
                    {"services": {s: {"environment": {spi.KEY: NEW}, "command": WANTED[s]} for s in spi.SERVICES}}
            return None
    monkeypatch.setitem(sys.modules, "truenas_api_client", types.SimpleNamespace(Client=Client))
    assert spi.main(["x", NEW]) == 0
    out = capsys.readouterr()
    assert "s3cret" not in out.out + out.err and "/k" not in out.out + out.err
    assert calls == ["app.config", "app.update", "app.config"] and out.out.strip().endswith("set")


def test_the_worker_takes_the_webs_probe_address_in_the_same_update():
    """#164: the worker's card recorder asks the GPU probe the web's panel
    asks; the node's config gave the address to the web alone."""
    cfg = a_config()
    cfg["services"]["web"]["environment"]["GPU_PROBE_URL"] = "http://gpu-probe:8080"
    spi.apply(cfg, NEW)
    assert spi.carry(cfg) == ["GPU_PROBE_URL None -> http://gpu-probe:8080"]
    assert cfg["services"]["worker"]["environment"]["GPU_PROBE_URL"] == "http://gpu-probe:8080"
    assert spi.carry(cfg) == [], "already there: nothing to change"
    cfg["services"]["worker"]["environment"]["GPU_PROBE_URL"] = "http://old:1"
    assert spi.carry(cfg) == ["GPU_PROBE_URL http://old:1 -> http://gpu-probe:8080"], \
        "the web's is the configured truth"
    assert "ADMIN_TOKEN" not in spi.CARRIED, "addresses only, never secrets"


def test_a_web_without_a_probe_leaves_the_worker_as_it_is():
    cfg = a_config()
    spi.apply(cfg, NEW)
    assert spi.carry(cfg) == []
    assert "GPU_PROBE_URL" not in cfg["services"]["worker"]["environment"]


def test_an_image_already_set_still_updates_to_carry_the_probe(monkeypatch, capsys):
    import sys
    import types
    calls = []

    def configured():
        cfg = a_config()
        for s in spi.SERVICES:
            cfg["services"][s]["environment"][spi.KEY] = NEW
        cfg["services"]["web"]["environment"]["GPU_PROBE_URL"] = "http://gpu-probe:8080"
        return cfg

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def call(self, method, *args, **kw):
            calls.append(method)
            if method == "app.update":
                state["cfg"] = args[1]["custom_compose_config"]
                return None
            return state["cfg"]
    state = {"cfg": configured()}
    monkeypatch.setitem(sys.modules, "truenas_api_client", types.SimpleNamespace(Client=Client))
    assert spi.main(["x", NEW]) == 0
    out = capsys.readouterr().out
    assert "worker  GPU_PROBE_URL None -> http://gpu-probe:8080" in out
    assert calls[:2] == ["app.config", "app.update"]


def test_an_update_the_worker_did_not_take_is_said(monkeypatch, capsys):
    import sys
    import types

    def configured():
        cfg = a_config()
        for s in spi.SERVICES:
            cfg["services"][s]["environment"][spi.KEY] = NEW
        cfg["services"]["web"]["environment"]["GPU_PROBE_URL"] = "http://gpu-probe:8080"
        return cfg

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def call(self, method, *args, **kw):
            return configured() if method == "app.config" else None     # the update is lost
    monkeypatch.setitem(sys.modules, "truenas_api_client", types.SimpleNamespace(Client=Client))
    assert spi.main(["x", NEW]) == 1
    assert "did not take ['GPU_PROBE_URL']" in capsys.readouterr().err


# --- the commands (set_commands.py, 2026-11-20) --------------------------------


def test_the_file_states_the_worker_guard_and_the_web_runs_its_image_cmd():
    """#779: the worker run by its guard (the OOM hold). The deploy never
    applied the file, so the command reached no node until München set it by
    hand at 8c56161."""
    assert WANTED == {"web": None, "worker": ["python", "-m", "app.worker_guard"]}


def test_the_files_command_is_set_and_nothing_else_moves():
    cfg = a_config()
    cfg["services"]["worker"]["command"] = ["python", "-m", "app.worker"]
    changes, notes = sc.apply(cfg, WANTED)
    assert changes == ["worker  command ['python', '-m', 'app.worker'] -> ['python', '-m', 'app.worker_guard']"]
    assert notes == [] and cfg["services"]["worker"]["command"] == WANTED["worker"]
    assert "command" not in cfg["services"]["web"] and cfg["services"]["gpu-probe"] == {"command": ["x"]}
    assert cfg["services"]["worker"]["environment"][spi.KEY] == OLD, "the commands alone"
    assert sc.apply(cfg, WANTED) == ([], []), "already there: nothing to change"
    assert sc.missing(cfg, WANTED) == []


def test_a_live_command_the_file_does_not_state_is_said_and_left():
    cfg = a_config()
    cfg["services"]["web"]["command"] = ["uvicorn", "app.api:app"]
    changes, notes = sc.apply(cfg, WANTED)
    assert notes == ["web     command ['uvicorn', 'app.api:app'] is not in truenas-app.yaml: left"]
    assert cfg["services"]["web"]["command"] == ["uvicorn", "app.api:app"] and len(changes) == 1


def test_a_config_or_a_file_it_does_not_recognise_changes_nothing(tmp_path):
    cfg = a_config()
    del cfg["services"]["worker"]
    with pytest.raises(ValueError, match="no worker service"):
        sc.apply(cfg, WANTED)
    assert "command" not in cfg["services"]["web"]
    with pytest.raises(ValueError, match="services"):
        sc.apply({}, WANTED)
    bad = tmp_path / "truenas-app.yaml"
    bad.write_text("services:\n  worker:\n    command: python -m app.worker_guard\n")
    with pytest.raises(ValueError, match="not a list of words"):
        sc.wanted(bad)


def _client(calls, state, *, lose_command=False):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def call(self, method, *args, **kw):
            import copy
            calls.append(method)
            if method == "app.update":
                state["cfg"] = copy.deepcopy(args[1]["custom_compose_config"])
                if lose_command:
                    state["cfg"]["services"]["worker"].pop("command", None)
                return None
            return copy.deepcopy(state["cfg"])
    return Client


def test_the_command_rides_the_images_update(monkeypatch, capsys):
    """One `app.update`: the guard's module does not exist in an older image,
    so the command never arrives on its own."""
    import sys
    import types
    calls: list[str] = []
    state = {"cfg": a_config()}
    monkeypatch.setitem(sys.modules, "truenas_api_client", types.SimpleNamespace(Client=_client(calls, state)))
    assert spi.main(["x", NEW]) == 0
    out = capsys.readouterr().out
    assert calls == ["app.config", "app.update", "app.config"]
    assert "worker  command None -> ['python', '-m', 'app.worker_guard']" in out
    assert state["cfg"]["services"]["worker"]["command"] == WANTED["worker"]
    assert state["cfg"]["services"]["worker"]["environment"][spi.KEY] == NEW
    calls.clear()
    assert spi.main(["x", NEW]) == 0 and calls == ["app.config"] and "unchanged" in capsys.readouterr().out


def test_an_image_already_set_still_updates_to_take_the_command(monkeypatch, capsys):
    import sys
    import types
    calls: list[str] = []
    cfg = a_config()
    for s in spi.SERVICES:
        cfg["services"][s]["environment"][spi.KEY] = NEW
    state = {"cfg": cfg}
    monkeypatch.setitem(sys.modules, "truenas_api_client", types.SimpleNamespace(Client=_client(calls, state)))
    assert spi.main(["x", NEW]) == 0 and "app.update" in calls


def test_a_command_the_update_did_not_take_is_said(monkeypatch, capsys):
    import sys
    import types
    calls: list[str] = []
    state = {"cfg": a_config()}
    monkeypatch.setitem(sys.modules, "truenas_api_client",
                        types.SimpleNamespace(Client=_client(calls, state, lose_command=True)))
    assert spi.main(["x", NEW]) == 1
    assert "did not take: ['worker']" in capsys.readouterr().err
