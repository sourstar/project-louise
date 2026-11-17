"""`louise dev pin-run` (app/hold/pin_run.py): München's wikidata.next, made
on 2026-11-17 with a parser-3 Codex naming no run, given the Codex that
names it in place — held here to a by-the-book creation with the corrected
Codex, BYTE FOR BYTE: the SHARD (Codex, members, parts, snapshot and its
sidecar), the markers the carries leave, the published marker, the build
staging's claim and the collection file; the manifest's documents and
cursors; and records-vs-origin passing on it. And every refusal of what may
be held elsewhere."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from app import louise
from app.hold import carry as carry_mod
from app.hold import collection as col
from app.hold import line_carry, origin_run, pin_run
from app.hold import snapshot as sn
from tests.hold import wikidata_runs as wr
from tests.hold.conftest import KEY

RUN, SID, NAME = wr.RUN, wr.SID, "wikidata.next"
NOW = "2026-11-17T12:00:00Z"


@pytest.fixture(autouse=True)
def _keyed(monkeypatch):
    from app.bundles import keys as node_keys
    from app.plugins.wikidata import chunker, entities, pack
    wr.register_tokenizer()
    monkeypatch.setattr(node_keys, "load_signing_key", lambda *a, **k: KEY)
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    pack.reset_caches()
    entities.reset()
    yield
    chunker._tokenizers.pop(wr.TOKENIZER, None)
    pack.reset_caches()
    entities.reset()


def _recreate(data_dir, config_dir, tmp_path, monkeypatch, capsys, *, corrected: bool):
    """Steps 1-6 of the runbook from the live parser-2 collection: by the
    book with the corrected Codex, or as München ran them on 2026-11-17 —
    the Codex naming no run, made and carried by the code before the guards."""
    root, corpus, _live = wr.live(data_dir, config_dir)
    recipe = wr.whole_recipe(root, tmp_path)
    with monkeypatch.context() as m:
        if not corrected:
            m.setattr(origin_run, "refuse_without_run", lambda *a, **k: None)
            m.setattr(louise, "_whole_codex_run", lambda root, spec, run, name: spec)
        assert louise.main(wr.create_args(root, recipe)) == 0
        carry_mod.carry(root, "wikidata", NAME, rebuilt=True, apply=True, log=lambda *_: None)
        line_carry.carry(root, NAME, "wikidata", log=lambda *_: None)
        carry_mod.reconcile(root, "wikidata", NAME, log=lambda *_: None)
    capsys.readouterr()
    return root, corpus


def _norm(rel: str, raw: bytes) -> bytes:
    """A signature's and a marker's time are the clock's, not the content's."""
    if rel.startswith("shard/snapshots/") and rel.endswith(".sig.json"):
        d = json.loads(raw)
        for s in d["signatures"]:
            s["time"] = "T"
        return json.dumps(d, sort_keys=True).encode()
    if rel.startswith("state/published/"):
        d = json.loads(raw)
        d["at"] = "T"
        return json.dumps(d, sort_keys=True).encode()
    return raw


def _world(root: Path, config_dir: Path) -> dict:
    """What a by-the-book creation leaves: every file of the SHARD, the
    markers, the claim, the collection file, and the manifest's documents
    and cursors."""
    base = root / NAME
    out: dict = {}
    for p in sorted((base / "shard").rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(base))
            out[rel] = _norm(rel, p.read_bytes())
    for rel in ("state/carry.json", "state/line-carry.json", f"state/published/{SID}.json",
                "state/build/.codex.json"):
        p = base / rel
        out[rel] = _norm(rel, p.read_bytes()) if p.is_file() else None
    out["toml"] = (config_dir / "collections" / f"{NAME}.toml").read_text()
    # and nothing more under state/ than a creation by the book leaves (this
    # pass's own marker aside)
    out["state_files"] = sorted(str(p.relative_to(base)) for p in (base / "state").rglob("*")
                                if p.is_file() and p.name != pin_run.MARKER
                                and not p.name.endswith(("-wal", "-shm")))
    con = sqlite3.connect(base / "state" / "manifest.sqlite")
    try:
        out["documents"] = con.execute("SELECT uid, path, txt_hash, embed_done FROM documents ORDER BY uid").fetchall()
        out["control"] = sorted(r for r in con.execute("SELECT key, value FROM control").fetchall()
                                if r[0].startswith("carry_"))
        out["space"] = con.execute("SELECT space_id, dim FROM space").fetchall()
    finally:
        con.close()
    with sqlite3.connect(base / "state" / "build" / ".build" / "build.db") as b:
        cols = [r[1] for r in b.execute("PRAGMA table_info(shards)") if r[1] != "written_at"]
        out["build"] = b.execute(f"SELECT {','.join(cols)} FROM shards ORDER BY slice, n").fetchall()
        out["frozen"] = b.execute("SELECT value FROM meta WHERE key='frozen_into'").fetchall()
    return out


def _wipe(data_dir: Path, config_dir: Path) -> None:
    """The two worlds alike: the test's default collection (gutenberg, in
    the same space) stays, as it was there for the first."""
    for n in ("wikidata", NAME):
        shutil.rmtree(data_dir / "collections" / n)
        (config_dir / "collections" / f"{n}.toml").unlink(missing_ok=True)


def test_pin_run_equals_the_creation_by_the_book_byte_for_byte(data_dir, config_dir, isolated_config, tmp_path,
                                                               monkeypatch, capsys):
    root, _ = _recreate(data_dir, config_dir, tmp_path, monkeypatch, capsys, corrected=True)
    book_codex = col.read(root, NAME)
    book = _world(root, config_dir)
    assert origin_run.codex_run(book_codex) == RUN
    _wipe(data_dir, config_dir)

    root, corpus = _recreate(data_dir, config_dir, tmp_path, monkeypatch, capsys, corrected=False)
    bad = col.read(root, NAME)
    assert origin_run.why_no_run(bad) and bad.collection_id != book_codex.collection_id
    snap = sn.load(root / NAME / "shard" / "snapshots" / f"{SID}.json")
    assert snap.origin_run is None, "München's snapshot: no run"
    # the step-6 check's attestations of the old id, as the node's check wrote them
    att = root / NAME / "state" / "attestations" / bad.collection_id.split(":")[1]
    att.mkdir(parents=True)
    (att / ("ab" * 32 + ".json")).write_text(json.dumps({"collection_id": bad.collection_id}))

    plan = pin_run.plan(root, NAME)
    assert plan["run"] == RUN and plan["to"] == book_codex.collection_id
    assert plan["space_id"]["from"] == plan["space_id"]["to"], "the space does not move"
    assert len(plan["removed"]["attestations"]) == 1
    assert plan["dump"]["missing"] == [f"wikidatawiki/entities/{RUN}/wikidata-{RUN}-md5sums.txt",
                                       f"wikidatawiki/entities/{RUN}/wikidata-{RUN}-sha1sums.txt"]
    assert any("manifest's locators: 20261012" in s for s in plan["run_said_by"])
    assert col.read(root, NAME).collection_id == bad.collection_id, "the plan writes nothing"

    got = pin_run.apply(root, NAME, key=KEY, now=NOW, log=lambda *_: None)
    assert got["step"] == "done" and got["attestations_removed"] == 1
    mine = _world(root, config_dir)
    assert sorted(mine) == sorted(book)
    for k in book:
        assert mine[k] == book[k], (k, mine[k][:600], book[k][:600])
    assert pin_run.apply(root, NAME, key=KEY, now=NOW, log=lambda *_: None)["step"] == "done", "repeatable"

    # and records-vs-origin runs on it, in its run
    from app.hold import check as chk
    new = col.read(root, NAME)
    snap = sn.load(root / NAME / "shard" / "snapshots" / f"{SID}.json")
    o = chk.run(root, NAME, new, snap, seed=b"\0" * 32, checks=["records-vs-origin"], sample="1",
                reparse=louise._reparse_from(new, NAME, corpus, run=origin_run.run_of(new, snap)),
                origin_unit=louise._origin_unit_from(new, NAME, corpus, run=RUN)).outcome("records-vs-origin")
    assert o.result == chk.PASS, o.why


def test_a_stopped_pin_resumes_to_the_same_bytes(data_dir, config_dir, isolated_config, tmp_path, monkeypatch,
                                                 capsys):
    root, _ = _recreate(data_dir, config_dir, tmp_path, monkeypatch, capsys, corrected=False)
    real = pin_run._repoint_json
    with monkeypatch.context() as m:
        m.setattr(pin_run, "_repoint_json", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt):
            pin_run.apply(root, NAME, key=KEY, now=NOW, log=lambda *_: None)
    body = json.loads((root / NAME / "state" / pin_run.MARKER).read_text())
    assert body["step"] == "codex" and pin_run._repoint_json is real
    got = pin_run.apply(root, NAME, key=KEY, now=NOW, log=lambda *_: None)
    assert got["step"] == "done"
    new = col.read(root, NAME)
    assert origin_run.codex_run(new) == RUN
    assert sn.load(root / NAME / "shard" / "snapshots" / f"{SID}.json").origin_run == RUN
    assert pin_run._sweep(root, NAME, body["from"]) == []


@pytest.mark.parametrize("why", ["share", "served", "attested", "pointer", "named", "run"])
def test_anything_published_or_shared_is_refused(why, data_dir, config_dir, isolated_config, tmp_path,
                                                monkeypatch, capsys):
    root, _ = _recreate(data_dir, config_dir, tmp_path, monkeypatch, capsys, corrected=False)
    bad = col.read(root, NAME)
    toml = config_dir / "collections" / f"{NAME}.toml"
    match = {"share": "share = true", "served": "the seeder served it", "attested": "published 1 attestation",
             "pointer": "a certifier's pointer", "named": "name(s) its id", "run": "the run is said differently"}[why]
    kw = {}
    if why == "share":
        toml.write_text(toml.read_text().replace("share = false", "share = true"))
    elif why == "served":
        with sqlite3.connect(root / NAME / "state" / "manifest.sqlite") as con:
            con.execute("INSERT INTO control(key, value) VALUES('share_served', ?)",
                        (json.dumps({"snapshot_id": SID}),))
    elif why == "attested":
        from app import node_state
        from app.share import attest
        con = attest.ensure_schema(node_state.connect())
        con.execute("INSERT INTO attestations_published VALUES('sha256:aa', 'e', ?, 1.0)", (bad.collection_id,))
        con.close()
    elif why == "pointer":
        from app.hold.certify import pointer_path
        p = pointer_path(root, NAME, bad)
        p.parent.mkdir(parents=True)
        p.write_text("{}")
    elif why == "named":
        d = json.loads((root / "wikidata" / "shard" / "codex.json").read_bytes())
        d["descriptive"]["summary"] = f"annotates {bad.collection_id}"    # a Codex naming the id
        d["work"]["tokens"] += 1
        d.pop("collection_id", None)
        from app.hold import codex as cx
        with monkeypatch.context() as m:
            m.setattr(origin_run, "refuse_without_run", lambda *a, **k: None)
            col.create(root, cx.from_dict(d), name="other")
    else:
        kw["run"] = "20261101"
    before = (root / NAME / "shard" / "codex.json").read_bytes()
    with pytest.raises(pin_run.PinRefused, match=re.escape(match)):
        pin_run.plan(root, NAME, **kw)
    assert louise.main(["dev", "pin-run", NAME, "--root", str(root), "--yes",
                        *(["--run", kw["run"]] if kw else [])]) == 2
    assert (root / NAME / "shard" / "codex.json").read_bytes() == before, "nothing written"
    assert not (root / NAME / "state" / pin_run.MARKER).exists()


def test_a_codex_naming_its_run_has_nothing_to_pin(data_dir, config_dir, isolated_config, tmp_path, monkeypatch,
                                                   capsys):
    root, _ = _recreate(data_dir, config_dir, tmp_path, monkeypatch, capsys, corrected=True)
    with pytest.raises(pin_run.PinRefused, match="names run 20261012 already"):
        pin_run.plan(root, NAME)
    assert louise.main(["dev", "pin-run", NAME, "--root", str(root)]) == 2


def test_the_dry_run_says_what_it_would_do(data_dir, config_dir, isolated_config, tmp_path, monkeypatch, capsys):
    root, _ = _recreate(data_dir, config_dir, tmp_path, monkeypatch, capsys, corrected=False)
    before = (root / NAME / "shard" / "codex.json").read_bytes()
    assert louise.main(["dev", "pin-run", NAME, "--root", str(root)]) == 0
    out, err = capsys.readouterr()
    assert f"would pin run {RUN}" in err and "space unchanged" in err and "nothing written (--yes)" in err
    assert json.loads(out)["plan"]["run"] == RUN
    assert (root / NAME / "shard" / "codex.json").read_bytes() == before
