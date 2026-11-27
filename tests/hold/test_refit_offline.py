"""The refit OFFLINE (the operator, 2026-11-27: "you fucking stop the node,
delete the vectors and the indexing of the only book that is affected by the
change, change the chunker offline, rewrite the codex and regenerate the
collection's identity, then replace it's identity everywhere. You restart the
node and it's done")."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys

import numpy as np
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from app import louise
from app.hold import codex as cx, collection as col, lock as lock_mod, pin_run, refit as refit_mod
from app.plugins.wikidata import chunker as _wd
from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed, fake_query_embed
from tests.hold.test_carry import _local, quiet
from tests.hold.test_template import recipe

LAYERS = [{"rule": "default", "chunker": {"id": "fixed", "version": 1, "params": {"tokens": 100, "overlap": 0}},
           "input": {"form": "text"}}]
DENSE = " ".join(["a"] * 300) + "."
NAME = "gb"


def _tok() -> Tokenizer:
    tok = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    return tok


_TOK_JSON = _tok().to_str().encode("utf-8")
_SHA = "sha256:" + hashlib.sha256(_TOK_JSON).hexdigest()
FIT = ("max_tokens=120", f"tokenizer={_SHA}")


@pytest.fixture
def words(tmp_path, monkeypatch):
    from app import config
    d = tmp_path / "tokenizers"
    d.mkdir()
    (d / f"{_SHA[7:]}.json").write_bytes(_TOK_JSON)
    monkeypatch.setenv("TOKENIZERS_DIR", str(d))
    monkeypatch.setattr(config, "TOKENIZERS_DIR", d)
    _wd.register(_SHA, Tokenizer.from_str(_TOK_JSON.decode("utf-8")))
    yield
    _wd._tokenizers.pop(_SHA, None)


@pytest.fixture
def signer(monkeypatch):
    from app.bundles import keys as node_keys
    monkeypatch.setattr(node_keys, "load_signing_key", lambda *a, **k: KEY)
    monkeypatch.setenv("LOUISE_PLATFORM_IMAGE", PLATFORM)
    return KEY


def _embed_as_cut(root, codex):
    from app import db, embeddings, fts, layers, plugin_io
    coll = _local(root, NAME)
    con = db.connect(coll)
    try:
        con.execute("INSERT INTO space(id, space_id, dim, pinned_at) VALUES(1, ?, ?, 0)",
                    (codex.space_id, codex.space.dim))
        docs = [dict(r) for r in con.execute("SELECT rowid, * FROM documents ORDER BY rowid")]
    finally:
        con.close()
    plugin = coll.plugin_instance()
    rng = np.random.default_rng(11)
    rows, held, by_book = [], {}, {}
    for d in docs:
        title, sections = plugin_io.title_and_sections(plugin, d, coll)
        for c in layers.chunks_for_document(coll, d, sections, title=title):
            rows.append({"id": c["id"], "book_id": int(d["native_id"]), "lang": d["lang"] or "en",
                         "seq": int(c["seq"]), "chapter": c.get("chapter") or "", "text": c["text"],
                         "vector": rng.standard_normal(codex.space.dim).astype("float32")})
            held.setdefault(int(d["native_id"]), {})[c["id"]] = c["text"]
            by_book.setdefault(int(d["native_id"]), []).append({"id": c["id"], "chapter": c.get("chapter") or "",
                                                                "text": c["text"]})
    embeddings.upsert_chunks(rows, collection=coll, dtype=codex.space.dtype)
    fts.index_books([(d["lang"] or "en", int(d["native_id"]), by_book[int(d["native_id"])]) for d in docs], coll)
    fts.close_all()
    m = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    m.execute("UPDATE documents SET embed_done=1, embed_version=7, fts_done=1, fts_version=2, entity_version=1, "
              "chunks=3, tokens=9")
    m.commit()
    m.close()
    return held


@pytest.fixture
def held_gb(isolated_config, tmp_path, corpus_dir, words, signer):
    """gutenberg as the node holds it: two snapshots (the older attested
    too), RAGE settings, bundles (own, inbox, refused), the seeder's served
    tree, an annotations collection annotating the latest snapshot whose
    cascade record names the old id, and platform.toml's quarantine list
    naming both ids."""
    from app import platform_config
    from app.hold import annotations as ann, cascade, check as chk, rage_settings, records as rec, snapshot as sn
    from tests.hold.test_annotations import a_claim, claims_file
    book = corpus_dir / "10002" / "pg10002.txt.utf8"
    text = book.read_text(encoding="utf-8")
    at = text.index("*** END OF THE PROJECT GUTENBERG EBOOK")
    book.write_text(text[:at] + DENSE + "\n\n" + text[at:], encoding="utf-8")
    root = tmp_path / "collections"
    old = col.create_from_recipe(root, "gutenberg", recipe(layers=LAYERS), platform_image=PLATFORM,
                                 embed=fake_embed, embed_query=fake_query_embed, name=NAME, key=KEY, now=NOW)
    rec.build(root, NAME, corpus_dir=corpus_dir, records_per_shard=2, log=quiet)
    rec.write_validation(root, NAME)
    first = sn.create(root, NAME, snapshot_id="2026-08", log=quiet)
    snap = sn.create(root, NAME, snapshot_id="2026-09", log=quiet)
    assert snap.previous == first.hash
    held = _embed_as_cut(root, old)
    (root / f"{NAME}.toml").write_text(f'codex = "{old.collection_id}"\nembed = "all"\n', encoding="utf-8")
    for s in (first, snap):
        chk.attest_landed(root, NAME, old, s, [x.path for x in s.shards], key=KEY, platform_image=PLATFORM)
    card = rage_settings.Card(driver="xe", vendor_id="8086", device_id="e223")
    rage_settings.record(col.shard_dir_of(root, NAME), KEY, collection_id=old.collection_id, space_id=old.space_id,
                         new=rage_settings.entry(card, engine_id="sha256:" + "12" * 32, batch=8, time=NOW), time=NOW)
    state = root / NAME / "state"
    for d in (f"bundles/{old.space_segment}/lang=en/00001/{'ab' * 32}",
              f"bundles/inbox/{'cd' * 32}", f"refused-bundles/{old.space_segment}/lang=en/00002/{'ef' * 32}"):
        (state / d).mkdir(parents=True)
        (state / d / "header.json").write_text(json.dumps({"collection_id": old.collection_id}))
        (state / d / "vectors.parquet").write_bytes(b"PAR1" + old.collection_id.encode() + b"PAR1")
    (state / "share" / "2026-09").mkdir(parents=True)
    (state / "share" / "2026-09" / "member.json").write_text(json.dumps({"collection_id": old.collection_id}))
    uids = sorted(_marks(root))
    ed, _ = ann.create(root, "gb-editions", target_ref=NAME, from_snapshot="2026-09",
                       claims=claims_file(a_claim(uids[0], uids[1], subject_lang="en")), platform_image=PLATFORM,
                       snapshot_id="2026-09", key=KEY, now=NOW, nonce="0f" * 16, log=lambda m: None)
    cascade.write_woken(root, "gb-editions", {"sources": {old.collection_id: "2026-09"}, "state": "done",
                                              "started": NOW, "finished": NOW, "error": None, "tries": 0})
    platform_config.platform_path().parent.mkdir(parents=True, exist_ok=True)
    platform_config.platform_path().write_text(
        f'[node]\nquarantine = "report"\nquarantine_in_use_before = ["{old.collection_id}", '
        f'"{ed.collection_id}"]\n', encoding="utf-8")
    return root, old, held, ed


def _refit(root, *params, extra=()):
    args = ["hold", "collection", "refit", NAME, "--root", str(root), *extra]
    for p in params:
        args += ["--layer-param", p]
    return louise.main(args)


def _marks(root):
    con = sqlite3.connect(root / NAME / "state" / "manifest.sqlite")
    try:
        return {r[0]: tuple(r[1:]) for r in con.execute(
            "SELECT uid, native_id, embed_done, embed_version, fts_done, fts_version, entity_version, chunks, "
            "tokens, error, retries FROM documents ORDER BY rowid")}
    finally:
        con.close()


def _dense(root):
    return next(u for u in _marks(root) if u.endswith("10002"))


def _rows(root, books):
    from app import embeddings
    got = embeddings.held_chunks(_local(root, NAME), books)
    return {b: {cid: v["text"] for cid, v in got.of(b).items()} for b in books}


def _entries(root, native):
    from app import fts
    coll = _local(root, NAME)
    return sum(fts.books_held(g, [native], coll) for g in fts.existing_groups(coll))


def _naming_left(root, name, cid):
    """Every file naming `cid` — under the collection's whole folder (its
    vector table, text index and databases too), its collection file and
    platform.toml — read here, byte for byte, independently of the refit's
    own sweep; what the refit set aside excepted."""
    from app import platform_config
    hexid = cid.split(":", 1)[1].encode()
    out = []
    for p in sorted([*(root / name).rglob("*"), *root.glob("*.toml"), platform_config.platform_path()]):
        rel = str(p.relative_to(root)) if p.is_relative_to(root) else p.name
        if p.is_file() and "/.replaced/" not in f"/{rel}" and hexid in p.read_bytes():
            out.append(rel)
    return out


def _tree(root):
    out = {}
    for p in sorted([*(root / NAME).rglob("*"), *(root / "gb-editions").rglob("*"), *root.glob("*.toml")]):
        if p.is_file() and p.name != "lock" and not p.name.endswith(("-wal", "-shm")):
            out[str(p.relative_to(root))] = (p.stat().st_size, p.stat().st_mtime_ns)
    return out


def test_the_one_affected_book_is_reset_and_the_identity_replaced_everywhere(held_gb):
    root, old, held, ed = held_gb
    before, dense = _marks(root), _dense(root)
    native = int(before[dense][0])
    assert _entries(root, native) > 0
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    assert new.collection_id != old.collection_id
    after = _marks(root)
    assert {u for u in after if after[u] != before[u]} == {dense}, "the affected document alone"
    assert after[dense][1:] == (0, 0, 0, 0, 0, 0, 0, None, 0)
    assert _rows(root, [native])[native] == {} and _entries(root, native) == 0
    assert all(_rows(root, [b])[b] == held[b] for b in held if b != native), "every other row untouched"
    assert all(_entries(root, b) > 0 for b in held if b != native)
    # the identity, everywhere
    assert (root / f"{NAME}.toml").read_text().count(new.collection_id) == 1
    from app.hold import carry, seed
    assert carry._pin(root / NAME / "state" / "manifest.sqlite")[0] == new.space_id
    snap = seed.latest_snapshot(root, NAME)
    assert snap.collection_id == new.collection_id and (col.shard_dir_of(root, NAME) /
                                                         f"snapshots/{snap.snapshot_id}.sig.json").is_file()
    att = root / NAME / "state" / "attestations" / new.collection_id.split(":", 1)[1]
    assert len(list(att.glob("*.json"))) >= 2, "re-keyed and the rewritten part attested"
    assert _naming_left(root, NAME, old.collection_id) == []
    # the editions collection, re-keyed in its turn
    _, ed_now = col.resolve(root, "gb-editions")
    assert [t.collection_id for t in cx.targets_of(ed_now)] == [new.collection_id]
    assert ed_now.collection_id != ed.collection_id
    assert seed.latest_snapshot(root, "gb-editions").collection_id == ed_now.collection_id
    assert _naming_left(root, "gb-editions", ed.collection_id) == []
    assert _naming_left(root, "gb-editions", old.collection_id) == []
    assert (root / "gb-editions.toml").read_text().count(ed_now.collection_id) == 1
    # the cascade's record and platform.toml's quarantine list, through their writers
    from app import platform_config
    from app.hold import cascade
    assert cascade.woken(root, "gb-editions")["sources"] == {new.collection_id: "2026-09"}
    assert platform_config.load().quarantine_in_use_before == (new.collection_id, ed_now.collection_id)
    # bundles, refused bundles, the served tree: set aside under the old id
    aside = root / NAME / "state" / ".replaced" / old.collection_id.split(":", 1)[1] / "state"
    assert (aside / "bundles" / "inbox").is_dir() and (aside / "refused-bundles").is_dir()
    assert not (root / NAME / "state" / "bundles").exists() and (aside / "share" / "2026-09").is_dir()


def _hold(path, what="the node (worker)"):
    """Another process holding the flock at `path`, as the worker does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-I", "-c",
                               "import fcntl, json, sys, time\n"
                               "f = open(sys.argv[1], 'a+b'); fcntl.flock(f, fcntl.LOCK_EX)\n"
                               "f.seek(0); f.truncate(); f.write(json.dumps({'what': sys.argv[2], 'pid': 1}).encode())\n"
                               "f.flush(); print('held', flush=True); time.sleep(60)\n", str(path), what],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    return holder


