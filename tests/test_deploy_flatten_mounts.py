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
    """Until the dataset is mounted on the host, nothing is mounted at /build
    and nothing set (a host without ssd3, or before its pool imports)."""
    assert fm.BUILD_HOST == "/mnt/ssd3/louise-build", "München's dataset, 2026-11-07"
    assert fm.build_area(is_dataset=lambda p: False) == ((), {})
    assert fm.build_area("/mnt/ssd3/not-there", is_dataset=lambda p: False) == ((), {})
    c = cfg()
    shared, env = fm.build_area(is_dataset=lambda p: False)
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


def test_the_two_cache_areas_are_mounted_once_their_copies_are_ready():
    """Operator, 2026-11-14: "For very fast caching (like embedding) use ssd5,
    for bulk caching use ssd3", then "You need 2 caches, one for fast mmap
    operations (like vectors caching) and one for things like files caching
    (a decompressed corpus archive to be parsed)". ssd5's `cache/` backs
    /data/cache (LOUISE_CACHE_DIR's path, unchanged); ssd3's `cache/` is the
    files cache (LOUISE_FILES_CACHE_DIR) and its `pubmed-pack/` backs
    /data/pubmed-pack. An area is mounted only once its copy is compared
    (READY), never a subfolder that is not there (Docker would create it
    empty and the packs would be rebuilt), and the fast one only beside the
    files one (the packs were not copied to ssd5)."""
    fast, files = "/mnt/ssd5/louise-cache", "/mnt/ssd3/louise-files"
    have = {fast, f"{fast}/READY", f"{fast}/cache",
            files, f"{files}/READY", f"{files}/cache", f"{files}/pubmed-pack"}
    yes = lambda p: True
    assert fm.FAST_HOST is None and fm.FILES_HOST is None, "the datasets are München's to name"
    shared, env, said = fm.cache_areas(is_dataset=yes, exists=have.__contains__)
    assert (shared, env) == ((), {}), "no host named: nothing"
    shared, env, _ = fm.cache_areas(fast, files, is_dataset=lambda p: False, exists=have.__contains__)
    assert (shared, env) == ((), {}), "not datasets: nothing"
    shared, env, _ = fm.cache_areas(fast, files, is_dataset=yes, exists=(have - {f"{files}/READY"}).__contains__)
    assert (shared, env) == ((), {}), "the files copy not compared: neither (the fast one needs it beside it)"
    shared, env, said = fm.cache_areas(fast, None, is_dataset=yes, exists=have.__contains__)
    assert (shared, env) == ((), {}) and any("NOT mounted" in line for line in said)
    shared, env, _ = fm.cache_areas(None, files, is_dataset=yes, exists=have.__contains__)
    assert [t for _, t, _ in shared] == ["/data/files-cache", "/data/pubmed-pack"], "the files one alone is safe"
    assert env == {"LOUISE_FILES_CACHE_DIR": "/data/files-cache"}
    shared, env, _ = fm.cache_areas(fast, files, is_dataset=yes,
                                    exists=(have - {f"{files}/pubmed-pack"}).__contains__)
    assert [t for _, t, _ in shared] == ["/data/files-cache", "/data/cache"], "a missing subfolder is never mounted"
    shared, env, _ = fm.cache_areas(fast, files, is_dataset=yes, exists=(have - {f"{files}/cache"}).__contains__)
    assert env == {}, "no files cache folder: the variable stays unset"
    shared, env, _ = fm.cache_areas(fast, files, is_dataset=yes, exists=have.__contains__)
    c = cfg()
    fm.apply(c, shared=shared, env={"LOUISE_CACHE_DIR": "/data/cache", **env})
    web, worker = c["services"]["web"], c["services"]["worker"]
    assert f"{fast}/cache:/data/cache" in web["volumes"] and f"{fast}/cache:/data/cache" in worker["volumes"]
    assert f"{files}/cache:/data/files-cache:ro" in web["volumes"]
    assert f"{files}/cache:/data/files-cache" in worker["volumes"]
    assert f"{files}/pubmed-pack:/data/pubmed-pack:ro" in web["volumes"]
    assert f"{files}/pubmed-pack:/data/pubmed-pack" in worker["volumes"]
    for svc in (web, worker):
        assert svc["environment"]["LOUISE_CACHE_DIR"] == "/data/cache", "the same path: nothing rebuilt"
        assert svc["environment"]["LOUISE_FILES_CACHE_DIR"] == "/data/files-cache"
    assert fm.apply(c, shared=shared, env={"LOUISE_CACHE_DIR": "/data/cache", **env}) == [], "idempotent"


def test_an_area_whose_copy_lacks_one_of_today_s_caches_is_not_mounted():
    """A cache the copy missed would be REBUILT once the area is mounted —
    wikidata's pack from the dump, days. Every `<kind>/<tag>` under today's
    `/data/cache` of the area's kinds, and every file of today's pubmed pack
    for the files area, must be in the copy."""
    data = "/mnt/ssd4/apps/louise-rag/data"
    fast, files = "/mnt/ssd5/louise-cache", "/mnt/ssd3/louise-files"
    tree = {data: ["cache", "pubmed-pack"], f"{data}/cache": ["wikidata-pack", "legi-facts", "x.lock"],
            f"{data}/cache/wikidata-pack": ["aa", ".bb.1.adopting"], f"{data}/cache/legi-facts": ["cc"],
            f"{data}/pubmed-pack": ["pubmed26n0001.xml.gz.parquet"]}
    dirs = {data, f"{data}/cache", f"{data}/cache/wikidata-pack", f"{data}/cache/legi-facts",
            f"{data}/cache/wikidata-pack/aa", f"{data}/cache/wikidata-pack/.bb.1.adopting",
            f"{data}/cache/legi-facts/cc", f"{data}/pubmed-pack"}
    copied = {fast, f"{fast}/READY", f"{fast}/cache", f"{fast}/cache/legi-facts/cc",
              files, f"{files}/READY", f"{files}/cache", f"{files}/pubmed-pack",
              f"{files}/cache/wikidata-pack/aa", f"{files}/pubmed-pack/pubmed26n0001.xml.gz.parquet"}

    def areas(have):
        return fm.cache_areas(fast, files, data=data, is_dataset=lambda p: True,
                              exists=lambda p: p in have or p in dirs, listdir=lambda p: tree.get(p, []),
                              isdir=dirs.__contains__)
    shared, env, said = areas(copied)
    assert [t for _, t, _ in shared] == ["/data/files-cache", "/data/pubmed-pack", "/data/cache"], said
    shared, env, said = areas(copied - {f"{files}/cache/wikidata-pack/aa"})
    assert (shared, env) == ((), {}), "the files copy lacks wikidata's pack: neither area"
    assert any("NOT mounted" in line and "wikidata-pack/aa" in line for line in said)
    shared, env, said = areas(copied - {f"{files}/pubmed-pack/pubmed26n0001.xml.gz.parquet"})
    assert shared == () and any("pubmed-pack/pubmed26n0001" in line for line in said)
    shared, env, said = areas(copied - {f"{fast}/cache/legi-facts/cc"})
    assert [t for _, t, _ in shared] == ["/data/files-cache", "/data/pubmed-pack"], "the files one alone"
    assert any(line.startswith("fast cache: NOT mounted") and "legi-facts/cc" in line for line in said)
    assert fm.data_host(cfg()) == data


def test_the_deploy_s_files_kinds_are_derived_s():
    from app import derived
    assert fm.FILES_KINDS == derived.FILES
