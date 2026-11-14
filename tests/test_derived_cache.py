"""tests/test_derived_cache.py — the fast drive for derived data (07 §7.8;
operator, 2026-10-20): LOUISE_CACHE_DIR names it; app/derived.py places
every plugin's cache under it by the origin's physical identity and carries
an existing cache there once; unset, nothing moves."""
import os
from pathlib import Path

from app import config, derived, parallel
from app.plugins.pubmed import pack as pubmed_pack
from app.plugins.wikipedia import page_props


def test_unset_means_beside_the_origin_as_before(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", None)
    monkeypatch.setattr(config, "PUBMED_PACK_DIR", None)
    assert derived.place("pubmed-pack", tmp_path) is None
    container = tmp_path / "corpus" / "pubmed26n0001.xml.gz"
    assert pubmed_pack.pack_path(container) == tmp_path / "corpus" / ".pack" / ("pubmed26n0001.xml.gz" + pubmed_pack.SUFFIX)
    props = tmp_path / "enwiki" / "enwiki-20260801-page_props.sql.gz"
    assert page_props._candidates(props)[0] == props.parent / ".pack" / (props.name + page_props.PACK_SUFFIX)


def test_the_cache_is_keyed_on_the_origins_physical_identity(tmp_path, monkeypatch):
    cache = tmp_path / "nvme" / "cache"
    monkeypatch.setattr(config, "CACHE_DIR", cache)
    a = tmp_path / "corpora" / "a"
    b = tmp_path / "corpora" / "b"
    a.mkdir(parents=True), b.mkdir(parents=True)
    link = tmp_path / "corpora" / "a-by-another-name"
    os.symlink(a, link, target_is_directory=True)
    pa, pb, pl = (derived.place("pubmed-pack", p) for p in (a, b, link))
    assert pa.parent == cache / "pubmed-pack" and pa != pb, "two origins: two places"
    assert pl == pa, "the same origin through a link: the same place"
    assert len(pa.name) == 16 and not pa.exists(), "named by the tag; not created here"
    assert derived.place("wikidata-pack", a) != pa, "per kind"


def test_an_existing_cache_is_carried_in_once_whole_and_left_in_place(tmp_path, monkeypatch):
    src = tmp_path / "beside" / "pack"
    (src / "sub").mkdir(parents=True)
    (src / "a.parquet").write_bytes(b"a" * 10)
    (src / "sub" / "b.parquet").write_bytes(b"b" * 20)
    (src / "00017.parquet.k3j2.tmp").write_bytes(b"half")          # a pass's uncommitted work
    dst = tmp_path / "cache" / "wikidata-pack" / "deadbeef" / "pack"
    assert derived.adopt(src, dst) is True
    assert (dst / "a.parquet").read_bytes() == b"a" * 10 and (dst / "sub" / "b.parquet").read_bytes() == b"b" * 20
    assert not (dst / "00017.parquet.k3j2.tmp").exists(), "*.tmp is not carried"
    assert src.is_dir() and (src / "a.parquet").is_file(), "the original is left for the operator"
    assert (dst / "a.parquet").stat().st_mtime_ns == (src / "a.parquet").stat().st_mtime_ns, "times kept"
    assert derived.adopt(src, dst) is False, "present already: a stat, nothing copied"
    assert derived.adopt(tmp_path / "nowhere", tmp_path / "cache" / "x") is False
    # a copy stopped half-way: only the sibling is left, and the next call redoes it whole
    half = dst.with_name(f".{dst.name}.adopting")
    dst2 = tmp_path / "cache" / "other" / "pack"
    half2 = dst2.with_name(f".{dst2.name}.adopting")
    half2.mkdir(parents=True)
    (half2 / "stale").write_bytes(b"from the stopped copy")
    assert derived.adopt(src, dst2) is True
    assert not half2.exists() and not (dst2 / "stale").exists() and (dst2 / "a.parquet").is_file()
    assert not half.exists()
    # a single file, too
    f = tmp_path / "beside" / "pubmed26n0001.xml.gz.pack"
    f.write_bytes(b"pack")
    assert derived.adopt(f, tmp_path / "cache" / "pubmed-pack" / "t" / f.name) is True
    assert (tmp_path / "cache" / "pubmed-pack" / "t" / f.name).read_bytes() == b"pack"


def test_pubmeds_pack_and_wikipedias_page_props_follow_the_fast_drive(tmp_path, monkeypatch):
    cache = tmp_path / "nvme" / "cache"
    monkeypatch.setattr(config, "CACHE_DIR", cache)
    monkeypatch.setattr(config, "PUBMED_PACK_DIR", None)
    corpus = tmp_path / "corpora" / "pubmed"
    (corpus / ".pack").mkdir(parents=True)
    container = corpus / "pubmed26n0001.xml.gz"
    beside = corpus / ".pack" / (container.name + pubmed_pack.SUFFIX)
    beside.write_bytes(b"built beside the containers")
    got = pubmed_pack.pack_path(container)
    assert got == derived.place("pubmed-pack", corpus) / (container.name + pubmed_pack.SUFFIX)
    assert got.read_bytes() == b"built beside the containers", "carried in on the first ask"
    assert beside.is_file()
    # PUBMED_PACK_DIR, when set, still wins
    monkeypatch.setattr(config, "PUBMED_PACK_DIR", tmp_path / "explicit")
    assert pubmed_pack.pack_path(container) == tmp_path / "explicit" / (container.name + pubmed_pack.SUFFIX)
    # wikipedia's page-props pack
    props = tmp_path / "corpora" / "enwiki" / "enwiki-20260801-page_props.sql.gz"
    (props.parent / ".pack").mkdir(parents=True)
    old = props.parent / ".pack" / (props.name + page_props.PACK_SUFFIX)
    old.write_bytes(b"old pack")
    first, second = page_props._candidates(props)
    assert first == derived.place("wikipedia-page-props", props.parent) / (props.name + page_props.PACK_SUFFIX)
    assert first.read_bytes() == b"old pack" and old.is_file()
    assert second.name.startswith(derived.tag(props) + "-")
    # the parse pool's children see the same drive
    assert "CACHE_DIR" in parallel.KNOBS


_RACE = """
import sys, time
from pathlib import Path
from multiprocessing import get_context

def one(args):
    kind, origin, at = args
    while time.time() < at:                 # every process starts together
        time.sleep(0.001)
    if kind == "pubmed":
        from app.plugins.pubmed import pack
        p = pack.ensure(Path(origin))
        return len(pack.record_dates(Path(origin)))
    from app.plugins.wikipedia import page_props
    p = page_props.ensure(Path(origin))
    return sum(1 for page in range(1, 100) if page_props.unit_of(Path(origin), page))

if __name__ == "__main__":
    kind, origin = sys.argv[1], sys.argv[2]
    at = time.time() + 3
    with get_context("spawn").Pool(4) as pool:
        print(pool.map(one, [(kind, origin, at)] * 4))
"""


def _race(tmp_path, kind, origin):
    import subprocess
    import sys
    script = tmp_path / "race.py"
    script.write_text(_RACE)
    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k not in ("LOUISE_CACHE_DIR", "PUBMED_PACK_DIR")}
    env["PYTHONPATH"] = str(root)
    return subprocess.run([sys.executable, str(script), kind, str(origin)], cwd=root, env=env,
                          capture_output=True, text=True, timeout=120)


