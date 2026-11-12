"""The annex an amendment's article is held under (2026-11-12): the older
exports' URN destinations label their fragment "<annex>-art. N", and the
amendments index read "codicecivile-art2043" as the article, so no citation
ever met it; and `_changes` ignored the citation's annex. Measured on the
node's normattiva: c.c. 132 of 136 rows, disp. att. c.p.p. 35 of 35."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import collections as collections_mod, db, validity, worker
from app.ask import articles
from app.plugins.normattiva import source as nsrc
from app.refs.normattiva import label_token

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


def test_the_article_is_one_token_carrying_the_annex_as_held(cc):
    """The architecture session's key (2026-11-12): `<annex>|<token>` in the
    existing `article` column, the annex whitespace normalised and case as
    held; the bare token for a destination naming no annex. No new column."""
    assert sorted(r[0] for r in cc.execute("SELECT article FROM validity_amendments")) == [
        "1", "CODICE CIVILE|1", "CODICE CIVILE|2043", "Disposizioni sulla legge in generale|12"]
    assert "annex" not in {r[1] for r in cc.execute("PRAGMA table_info(validity_amendments)")}
    assert validity.stored_article("CODICE  CIVILE", "Art. 1469 bis") == "CODICE CIVILE|1469bis"
    assert validity.stored_article("", "art. 5") == "5"


def test_the_codes_article_lists_its_own_changes(cc):
    got = articles._changes(cc, "cc-work", "2043", ("CODICE CIVILE",))
    assert [(r["su"], r["tipo"]) for r in got] == [("art. 2043", "substitution")]


def test_another_annexs_article_of_the_same_number_is_not_listed(cc):
    assert articles._changes(cc, "cc-work", "12", ("CODICE CIVILE",)) == []
    assert [r["su"] for r in articles._changes(cc, "cc-work", "12",
                                               ("Disposizioni sulla legge in generale",))] == ["art. 12"]


def test_a_bare_article_several_candidates_hold_is_shown_on_each_marked(cc):
    """A bare `#1` on an annex code, when the decree, the preleggi and the
    code all hold an art. 1: shown on EVERY candidate's fact, marked — lost
    nowhere, claimed nowhere."""
    held = {None, "Disposizioni sulla legge in generale", "CODICE CIVILE"}
    got = articles._changes(cc, "cc-work", "1", ("CODICE CIVILE",), held)
    assert sorted((r["su"], r["tipo"]) for r in got) == [
        ("art. 1", "repeal"), (f"art. 1 ({articles.AMBIGUOUS})", "substitution")]
    pre = articles._changes(cc, "cc-work", "1", ("Disposizioni sulla legge in generale",), held)
    assert [r["su"] for r in pre] == [f"art. 1 ({articles.AMBIGUOUS})"], "on the preleggi's fact too"


def test_the_decree_cited_without_annexes_sees_its_own_articles_marked_where_ambiguous(cc):
    """"art. 1 del regio decreto 262/1942" (no annexes): the bare rows only,
    never the code's `CODICE CIVILE|1`, and the bare `#1` carries the same
    mark as on the code's fact (defect 5)."""
    held = {None, "Disposizioni sulla legge in generale", "CODICE CIVILE"}
    got = articles._changes(cc, "cc-work", "1", (), held)
    assert [(r["su"], r["tipo"]) for r in got] == [(f"art. 1 ({articles.AMBIGUOUS})", "substitution")]
    assert [r["su"] for r in articles._changes(cc, "cc-work", "1", (), {None})] == ["art. 1"], \
        "one candidate: unmarked"


def test_a_bare_article_only_one_candidate_holds_goes_to_it_unmarked():
    """AKN `~art_115` on c.p.a.: the decree holds arts 1-2 only, so Allegato
    1 is the one candidate holding art. 115 — read from the held version."""
    con = _con()
    cpa = "urn:nir:stato:decreto.legislativo:2010-07-02;104"
    _state(con, "normattiva:2", "cpa-work", cpa, [
        _mod("/akn/it/act/decreto_legislativo/stato/2010-07-02/104/ita@2022-10-18/!main/~art_115")])
    con.execute("INSERT OR IGNORE INTO validity_works(key, work) VALUES(?,?)",
                ("akn:it/act/decreto_legislativo/stato/2010-07-02/104", "cpa-work"))
    assert con.execute("SELECT article FROM validity_amendments").fetchone()[0] == "115"
    a1 = "Allegato 1 Codice del processo amministrativo"
    assert [r["su"] for r in articles._changes(con, "cpa-work", "115", (a1,), {a1})] == ["art. 115"]
    assert articles._changes(con, "cpa-work", "115", ("Allegato 2 Norme di attuazione",), {a1}) == [], \
        "an annex not holding the number never claims it"


