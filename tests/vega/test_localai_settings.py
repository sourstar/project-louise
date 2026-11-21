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
    assert settings.DESIRED == {"memory_reclaimer_enabled": True, "memory_reclaimer_threshold": 0.95,
                                "watchdog_idle_enabled": False,
                                # the operator, 2026-11-21: auto-upgrade off (invariant 12)
                                "auto_upgrade_backends": False, "auto_upgrade_addons": False}


def test_a_runtime_auto_upgrade_is_drift_whatever_the_env_says():
    """München, 2026-11-21: runtime_settings.json held auto_upgrade_backends
    true from the UI; it wins over LOCALAI_AUTO_UPGRADE_BACKENDS=false, and v5
    upgraded three upstream backends by itself."""
    live = {"memory_reclaimer_enabled": True, "memory_reclaimer_threshold": 0.95, "watchdog_idle_enabled": False,
            "auto_upgrade_backends": True, "api_keys": ["k"]}
    assert settings.drift(live) == {"auto_upgrade_backends": True, "auto_upgrade_addons": None}
    fixed = settings.merged(live)
    assert settings.drift(fixed) == {} and fixed["api_keys"] == ["k"]


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
    assert out["memory_reclaimer_threshold"] == 0.95
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
