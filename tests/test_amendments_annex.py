"""The annex an amendment's article is held under (2026-11-12): the older
exports' URN destinations label their fragment "<annex>-art. N", and the
amendments index read "codicecivile-art2043" as the article, so no citation
ever met it; and `_changes` ignored the citation's annex. Measured on the
node's normattiva: c.c. 132 of 136 rows, disp. att. c.p.p. 35 of 35."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from app import collections as collections_mod, db, validity, worker
from app.ask import articles
from app.plugins.normattiva import source as nsrc

CC = "urn:nir:stato:regio.decreto:1942-03-16;262"
LAW = "urn:nir:stato:legge:1990-08-07;241"


@pytest.mark.parametrize("part, annex, token", [
    ("CODICE CIVILE-art. 2441", "CODICE CIVILE", "2441"),
    ("CODICE CIVILE-art. 1469 bis", "CODICE CIVILE", "1469bis"),
    ("Allegato 1 Codice del processo amministrativo-art. 120",
     "Allegato 1 Codice del processo amministrativo", "120"),
    ("Norme-art. 227", "Norme", "227"),
    ("Allegato 4 Norme di coordinamento e abrogazioni -art. 1",
     "Allegato 4 Norme di coordinamento e abrogazioni", "1"),
    ("CODICE  CIVILE - art. 12", "CODICE CIVILE", "12"),
    ("12", "", "12"),
    ("5-bis", "", "5bis"),
    ("art5-com2", "", "5"),
])
def test_a_urn_fragment_gives_its_annex_and_article(part, annex, token):
    mod = {"destination": f"{CC}#{part}"}
    assert nsrc.destination_annex(mod) == annex
    assert validity.article_token(nsrc.destination_article(mod)) == token


def test_the_akn_form_names_no_annex():
    mod = {"destination": "/akn/it/act/legge/stato/1990-08-07/241/!main/~art_145__para_1"}
    assert nsrc.destination_annex(mod) == ""
    assert validity.article_token(nsrc.destination_article(mod)) == "145"


def _mod(dest: str, kind: str = "substitution") -> dict:
    return {"destination": dest, "type": kind}


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    return con


def _state(con, uid: str, work: str, key: str, mods: list[dict]) -> None:
    doc = SimpleNamespace(extra={"vigenza_from": "2020-01-01", "active_modifications": mods}, keys=[])
    validity._record_amendments(con, uid, validity.amendments_of_doc(doc, nsrc.NormattivaSource()))
    con.execute("INSERT OR IGNORE INTO validity_works(key, work) VALUES(?,?)", (key, work))


@pytest.fixture
def cc():
    con = _con()
    _state(con, "normattiva:1", "cc-work", CC, [
        _mod(f"{CC}#CODICE CIVILE-art. 2043"),
        _mod(f"{CC}#Disposizioni sulla legge in generale-art. 12"),   # the preleggi's art. 12
        _mod(f"{CC}#1"),                                               # the decree's, or the code's?
        _mod(f"{CC}#CODICE CIVILE-art. 1", "repeal")])
    return con


def test_the_codes_article_lists_its_own_changes(cc):
    got = articles._changes(cc, "cc-work", "2043", ("CODICE CIVILE",))
    assert [(r["su"], r["tipo"]) for r in got] == [("art. 2043", "substitution")]
    row = cc.execute("SELECT annex, article FROM validity_amendments WHERE article='2043'").fetchone()
    assert tuple(row) == ("CODICE CIVILE", "2043")


def test_another_annexs_article_of_the_same_number_is_not_listed(cc):
    assert articles._changes(cc, "cc-work", "12", ("CODICE CIVILE",)) == []
    # the preleggi's own annex lists it
    assert [r["su"] for r in articles._changes(cc, "cc-work", "12",
                                               ("Disposizioni sulla legge in generale",))] == ["art. 12"]


def test_a_bare_article_several_candidates_hold_is_shown_on_each_marked(cc):
    """The architecture session's rule (2026-11-12): a bare `#1` on an annex
    code, when the decree, the preleggi and the code all hold an art. 1, is
    stored with no annex and shown on EVERY candidate's fact, marked — lost
    nowhere, claimed nowhere."""
    held = {None, "Disposizioni sulla legge in generale", "CODICE CIVILE"}
    got = articles._changes(cc, "cc-work", "1", ("CODICE CIVILE",), held)
    assert sorted((r["su"], r["tipo"]) for r in got) == [
        ("art. 1", "repeal"), ("art. 1 (la fonte non indica di quale testo)", "substitution")]
    pre = articles._changes(cc, "cc-work", "1", ("Disposizioni sulla legge in generale",), held)
    assert [r["su"] for r in pre] == ["art. 1 (la fonte non indica di quale testo)"], "on the preleggi's fact too"
    assert cc.execute("SELECT COUNT(*) FROM validity_amendments WHERE article='1' AND annex IS NULL"
                      ).fetchone()[0] == 1, "stored once, with no annex"


def test_a_bare_article_only_one_candidate_holds_goes_to_it_unmarked():
    """AKN `~art_115` on c.p.a.: the decree holds arts 1-2 only, so Allegato
    1 is the one candidate holding art. 115 — read from the held version."""
    con = _con()
    cpa = "urn:nir:stato:decreto.legislativo:2010-07-02;104"
    _state(con, "normattiva:2", "cpa-work", cpa, [
        _mod("/akn/it/act/decreto_legislativo/stato/2010-07-02/104/ita@2022-10-18/!main/~art_115")])
    # a real collection holds every canonical id of the Work, the AKN key too
    con.execute("INSERT OR IGNORE INTO validity_works(key, work) VALUES(?,?)",
                ("akn:it/act/decreto_legislativo/stato/2010-07-02/104", "cpa-work"))
    assert con.execute("SELECT target, annex FROM validity_amendments").fetchone()[1] is None
    a1 = "Allegato 1 Codice del processo amministrativo"
    got = articles._changes(con, "cpa-work", "115", (a1,), {a1})
    assert [r["su"] for r in got] == ["art. 115"]
    assert articles._changes(con, "cpa-work", "115", ("Allegato 2 Norme di attuazione",), {a1}) == [], \
        "an annex not holding the number never claims it"


def test_the_decree_cited_without_annexes_sees_only_its_own_articles(cc):
    """"art. 1 del regio decreto 262/1942" (no annexes): the unannexed rows,
    never the code's `CODICE CIVILE-art. 1`."""
    got = articles._changes(cc, "cc-work", "1")
    assert [(r["su"], r["tipo"]) for r in got] == [("art. 1", "substitution")]


