"""`louise dev pin-run` on a wikidata.next holding EVERY file a writer under
`shard/` and `state/` puts the collection_id in (app/hold/pin_run.py's
table, read from the code): its vectors carried with the manifest — the
vector table split by FAMILY (`layout.json`, one part per family) — the
step-6 check's attestations and origin note, an operator's origin
acknowledgement, gate statements made under the old id and one borrowed from
the space, an update that had done nothing, the scheduled check's files and
this node's RAGE settings. The pin takes each as its table says, leaves
nothing anywhere in the collection naming the old id, and the carried
vectors still pass vectors-sample against the new Codex."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app import embeddings, louise
from app.hold import carry as carry_mod
from app.hold import check as chk
from app.hold import collection as col
from app.hold import land, line_carry, origin_run, pin_run, template
from app.hold import records as rec
from app.hold import snapshot as sn
from tests.hold import wikidata_runs as wr
from tests.hold.conftest import KEY, PLATFORM, fake_embed, fake_query_embed

RUN, SID, NAME = wr.RUN, wr.SID, "wikidata.next"
NOW = "2026-11-17T12:00:00Z"


@pytest.fixture(autouse=True)
def _keyed(monkeypatch):
    from app.bundles import keys as node_keys
    from app.plugins.wikidata import chunker, entities, pack
    wr.register_tokenizer()
    monkeypatch.setattr(node_keys, "load_signing_key", lambda *a, **k: KEY)
    monkeypatch.setattr("app.config.CACHE_DIR", None)
    # wikidata's vectors split by family (app/vector_parts.py; docs/notes/
    # vector-tables-split.md), as the coordinator's München layout has them
    from app.pack import slicing
    monkeypatch.setitem(slicing.TABLE_KEYS, "wikidata", "family")
    pack.reset_caches()
    entities.reset()
    yield
    chunker._tokenizers.pop(wr.TOKENIZER, None)
    pack.reset_caches()
    entities.reset()


def _family_live(data_dir: Path, config_dir: Path):
    """The live wikidata at parser 2 sliced by FAMILY under its dump's map, its
    Codex naming no run (the code of 2026-10-20), its file stating the run,
    built and frozen, every chunk embedded into its family's vector part."""
    from unittest import mock

    from app import collections as collections_mod
    from app import db, discovery
    from app.pack import slicing
    from app.plugins.wikidata import pack
    from tests.test_plugins_wikidata_families import CLASSES, _line
    from tests.test_plugins_wikidata_source import a_dump, entity_lines
    root = Path(data_dir) / "collections"
    corpus = root / "wikidata" / "origin"
    dump = a_dump(corpus, RUN, lines=entity_lines() + [_line(o) for o in CLASSES])
    pack.build(dump, languages=("en",), chunk=4, workers=1)
    raw = pack.creation_map(dump)
    r = wr.recipe()
    r["slicing"] = slicing.family_block("sha256:" + hashlib.sha256(raw).hexdigest())
    with mock.patch.object(template, "pin_dated_spec", lambda plugin, spec, run: spec):
        codex = col.create_from_recipe(root, "wikidata", r, platform_image=PLATFORM, embed=fake_embed,
                                       embed_query=fake_query_embed, name="wikidata", key=KEY, now=NOW,
                                       slicing_member=raw)
    (Path(config_dir) / "collections").mkdir(parents=True, exist_ok=True)
    (Path(config_dir) / "collections" / "wikidata.toml").write_text(
        f'codex = "{codex.collection_id}"\nslices = "all"\nkinds = ["records", "vectors"]\nshare = false\n'
        f'snapshot = "{RUN}"\n', encoding="utf-8")
    c = collections_mod.by_name("wikidata")
    con = db.connect(c)
    try:
        discovery.sync_collection(con, c)
        con.execute("INSERT INTO space(id, space_id, dim, pinned_at) VALUES(1, ?, ?, 0)",
                    (codex.space_id, codex.space.dim))
    finally:
        con.close()
    rec.build(root, "wikidata", corpus_dir=corpus, snapshot_id=SID, log=lambda *_: None)
    snap = sn.create(root, "wikidata", snapshot_id=SID, log=lambda *_: None)
    local = collections_mod.by_name("wikidata")          # the node's view: its corpus, to route by family
    rows = []
    for s in snap.shards:
        for cid, e in land.cut(codex, land.records_for(root, "wikidata", snap, f"{s.slice}/{s.n:05d}",
                                                       None)).items():
            rows.append({"id": cid, "book_id": e["book_id"], "lang": e["lang"], "seq": e["seq"],
                         "chapter": e["chapter"], "text": e["text"],
                         "vector": fake_embed([e["text"]], dim=codex.space.dim)[0]})
    embeddings.upsert_chunks(rows, collection=local, dtype=codex.space.dtype)
    return root, corpus, codex, [r["id"] for r in rows]


