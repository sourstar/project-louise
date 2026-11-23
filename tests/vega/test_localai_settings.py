"""deploy/vega/stage/localai_settings.py: the WatchDog threshold the operator
set (2026-10-23, raised 2026-11-08) is in the repo, and applying it changes nothing else."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "localai_settings", ROOT / "deploy" / "vega" / "stage" / "localai_settings.py")
settings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(settings)


def test_the_reclaimer_threshold_is_the_operators():
    # the operator, 2026-11-23: 0.98, with the keeper's working-memory reserve (app/resident.py)
    assert settings.DESIRED == {"memory_reclaimer_enabled": True, "memory_reclaimer_threshold": 0.98,
                                "watchdog_idle_enabled": False,
                                # the operator, 2026-11-21: auto-upgrade off (invariant 12)
                                "auto_upgrade_backends": False, "auto_upgrade_addons": False}


def test_a_runtime_auto_upgrade_is_drift_whatever_the_env_says():
    """München, 2026-11-21: runtime_settings.json held auto_upgrade_backends
    true from the UI; it wins over LOCALAI_AUTO_UPGRADE_BACKENDS=false, and v5
    upgraded three upstream backends by itself."""
    live = {"memory_reclaimer_enabled": True, "memory_reclaimer_threshold": 0.98, "watchdog_idle_enabled": False,
            "auto_upgrade_backends": True, "api_keys": ["k"]}
    assert settings.drift(live) == {"auto_upgrade_backends": True, "auto_upgrade_addons": None}
    fixed = settings.merged(live)
    assert settings.drift(fixed) == {} and fixed["api_keys"] == ["k"]


def test_the_line_of_before_is_drift_and_the_deploy_moves_it():
    """The node held 0.95 until the deploy carrying the keeper's reserve: the
    deploy's apply sets 0.98, nothing is set by hand (a value set through
    /api/settings alone would be put back by the next deploy)."""
    live = {"memory_reclaimer_enabled": True, "memory_reclaimer_threshold": 0.95, "watchdog_idle_enabled": False,
            "auto_upgrade_backends": False, "auto_upgrade_addons": False}
    assert settings.drift(live) == {"memory_reclaimer_threshold": 0.95}
    assert settings.merged(live)["memory_reclaimer_threshold"] == 0.98

def test_the_projection_marker_is_never_written_back():
    out = settings.merged({"addon_host_settings_authority": "owner_required", "watchdog_busy_timeout": "10m0s"})
    assert "addon_host_settings_authority" not in out and out["watchdog_busy_timeout"] == "10m0s"


def test_check_reads_and_writes_nothing(monkeypatch):
    calls = []

    def call(url, body=None):
        calls.append((url, body))
        return {"auto_upgrade_backends": True, "api_keys": ["secret"]}
    monkeypatch.setattr(settings, "_call", call)
    got = settings.check("http://x:1/")
    assert calls == [("http://x:1/api/settings", None)], "one GET, no POST"
    assert got["drift"]["auto_upgrade_backends"] is True and "api_keys" not in got and "secret" not in str(got)


def test_only_the_managed_keys_change():
    current = {"memory_reclaimer_threshold": 0.68, "memory_reclaimer_enabled": True, "watchdog_idle_enabled": True,
               "api_keys": ["k"], "p2p_token": "t", "watchdog_busy_timeout": "10m0s"}
    out = settings.merged(current)
    assert out["memory_reclaimer_threshold"] == 0.98
    assert out["watchdog_idle_enabled"] is False and out["watchdog_busy_timeout"] == "10m0s"
    assert {k: v for k, v in out.items() if k not in settings.DESIRED} == \
        {k: v for k, v in current.items() if k not in settings.DESIRED}
    assert current["memory_reclaimer_threshold"] == 0.68, "the input is not modified"


#: the encoders processing pins for its duration and restores after (app/model_pins.py;
#: operator, 2026-10-23: "reading the pinning status, in case pinning a model before
#: processing, then restoring the previous pinning status after"). Their resting state is
#: UNPINNED: a static pin here would keep the card from other models while nothing runs,
#: and the scoped pin would find it pinned and never release it.
SCOPED = ("vega-qwen3-embedding-4b", "vega-dar-legal-it", "vega-medcpt-article-ov", "vega-bge-m3", "vega-granite",
          "louise-rerank-medcpt")   # the operator, 2026-10-23: "Remove the reranker pin"


def test_the_scoped_pin_encoders_rest_unpinned():
    import json
    for name in SCOPED:
        text = (ROOT / "deploy" / "vega" / "stage" / "models" / f"{name}.yaml").read_text()
        body = json.loads("\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#")))
        assert body.get("pinned") is False, f"{name} is staged pinned"


def test_the_lamar_candidate_is_the_bge_reranker_s_strict_configuration():
    """LAMAR-600m has bge-reranker-v2-m3's architecture (reranker-scope §14):
    the candidate runs under the deployed reranker's bit-exact settings
    (invariant 12: GGML_SYCL_ENABLE_DNN=0, parallel:1, cache_ram:0, flash
    attention off), differing only in its name and its weights."""
    import json
    import re
    from pathlib import Path
    d = Path(__file__).resolve().parents[2] / "deploy" / "vega" / "stage" / "models"

    def load(name):
        text = (d / name).read_text(encoding="utf-8")
        return json.loads(re.sub(r"(?m)^#.*$", "", text))
    bge, lamar = load("louise-rerank-bge.yaml"), load("louise-rerank-lamar.yaml")
    assert lamar["name"] == "louise-rerank-lamar"
    assert lamar["parameters"]["model"] == "weights/lamar/LAMAR-600m-f16.gguf"
    assert lamar.get("pinned") is False
    for body in (bge, lamar):
        body.pop("name"); body["parameters"].pop("model"); body.pop("pinned", None)
    assert lamar == bge



# --- the add-on owner on (2026-11-22: the deploy of 2f26d6d rolled back) -------

OWNER_ON = {"memory_reclaimer_enabled": True, "memory_reclaimer_threshold": 0.98, "watchdog_idle_enabled": False,
            "auto_upgrade_backends": False, "api_keys": [],
            "addon_host_settings_authority": "owner_required"}       # v5.0.1's projection, no auto_upgrade_addons


class _LocalAI:
    """v5.0.1's /api/settings with its add-on owner running: a POST that
    carries a key the owner holds is refused with LocalAI's own 409."""

    def __init__(self, current):
        self.current, self.posted = dict(current), []

    def __call__(self, url, body=None):
        import io
        import urllib.error
        if body is None:
            return dict(self.current)
        self.posted.append(body)
        if "auto_upgrade_addons" in body:
            raise urllib.error.HTTPError(url, 409, "Conflict", {}, io.BytesIO(
                b'{"success":false,"error":"auto_upgrade_addons is set on the add-on owner, not through '
                b'/api/settings","restart_required":false}'))
        self.current.update({k: v for k, v in body.items() if k in settings.DESIRED})
        return {"success": True}