@pytest.mark.parametrize("which", ["node-wide", "collection"])
def test_refused_while_the_worker_runs_and_the_refusal_names_how_the_node_stays_down(held_gb, capsys, which):
    """The worker holds a node-wide lock from boot (a free one means no
    worker); an older worker, each collection's. `node stop` alone is not
    the way: the restart policy brings it back."""
    root, old, _h, _e = held_gb
    p = (lock_mod.worker_state(root) / lock_mod.WORKER_LOCK if which == "node-wide"
         else lock_mod.lock_path(lock_mod.state_dir(root, "gb-editions")))
    holder = _hold(p)
    try:
        tree = _tree(root)
        assert _refit(root, *FIT) == 3
        err = capsys.readouterr().err
        assert "midclt call app.stop louise-rag" in err and "unless-stopped" in err
        assert "deploy/supervise-pass.sh" in err
        assert _tree(root) == tree
    finally:
        holder.kill()
        holder.wait()


def test_the_worker_takes_the_node_wide_lock_at_boot_and_waits_for_an_offline_pass(tmp_path, monkeypatch,
                                                                                   capsys):
    from app import worker
    monkeypatch.setenv("LOUISE_ROOT", str(tmp_path))
    seen = []
    monkeypatch.setattr(worker, "_main_locked", lambda: seen.append(lock_mod.holder(
        lock_mod.worker_state(tmp_path / "collections"), file=lock_mod.WORKER_LOCK)) or 0)
    assert worker.main() == 0
    assert seen[0]["what"] == lock_mod.WORKER, "held from boot, before any writer starts"
    assert lock_mod.holder(lock_mod.worker_state(tmp_path / "collections"), file=lock_mod.WORKER_LOCK) is None
    lk = lock_mod.take_worker(tmp_path / "collections", "louise hold collection refit (offline)")
    try:
        assert worker.main() == 3, "deferred while the refit holds it"
        assert "refit" in capsys.readouterr().err and len(seen) == 1
    finally:
        lk.release()


