"""The possession index is rebuilt for a change of the ACCEPTED bundles only
(app/share/possession.py `_fingerprint`; docs/notes/STATUS.md, 2026-11-08).

A neighbour's claim — a coverage row with no acceptance, recorded from each
digest (`coverage.claim`) — changed the fingerprint, and the whole index,
every uid of every accepted bundle, was built again for a row it does not
read: pubmed's, 14.55 GB allocated under `_index_cold` in 81 s on the node.
"""
import gc
import tracemalloc

from app.share import coverage as cov
from app.share import possession
from tests.share.test_contribute import ready  # noqa: F401


def test_claims_do_not_rebuild_the_index_an_acceptance_does(ready):
    root, _snapshot, codex, kw = ready
    con = cov.ensure_schema(kw["con"])
    key = str(root / "gutenberg")
    possession.REBUILDS.pop(key, None)
    first = possession.index(root, "gutenberg", codex, con)
    assert possession.REBUILDS[key] == 1
    tracemalloc.start(1)
    try:
        gc.collect()
        base = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        for k in range(30):
            cov.claim(con, bundle_id=f"sha256:{k:064x}", shard=cov.shard_id(codex, "lang=en/00001"),
                      key=f"peer-{k}")
            cov.claim(con, bundle_id="sha256:" + "d" * 64, shard=cov.shard_id(codex, "lang=en/00001"),
                      key=f"peer-{k}")
            assert possession.index(root, "gutenberg", codex, con) is first
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    assert possession.REBUILDS[key] == 1, "thirty claims, no rebuild"
    assert peak < 256 << 10, f"{peak} bytes at the peak of thirty claims' rounds"
    cov.record(con, bundle_id="sha256:" + "c" * 64, shard=cov.shard_id(codex, "lang=en/00001"),
               producer="k", uids=[], chunks=0, tiers={}, source=cov.FETCHED,
               accepted_at="2026-11-08T00:00:00Z")
    possession.index(root, "gutenberg", codex, con)
    assert possession.REBUILDS[key] == 2, "an accepted bundle is a new index"
    con.execute("DELETE FROM coverage WHERE bundle_id=?", ("sha256:" + "c" * 64,))
    possession.index(root, "gutenberg", codex, con)
    assert possession.REBUILDS[key] == 3, "an accepted bundle withdrawn is a new index"
