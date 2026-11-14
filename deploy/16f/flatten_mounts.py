#!/usr/bin/env python3
"""deploy/16f/flatten_mounts.py — one mount per kind, a folder per collection
(operator, 2026-10-12), in the louise-rag app.

In every service that has them: every bind mount whose CONTAINER path is a
collection's `origin` or `shard` (or something mounted inside one, as
wikivec's `origin/data`) leaves; `/corpora`, `/shards` and the custom
origins named below come in; and `SHARDS_ROOT` / `ORIGINS_ROOT` are set, so
`create` links a new collection's folders there (app/hold/collection.py).
The per-collection paths themselves become symlinks on the host, made by the
runbook before this runs, so the paths every manifest stores do not move.

Run on the node with the app STOPPED, through TrueNAS's API client in this
process (set_platform_image.py's reasons: the config carries the admin token,
so it never reaches a command line, a log or this output). Prints the mounts
it removes and adds and the two variables, nothing else. Idempotent.
"""
from __future__ import annotations

import re
import sys

APP = "louise-rag"
#: a collection's origin or shard, or anything mounted inside one
PER_COLLECTION = re.compile(r"^/data/collections/[^/]+/(origin|shard)(/.*)?$")
#: (host, container, read-only in `web`)
SHARED = (("/mnt/tank/louise-corpora", "/corpora", True),
          ("/mnt/tank/apps/louise-shard", "/shards", False),
          ("/mnt/tank/media/public/books/project_gutenberg", "/corpora-custom/gutenberg", True),
          # the bundles (09 §9.7): a second copy of the vectors, on tank with
          # the SHARDs (operator, 2026-10-19); the web never reads them
          ("/mnt/tank/apps/louise-bundles", "/bundles", True))
ENV = {"SHARDS_ROOT": "/shards", "ORIGINS_ROOT": "/corpora", "BUNDLES_ROOT": "/bundles"}
SERVICES = ("web", "worker")

#: THE BUILD AREA (operator, 2026-11-07, docs/notes/storage-tiering.md §8:
#: "B, ssd3 as the build area"): a dataset on ssd3 mounted at /build in web
#: and worker, LOUISE_BUILD_DIR=/build (app/vector_place.py). Read and
#: written by both, as /data is: a table built there is searched from there
#: until it moves. The dataset's NAME IS MÜNCHEN'S TO REPORT: until it is
#: written here (or given as LOUISE_BUILD_HOST to the deploy), `build_area`
#: sees no dataset and the setting stays unset — nothing mounted, nothing set.
#: München created it 2026-11-07: ssd3/louise-build (atime off, lz4,
#: recordsize 1M, primarycache metadata), 7.14 TiB free.
BUILD_HOST: str | None = "/mnt/ssd3/louise-build"
BUILD_TARGET = "/build"

#: THE TWO CACHE AREAS (operator, 2026-11-14: "For very fast caching (like
#: embedding) use ssd5, for bulk caching use ssd3"; then "You need 2 caches, one
#: for fast mmap operations (like vectors caching) and one for things like files
#: caching (a decompressed corpus archive to be parsed)"; storage-tiering.md §10).
#: Each a dataset whose subfolders are mounted in web and worker:
#: - FAST (ssd5): `cache/` over `/data/cache`, the path LOUISE_CACHE_DIR already
#:   names — the kinds read at random or memory-mapped (app/derived.py);
#: - FILES (ssd3): `cache/` at `/data/files-cache`, LOUISE_FILES_CACHE_DIR — the
#:   FILES kinds, decompressed archives a parse streams through — and
#:   `pubmed-pack/` over `/data/pubmed-pack`, PUBMED_PACK_DIR's path.
#: No path a cache already has changes: derived.py keys a cache on its origin's
#: resolved path, and a kind moves between the two caches as the same
#: `<kind>/<tag>` bytes. The pubmed pack keeps its own path, never inside a
#: cache dir, where its files would share `pubmed-pack/`, a kind name of
#: derived.py's own tree, and `derived.held` (a rename's walk) would list each.
#: Each area is mounted only once its dataset holds READY, written after its
#: copy is compared (count, bytes, a sha256 sample), and never a subfolder that
#: is not there (Docker would create it EMPTY over the copy on ssd4, and the
#: caches would be rebuilt: the wikidata pack is 122 G, the pubmed pack 84 G).
#: The FAST area needs the FILES area: without it the FILES kinds would be
#: looked for on ssd5, where they were not copied, and rebuilt.
#: The datasets' NAMES ARE MÜNCHEN'S TO REPORT (LOUISE_FAST_HOST and
#: LOUISE_FILES_HOST at the deploy override them). Reported 2026-11-14; neither
#: is mounted before its dataset holds READY.
FAST_HOST: str | None = "/mnt/ssd5/louise-fast-cache"
FILES_HOST: str | None = "/mnt/ssd3/louise-files-cache"
#: (subfolder of the dataset, container path, read-only in `web`)
FAST_MOUNTS = (("cache", "/data/cache", False),)              # web writes the speech cache
FILES_MOUNTS = (("cache", "/data/files-cache", True),
                ("pubmed-pack", "/data/pubmed-pack", True))