def test_without_the_node_key_it_is_refused_before_anything(held_gb, monkeypatch, capsys):
    from app.bundles import keys as node_keys
    root, _old, _h, _e = held_gb

    def no_key(*a, **k):
        raise FileNotFoundError("NODE_KEY_PATH")
    monkeypatch.setattr(node_keys, "load_signing_key", no_key)
    tree = _tree(root)
    assert _refit(root, *FIT) == 2
    err = capsys.readouterr().err
    assert "no node key here" in err and "deploy/supervise-pass.sh" in err and "web container" in err
    assert _tree(root) == tree


def _refused_untouched(root, capsys, *needles, code=2):
    tree = _tree(root)
    assert _refit(root, *FIT) == code
    err = capsys.readouterr().err
    assert all(n in err for n in needles), err
    assert _tree(root) == tree, "decided in the plan, before the first write"
    assert refit_mod.read_marker(root, NAME) is None


def test_a_peers_rage_settings_refuse_in_the_plan(held_gb, capsys):
    from nacl.signing import SigningKey
    from app.hold import rage_settings
    root, old, _h, _e = held_gb
    card = rage_settings.Card(driver="xe", vendor_id="8086", device_id="e223")
    rage_settings.record(col.shard_dir_of(root, NAME), SigningKey(b"\x07" * 32), collection_id=old.collection_id,
                         space_id=old.space_id, time=NOW,
                         new=rage_settings.entry(card, engine_id="sha256:" + "12" * 32, batch=8, time=NOW))
    _refused_untouched(root, capsys, "another peer signed")