CPA = "urn:nir:stato:decreto.legislativo:2010-07-02;104"
CPA_A1 = "Allegato 1 Codice del processo amministrativo"
CPA_LABELS = ["Art. 1.", "Art. 2.", f"{CPA_A1}-art. 1", "Allegato 2 Norme di attuazione-art. 1",
              "Allegato 3 Norme transitorie-art. 1", "Allegato 4 Norme di coordinamento e abrogazioni -art. 1"]


def test_cpa_akn_art_1_para_1_is_a_candidate_of_every_text_holding_art_1():
    """The node's 9 c.p.a. `~art_1__para_1` rows. The decree and Allegati 1-4
    each hold an art. 1, so the AKN path (which names no annex) could be any
    of them: by number alone, Allegati 2-4 ARE candidates — meant: the
    origin does not say which, and only the mark is true. "art. 1 c.p.a."
    (Allegato 1) lists it marked; an article no other text holds would not
    be marked."""
    con = _con()
    _state(con, "normattiva:3", "cpa-work", CPA, [
        _mod("/akn/it/act/decreto_legislativo/stato/2010-07-02/104/ita@2011-01-01/!main/~art_1__para_1")])
    con.execute("INSERT OR IGNORE INTO validity_works(key, work) VALUES(?,?)",
                ("akn:it/act/decreto_legislativo/stato/2010-07-02/104", "cpa-work"))
    held = articles._holders([{"chapter": x} for x in CPA_LABELS], label_token, "1")
    assert held == {None, CPA_A1, "Allegato 2 Norme di attuazione", "Allegato 3 Norme transitorie",
                    "Allegato 4 Norme di coordinamento e abrogazioni"}
    assert [r["su"] for r in articles._changes(con, "cpa-work", "1", (CPA_A1,), held)] == \
        [f"art. 1 ({articles.AMBIGUOUS})"]


def test_holders_reads_the_versions_own_labels():
    chunks = [{"chapter": "Libro I › CODICE CIVILE-art. 1"}, {"chapter": "Art. 1."},
              {"chapter": "Disposizioni sulla legge in generale-art. 1"}, {"chapter": "CODICE CIVILE-art. 2"},
              {"chapter": None}]
    assert articles._holders(chunks, label_token, "1") == {None, "CODICE CIVILE",
                                                           "Disposizioni sulla legge in generale"}
    assert articles._holders(chunks, label_token, "2") == {"CODICE CIVILE"}
    assert articles._holders(chunks, label_token, "9") == set()


def test_holders_come_from_the_same_read_as_the_article(monkeypatch):
    """One `reader.document` per version pinned (should-fix: `_holders`
    re-read the whole code)."""
    from app import reader
    calls = []
    doc = {"chunks": [{"chapter": "Art. 1.", "text": "decreto"},
                      {"chapter": "CODICE CIVILE-art. 1", "text": "codice"}]}
    monkeypatch.setattr(reader, "document", lambda c, n: calls.append(n) or doc)
    want = SimpleNamespace(read=label_token, article="1", citation=SimpleNamespace(annexes=("CODICE CIVILE",)))
    ch, held = articles._read_article(None, 7, want)
    assert ch["text"] == "codice" and held == {None, "CODICE CIVILE"} and calls == [7]


def _codes() -> dict[str, tuple[str, tuple[str, ...]]]:
    raw = json.loads((Path(articles.__file__).resolve().parent.parent / "refs" / "normattiva_codes.json")
                     .read_text(encoding="utf-8"))
    return {c["abbrev"][0]: (c["urn"], tuple(c.get("annexes") or ())) for c in raw["codes"]}


