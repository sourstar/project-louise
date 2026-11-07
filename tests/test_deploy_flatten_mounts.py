"""tests/test_deploy_flatten_mounts.py — deploy/16f/flatten_mounts.py: every
per-collection origin/shard mount leaves (nested ones too), the shared roots
and the custom origin come in once, the two variables are set, and a second
run changes nothing (operator, 2026-10-12: one mount per kind)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "flatten_mounts", Path(__file__).resolve().parent.parent / "deploy" / "16f" / "flatten_mounts.py")
fm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fm)


def cfg():
    per = ["/mnt/tank/louise-corpora/pubmed:/data/collections/pubmed/origin",
           "/mnt/tank/apps/louise-shard/pubmed:/data/collections/pubmed/shard",
           "/mnt/tank/louise-corpora/wikivec/data:/data/collections/wikipedia/origin/data:ro",
           "/mnt/ssd4/apps/louise-rag/data:/data", "/mnt/tank/apps/louise-exports:/exports"]
    return {"services": {"web": {"volumes": list(per), "environment": {"ADMIN_TOKEN_FILE": "/x"}},
                         "worker": {"volumes": list(per) + [
                             {"type": "bind", "source": "/mnt/tank/apps/louise-shard/gutenberg",
                              "target": "/data/collections/gutenberg/shard"}]},
                         "gpu-probe": {"image": "x"}}}


def test_per_collection_mounts_leave_and_the_shared_roots_come_in():
    c = cfg()
    said = fm.apply(c)
    web, worker = c["services"]["web"], c["services"]["worker"]
    for svc in (web, worker):
        targets = [fm._parts(v)[1] for v in svc["volumes"]]
        assert not any(t.startswith("/data/collections/") for t in targets)
        assert {"/data", "/exports", "/corpora", "/shards", "/corpora-custom/gutenberg"} <= set(targets)
        assert svc["environment"]["SHARDS_ROOT"] == "/shards"
        assert svc["environment"]["ORIGINS_ROOT"] == "/corpora"
        assert svc["environment"]["BUNDLES_ROOT"] == "/bundles" and "/bundles" in targets
    assert "/mnt/tank/apps/louise-bundles:/bundles:ro" in web["volumes"], "the web never reads a bundle"
    assert "/mnt/tank/apps/louise-bundles:/bundles" in worker["volumes"]
    assert "/mnt/tank/louise-corpora:/corpora:ro" in web["volumes"], "web reads origins only"
    assert "/mnt/tank/louise-corpora:/corpora" in worker["volumes"]
    assert web["environment"]["ADMIN_TOKEN_FILE"] == "/x", "nothing else is touched"
    assert "gpu-probe: " not in " ".join(said)
    assert fm.apply(c) == [], "a second run changes nothing"


def test_no_build_dataset_leaves_the_build_area_unset():
    """The ssd3 dataset's name is München's to report (2026-11-07): until it
    is named and mounted, nothing is mounted at /build and nothing set."""
    assert fm.BUILD_HOST is None
    assert fm.build_area() == ((), {})
    assert fm.build_area("/mnt/ssd3/not-there", is_dataset=lambda p: False) == ((), {})
    c = cfg()
    shared, env = fm.build_area()
    fm.apply(c, shared=shared, env={"LOUISE_CACHE_DIR": "/data/cache", **env})
    for svc in (c["services"]["web"], c["services"]["worker"]):
        assert "LOUISE_BUILD_DIR" not in svc["environment"]
        assert "/build" not in {fm._parts(v)[1] for v in svc["volumes"]}


def test_a_build_dataset_is_mounted_in_web_and_worker_and_named():
    shared, env = fm.build_area("/mnt/ssd3/louise-build", is_dataset=lambda p: True)
    assert env == {"LOUISE_BUILD_DIR": "/build"}
    c = cfg()
    said = fm.apply(c, shared=shared, env={"LOUISE_CACHE_DIR": "/data/cache", **env})
    for name in ("web", "worker"):
        svc = c["services"][name]
        assert "/mnt/ssd3/louise-build:/build" in svc["volumes"], "read and written, as /data is"
        assert svc["environment"]["LOUISE_BUILD_DIR"] == "/build"
    assert "gpu-probe" not in " ".join(said)
    assert fm.apply(c, shared=shared, env={"LOUISE_CACHE_DIR": "/data/cache", **env}) == [], "idempotent"