FILES_ENV = {"LOUISE_FILES_CACHE_DIR": "/data/files-cache"}
#: written by the operator's session in a dataset AFTER its copy is compared:
#: a half copy is never mounted over the whole
READY = "READY"
#: app/derived.py `FILES`, the kinds of the files cache (tests/test_deploy_
#: flatten_mounts.py holds the two equal): this runs on the host, without the app
FILES_KINDS = frozenset({"pubmed-pack", "legi-pack", "wikipedia-page-props", "wikidata-pack"})


def build_area(host: str | None = None, *, is_dataset=None) -> tuple[tuple, dict]:
    """`(shared, env)` for `apply`: the build area's mount and its variable
    when `host` (else BUILD_HOST) is a mounted dataset on this host, else
    nothing. `is_dataset` (tests): the check, `os.path.ismount` by default —
    a plain directory is not a dataset, and its properties are not the build
    area's (atime off, lz4, recordsize 1M, primarycache metadata)."""
    import os
    host = host or BUILD_HOST
    check = is_dataset or os.path.ismount
    if not host or not check(host):
        return (), {}
    return ((host, BUILD_TARGET, False),), {"LOUISE_BUILD_DIR": BUILD_TARGET}


def _area(host: str | None, mounts: tuple, check, there) -> tuple:
    if not host or not check(host) or not there(f"{host}/{READY}"):
        return ()
    return tuple((f"{host}/{sub}", target, ro) for sub, target, ro in mounts if there(f"{host}/{sub}"))


def data_host(cfg: dict) -> str | None:
    """The host folder behind the worker's `/data`: where the caches are
    today, before an area is mounted over or beside them."""
    for v in (cfg.get("services", {}).get("worker", {}) or {}).get("volumes") or []:
        src, dst = _parts(v)
        if dst == "/data":
            return src
    return None


def missing(area_cache: str, old_cache: str | None, *, files: bool, there, listdir, isdir) -> list[str]:
    """Every `<kind>/<tag>` the node's cache holds today (`old_cache`, on the
    host) of the kinds this area takes — the FILES kinds, or every other —
    that the area's copy (`area_cache`) lacks: a cache the copy missed would
    be REBUILT once the area is mounted (wikidata's pack, from the dump: days)."""
    if not old_cache or not there(old_cache):
        return []
    out = []
    for kind in sorted(listdir(old_cache)):
        if (kind in FILES_KINDS) != files or not isdir(f"{old_cache}/{kind}"):
            continue
        out += [f"{kind}/{tag}" for tag in sorted(listdir(f"{old_cache}/{kind}"))
                if not tag.startswith(".") and isdir(f"{old_cache}/{kind}/{tag}")
                and not there(f"{area_cache}/{kind}/{tag}")]
    return out