def test_four_processes_build_one_pubmed_pack_once_and_read_it_whole(tmp_path):
    """München, 2026-10-30: the plan's pool children each built the same pack
    into ONE temporary file (a thread lock serialises one process only); a
    third read it truncated, and the plan skipped two documents. Four
    processes started together: one builds, three wait and read it."""
    import shutil
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    src = Path(__file__).parent / "fixtures" / "pubmed-mini" / "pubmed25n0001.xml"
    container = shutil.copy(src, corpus / src.name)
    p = _race(tmp_path, "pubmed", container)
    assert p.returncode == 0, p.stderr[-3000:]
    counts = eval(p.stdout.strip().splitlines()[-1])
    assert len(set(counts)) == 1 and counts[0] > 0, counts
    assert p.stderr.count("[pubmed pack] built") == 1, p.stderr
    assert not list((corpus / ".pack").glob("*.tmp")), "no writer's file left behind"


def test_four_processes_build_one_page_props_pack_once(tmp_path):
    import gzip
    corpus = tmp_path / "enwiki"
    corpus.mkdir()
    props = corpus / "enwiki-20260801-page_props.sql.gz"
    rows = ",".join(f"({p},'wikibase_item','Q{p}',NULL)" for p in range(1, 60))
    with gzip.open(props, "wb") as f:
        f.write(f"INSERT INTO `page_props` VALUES {rows};\n".encode())
    p = _race(tmp_path, "page_props", props)
    assert p.returncode == 0, p.stderr[-3000:]
    counts = eval(p.stdout.strip().splitlines()[-1])
    assert counts == [59] * 4, counts
    assert p.stderr.count("[wikipedia page_props] built") == 1, p.stderr


def test_a_killed_writers_temporary_file_goes_with_the_next_lock(tmp_path):
    dest = tmp_path / "x.pack.parquet"
    orphan = tmp_path / "x.pack.parquet.4242.tmp"
    old_name = tmp_path / "x.pack.parquet.tmp"
    other = tmp_path / "y.pack.parquet.4242.tmp"
    for f in (orphan, old_name, other):
        f.write_bytes(b"half")
    with derived.building(dest):
        assert not orphan.exists() and not old_name.exists() and other.exists()


