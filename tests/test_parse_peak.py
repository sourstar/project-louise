"""tools/measure/parse_peak.py — one document's parse, kind by kind, its
peak memory measured (2026-11-20: EUR-Lex5's 32018R0688 el held 11.16 GB,
87x its 128 MB, against the 26x measured on one act's chunks alone)."""
from app import db
from tools.measure import parse_peak


def test_each_kind_says_what_it_held_and_made(isolated_config):
    from app import collections as collections_mod
    from app import discovery
    c = collections_mod.by_name("gutenberg")
    con = db.connect(c)
    discovery.sync_collection(con, c)
    row = dict(con.execute("SELECT * FROM documents ORDER BY uid LIMIT 1").fetchone())
    con.close()
    got = parse_peak.measure_one(c, row, "chunks")
    assert got["ok"] and got["chunks"] > 0 and got["characters"] > 0
    assert got["hwm_after"] >= got["hwm_before"] > 0 and got["rise"] >= 0 and got["seconds"] >= 0
    meta = parse_peak.measure_one(c, row, "metadata")
    assert meta["ok"] and "chunks" not in meta


def _killed(name, uid, kind, q):
    import os
    import signal
    os.kill(os.getpid(), signal.SIGKILL)


def test_a_kind_whose_process_was_killed_says_so():
    got = parse_peak.run("none", "x:1", ["chunks"], target=_killed)
    assert got["kinds"] == [{"kind": "chunks", "killed": True, "exitcode": -9,
                             "note": "the process ended without an answer (-9: the kernel's kill)"}]