def cache_areas(fast_host: str | None = None, files_host: str | None = None, *, data: str | None = None,
                is_dataset=None, exists=None, listdir=None, isdir=None) -> tuple[tuple, dict, list[str]]:
    """`(shared, env, said)` for `apply`: the two cache areas' mounts and the
    files cache's variable, each area only when its host (else FAST_HOST,
    FILES_HOST) is a mounted dataset holding READY and every cache the node
    holds today under `<data>/cache` of its kinds (and, for the files area,
    every file of `<data>/pubmed-pack`), the fast one only beside the files
    one; `said`, a line per area for the deploy's output. `is_dataset`,
    `exists`, `listdir`, `isdir` (tests): the `os` calls."""
    import os
    check, there = is_dataset or os.path.ismount, exists or os.path.exists
    ls, isd = listdir or os.listdir, isdir or os.path.isdir
    files = _area(files_host or FILES_HOST, FILES_MOUNTS, check, there)
    fast = _area(fast_host or FAST_HOST, FAST_MOUNTS, check, there)
    said = []
    old = f"{data}/cache" if data else None
    if files:
        host = files_host or FILES_HOST
        lacking = missing(f"{host}/cache", old, files=True, there=there, listdir=ls, isdir=isd)
        if data and there(f"{data}/pubmed-pack"):
            lacking += [f"pubmed-pack/{n}" for n in sorted(ls(f"{data}/pubmed-pack"))
                        if not n.startswith(".") and not there(f"{host}/pubmed-pack/{n}")]
        if lacking:
            said.append(f"files cache: NOT mounted: its copy lacks {len(lacking)} of today's caches "
                        f"({', '.join(lacking[:5])}{' …' if len(lacking) > 5 else ''}), which would be rebuilt")
            files = ()
    if fast:
        host = fast_host or FAST_HOST
        lacking = missing(f"{host}/cache", old, files=False, there=there, listdir=ls, isdir=isd)
        if lacking:
            said.append(f"fast cache: NOT mounted: its copy lacks {len(lacking)} of today's caches "
                        f"({', '.join(lacking[:5])}{' …' if len(lacking) > 5 else ''}), which would be rebuilt")
            fast = ()
    env = dict(FILES_ENV) if any(t == "/data/files-cache" for _, t, _ in files) else {}
    if not any(line.startswith("files cache:") for line in said):
        said.append("files cache: " + ", ".join(f"{h} -> {t}" for h, t, _ in files) if files else
                    "files cache: no ssd3 dataset named, mounted and READY: the packs stay where they are")
    if fast and not files:
        said.append("fast cache: NOT mounted: it needs the files cache beside it (the packs were not "
                    "copied to it and would be rebuilt)")
        fast = ()
    elif not any(line.startswith("fast cache:") for line in said):
        said.append("fast cache: " + ", ".join(f"{h} -> {t}" for h, t, _ in fast) if fast else
                    "fast cache: no ssd5 dataset named, mounted and READY: the caches stay where they are")
    return files + fast, env, said


def _parts(v) -> tuple[str | None, str | None]:
    if isinstance(v, str):
        bits = v.split(":")
        return (bits[0], bits[1] if len(bits) > 1 else None)
    if isinstance(v, dict):
        return (v.get("source"), v.get("target"))
    return (None, None)


def apply(cfg: dict, shared=SHARED, env=ENV) -> list[str]:
    services = cfg.get("services")
    if not isinstance(services, dict):
        raise ValueError("the app config has no services mapping; not touched")
    said: list[str] = []
    for name in SERVICES:
        svc = services.get(name)
        if not isinstance(svc, dict):
            continue
        vols = svc.get("volumes")
        if not isinstance(vols, list):
            raise ValueError(f"service {name} has no volume list; not touched")
        keep = []
        for v in vols:
            src, dst = _parts(v)
            if dst and PER_COLLECTION.match(dst):
                said.append(f"{name}: remove {src} -> {dst}")
            else:
                keep.append(v)
        targets = {_parts(v)[1] for v in keep}
        for host, target, ro_in_web in shared:
            if target not in targets:
                keep.append(f"{host}:{target}" + (":ro" if ro_in_web and name == "web" else ""))
                said.append(f"{name}: add {host} -> {target}" + (" (ro)" if ro_in_web and name == "web" else ""))
        svc["volumes"] = keep
        e = svc.setdefault("environment", {})
        if not isinstance(e, dict):
            raise ValueError(f"service {name}'s environment is not a mapping; not touched")
        for k, val in env.items():
            if e.get(k) != val:
                e[k] = val
                said.append(f"{name}: {k}={val}")
    return said


def main(argv: list[str]) -> int:
    from truenas_api_client import Client
    with Client() as c:
        cfg = c.call("app.config", APP)
        try:
            said = apply(cfg)
        except ValueError as e:
            print(f"flatten_mounts: {e}", file=sys.stderr)
            return 2
        for line in said:
            print(line)
        if not said:
            print("nothing to change")
            return 0
        try:
            c.call("app.update", APP, {"custom_compose_config": cfg}, job=True)
        except Exception as e:   # noqa: BLE001 — its message may quote the config: the type alone
            print(f"flatten_mounts: app.update failed ({type(e).__name__}); see /var/log/middlewared.log",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