def test_a_structured_code_is_unchanged():
    con = _con()
    _state(con, "normattiva:2", "law-work", LAW, [_mod(f"{LAW}#5"), _mod(LAW, "repeal"),
                                                 _mod(f"{LAW}#6")])
    got = articles._changes(con, "law-work", "5")
    assert sorted((r["su"], r["tipo"]) for r in got) == [("art. 5", "substitution"), ("l'atto", "repeal")]
    assert validity.amendments_of(con, [LAW], "5") == validity.amendments_of(con, [LAW], "5", annexes=())


def _collection(tmp_path):
    return collections_mod.Collection(
        name="normattiva", plugin="normattiva", profile=None, embed_mode_default=None,
        corpus_dir=tmp_path / "corpus", manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb",
        embed=collections_mod.EmbedRecipe(url="http://cpu-encoder:1", api="openai", model="m",
                                          dtype="f16", ctx=512, dim=8, concurrency=2))


def _old_manifest(path) -> None:
    """A manifest made before the annex: the table without the column, its
    key without it, one row as the old hook stored it."""
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE validity_amendments (stated_by TEXT NOT NULL, target TEXT NOT NULL,
          article TEXT NOT NULL DEFAULT '', amending TEXT NOT NULL DEFAULT '', day INTEGER,
          kind TEXT NOT NULL, PRIMARY KEY (stated_by, target, article, amending, kind));
        CREATE INDEX idx_validity_amendments_target ON validity_amendments(target, article);
        INSERT INTO validity_amendments VALUES ('normattiva:1', 'urn:x', 'codicecivile-art2043', '', NULL, 'm');
    """)
    old.commit()
    old.close()


def test_an_old_manifest_gains_the_column_and_is_due_once(tmp_path, isolated_config):
    _old_manifest(tmp_path / "manifest.db")
    con = db.connect(_collection(tmp_path))
    cols = {r[1] for r in con.execute("PRAGMA table_info(validity_amendments)")}
    assert "annex" in cols
    assert con.execute("SELECT COUNT(*) FROM validity_amendments").fetchone()[0] == 1, "rows kept"
    assert not validity.amendments_current(con), "an old table is re-derived"


def test_a_new_manifest_has_the_annex_in_its_key_and_nothing_to_rederive(tmp_path, isolated_config):
    con = db.connect(_collection(tmp_path))
    pk = {r[1]: r[5] for r in con.execute("PRAGMA table_info(validity_amendments)")}
    assert pk["annex"]
    assert validity.amendments_current(con)


def test_the_rederivation_reads_the_held_records_and_runs_once(tmp_path, isolated_config, monkeypatch):
    """Every version's rows from its record line (no parse), the key rebuilt
    with the rows kept until then, a version no held part carries reset to
    its rdf stage, the version marked last; a second turn does nothing."""
    from app.hold import collection as col
    from app.pack import records as pack_records
    from app.share import seeder
    _old_manifest(tmp_path / "manifest.db")
    c = _collection(tmp_path)
    con = db.connect(c)
    for nid in (1, 2):
        con.execute("INSERT INTO documents(uid, source, native_id, path, rdf_done) VALUES(?,?,?,?,1)",
                    (f"normattiva:{nid}", "normattiva", str(nid), f"/x/{nid}"))
        con.execute("INSERT INTO validity(uid, native_id, work, valid_from) VALUES(?,?,?,20200101)",
                    (f"normattiva:{nid}", nid, "cc-work"))
    monkeypatch.setattr(collections_mod, "louise_root", lambda: tmp_path / "louise")
    tree = col.shard_dir_of(tmp_path / "louise" / "collections", "normattiva")
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "p1").write_bytes(b"")
    monkeypatch.setattr(seeder, "current_snapshot", lambda _t: SimpleNamespace(shards=[SimpleNamespace(path="p1")]))
    line = {"uid": "normattiva:1", "keys": [CC], "extra": {
        "vigenza_from": "2020-01-01", "active_modifications": [_mod(f"{CC}#CODICE CIVILE-art. 2043"),
                                                               _mod(f"{CC}#CODICE CIVILE-art. 12")]}}
    reads = []

    def fields(path, names):
        reads.append(path.name)
        return iter([line, {"uid": "normattiva:9", "extra": {}}])
    monkeypatch.setattr(pack_records, "iter_fields", fields)
    monkeypatch.setattr(nsrc.NormattivaSource, "parse", lambda *a, **k: pytest.fail("nothing is re-parsed"))

    assert worker._rederive_amendments(con, c) is True
    rows = sorted(tuple(r) for r in con.execute(
        "SELECT stated_by, article, annex FROM validity_amendments"))
    assert rows == [("normattiva:1", "12", "CODICE CIVILE"), ("normattiva:1", "2043", "CODICE CIVILE")]
    assert {r[1]: r[5] for r in con.execute("PRAGMA table_info(validity_amendments)")}["annex"], "rekeyed"
    assert con.execute("SELECT rdf_done FROM documents WHERE uid='normattiva:2'").fetchone()[0] == 0, \
        "not in a held part: re-parsed by its rdf stage"
    assert con.execute("SELECT rdf_done FROM documents WHERE uid='normattiva:1'").fetchone()[0] == 1
    assert validity.amendments_current(con)
    assert worker._rederive_amendments(con, c) is False
    assert reads == ["p1"], "once"
