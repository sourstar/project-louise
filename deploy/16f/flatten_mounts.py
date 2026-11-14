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

#: THE FAST CACHE AREA (operator, 2026-11-14: "For very fast caching (like
#: embedding) use ssd5, for bulk caching use ssd3"; storage-tiering.md §10):
#: a dataset on ssd5 (NVMe Gen5) whose subfolders back the container paths the
#: caches ALREADY use — `/data/cache` (LOUISE_CACHE_DIR, app/derived.py) and
#: `/data/pubmed-pack` (PUBMED_PACK_DIR) — so no path inside the containers
#: changes: derived.py keys a cache on its origin's resolved path and the cache
#: dir's value, both the same, and nothing is rebuilt. The pubmed pack keeps
#: its own path, never inside `/data/cache`: there its 2,124 files would share
#: `pubmed-pack/`, the kind name derived.py's own tree uses, and `derived.held`
#: (a rename's walk) would list each as a cache. The dataset's NAME IS
#: MÜNCHEN'S TO REPORT (LOUISE_FAST_HOST at the deploy until it is written here).
FAST_HOST: str | None = None
#: (subfolder of the dataset, container path, read-only in `web`)
FAST_MOUNTS = (("cache", "/data/cache", False),          # web writes the speech cache
               ("pubmed-pack", "/data/pubmed-pack", True))
#: written by the operator's session in the dataset AFTER the copy is compared
#: (count, bytes, a sha256 sample): a half copy is never mounted over the whole
FAST_READY = "READY"


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


def fast_area(host: str | None = None, *, is_dataset=None, exists=None) -> tuple:
    """`shared` for `apply`: the fast area's mounts when `host` (else
    FAST_HOST) is a mounted dataset holding the READY marker, else none.
    A subfolder that does not exist is not mounted: Docker would create it
    EMPTY over the copy on ssd4, and the caches would be rebuilt (the
    wikidata pack is 122 G, the pubmed pack 84 G). `is_dataset` and `exists`
    (tests): `os.path.ismount` and `os.path.exists` by default."""
    import os
    host = host or FAST_HOST
    check, there = is_dataset or os.path.ismount, exists or os.path.exists
    if not host or not check(host) or not there(f"{host}/{FAST_READY}"):
        return ()
    return tuple((f"{host}/{sub}", target, ro) for sub, target, ro in FAST_MOUNTS if there(f"{host}/{sub}"))


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