def test_with_the_addon_owner_on_its_key_is_neither_written_nor_drift(monkeypatch):
    server = _LocalAI({**OWNER_ON, "auto_upgrade_backends": True})
    monkeypatch.setattr(settings, "_call", server)
    got = settings.apply("http://x:1")
    assert got["changed"] is True and got["auto_upgrade_backends"] is False
    assert got["held_by_the_addon_owner"] == ["auto_upgrade_addons"]
    (body,) = server.posted
    assert "auto_upgrade_addons" not in body and "addon_host_settings_authority" not in body
    assert settings.check("http://x:1")["drift"] == {}


def test_with_the_owner_on_the_backends_key_stays_strict(monkeypatch):
    class Stubborn(_LocalAI):
        def __call__(self, url, body=None):
            if body is not None:
                self.posted.append(body)
                return {"success": True}                  # says yes, takes nothing
            return dict(self.current)
    monkeypatch.setattr(settings, "_call", Stubborn({**OWNER_ON, "auto_upgrade_backends": True}))
    import pytest
    with pytest.raises(SystemExit, match="auto_upgrade_backends"):
        settings.apply("http://x:1")


def test_with_the_owner_off_both_keys_are_managed():
    assert settings.desired_for({"auto_upgrade_backends": False}) == settings.DESIRED
    assert "auto_upgrade_addons" not in settings.desired_for(OWNER_ON)