#: every labelled fragment spelling München counted on the node for the 8
#: annex codes (2026-11-12): the code, the spelling, whether it is the code's own
SPELLINGS = [
    ("c.c.", "CODICE CIVILE", True),
    ("c.c.", "Disposizioni sulla legge in generale", False),          # the preleggi, not the code's
    ("c.p.", "Codice Penale", True),
    ("c.p.c.", "CODICE DI PROCEDURA CIVILE", True),
    ("cod. nav.", "Codice della navigazione", True),
    ("disp. att. c.c.", "Disposizioni per l'attuazione del Codice Civile e disposizioni transitorie", True),
    ("disp. att. c.p.c.", "DISPOSIZIONI DI ATTUAZIONE DEL CODICE DI PROCEDURA CIVILE", True),
    ("disp. att. c.p.p.", "Norme", True),
    ("c.p.a.", "Allegato 1 Codice del processo amministrativo", True),
    ("c.p.a.", "Allegato 2 Norme di attuazione", False),
    ("c.p.a.", "Allegato 4 Norme di coordinamento e abrogazioni", False),
]


@pytest.mark.parametrize("code, spelling, own", SPELLINGS)
def test_each_real_spelling_meets_its_code_exactly(code, spelling, own):
    """Exact, case as held, on both sides: the codes table's annex, the
    held label (`in_annex`) and the stored token agree."""
    urn, annexes = _codes()[code]
    con = _con()
    _state(con, "normattiva:5", "w", urn, [_mod(f"{urn}#{spelling}-art. 7")])
    got = articles._changes(con, "w", "7", annexes, {validity.annex_key(spelling)})
    assert ([r["su"] for r in got] == ["art. 7"]) is own
    assert articles.in_annex(f"Libro I › {spelling}-art. 7", annexes) is own


def test_case_is_not_folded():
    """No case-only variant exists on the node; an uppercase fragment for the
    c.p.'s "Codice Penale" is another spelling and does not match."""
    urn, annexes = _codes()["c.p."]
    con = _con()
    _state(con, "normattiva:6", "w", urn, [_mod(f"{urn}#CODICE PENALE-art. 7")])
    assert con.execute("SELECT article FROM validity_amendments").fetchone()[0] == "CODICE PENALE|7"
    assert articles._changes(con, "w", "7", annexes, {"Codice Penale"}) == []
    assert not articles.in_annex("CODICE PENALE-art. 7", annexes)


def test_a_structured_code_is_unchanged():
    con = _con()
    _state(con, "normattiva:2", "law-work", LAW, [_mod(f"{LAW}#5"), _mod(LAW, "repeal"),
                                                 _mod(f"{LAW}#6")])
    for holders in (None, {None}, set()):
        got = articles._changes(con, "law-work", "5", (), holders)
        assert sorted((r["su"], r["tipo"]) for r in got) == [("art. 5", "substitution"), ("l'atto", "repeal")]
    assert validity.amendments_of(con, [LAW], "5") == validity.amendments_of(con, [LAW], "5", annexes=())


# ---- the re-derivation, once -------------------------------------------------

def _collection(tmp_path, plugin="normattiva"):
    return collections_mod.Collection(
        name=plugin, plugin=plugin, profile=None, embed_mode_default=None,
        corpus_dir=tmp_path / "corpus", manifest_path=tmp_path / "manifest.db",
        fts_dir=tmp_path / "fts", lance_dir=tmp_path / "lancedb",
        embed=collections_mod.EmbedRecipe(url="http://cpu-encoder:1", api="openai", model="m",
                                          dtype="f16", ctx=512, dim=8, concurrency=2))