def _munich(data_dir, config_dir, tmp_path, monkeypatch, capsys):
    """Steps 1-5 as München ran them on 2026-11-17: the Codex naming no run,
    made and carried by the code before the guards; then what the node's
    state may hold beside it."""
    root, corpus, live, ids = _family_live(data_dir, config_dir)
    with monkeypatch.context() as m:
        m.setattr(origin_run, "refuse_without_run", lambda *a, **k: None)
        m.setattr(louise, "_whole_codex_run", lambda root, spec, run, name: spec)
        assert louise.main(wr.create_args(root, wr.whole_recipe(root, tmp_path))) == 0
        carry_mod.carry(root, "wikidata", NAME, rebuilt=True, apply=True, log=lambda *_: None)
        line_carry.carry(root, NAME, "wikidata", log=lambda *_: None)
        carry_mod.reconcile(root, "wikidata", NAME, log=lambda *_: None)
    capsys.readouterr()
    return root, corpus, live, ids


def _every_writer(root: Path, bad, live) -> dict[str, str]:
    """Each file of the table, as its writer writes it, naming the old id."""
    from app import copy_state
    from app.hold import rage_settings, runs, update_state
    from app.hold import shard as shard_mod
    from app.scheduled_checks import STARTED_FILE, STATE_FILE, STDERR_FILE
    state, shard = root / NAME / "state", root / NAME / "shard"
    old = bad.collection_id
    att = state / "attestations" / shard_mod._hex(old)
    att.mkdir(parents=True)
    (att / ("ab" * 32 + ".json")).write_text(json.dumps({"collection_id": old}))
    copy_state.note_origin_attempt(root, NAME, bad, why="an origin naming an entity is read in a run", now=NOW)
    copy_state.record_origin_ack(root, NAME, bad, now=NOW)
    (state / "calibration.json").write_text(json.dumps({"body": {"collection_id": old, "space_id": bad.space_id}}))
    cal = runs.directory(root, NAME)
    cal.mkdir(parents=True, exist_ok=True)
    (cal / ("document-" + "cd" * 8 + ".json")).write_text(json.dumps({"body": {"collection_id": old}}))
    borrowed = cal / ("document-" + "ef" * 8 + ".json")
    borrowed.write_text(json.dumps({"body": {"collection_id": live.collection_id}}))
    runs.record_batching(root, NAME, "document", "sha256:" + "ef" * 32, {"batch": 1})
    update_state.write(root, NAME, {"collection_id": old, "period": "2026-11", "step": "due", "moved": False,
                                    "waiting": {"step": "due", "says": "waiting", "at": NOW}, "last": None})
    (state / STATE_FILE).write_text(json.dumps({"snapshot_id": SID, "report": {"collection_id": old}}))
    (state / STARTED_FILE).write_text(json.dumps({"snapshot_id": SID, "collection_id": old}))
    (state / STDERR_FILE).write_text(f"PASS  {NAME}  snapshot {SID}\n  attested {old}\n")
    card = rage_settings.Card(driver="xe", vendor_id="8086", device_id="e223")
    rage_settings.record(shard, KEY, collection_id=old, space_id=bad.space_id,
                         new=rage_settings.entry(card, engine_id="sha256:" + "12" * 32, batch=8, time=NOW), time=NOW)
    return {"borrowed": str(borrowed.relative_to(root / NAME))}


def _names(base: Path, hexid: bytes) -> list[str]:
    """Every file under the collection's folder whose bytes hold `hexid` —
    the vector table and the text index included, which the pin's sweep
    does not read."""
    out = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.name != pin_run.MARKER and hexid in p.read_bytes():
            out.append(str(p.relative_to(base)))
    return out


