"""A RE-SCAN THAT MOVES A DOCUMENT'S LANGUAGE (2026-11-21, the review's P5):
a gutenberg book whose RDF language is corrected en -> fr and whose files
changed. Discovery wrote lang=fr in the same UPDATE that reset rdf/fts, so
the rdf stage saw no move and the fts stage wrote the fr group beside the
old en rows (the pubmed duplicate by another path). Now discovery deletes
the rows of the group the language leaves, before its marks commit."""
from __future__ import annotations


from tests.test_fts_batching import _discovered, _pending

from app import discovery, fts, worker


def _run_all(con, c):
    for _ in range(50):
        r = con.execute("SELECT * FROM documents WHERE rdf_done=0 AND error IS NULL ORDER BY native_id").fetchone()
        if r is None:
            break
        worker._execute_step(con, c, "rdf", r)
    while _pending(con) is not None:
        worker._execute_step(con, c, "fts", _pending(con))


def test_a_rescan_that_moves_a_book_s_language_leaves_no_row_in_the_old_group(tmp_path, monkeypatch, isolated_config):
    c, con = _discovered(tmp_path)
    con.execute("UPDATE documents SET rdf_done=0")
    con.commit()
    _run_all(con, c)
    row = con.execute("SELECT * FROM documents WHERE native_id='10002'").fetchone()
    assert row["lang"] == "en" and row["fts_done"] == 1
    nid = int(row["native_id"])
    assert fts.books_held("en", [nid], c) > 0
    book = c.corpus_dir / "10002"
    rdf = book / "pg10002.rdf"
    rdf.write_text(rdf.read_text().replace(">en</rdf:value>", ">fr</rdf:value>"))
    txt = book / "pg10002.txt.utf8"
    txt.write_text(txt.read_text() + "\nUne ligne de plus.\n")
    discovery.sync_collection(con, c)
    after_scan = con.execute("SELECT lang, rdf_done, fts_done FROM documents WHERE uid=?",
                             (row["uid"],)).fetchone()
    assert tuple(after_scan)[1:] == (0, 0)
    _run_all(con, c)
    en, fr = fts.books_held("en", [nid], c), fts.books_held(fts.group_for("fr"), [nid], c)
    assert en == 0, f"stale: {en} rows in en beside {fr} in {fts.group_for('fr')}"
    assert fr > 0, "indexed in its new group"