def _old_manifest(path) -> None:
    """A manifest made before the fix: the table as it is, rows as the old
    hook stored them (`codicecivile-art2043`, stale; `5`, not)."""
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE validity_amendments (stated_by TEXT NOT NULL, target TEXT NOT NULL,
          article TEXT NOT NULL DEFAULT '', amending TEXT NOT NULL DEFAULT '', day INTEGER,
          kind TEXT NOT NULL, PRIMARY KEY (stated_by, target, article, amending, kind));
        CREATE INDEX idx_validity_amendments_target ON validity_amendments(target, article);
        INSERT INTO validity_amendments VALUES ('normattiva:1', 'urn:x', 'codicecivile-art2043', '', NULL, 'm');
        INSERT INTO validity_amendments VALUES ('normattiva:2', 'urn:x', 'norme-art227', '', NULL, 'm');
        INSERT INTO validity_amendments VALUES ('normattiva:3', 'urn:x', '5', '', NULL, 'm');
    """)
    old.commit()
    old.close()


def test_an_old_manifest_keeps_its_schema_and_is_due_once(tmp_path, isolated_config):
    _old_manifest(tmp_path / "manifest.db")
    con = db.connect(_collection(tmp_path))
    assert "annex" not in {r[1] for r in con.execute("PRAGMA table_info(validity_amendments)")}
    assert con.execute("SELECT COUNT(*) FROM validity_amendments").fetchone()[0] == 3, "rows kept"
    assert not validity.amendments_current(con), "an old table is re-derived"
    assert validity.stale_versions(con) == {"normattiva:1", "normattiva:2"}


def test_a_new_manifest_has_nothing_to_rederive(tmp_path, isolated_config):
    assert validity.amendments_current(db.connect(_collection(tmp_path)))


@pytest.fixture
def held(tmp_path, isolated_config, monkeypatch):
    """An old manifest with three versions, and two held parts: p1 carries
    version 1, p2 version 2 (each a fake reader of `uid`/`extra`); version 3
    is in no part and holds no stale row. The node runs (its mode allows
    maintenance), as a live node does."""
    monkeypatch.setattr(worker, "_maintenance_allowed", lambda: True)
    from app.hold import collection as col
    from app.pack import records as pack_records
    from app.share import seeder
    _old_manifest(tmp_path / "manifest.db")
    c = _collection(tmp_path)
    con = db.connect(c)
    for nid in (1, 2, 3):
        con.execute("INSERT INTO documents(uid, source, native_id, path, rdf_done) VALUES(?,?,?,?,1)",
                    (f"normattiva:{nid}", "normattiva", str(nid), f"/x/{nid}"))
        con.execute("INSERT INTO validity(uid, native_id, work, valid_from) VALUES(?,?,?,20200101)",
                    (f"normattiva:{nid}", nid, "cc-work"))
    monkeypatch.setattr(collections_mod, "louise_root", lambda: tmp_path / "louise")
    tree = col.shard_dir_of(tmp_path / "louise" / "collections", "normattiva")
    tree.mkdir(parents=True, exist_ok=True)
    for p in ("p1", "p2"):
        (tree / p).write_bytes(b"")
    monkeypatch.setattr(seeder, "current_snapshot", lambda _t: SimpleNamespace(
        shards=[SimpleNamespace(path="p1"), SimpleNamespace(path="p2")]))
    lines = {
        "p1": [{"uid": "normattiva:1", "extra": {"vigenza_from": "2020-01-01", "active_modifications": [
            _mod(f"{CC}#CODICE CIVILE-art. 2043"), _mod(f"{CC}#CODICE CIVILE-art. 12")]}},
            {"uid": "normattiva:9", "extra": {}}],
        "p2": [{"uid": "normattiva:2", "extra": {"vigenza_from": "2020-01-01", "active_modifications": [
            _mod("urn:nir:stato:decreto.legislativo:1989-07-28;271#Norme-art. 227")]}}]}
    reads: list[str] = []
    broken: set[str] = set()

    def fields(path, names):
        reads.append(path.name)
        if path.name in broken:
            raise OSError("unreadable part")
        return iter(lines[path.name])
    monkeypatch.setattr(pack_records, "iter_fields", fields)
    monkeypatch.setattr(nsrc.NormattivaSource, "parse", lambda *a, **k: pytest.fail("nothing is re-parsed"))
    return SimpleNamespace(c=c, con=con, reads=reads, broken=broken)


def _rows(con):
    return sorted(tuple(r) for r in con.execute("SELECT stated_by, article FROM validity_amendments"))


def _rdf(con, nid):
    return con.execute("SELECT rdf_done FROM documents WHERE uid=?", (f"normattiva:{nid}",)).fetchone()[0]


def test_the_rederivation_reads_the_held_records_and_runs_once(held):
    con, c = held.con, held.c
    assert worker._rederive_amendments(con, c) is True
    assert _rows(con) == [("normattiva:1", "CODICE CIVILE|12"), ("normattiva:1", "CODICE CIVILE|2043"),
                          ("normattiva:2", "Norme|227"), ("normattiva:3", "5")]
    assert (_rdf(con, 1), _rdf(con, 2), _rdf(con, 3)) == (1, 1, 1), \
        "rewritten from records; version 3 is in no part but holds no stale row: not re-parsed"
    assert validity.amendments_current(con)
    assert worker._rederive_amendments(con, c) is False
    assert held.reads == ["p1", "p2"], "once"
    assert db.get_control(con, worker.AMENDMENTS_PARTS_KEY) is None


def test_an_unreadable_part_is_left_once_and_the_pass_ends(held):
    """Defect 2: a part that cannot be read made the pass re-read every part
    every turn. Its versions go to the end — re-parsed only where they still
    hold an old labelled row — the part is logged once, the pass marked."""
    con, c = held.con, held.c
    held.broken.add("p2")
    assert worker._rederive_amendments(con, c) is True
    assert validity.amendments_current(con)
    assert (_rdf(con, 1), _rdf(con, 2), _rdf(con, 3)) == (1, 0, 1)
    assert ("normattiva:2", "norme-art227") in _rows(con), "kept until its rdf stage rewrites it"
    warns = [r[0] for r in con.execute("SELECT message FROM events WHERE level='warn' AND message LIKE "
                                       "'%unreadable%'")]
    assert len(warns) == 1 and "p2" in warns[0]
    assert worker._rederive_amendments(con, c) is False
    assert held.reads == ["p1", "p2"]


@pytest.mark.parametrize("why", ["stop", "pause"])
def test_a_pass_stopped_between_parts_resumes_after_them(held, monkeypatch, why):
    from app import node_state
    con, c = held.con, held.c
    real = validity.write_amendments

    def once_then_halt(con_, pairs):
        n = real(con_, pairs)
        if why == "stop":
            worker._stop.set()
        else:
            monkeypatch.setattr(node_state, "paused_collections", lambda *a: {"normattiva"})
        return n
    monkeypatch.setattr(validity, "write_amendments", once_then_halt)
    try:
        assert worker._rederive_amendments(con, c) is True
    finally:
        worker._stop.clear()
    assert not validity.amendments_current(con), "unmarked"
    assert held.reads[:1] == ["p1"]
    monkeypatch.setattr(validity, "write_amendments", real)
    monkeypatch.setattr(node_state, "paused_collections", lambda *a: set())
    held.reads.clear()
    assert worker._rederive_amendments(con, c) is True
    assert held.reads == ["p2"], "p1 is not read again"
    assert validity.amendments_current(con)
    assert _rows(con)[:3] == [("normattiva:1", "CODICE CIVILE|12"), ("normattiva:1", "CODICE CIVILE|2043"),
                              ("normattiva:2", "Norme|227")]


def test_a_plugin_without_the_hook_is_marked_and_nothing_is_copied(tmp_path, isolated_config, monkeypatch):
    """Defect 3: legi, neuris, eurlex — rows from edges, no annex: marked
    current, no part read, the table untouched."""
    monkeypatch.setattr(worker, "_maintenance_allowed", lambda: True)
    from app.pack import records as pack_records
    _old_manifest(tmp_path / "manifest.db")
    c = _collection(tmp_path, plugin="legi")
    con = db.connect(c)
    monkeypatch.setattr(validity, "declared", lambda _c: object())
    monkeypatch.setattr(pack_records, "iter_fields", lambda *a: pytest.fail("no part is read"))
    before = _rows(con)
    assert worker._rederive_amendments(con, c) is True
    assert validity.amendments_current(con) and _rows(con) == before
    assert {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
        "validity_amendments"} and not any(
        r[0].startswith("_validity") for r in con.execute("SELECT name FROM sqlite_master"))


def test_the_nodes_mode_holds_the_pass_and_it_resumes_when_running(held, monkeypatch):
    """Architecture session, 2026-11-12: the pass is maintenance, so the node's
    mode (paused / acquire-only / seed-only, invariant 10) holds it as the
    collection's pause does: it reads nothing and stays unmarked."""
    con, c = held.con, held.c
    monkeypatch.setattr(worker, "_maintenance_allowed", lambda: False)
    assert worker._rederive_amendments(con, c) is False
    assert held.reads == [] and not validity.amendments_current(con)
    monkeypatch.setattr(worker, "_maintenance_allowed", lambda: True)
    assert worker._rederive_amendments(con, c) is True
    assert validity.amendments_current(con)