def test_an_update_under_way_refuses_in_the_plan(held_gb, capsys):
    from app.hold import update_state
    root, old, _h, _e = held_gb
    update_state.write(root, NAME, {"collection_id": old.collection_id, "period": "2026-11", "step": "fetch",
                                    "moved": True, "run": "20261101"})
    _refused_untouched(root, capsys, "update of the old Codex is under way")


def test_a_file_naming_the_old_id_the_refit_does_not_know_refuses_in_the_plan(held_gb, capsys):
    root, old, _h, _e = held_gb
    (root / NAME / "state" / "mystery.json").write_text(json.dumps({"id": old.collection_id}))
    _refused_untouched(root, capsys, "state/mystery.json names", "does not know what to do with it")


def test_parts_built_since_the_latest_snapshot_refuse_in_the_plan(held_gb, capsys):
    root, _old, _h, _e = held_gb
    db = sqlite3.connect(refit_mod._build_db(root, NAME))
    db.execute("UPDATE shards SET root='sha256:' || hex(randomblob(32)) WHERE n=1 AND slice='lang=en'")
    db.commit()
    db.close()
    _refused_untouched(root, capsys, "publish or discard them first")


def test_annotations_of_an_older_target_snapshot_refuse_in_the_plan(held_gb, capsys):
    from app.hold import annotations as ann
    from tests.hold.test_annotations import a_claim, claims_file
    root, _old, _h, _e = held_gb
    uids = sorted(_marks(root))
    ann.create(root, "gb-older", target_ref=NAME, from_snapshot="2026-08",
               claims=claims_file(a_claim(uids[0], uids[2], subject_lang="en")), platform_image=PLATFORM,
               snapshot_id="2026-09", key=KEY, now=NOW, nonce="1f" * 16, log=lambda m: None)
    _refused_untouched(root, capsys, "update gb-older to the latest snapshot first")