def test_stale_names_are_what_the_glob_gave_and_compile_no_pattern(tmp_path, monkeypatch):
    """`derived._named` is `glob(escape(prefix) + "*" + suffix)`, name for
    name, without a compiled pattern per pack: the standard library's cache
    kept one for each (~750 B a pack, in every process — the per-file growth
    the update-file heap test measured, 2026-11-12). Where that cache lives
    is the version's own (3.12: `pathlib._compile_pattern`, 3.13:
    `glob._compile_pattern`), so what is held here is that no glob runs."""
    import glob
    names = ["p.pack", "p.pack.tmp", "p.pack.123.tmp", "p.packtmp", "p.pack.lock", "q.pack.1.tmp",
             ".p.pack.9.adopting", ".p.pack.adopting", ".p.packadopting", "p.pack.9.adopting",
             "a[b].pack.1.tmp", "a[b].pack.tmp.x"]
    for n in names:
        (tmp_path / n).write_bytes(b"")
    cases = [("p.pack.", "tmp"), (".p.pack.", "adopting"), ("a[b].pack.", "tmp"), ("x.", "tmp")]
    for prefix, suffix in cases:
        want = sorted(tmp_path.glob(glob.escape(prefix) + "*" + suffix))
        assert sorted(derived._named(tmp_path, prefix, suffix)) == want, (prefix, suffix)
    assert derived._named(tmp_path / "absent", "p.", "tmp") == []

    def no_glob(self, *a, **k):
        raise AssertionError(f"a glob pattern compiled for {self}")
    monkeypatch.setattr(Path, "glob", no_glob)
    for k in range(5):
        with derived.building(tmp_path / f"pack-{k}.bin"):
            pass
    src, dst = tmp_path / "origin.bin", tmp_path / "carried" / "origin.bin"
    src.write_bytes(b"x")
    assert derived.adopt(src, dst) is True and dst.read_bytes() == b"x"


def test_two_caches_place_each_kind_by_how_it_is_read(tmp_path, monkeypatch):
    """Operator, 2026-11-14: "You need 2 caches, one for fast mmap operations
    (like vectors caching) and one for things like files caching (a
    decompressed corpus archive to be parsed)". A FILES kind goes to the files
    cache, every other kind to the fast one; the tag is the origin's, whatever
    the root, so a cache moves between the two as the same bytes."""
    fast, files = tmp_path / "ssd5", tmp_path / "ssd3"
    origin = tmp_path / "corpus"
    origin.mkdir()
    monkeypatch.setattr(config, "CACHE_DIR", fast)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", None)
    before = {k: derived.place(k, origin) for k in (*derived.FILES, "legi-facts", "wikidata-labels")}
    assert all(p.parent.parent == fast for p in before.values()), "no files cache: every kind where it was"
    assert not derived.apart("wikidata-pack", "wikidata-labels")
    monkeypatch.setattr(config, "FILES_CACHE_DIR", files)
    for kind in derived.FILES:
        assert derived.place(kind, origin) == files / kind / before[kind].name, kind
    for kind in ("legi-facts", "wikidata-labels", "annotation-claim-index", "speech-clips"):
        assert derived.place(kind, origin).parent.parent == fast, kind
    assert derived.apart("wikidata-pack", "wikidata-labels")
    assert derived.places_key() != str(fast), "an answer kept per process is keyed on both caches"
    monkeypatch.setattr(config, "CACHE_DIR", None)
    assert derived.place("pubmed-pack", origin).parent.parent == files
    assert derived.place("legi-facts", origin) is None, "no fast cache: beside the origin, as before"


def test_held_lists_each_kind_under_its_own_cache_only(tmp_path, monkeypatch):
    """A rename carries what the caches hold; a FILES kind left on the fast
    cache after the files cache was named is a copy nothing reads, the
    operator's to delete, never carried."""
    fast, files = tmp_path / "ssd5", tmp_path / "ssd3"
    for p in (fast / "legi-facts" / "a", fast / "wikidata-pack" / "old", files / "wikidata-pack" / "b",
              files / "legi-facts" / "stray", files / "wikidata-pack" / ".b.1.adopting"):
        p.mkdir(parents=True)
    monkeypatch.setattr(config, "CACHE_DIR", fast)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", files)
    assert derived.held() == sorted([fast / "legi-facts" / "a", files / "wikidata-pack" / "b"])
    monkeypatch.setattr(config, "FILES_CACHE_DIR", None)
    assert derived.held() == sorted([fast / "legi-facts" / "a", fast / "wikidata-pack" / "old"])


def test_the_pool_s_children_see_both_caches():
    assert {"CACHE_DIR", "FILES_CACHE_DIR"} <= set(parallel.KNOBS)


