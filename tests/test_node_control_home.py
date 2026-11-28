"""THE NODE'S CONTROL HAS ITS OWN HOME (operator, 2026-11-28, choice A):
the query log, the node-wide events and the worker's published samples are
in state/node.sqlite, never in the first collection's manifest; the query
log is copied out of the manifests once, all or nothing."""
import json
import sqlite3
from contextlib import closing

from app import db, node_state


def _manifest(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE query_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, endpoint TEXT, "
                "query TEXT, mode TEXT, k INTEGER, t_catalog REAL, t_fts REAL, t_dense REAL, t_rerank REAL, "
                "t_llm REAL, total_ms REAL, n_results INTEGER, books TEXT)")
    for ts, q in rows:
        con.execute("INSERT INTO query_log(ts,endpoint,query,mode,k) VALUES(?,?,?,?,?)", (ts, "ask", q, "hybrid", 10))
    con.commit()
    return con


def test_the_log_is_copied_once_from_every_manifest_in_time_order(isolated_config, tmp_path):
    a = _manifest(tmp_path / "a.sqlite", [(30, "terza"), (10, "prima")])
    b = _manifest(tmp_path / "b.sqlite", [(20, "seconda")])
    with closing(node_state.connect()) as node:
        db.log_query(node, "ask", "nuova", "hybrid", 10, {}, 0, [])     # asked after the deploy, before the boot
        assert node_state.adopt_query_log(node, [a, b]) == 3
        got = [q for (q,) in node.execute("SELECT query FROM query_log ORDER BY ts, id")]
        assert got[:3] == ["prima", "seconda", "terza"] and "nuova" in got
        moved = json.loads(db.get_control(node, node_state.QUERY_LOG_MOVED_KEY))
        assert moved["rows"] == 3
        assert node_state.adopt_query_log(node, [a, b]) is None, "once"
        assert node.execute("SELECT COUNT(*) FROM query_log").fetchone()[0] == 4
    assert a.execute("SELECT COUNT(*) FROM query_log").fetchone()[0] == 2, "the manifests keep their rows"


def test_a_manifest_that_cannot_be_read_moves_nothing_and_marks_nothing(isolated_config, tmp_path):
    a = _manifest(tmp_path / "a.sqlite", [(10, "prima")])
    with closing(node_state.connect()) as node:
        assert node_state.adopt_query_log(node, [a, None]) is None
        assert node.execute("SELECT COUNT(*) FROM query_log").fetchone()[0] == 0
        assert db.get_control(node, node_state.QUERY_LOG_MOVED_KEY) is None
        assert node_state.adopt_query_log(node, [a]) == 1, "the next boot, every manifest read"


def test_the_worker_s_samples_are_read_from_the_node_s_file(isolated_config):
    with closing(db.control()) as con:
        db.publish_worker_stats(con, {"gutenberg": {"in_flight": 2}})
    ro = db.read_only_control()
    try:
        assert db.read_worker_stats(ro)[0] == {"gutenberg": {"in_flight": 2}}
    finally:
        ro.close()
    from app import web
    assert web._worker_stats() == {"gutenberg": {"in_flight": 2}}