def test_a_claim_on_a_chunk_of_the_book_cut_otherwise_refuses_in_the_plan(held_gb, monkeypatch, capsys):
    """A claim pointing at a RECORD holds (its bytes are kept); one pointing
    at a CHUNK of the document cut otherwise names a chunk id that is gone."""
    from app.hold import annotations as ann
    root, _old, _h, _e = held_gb
    dense = _dense(root)
    real = ann.claim_of
    monkeypatch.setattr(ann, "claim_of", lambda r: {**real(r), "type": ann.MENTION, "object": "wikidata:Q42",
                                                    "subject": {"uid": dense, "chunk_id": f"{dense}:0",
                                                                "text_hash": "x"}})
    _refused_untouched(root, capsys, "point at chunks of " + dense)


def test_a_platform_file_its_writer_cannot_rewrite_refuses_in_the_plan(held_gb, capsys):
    from app import platform_config
    root, old, _h, ed = held_gb
    platform_config.platform_path().write_text(
        f'[node]\nquarantine = "report"\nquarantine_in_use_before = [\n  "{old.collection_id}",\n'
        f'  "{ed.collection_id}",\n]\n', encoding="utf-8")
    _refused_untouched(root, capsys, "quarantine_in_use_before names the old id and cannot be rewritten")


def test_a_held_index_writer_refuses_before_the_marker(held_gb, capsys):
    """Decided first, saved after: the text index's writers are taken before
    `state/refit.json` is written."""
    from app import fts
    root, _old, _h, _e = held_gb
    coll = _local(root, NAME)
    g = next(iter(fts.existing_groups(coll)))
    path = fts.base_of(coll) / g
    holder = subprocess.Popen([sys.executable, "-c",
                               "import sys, time, tantivy\n"
                               "w = tantivy.Index.open(sys.argv[1]).writer()\n"
                               "print('held', flush=True); time.sleep(60)\n", str(path)],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        _refused_untouched(root, capsys, "text index", "midclt call app.stop louise-rag", code=3)
    finally:
        holder.kill()
        holder.wait()


class _Boom(Exception):
    pass


@pytest.mark.parametrize("step", refit_mod.STEPS)
def test_a_rerun_after_a_kill_at_each_step_continues(held_gb, monkeypatch, step):
    root, old, held, _e = held_gb
    real = refit_mod._save

    def save(r, n, m):
        real(r, n, m)
        if (m["steps"].get(step) or {}).get("done") and not m.get("done"):
            raise _Boom(f"after {step}")
    monkeypatch.setattr(refit_mod, "_save", save)
    with pytest.raises(_Boom):
        _refit(root, *FIT)
    monkeypatch.setattr(refit_mod, "_save", real)
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    assert refit_mod.read_marker(root, NAME) is None, "filed with what it set aside"
    assert _naming_left(root, NAME, old.collection_id) == []
    native = int(_marks(root)[_dense(root)][0])
    assert _rows(root, [native])[native] == {}
    assert all(_rows(root, [b])[b] == held[b] for b in held if b != native)


def test_the_dry_run_writes_nothing_and_names_every_dataset_it_writes_by_its_real_path(held_gb, capsys,
                                                                                         tmp_path):
    """The rollback is the ZFS snapshot of every dataset the refit writes,
    each resolved from its real path (the vector table under
    LOUISE_LANCE_ROOT is a link), and the dry run says what else changes."""
    import os
    root, _old, _h, _e = held_gb
    lance = root / NAME / "lance"
    elsewhere = tmp_path / "ssd4" / "gb-lance"
    elsewhere.parent.mkdir()
    os.replace(lance, elsewhere)
    lance.symlink_to(elsewhere)
    tree = _tree(root)
    assert _refit(root, *FIT, extra=("--dry-run",)) == 0
    out = capsys.readouterr().out
    assert "affected    1 document(s): " + _dense(root) in out and "gb-editions" in out
    assert "take a ZFS snapshot of every dataset holding these — that is the rollback" in out
    for what, path in (("gb: vector table", elsewhere), ("gb: SHARD", root / NAME / "shard"),
                       ("gb: build area", root / NAME / "state" / "build"), ("gb-editions: state",
                                                                             root / "gb-editions" / "state")):
        assert f"{what}: {os.path.realpath(path)}" in out, what
    assert "platform.toml:" in out and "node.sqlite:" in out
    for said in ("coverage rows of the old space deleted", "published marker of snapshot 2026-09 set aside",
                 "publisher's row of gb in node.sqlite deleted", "search of gb withheld until the worker's gate",
                 "bundles exported again under the new id", "quarantine_in_use_before names the new ids"):
        assert said in out, said
    assert "gb/state/bundles/" in out and "gb/state/refused-bundles/" in out and "gb/state/share/" in out
    assert "1 re-keyed — the cascade's record of the snapshots it ran against" in out
    assert "gb-editions/state/woken.json" in out
    assert "signed again — this node's RAGE settings" in out and "gb/shard/rage-settings/" in out
    assert "REFUSED" not in out
    assert _tree(root) == tree


def test_a_window_that_cuts_nothing_otherwise_is_refused(held_gb, capsys):
    root, old, _h, _e = held_gb
    tree = _tree(root)
    assert _refit(root, "max_tokens=100000", f"tokenizer={_SHA}") != 0
    assert "nothing to refit" in capsys.readouterr().err
    assert _tree(root) == tree and col.read(root, NAME).collection_id == old.collection_id


def test_a_shared_text_index_and_an_official_publication_refuse(held_gb, monkeypatch, capsys):
    from app import text_index
    root, old, _h, _e = held_gb
    tree = _tree(root)
    monkeypatch.setattr(text_index, "placement", lambda r, n: {"dir": ".text-index/x", "identity": "x"}
                        if n in (NAME, "gb-editions") else None)
    assert _refit(root, *FIT) != 0
    assert "shared with gb-editions" in capsys.readouterr().err
    monkeypatch.setattr(text_index, "placement", lambda r, n: None)
    monkeypatch.setattr(refit_mod, "official_publication", lambda r, n, c: f"{n} is certified")
    assert _refit(root, *FIT) != 0
    assert "is certified" in capsys.readouterr().err
    assert _tree(root) == tree


def test_the_refit_is_not_routed_the_worker_cannot_run_an_offline_pass():
    from app import api_commands as ac
    with pytest.raises(ac.Refused, match="not routed: an OFFLINE pass"):
        ac.resolve(["hold", "collection", "refit"])


def test_only_this_nodes_attestations_of_the_latest_snapshot_are_signed_again(held_gb):
    """09 §9.8: the refit makes the latest snapshot again, no older one; an
    attestation over 2026-08 is set aside, never signed for the new id."""
    from app.hold import check as chk, envelope as env, seed, snapshot as sn
    root, old, _h, _e = held_gb
    shard = col.shard_dir_of(root, NAME)
    kept = [s for s in seed.latest_snapshot(root, NAME).shards if not s.path.endswith("en/00001.jsonl.zst")]
    assert kept, "a part the refit does not write again"
    for sid in ("2026-08", "2026-09"):
        snap = sn.load(shard / "snapshots" / f"{sid}.json")
        chk.attest_landed(root, NAME, old, snap, [s.path for s in kept], key=KEY, platform_image=PLATFORM)
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    now = seed.latest_snapshot(root, NAME)
    d = root / NAME / "state" / "attestations" / new.collection_id.split(":", 1)[1]
    bodies = [env.verify(env.ATTESTATION, json.loads(f.read_bytes())) for f in d.glob("*.json")]
    assert {b["snapshot_hash"] for b in bodies} == {now.hash}
    over_kept = [b for b in bodies if b["shards"] and all("/lang=fr/" in x for x in b["shards"])]
    assert len(over_kept) == 3, "roots, records and chunks over 2026-09's kept part — 2026-08's set aside"


class _Kill(Exception):
    pass


def _killing(monkeypatch, owner, attr, *, when=lambda *a, **k: True):
    """`owner.attr` runs, then the process "dies" — between a write and the
    marker that records it."""
    real = getattr(owner, attr)
    fired = []

    def wrapped(*a, **k):
        got = real(*a, **k)
        if not fired and when(*a, **k):
            fired.append(1)
            raise _Kill(attr)
        return got
    monkeypatch.setattr(owner, attr, wrapped)
    return lambda: monkeypatch.setattr(owner, attr, real)


def _inside(name):
    from app import platform_config
    from app.hold import check as chk, snapshot as sn
    from app.hold import records as rec
    return {"reset: rows gone, index entries not": (refit_mod, "_reset", None),
            "codex: written, snapshots not set aside": (refit_mod, "_replace_codex", None),
            "parts: written, snapshot not made": (rec, "write_validation", None),
            "parts: snapshot made, not signed": (sn, "create", None),
            "identity: the file re-pointed, the rest not": (refit_mod, "_resign_attestations", None),
            "others: one snapshot signed, the rest not": (sn, "sign_snapshot",
                                                          lambda r, n, *a, **k: n == "gb-editions"),
            "platform: written, marker not": (platform_config, "set_keys", None),
            "attest: attested, marker not": (chk, "attest_landed", None)}[name]


@pytest.mark.parametrize("point", ["reset: rows gone, index entries not", "codex: written, snapshots not set aside",
                                   "parts: written, snapshot not made", "parts: snapshot made, not signed",
                                   "identity: the file re-pointed, the rest not",
                                   "others: one snapshot signed, the rest not", "platform: written, marker not",
                                   "attest: attested, marker not"])
def test_a_kill_inside_a_step_between_a_write_and_its_marker_is_continued(held_gb, monkeypatch, point):
    root, old, held, _e = held_gb
    owner, attr, when = _inside(point)
    restore = _killing(monkeypatch, owner, attr, **({"when": when} if when else {}))
    with pytest.raises(_Kill):
        _refit(root, *FIT)
    assert (col.shard_dir_of(root, NAME) / "codex.json").is_file(), "the Codex file is never missing"
    restore()
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    assert refit_mod.read_marker(root, NAME) is None
    assert _naming_left(root, NAME, old.collection_id) == []
    _, ed_now = col.resolve(root, "gb-editions")
    assert [t.collection_id for t in cx.targets_of(ed_now)] == [new.collection_id]
    assert _naming_left(root, "gb-editions", old.collection_id) == []
    native = int(_marks(root)[_dense(root)][0])
    assert _rows(root, [native])[native] == {} and _entries(root, native) == 0
    assert all(_rows(root, [b])[b] == held[b] for b in held if b != native)


def test_a_marker_is_written_whole(held_gb, monkeypatch):
    """A kill before a marker's rename leaves the one before it: the
    annotations collection's target marker is never torn."""
    import os
    root, old, _h, _e = held_gb
    real = os.fsync

    def fsync(fd):
        if os.readlink(f"/proc/self/fd/{fd}").endswith(".refit-target.json.refit-tmp"):
            raise _Kill("fsync")
        return real(fd)
    monkeypatch.setattr(refit_mod.os, "fsync", fsync)
    with pytest.raises(_Kill):
        _refit(root, *FIT)
    assert not (root / "gb-editions" / "state" / refit_mod.TARGET_MARKER).exists()
    monkeypatch.setattr(refit_mod.os, "fsync", real)
    assert _refit(root, *FIT) == 0
    assert _naming_left(root, "gb-editions", old.collection_id) == []


def test_the_parts_and_snapshot_are_a_fresh_build_of_the_new_codex(held_gb, tmp_path, corpus_dir):
    """As pin-run's tests do: the refit's rewritten part and its snapshot's
    entries are what a build of the new Codex from scratch writes, byte for
    byte."""
    from app.hold import records as rec, seed, snapshot as sn
    root, _old, _h, _e = held_gb
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    fresh_root = tmp_path / "fresh"
    layers = [{"rule": "default", "chunker": {"id": "fixed", "version": 1, "params": {
        "tokens": 100, "overlap": 0, "max_tokens": 120, "tokenizer": _SHA}}, "input": {"form": "text"}}]
    fresh = col.create_from_recipe(fresh_root, "gutenberg", recipe(layers=layers), platform_image=PLATFORM,
                                   embed=fake_embed, embed_query=fake_query_embed, name=NAME, key=KEY, now=NOW)
    assert fresh.space_id == new.space_id and fresh.collection_id == new.collection_id, \
        "the same Codex as a create of the new recipe"
    rec.build(fresh_root, NAME, corpus_dir=corpus_dir, records_per_shard=2, log=quiet)
    rec.write_validation(fresh_root, NAME)
    fs = sn.create(fresh_root, NAME, snapshot_id="2026-09", log=quiet)
    mine = seed.latest_snapshot(root, NAME)
    assert {(s.slice, int(s.n)): s.to_dict() for s in mine.shards} == \
        {(s.slice, int(s.n)): s.to_dict() for s in fs.shards}, "every entry, root and writer included"
    for s in fs.shards:
        assert (root / NAME / "shard" / s.path).read_bytes() == (fresh_root / NAME / "shard" / s.path).read_bytes()
    assert (mine.collection_id, mine.snapshot_id) == (fs.collection_id, fs.snapshot_id)


def test_the_worker_boots_on_the_result_gates_it_then_serves_it_and_redoes_the_one_book(
        held_gb, monkeypatch, tmp_path, corpus_dir):
    """The restart: the boot check takes the collection; search is withheld
    until the gate passes for the new space (quarantine's gate test), then
    served; the worker's own selections pick the reset book alone, cut by the
    new layers exactly as a fresh build cuts it, indexed and embedded again."""
    from types import SimpleNamespace
    from app import copy_state, db, embeddings, fts, layers, run_check, spaces, worker
    from app.hold import envelope as env, records as rec, runs as hold_runs
    root, _old, held, _e = held_gb
    assert _refit(root, *FIT) == 0
    _, new = col.resolve(root, NAME)
    coll = rec.local_collection(root, NAME, new)
    assert spaces.refuse_boot([coll]) == [], "the worker boots on it"
    me = env.key_text(KEY.verify_key)

    def gate():
        copy_state.reset_cache()
        return copy_state.evaluate(root, NAME, new, licence="green", key=me).test("gate").status
    assert gate() == "open", "withheld until the worker's gate passes for the new space"
    monkeypatch.setattr(louise, "_recipe_for", lambda codex, name: object())
    monkeypatch.setattr(hold_runs, "passing", lambda body, codex: True)

    # the gate's MEASUREMENT stood in for (the worker's, against the live encoder) — what a pass of it
    # keeps: a statement signed by this node for the NEW space; the old space's count for nothing
    def validate(root_, name, codex_, recipe, *, role, on_run=None, **kw):
        if on_run is not None:
            on_run(SimpleNamespace(all_cosines=(0.9999,) * 512))
        body = {"kind": "louise-calibration-run", "version": 3, "space_id": codex_.space_id,
                "collection_id": codex_.collection_id, "role": role, "run": "sha256:" + "ab" * 32,
                "engine": {"image": "the engine", "args": []}, "driver": "cpu", "passed": 16,
                "sampled": list(range(16)), "worst": "0.999900", "median": "0.999900", "fingerprint": "f",
                "batch_invariant": True, "tokens_per_s": 1, "key": me, "time": NOW}
        hold_runs.keep(root_, name, env.sign(env.CALIBRATION, body, KEY))
        return body
    monkeypatch.setattr(run_check, "validate", validate)
    assert louise.main(["hold", "embedder", "check", NAME, "--root", str(root)]) == 0
    assert gate() == "passed", "served once its gate has run"
    con = db.connect(coll)
    try:
        fresh = [r["uid"] for r in con.execute("SELECT uid FROM documents WHERE fts_done=0 AND embed_done=0")]
        assert fresh == [_dense(root)], "the reset book alone is the worker's to redo"
        row = dict(con.execute("SELECT rowid, * FROM documents WHERE uid=?", (fresh[0],)).fetchone())
    finally:
        con.close()
    chunks = worker._load_chunks(row, coll)
    fresh_root = tmp_path / "fresh"
    fl = [{"rule": "default", "chunker": {"id": "fixed", "version": 1, "params": {
        "tokens": 100, "overlap": 0, "max_tokens": 120, "tokenizer": _SHA}}, "input": {"form": "text"}}]
    col.create_from_recipe(fresh_root, "gutenberg", recipe(layers=fl), platform_image=PLATFORM, embed=fake_embed,
                           embed_query=fake_query_embed, name=NAME, key=KEY, now=NOW)
    from app.hold import records as rec2
    rec2.build(fresh_root, NAME, corpus_dir=corpus_dir, records_per_shard=2, log=quiet)
    fcoll = rec.local_collection(fresh_root, NAME, new)
    fcon = db.connect(fcoll)
    try:
        frow = dict(fcon.execute("SELECT rowid, * FROM documents WHERE uid=?", (row["uid"],)).fetchone())
    finally:
        fcon.close()
    assert [(c["id"], c["text"]) for c in chunks] == \
        [(c["id"], c["text"]) for c in worker._load_chunks(frow, fcoll)], "cut as a fresh build cuts it"
    assert len(chunks) != len(held[int(row["native_id"])]), "cut otherwise than before"
    native = int(row["native_id"])
    fts.index_book(row["lang"] or "en", native, chunks, coll)
    fts.close_all()
    rng = np.random.default_rng(3)
    embeddings.upsert_chunks([{"id": c["id"], "book_id": native, "lang": row["lang"] or "en", "seq": int(c["seq"]),
                               "chapter": c.get("chapter") or "", "text": c["text"],
                               "vector": rng.standard_normal(new.space.dim).astype("float32")} for c in chunks],
                             collection=coll, dtype=new.space.dtype)
    assert _rows(root, [native])[native] == {c["id"]: c["text"] for c in chunks} and _entries(root, native) > 0