def test_every_writers_file_is_taken_as_the_table_says_and_the_vectors_still_check(
        data_dir, config_dir, isolated_config, tmp_path, monkeypatch, capsys):
    from app.hold import rage_settings, update_state
    from app.vector_parts import LAYOUT
    root, _corpus, live, _ids = _munich(data_dir, config_dir, tmp_path, monkeypatch, capsys)
    bad = col.read(root, NAME)
    lance = root / NAME / "lance"
    assert (lance / LAYOUT).is_file() and any(p.name.startswith("family=") for p in lance.iterdir()), \
        "the vectors are split by family, as wikidata's are"
    said = _every_writer(root, bad, live)

    plan = pin_run.plan(root, NAME)
    for rel in ("state/origin-check.json", "state/origin-ack.json", "state/calibration.json",
                "state/update.json", "state/scheduled-check.json", "state/scheduled-check.started.json",
                "state/scheduled-check.stderr", f"shard/rage-settings/{KEY.verify_key.encode().hex()}.json"):
        assert rel in plan["naming"], (rel, sorted(plan["naming"]))
    assert said["borrowed"] not in plan["naming"], "a statement of the space another collection made is kept"
    assert plan["not_swept"], plan

    got = pin_run.apply(root, NAME, key=KEY, now=NOW, log=lambda *_: None)
    assert got["step"] == "done"
    new = col.read(root, NAME)
    assert new.collection_id == plan["to"] and origin_run.codex_run(new) == RUN
    hexid = bad.collection_id.split(":", 1)[1].encode()
    assert _names(root / NAME, hexid) == [], "nothing in the collection names the old id"
    state = root / NAME / "state"
    for gone in ("origin-check.json", "origin-ack.json", "calibration.json", update_state.FILE,
                 "scheduled-check.json", "scheduled-check.started.json", "scheduled-check.stderr"):
        assert not (state / gone).exists(), gone
    assert (root / NAME / said["borrowed"]).is_file() and (state / "gate-batching.json").is_file()
    bodies, refused = rage_settings.read_all(root / NAME / "shard", collection_id=new.collection_id,
                                             space_id=new.space_id)
    assert len(bodies) == 1 and not refused and bodies[0]["entries"][0]["settings"] == {"batch": 8}, \
        "this node's RAGE settings, signed again for the new id"

    # the carried vectors, read through the new Codex, reproduce
    snap = sn.load(root / NAME / "shard" / "snapshots" / f"{SID}.json")
    assert snap.origin_run == RUN
    from app import collections as collections_mod
    local = collections_mod.by_name(NAME)
    o = chk.run(root, NAME, new, snap, seed=b"\0" * 32, checks=["vectors-sample"], sample="1",
                embed=lambda texts: fake_embed(texts, dim=new.space.dim),
                vectors_for=lambda i: embeddings.vectors_by_chunk_id(i, collection=local)).outcome("vectors-sample")
    assert o.result == chk.PASS, o.why
    assert o.detail["chunks"] > 0


def test_an_update_under_way_and_a_peers_rage_settings_refuse_the_pin(data_dir, config_dir, isolated_config,
                                                                       tmp_path, monkeypatch, capsys):
    from nacl.signing import SigningKey

    from app.hold import rage_settings, update_state
    root, _corpus, _live, _ids = _munich(data_dir, config_dir, tmp_path, monkeypatch, capsys)
    bad = col.read(root, NAME)
    before = (root / NAME / "shard" / "codex.json").read_bytes()
    update_state.write(root, NAME, {"collection_id": bad.collection_id, "period": "2026-11", "step": "fetch",
                                    "moved": True, "run": "20261101"})
    with pytest.raises(pin_run.PinRefused, match=r"update of the old Codex is under way \(step 'fetch'"):
        pin_run.plan(root, NAME)
    update_state.path(root, NAME).unlink()
    card = rage_settings.Card(driver="xe", vendor_id="8086", device_id="e223")
    peer = SigningKey(b"\x07" * 32)
    rage_settings.record(root / NAME / "shard", peer, collection_id=bad.collection_id, space_id=bad.space_id,
                         new=rage_settings.entry(card, engine_id="sha256:" + "12" * 32, batch=8, time=NOW),
                         time=NOW)
    with pytest.raises(pin_run.PinRefused, match="another peer signed"):
        pin_run.apply(root, NAME, key=KEY, now=NOW, log=lambda *_: None)
    assert (root / NAME / "shard" / "codex.json").read_bytes() == before
    assert not (root / NAME / "state" / pin_run.MARKER).exists()