def _old(p: Path, now: float, days: float) -> None:
    os.utime(p, (now - days * 86400, now - days * 86400))


def test_an_idle_pack_goes_and_a_used_one_stays(tmp_path, monkeypatch):
    """Operator, 2026-11-14: the caches built from the source downloads,
    "only upon need". A pack idle IDLE_S goes; one used since stays; a FAST
    kind never goes this way; PUBMED_PACK_DIR's packs are packs too."""
    import time
    fast, files, pp = tmp_path / "ssd5", tmp_path / "ssd3", tmp_path / "pubmed-pack"
    monkeypatch.setattr(config, "CACHE_DIR", fast)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", files)
    monkeypatch.setattr(config, "PUBMED_PACK_DIR", pp)
    idle, used = files / "legi-pack" / "t1" / "a.tar.gz.pack.parquet", files / "legi-pack" / "t1" / "b.tar.gz.pack.parquet"
    pubmed, facts = pp / "pubmed26n0001.xml.gz.pack.parquet", fast / "legi-facts" / "t1" / "legi-facts.sqlite"
    now = time.time()
    for p in (idle, used, pubmed, facts):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 10)
        _old(p, now, 2)
    derived._touched.clear()
    derived.used(used, now=now)
    assert derived.stamp_of(used).is_file()
    would = derived.evict_idle(now=now, dry_run=True)
    assert {Path(r["path"]) for r in would} == {idle, pubmed} and all(r["bytes"] == 10 for r in would)
    assert idle.exists(), "a dry run removes nothing"
    gone = derived.evict_idle(now=now)
    assert {Path(r["path"]) for r in gone} == {idle, pubmed}
    assert not idle.exists() and not pubmed.exists() and used.exists() and facts.exists()
    assert derived.evict_idle(now=now + derived.IDLE_S + 1) and not used.exists(), "idle in its turn"
    assert not derived.stamp_of(used).exists()


def test_a_pack_being_built_is_never_evicted(tmp_path, monkeypatch):
    import fcntl
    import time
    monkeypatch.setattr(config, "CACHE_DIR", None)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", tmp_path / "ssd3")
    monkeypatch.setattr(config, "PUBMED_PACK_DIR", None)
    pack = tmp_path / "ssd3" / "legi-pack" / "t" / "a.tar.gz.pack.parquet"
    pack.parent.mkdir(parents=True)
    pack.write_bytes(b"x")
    with open(pack.with_name(pack.name + ".lock"), "a+b") as f:
        fcntl.flock(f, fcntl.LOCK_EX)                           # derived.building's lock, held
        assert derived.evict_idle(now=time.time() + 3 * 86400) == []
    assert pack.exists()


def test_a_pack_evicted_under_a_kept_answer_is_built_again_upon_need(tmp_path, monkeypatch):
    """The pack memo (app/container_pack.py) kept its answer across the
    eviction: it now asks the file is still there, and builds it again."""
    import time

    import pyarrow as pa

    from app.container_pack import ContainerPack
    monkeypatch.setattr(config, "CACHE_DIR", None)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", tmp_path / "ssd3")
    monkeypatch.setattr(config, "PUBMED_PACK_DIR", None)
    origin = tmp_path / "corpus" / "a.tar.gz"
    origin.parent.mkdir()
    origin.write_bytes(b"abc")
    place = tmp_path / "ssd3" / "legi-pack" / "t" / ("a.tar.gz" + derived.PACK_FILE)
    cp = ContainerPack("test pack", "rows", b"test.version")
    builds = []

    def build(o, dest):
        builds.append(dest)
        return cp.write(o, dest, pa.schema([("a", pa.int64())]), [pa.table({"a": [1]})], 1)
    assert cp.ensure(origin, lambda: [place], version=1, build=build) == place
    assert cp.ensure(origin, lambda: [place], version=1, build=build) == place and len(builds) == 1
    derived._touched.clear()
    assert [r["path"] for r in derived.evict_idle(now=time.time() + 2 * 86400)] == [str(place)]
    assert not place.exists()
    assert cp.ensure(origin, lambda: [place], version=1, build=build) == place
    assert place.is_file() and len(builds) == 2, "built again upon need"


def test_eviction_runs_at_most_once_an_hour(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", None)
    monkeypatch.setattr(config, "FILES_CACHE_DIR", tmp_path / "ssd3")
    monkeypatch.setattr(config, "PUBMED_PACK_DIR", None)
    calls = []
    monkeypatch.setattr(derived, "evict_idle", lambda **k: calls.append(k) or [])
    monkeypatch.setattr(derived, "_last_evict", 0.0)
    derived.evict_due(now=10_000.0)
    derived.evict_due(now=10_000.0 + derived.EVICT_EVERY_S - 1)
    derived.evict_due(now=10_000.0 + derived.EVICT_EVERY_S)
    assert len(calls) == 2
